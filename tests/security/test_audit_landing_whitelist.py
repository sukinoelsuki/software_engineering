"""对抗性验证：审计落点**路径白名单**（``REQ-SEC-06`` / ``SECURITY.md`` 路径白名单）。

依据（独立验证，不听实现者的解释）：``docs/design/interfaces/audit.md`` §2.5 的 P1~P7 与
判据 **W1~W8**。

攻击者视角（``audit.directory`` 来自跟着仓库走的 ``.lowspec.toml``，属不可信输入）：

* 配置期越界 ⇒ ``ConfigError``，**不得回退默认目录**；
* 装配期越界 ⇒ ``PathNotAllowedError``；
* **必须先校验、后 mkdir**：越界时**不得创建任何目录**——"先建后拒"等于已按不可信路径写了一次；
* 禁止静默降级：不得回退默认目录、不得关闭审计；
* 根集合是**常量**、配置不得影响它：显式给出根外 ``audit.directory`` 不能让它通过；
* ``~`` 展开后落在根外同样被拒（预期内的收紧）。

附**变异探针**（W7/W8）：临时把 ``resolve_within`` 替换成不过滤的直通实现，
证明 W1~W5 依赖真实白名单、非恒过；并分别只撤掉一层，证明配置期 / 装配期两层**各自独立**
在起作用（而非其中一层恒真）。探针经 ``monkeypatch`` 自动还原，不留在共享工作树。
"""

from __future__ import annotations

import pathlib

import pytest

from agent_sec_perf.foundation import config as config_module
from agent_sec_perf.foundation.errors import ConfigError, PathNotAllowedError
from agent_sec_perf.observability import audit as audit_module
from agent_sec_perf.observability.audit import JsonlAuditSink


def _outside_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """一个绝对路径：必然在 ``ALLOWED_AUDIT_ROOTS`` 之外（根由 platformdirs 决定）。"""
    return tmp_path / "evil_audit_dir"


def _write_project_config(tmp_path: pathlib.Path, *, directory: str) -> pathlib.Path:
    path = tmp_path / "evil.toml"
    path.write_text(f"[audit]\ndirectory = {directory!r}\n", encoding="utf-8")
    return path


@pytest.mark.security
def test_config_out_of_root_rejected_and_not_created(tmp_path: pathlib.Path) -> None:
    """W1：``[audit] directory`` 指向根外 ⇒ ``ConfigError``，且该目录**未被创建**。"""
    bad = _outside_root(tmp_path)
    project = _write_project_config(tmp_path, directory=str(bad))

    with pytest.raises(ConfigError):
        config_module.load_config(
            user_file=tmp_path / "no_user.toml",
            project_file=project,
        )
    assert not bad.exists(), "越界目录不应被创建（防'先建后拒'）"


@pytest.mark.security
def test_config_parent_escape_rejected(tmp_path: pathlib.Path) -> None:
    """W2：``<允许根>/../../.ssh`` 这类上跳 ⇒ ``ConfigError``（resolve 后落在根外）。"""
    root = config_module.ALLOWED_AUDIT_ROOTS[0]
    escape = str(root / ".." / ".." / ".ssh")
    project = _write_project_config(tmp_path, directory=escape)

    with pytest.raises(ConfigError):
        config_module.load_config(
            user_file=tmp_path / "no_user.toml",
            project_file=project,
        )


@pytest.mark.security
def test_config_within_root_loads_resolved_absolute(tmp_path: pathlib.Path) -> None:
    """W4：允许根内（含子目录）加载成功；``AuditConfig.directory`` 是已 resolve 的绝对路径。"""
    root = config_module.ALLOWED_AUDIT_ROOTS[0]
    sub = root / "session-1"
    project = _write_project_config(tmp_path, directory=str(sub))

    cfg = config_module.load_config(
        user_file=tmp_path / "no_user.toml",
        project_file=project,
    )
    assert cfg.audit.directory.is_absolute()
    assert cfg.audit.directory == sub.resolve()


