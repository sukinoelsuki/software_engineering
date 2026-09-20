"""领域包加载（``REQ-HARNESS-08``；``harness.md`` §4）。

领域包把"某个场景需要哪些工具、哪些能力、什么提示片段"写成**声明式配置**，
使能力授予与提示注入面收窄到一份可审计的文件。本模块是 harness 里**唯一读盘者**。

**安全立场（``ADR-0015`` §5.1.1 的 ``R5``）**：只加载声明式配置（TOML），
**禁止加载其中的 Python 代码**。这不是"尽力而为"：目录内一旦出现 ``.py`` / ``.pyc`` /
``__pycache__`` 就**拒绝加载**（``DomainPackError``），不"忽略它继续"——
因为无法区分"作者误放"与"投毒尝试"，而"这个包里带代码"这一事实一旦不留痕迹，
就等于允许一次静默降级（``ADR-0006`` §5.2 规则 ``S-2``）。

三条 fail-secure 取向（契约 §4.3 的失败模式表）：

1. **未知键 / 未知段一律拒绝**，不"向前兼容地忽略"。未知键是"作者以为生效、实际没生效"
   的唯一来源——把 ``allowlist`` 拼成 ``allowlis`` 会让白名单**静默失效**（fail-open）。
2. **未知名字一律拒绝**（工具名 / 能力名 / 风险等级），不跳过。口径与
   ``security/capabilities.py`` 对未知能力名"拒绝而非跳过"一致。
3. **路径先校验后读**：``directory`` 必须经 ``foundation.paths.resolve_within``
   落在允许的根内；越界 / 符号链接逃逸 ⇒ ``PathNotAllowedError``。
   允许的根 ``roots`` 是**必填 keyword-only、无默认值**——默认值会让"忘了传"退化为
   "任意路径"（与 ``audit.md`` §2.5 的 ``P1`` 同一取向）。

**不存 ``description``**：契约 §3.1 的 :class:`DomainPack` 字段表没有它；
但 §4.2 定义了它、§4.3 要求"取值超长 ⇒ 拒绝"，因此本模块**校验后丢弃**它——
"校验存在"与"字段被保留"是两件事，前者由失败模式表要求，后者由字段表决定。

依赖：``contracts`` + ``foundation``（``paths`` / ``errors``）+ ``harness.errors``。
不 import ``model`` / ``tools`` / ``security`` / ``cli``（契约 §3.2 的 H1）。
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from agent_sec_perf.contracts.policy import Capability, RiskLevel
from agent_sec_perf.foundation.paths import resolve_within
from agent_sec_perf.harness.errors import DomainPackError

__all__ = ["DomainPack", "load_pack"]

#: ``pack.name`` 的形状（契约 §4.2）：小写字母/数字开头，其后可含 ``-``，总长 ≤ 64。
_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

#: ``pack.version`` 的形状：语义化版本的三段数字（**只校验形状，不解析语义**）。
_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")

_MAX_DESCRIPTION_CHARS = 500
_MAX_FRAGMENTS = 8
_MAX_FRAGMENT_CHARS = 2000

#: ``output.format`` 的本轮固定小集合（契约 §4.2）；扩集合属契约变更，不走实现。
_ALLOWED_OUTPUT_FORMATS = frozenset({"markdown", "text"})

#: 出现即拒绝的目录名与后缀（``R5``；判据见模块 docstring）。
_FORBIDDEN_DIRECTORY_NAME = "__pycache__"
_FORBIDDEN_SUFFIXES = (".py", ".pyc")

_ALLOWED_SECTIONS = frozenset({"pack", "prompt", "tools", "security", "output"})
_PACK_KEYS = frozenset({"name", "version", "description"})
_PROMPT_KEYS = frozenset({"fragments"})
_TOOLS_KEYS = frozenset({"allowlist"})
_SECURITY_KEYS = frozenset({"capabilities", "risk_overrides"})
_OUTPUT_KEYS = frozenset({"format"})


@dataclass(frozen=True)
class DomainPack:
    """一份已校验的领域包（契约 §3.1）。

    ``tool_allowlist`` 与 ``capabilities_allowlist`` **只能收窄**：装配点把后者与用户
    授予求**交集**（``∩``，不是并集），且"包未声明 ⇒ 视为全授予"是禁止的
    （契约 §4.4 第 1 条）。``prompt_fragments`` 是**数据**，经
    ``prompts.pack_context_message`` 装配成 ``role=USER`` 消息，**永不进 SYSTEM 位置**。
    """

    name: str
    version: str
    prompt_fragments: tuple[str, ...]
    tool_allowlist: frozenset[str]
    capabilities_allowlist: frozenset[Capability]
    risk_overrides: Mapping[str, RiskLevel]
    output_format: str | None
    source: Path


def load_pack(
    directory: Path, *, roots: tuple[Path, ...], known_tools: frozenset[str]
) -> DomainPack:
    """加载并**严格校验**一个领域包目录（契约 §3.1 / §4）。

    Args:
        directory: 领域包目录。**不可信输入**：先经
            :func:`foundation.paths.resolve_within` 规范化并校验落在 ``roots`` 内。
        roots: 允许的根目录集合（进 :func:`resolve_within`）。**无默认值**：默认会让
            "忘了传"退化为"任意路径"（契约 §4.1 的 ``P4``）。
        known_tools: 已知工具名的全集（通常为
            ``frozenset(spec.name for spec in registry.specs())``）。
            ``tools.allowlist`` 与 ``security.risk_overrides`` 的键都必须落在其中。

    Returns:
        已校验的 :class:`DomainPack`；``source`` 是规范化后的 ``pack.toml`` 路径（证据用）。

    Raises:
        PathNotAllowedError: ``directory`` 不在 ``roots`` 内（含符号链接逃逸）。
        DomainPackError: 目录内有 Python 内容；``pack.toml`` 缺失/非普通文件；
            TOML 语法错误；缺必填键/段；类型不符；取值超长；未知键/未知段；
            未知工具名/能力名/风险等级。**任何一条都是拒绝启动，不得降级为"无 pack 继续跑"**。
        OSError: 目录不可读或不是目录（原样冒泡，不包装成 ``DomainPackError``——
            那是环境故障，与"包内容不合法"是两类处置）。
    """
    resolved = resolve_within(directory, roots, what="领域包目录")
    _reject_python_content(resolved)

    pack_path = resolved / "pack.toml"
    if not pack_path.is_file():
        raise DomainPackError("领域包缺少 pack.toml（或它不是普通文件）")

    data = _parse_toml(pack_path)
    return _build_pack(data, known_tools=known_tools, source=pack_path)


def _reject_python_content(directory: Path) -> None:
    """``P2``：目录内出现 ``.py`` / ``.pyc`` / ``__pycache__`` ⇒ 拒绝加载。

    **只扫描本层，不递归**（``P1``：只读 ``pack.toml`` 一个文件）。
    错误信息只报**类别**，不回显目录项名字——名字来自外部目录，可能带控制字符
    （log injection 的对手与 ``foundation.logging`` 同源）。
    """
    for entry in directory.iterdir():
        if entry.name == _FORBIDDEN_DIRECTORY_NAME:
            raise DomainPackError("领域包目录内出现 __pycache__：R5 只允许声明式配置")
        if entry.name.endswith(_FORBIDDEN_SUFFIXES):
            raise DomainPackError("领域包目录内出现 Python 源文件：R5 只允许声明式配置")


def _parse_toml(path: Path) -> Mapping[str, object]:
    """读并解析 ``pack.toml``；语法/编码错误 ⇒ ``DomainPackError``（**不回显原文**）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise DomainPackError("pack.toml 不是合法的 UTF-8 文本") from exc
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise DomainPackError("pack.toml 不是合法的 TOML（不回显原文片段）") from exc


