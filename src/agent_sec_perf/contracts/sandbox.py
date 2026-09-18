"""沙箱契约（横切 SEC 层）。

字段级定义见 ``docs/design/interfaces/sandbox.md``（本模块是它的唯一实现）；
实现见 ``security/sandbox/``，执行经 ``foundation/proc.py``。
关键不变式：``enforced=True`` **只能**来自"负向探针确实被阻止"（ADR-0006 规则 S-1），
**禁止**以退出码为 0 推断；``degraded=True`` ⇒ ``reason`` 必填且必须进审计（规则 S-2）。
本模块只依赖标准库（ADR-0015 §7.1 R3）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class IsolationDimension(StrEnum):
    """隔离维度（取自 ADR-0007 §3.1 的探针矩阵，不是新造的）。"""

    FILESYSTEM = "filesystem"
    NETWORK = "network"
    USER = "user"
    CAPABILITIES = "capabilities"
    CPU_TIME = "cpu_time"
    ADDRESS_SPACE = "address_space"
    FILE_SIZE = "file_size"
    PROCESS_COUNT = "process_count"


class IsolationMechanism(StrEnum):
    """提供隔离的机制类别（ADR-0007）。``NONE`` 表示该维度未隔离。

    ``CGROUP`` 保留在枚举里是为了让探针能**如实记录**"尝试过但未生效"，而不是把失败从数据里抹掉。
    """

    NONE = "none"
    SETRLIMIT = "setrlimit"
    NAMESPACE = "namespace"
    CGROUP = "cgroup"


@dataclass(frozen=True)
class IsolationDimensionStatus:
    """单个维度的隔离状态。

    不变式：``enforced=True`` ⇒ 必须存在一条**负向探针**作为依据；``mechanism == NONE``
    ⇒ ``enforced`` 必须为 ``False``；``evidence`` 为空字符串**视为未验证** ⇒ 按
    ``enforced=False`` 处理（fail-secure）。
    """

    dimension: IsolationDimension
    enforced: bool
    mechanism: IsolationMechanism
    evidence: str


class SandboxTier(StrEnum):
    """实际达到的沙箱档位（名与 ADR-0007 §4.2 一一对应）。

    产品**不得依赖** ``L2_CONTAINER``：目标设备（手机 / Termux）无 docker，
    必须能在只有 ``L1`` 时运行（ADR-0007 §4.3）。
    """

    L0 = "l0"
    L1 = "l1"
    L2_NAMESPACE = "l2-namespace"
    L2_CONTAINER = "l2-container"


@dataclass(frozen=True)
class IsolationMatrix:
    """逐维度隔离矩阵。

    不变式：**未列出**的维度一律按 ``enforced=False, mechanism=NONE`` 处理（default-deny）；
    ``tier == L0`` ⇒ 调用方**必须拒绝执行**（不是"降级执行"）；``degraded=True`` ⇒
    ``reason`` 非 ``None`` 且**必须**产出一条 ``AuditEvent(kind=EXECUTION_DEGRADATION)``；
    ``tier in {L1, L2_NAMESPACE, L2_CONTAINER}`` 时 ``dimensions`` 不得为空。
    """

    tier: SandboxTier
    dimensions: Mapping[IsolationDimension, IsolationDimensionStatus]
    degraded: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class SandboxRequest:
    """一次沙箱执行的请求（只描述"要执行什么"，档位由后端按探针结果选择）。

    安全语义：``argv`` **不经 shell**；``env`` 默认空且**禁止继承** ``os.environ``
    （父进程的 ``CNB_TOKEN`` 等绝不能传给执行模型产物的子进程）；``network_allowed``
    默认 ``False``（default-deny）。
    """

    argv: tuple[str, ...]
    cwd: Path
    allowed_roots: tuple[Path, ...]
    timeout_s: float
    env: Mapping[str, str] = field(default_factory=dict)
    network_allowed: bool = False


@dataclass(frozen=True)
class SandboxResult:
    """一次沙箱执行的结果；``isolation`` 是**本次执行实际达到**的隔离矩阵。

    ``returncode`` **不**作为隔离是否生效的判据（firejail 的 fail-open 退出码为 0 却未阻断）。
    """

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool
    duration_s: float
    isolation: IsolationMatrix
