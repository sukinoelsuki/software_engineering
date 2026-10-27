"""内核状态机：把"这个包现在能做什么"变成**机器可读的一步**。

大模型（总控）不需要读代码，也不需要猜：它读 ``pack status --json``，拿到
``stage`` / ``blockers`` / ``next_actions``，选一个动作执行，再读下一次状态。
这条接口刻意做得很窄——**状态由内核计算，不由大模型声称**。

状态迁移（单向，除 ``broken`` 可经重建修复）::

    unbuilt ──build──► verified ──run──► evaluated
                          │
                          └── problems ──► broken

**没有 `built` 这个可观测状态**：``build`` 之后状态查询会立刻做一次校验，
"构建了但还没验证"只是构建命令内部的一瞬间，不对外暴露（少一个状态就少一处歧义）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from agent_sec_perf.pack.bundle import (
    EVIDENCE_DIR_NAME,
    MANIFEST_NAME,
    BundleError,
    Manifest,
    verify_bundle,
)

__all__ = ["BundleStage", "BundleStatus", "NextAction", "inspect_bundle"]


class BundleStage(StrEnum):
    """bundle 的当前阶段（只由**已发生的事实**决定）。"""

    UNBUILT = "unbuilt"
    VERIFIED = "verified"
    EVALUATED = "evaluated"
    BROKEN = "broken"


@dataclass(frozen=True)
class NextAction:
    """一个允许的下一步（``command`` 可直接复制执行）。"""

    id: str
    title: str
    command: str
    requires: str


@dataclass(frozen=True)
class BundleStatus:
    """一次状态查询的结果。"""

    bundle_dir: Path
    stage: BundleStage
    manifest: Manifest | None
    blockers: tuple[str, ...]
    evidence_files: tuple[str, ...]
    next_actions: tuple[NextAction, ...]

    @property
    def ok(self) -> bool:
        """当前包是否处于"可继续推进"的状态。"""
        return self.stage is not BundleStage.BROKEN and not self.blockers


def inspect_bundle(bundle_dir: Path) -> BundleStatus:
    """读 bundle 并计算状态。

    **不执行模型、不修改任何文件**：``pack status`` 是只读操作。
    """
    base = Path(bundle_dir)
    if not (base / MANIFEST_NAME).is_file():
        return BundleStatus(
            bundle_dir=base,
            stage=BundleStage.UNBUILT,
            manifest=None,
            blockers=("缺少 manifest.json：这个目录还不是一个能力包",),
            evidence_files=(),
            next_actions=(
                NextAction(
                    id="build",
                    title="从案例目录编译能力包",
                    command="agent-sec-perf pack build <案例目录> --out <本目录>",
                    requires="case.toml 与 [selftest] 样例",
                ),
            ),
        )

    try:
        result = verify_bundle(base)
    except BundleError as exc:
        return BundleStatus(
            bundle_dir=base,
            stage=BundleStage.BROKEN,
            manifest=None,
            blockers=(f"清单不可读：{exc}",),
            evidence_files=(),
            next_actions=(),
        )

    evidence = _evidence_files(base)
    blockers: list[str] = list(result.problems)
    if result.selftest is not None and not result.selftest.ok:
        blockers.append("判据自证未通过")

    if result.ok:
        stage = BundleStage.EVALUATED if evidence else BundleStage.VERIFIED
    else:
        # 构建产物存在但校验不过 ⇒ broken（而不是 built：built 尚未验证，不该单独存在）
        stage = BundleStage.BROKEN

    actions: list[NextAction] = []
    if stage is BundleStage.BROKEN:
        actions.append(
            NextAction(
                id="rebuild",
                title="修正问题后重新编译",
                command="agent-sec-perf pack build <案例目录> --out <本目录> --force",
                requires="blockers 全部消除",
            )
        )
    elif stage is BundleStage.VERIFIED:
        actions.append(
            NextAction(
                id="run",
                title="用端侧模型跑一次并留下证据",
                command=f"agent-sec-perf pack run {base} --model <GGUF 路径>",
                requires="本地 llama-server 与 GGUF 模型",
            )
        )
    elif stage is BundleStage.EVALUATED:
        actions.append(
            NextAction(
                id="plan",
                title="读证据、选一个结构性修复动作",
                command=f"agent-sec-perf pack plan {base} --json",
                requires="一次运行证据",
            )
        )
    actions.append(
        NextAction(
            id="inspect",
            title="查看清单、判据覆盖与文件摘要",
            command=f"agent-sec-perf pack verify {base} --json",
            requires="无",
        )
    )

    return BundleStatus(
        bundle_dir=base,
        stage=stage,
        manifest=result.manifest,
        blockers=tuple(blockers),
        evidence_files=evidence,
        next_actions=tuple(actions),
    )


def _evidence_files(base: Path) -> tuple[str, ...]:
    directory = base / EVIDENCE_DIR_NAME
    if not directory.is_dir():
        return ()
    return tuple(sorted(path.name for path in directory.glob("*.json") if path.is_file()))
