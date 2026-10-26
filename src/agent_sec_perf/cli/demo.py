"""演示案例运行器（``agent-sec-perf demo``）。

它把"一次可展示的端侧 agent 回环"拆成四步，**每一步都可单独核对**：

1. **准备隔离工作区**：在临时目录里复制案例现场，只把该目录与案例根加入允许根；
2. **真跑**：真实 ``llama-server`` + 本地 GGUF，模型自己决定调用 ``read_file``；
3. **机检**：对事件流逐条断言（终态、``seq`` 连续、工具执行、结论事实）；
4. **审计回放**：从落盘的 JSONL 里按 ``audit_id`` 还原每一次工具调用。

为什么机检要写进产品而不是只写在演示文档里：**"跑完了"不等于"跑对了"**。
演示最容易出现的失败是"模型说了一段像模像样的话，但没读文件"——只靠人眼看输出
分辨不出来；``required_substrings`` + ``tool_results`` 断言把这件事变成可判定的。

本模块**不**修改 ``RunRequest`` / 退出码表 / 安全默认：它只是 ``execute()`` 的一个调用方。
"""

from __future__ import annotations

import io
import json
import pathlib
import shutil
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, cast

from rich.console import Console
from rich.table import Table
from rich.text import Text

from agent_sec_perf.cli.doctor import DEFAULT_MODEL_DIR, repo_root
from agent_sec_perf.foundation.config import AppConfig, AuditConfig, LoggingConfig, PolicyConfig
from agent_sec_perf.foundation.errors import BenchError
from agent_sec_perf.foundation.logging import sanitize_for_display
from agent_sec_perf.observability.audit import JsonlAuditSink
from agent_sec_perf.security.sandbox import IsolationReport, emit_degradation_audit, probe_isolation

__all__ = [
    "DemoCase",
    "DemoOutcome",
    "VerifyCheck",
    "load_case",
    "prepare_workspace",
    "run_demo",
    "verify_events",
]

#: 案例默认位置（相对仓库根）。
DEFAULT_CASE: Final = pathlib.Path("examples/showcase/incident-01")

#: 演示授予的能力：**只有** ``read_file``（案例是只读的；不因演示而扩权）。
DEMO_GRANTED_CAPABILITIES: Final[tuple[str, ...]] = ("read_file",)

_LABEL_LIMIT: Final = 80
_LIVE_CONTENT_LIMIT: Final = 120


@dataclass(frozen=True)
class DemoCase:
    """从 ``case.toml`` 读到的案例声明（唯一真源）。"""

    case_id: str
    title: str
    task: str
    case_dir: pathlib.Path
    scene_dir: pathlib.Path
    pack_dir: pathlib.Path
    min_tool_calls: int
    required_substrings: tuple[str, ...]
    preferred_model: str
    max_completion_tokens: int
    request_timeout_s: float
    ready_timeout_s: float


@dataclass(frozen=True)
class VerifyCheck:
    """一条机检结论。"""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class DemoOutcome:
    """一次演示的完整结果（供 CLI 渲染与测试断言）。"""

    case: DemoCase
    workspace: pathlib.Path
    exit_code: int
    events: tuple[Mapping[str, object], ...]
    session_id: str | None
    isolation: IsolationReport
    checks: tuple[VerifyCheck, ...]

    @property
    def passed(self) -> bool:
        """全部机检通过 ⇔ 演示成功。"""
        return self.exit_code == 0 and all(item.ok for item in self.checks)


# ----------------------------------------------------------------------
# 案例加载与工作区准备
# ----------------------------------------------------------------------


