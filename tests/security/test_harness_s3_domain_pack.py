"""``S3``（契约 §6.2 / ADR-0015 §7.2）：领域包目录内放置带副作用的 ``.py`` 必须被拒绝加载。

攻击场景：把一段会写标志文件 / 打印的 Python 代码放进领域包目录，诱导 harness 在加载时
执行它（``R5``：领域包只允许声明式配置，禁止加载其中的 Python 代码）。

三条判据（契约 §6.2 原文）：
1. ``load_pack`` **抛** ``DomainPackError``；
2. 包内 ``.py`` 的**副作用不发生**（标志文件不得被创建）；
3. 该模块名**不在** ``sys.modules``（"不导入"是行为断言，不能只看返回值——只看返回值
   会漏掉"其实导入了但恰好没报错"的静默失败）。

被测对象（只读）：``agent_sec_perf.harness.domain_pack.load_pack`` —— 它是 harness 里
**唯一读盘者**，且 ``_reject_python_content`` 是 fail-secure 的最后一道闸（契约 §4.3）。

变异探针见 :func:`test_s3_rejection_depends_on_guard`：把 ``_reject_python_content`` 换成
空操作后再加载，``load_pack`` **不再抛错**（保护被摘掉 ⇒ 原断言翻红），证明上面的拒绝
确实由真实保护产生，而非偶然通过。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from agent_sec_perf.harness import domain_pack
from agent_sec_perf.harness.domain_pack import DomainPack, DomainPackError

#: 合法 pack.toml：让"唯一"的拒绝触发点就是目录里的 Python 内容。
_VALID_PACK_TOML = """\
[pack]
name = "evil"
version = "1.0.0"

[tools]
allowlist = []

[security]
capabilities = []
"""


def _write_pack(tmp_path: Path, *, python_body: str, python_name: str) -> Path:
    """在 ``tmp_path`` 下造一个带 pack.toml + 指定 Python 文件的领域包目录。"""
    pack_dir = tmp_path / "evil_pack"
    pack_dir.mkdir()
    (pack_dir / "pack.toml").write_text(_VALID_PACK_TOML, encoding="utf-8")
    (pack_dir / python_name).write_text(python_body, encoding="utf-8")
    return pack_dir


@pytest.mark.security
def test_s3_rejects_python_source_with_side_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``.py`` 带副作用 ⇒ ① 抛 DomainPackError ② 副作用不发生 ③ 模块不在 sys.modules。"""
    flag = tmp_path / "evil_pack" / "side_effect_marker"
    body = (
        "import pathlib\n"
        "pathlib.Path(__file__).with_name('side_effect_marker').write_text('pwned')\n"
    )
    pack_dir = _write_pack(tmp_path, python_body=body, python_name="helper.py")

    # 让该模块可被导入，使"不在 sys.modules"这一断言有实际意义（否则它本就不可导入）。
    monkeypatch.syspath_prepend(str(pack_dir))
    spec = importlib.util.find_spec("helper")
    assert spec is not None, "前置条件：helper 必须可被导入，否则 '不在 sys.modules' 无意义"

    with pytest.raises(DomainPackError):
        domain_pack.load_pack(pack_dir, roots=(tmp_path,), known_tools=frozenset())

    assert not flag.exists(), "包内 .py 不得被导入执行：副作用标志文件不应存在"
    assert "helper" not in sys.modules, "load_pack 不得把包内 .py 导入进 sys.modules"


@pytest.mark.security
def test_s3_rejects_compiled_python_file(tmp_path: Path) -> None:
    """.pyc 同样属禁止后缀 ⇒ load_pack 必须抛 DomainPackError（§4.3 的 R5）。"""
    pack_dir = tmp_path / "evil_pack"
    pack_dir.mkdir()
    (pack_dir / "pack.toml").write_text(_VALID_PACK_TOML, encoding="utf-8")
    (pack_dir / "helper.pyc").write_bytes(b"\x00\x00\x00\x00compiled-marker")

    with pytest.raises(DomainPackError):
        domain_pack.load_pack(pack_dir, roots=(tmp_path,), known_tools=frozenset())


@pytest.mark.security
def test_s3_rejects_pycache_directory(tmp_path: Path) -> None:
    """``__pycache__`` 目录名命中禁止名单 ⇒ load_pack 必须抛 DomainPackError。"""
    pack_dir = tmp_path / "evil_pack"
    pack_dir.mkdir()
    (pack_dir / "pack.toml").write_text(_VALID_PACK_TOML, encoding="utf-8")
    cache = pack_dir / "__pycache__"
    cache.mkdir()
    (cache / "helper.pyc").write_bytes(b"\x00\x00\x00\x00compiled-marker")

    with pytest.raises(DomainPackError):
        domain_pack.load_pack(pack_dir, roots=(tmp_path,), known_tools=frozenset())


@pytest.mark.security
def test_s3_rejection_depends_on_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """变异探针：摘掉 ``_reject_python_content`` 这道闸后，同样的包不再被拒绝。

    原用例之所以能通过，是真实保护在起作用；把保护换成空操作，``load_pack`` 不再抛
    ``DomainPackError``（而是正常装配出 ``DomainPack``）——这证明上面的拒绝断言**依赖**于
    真实保护，而非偶然通过（防"绿着但没生效"）。
    """
    pack_dir = tmp_path / "evil_pack"
    pack_dir.mkdir()
    (pack_dir / "pack.toml").write_text(_VALID_PACK_TOML, encoding="utf-8")
    (pack_dir / "helper.py").write_text("# harmless-looking payload\n", encoding="utf-8")

    monkeypatch.setattr(domain_pack, "_reject_python_content", lambda directory: None)

    result = domain_pack.load_pack(pack_dir, roots=(tmp_path,), known_tools=frozenset())
    assert isinstance(result, DomainPack), "保护被摘掉后，原拒绝断言会翻红（此处验证它确实翻红）"
