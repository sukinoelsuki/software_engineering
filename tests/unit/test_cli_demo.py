"""演示案例的加载、工作区准备与机检断言（不调用模型）。"""

from __future__ import annotations

from collections.abc import Mapping

from agent_sec_perf.cli.demo import load_case, prepare_workspace, verify_events
from agent_sec_perf.cli.doctor import repo_root
from agent_sec_perf.contracts.policy import Capability
from agent_sec_perf.harness.domain_pack import load_pack


def _events(
    content: str,
    *,
    calls: int = 2,
    ok: bool = True,
    denied: bool = False,
) -> tuple[Mapping[str, object], ...]:
    """构造一条结构合法的事件流（seq 从 0 连续）。"""
    items: list[Mapping[str, object]] = []
    seq = 0
    for index in range(calls):
        items.append({"kind": "tool_call", "seq": seq, "tool_name": "read_file"})
        seq += 1
        items.append(
            {
                "kind": "tool_result",
                "seq": seq,
                "tool_name": "read_file",
                "audit_id": f"audit-{index}",
                "result": None if denied else {"ok": ok},
            }
        )
        seq += 1
    items.append({"kind": "model_response", "seq": seq, "response": {"content": content}})
    seq += 1
    items.append({"kind": "task_finished", "seq": seq, "status": "completed"})
    return tuple(items)


def test_case_declares_read_only_scope() -> None:
    case = load_case()

    assert case.case_id == "incident-01"
    assert case.min_tool_calls == 2
    assert "8000" in case.required_substrings

    pack = load_pack(
        case.pack_dir,
        roots=(repo_root(),),
        known_tools=frozenset({"read_file", "list_dir", "write_file", "run_command"}),
    )
    assert pack.capabilities_allowlist == frozenset({Capability.READ_FILE})
    assert pack.tool_allowlist <= {"read_file", "list_dir"}, "演示包不得声明写/执行工具"


def test_prepare_workspace_grants_only_read_file(tmp_path) -> None:
    case = load_case()
    workspace = prepare_workspace(case, tmp_path / "workspace")

    assert (workspace / "app.log").is_file()
    assert (workspace / "service.toml").is_file()
    config = (workspace / ".lowspec.toml").read_text(encoding="utf-8")
    assert 'granted_capabilities = ["read_file"]' in config
    assert "write_file" not in config
    assert "execute_command" not in config


def test_verify_passes_on_grounded_stream() -> None:
    case = load_case()
    content = "CODE=E42\nLOG_TIMEOUT_MS=3000\nCONFIG_TIMEOUT_MS=8000\nVERDICT=不一致"

    checks = verify_events(_events(content), case, exit_code=0)

    failed = [item for item in checks if not item.ok]
    assert failed == [], failed


def test_verify_rejects_hallucinated_value() -> None:
    """模型没读到 service.toml 就编一个值 ⇒ 机检必须失败（这正是演示要防的形态）。"""
    case = load_case()
    content = "CODE=E42\nLOG_TIMEOUT_MS=3000\nCONFIG_TIMEOUT_MS=5000\nVERDICT=不一致"

    checks = verify_events(_events(content), case, exit_code=0)

    facts = next(item for item in checks if item.name == "结论事实")
    assert facts.ok is False
    assert "8000" in facts.detail


def test_verify_rejects_denied_tool_and_single_call() -> None:
    case = load_case()
    content = "CODE=E42\nLOG_TIMEOUT_MS=3000\nCONFIG_TIMEOUT_MS=8000\nVERDICT=不一致"

    checks = verify_events(_events(content, calls=1, denied=True), case, exit_code=0)

    tool_calls = next(item for item in checks if item.name == "工具调用")
    executed = next(item for item in checks if item.name == "工具真的执行")
    assert tool_calls.ok is False
    assert executed.ok is False