def _build_pack(
    data: Mapping[str, object], *, known_tools: frozenset[str], source: Path
) -> DomainPack:
    """把已解析的表校验并装配成 :class:`DomainPack`（校验顺序无关正确性，只影响报错先后）。"""
    if set(data) - _ALLOWED_SECTIONS:
        raise DomainPackError("pack.toml 含未定义的段（未知段不得忽略，契约 §4.3）")

    pack_table = _required_table(data, "pack")
    _reject_unknown_keys(pack_table, _PACK_KEYS, section="pack")
    name = _required_str(pack_table, "name", section="pack")
    if _NAME_PATTERN.fullmatch(name) is None:
        raise DomainPackError("pack.name 形状非法（应为 ^[a-z0-9][a-z0-9-]{0,63}$）")
    version = _required_str(pack_table, "version", section="pack")
    if _VERSION_PATTERN.fullmatch(version) is None:
        raise DomainPackError("pack.version 形状非法（应为 MAJOR.MINOR.PATCH）")
    description = _optional_str(pack_table, "description", section="pack")
    if description is not None and len(description) > _MAX_DESCRIPTION_CHARS:
        raise DomainPackError("pack.description 超过 500 字符上限")

    prompt_table = _optional_table(data, "prompt")
    fragments: tuple[str, ...] = ()
    if prompt_table is not None:
        _reject_unknown_keys(prompt_table, _PROMPT_KEYS, section="prompt")
        raw_fragments = _optional_str_list(prompt_table, "fragments", section="prompt")
        if raw_fragments is not None:
            if len(raw_fragments) > _MAX_FRAGMENTS:
                raise DomainPackError("prompt.fragments 最多 8 条")
            if any(len(fragment) > _MAX_FRAGMENT_CHARS for fragment in raw_fragments):
                raise DomainPackError("prompt.fragments 单条不得超过 2000 字符")
            fragments = tuple(raw_fragments)

    tools_table = _required_table(data, "tools")
    _reject_unknown_keys(tools_table, _TOOLS_KEYS, section="tools")
    allowlist = _required_str_list(tools_table, "allowlist", section="tools")
    if any(tool_name not in known_tools for tool_name in allowlist):
        raise DomainPackError("tools.allowlist 含未知工具名（不得跳过，契约 §4.3）")

    security_table = _required_table(data, "security")
    _reject_unknown_keys(security_table, _SECURITY_KEYS, section="security")
    capability_names = _required_str_list(security_table, "capabilities", section="security")
    capabilities = frozenset(_parse_capability(name=item) for item in capability_names)

    risk_overrides = _parse_risk_overrides(security_table, known_tools=known_tools)

    output_table = _optional_table(data, "output")
    output_format: str | None = None
    if output_table is not None:
        _reject_unknown_keys(output_table, _OUTPUT_KEYS, section="output")
        output_format = _optional_str(output_table, "format", section="output")
        if output_format is not None and output_format not in _ALLOWED_OUTPUT_FORMATS:
            raise DomainPackError("output.format 取值非法（本轮仅 markdown | text）")

    return DomainPack(
        name=name,
        version=version,
        prompt_fragments=fragments,
        tool_allowlist=frozenset(allowlist),
        capabilities_allowlist=capabilities,
        risk_overrides=MappingProxyType(risk_overrides),
        output_format=output_format,
        source=source,
    )