def load_case(case_dir: pathlib.Path | None = None) -> DemoCase:
    """读取并校验 ``case.toml``。

    Raises:
        FileNotFoundError: 案例目录或 ``case.toml`` 缺失。
        ValueError: 必填字段缺失 / 类型不符（这是**仓库内容**问题，不是用户输入）。
    """
    base = (repo_root() / DEFAULT_CASE) if case_dir is None else case_dir
    case_file = base / "case.toml"
    with case_file.open("rb") as handle:
        document = tomllib.load(handle)

    case = _section(document, "case")
    model = _section(document, "model")
    return DemoCase(
        case_id=_require_str(case, "id"),
        title=_require_str(case, "title"),
        task=_require_str(case, "task").strip(),
        case_dir=base,
        scene_dir=base / _require_str(case, "scene"),
        pack_dir=base / _require_str(case, "pack"),
        min_tool_calls=_require_positive_int(case, "min_tool_calls"),
        required_substrings=_require_str_list(case, "required_substrings"),
        preferred_model=_require_str(model, "preferred"),
        max_completion_tokens=_require_positive_int(model, "max_completion_tokens"),
        request_timeout_s=_require_positive_float(model, "request_timeout_s"),
        ready_timeout_s=_require_positive_float(model, "ready_timeout_s"),
    )


def prepare_workspace(case: DemoCase, workspace: pathlib.Path) -> pathlib.Path:
    """把现场复制进工作区，并写入**只授予 read_file** 的项目级配置。

    返回工作区路径。工作区之外的文件不在 ``allowed_roots`` 内 ⇒ 读不到。
    """
    workspace.mkdir(parents=True, exist_ok=True)
    shutil.copytree(case.scene_dir, workspace, dirs_exist_ok=True)
    granted = ", ".join(f'"{item}"' for item in DEMO_GRANTED_CAPABILITIES)
    (workspace / ".lowspec.toml").write_text(
        "# 演示自动生成：只授予只读能力（能力只能由显式配置授予）\n"
        "[policy]\n"
        f"granted_capabilities = [{granted}]\n",
        encoding="utf-8",
    )
    return workspace


def run_demo(
    *,
    case: DemoCase | None = None,
    model_path: pathlib.Path | None = None,
    model_dir: pathlib.Path = DEFAULT_MODEL_DIR,
    workspace: pathlib.Path | None = None,
    keep: bool = False,
    console: Console | None = None,
) -> DemoOutcome:
    """准备 + 运行 + 机检 + 审计回放；**不修改任何安全默认**。

    Args:
        case: 案例；``None`` ⇒ 仓库默认案例。
        model_path: 显式 GGUF；``None`` ⇒ 在 ``model_dir`` 里按 preferred / 任意 ``*.gguf`` 解析。
        model_dir: 模型目录。
        workspace: 工作区；``None`` ⇒ 新建临时目录（``keep=False`` 时结束后删除）。
        keep: 保留临时工作区并在结果里打印路径。
        console: 输出通道（测试可注入）。

    Raises:
        FileNotFoundError: 案例资产或模型缺失（调用方据此返回装配期退出码 3）。
    """
    output = console if console is not None else Console()
    selected = load_case() if case is None else case
    resolved_model = _resolve_model(model_path, model_dir, selected.preferred_model)
    if resolved_model is None:
        raise FileNotFoundError(f"{model_dir} 下没有可用的 .gguf 模型")

    report = probe_isolation()
    temp_handle: tempfile.TemporaryDirectory[str] | None = None
    if workspace is None:
        if keep:
            raw = pathlib.Path(tempfile.mkdtemp(prefix="lowspec-showcase-"))
            workdir = prepare_workspace(selected, raw / "workspace")
        else:
            temp_handle = tempfile.TemporaryDirectory(prefix="lowspec-showcase-")
            workdir = prepare_workspace(selected, pathlib.Path(temp_handle.name) / "workspace")
    else:
        workdir = prepare_workspace(selected, workspace)

    _render_plan(output, selected, workdir, resolved_model, report)
    try:
        exit_code, events, session_id = _execute_case(selected, workdir, resolved_model, output)
        checks = list(verify_events(events, selected, exit_code=exit_code))
        checks.extend(_audit_checks(events, session_id))
        if session_id is not None and report.matrix.degraded:
            checks.append(_emit_degradation(session_id, report))
        outcome = DemoOutcome(
            case=selected,
            workspace=workdir,
            exit_code=exit_code,
            events=events,
            session_id=session_id,
            isolation=report,
            checks=tuple(checks),
        )
        _render_report(outcome, output)
        return outcome
    finally:
        if temp_handle is not None:
            temp_handle.cleanup()


# ----------------------------------------------------------------------
# 执行（与 `run` 共用装配与退出码表）
# ----------------------------------------------------------------------


