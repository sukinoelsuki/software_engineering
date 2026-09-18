"""S2（ADR-0015 §7.2）：工具参数含路径穿越 / 软链接指向白名单外时必须被拒绝。

验收标准（§7.2 S2）：路径解析抛 ``PathNotAllowedError`` / 返回拒绝；**不得**回退为放行。

被测对象（只读）：``agent_sec_perf.foundation.paths.resolve_within`` —— 全项目唯一的路径
校验入口（ADR-0015 §7.1 接缝）。本文件只验证「拒绝行为」这一可独立复现的部分；
「拒绝被正确审计记录」一半目前**无法验证**，因为 ``AuditSink`` 的实现
（``observability/audit.py``）尚未落地（见回报 §遗留）。

所有用例均为攻击者视角：把恶意文件名当作工具收到的参数，构造相对于允许根目录的候选路径，
断言它**不会**被解析到白名单之外、且以异常形式被拒绝（绝不静默放行）。

变异验证见 :func:`test_path_traversal_rejection_depends_on_protection`：临时移除拒绝逻辑后，
穿越攻击即「得手」（不再抛错），证明上面的对抗性用例确实依赖于真实保护，而非偶然通过。
"""

from __future__ import annotations

import pathlib

import pytest

from agent_sec_perf.foundation import paths
from agent_sec_perf.foundation.errors import PathNotAllowedError

_CORPUS = pathlib.Path(__file__).resolve().parent / "corpus" / "traversal_payloads.txt"


def _traversal_payloads() -> list[str]:
    """读取 corpus，返回非注释、非空的穿越载荷。"""
    return [
        line.strip()
        for line in _CORPUS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _is_within(resolved: pathlib.Path, root: pathlib.Path) -> bool:
    root_resolved = root.resolve()
    return resolved == root_resolved or root_resolved in resolved.parents


@pytest.mark.security
@pytest.mark.parametrize("payload", _traversal_payloads())
def test_path_traversal_via_dotdot_is_rejected(payload: str, tmp_path: pathlib.Path) -> None:
    """``../../etc/passwd`` 这类相对穿越拼接在允许根目录下时，必须被拒绝。"""
    root = tmp_path / "allowed"
    root.mkdir()
    candidate = root / payload
    with pytest.raises(PathNotAllowedError):
        paths.resolve_within(candidate, [root], what="asset")


@pytest.mark.security
def test_absolute_path_outside_root_is_rejected(tmp_path: pathlib.Path) -> None:
    """绝对路径直接指向白名单外（如 ``/etc/passwd``）必须被拒绝。"""
    root = tmp_path / "allowed"
    root.mkdir()
    with pytest.raises(PathNotAllowedError):
        paths.resolve_within("/etc/passwd", [root], what="asset")


@pytest.mark.security
def test_symlink_pointing_outside_whitelist_is_rejected(
    tmp_path: pathlib.Path,
) -> None:
    """软链接指向白名单外目录 / 文件时，``resolve()`` 会跟随符号链接，必须被拒绝。

    S2 明确列举「符号链接指向白名单外」这一攻击场景。
    """
    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "outside_root"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("topsecret", encoding="utf-8")

    link = root / "link_to_outside"
    link.symlink_to(outside)

    # 经软链接进入白名单外文件
    with pytest.raises(PathNotAllowedError):
        paths.resolve_within(link / "secret.txt", [root], what="asset")
    # 软链接本身即指向白名单外目录
    with pytest.raises(PathNotAllowedError):
        paths.resolve_within(link, [root], what="asset")


@pytest.mark.security
def test_deep_traversal_is_rejected(tmp_path: pathlib.Path) -> None:
    """深层 ``..`` 穿越（即便中间夹着合法子目录）必须被拒绝。"""
    root = tmp_path / "allowed"
    root.mkdir()
    candidate = root / "sub" / ".." / ".." / "etc" / "passwd"
    with pytest.raises(PathNotAllowedError):
        paths.resolve_within(candidate, [root], what="asset")


@pytest.mark.security
def test_legit_path_within_root_is_resolved_not_over_rejected(
    tmp_path: pathlib.Path,
) -> None:
    """边界正确性：根目录内的合法文件必须被正常解析，证明拒绝是精确的而非「一律拒绝」。"""
    root = tmp_path / "allowed"
    (root / "sub").mkdir(parents=True)
    legit = root / "sub" / "ok.txt"
    legit.write_text("data", encoding="utf-8")

    resolved = paths.resolve_within(legit, [root], what="asset")
    assert resolved == legit.resolve()
    assert _is_within(resolved, root)


@pytest.mark.security
def test_path_traversal_rejection_depends_on_protection(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """变异验证（in-repo）：移除拒绝逻辑后，穿越攻击即「得手」。

    把 ``resolve_within`` 替换为「只解析、不拒绝」的弱实现，再喂入穿越输入：
    此时不再抛 ``PathNotAllowedError``，且解析结果确实逃出了白名单。
    这证明上面的对抗性用例是通过「真实保护在起作用」才通过的，而非偶然。
    """
    root = tmp_path / "allowed"
    root.mkdir()

    def _weakened(candidate: str | pathlib.Path, *args: object, **kwargs: object) -> pathlib.Path:
        return pathlib.Path(candidate).expanduser().resolve()

    monkeypatch.setattr(paths, "resolve_within", _weakened)

    # root 位于临时目录之下，因此 ``../../etc/passwd`` 会逃到临时目录树的更上层，
    # 而非字面的 /etc/passwd——但无论如何它都**逃出了允许根目录**；这已足以证明
    # "保护缺失时攻击得手"。关键断言是：不抛错 且 解析结果不在白名单内。
    resolved = paths.resolve_within(root / "../../etc/passwd", [root], what="asset")
    assert not _is_within(resolved, root)
