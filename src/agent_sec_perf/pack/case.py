"""案例契约（``case.toml``）的解析与校验：能力包内核的**输入边界**。

一个"案例"是内核的输入单位，声明四件事：

1. **现场**（``scene``）：模型只能看到的输入文件；
2. **动作面**（``pack_draft`` / ``pack_revise``）：每个阶段暴露哪些工具、要求哪些能力；
3. **任务**（``task_draft`` / ``task_revise``）：每个阶段给模型的文本；
4. **判据**（``report_*`` 与 ``[selftest]``）：产物必须满足什么，以及**用什么样例证明
   这套判据本身是有效的**。

来源：原先是 ``cli/demo.py`` 的 ``load_case``（``devlog 0031``）。本模块把它上移为
**内核契约**，理由是判据与案例是产品资产，不应绑在某个 CLI 子命令上；实现保持逐字
兼容（字段、默认值、错误信息），``cli.demo.load_case`` 现在只是"补默认路径"的薄包装。

**严格性**：必填段/键缺失、类型不符一律抛 :class:`CaseSpecError`（属装配期故障，
退出码 ``3``），**不回落默认值**——"案例写错了却按默认值跑起来"会让一次失败的调教
看起来像成功。
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from agent_sec_perf.foundation.errors import BenchError
from agent_sec_perf.foundation.paths import resolve_within

__all__ = [
    "CaseSpec",
    "CaseSpecError",
    "SelftestSpec",
    "load_case_spec",
]


class CaseSpecError(BenchError, ValueError):
    """案例声明非法。

    同时继承 :class:`ValueError`：原 ``cli.demo.load_case`` 抛的就是 ``ValueError``，
    保持既有调用方的 ``except ValueError`` 继续有效（不制造一处静默的行为变更）。
    """


@dataclass(frozen=True)
class SelftestSpec:
    """判据自证样例（**必须有**：没有样例的判据不得用于评估）。

    ``positive`` 必须让全部内容判据通过；``negative`` 必须至少让一条失败。
    两者的判定由 :mod:`agent_sec_perf.pack.criteria` 执行，本模块只做路径校验。
    """

    positive: Path
    negative: Path


@dataclass(frozen=True)
class CaseSpec:
    """一份已校验的案例声明（**唯一真源**：``case.toml``）。"""

    case_id: str
    title: str
    case_dir: Path
    scene_dir: Path
    pack_draft_dir: Path
    pack_revise_dir: Path
    task_draft: str
    task_revise: str
    min_tool_calls: int
    min_read_calls: int
    min_write_calls: int
    report_file: str
    report_substrings: tuple[str, ...]
    report_forbidden: tuple[str, ...]
    report_preserved_keys: tuple[str, ...]
    report_revised_key: str
    preferred_model: str
    max_completion_tokens: int
    request_timeout_s: float
    ready_timeout_s: float
    selftest: SelftestSpec | None

    @property
    def content_criteria_declared(self) -> bool:
        """是否声明了可自证的内容判据（内核据此判断"能否发布"）。"""
        return bool(self.report_substrings) and self.selftest is not None


def load_case_spec(case_dir: Path) -> CaseSpec:
    """读取并校验 ``case_dir/case.toml``。

    Args:
        case_dir: 案例目录。**必须存在**；路径本身由调用方决定（内核不猜仓库根）。

    Raises:
        CaseSpecError: 目录/文件缺失、TOML 非法、必填字段缺失或类型不符、
            现场/能力包目录不存在、自证样例越界或缺失。
    """
    base = Path(case_dir)
    if not base.is_dir():
        raise CaseSpecError(f"案例目录不存在：{base}")
    case_path = base / "case.toml"
    if not case_path.is_file():
        raise CaseSpecError(f"案例缺少 case.toml：{base}")

    try:
        document = tomllib.loads(case_path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise CaseSpecError("case.toml 不是合法的 UTF-8 文本") from exc
    except tomllib.TOMLDecodeError as exc:
        raise CaseSpecError("case.toml 不是合法的 TOML（不回显原文片段）") from exc

    case = _section(document, "case")
    model = _section(document, "model")
    selftest = _optional_selftest(document, base=base)

    scene_dir = _subdirectory(case, "scene", base=base)
    pack_draft_dir = _subdirectory(case, "pack_draft", base=base)
    pack_revise_dir = _subdirectory(case, "pack_revise", base=base)

    return CaseSpec(
        case_id=_require_str(case, "id"),
        title=_require_str(case, "title"),
        case_dir=base,
        scene_dir=scene_dir,
        pack_draft_dir=pack_draft_dir,
        pack_revise_dir=pack_revise_dir,
        task_draft=_require_str(case, "task_draft").strip(),
        task_revise=_require_str(case, "task_revise").strip(),
        min_tool_calls=_require_positive_int(case, "min_tool_calls"),
        min_read_calls=_require_positive_int(case, "min_read_calls"),
        min_write_calls=_require_positive_int(case, "min_write_calls"),
        report_file=_require_str(case, "report_file"),
        report_substrings=_require_str_list(case, "report_substrings"),
        report_forbidden=_require_str_list(case, "report_forbidden"),
        report_preserved_keys=_require_str_list(case, "report_preserved_keys"),
        report_revised_key=_require_str(case, "report_revised_key"),
        preferred_model=_require_str(model, "preferred"),
        max_completion_tokens=_require_positive_int(model, "max_completion_tokens"),
        request_timeout_s=_require_positive_float(model, "request_timeout_s"),
        ready_timeout_s=_require_positive_float(model, "ready_timeout_s"),
        selftest=selftest,
    )


# ---------------------------------------------------------------------------
# 解析辅助
# ---------------------------------------------------------------------------


def _section(document: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = document.get(name)
    if not isinstance(value, dict):
        raise CaseSpecError(f"case.toml 缺少 [{name}] 段")
    return cast("Mapping[str, object]", value)


def _optional_selftest(document: Mapping[str, object], *, base: Path) -> SelftestSpec | None:
    """读可选的 ``[selftest]``；存在但越界/缺文件 ⇒ 拒绝（不是"忽略"）。"""
    raw = document.get("selftest")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CaseSpecError("case.toml 的 [selftest] 必须是表（table）")
    table = cast("Mapping[str, object]", raw)
    positive = _fixture_path(table, "positive", base=base)
    negative = _fixture_path(table, "negative", base=base)
    return SelftestSpec(positive=positive, negative=negative)


def _fixture_path(table: Mapping[str, object], key: str, *, base: Path) -> Path:
    relative = _require_str(table, key, section_name="selftest")
    # 自证样例是不可信输入（可能随包分发）：规范化后必须落在案例目录内。
    path = resolve_within(base / relative, (base,), what=f"[selftest].{key}")
    if not path.is_file():
        raise CaseSpecError(f"[selftest].{key} 指向的文件不存在：{relative}")
    return path


def _subdirectory(section: Mapping[str, object], key: str, *, base: Path) -> Path:
    relative = _require_str(section, key)
    path = resolve_within(base / relative, (base,), what=f"case.{key}")
    if not path.is_dir():
        raise CaseSpecError(f"case.{key} 指向的目录不存在：{relative}")
    return path


def _require_str(section: Mapping[str, object], key: str, *, section_name: str = "case") -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value:
        raise CaseSpecError(f"case.toml 的 {key} 必须是非空字符串（[{section_name}]）")
    return value


def _require_positive_int(section: Mapping[str, object], key: str) -> int:
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CaseSpecError(f"case.toml 的 {key} 必须是正整数")
    return value


def _require_positive_float(section: Mapping[str, object], key: str) -> float:
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
        raise CaseSpecError(f"case.toml 的 {key} 必须是正数")
    return float(value)


def _require_str_list(section: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = section.get(key)
    if not isinstance(value, list) or not value:
        raise CaseSpecError(f"case.toml 的 {key} 必须是非空字符串数组")
    items: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry:
            raise CaseSpecError(f"case.toml 的 {key} 含非字符串项")
        items.append(entry)
    return tuple(items)