def _parse_capability(*, name: str) -> Capability:
    """能力名必须是 :class:`Capability` 成员，**不跳过未知名**（契约 §4.3）。"""
    try:
        return Capability(name)
    except ValueError as exc:
        raise DomainPackError("security.capabilities 含未知能力名（不得跳过）") from exc


def _parse_risk_overrides(
    security_table: Mapping[str, object], *, known_tools: frozenset[str]
) -> dict[str, RiskLevel]:
    """``[security.risk_overrides]``：键必须是已知工具名，值必须是 :class:`RiskLevel` 成员。"""
    risk_table = _optional_table(security_table, "risk_overrides")
    if risk_table is None:
        return {}

    overrides: dict[str, RiskLevel] = {}
    for tool_name, level_name in risk_table.items():
        if tool_name not in known_tools:
            raise DomainPackError("security.risk_overrides 含未知工具名（不得跳过）")
        raw_level = _as_str(level_name, what="security.risk_overrides 的取值")
        try:
            overrides[tool_name] = RiskLevel(raw_level)
        except ValueError as exc:
            raise DomainPackError("security.risk_overrides 含未知风险等级") from exc
    return overrides


def _reject_unknown_keys(
    table: Mapping[str, object], allowed: frozenset[str], *, section: str
) -> None:
    """未知键不得忽略（契约 §4.3）：它们是"作者以为生效、实际没生效"的唯一来源。"""
    if set(table) - allowed:
        raise DomainPackError(f"[{section}] 段含未定义的键（未知键不得忽略，契约 §4.3）")


def _required_table(data: Mapping[str, object], key: str) -> Mapping[str, object]:
    if key not in data:
        raise DomainPackError(f"缺少必填段 [{key}]")
    return _as_table(data[key], what=f"[{key}]")


def _optional_table(data: Mapping[str, object], key: str) -> Mapping[str, object] | None:
    if key not in data:
        return None
    return _as_table(data[key], what=f"[{key}]")


def _as_table(value: object, *, what: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise DomainPackError(f"{what} 必须是表（table）")
    return value


def _as_str(value: object, *, what: str) -> str:
    if not isinstance(value, str):
        raise DomainPackError(f"{what} 必须是字符串")
    return value


def _as_str_list(value: object, *, what: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise DomainPackError(f"{what} 必须是字符串数组")
    return list(value)


def _required_str(table: Mapping[str, object], key: str, *, section: str) -> str:
    if key not in table:
        raise DomainPackError(f"[{section}] 缺少必填键 {key}")
    return _as_str(table[key], what=f"[{section}].{key}")


def _optional_str(table: Mapping[str, object], key: str, *, section: str) -> str | None:
    if key not in table:
        return None
    return _as_str(table[key], what=f"[{section}].{key}")


def _required_str_list(table: Mapping[str, object], key: str, *, section: str) -> list[str]:
    if key not in table:
        raise DomainPackError(f"[{section}] 缺少必填键 {key}")
    return _as_str_list(table[key], what=f"[{section}].{key}")


def _optional_str_list(table: Mapping[str, object], key: str, *, section: str) -> list[str] | None:
    if key not in table:
        return None
    return _as_str_list(table[key], what=f"[{section}].{key}")
