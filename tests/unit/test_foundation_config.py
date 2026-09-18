"""配置加载与严格校验的行为断言（G4；fail-secure）。

用例覆盖：默认值、逐键覆盖、类型/范围/长度/键名校验、以及"非法输入一律拒绝"的失败路径。
"""

from __future__ import annotations

import dataclasses
import pathlib

import pytest

from agent_sec_perf.foundation import config as config_module
from agent_sec_perf.foundation.config import (
    ALLOWED_SECTIONS,
    DEFAULT_AUDIT_RETENTION_DAYS,
    LOGGING_LEVELS,
    MAX_GRANTED_CAPABILITIES,
    AppConfig,
    load_config,
    project_config_file,
    user_config_file,
)
from agent_sec_perf.foundation.errors import ConfigError


def _write(path: pathlib.Path, text: str) -> pathlib.Path:
    path.write_text(text, encoding="utf-8")
    return path


def _load(
    tmp_path: pathlib.Path, *, user: str | None = None, project: str | None = None
) -> AppConfig:
    """把内容写进临时目录再加载（不触碰真实的 ``~/.lowspec``）。"""
    user_file = (
        _write(tmp_path / "user.toml", user) if user is not None else tmp_path / "absent-user.toml"
    )
    project_file = (
        _write(tmp_path / "project.toml", project)
        if project is not None
        else tmp_path / "absent-project.toml"
    )
    return load_config(user_file=user_file, project_file=project_file)


@pytest.fixture
def audit_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """把"允许的审计根"换到临时目录。

    两个作用：① 测试必须 hermetic——不往用户的真实状态目录（``~/.local/state/lowspec/audit``）
    里写东西；② 顺带证明常量是**调用时**从模块属性读取的，否则本 fixture 会静默失效。
    """
    root = tmp_path / "state" / "audit"
    root.mkdir(parents=True)
    monkeypatch.setattr(config_module, "ALLOWED_AUDIT_ROOTS", (root,))
    return root


def _audit_directory(path: pathlib.Path) -> str:
    """生成 ``[audit] directory`` 配置片段。

    用 TOML 的**字面字符串**（单引号）：Windows 路径里的反斜杠在基本字符串中会被当成转义序列
    ⇒ 在那边解析失败（``REQ-PLAT-01``：测试也要跨平台成立）。
    """
    return f"[audit]\ndirectory = '{path}'\n"


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_config_paths_are_derived_from_platformdirs_and_start_dir(tmp_path: pathlib.Path) -> None:
    """用户级路径经 ``platformdirs`` 解析，项目级只查给定的那一层目录。"""
    assert user_config_file().name == "config.toml"
    assert user_config_file().parent.name == "lowspec"
    assert user_config_file().is_absolute()

    assert project_config_file(tmp_path) == tmp_path / ".lowspec.toml"


@pytest.mark.unit
def test_missing_config_files_are_not_an_error(tmp_path: pathlib.Path) -> None:
    """配置是可选的：文件不存在 ⇒ 用默认值，且 ``sources`` 为空。"""
    config = _load(tmp_path)

    assert config.logging.level == "INFO"
    assert config.policy.granted_capabilities == ()
    assert config.audit.retention_days == DEFAULT_AUDIT_RETENTION_DAYS
    assert config.sources == ()
    assert config.audit.directory.is_absolute()


@pytest.mark.unit
def test_defaults_deny_every_capability(tmp_path: pathlib.Path) -> None:
    """default-deny：没有显式声明 ⇒ 一个能力也不授予。"""
    config = _load(tmp_path, user="", project="")

    assert config.policy.granted_capabilities == ()


# ---------------------------------------------------------------------------
# 合并与取值
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_project_config_overrides_user_config_key_by_key(tmp_path: pathlib.Path) -> None:
    """按键合并：项目级只覆盖它自己声明的键，其余沿用用户级；``sources`` 记录读取顺序。"""
    config = _load(
        tmp_path,
        user='[logging]\nlevel = "WARNING"\n[audit]\nretention_days = 7\n',
        project='[logging]\nlevel = "DEBUG"\n',
    )

    assert config.logging.level == "DEBUG"
    assert config.audit.retention_days == 7
    assert [path.name for path in config.sources] == ["user.toml", "project.toml"]


