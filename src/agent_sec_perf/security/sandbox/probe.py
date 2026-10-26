"""逐维度隔离能力探针（``ADR-0006`` / ``ADR-0007``；契约 ``docs/design/interfaces/sandbox.md``）。

**这个模块只做一件事：把"到底隔离了什么"变成可复现的证据。**

三条硬规定（缺一条就会把"装了个沙箱工具"误当成"已经隔离"）：

1. ``enforced=True`` **只能**来自**负向探针**——越界操作**被拒绝**。
   "命令退出码是 0"不构成证据：``firejail`` 在本项目环境里就是退出码 0、
   网络仍可达、``CapEff`` 未变的 fail-open 形态（ADR-0007 §3.1）。
2. 没测过的维度**一律记未隔离**（default-deny）：``mechanism=NONE, enforced=False``。
   宁可少报，不可多报——多报会让上层（``refusal`` / CLI / 报告）基于假前提做决定。
3. 降级**不得静默**：未达到 L2 时 ``degraded=True`` 且 ``reason`` 必填；
   需要审计时由调用方调用 :func:`emit_degradation_audit` 落一条
   ``AuditEventKind.EXECUTION_DEGRADATION``。

当前实现的探针（L1，``foundation.proc.run(isolation="user")``）：

===================================  ==========================================
维度                                  负向探针
===================================  ==========================================
``USER``                              子进程回读 ``os.getuid()`` 是否为 65534
``ADDRESS_SPACE``                     2 GiB 上限下分配 3 GiB ⇒ 必须 ``MemoryError``
``FILE_SIZE``                         64 MiB 上限下写 65 MiB ⇒ 必须失败
``FILESYSTEM`` / ``NETWORK`` / ...    无内核级机制 ⇒ 如实记未隔离
===================================  ==========================================

依赖：``contracts`` + ``foundation`` + 标准库（``ADR-0015`` §7.1 R2）。
"""

from __future__ import annotations

import pathlib
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from agent_sec_perf.contracts.audit import AuditEvent, AuditEventKind, AuditOutcome, AuditSink
from agent_sec_perf.contracts.sandbox import (
    IsolationDimension,
    IsolationDimensionStatus,
    IsolationMatrix,
    IsolationMechanism,
    SandboxTier,
)
from agent_sec_perf.foundation import proc
from agent_sec_perf.foundation.errors import BenchError
from agent_sec_perf.foundation.paths import resolve_within

__all__ = [
    "IsolationReport",
    "emit_degradation_audit",
    "probe_isolation",
    "probe_namespace_backend",
]

#: 单条探针的超时（秒）。探针是诊断，不是负载：必须在交互可接受的范围内结束。
_PROBE_TIMEOUT_S: Final = 20.0

#: 地址空间负向探针的分配量：必须**显著大于** ``proc.ADDRESS_SPACE_LIMIT_B``。
_AS_PROBE_BYTES: Final = 3 * 1024**3

#: 文件大小负向探针的写入量：必须**大于** ``proc.FILE_SIZE_LIMIT_B``。
_FSIZE_PROBE_BYTES: Final = proc.FILE_SIZE_LIMIT_B + 1024**2

#: 展示用的证据摘要上限。
_EVIDENCE_LIMIT: Final = 200


@dataclass(frozen=True)
class IsolationReport:
    """一次探测的完整结论（矩阵 + 人可读说明）。"""

    matrix: IsolationMatrix
    notes: tuple[str, ...]

    @property
    def tier(self) -> SandboxTier:
        """本次实际达到的档位。"""
        return self.matrix.tier

    def summary(self) -> str:
        """一行档位摘要（用于 CLI 首屏与报告）。"""
        enforced = sum(1 for item in self.matrix.dimensions.values() if item.enforced)
        total = len(self.matrix.dimensions)
        return f"{self.tier.value}（{enforced}/{total} 个维度有负向证据）"