def _execute_case(
    case: DemoCase,
    workdir: pathlib.Path,
    model_path: pathlib.Path,
    console: Console,
) -> tuple[int, tuple[Mapping[str, object], ...], str | None]:
    """调用 ``execute()`` 并把 stdout 的 JSONL 解析成事件载荷。"""
    # 延迟导入：app.py 需要注册本模块的命令，顶层互相 import 会成环。
    from agent_sec_perf.cli.app import OUTPUT_JSON, RunRequest, execute

    stream = _LiveStream(console=console)
    stderr = io.StringIO()
    request = RunRequest(
        task=case.task,
        working_dir=workdir,
        # 允许根只有"工作区"与"案例根"：模型读不到演示之外的文件。
        allowed_roots=(workdir, case.case_dir.parent),
        output_format=OUTPUT_JSON,
        pack_directory=case.pack_dir,
        max_steps=8,
        max_consecutive_failures=3,
        tool_timeout_s=30.0,
        max_prompt_tokens=8192,
        max_completion_tokens=case.max_completion_tokens,
        model_path=model_path,
        model_log=workdir / "llama-server.log",
        model_request_timeout_s=case.request_timeout_s,
        model_ready_timeout_s=case.ready_timeout_s,
    )
    # 演示的授权集合**写死在这里**：不读用户级 config，避免"演示时比标准配置更大权限"。
    config = AppConfig(
        logging=LoggingConfig(),
        policy=PolicyConfig(granted_capabilities=DEMO_GRANTED_CAPABILITIES),
        audit=AuditConfig(),
    )
    exit_code = execute(request, stdout=stream, stderr=stderr, config=config)
    events = _parse_events(stream.getvalue())
    session_id = _session_id(events)
    return exit_code, events, session_id


class _LiveStream(io.StringIO):
    """既收集 JSONL，又把每行渲染成一行进度（不写产品 stdout 以外的内容）。"""

    def __init__(self, *, console: Console) -> None:
        super().__init__()
        self._console = console

    def write(self, text: str) -> int:
        written = super().write(text)
        for line in text.splitlines():
            if line.strip():
                self._console.print(Text(_live_line(line), style="dim"))
        return written


def _live_line(raw: str) -> str:
    """把一条事件 JSON 变成一行可读进度；解析失败只报"事件"不回显原文。"""
    try:
        payload = cast("Mapping[str, object]", json.loads(raw))
    except json.JSONDecodeError:
        return "· 事件（无法解析）"
    kind = payload.get("kind")
    tool = payload.get("tool_name")
    shown_tool = (
        sanitize_for_display(str(tool), limit=_LABEL_LIMIT) if isinstance(tool, str) else None
    )
    if kind == "tool_call" and shown_tool:
        return f"→ 调用 {shown_tool}"
    if kind == "tool_result" and shown_tool:
        result = payload.get("result")
        ok = isinstance(result, Mapping) and result.get("ok") is True
        return f"✓ 结果 {shown_tool}" if ok else f"✗ 结果 {shown_tool}"
    if kind == "policy_decision" and shown_tool:
        return f"· 策略 {shown_tool}"
    if kind == "model_response":
        content = _response_content(payload)
        if content:
            return f"· 模型：{sanitize_for_display(content, limit=_LIVE_CONTENT_LIMIT)}"
        return "· 模型：请求工具"
    if kind == "task_finished":
        return f"· 终态：{payload.get('status')}"
    return f"· {kind}"


# ----------------------------------------------------------------------
# 机检
# ----------------------------------------------------------------------