@pytest.mark.unit
@pytest.mark.parametrize("level", LOGGING_LEVELS)
def test_every_standard_level_is_accepted(tmp_path: pathlib.Path, level: str) -> None:
    """5 个标准级别逐个可用（``LOGGING_LEVELS`` 就是门禁集合本身）。"""
    config = _load(tmp_path, user=f'[logging]\nlevel = "{level}"\n')

    assert config.logging.level == level


@pytest.mark.unit
def test_lowercase_level_is_normalized_to_uppercase(tmp_path: pathlib.Path) -> None:
    """大小写不敏感地规范化，避免"配置能读懂但门禁不认"的二元状态。"""
    config = _load(tmp_path, user='[logging]\nlevel = "info"\n')

    assert config.logging.level == "INFO"


@pytest.mark.unit
def test_audit_directory_equal_to_the_allowed_root_is_accepted(
    tmp_path: pathlib.Path, audit_root: pathlib.Path
) -> None:
    """允许根本身是合法取值，且返回的是**已 resolve 的绝对路径**（不是原始字符串）。"""
    config = _load(tmp_path, user=_audit_directory(audit_root))

    assert config.audit.directory == audit_root.resolve()
    assert config.audit.directory.is_absolute()


@pytest.mark.unit
def test_audit_directory_inside_the_allowed_root_is_accepted(
    tmp_path: pathlib.Path, audit_root: pathlib.Path
) -> None:
    """根内子目录合法（审计可按会话 / 日期分子目录）。"""
    target = audit_root / "2026-09-19"

    config = _load(tmp_path, user=_audit_directory(target))

    assert config.audit.directory == target.resolve()


@pytest.mark.unit
def test_audit_directory_outside_the_allowed_root_is_rejected_without_creating_it(
    tmp_path: pathlib.Path, audit_root: pathlib.Path
) -> None:
    """根外路径 ⇒ ``ConfigError``（配置期语义），**且不创建该目录**（防"先建后拒"）。"""
    outside = tmp_path / "outside"

    with pytest.raises(ConfigError) as excinfo:
        _load(tmp_path, user=_audit_directory(outside))

    assert "audit.directory" in str(excinfo.value)
    assert not outside.exists()


@pytest.mark.unit
def test_audit_directory_parent_traversal_is_rejected(
    tmp_path: pathlib.Path, audit_root: pathlib.Path
) -> None:
    """``..`` 上跳：``resolve()`` 展开后落在根外 ⇒ 拒绝。"""
    escape = audit_root / ".." / ".." / "ssh"

    with pytest.raises(ConfigError):
        _load(tmp_path, user=_audit_directory(escape))


@pytest.mark.unit
def test_audit_directory_home_expansion_outside_the_root_is_rejected(
    tmp_path: pathlib.Path, audit_root: pathlib.Path
) -> None:
    """``~`` 展开后落在根外 ⇒ 拒绝（裁决预期的收紧：**形状合法 ≠ 被允许**）。"""
    with pytest.raises(ConfigError):
        _load(tmp_path, user='[audit]\ndirectory = "~/audit-dummy"\n')


@pytest.mark.unit
def test_audit_directory_symlink_escape_is_rejected(
    tmp_path: pathlib.Path, audit_root: pathlib.Path
) -> None:
    """根内的符号链接指向根外 ⇒ ``resolve()`` 展开符号链接后落在根外 ⇒ 拒绝。"""
    link = audit_root / "link"
    link.symlink_to(tmp_path / "outside")

    with pytest.raises(ConfigError):
        _load(tmp_path, user=_audit_directory(link))