def probe_isolation() -> IsolationReport:
    """探测当前环境的隔离能力（只读 + 短超时；不写工作区以外的任何位置）。"""
    with tempfile.TemporaryDirectory(prefix="lowspec-probe-") as raw:
        workdir = pathlib.Path(raw)
        dimensions: dict[IsolationDimension, IsolationDimensionStatus] = {}
        notes: list[str] = []

        user = _probe_user(workdir)
        dimensions[IsolationDimension.USER] = user
        dimensions[IsolationDimension.ADDRESS_SPACE] = _probe_address_space(workdir)
        dimensions[IsolationDimension.FILE_SIZE] = _probe_file_size(workdir)
        dimensions[IsolationDimension.CPU_TIME] = _unprobed(
            IsolationDimension.CPU_TIME,
            "setrlimit 已施加但未做负向探针（耗尽 300 s CPU 的代价不属于诊断）；按 fail-secure 记未验证",
        )
        dimensions[IsolationDimension.PROCESS_COUNT] = _unprobed(
            IsolationDimension.PROCESS_COUNT,
            "foundation.proc 当前未设置 RLIMIT_NPROC；记未隔离",
        )
        dimensions[IsolationDimension.CAPABILITIES] = _unprobed(
            IsolationDimension.CAPABILITIES,
            "L1 不降 capability 集（未调用 capset）；记未隔离",
        )
        dimensions[IsolationDimension.FILESYSTEM] = _filesystem_status(workdir)
        dimensions[IsolationDimension.NETWORK] = _network_status()

        namespace_ok, namespace_evidence = probe_namespace_backend()
        notes.append(namespace_evidence)
        if not namespace_ok:
            notes.append("未达到 L2：按设计停在 L1，逐维度如实记录未隔离项（不得表述为“已隔离”）")
        notes.append(
            "L1 的 FILESYSTEM / NETWORK 是**应用级**拦截（路径白名单 + 默认拒绝 + 审计），不是内核隔离"
        )

        tier = SandboxTier.L1 if user.enforced else SandboxTier.L0
        degraded = tier is not SandboxTier.L2_NAMESPACE and tier is not SandboxTier.L2_CONTAINER
        reason = None
        if degraded:
            reason = "；".join(notes)
        matrix = IsolationMatrix(
            tier=tier,
            dimensions=dimensions,
            degraded=degraded,
            reason=reason,
        )
        return IsolationReport(matrix=matrix, notes=tuple(notes))


def probe_namespace_backend() -> tuple[bool, str]:
    """检查 ``bwrap`` 是否真能创建命名空间（**执行**一次最小命令，不只看是否安装）。"""
    binary = _which("bwrap")
    if binary is None:
        return False, "bwrap 未安装：命名空间后端不可用"
    argv = [
        binary,
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--unshare-net",
        "--",
        "/bin/true",
    ]
    try:
        result = proc.run_inherit_env(argv, cwd=pathlib.Path("/"), timeout_s=10.0)
    except BenchError as exc:
        return False, f"bwrap 最小命令无法执行（{type(exc).__name__}）：命名空间后端不可用"
    if result.returncode != 0:
        detail = _first_line(result.output()) or "无输出"
        return False, f"bwrap 创建命名空间失败（退出码 {result.returncode}）：{detail}"
    return True, "bwrap 最小命令成功：命名空间后端可用（仍需业务侧负向探针才能宣称隔离）"


def emit_degradation_audit(sink: AuditSink, *, session_id: str, report: IsolationReport) -> str:
    """把降级事实落进审计（``ADR-0006`` 规则 S-2：禁止静默降级）。

    Returns:
        审计事件的 ``event_id``（便于调用方回放核对）。
    """
    event_id = uuid.uuid4().hex
    sink.emit(
        AuditEvent(
            event_id=event_id,
            kind=AuditEventKind.EXECUTION_DEGRADATION,
            timestamp=datetime.now(tz=UTC).isoformat(),
            session_id=session_id,
            outcome=AuditOutcome.DEGRADED,
            detail={
                "tier": report.matrix.tier.value,
                "degraded": report.matrix.degraded,
                "reason": report.matrix.reason or "未提供原因（按契约不得出现）",
                "enforced_dimensions": sorted(
                    dimension.value
                    for dimension, status in report.matrix.dimensions.items()
                    if status.enforced
                ),
            },
        )
    )
    return event_id


# ----------------------------------------------------------------------
# 负向探针
# ----------------------------------------------------------------------


def _probe_user(workdir: pathlib.Path) -> IsolationDimensionStatus:
    """负向探针：子进程**必须**以非特权 uid 运行。"""
    code = "import os;print(os.getuid());print(os.getgid())"
    try:
        result = proc.run(
            [_python(), "-c", code],
            cwd=workdir,
            timeout_s=_PROBE_TIMEOUT_S,
            isolation="user",
        )
    except BenchError as exc:
        return _status(
            IsolationDimension.USER,
            False,
            IsolationMechanism.NONE,
            f"非特权执行失败（{type(exc).__name__}）：不得回退为 root 执行",
        )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    expected = f"{proc.UNPRIVILEGED_UID}"
    ok = result.returncode == 0 and bool(lines) and lines[0] == expected
    evidence = (
        f"`id` 探针：uid={lines[0] if lines else '?'}（期望 {expected}）"
        if ok
        else f"`id` 探针未得到期望 uid（退出码 {result.returncode}）"
    )
    return _status(
        IsolationDimension.USER,
        ok,
        IsolationMechanism.PRIVDROP if ok else IsolationMechanism.NONE,
        evidence,
    )


