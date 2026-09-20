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
    narrow_granted,
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
def test_narrow_granted_intersects_with_the_allowlist() -> None:
    """收窄 = 交集：只保留"已授予且在 allowlist 内"的能力。"""
    granted = CapabilitySet(granted=frozenset({Capability.READ_FILE, Capability.WRITE_FILE}))
    narrowed = narrow_granted(
        granted, frozenset({Capability.READ_FILE, Capability.EXECUTE_COMMAND})
    )

    assert narrowed == CapabilitySet(granted=frozenset({Capability.READ_FILE}))


@pytest.mark.unit
def test_narrow_granted_never_widens_beyond_the_granted_set() -> None:
    """**对抗性取向**：``allowlist`` 超出 ``granted`` 时结果**仍 ⊆ granted**（防 fail-open）。

    领域包是外部输入（随仓库走）⇒ 它的声明只能收窄。若实现写成"按 allowlist 重建集合"，
    本用例会失败：结果里会出现用户从未授予的 ``EXECUTE_COMMAND`` / ``NETWORK_OUTBOUND``。
    """
    granted = CapabilitySet(granted=frozenset({Capability.READ_FILE}))

    narrowed = narrow_granted(granted, frozenset(Capability))

    assert narrowed.granted <= granted.granted
    assert narrowed.allows(Capability.EXECUTE_COMMAND) is False
    assert narrowed.allows(Capability.NETWORK_OUTBOUND) is False


@pytest.mark.unit
def test_narrow_granted_with_empty_allowlist_denies_everything() -> None:
    """空 allowlist = 什么都不给，**不是**"不限制"（与 pack 的"必填可为空数组"同口径）。"""
    granted = CapabilitySet(granted=frozenset(Capability))

    assert narrow_granted(granted, frozenset()) == DENY_ALL


@pytest.mark.unit
def test_narrow_granted_cannot_resurrect_an_unconfigured_grant() -> None:
    """``granted`` 为空时任何 allowlist 都无济于事（default-deny 不得被 allowlist 绕过）。"""
    assert narrow_granted(DENY_ALL, frozenset(Capability)) == DENY_ALL


@pytest.mark.unit
def test_narrow_granted_is_commutative_and_idempotent() -> None:
    """交集给出的两条性质：交换律、幂等（重复收窄既不继续减小，也不放大）。"""
    granted = CapabilitySet(granted=frozenset({Capability.READ_FILE, Capability.WRITE_FILE}))
    allowlist = frozenset({Capability.WRITE_FILE, Capability.EXECUTE_COMMAND})

    narrowed = narrow_granted(granted, allowlist)

    assert narrow_granted(granted, allowlist) == narrow_granted(
        CapabilitySet(granted=allowlist), granted.granted
    )
    assert narrow_granted(narrowed, allowlist) == narrowed


@pytest.mark.unit
def test_as_names_is_sorted_for_stable_audit_output() -> None:
    """能力名输出排序稳定（审计与日志要能跨轮比对）。"""
    granted = CapabilitySet(
        granted=frozenset({Capability.WRITE_FILE, Capability.READ_FILE, Capability.EXECUTE_COMMAND})
    )

    assert granted.as_names() == ("execute_command", "read_file", "write_file")