@pytest.mark.unit
def test_audit_directory_default_is_inside_the_allowed_roots() -> None:
    """默认审计目录必须落在允许根内（否则"不配置"这一默认路径自己就会被拒）。"""
    default = config_module.default_audit_directory()
    resolved = default.expanduser().resolve()

    assert any(
        resolved == root.expanduser().resolve() or root.expanduser().resolve() in resolved.parents
        for root in config_module.ALLOWED_AUDIT_ROOTS
    )


@pytest.mark.unit
def test_capability_names_are_kept_in_declaration_order(tmp_path: pathlib.Path) -> None:
    """能力名解析为元组且保持声明顺序（顺序稳定 ⇒ 日志与审计可比对）。"""
    config = _load(tmp_path, user='[policy]\ngranted_capabilities = ["read_file", "write_file"]\n')

    assert config.policy.granted_capabilities == ("read_file", "write_file")


@pytest.mark.unit
def test_loaded_config_is_immutable() -> None:
    """返回的配置不可变：就地赋值必须失败（配置在会话内不得被改写）。"""
    config = AppConfig()

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.logging.__setattr__("level", "DEBUG")


# ---------------------------------------------------------------------------
# 非法输入：一律拒绝（fail-secure）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_unknown_section_is_rejected(tmp_path: pathlib.Path) -> None:
    """拼错的段名必须报错，而不是被静默忽略（否则用户以为配置生效了）。"""
    with pytest.raises(ConfigError) as excinfo:
        _load(tmp_path, user='[loging]\nlevel = "DEBUG"\n')

    assert "loging" in str(excinfo.value)
    assert ALLOWED_SECTIONS  # 段集合非空，且上面那个名字不在其中
    assert "loging" not in ALLOWED_SECTIONS


@pytest.mark.unit
def test_unknown_key_inside_known_section_is_rejected(tmp_path: pathlib.Path) -> None:
    """段内未知键同样拒绝。"""
    with pytest.raises(ConfigError) as excinfo:
        _load(tmp_path, user='[logging]\nleval = "DEBUG"\n')

    assert "logging.leval" in str(excinfo.value)


@pytest.mark.unit
def test_wrong_type_is_rejected_for_every_field(tmp_path: pathlib.Path) -> None:
    """类型不符即拒绝（三处字段各一例）。"""
    with pytest.raises(ConfigError):
        _load(tmp_path, user="[logging]\nlevel = 3\n")
    with pytest.raises(ConfigError):
        _load(tmp_path, user='[policy]\ngranted_capabilities = "read_file"\n')
    with pytest.raises(ConfigError):
        _load(tmp_path, user='[audit]\nretention_days = "30"\n')


@pytest.mark.unit
def test_boolean_is_not_accepted_as_retention_days(tmp_path: pathlib.Path) -> None:
    """TOML 的 ``true`` 不得被当成整数 1 接受（Python 里 ``bool`` 是 ``int`` 的子类）。"""
    with pytest.raises(ConfigError):
        _load(tmp_path, user="[audit]\nretention_days = true\n")


@pytest.mark.unit
@pytest.mark.parametrize("days", ["0", "3651"])
def test_retention_days_outside_range_is_rejected(tmp_path: pathlib.Path, days: str) -> None:
    """范围外的保留天数即拒绝（越界不是"夹到边界"，是配置错了）。"""
    with pytest.raises(ConfigError):
        _load(tmp_path, user=f"[audit]\nretention_days = {days}\n")


@pytest.mark.unit
@pytest.mark.parametrize(
    "name",
    ["", "Read_File", "read-file", "read file", "../etc/passwd", "a" * 33, "read_file\\u000A"],
)
def test_capability_name_shape_is_validated(tmp_path: pathlib.Path, name: str) -> None:
    """能力名只接受 ``^[a-z][a-z0-9_]{0,31}$``：大小写、分隔符、路径样式与超长名一律拒绝。"""
    with pytest.raises(ConfigError):
        _load(tmp_path, user=f'[policy]\ngranted_capabilities = ["{name}"]\n')