def verify_events(
    events: Sequence[Mapping[str, object]],
    case: DemoCase,
    *,
    exit_code: int,
) -> tuple[VerifyCheck, ...]:
    """对事件流逐条断言（纯函数，便于单测；不读盘）。"""
    checks: list[VerifyCheck] = []

    checks.append(VerifyCheck("退出码", exit_code == 0, f"execute() 返回 {exit_code}（期望 0）"))

    finished = [item for item in events if item.get("kind") == "task_finished"]
    status = finished[-1].get("status") if finished else None
    checks.append(
        VerifyCheck(
            "终态",
            len(finished) == 1
            and status == "completed"
            and events[-1].get("kind") == "task_finished",
            f"TASK_FINISHED 条数={len(finished)}，status={status}",
        )
    )

    seqs = [item.get("seq") for item in events]
    continuous = seqs == list(range(len(seqs)))
    checks.append(
        VerifyCheck("seq 连续", continuous, f"{len(seqs)} 条事件，区间 {_seq_span(seqs)}")
    )

    tool_calls = [item for item in events if item.get("kind") == "tool_call"]
    read_calls = [item for item in tool_calls if item.get("tool_name") == "read_file"]
    checks.append(
        VerifyCheck(
            "工具调用",
            len(tool_calls) >= case.min_tool_calls and bool(read_calls),
            f"共 {len(tool_calls)} 次（read_file {len(read_calls)} 次，要求 ≥ {case.min_tool_calls}）",
        )
    )

    results = [item for item in events if item.get("kind") == "tool_result"]
    executed_ok = [item for item in results if _result_ok(item)]
    denied = [item for item in results if item.get("result") is None]
    checks.append(
        VerifyCheck(
            "工具真的执行",
            len(executed_ok) >= case.min_tool_calls and not denied,
            f"成功执行 {len(executed_ok)} 次，未执行/被拒 {len(denied)} 次",
        )
    )

    final = _final_content(events)
    missing = [item for item in case.required_substrings if item.upper() not in final.upper()]
    checks.append(
        VerifyCheck(
            "结论事实",
            not missing,
            (
                "全部命中：" + "、".join(case.required_substrings)
                if not missing
                else "缺少：" + "、".join(missing)
            ),
        )
    )
    return tuple(checks)


def _result_ok(item: Mapping[str, object]) -> bool:
    """该 ``tool_result`` 是否表示"真的执行且成功"（``result`` 为 ``None`` 是未执行）。"""
    result = item.get("result")
    if not isinstance(result, Mapping):
        return False
    return cast("Mapping[str, object]", result).get("ok") is True


def _audit_checks(
    events: Sequence[Mapping[str, object]], session_id: str | None
) -> tuple[VerifyCheck, ...]:
    """审计回放：每条 ``tool_result`` 的 ``audit_id`` 必须能从落盘文件还原。"""
    if session_id is None:
        return (VerifyCheck("审计回放", False, "事件流缺少 session_id"),)
    try:
        sink = JsonlAuditSink(_audit_directory())
        stored = [event for event in sink.read_all() if event.session_id == session_id]
    except (BenchError, OSError, ValueError) as exc:
        return (VerifyCheck("审计回放", False, f"读取审计失败（{type(exc).__name__}）"),)

    audit_ids = [item.get("audit_id") for item in events if item.get("kind") == "tool_result"]
    usable = [item for item in audit_ids if isinstance(item, str) and item]
    replayable = [item for item in usable if sink.query_by_id(item) is not None]
    return (
        VerifyCheck(
            "审计回放",
            bool(usable) and len(replayable) == len(usable) and bool(stored),
            f"本会话审计 {len(stored)} 行；{len(replayable)}/{len(usable)} 个 audit_id 可回放",
        ),
    )


def _emit_degradation(session_id: str, report: IsolationReport) -> VerifyCheck:
    """把隔离降级写进审计（规则 S-2；失败不改变任务结论，但必须显示为未通过）。"""
    try:
        sink = JsonlAuditSink(_audit_directory())
        emit_degradation_audit(sink, session_id=session_id, report=report)
        sink.flush()
    except (BenchError, OSError) as exc:
        return VerifyCheck("降级留痕", False, f"写入失败（{type(exc).__name__}）")
    return VerifyCheck("降级留痕", True, f"已记录 tier={report.tier.value}")


def _audit_directory() -> pathlib.Path:
    from agent_sec_perf.foundation.config import default_audit_directory

    return default_audit_directory()


# ----------------------------------------------------------------------
# 渲染
# ----------------------------------------------------------------------


