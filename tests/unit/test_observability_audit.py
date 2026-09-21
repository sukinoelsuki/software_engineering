"""审计落点的行为断言：只追加、失败冒泡、可回放、落点再脱敏（``REQ-SEC-06`` / ``REQ-OBS-01``）。

这是实现侧的功能断言；"每次拒绝都有可回放记录"这类端到端安全断言属 ``tests/security/``，
由验证角色独立完成（安全断言不得由实现者自证）。
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_sec_perf.contracts.audit import AuditEvent, AuditEventKind, AuditOutcome
from agent_sec_perf.contracts.policy import Capability, RiskLevel
from agent_sec_perf.foundation import config
from agent_sec_perf.foundation.errors import AuditWriteError, PathNotAllowedError, SchemaError
from agent_sec_perf.foundation.logging import REDACTED
from agent_sec_perf.observability.audit import JsonlAuditSink

TIMESTAMP = "2026-09-19T08:06:08.123456+00:00"


def _event(
    *,
    event_id: str = "event-1",
    session_id: str = "session-1",
    call_id: str | None = "call-1",
    tool_name: str | None = "read_file",
    kind: AuditEventKind = AuditEventKind.POLICY_DECISION,
    outcome: AuditOutcome = AuditOutcome.ALLOW,
    capability: Capability | None = Capability.READ_FILE,
    risk_level: RiskLevel | None = RiskLevel.LOW,
    detail: Mapping[str, object] | None = None,
    timestamp: str = TIMESTAMP,
) -> AuditEvent:
    return AuditEvent(
        event_id=event_id,
        kind=kind,
        timestamp=timestamp,
        session_id=session_id,
        outcome=outcome,
        call_id=call_id,
        tool_name=tool_name,
        capability=capability,
        risk_level=risk_level,
        detail={} if detail is None else detail,
    )


@pytest.fixture
def sink(tmp_path: pathlib.Path) -> JsonlAuditSink:
    """落点在 ``tmp_path`` 内的 sink（``roots`` 显式覆盖，理由见 ``audit.md`` §2.5 的 P3）。"""
    return JsonlAuditSink(tmp_path / "audit", roots=(tmp_path,))


# ---------------------------------------------------------------------------
# 只追加与回放
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_emit_appends_one_json_line_per_event(sink: JsonlAuditSink) -> None:
    """一条事件一行 JSON，字段与契约一致。"""
    sink.emit(_event(event_id="a"))
    sink.emit(_event(event_id="b", outcome=AuditOutcome.DENY))

    lines = sink.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event_id"] == "a"
    assert first["kind"] == "policy_decision"
    assert first["outcome"] == "allow"
    assert first["capability"] == "read_file"
    assert first["risk_level"] == "low"
    assert first["timestamp"] == TIMESTAMP
    assert json.loads(lines[1])["outcome"] == "deny"


@pytest.mark.unit
def test_events_roundtrip_in_write_order(sink: JsonlAuditSink) -> None:
    """``read_all`` 按写入顺序还原事件，可选字段保持 ``None``。"""
    sink.emit(_event(event_id="a"))
    sink.emit(_event(event_id="b", capability=None, risk_level=None, tool_name=None))

    events = sink.read_all()

    assert [event.event_id for event in events] == ["a", "b"]
    assert events[0].kind is AuditEventKind.POLICY_DECISION
    assert events[0].capability is Capability.READ_FILE
    assert events[1].capability is None
    assert events[1].risk_level is None
    assert events[1].tool_name is None


@pytest.mark.unit
def test_read_all_returns_empty_before_anything_is_written(sink: JsonlAuditSink) -> None:
    """还没写过事件时读出空元组（不因"文件不存在"报错）。"""
    assert sink.read_all() == ()


@pytest.mark.unit
def test_events_survive_a_new_sink_instance(tmp_path: pathlib.Path) -> None:
    """只追加：换一个实例读同一目录仍然能看到既有事件。"""
    first = JsonlAuditSink(tmp_path / "audit", roots=(tmp_path,))
    first.emit(_event(event_id="a"))

    second = JsonlAuditSink(tmp_path / "audit", roots=(tmp_path,))
    second.emit(_event(event_id="b"))

    assert [event.event_id for event in second.read_all()] == ["a", "b"]


@pytest.mark.unit
def test_query_by_id_finds_the_event_and_returns_none_when_absent(sink: JsonlAuditSink) -> None:
    """按 ``audit_id`` 查询：命中返回事件，未命中返回 ``None``。"""
    sink.emit(_event(event_id="a"))
    sink.emit(_event(event_id="b"))

    found = sink.query_by_id("b")
    assert found is not None
    assert found.event_id == "b"
    assert sink.query_by_id("missing") is None


@pytest.mark.unit
def test_blank_lines_are_ignored_but_corrupt_content_is_not(sink: JsonlAuditSink) -> None:
    """空行（如手工追加的换行）可容忍；内容损坏则必须报错。"""
    sink.emit(_event(event_id="a"))
    with sink.path.open("a", encoding="utf-8") as handle:
        handle.write("\n\n")

    assert [event.event_id for event in sink.read_all()] == ["a"]


# ---------------------------------------------------------------------------
# 失败必须冒泡（不得吞、不得降级为告警）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_emit_failure_bubbles_when_the_target_is_not_writable(sink: JsonlAuditSink) -> None:
    """写入失败必须抛异常（静默丢事件 = ``REQ-SEC-06`` 验收失败）。

    ⚠️ **2026-09-22 变更（契约 ``audit.md`` §2.4 + 威胁模型 ``P-3``）**：
    类型由裸 ``OSError`` 改为 :class:`AuditWriteError`（底层 ``OSError`` 保留在 ``__cause__``）。
    裸 ``OSError`` 与"调用方自身的 I/O 异常"**类型不可分**，下游只能用 ``except Exception``
    一把抓 ⇒ 会把"**证据面坏了**"收敛成"**任务失败**"。
    """
    sink.path.mkdir()  # 用同名目录顶替文件：追加写必然失败

    with pytest.raises(AuditWriteError) as excinfo:
        sink.emit(_event())

    # 底层原因必须保留（磁盘满 / 权限 / 只读挂载的具体 OSError），不得丢。
    assert isinstance(excinfo.value.__cause__, OSError)


# 注：``P-3`` 的**行为级**断言（审计写入失败逃逸出 ``run()`` 而**不**被收敛成任务失败，
# 以及"通用 gate 故障仍按 R3 收敛"的对照组）在 ``tests/unit/test_harness_loop.py``：
#   * ``test_audit_write_failure_escapes_run_instead_of_being_a_task_failure``
#   * ``test_generic_gate_failure_still_terminates_as_task_failure``（变异探针）
# 这里只钉住 sink 侧的**类型与原因链**。


@pytest.mark.unit
def test_unserializable_detail_raises_instead_of_being_stringified(sink: JsonlAuditSink) -> None:
    """``detail`` 含不可序列化对象 ⇒ 报错，而不是 ``default=str`` 糊过去（那会固化不可信文本）。"""
    with pytest.raises(TypeError):
        sink.emit(_event(detail={"target": pathlib.Path("/tmp/dummy")}))


@pytest.mark.unit
def test_event_with_naive_timestamp_is_rejected_before_writing(sink: JsonlAuditSink) -> None:
    """naive 时间戳即拒绝，且**不留下半条证据**（校验发生在打开文件之前）。"""
    with pytest.raises(ValueError):
        sink.emit(_event(timestamp="2026-09-19T08:06:08"))

    assert not sink.path.exists()


# ---------------------------------------------------------------------------
# flush 与并发
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_flush_is_idempotent_and_safe_on_an_empty_sink(sink: JsonlAuditSink) -> None:
    """``flush`` 可重复调用（退出路径可能多次触发），无事件时也不报错。"""
    sink.flush()
    sink.flush()

    sink.emit(_event())
    sink.flush()
    sink.flush()

    assert len(sink.read_all()) == 1


@pytest.mark.unit
def test_concurrent_emits_produce_one_intact_line_each(sink: JsonlAuditSink) -> None:
    """实现内部串行化写入（契约）：并发写入不串行、不撕裂，且一条不丢。"""
    total = 40

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: sink.emit(_event(event_id=f"e{index}")), range(total)))

    events = sink.read_all()
    assert len(events) == total
    assert len({event.event_id for event in events}) == total


# ---------------------------------------------------------------------------
# 读取侧校验：坏行不得被静默跳过
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_truncated_line_is_reported_with_its_line_number(sink: JsonlAuditSink) -> None:
    """崩溃时半写的行必须被报出（含行号），不得当作"没有这条"放过。"""
    sink.emit(_event(event_id="a"))
    with sink.path.open("a", encoding="utf-8") as handle:
        handle.write('{"event_id": "b", "kind": "policy_dec')

    with pytest.raises(SchemaError) as excinfo:
        sink.read_all()

    message = str(excinfo.value)
    assert "第 2 行" in message
    assert "policy_dec" not in message


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        '{"kind": "policy_decision"}',
        '{"event_id": "", "kind": "policy_decision"}',
    ],
)
def test_structurally_invalid_lines_are_rejected(sink: JsonlAuditSink, payload: str) -> None:
    """结构不符即拒绝：非对象、缺必需字段、空 ``event_id``。"""
    with sink.path.open("a", encoding="utf-8") as handle:
        handle.write(payload + "\n")

    with pytest.raises(SchemaError):
        sink.read_all()


@pytest.mark.unit
def test_unknown_enum_value_is_rejected_on_read(sink: JsonlAuditSink) -> None:
    """枚举取值非法即拒绝（``REQ-OBS-01`` 要按结果检索，脏值会让查询静默漏项）。"""
    with sink.path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "event_id": "a",
                    "kind": "not_a_kind",
                    "timestamp": TIMESTAMP,
                    "session_id": "s",
                    "outcome": "allow",
                }
            )
            + "\n"
        )

    with pytest.raises(SchemaError):
        sink.read_all()


@pytest.mark.unit
def test_naive_timestamp_is_rejected_on_read(sink: JsonlAuditSink) -> None:
    """读取侧同样拒绝 naive 时间戳（证据里的时间必须可比较）。"""
    with sink.path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "event_id": "a",
                    "kind": "policy_decision",
                    "timestamp": "2026-09-19T08:06:08",
                    "session_id": "s",
                    "outcome": "allow",
                }
            )
            + "\n"
        )

    with pytest.raises(SchemaError):
        sink.read_all()


@pytest.mark.unit
def test_detail_must_be_a_json_object(sink: JsonlAuditSink) -> None:
    """``detail`` 不是对象即拒绝。"""
    with sink.path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "event_id": "a",
                    "kind": "policy_decision",
                    "timestamp": TIMESTAMP,
                    "session_id": "s",
                    "outcome": "allow",
                    "detail": ["not", "an", "object"],
                }
            )
            + "\n"
        )

    with pytest.raises(SchemaError):
        sink.read_all()


# ---------------------------------------------------------------------------
# 落点脱敏与文件名校验
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_detail_is_redacted_again_at_the_sink(sink: JsonlAuditSink) -> None:
    """落点再脱敏一次（纵深防御）：生产者漏脱时，凭据也不进证据。"""
    sink.emit(_event(detail={"api_key": "sk-dummy-value", "path": "/tmp/a"}))

    raw = sink.path.read_text(encoding="utf-8")
    assert "sk-dummy-value" not in raw
    assert json.loads(raw.splitlines()[0])["detail"] == {"api_key": REDACTED, "path": "/tmp/a"}


@pytest.mark.unit
def test_payload_keeps_control_characters_in_tool_name_for_evidence(sink: JsonlAuditSink) -> None:
    """证据字段保持原样（不得改写），由 JSON 负责转义。"""
    sink.emit(_event(tool_name="evil\ntool"))

    assert sink.read_all()[0].tool_name == "evil\ntool"
    assert len(sink.path.read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    "filename", ["", ".", "..", "sub/audit.jsonl", "sub\\audit.jsonl", "a\x00b"]
)
def test_invalid_filenames_are_rejected(tmp_path: pathlib.Path, filename: str) -> None:
    """文件名一旦可配置就是路径输入 ⇒ 拒绝路径分隔符与特殊名。"""
    with pytest.raises(ValueError):
        JsonlAuditSink(tmp_path / "audit", roots=(tmp_path,), filename=filename)


@pytest.mark.unit
def test_directory_is_created_at_construction(tmp_path: pathlib.Path) -> None:
    """构造期即建目录：目录不可写要在启动时暴露，而不是第一次特权操作之后。"""
    target = tmp_path / "nested" / "audit"

    sink = JsonlAuditSink(target, roots=(tmp_path,))

    assert target.is_dir()
    assert sink.path == target / "audit.jsonl"


# ---------------------------------------------------------------------------
# 装配期路径白名单（P3/P4/P6；对抗性 W 判据见 tests/security/ —— 由验证角色独立写）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_directory_outside_allowed_roots_is_rejected_before_creation(
    tmp_path: pathlib.Path,
) -> None:
    """根外目录 ⇒ ``PathNotAllowedError``，且**没有**先按不可信路径建过目录。"""
    outside = tmp_path / "outside"

    with pytest.raises(PathNotAllowedError):
        JsonlAuditSink(outside, roots=(tmp_path / "allowed",))

    assert not outside.exists()


@pytest.mark.unit
def test_default_roots_come_from_the_config_module_at_call_time(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不给 ``roots`` 时用的是 ``config.ALLOWED_AUDIT_ROOTS``，且**调用时**才读。

    若把常量绑进默认形参，测试替换根集合时会**静默失效**（``audit.md`` §2.5 的单测注意）。
    """
    monkeypatch.setattr(config, "ALLOWED_AUDIT_ROOTS", (tmp_path,))

    sink = JsonlAuditSink(tmp_path / "audit")

    assert sink.path == tmp_path / "audit" / "audit.jsonl"


@pytest.mark.unit
def test_default_roots_are_restrictive_not_unrestricted(tmp_path: pathlib.Path) -> None:
    """``roots`` 默认值的安全取向：``None`` 表示"用常量"，**不是**"不校验"。"""
    default_root = config.ALLOWED_AUDIT_ROOTS[0]
    assert default_root not in tmp_path.parents
    assert tmp_path != default_root

    with pytest.raises(PathNotAllowedError):
        JsonlAuditSink(tmp_path / "audit")


@pytest.mark.unit
def test_empty_roots_allow_nothing(tmp_path: pathlib.Path) -> None:
    """``roots=()`` 不是"放行"而是"没有任何根可选" ⇒ 一切被拒（fail-secure）。"""
    with pytest.raises(PathNotAllowedError):
        JsonlAuditSink(tmp_path, roots=())