@pytest.mark.unit
def test_capability_count_limit_is_enforced(tmp_path: pathlib.Path) -> None:
    """条目数有上限：等于上限可接受、超一条即拒绝。"""
    at_limit = ", ".join(f'"cap_{index}"' for index in range(MAX_GRANTED_CAPABILITIES))
    over_limit = ", ".join(f'"cap_{index}"' for index in range(MAX_GRANTED_CAPABILITIES + 1))

    config = _load(tmp_path, user=f"[policy]\ngranted_capabilities = [{at_limit}]\n")
    assert len(config.policy.granted_capabilities) == MAX_GRANTED_CAPABILITIES

    with pytest.raises(ConfigError):
        _load(tmp_path, user=f"[policy]\ngranted_capabilities = [{over_limit}]\n")


@pytest.mark.unit
@pytest.mark.parametrize("value", ['"relative/audit"', '"./audit"', '""'])
def test_relative_or_empty_audit_directory_is_rejected(tmp_path: pathlib.Path, value: str) -> None:
    """审计目录必须是绝对路径（相对路径会随 cwd 变化，不能作为证据落点）。"""
    with pytest.raises(ConfigError):
        _load(tmp_path, user=f"[audit]\ndirectory = {value}\n")


@pytest.mark.unit
def test_audit_directory_with_nul_byte_is_rejected(tmp_path: pathlib.Path) -> None:
    """路径含 NUL 字节即拒绝（OS 会在 NUL 处截断，属经典的绕过手法）。"""
    with pytest.raises(ConfigError):
        _load(tmp_path, user='[audit]\ndirectory = "/tmp/before\\u0000after"\n')


@pytest.mark.unit
def test_malformed_toml_is_rejected_without_echoing_content(tmp_path: pathlib.Path) -> None:
    """TOML 语法错误即拒绝；错误信息给出位置，但**不含**配置内容。"""
    with pytest.raises(ConfigError) as excinfo:
        _load(tmp_path, user='[policy]\ngranted_capabilities = ["sk-live-dummy-secret\n')

    message = str(excinfo.value)
    assert "TOML" in message
    assert "sk-live-dummy-secret" not in message


@pytest.mark.unit
def test_invalid_values_are_not_echoed_in_error_message(tmp_path: pathlib.Path) -> None:
    """非法**值**不得出现在错误信息里（配置内容没必要进日志/终端）。"""
    with pytest.raises(ConfigError) as excinfo:
        _load(tmp_path, user='[policy]\ngranted_capabilities = ["sk-live-dummy-secret"]\n')

    message = str(excinfo.value)
    assert "policy.granted_capabilities" in message
    assert "sk-live-dummy-secret" not in message


@pytest.mark.unit
def test_non_utf8_config_is_rejected(tmp_path: pathlib.Path) -> None:
    """非 UTF-8 文件即拒绝（TOML 规范要求 UTF-8；静默按替换字符解析会读出鬼值）。"""
    bad = tmp_path / "bad.toml"
    bad.write_bytes(b"\xff\xfe\x00[logging]\n")

    with pytest.raises(ConfigError):
        load_config(user_file=bad, project_file=tmp_path / "absent.toml")


@pytest.mark.unit
def test_unreadable_config_is_rejected_not_silently_ignored(tmp_path: pathlib.Path) -> None:
    """存在但读不了 ⇒ 报错（把"读失败"当成"没配置"会把配置错误藏起来）。

    用**目录**冒充配置文件来触发 ``OSError``：这种方式不依赖权限位，
    在以 root 运行的容器里同样成立。
    """
    directory = tmp_path / "config-as-directory.toml"
    directory.mkdir()

    with pytest.raises(ConfigError):
        load_config(user_file=directory, project_file=tmp_path / "absent.toml")


@pytest.mark.unit
def test_invalid_user_config_is_rejected_even_if_project_overrides_it(
    tmp_path: pathlib.Path,
) -> None:
    """用户级配置里的非法值即便会被项目级覆盖，也必须报错（覆盖 ≠ 没写错）。"""
    with pytest.raises(ConfigError):
        _load(
            tmp_path, user='[logging]\nlevel = "VERBOSE"\n', project='[logging]\nlevel = "INFO"\n'
        )