def _render_plan(
    console: Console,
    case: DemoCase,
    workdir: pathlib.Path,
    model_path: pathlib.Path,
    report: IsolationReport,
) -> None:
    console.print(Text(f"演示案例：{case.title}（{case.case_id}）", style="bold cyan"))
    rows = Table(show_header=False, box=None)
    rows.add_column("键", style="bold", no_wrap=True)
    rows.add_column("值")
    rows.add_row(Text("工作区"), Text(str(workdir)))
    rows.add_row(Text("允许根"), Text("工作区 + 案例根（其余路径不可读）"))
    rows.add_row(Text("领域包"), Text(str(case.pack_dir)))
    rows.add_row(Text("模型"), Text(str(model_path)))
    rows.add_row(Text("隔离"), Text(f"{report.tier.value}（未达到 L2 的原因会写入审计）"))
    console.print(rows)


def _render_report(outcome: DemoOutcome, console: Console) -> None:
    table = Table(title="机检结果", header_style="bold cyan")
    table.add_column("检查", style="bold", no_wrap=True)
    table.add_column("结果", no_wrap=True)
    table.add_column("证据")
    for item in outcome.checks:
        table.add_row(
            Text(item.name),
            Text("✓ pass" if item.ok else "✗ fail", style="green" if item.ok else "red"),
            Text(item.detail),
        )
    console.print(table)

    if outcome.passed:
        console.print(
            Text(
                "演示成功：模型真实读取了现场文件，结论可核对，审计可回放。",
                style="bold green",
            )
        )
    else:
        console.print(
            Text(
                "演示未通过：先看上面第一条 fail 的证据，再查 llama-server.log。", style="bold red"
            )
        )


def _config_hint() -> str:
    """演示配置的说明（供文档与测试引用；不在运行时回显）。"""
    return "演示只授予 read_file；不读用户级配置，避免演示权限大于标准配置"


# ----------------------------------------------------------------------
# 解析辅助
# ----------------------------------------------------------------------


def _parse_events(raw: str) -> tuple[Mapping[str, object], ...]:
    """把 JSONL 解析成事件列表；坏行**不静默跳过**（跳过会让 seq 检查失真）。"""
    items: list[Mapping[str, object]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError("事件行不是 JSON 对象")
        items.append(cast("Mapping[str, object]", payload))
    return tuple(items)


def _session_id(events: Sequence[Mapping[str, object]]) -> str | None:
    for item in events:
        value = item.get("session_id")
        if isinstance(value, str) and value:
            return value
    return None


def _response_content(payload: Mapping[str, object]) -> str | None:
    response = payload.get("response")
    if not isinstance(response, Mapping):
        return None
    content = response.get("content")
    return content if isinstance(content, str) else None


def _final_content(events: Sequence[Mapping[str, object]]) -> str:
    """最后一条带正文的 ``model_response``（终态事件不带回答，见手册 §2.3 的注）。"""
    for item in reversed(events):
        if item.get("kind") != "model_response":
            continue
        content = _response_content(item)
        if content:
            return content
    return ""


def _seq_span(seqs: Sequence[object]) -> str:
    if not seqs:
        return "空"
    return f"{seqs[0]}..{seqs[-1]}"


def _resolve_model(
    explicit: pathlib.Path | None, model_dir: pathlib.Path, preferred: str
) -> pathlib.Path | None:
    if explicit is not None:
        return explicit if explicit.is_file() else None
    candidate = model_dir / preferred
    if candidate.is_file():
        return candidate
    try:
        available = sorted(model_dir.glob("*.gguf"))
    except OSError:
        return None
    return available[0] if available else None


def _section(document: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = document.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"case.toml 缺少 [{name}] 段")
    return cast("Mapping[str, object]", value)


def _require_str(section: Mapping[str, object], key: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"case.toml 的 {key} 必须是非空字符串")
    return value


def _require_positive_int(section: Mapping[str, object], key: str) -> int:
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"case.toml 的 {key} 必须是正整数")
    return value


def _require_positive_float(section: Mapping[str, object], key: str) -> float:
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
        raise ValueError(f"case.toml 的 {key} 必须是正数")
    return float(value)


def _require_str_list(section: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = section.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"case.toml 的 {key} 必须是非空字符串数组")
    items: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry:
            raise ValueError(f"case.toml 的 {key} 含非字符串项")
        items.append(entry)
    return tuple(items)