@pytest.mark.security
def test_assembly_out_of_root_rejected_and_not_created(tmp_path: pathlib.Path) -> None:
    """W5：绕过配置直接 ``JsonlAuditSink(根外目录)`` ⇒ ``PathNotAllowedError``，且目录未被创建。"""
    bad = _outside_root(tmp_path)
    with pytest.raises(PathNotAllowedError):
        JsonlAuditSink(bad)
    assert not bad.exists(), "越界目录不应被创建（先校验、后 mkdir）"


@pytest.mark.security
def test_no_silent_fallback_to_default_audit_dir(tmp_path: pathlib.Path) -> None:
    """W6：越界尝试后，**不**回退到 ``default_audit_directory()``（该目录下无新文件）。"""
    default_dir = config_module.default_audit_directory()
    before = set(default_dir.iterdir()) if default_dir.is_dir() else set()

    # 触发两类越界（各自应失败）。
    bad = _outside_root(tmp_path)
    project = _write_project_config(tmp_path, directory=str(bad))
    with pytest.raises(ConfigError):
        config_module.load_config(user_file=tmp_path / "no_user.toml", project_file=project)
    with pytest.raises(PathNotAllowedError):
        JsonlAuditSink(bad)

    after = set(default_dir.iterdir()) if default_dir.is_dir() else set()
    assert after == before, "越界后不得回退默认审计目录写入任何文件"


@pytest.mark.security
def test_whitelist_removal_makes_attack_succeed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """W7（变异探针）：把 ``resolve_within`` 换成不过滤的直通实现后，W1~W5 至少失败 3 条
    ⇒ 证明断言依赖真实白名单、非恒过。"""

    def _pass_through(candidate: object, *args: object, **kwargs: object) -> pathlib.Path:
        return pathlib.Path(candidate).expanduser().resolve()  # type: ignore[arg-type]

    monkeypatch.setattr(config_module, "resolve_within", _pass_through)
    monkeypatch.setattr(audit_module, "resolve_within", _pass_through)

    bad = _outside_root(tmp_path)
    project = _write_project_config(tmp_path, directory=str(bad))

    # W1 失效：配置期不再拒。
    cfg = config_module.load_config(
        user_file=tmp_path / "no_user.toml",
        project_file=project,
    )
    assert cfg is not None
    # W5 失效：装配期不再拒。
    sink = JsonlAuditSink(bad)
    assert sink.path is not None


@pytest.mark.security
def test_config_layer_is_independent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """W8（只撤配置层）：配置期失效 ⇒ W1 不再拒；但装配层仍真 ⇒ W5 仍拒。"""
    monkeypatch.setattr(
        config_module,
        "resolve_within",
        lambda candidate, *a, **k: pathlib.Path(candidate).expanduser().resolve(),
    )

    bad = _outside_root(tmp_path)
    project = _write_project_config(tmp_path, directory=str(bad))

    # 配置层被撤 ⇒ 配置期不再抛 ConfigError。
    cfg = config_module.load_config(
        user_file=tmp_path / "no_user.toml",
        project_file=project,
    )
    assert cfg is not None
    # 装配层仍真 ⇒ 绕过配置的越界仍被拒。
    with pytest.raises(PathNotAllowedError):
        JsonlAuditSink(bad)


@pytest.mark.security
def test_assembly_layer_is_independent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """W8（只撤装配层）：装配期失效 ⇒ W5 不再拒；但配置层仍真 ⇒ W1 仍拒。"""
    monkeypatch.setattr(
        audit_module,
        "resolve_within",
        lambda candidate, *a, **k: pathlib.Path(candidate).expanduser().resolve(),
    )

    bad = _outside_root(tmp_path)
    project = _write_project_config(tmp_path, directory=str(bad))

    # 装配层被撤 ⇒ 绕过配置的越界不再抛 PathNotAllowedError。
    sink = JsonlAuditSink(bad)
    assert sink.path is not None
    # 配置层仍真 ⇒ 配置期越界仍被拒。
    with pytest.raises(ConfigError):
        config_module.load_config(
            user_file=tmp_path / "no_user.toml",
            project_file=project,
        )
