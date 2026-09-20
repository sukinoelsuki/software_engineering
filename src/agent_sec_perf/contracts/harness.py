"""编排层契约（L3 边界与会话事件流）。

字段级定义见 ``docs/design/interfaces/harness.md``（本模块是它的唯一实现）；
边界处签名与语义的上游决策见 ADR-0015 §5.1.2。
本模块**只依赖标准库与 ``contracts`` 内部模块**（ADR-0015 §7.1 的 R3）。

本模块覆盖 ``harness.md`` §2 的**类型层**：``SessionEvent``（7 个 kind / 14 个字段 /
``I1``~``I10`` 的形状）· ``TaskStatus`` · ``SessionErrorKind`` · 审批门四类型 ·
``SessionConfig`` · ``Session`` / ``ApprovalGate`` / ``ArgumentValidator`` 三个 Protocol。

**零行为**（``docs/design/interfaces/README.md`` §2 的 C1）：

* ``Protocol`` 的方法体固定为 ``...``；
* **不写** ``__post_init__``——``SessionConfig`` 的装配期校验（``harness.md`` §2.8 的三条）
  是 ``harness/session.py`` 的**生产者义务**，写进契约会把"校验失败即拒绝启动"变成
  "契约替实现做了一半"；
* ``SessionEvent`` 的不变式 ``I1``~``I10`` 由**生产者**（``harness/loop.py``）保证，
  类型层面表达不了 ⇒ 断言归实现与测试，不归本层。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from agent_sec_perf.contracts.model import CapabilityTier, ModelResponse
from agent_sec_perf.contracts.policy import PolicyDecision, RiskLevel
from agent_sec_perf.contracts.tools import ToolResult, ToolSpec

__all__ = [
    "ApprovalGate",
    "ApprovalOutcome",
    "ApprovalRequest",
    "ApprovalResult",
    "ArgumentValidator",
    "Session",
    "SessionConfig",
    "SessionErrorKind",
    "SessionEvent",
    "SessionEventKind",
    "TaskStatus",
]


class SessionEventKind(StrEnum):
    """会话事件的类别（``harness.md`` §2.1）。

    **每个成员都有明确的产出时机、生产者与消费方**——没有"为了完整而列"的成员
    （被否决的 ``TASK_STARTED`` / ``PROMPT_SENT`` / ``TOOL_DENIED`` 等理由见契约 §2.1）。
    """

    MODEL_RESPONSE = "model_response"
    TOOL_CALL = "tool_call"
    POLICY_DECISION = "policy_decision"
    APPROVAL_RESULT = "approval_result"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    TASK_FINISHED = "task_finished"


class TaskStatus(StrEnum):
    """一次 ``run`` 的终态（``harness.md`` §2.3）。

    ``LIMIT_REACHED`` 与 ``FAILED`` **刻意分开**：前者是"成功但用尽了步数预算"
    （配置问题），后者是"遇到不可恢复错误"（故障）；合并会让调参时读到错误的信号。
    """

    COMPLETED = "completed"
    FAILED = "failed"
    LIMIT_REACHED = "limit_reached"


class SessionErrorKind(StrEnum):
    """``ERROR`` 事件的错误分级（``harness.md`` §2.4）。

    **工具级失败不在此列**：``ToolResult.ok=False`` 是正常数据通路（回喂给模型），
    只有连续失败达上限时才升格为 ``STALLED``。
    """

    TRANSIENT = "transient"
    UNREACHABLE = "unreachable"
    PROTOCOL = "protocol"
    STALLED = "stalled"
    INTERNAL = "internal"


class ApprovalOutcome(StrEnum):
    """人工确认的三种结果（``harness.md`` §2.5.1，覆盖 ``REQ-UX-02``）。"""

    ALLOW_ONCE = "allow_once"
    ALLOW_ALWAYS = "allow_always"
    DENY = "deny"


@dataclass(frozen=True)
class SessionEvent:
    """会话事件流的一条事件（``harness.md`` §2.2）。

    采用"**宽记录 + 按 kind 的不变式**"（与 ``AuditEvent`` 同构）：序列化只有一条路径，
    代价是"哪些字段在哪个 kind 下必填"必须由不变式 ``I1``~``I10`` 钉住——
    那由生产者（``harness/loop.py``）与测试负责，本零行为层不承载。

    **不可信内容只允许出现在四处**（``I8``）：``response.content`` / ``result.content`` /
    ``result.error`` / ``tool_name``；原始 ``arguments_json`` **不得**进事件、
    也不得进 ``text``。
    """

    kind: SessionEventKind
    session_id: str
    seq: int
    timestamp: str
    call_id: str | None = None
    tool_name: str | None = None
    response: ModelResponse | None = None
    decision: PolicyDecision | None = None
    approval: ApprovalResult | None = None
    result: ToolResult | None = None
    text: str | None = None
    error_kind: SessionErrorKind | None = None
    status: TaskStatus | None = None
    audit_id: str | None = None


@dataclass(frozen=True)
class ApprovalRequest:
    """提交给确认通路的一次请求（``harness.md`` §2.5.1）。

    ``tool_name`` 原样取自模型请求 ⇒ **不可信数据**（展示前必须净化）；
    ``arguments_summary`` 由 ``harness`` 生成、**只供展示**——任何控制流、权限判定、
    工具选择都**不得**读它（``harness.md`` §2.5.3 的 ``S4``）。
    """

    session_id: str
    call_id: str
    tool_name: str
    risk_level: RiskLevel
    reason: str
    arguments_summary: str | None = None


@dataclass(frozen=True)
class ApprovalResult:
    """确认通路的结果；``audit_id`` 是"这次确认"与审计的关联键（``REQ-SEC-06``）。"""

    outcome: ApprovalOutcome
    audit_id: str


@dataclass(frozen=True)
class SessionConfig:
    """会话装配配置（``harness.md`` §2.8）。

    ``working_dir`` / ``allowed_roots`` **无默认值**：给它们默认值会让"忘了传"退化为
    "用了某个我方可写目录"，而白名单根**不得**来自猜测（与 ``audit.md`` §2.5 同一取向）。

    ``capability_tier`` 默认 **``BASIC``（最弱档）**：工具最少、提示最结构化 ⇒ 方向收窄，
    符合 fail-secure；能力探测（``REQ-MODEL-06``）未开工，故取最保守的一侧。
    """

    working_dir: Path
    allowed_roots: tuple[Path, ...]
    capability_tier: CapabilityTier = CapabilityTier.BASIC
    max_steps: int = 12
    max_consecutive_failures: int = 3
    tool_timeout_s: float = 30.0
    max_prompt_tokens: int = 8192
    max_completion_tokens: int | None = None


class ArgumentValidator(Protocol):
    """信任边界上的参数校验器（``harness.md`` §3.4）。

    输入 ``arguments_json`` 是**不可信文本**，按 ``spec.parameters_schema`` **严格**校验
    （类型 / ``required`` / 未知键拒绝）；失败抛
    ``foundation.errors.ToolArgumentsInvalidError``，**错误信息不得回显原始内容**。
    **实现选型未定**（pydantic 严格模式 vs 手写），属实现侧需停下上报的事项。
    """

    def validate(self, *, spec: ToolSpec, arguments_json: str) -> Mapping[str, object]: ...


class ApprovalGate(Protocol):
    """人工确认通路（``harness.md`` §2.5）。

    **阻塞式同步方法**，返回值即用户选择；实现归 ``cli/approval.py``（依赖倒置：
    ``R1`` 禁止 ``harness`` 依赖 ``cli``），因而单测可注入 fake。
    实现**必须**先 ``emit(AuditEvent(kind=APPROVAL))`` 再返回，``audit_id`` 即该事件 id。
    """

    def request(self, request: ApprovalRequest) -> ApprovalResult: ...


class Session(Protocol):
    """会话（``harness.md`` §2.9；具体类见 ``harness/session.py``）。

    执行模型：**同步** ``Iterator``、**单会话单线程**、事件流串行产出（不引入 ``asyncio``，
    理由见契约 §2.9 的裁决）。

    错误面：``run()`` 内的业务失败一律经 ``ERROR`` / ``TASK_FINISHED`` 表达、**不抛异常**；
    逃逸的只有 ``ConfigError``（装配期）、**审计写入失败**（``emit`` / ``flush`` 必须冒泡）、
    以及编程缺陷。

    具体类还需实现 ``__enter__`` / ``__exit__``（退出按序 teardown：工具 → 模型客户端 →
    ``llama-server`` 进程 → ``flush`` 审计）。
    """

    def run(self, task: str) -> Iterator[SessionEvent]: ...

    def close(self) -> None: ...