def _probe_address_space(workdir: pathlib.Path) -> IsolationDimensionStatus:
    """负向探针：在 2 GiB 上限下分配 3 GiB **必须**失败。"""
    code = (
        "try:\n"
        f"    block = bytearray({_AS_PROBE_BYTES})\n"
        "    print('ALLOCATED', len(block))\n"
        "except MemoryError:\n"
        "    print('MEMORY_ERROR')\n"
    )
    try:
        result = proc.run(
            [_python(), "-c", code],
            cwd=workdir,
            timeout_s=_PROBE_TIMEOUT_S,
            isolation="user",
        )
    except BenchError as exc:
        return _status(
            IsolationDimension.ADDRESS_SPACE,
            False,
            IsolationMechanism.NONE,
            f"探针无法执行（{type(exc).__name__}）",
        )
    blocked = "MEMORY_ERROR" in result.stdout
    return _status(
        IsolationDimension.ADDRESS_SPACE,
        blocked,
        IsolationMechanism.SETRLIMIT if blocked else IsolationMechanism.NONE,
        (
            f"分配 {_AS_PROBE_BYTES // 1024**3} GiB 被拒绝（上限 {proc.ADDRESS_SPACE_LIMIT_B // 1024**3} GiB）"
            if blocked
            else "超限分配未被拒绝：RLIMIT_AS 未生效"
        ),
    )


def _probe_file_size(workdir: pathlib.Path) -> IsolationDimensionStatus:
    """负向探针：在 64 MiB 上限下写 65 MiB **必须**失败。"""
    target = workdir / "fsize-probe.bin"
    code = (
        "import pathlib\n"
        f"path = pathlib.Path({str(target)!r})\n"
        "try:\n"
        f"    with path.open('wb') as handle:\n"
        f"        handle.write(b'x' * {_FSIZE_PROBE_BYTES})\n"
        "    print('WROTE', path.stat().st_size)\n"
        "except (OSError, MemoryError):\n"
        "    print('WRITE_REJECTED')\n"
    )
    try:
        result = proc.run(
            [_python(), "-c", code],
            cwd=workdir,
            timeout_s=_PROBE_TIMEOUT_S,
            isolation="user",
        )
    except BenchError as exc:
        return _status(
            IsolationDimension.FILE_SIZE,
            False,
            IsolationMechanism.NONE,
            f"探针无法执行（{type(exc).__name__}）",
        )
    blocked = "WRITE_REJECTED" in result.stdout or result.returncode != 0
    return _status(
        IsolationDimension.FILE_SIZE,
        blocked,
        IsolationMechanism.SETRLIMIT if blocked else IsolationMechanism.NONE,
        (
            f"写入 {_FSIZE_PROBE_BYTES // 1024**2} MiB 被拒绝（上限 {proc.FILE_SIZE_LIMIT_B // 1024**2} MiB）"
            if blocked
            else "超限写入未被拒绝：RLIMIT_FSIZE 未生效"
        ),
    )


# ----------------------------------------------------------------------
# 应用级拦截（**不是**内核隔离，一律 enforced=False）
# ----------------------------------------------------------------------


def _filesystem_status(workdir: pathlib.Path) -> IsolationDimensionStatus:
    """文件系统：L1 只有路径白名单（应用级），**没有**挂载级只读。"""
    try:
        resolve_within(pathlib.Path("/etc/shadow"), (workdir,), what="探针目标")
    except BenchError:
        blocked = True
    else:
        blocked = False
    evidence = (
        "路径白名单可用（应用级：`/etc/shadow` 不在允许根内即拒绝），但无挂载级只读 ⇒ 记未隔离"
        if blocked
        else "路径白名单未拦住越界路径（异常）"
    )
    return _status(IsolationDimension.FILESYSTEM, False, IsolationMechanism.NONE, evidence)


def _network_status() -> IsolationDimensionStatus:
    """网络：L1 无内核级出站拦截（``T-10`` 仍未缓解）。"""
    return _status(
        IsolationDimension.NETWORK,
        False,
        IsolationMechanism.NONE,
        "L1 无 netns / 防火墙机制；出站由策略层默认拒绝 + 审计承载（T-10 未缓解）",
    )


# ----------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------


def _status(
    dimension: IsolationDimension,
    enforced: bool,
    mechanism: IsolationMechanism,
    evidence: str,
) -> IsolationDimensionStatus:
    """构造单维度状态；``mechanism=NONE`` 时强制 ``enforced=False``（契约不变式）。"""
    if mechanism is IsolationMechanism.NONE and enforced:
        raise ValueError("mechanism=NONE 时不得声明 enforced=True（契约 §2.2）")
    return IsolationDimensionStatus(
        dimension=dimension,
        enforced=enforced,
        mechanism=mechanism,
        evidence=evidence[:_EVIDENCE_LIMIT],
    )


def _unprobed(dimension: IsolationDimension, evidence: str) -> IsolationDimensionStatus:
    """未做负向探针的维度：一律记未隔离（fail-secure）。"""
    return _status(dimension, False, IsolationMechanism.NONE, evidence)


def _python() -> str:
    """探针用的解释器绝对路径（避免 PATH 污染）。"""
    return proc.resolve_binary("python3")


def _which(name: str) -> str | None:
    """查找可执行文件；找不到返回 ``None``（不抛异常：探测场景里"没有"是正常结果）。"""
    try:
        return proc.resolve_binary(name)
    except BenchError:
        return None


def _first_line(text: str) -> str:
    """取第一行非空输出并截断（错误回显面）。"""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:_EVIDENCE_LIMIT]
    return ""
