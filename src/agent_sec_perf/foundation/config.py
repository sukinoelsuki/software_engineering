"""配置读取与严格校验（来源：``~/.lowspec/config.toml`` 与项目级 ``.lowspec.toml``）。

设计取舍（逐条写"为什么"）：

1. **只用标准库 ``tomllib`` 解析**（ADR-0015 §5.2.3 行 C1，及 D2 的用途限定：
   pydantic 只用于信任边界校验与工具参数 JSON Schema，**配置解析不用它**）。
2. **配置文件属不可信输入**：它可能来自别处（仓库里的 `.lowspec.toml` 会跟着仓库走），
   因此**类型 / 长度 / 范围 / 键名**逐项校验，任何一项不合法 ⇒ 抛
   :class:`ConfigError` **拒绝加载**，而**不是**退回默认值继续跑——
   静默退回会让"我明明配了"与"系统其实没用"同时为真，且没人能察觉。
3. **未知键一律拒绝**（顶层段与段内键都是）：拼错的 ``[loging]`` 若被忽略，
   用户会以为配置生效了。这条与 pydantic 的 ``extra="forbid"`` 是同一取向。
4. **逐文件校验、再按键合并**（默认值 ← 用户级 ← 项目级）：一个文件里的非法值
   即便会被另一个文件覆盖，仍然报错——覆盖掉不等于它没写错。
5. **错误信息不回显配置值**：只报文件、键路径、期望类型与范围。
   配置里本不该有凭据（本模块的 schema 也没有凭据字段，凭据只经环境变量注入），
   但"把配错的值原样打进日志/终端"是没必要开的口子，因此只报**类型名**与长度。
6. 返回**不可变** ``dataclass``（``frozen=True``）：配置一旦加载即不再变化，
   避免运行期被就地改写而出现"同一次会话里配置前后不一致"。

本模块只读文件、不写文件、不起进程；依赖标准库 + ``platformdirs``（ADR-0015 §7.1 R1：
``foundation/`` 不得依赖任何业务包）。
"""

from __future__ import annotations

import pathlib
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

import platformdirs

from agent_sec_perf.foundation.errors import ConfigError, PathNotAllowedError
from agent_sec_perf.foundation.paths import resolve_within

#: 应用名：``platformdirs`` 用它拼出跨平台的用户目录（``REQ-PLAT-01``）。
APP_NAME = "lowspec"

#: 用户级配置文件名（位于 ``platformdirs`` 的 ``user_config_path`` 下）。
USER_CONFIG_FILENAME = "config.toml"

#: 项目级配置文件名（位于项目根目录）。
PROJECT_CONFIG_FILENAME = ".lowspec.toml"

#: 允许的日志级别（与 ``logging`` 的 5 个标准级别一致；``WARN`` / ``FATAL`` 等别名不接受）。
LOGGING_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

DEFAULT_LOG_LEVEL = "INFO"

#: 能力名书写规范：小写字母开头、只含小写字母/数字/下划线，最长 32 字符。
#: **只校验书写形状**，不校验语义——"这个能力是否存在"由 ``security/capabilities.py``
#: 的枚举负责（``foundation/`` 不得 import ``security/``，见 ADR-0015 §7.1 R1）。
CAPABILITY_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

#: 单次允许声明的能力条目数上限：防"配置文件被塞成巨型列表"这类资源耗尽。
MAX_GRANTED_CAPABILITIES = 64

#: 审计目录路径长度上限（字节级上限由文件系统决定，这里只挡明显异常的输入）。
MAX_PATH_LENGTH = 4096

#: 审计保留天数范围（与基准数据保留期的量级一致：至少 1 天、至多 10 年）。
AUDIT_RETENTION_DAYS_MIN = 1
AUDIT_RETENTION_DAYS_MAX = 3650

DEFAULT_AUDIT_RETENTION_DAYS = 30

#: 允许出现的配置段；未列出的段一律拒绝（理由见模块 docstring 第 3 条）。
ALLOWED_SECTIONS: frozenset[str] = frozenset({"logging", "policy", "audit"})

_ALLOWED_KEYS: dict[str, frozenset[str]] = {
    "logging": frozenset({"level"}),
    "policy": frozenset({"granted_capabilities"}),
    "audit": frozenset({"directory", "retention_days"}),
}


