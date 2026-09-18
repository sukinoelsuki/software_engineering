"""``ToolRegistry`` 与描述摘要的行为断言（``G8``）。

重点在**fail-secure 的两条路径**：未知工具返回 ``None``（不抛异常），
以及外部来源的描述摘要不一致 ⇒ **拒绝使用**（``REQ-TOOL-03`` 防投毒 / rug-pull）。

这是实现侧的功能断言；"工具描述冒充 / 越权调用"一类对抗性安全断言属 ``tests/security/``，
由验证角色独立完成（安全断言不得由实现者自证）。
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from agent_sec_perf.contracts.policy import Capability
from agent_sec_perf.contracts.tools import ExecutionContext, ToolResult, ToolSpec
from agent_sec_perf.tools.registry import (
    ToolRegistrationError,
    ToolRegistry,
    description_digest,
)

#: 独立于被测代码写死的期望摘要（固定输入的 sha256）。
#: 若规范化口径被改动，这条断言会失败——这正是它的用途。
EXPECTED_DIGEST = "661ef1a34a0f866129b901c31e58bdd9aca111964218401d81931485dc37b2ba"

FIXED_NAME = "read_file"
FIXED_DESCRIPTION = "读取文件"
FIXED_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
}


class FakeTool:
    """``Tool`` 的最小替身（注册表只用到 ``spec``）。"""

    def __init__(self, spec: ToolSpec) -> None:
        self._spec = spec

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def invoke(self, args: Mapping[str, object], *, ctx: ExecutionContext) -> ToolResult:
        return ToolResult(ok=True, content="")


def make_spec(
    name: str = FIXED_NAME,
    *,
    source: str = "builtin",
    digest: str | None = None,
    description: str = FIXED_DESCRIPTION,
    parameters_schema: Mapping[str, object] | None = None,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        parameters_schema=FIXED_SCHEMA if parameters_schema is None else parameters_schema,
        capabilities=frozenset({Capability.READ_FILE}),
        source=source,
        description_digest=digest,
    )


# ---------------------------------------------------------------------------
# 摘要口径
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_description_digest_matches_fixed_expectation() -> None:
    assert description_digest(FIXED_NAME, FIXED_DESCRIPTION, FIXED_SCHEMA) == EXPECTED_DIGEST


@pytest.mark.unit
def test_description_digest_is_key_order_insensitive() -> None:
    reordered: Mapping[str, object] = {
        "properties": {"path": {"type": "string"}},
        "type": "object",
    }
    assert description_digest(FIXED_NAME, FIXED_DESCRIPTION, reordered) == EXPECTED_DIGEST


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "description"),
    [
        ("other_file", FIXED_DESCRIPTION),
        (FIXED_NAME, "另一个描述"),
    ],
)
def test_description_digest_changes_with_content(name: str, description: str) -> None:
    assert description_digest(name, description, FIXED_SCHEMA) != EXPECTED_DIGEST


@pytest.mark.unit
def test_description_digest_changes_with_schema() -> None:
    other: Mapping[str, object] = {"type": "object", "properties": {"x": {"type": "integer"}}}
    assert description_digest(FIXED_NAME, FIXED_DESCRIPTION, other) != EXPECTED_DIGEST


# ---------------------------------------------------------------------------
# 注册与解析
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_specs_preserve_registration_order() -> None:
    first = FakeTool(make_spec("a"))
    second = FakeTool(make_spec("b"))
    registry = ToolRegistry([first, second])
    assert [spec.name for spec in registry.specs()] == ["a", "b"]


@pytest.mark.unit
def test_resolve_returns_registered_tool() -> None:
    tool = FakeTool(make_spec())
    registry = ToolRegistry([tool])
    assert registry.resolve(FIXED_NAME) is tool


@pytest.mark.unit
def test_resolve_unknown_tool_returns_none_without_raising() -> None:
    registry = ToolRegistry([FakeTool(make_spec())])
    assert registry.resolve("does_not_exist") is None


@pytest.mark.unit
def test_resolve_non_string_name_returns_none() -> None:
    registry = ToolRegistry([FakeTool(make_spec())])
    assert registry.resolve(123) is None  # type: ignore[arg-type]


@pytest.mark.unit
def test_duplicate_tool_name_is_rejected() -> None:
    with pytest.raises(ToolRegistrationError):
        ToolRegistry([FakeTool(make_spec()), FakeTool(make_spec())])


@pytest.mark.unit
def test_empty_registry_is_allowed() -> None:
    registry = ToolRegistry([])
    assert registry.specs() == ()
    assert registry.resolve("anything") is None


# ---------------------------------------------------------------------------
# 摘要校验：builtin 与外部来源
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_builtin_without_digest_is_accepted() -> None:
    registry = ToolRegistry([FakeTool(make_spec())])
    assert registry.resolve(FIXED_NAME) is not None


@pytest.mark.unit
def test_builtin_with_matching_digest_is_accepted() -> None:
    registry = ToolRegistry([FakeTool(make_spec(digest=EXPECTED_DIGEST))])
    assert registry.resolve(FIXED_NAME) is not None


@pytest.mark.unit
def test_builtin_with_mismatched_digest_is_rejected() -> None:
    with pytest.raises(ToolRegistrationError):
        ToolRegistry([FakeTool(make_spec(digest="0" * 64))])


@pytest.mark.unit
def test_external_without_digest_is_rejected() -> None:
    with pytest.raises(ToolRegistrationError):
        ToolRegistry([FakeTool(make_spec(source="mcp:demo", digest=None))])


@pytest.mark.unit
def test_external_with_mismatched_digest_is_rejected() -> None:
    with pytest.raises(ToolRegistrationError):
        ToolRegistry([FakeTool(make_spec(source="mcp:demo", digest="0" * 64))])


@pytest.mark.unit
def test_external_with_matching_digest_is_accepted() -> None:
    registry = ToolRegistry([FakeTool(make_spec(source="mcp:demo", digest=EXPECTED_DIGEST))])
    assert registry.resolve(FIXED_NAME) is not None


@pytest.mark.unit
def test_digest_mismatch_blocks_the_whole_registration() -> None:
    """fail-secure：拒绝启动，**不**静默剔除被篡改的工具。"""
    good = FakeTool(make_spec("ok_tool"))
    bad = FakeTool(make_spec("bad_tool", source="mcp:demo", digest="0" * 64))
    with pytest.raises(ToolRegistrationError):
        ToolRegistry([good, bad])
