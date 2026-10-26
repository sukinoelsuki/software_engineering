"""环境体检（``agent-sec-perf doctor``）。

设计目标：**在跑模型之前**把"会以看似模型不行的方式失败"的前提逐条查出来。

三类检查：

* ``fail`` —— 缺了它产品根本跑不起来（解释器版本、推理后端、模型文件、案例资产）；
* ``warn`` —— 能跑，但演示/安全口径会被削弱（隔离档位停在 L1、审计目录不可写）；
* ``ok``   —— 已核验。

为什么把隔离矩阵放进体检：``enforced=True`` 只能来自负向探针（``sandbox.md`` §2.2），
而"镜像里装了 bwrap"与"bwrap 能创建命名空间"是两件事。本命令**执行**一条最小命令
来区分二者，并如实报告 L0/L1/L2——不把"未隔离"写成"已隔离"。
"""

from __future__ import annotations

import json
import pathlib
import sys
from dataclasses import asdict, dataclass
from typing import Final

from rich.console import Console
from rich.table import Table
from rich.text import Text

from agent_sec_perf import __version__
from agent_sec_perf.foundation.config import AppConfig, default_audit_directory, load_config
from agent_sec_perf.foundation.errors import BenchError
from agent_sec_perf.foundation.logging import sanitize_for_display
from agent_sec_perf.observability.audit import JsonlAuditSink
from agent_sec_perf.security.sandbox import IsolationReport, probe_isolation

__all__ = ["CheckResult", "collect_checks", "doctor_main", "repo_root"]

_OK: Final = "ok"
_WARN: Final = "warn"
_FAIL: Final = "fail"

#: 体检默认查找模型的位置（与镜像预置路径一致；可用 ``--model-dir`` 覆盖）。
DEFAULT_MODEL_DIR: Final = pathlib.Path("/opt/models")

#: 案例目录（相对于仓库根）。
SHOWCASE_RELATIVE: Final = pathlib.Path("examples/showcase/incident-01")

_LABEL_LIMIT: Final = 120


@dataclass(frozen=True)
class CheckResult:
    """一条体检结论。"""

    name: str
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        """是否属于"跑不起来"的硬失败。"""
        return self.status == _FAIL


def repo_root() -> pathlib.Path:
    """仓库根目录（``src/agent_sec_perf/cli/doctor.py`` 向上三级）。

    产品**不**依赖 cwd：``doctor`` 在任意目录下执行都应能找到仓库内的案例资产。
    """
    return pathlib.Path(__file__).resolve().parents[3]


def collect_checks(*, model_dir: pathlib.Path, probe: bool = True) -> list[CheckResult]:
    """收集全部体检结论（纯读 + 一次隔离探针；不写审计）。"""
    checks: list[CheckResult] = [
        _check_python(),
        _check_package(),
        _check_config(),
        _check_audit_directory(),
        _check_llama_server(),
        _check_models(model_dir),
        _check_showcase(),
    ]
    if probe:
        checks.append(_check_isolation(probe_isolation()))
    return checks


