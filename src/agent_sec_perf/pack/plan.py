"""修复动作目录：把"调教经验"写成**机器可读的白名单**。

这份目录是本项目最值得沉淀的资产（``docs/proposals/0005-capability-pack-compiler.md`` §3.2）。
它回答的不是"怎么改 prompt"，而是：

> 观测到**哪一类**失效时，**只允许**做哪一类结构性修改。

> **2026-10-27 追加 `F9` 的理由**（实测驱动）：Qwen3-1.7B 在同一案例上反复调用
> `read_file` 8 次直到 `LIMIT_REACHED`，而 `write_incident_report` **确实在 allowlist 内**。
> 这与 `F1`（工具没暴露）是**同一个观测信号、不同原因**；把两者混为一类会让修复动作指错
> （去调预算，实际该拆阶段）。⇒ 按"编号只增不复用"追加 `F9`，并把 `F1` 的信号收窄为
> "目标工具不在暴露面内"。

为什么必须固定：``devlog 0031`` §4.5 的八轮迭代里，多次出现"改提示 → 仍失败 → 换模型"
的无效循环。目录的作用是把这条路堵住——**修复动作从有限的集合里选**，选不出就说明
判据不够，而不是继续试。

**本模块只描述，不执行**：内核不替大模型改代码，也不生成 prompt；
大模型（总控）读到 ``signal`` 后自己决定改哪个声明式文件，再由 ``pack build`` 重新校验。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = ["REPAIR_CATALOG", "RepairOption", "catalog_as_json"]


@dataclass(frozen=True)
class RepairOption:
    """一条失效模式及其**允许**的结构性修复。"""

    code: str
    failure: str
    signal: str
    action: str
    forbidden: str


REPAIR_CATALOG: Final[tuple[RepairOption, ...]] = (
    RepairOption(
        code="F1",
        failure="未暴露（授权 ≠ 暴露）",
        signal="目标工具**不在**该阶段的暴露面内（被档位预算或 allowlist 过滤），模型反复调用其它工具直到步数耗尽",
        action="调整能力档位预算或 allowlist，使该阶段真正暴露目标工具；或把工具的能力声明改到与阶段匹配",
        forbidden="提高全局权限；把风险统一降成 low",
    ),
    RepairOption(
        code="F2",
        failure="格式不合规",
        signal="参数校验失败；占位符（如 <...>）被原样写进产物",
        action="改成封闭字段 schema；在工具侧加格式闸门（写入时就拒）",
        forbidden="在提示里重复'不要写尖括号'",
    ),
    RepairOption(
        code="F3",
        failure="拒绝后放弃",
        signal="一次 ok=false 之后任务直接 FAILED",
        action="让错误信息可执行（指出字段与可选值）；给出重试预算",
        forbidden="放宽校验让它通过",
    ),
    RepairOption(
        code="F4",
        failure="阶段串味",
        signal="调用了不属于本阶段暴露面的工具",
        action="按阶段拆包，使会话级暴露面固定",
        forbidden="在提示里写'现在不要用 X'",
    ),
    RepairOption(
        code="F5",
        failure="参数形状不封闭",
        signal="全部被拒为 invalid_arguments，工具从未被真正调用",
        action="把工具参数改成封闭字段表（additionalProperties: false）",
        forbidden="让校验器接受任意键",
    ),
    RepairOption(
        code="F6",
        failure="值幻觉",
        signal="格式正确，但取值不在现场文件里",
        action="在工具内校验'值须逐字出现在证据里'，拒绝时点名字段",
        forbidden="在提示里写'不要编造'",
    ),
    RepairOption(
        code="F7",
        failure="幂等重复",
        signal="同一写调用被重复 N 次且内容相同",
        action="在工具成功消息里引导；记为'重复但幂等'，不判失败",
        forbidden="在 harness 层去重（那是改核心循环）",
    ),
    RepairOption(
        code="F8",
        failure="判据误伤",
        signal="仅排版/空白差异导致失败",
        action="判据归一化；用正反例自证判据",
        forbidden="放宽到'看起来对'",
    ),
    RepairOption(
        code="F9",
        failure="工具选择失败（暴露但不用）",
        signal="目标工具**在** allowlist 内、可被调用，但一次都没被选择；反复调用同一工具直到预算耗尽",
        action="把阶段拆得更小；把多字段工具拆成单字段/更窄形状；在任务里点名工具与参数（可判定的下一步）",
        forbidden="再加一个同义工具；只提高步数预算；泛泛叮嘱'记得写报告'",
    ),
)


def catalog_as_json() -> list[dict[str, str]]:
    """目录的 JSON 形态（给大模型总控读；键名稳定，可直接作为动作白名单）。"""
    return [
        {
            "code": option.code,
            "failure": option.failure,
            "signal": option.signal,
            "action": option.action,
            "forbidden": option.forbidden,
        }
        for option in REPAIR_CATALOG
    ]
