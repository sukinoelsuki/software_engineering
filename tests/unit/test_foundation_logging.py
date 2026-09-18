"""结构化日志装配与脱敏的行为断言（``REQ-OBS-01`` / ``REQ-SEC-07``）。

断言的是**可观察行为**：输出是 JSON、字段齐全、键名命中敏感标记的值被替换、
以及"脱敏发生在渲染之前"。安全断言（注入、越权、逃逸）属 ``tests/security/``，
由验证角色独立完成，本文件不重复自证。
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest
import structlog

from agent_sec_perf.foundation.logging import (
    REDACTED,
    configure_logging,
    get_logger,
    is_sensitive_key,
    redact_sensitive,
    sanitize_for_display,
)


@pytest.fixture
def log_output() -> Iterator[io.StringIO]:
    """确定性输出流，并在用例结束后复原进程级日志状态。

    ``configure_logging`` 改的是**进程级**的 root logger 与 structlog 全局配置；
    不复原就会污染其它测试文件（测试结果不得依赖执行顺序或并发分组）。
    """
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    stream = io.StringIO()
    try:
        yield stream
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in handlers:
            root.addHandler(handler)
        root.setLevel(level)
        structlog.reset_defaults()


def _records(stream: io.StringIO) -> list[dict[str, object]]:
    """把输出流解析成 JSON 记录列表（空行忽略；不忽略非法行——那属于断言失败）。"""
    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


@pytest.mark.unit
def test_log_event_is_emitted_as_one_json_line_with_level_and_timestamp(
    log_output: io.StringIO,
) -> None:
    """一次日志 ⇒ 一行 JSON，含 ``level`` / ``logger`` / ``timestamp`` / 业务字段。"""
    configure_logging(stream=log_output)

    get_logger("probe").info("会话开始", turn=1)

    records = _records(log_output)
    assert len(records) == 1
    record = records[0]
    assert record["event"] == "会话开始"
    assert record["level"] == "info"
    assert record["logger"] == "probe"
    assert record["turn"] == 1
    assert isinstance(record["timestamp"], str)
    assert "T" in str(record["timestamp"])


@pytest.mark.unit
def test_sensitive_values_never_reach_the_log_output(log_output: io.StringIO) -> None:
    """键名命中敏感标记的值一律被替换，原始值**不得**出现在输出里。"""
    configure_logging(stream=log_output)

    get_logger("probe").info(
        "调用模型",
        api_key="sk-live-dummy-value",
        password="hunter2-dummy",
        nested={"access_token": "dummy-token-value"},
    )

    record = _records(log_output)[0]
    nested = record["nested"]
    assert record["api_key"] == REDACTED
    assert record["password"] == REDACTED
    assert isinstance(nested, dict)
    assert nested["access_token"] == REDACTED
    output = log_output.getvalue()
    assert "sk-live-dummy-value" not in output
    assert "hunter2-dummy" not in output
    assert "dummy-token-value" not in output


@pytest.mark.unit
def test_redaction_is_case_insensitive_and_matches_key_substrings() -> None:
    """大小写不敏感、子串匹配（``X-Authorization`` / ``DB_PASSWORD`` 都要命中）。"""
    assert is_sensitive_key("API_TOKEN") is True
    assert is_sensitive_key("X-Authorization") is True
    assert is_sensitive_key("db_password") is True
    assert is_sensitive_key("session_key") is True
    assert is_sensitive_key("tool") is False
    assert is_sensitive_key("arguments") is False
    assert is_sensitive_key(42) is False


@pytest.mark.unit
def test_redaction_recurses_into_nested_structures_and_keeps_shape() -> None:
    """嵌套映射与序列递归脱敏，且结构（键、顺序、非敏感值）保持不变。"""
    payload: dict[str, object] = {
        "tool": "read_file",
        "attempt": 2,
        "headers": {"X-Authorization": "Bearer dummy", "Accept": "application/json"},
        "batch": [{"secret": "dummy"}, {"path": "/tmp/a"}],
    }

    redacted = redact_sensitive(payload)

    assert redacted == {
        "tool": "read_file",
        "attempt": 2,
        "headers": {"X-Authorization": REDACTED, "Accept": "application/json"},
        "batch": [{"secret": REDACTED}, {"path": "/tmp/a"}],
    }


@pytest.mark.unit
def test_redaction_does_not_mutate_the_input_mapping() -> None:
    """脱敏产生新结构，不得就地改写调用方的数据（审计是证据，不能被顺手改掉）。"""
    payload: dict[str, object] = {"token": "dummy", "path": "/tmp/a"}

    redact_sensitive(payload)

    assert payload == {"token": "dummy", "path": "/tmp/a"}


@pytest.mark.unit
def test_sanitize_for_display_neutralizes_control_characters_and_truncates() -> None:
    """展示层净化：换行 / ESC 变成可见字符，超长文本截断并加省略号。"""
    assert sanitize_for_display("read\nfile\x1b[31m") == "read?file?[31m"
    assert sanitize_for_display("a" * 100, limit=10) == "a" * 10 + "…"
    assert sanitize_for_display("tool_name") == "tool_name"


@pytest.mark.unit
def test_configure_logging_is_idempotent(log_output: io.StringIO) -> None:
    """重复装配不产生重复输出（否则每次重配都会把同一条日志写两遍）。"""
    configure_logging(stream=log_output)
    configure_logging(stream=log_output)

    get_logger("probe").info("只应出现一次")

    assert len(_records(log_output)) == 1


@pytest.mark.unit
def test_configure_logging_keeps_handlers_installed_by_others(log_output: io.StringIO) -> None:
    """只回收自己装过的 handler；调用方/``bench`` 已装的 handler 必须保留。"""
    root = logging.getLogger()
    foreign = logging.NullHandler()
    root.addHandler(foreign)

    configure_logging(stream=log_output)

    assert foreign in root.handlers


@pytest.mark.unit
def test_stdlib_logging_records_use_the_same_json_pipeline(log_output: io.StringIO) -> None:
    """stdlib ``logging`` 的记录也走同一条 JSON 管线，且不携带 ``extra`` 字段。

    ``ProcessorFormatter`` 对外来记录只取 ``getMessage()``，不搬运 ``record.__dict__``
    ⇒ stdlib 侧**没有**"把密钥塞进 ``extra`` 而绕过脱敏"的通道。这条断言把该事实钉住：
    哪天上游改成搬运 extras，本用例会失败，提醒我们必须依赖脱敏 processor 兜住。
    """
    configure_logging(stream=log_output)

    logging.getLogger("bench.rounds").warning("进度行", extra={"api_key": "sk-leak-dummy"})

    record = _records(log_output)[0]
    assert record["event"] == "进度行"
    assert record["level"] == "warning"
    assert record["logger"] == "bench.rounds"
    assert "api_key" not in record
    assert "sk-leak-dummy" not in log_output.getvalue()


@pytest.mark.unit
def test_level_accepts_the_name_produced_by_the_config_module(log_output: io.StringIO) -> None:
    """``level`` 接受名称字符串（``foundation.config.LoggingConfig.level`` 就是这种取值）。

    这两个模块由 CLI 装配在一起，取值口径必须对得上：配置给 ``"WARNING"``，
    这里就必须能直接吃下去（否则只能靠调用点手工翻译，翻译处就是漂移点）。
    """
    configure_logging(stream=log_output, level="WARNING")

    logger = get_logger("probe")
    logger.info("低于级别：不应出现")
    logger.warning("达到级别：应出现")

    events = [record["event"] for record in _records(log_output)]
    assert events == ["达到级别：应出现"]


@pytest.mark.unit
def test_exception_is_rendered_as_text_before_json_encoding(log_output: io.StringIO) -> None:
    """异常信息被渲染成文本（而非不可序列化的元组），且级别映射为 ``error``。"""
    configure_logging(stream=log_output)

    try:
        raise ValueError("dummy failure")
    except ValueError:
        get_logger("probe").exception("调用失败")

    record = _records(log_output)[0]
    assert record["level"] == "error"
    assert "ValueError: dummy failure" in str(record["exception"])