def doctor_main(
    *,
    model_dir: pathlib.Path = DEFAULT_MODEL_DIR,
    as_json: bool = False,
    console: Console | None = None,
) -> int:
    """执行体检并输出；返回进程退出码（有 ``fail`` ⇒ 1）。"""
    output = console if console is not None else Console()
    # 体检只跑一次探针：先探测，再把同一个结论交给渲染与 JSON。
    report = probe_isolation()
    checks = collect_checks(model_dir=model_dir, probe=False)
    checks.append(_check_isolation(report))

    if as_json:
        payload = {
            "version": __version__,
            "python": sys.version.split()[0],
            "checks": [asdict(item) for item in checks],
            "isolation": {
                "tier": report.tier.value,
                "degraded": report.matrix.degraded,
                "reason": report.matrix.reason,
                "dimensions": {
                    dimension.value: {
                        "enforced": status.enforced,
                        "mechanism": status.mechanism.value,
                        "evidence": status.evidence,
                    }
                    for dimension, status in report.matrix.dimensions.items()
                },
            },
        }
        output.print_json(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _render(checks, report, console=output)

    return 1 if any(item.failed for item in checks) else 0


# ----------------------------------------------------------------------
# 检查项
# ----------------------------------------------------------------------


def _check_python() -> CheckResult:
    current = sys.version_info
    ok = current >= (3, 12)
    return CheckResult(
        name="Python 版本",
        status=_OK if ok else _FAIL,
        detail=f"{current.major}.{current.minor}.{current.micro}（要求 ≥ 3.12）",
    )


def _check_package() -> CheckResult:
    """产品版本取包内 ``__version__``（``src/`` 只允许静态导入）。"""
    return CheckResult(name="产品版本", status=_OK, detail=__version__)


def _check_config() -> CheckResult:
    try:
        config = load_config()
    except BenchError as exc:
        return CheckResult(
            name="配置加载",
            status=_FAIL,
            detail=f"{type(exc).__name__}（修好 .lowspec.toml 或用户级 config.toml）",
        )
    return CheckResult(name="配置加载", status=_OK, detail=_config_summary(config))


def _config_summary(config: AppConfig) -> str:
    granted = ", ".join(config.policy.granted_capabilities) or "（空：默认拒绝一切工具）"
    return f"已授予能力：{granted}"


def _check_audit_directory() -> CheckResult:
    directory = default_audit_directory()
    try:
        sink = JsonlAuditSink(directory)
    except (BenchError, OSError) as exc:
        return CheckResult(
            name="审计落点",
            status=_FAIL,
            detail=f"{type(exc).__name__}：{sanitize_for_display(str(directory), limit=_LABEL_LIMIT)}",
        )
    return CheckResult(
        name="审计落点",
        status=_OK,
        detail=sanitize_for_display(str(sink.path), limit=_LABEL_LIMIT),
    )


def _check_llama_server() -> CheckResult:
    from agent_sec_perf.foundation import proc

    try:
        binary = proc.resolve_binary("llama-server")
    except BenchError:
        fallback = pathlib.Path("/opt/llama.cpp/build/bin/llama-server")
        if fallback.is_file():
            return CheckResult(
                name="推理后端",
                status=_WARN,
                detail=f"PATH 中没有 llama-server；可用绝对路径 {fallback}（用 --model-binary 指定）",
            )
        return CheckResult(
            name="推理后端",
            status=_FAIL,
            detail="找不到 llama-server（安装 llama.cpp 或把 bin 目录加入 PATH）",
        )
    return CheckResult(name="推理后端", status=_OK, detail=binary)


def _check_models(model_dir: pathlib.Path) -> CheckResult:
    try:
        models = sorted(model_dir.glob("*.gguf"))
    except OSError as exc:
        return CheckResult(
            name="端侧模型", status=_FAIL, detail=f"目录不可读（{type(exc).__name__}）"
        )
    if not models:
        return CheckResult(
            name="端侧模型",
            status=_FAIL,
            detail=f"{model_dir} 下没有 .gguf（用 --model-dir 指定模型目录）",
        )
    names = "、".join(item.name for item in models[:3])
    more = f" 等 {len(models)} 个" if len(models) > 3 else ""
    return CheckResult(name="端侧模型", status=_OK, detail=f"{names}{more}")


def _check_showcase() -> CheckResult:
    case = repo_root() / SHOWCASE_RELATIVE / "case.toml"
    if not case.is_file():
        return CheckResult(
            name="演示案例",
            status=_FAIL,
            detail=f"缺少 {SHOWCASE_RELATIVE}/case.toml（仓库不完整？）",
        )
    return CheckResult(name="演示案例", status=_OK, detail=str(SHOWCASE_RELATIVE))


def _check_isolation(report: IsolationReport) -> CheckResult:
    """隔离能力：L0 是硬失败（不得执行），L1 是告警（产品按设计可在 L1 运行）。"""
    if report.tier.value == "l0":
        return CheckResult(
            name="隔离能力",
            status=_FAIL,
            detail="L0：非特权执行不可用 ⇒ 调用方必须拒绝执行（不回退为 root）",
        )
    status = _OK if report.tier.value.startswith("l2") else _WARN
    enforced = sorted(
        dimension.value for dimension, item in report.matrix.dimensions.items() if item.enforced
    )
    shown = "、".join(enforced) if enforced else "无"
    return CheckResult(
        name="隔离能力",
        status=status,
        detail=f"{report.summary()}；有负向证据的维度：{shown}",
    )


# ----------------------------------------------------------------------
# 渲染
# ----------------------------------------------------------------------


def _render(checks: list[CheckResult], report: IsolationReport, *, console: Console) -> None:
    table = Table(title="环境体检", header_style="bold cyan")
    table.add_column("项", style="bold", no_wrap=True)
    table.add_column("结果", no_wrap=True)
    table.add_column("说明")
    for item in checks:
        mark, style = _mark(item.status)
        table.add_row(Text(item.name), Text(mark, style=style), Text(item.detail))
    console.print(table)

    if report.matrix.degraded:
        console.print(
            Text(
                "隔离说明：当前只达到 "
                f"{report.tier.value}。未达到 L2 的原因与每个维度的证据见上方“隔离能力”行；"
                "产品不会把未隔离写成已隔离（sandbox.md §2.2）。",
                style="yellow",
            )
        )


def _mark(status: str) -> tuple[str, str]:
    if status == _OK:
        return "✓ ok", "green"
    if status == _WARN:
        return "! warn", "yellow"
    return "✗ fail", "red"
