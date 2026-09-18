"""能力模型的行为断言：default-deny、无隐含继承、未知能力名即拒绝。

这是实现侧的**功能**断言；"未授权操作拦截率 100%"这类安全行为断言属 ``tests/security/``，
由验证角色独立完成（安全断言不得由实现者自证）。
"""

from __future__ import annotations

import dataclasses

import pytest

from agent_sec_perf.contracts.policy import Capability
from agent_sec_perf.security.capabilities import (
    DENY_ALL,
    CapabilitySet,
    UnknownCapabilityError,
    parse_capabilities,
)


@pytest.mark.unit
def test_default_capability_set_denies_every_capability() -> None:
    """默认构造 = 什么都不授予（default-deny 的默认值取向）。"""
    empty = CapabilitySet()

    for capability in Capability:
        assert empty.allows(capability) is False
    assert empty.missing(frozenset(Capability)) == frozenset(Capability)


@pytest.mark.unit
def test_deny_all_constant_is_the_empty_grant() -> None:
    """``DENY_ALL`` 就是空授予集合（给"想不出该给什么"的调用方一个明确的默认）。"""
    assert CapabilitySet() == DENY_ALL
    assert DENY_ALL.granted == frozenset()


@pytest.mark.unit
def test_explicitly_granted_capability_is_allowed() -> None:
    """显式授予的能力才放行，且只放行它自己。"""
    granted = CapabilitySet(granted=frozenset({Capability.READ_FILE}))

    assert granted.allows(Capability.READ_FILE) is True
    assert granted.allows(Capability.WRITE_FILE) is False
    assert granted.allows_all([Capability.READ_FILE]) is True


@pytest.mark.unit
def test_partial_grant_denies_request_and_reports_missing_capabilities() -> None:
    """多能力请求只要缺一个就整体不放行，并报出缺的是哪一个。"""
    granted = CapabilitySet(granted=frozenset({Capability.READ_FILE}))
    requested = [Capability.READ_FILE, Capability.EXECUTE_COMMAND]

    assert granted.allows_all(requested) is False
    assert granted.missing(requested) == frozenset({Capability.EXECUTE_COMMAND})


@pytest.mark.unit
def test_capabilities_have_no_implied_relationships() -> None:
    """能力之间**没有**隐含继承/包含关系：写权限不等于读权限，读也不蕴含执行。"""
    writer = CapabilitySet(granted=frozenset({Capability.WRITE_FILE}))
    network = CapabilitySet(granted=frozenset({Capability.NETWORK_OUTBOUND}))

    assert writer.allows(Capability.READ_FILE) is False
    assert writer.allows(Capability.EXECUTE_COMMAND) is False
    assert network.allows(Capability.EXECUTE_COMMAND) is False


@pytest.mark.unit
def test_parse_capabilities_maps_declared_names_and_drops_duplicates() -> None:
    """声明式名字解析成枚举；重复声明自然去重（顺序不影响结果）。"""
    parsed = parse_capabilities(["read_file", "write_file", "read_file"])

    assert parsed == CapabilitySet(granted=frozenset({Capability.READ_FILE, Capability.WRITE_FILE}))


@pytest.mark.unit
def test_empty_declaration_grants_nothing() -> None:
    """空声明 = 不授予任何能力（配置里漏写不会变成"默认全开"）。"""
    assert parse_capabilities([]) == DENY_ALL


@pytest.mark.unit
def test_parse_capabilities_rejects_unknown_name_instead_of_ignoring_it() -> None:
    """拼错的名字必须报错：静默跳过会变成一次"以为授予了、其实没有"的降权。"""
    with pytest.raises(UnknownCapabilityError) as excinfo:
        parse_capabilities(["read_file", "write_files"])

    assert "write_files" in str(excinfo.value)


@pytest.mark.unit
def test_unknown_capability_error_neutralizes_control_characters() -> None:
    """错误信息里的名字来自不可信文本 ⇒ 换行/转义序列必须被中和（防日志伪造）。"""
    with pytest.raises(UnknownCapabilityError) as excinfo:
        parse_capabilities(["read\nfile\x1b[31m"])

    message = str(excinfo.value)
    assert "\n" not in message
    assert "\x1b" not in message


@pytest.mark.unit
def test_capability_set_is_immutable() -> None:
    """授予集合不可变：构造后不得被就地扩权。"""
    granted = CapabilitySet(granted=frozenset({Capability.READ_FILE}))

    with pytest.raises(dataclasses.FrozenInstanceError):
        granted.__setattr__("granted", frozenset({Capability.WRITE_FILE}))


@pytest.mark.unit
def test_as_names_is_sorted_for_stable_audit_output() -> None:
    """能力名输出排序稳定（审计与日志要能跨轮比对）。"""
    granted = CapabilitySet(
        granted=frozenset({Capability.WRITE_FILE, Capability.READ_FILE, Capability.EXECUTE_COMMAND})
    )

    assert granted.as_names() == ("execute_command", "read_file", "write_file")