def user_config_file() -> pathlib.Path:
    """用户级配置文件路径（跨平台由 ``platformdirs`` 决定，不手写 XDG/Windows 分支）。"""
    return platformdirs.PlatformDirs(APP_NAME).user_config_path / USER_CONFIG_FILENAME


def project_config_file(start: pathlib.Path | None = None) -> pathlib.Path:
    """项目级配置文件路径：``<start>/.lowspec.toml``（``start`` 默认当前工作目录）。

    **只查 ``start`` 这一层，不向父目录搜索**：向上搜索会把 ``/tmp`` 一类共享目录里的同名
    文件也拉进信任范围，且"这份配置从哪来"变得不可预测（隐式依赖 cwd 的祖先目录）。
    """
    base = pathlib.Path.cwd() if start is None else pathlib.Path(start)
    return base / PROJECT_CONFIG_FILENAME


def default_audit_directory() -> pathlib.Path:
    """默认审计目录：``platformdirs`` 的用户状态目录下的 ``audit/``（始终为绝对路径）。"""
    return platformdirs.PlatformDirs(APP_NAME).user_state_path / "audit"


#: 允许作为审计落点的根目录集合：**常量，不来自配置**（`docs/design/interfaces/audit.md` §2.5 的 P1）。
#: 项目级 ``.lowspec.toml`` 跟着仓库走、属不可信输入；若由它决定"允许哪些根"，等于**把白名单交给
#: 攻击者**。当前只含默认审计目录：审计只需要"写审计"，根收紧到审计子树即满足最小权限。
ALLOWED_AUDIT_ROOTS: tuple[pathlib.Path, ...] = (default_audit_directory(),)


@dataclass(frozen=True)
class LoggingConfig:
    """日志配置。``level`` 取 :data:`LOGGING_LEVELS` 中的名称（大写）。"""

    level: str = DEFAULT_LOG_LEVEL


@dataclass(frozen=True)
class PolicyConfig:
    """策略配置。

    ``granted_capabilities`` 是**显式授予**的能力名集合；默认空元组 ⇒ **一个能力也不授予**
    （default-deny：`REQ-SEC-01`）。名字到 ``Capability`` 枚举的解析由
    ``security/capabilities.py`` 完成（本层不得 import 业务包）。
    """

    granted_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuditConfig:
    """审计配置。``directory`` 必须是**绝对路径**（相对路径会随 cwd 变化，无法作为证据落点）。"""

    directory: pathlib.Path = field(default_factory=default_audit_directory)
    retention_days: int = DEFAULT_AUDIT_RETENTION_DAYS


