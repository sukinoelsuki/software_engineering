"""判据：内容级检查与**判据自证**。

本模块只做一件事：把"报告内容算不算合格"写成**纯函数**，并用正/负两个样例证明
这套判据**分得开对错**。

为什么必须有自证（``docs/notes/evaluation-pitfalls.md`` 第 1 条）：

> 没有做过"已知正确必过、已知错误必败"这一步的判据，不得用于评估。

这条在本项目里不是空话：``devlog 0031`` §4.5 的第 ⑦ 轮就因为"判据把排版当内容"
误判过一次（少一个空格被判失败）。本模块的应对是**归一化**（去空白、转大写），
而自证是防止归一化写得过宽（把"缺值"也归一化掉）的机器检查。

**诚实边界**：自证只覆盖 ``CONTENT_CHECK_NAMES`` 三项。事件流级检查
（终态/seq/工具调用/审计回放）需要一次真实执行，**不在**本模块覆盖范围内，
bundle 的 manifest 会如实写出 ``covered`` / ``uncovered``，不得读成"全部判据已自证"。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from agent_sec_perf.pack.case import CaseSpec, CaseSpecError

__all__ = [
    "CONTENT_CHECK_NAMES",
    "TRACE_CHECK_NAMES",
    "CheckResult",
    "SelftestResult",
    "check_report",
    "normalize",
    "run_selftest",
]

#: 可由"一份报告文本"判定的检查（可自证）。
CONTENT_CHECK_NAMES: Final[tuple[str, ...]] = (
    "报告非空",
    "报告必含项",
    "报告禁含项",
    "报告键完整",
)

#: 需要一次真实执行（事件流 / 审计）才能判定的检查（**未**自证）。
TRACE_CHECK_NAMES: Final[tuple[str, ...]] = (
    "阶段完整",
    "阶段终态",
    "seq 连续",
    "工具集使用",
    "工具真的执行",
    "修订保真",
    "审计回放",
)


@dataclass(frozen=True)
class CheckResult:
    """一条判据的结论（与 ``cli.demo.VerifyCheck`` 同形，便于合并展示）。"""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class SelftestResult:
    """一次判据自证的结果。

    ``positive_ok`` 与 ``negative_ok`` **两者都要为真**：

    * ``positive_ok`` = 正例让**全部**内容判据通过；
    * ``negative_ok`` = 反例让**至少一条**内容判据失败。

    只满足前者说明判据太宽（反例也过），只满足后者说明判据太严（正例也挂）。
    """

    positive_ok: bool
    negative_ok: bool
    checks: tuple[CheckResult, ...]
    covered: tuple[str, ...]
    uncovered: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """判据是否可以用于评估（正例全过且反例至少失败一项）。"""
        return self.positive_ok and self.negative_ok


def normalize(text: str) -> str:
    """判据比较用的归一化：去掉全部空白并转大写。

    空白不承载语义：``配置值 8000 大于日志值 3000`` 与 ``配置值8000大于日志值3000``
    是同一句话（``devlog 0031`` §4.5 第 ⑦ 轮的实测）。
    """
    return "".join(text.split()).upper()


def check_report(case: CaseSpec, text: str) -> tuple[CheckResult, ...]:
    """对**落盘报告**逐条判定内容判据（纯函数，不读盘）。"""
    normalized = normalize(text)
    checks: list[CheckResult] = []

    checks.append(
        CheckResult(
            "报告非空",
            bool(normalized),
            "报告为空或只有空白" if not normalized else f"{len(text)} 字符",
        )
    )

    missing = [item for item in case.report_substrings if normalize(item) not in normalized]
    checks.append(
        CheckResult(
            "报告必含项",
            not missing,
            f"命中全部 {len(case.report_substrings)} 项" if not missing else f"缺少 {missing}",
        )
    )

    leftovers = [item for item in case.report_forbidden if normalize(item) in normalized]
    checks.append(
        CheckResult(
            "报告禁含项",
            not leftovers,
            "未出现禁止内容" if not leftovers else f"残留 {leftovers}",
        )
    )

    key_values = _key_values(text)
    problems: list[str] = []
    for key in (*case.report_preserved_keys, case.report_revised_key):
        count = len(key_values.get(key, ()))
        if count != 1:
            problems.append(f"{key} 出现 {count} 次（要求恰好 1 次）")
    checks.append(
        CheckResult(
            "报告键完整",
            not problems,
            "必填键各出现一次" if not problems else "；".join(problems),
        )
    )
    return tuple(checks)


def run_selftest(case: CaseSpec) -> SelftestResult:
    """用案例声明的正/负样例证明判据有效。

    Raises:
        CaseSpecError: ``case.toml`` 未声明 ``[selftest]``（**判据不可自证 ⇒ 拒绝发布**）。
    """
    spec = case.selftest
    if spec is None:
        raise CaseSpecError("未声明 [selftest]：判据无法自证，拒绝发布")

    positive_checks = check_report(case, _read_text(spec.positive))
    negative_checks = check_report(case, _read_text(spec.negative))

    positive_ok = all(item.ok for item in positive_checks)
    negative_ok = any(not item.ok for item in negative_checks)
    combined = tuple(
        CheckResult(
            name=f"正例：{positive.name}",
            ok=positive.ok,
            detail=positive.detail,
        )
        for positive in positive_checks
    ) + tuple(
        CheckResult(
            name=f"反例：{negative.name}",
            ok=not negative.ok,
            detail=f"按预期失败（{negative.detail}）" if not negative.ok else negative.detail,
        )
        for negative in negative_checks
    )
    return SelftestResult(
        positive_ok=positive_ok,
        negative_ok=negative_ok,
        checks=combined,
        covered=CONTENT_CHECK_NAMES,
        uncovered=TRACE_CHECK_NAMES,
    )


def _read_text(path: Path) -> str:
    """读一份自证样例；路径已由 ``load_case_spec`` 校验，这里只处理 IO 故障。"""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CaseSpecError(f"自证样例不可读（{type(exc).__name__}）") from exc


def _key_values(text: str) -> dict[str, list[str]]:
    """按 ``KEY=VALUE`` 抽出报告行（key 去空白并转大写；保留原值）。"""
    values: dict[str, list[str]] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        name = key.strip().upper()
        if separator and name:
            values.setdefault(name, []).append(value.strip())
    return values