@dataclass(frozen=True)
class AppConfig:
    """合并后的生效配置（不可变），并记录**实际读到**的文件。

    ``sources`` 按读取顺序（用户级 → 项目级）列出真正存在的配置文件：没有它，
    "为什么这台机器上是这个行为"只能靠猜（``REQ-PERF-06`` 要求配置生效过程可留痕）。
    """

    logging: LoggingConfig = field(default_factory=LoggingConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    audit: AuditConfig = field(default_factory=AuditConfig)
    sources: tuple[pathlib.Path, ...] = ()


def load_config(
    *,
    start: pathlib.Path | None = None,
    user_file: pathlib.Path | None = None,
    project_file: pathlib.Path | None = None,
) -> AppConfig:
    """加载并校验配置：默认值 ← 用户级 ← 项目级（项目级覆盖用户级）。

    Args:
        start: 项目级配置的所在目录；默认当前工作目录。
        user_file: 显式指定用户级配置路径（覆盖 ``platformdirs`` 的默认位置）。
        project_file: 显式指定项目级配置路径。

    Returns:
        合并后的不可变配置；``sources`` 为实际读到的文件（不存在的不计入）。

    Raises:
        ConfigError: 任何**已存在**的配置文件非法（不是合法 TOML、不是 UTF-8、
            类型/长度/范围不合法、出现未知段或未知键、读取出错）。
            文件**不存在**不算错误——配置是可选的。
    """
    paths = (
        user_config_file() if user_file is None else pathlib.Path(user_file),
        project_config_file(start) if project_file is None else pathlib.Path(project_file),
    )

    sections: dict[str, dict[str, object]] = {}
    sources: list[pathlib.Path] = []
    for path in paths:
        document = _read_toml(path)
        if document is None:
            continue
        sources.append(path)
        for section, values in _validate_document(document, source=path).items():
            sections.setdefault(section, {}).update(values)

    return _build(sections, sources=tuple(sources))


def _read_toml(path: pathlib.Path) -> Mapping[str, object] | None:
    """读取 TOML 文档；文件不存在返回 ``None``。"""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        msg = f"配置文件无法读取：{path}（{type(exc).__name__}）"
        raise ConfigError(msg) from exc

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        msg = f"配置文件不是 UTF-8 文本：{path}（{exc.reason}）"
        raise ConfigError(msg) from exc

    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        # TOMLDecodeError 的消息只含原因与行列位置（2026-09-19 实测 4 类错误：不含配置内容），
        # 因此可安全带出；带位置信息才能让人找得到那一行。
        msg = f"配置文件不是合法的 TOML：{path}（{exc}）"
        raise ConfigError(msg) from exc
    return document


def _validate_document(
    document: Mapping[str, object], *, source: pathlib.Path
) -> dict[str, dict[str, object]]:
    """校验整份文档的结构（段名、段类型、段内键名），返回"段 → 已校验值"。"""
    validated: dict[str, dict[str, object]] = {}
    for section, value in document.items():
        if section not in ALLOWED_SECTIONS:
            raise _invalid(source, section, "不是已知的配置段")
        if not isinstance(value, Mapping):
            raise _invalid(source, section, "必须是配置表（TOML 的 [段]）")

        values = cast("Mapping[str, object]", value)
        unknown = sorted(str(key) for key in values if key not in _ALLOWED_KEYS[section])
        if unknown:
            raise _invalid(source, f"{section}.{unknown[0]}", "不是已知的配置键")
        validated[section] = _validate_section(section, values, source=source)
    return validated


def _validate_section(
    section: str, values: Mapping[str, object], *, source: pathlib.Path
) -> dict[str, object]:
    """按段分派校验，返回该段已校验（含规范化）的键值。"""
    if section == "logging":
        return _validate_logging(values, source=source)
    if section == "policy":
        return _validate_policy(values, source=source)
    return _validate_audit(values, source=source)


def _validate_logging(values: Mapping[str, object], *, source: pathlib.Path) -> dict[str, object]:
    """``[logging]``：``level`` 必须是 5 个标准级别之一（大小写不敏感，规范化为大写）。"""
    result: dict[str, object] = {}
    if "level" in values:
        level = values["level"]
        if not isinstance(level, str):
            raise _invalid(source, "logging.level", f"必须是字符串（实际是 {_type_name(level)}）")
        normalized = level.strip().upper()
        if normalized not in LOGGING_LEVELS:
            allowed = " / ".join(LOGGING_LEVELS)
            raise _invalid(source, "logging.level", f"必须是 {allowed} 之一")
        result["level"] = normalized
    return result


def _validate_policy(values: Mapping[str, object], *, source: pathlib.Path) -> dict[str, object]:
    """``[policy]``：``granted_capabilities`` 必须是能力名组成的字符串数组。"""
    result: dict[str, object] = {}
    if "granted_capabilities" in values:
        declared = values["granted_capabilities"]
        if not isinstance(declared, list):
            raise _invalid(
                source,
                "policy.granted_capabilities",
                f"必须是字符串数组（实际是 {_type_name(declared)}）",
            )
        if len(declared) > MAX_GRANTED_CAPABILITIES:
            raise _invalid(
                source,
                "policy.granted_capabilities",
                f"条目数不得超过 {MAX_GRANTED_CAPABILITIES}",
            )
        names: list[str] = []
        for item in declared:
            if not isinstance(item, str):
                raise _invalid(
                    source,
                    "policy.granted_capabilities",
                    f"条目必须是字符串（实际是 {_type_name(item)}）",
                )
            if not CAPABILITY_NAME_PATTERN.fullmatch(item):
                raise _invalid(
                    source,
                    "policy.granted_capabilities",
                    "条目必须是形如 read_file 的能力名（小写字母开头，只含小写字母/数字/下划线，"
                    "最长 32 字符）",
                )
            names.append(item)
        result["granted_capabilities"] = tuple(names)
    return result


def _validate_audit(values: Mapping[str, object], *, source: pathlib.Path) -> dict[str, object]:
    """``[audit]``：``directory`` 为绝对路径、``retention_days`` 为受限整数。"""
    result: dict[str, object] = {}
    if "directory" in values:
        result["directory"] = _as_absolute_directory(values["directory"], source=source)
    if "retention_days" in values:
        result["retention_days"] = _as_bounded_int(
            values["retention_days"],
            source=source,
            key="audit.retention_days",
            minimum=AUDIT_RETENTION_DAYS_MIN,
            maximum=AUDIT_RETENTION_DAYS_MAX,
        )
    return result


def _as_absolute_directory(value: object, *, source: pathlib.Path) -> pathlib.Path:
    """把 ``audit.directory`` 校验成**允许根之内**的规范绝对路径（形状校验 + 白名单）。

    两层都需要：**形状**给出"配置里到底写错了什么"的精确理由；**白名单**才是安全边界
    （``resolve_within``）。只校验"是不是绝对路径 + 有没有 NUL"并不满足 `SECURITY.md` 的
    路径白名单要求——那会让配置变成一个**任意路径追加写**原语（`audit.md` §2.5 的 P2）。

    ``..`` 上跳与符号链接逃逸由 ``resolve_within`` 内部的 ``expanduser().resolve()`` 展开后判定，
    因此天然被拒；``~`` 展开后落在根外同样被拒（这是预期的收紧）。

    Returns:
        **已 ``resolve``** 的路径（不是原始字符串）：消费方拿到的就是被判定过的那一条，
        避免"校验一条、使用另一条"。
    """
    if not isinstance(value, str) or not value.strip():
        raise _invalid(
            source, "audit.directory", f"必须是非空字符串路径（实际是 {_type_name(value)}）"
        )
    if len(value) > MAX_PATH_LENGTH:
        raise _invalid(source, "audit.directory", f"路径长度不得超过 {MAX_PATH_LENGTH} 字符")
    if "\x00" in value:
        raise _invalid(source, "audit.directory", "路径不得包含 NUL 字节")

    path = pathlib.Path(value).expanduser()
    if not path.is_absolute():
        raise _invalid(source, "audit.directory", "必须是绝对路径或 ~ 开头（相对路径随 cwd 变化）")

    try:
        return resolve_within(path, ALLOWED_AUDIT_ROOTS, what="audit.directory")
    except PathNotAllowedError as exc:
        allowed = "、".join(str(root) for root in ALLOWED_AUDIT_ROOTS)
        # 越界 ⇒ ConfigError（配置期语义），**不得**回退默认目录（audit.md §2.5 的 P2/P5）。
        raise _invalid(
            source, "audit.directory", f"不在允许的审计根目录内（允许：{allowed}）"
        ) from exc


def _as_bounded_int(
    value: object, *, source: pathlib.Path, key: str, minimum: int, maximum: int
) -> int:
    """校验受限整数。

    ``bool`` 必须先被排除：Python 里 ``isinstance(True, int)`` 为真，若不显式挡住，
    ``retention_days = true`` 会被静默当成 ``1`` 接受（TOML 的 ``true`` 与整数是两种类型）。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid(source, key, f"必须是整数（实际是 {_type_name(value)}）")
    if not minimum <= value <= maximum:
        raise _invalid(source, key, f"必须在 {minimum}~{maximum} 之间")
    return value


def _build(
    sections: Mapping[str, Mapping[str, object]], *, sources: tuple[pathlib.Path, ...]
) -> AppConfig:
    """按合并结果构造不可变配置。

    这里的 ``cast`` 是必要的：中间表示是 ``dict[str, object]``，而各值在
    :func:`_validate_section` 中已逐项校验（类型不合法在那一步就已抛 ``ConfigError``）。
    """
    logging_values = sections.get("logging", {})
    policy_values = sections.get("policy", {})
    audit_values = sections.get("audit", {})

    return AppConfig(
        logging=LoggingConfig(level=cast("str", logging_values.get("level", DEFAULT_LOG_LEVEL))),
        policy=PolicyConfig(
            granted_capabilities=cast(
                "tuple[str, ...]", policy_values.get("granted_capabilities", ())
            )
        ),
        audit=AuditConfig(
            directory=cast(
                "pathlib.Path",
                audit_values.get("directory", default_audit_directory()),
            ),
            retention_days=cast(
                "int", audit_values.get("retention_days", DEFAULT_AUDIT_RETENTION_DAYS)
            ),
        ),
        sources=sources,
    )


def _invalid(source: pathlib.Path, key: str, reason: str) -> ConfigError:
    """构造统一的"配置非法"异常（只报位置与原因，不回显配置值）。"""
    return ConfigError(f"配置非法：{source} 的 [{key}] {reason}")


def _type_name(value: object) -> str:
    """类型名（用于错误信息；不回显值本身）。"""
    return type(value).__name__
