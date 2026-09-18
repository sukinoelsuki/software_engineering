"""工具裁剪的行为断言：档位预算、默认少暴露、确定性、只读、未知档位拒绝。

这是实现侧的**功能**断言。对抗性断言（"未授权/未暴露的工具调用被拒**且**审计可回放"，
``ADR-0015`` §7.2 的 ``S1``）属 ``tests/security/``，由验证角色独立完成
（安全断言不得由实现者自证）。

变异探针（说明这些断言不是"陪跑"，逐条能被一个具体改动杀死）：

* 把 ``spec.capabilities <= budget`` 改成 ``spec.capabilities & budget`` ⇒
  :func:`test_tool_requiring_write_is_hidden_on_weakest_tier` 失败；
* 把 ``S`` 档预算加上 ``EXECUTE_COMMAND`` ⇒
  :func:`test_command_tool_is_only_exposed_on_strongest_tier` 失败；
* 从入选条件里删掉 ``spec.capabilities``（非空判定）⇒
  :func:`test_tool_without_declared_capability_is_never_exposed` 失败；
* 去掉结果里的 ``sorted(...)`` ⇒
  :func:`test_selection_is_deterministic_and_order_independent` 失败；
* 把未知档位的 ``raise ValueError`` 改成"回落到 ``HardwareTier.L``" ⇒
  :func:`test_unknown_tier_is_rejected_instead_of_guessed` 失败。
"""

from __future__ import annotations

import pytest

from agent_sec_perf.contracts.model import HardwareTier
from agent_sec_perf.contracts.policy import Capability
from agent_sec_perf.contracts.tools import ToolSpec
from agent_sec_perf.harness.trimming import (
    TIER_CAPABILITY_BUDGET,
    exposed_tool_names,
    select_tools,
)


def _spec(name: str, *capabilities: Capability) -> ToolSpec:
    """构造一个最小可用的工具描述（测试自带构造器，不借用生产代码）。"""
    return ToolSpec(
        name=name,
        description=f"{name}（测试用）",
        parameters_schema={"type": "object", "properties": {}, "additionalProperties": False},
        capabilities=frozenset(capabilities),
    )


#: 与内置工具同形的候选集合：能力不同、名字可排序（用于断言确定性）。
READ_FILE = _spec("read_file", Capability.READ_FILE)
LIST_DIR = _spec("list_dir", Capability.READ_FILE)
WRITE_FILE = _spec("write_file", Capability.WRITE_FILE)
RUN_COMMAND = _spec("run_command", Capability.EXECUTE_COMMAND)
FETCH_URL = _spec("fetch_url", Capability.NETWORK_OUTBOUND)
MYSTERY = _spec("mystery_tool")
READ_AND_RUN = _spec("read_and_run", Capability.READ_FILE, Capability.EXECUTE_COMMAND)

ALL_SPECS = (READ_FILE, LIST_DIR, WRITE_FILE, RUN_COMMAND, FETCH_URL, MYSTERY, READ_AND_RUN)


def _names(tier: HardwareTier) -> set[str]:
    return {spec.name for spec in select_tools(ALL_SPECS, tier)}


@pytest.mark.unit
def test_budget_is_monotone_across_tiers() -> None:
    """能力预算 S ⊆ M ⊆ L；档位越高只会多给能力，不会换一批。"""
    assert TIER_CAPABILITY_BUDGET[HardwareTier.S] < TIER_CAPABILITY_BUDGET[HardwareTier.M]
    assert TIER_CAPABILITY_BUDGET[HardwareTier.M] < TIER_CAPABILITY_BUDGET[HardwareTier.L]


@pytest.mark.unit
def test_weak_tier_exposes_fewer_tools_than_strong_tier() -> None:
    """弱档位下暴露的工具数**严格少于**强档位（REQ-HARNESS-03 的验收取向）。"""
    weak = select_tools(ALL_SPECS, HardwareTier.S)
    strong = select_tools(ALL_SPECS, HardwareTier.L)

    assert len(weak) < len(strong)
    assert len(weak) * 2 <= len(strong)


@pytest.mark.unit
def test_read_only_tools_are_exposed_at_every_tier() -> None:
    """只读工具在三档都可用（裁剪不是"越弱越什么都干不了"）。"""
    for tier in HardwareTier:
        assert {"read_file", "list_dir"} <= _names(tier)


@pytest.mark.unit
def test_tool_requiring_write_is_hidden_on_weakest_tier() -> None:
    """写文件在 S 档不暴露，在 M / L 档暴露。"""
    assert "write_file" not in _names(HardwareTier.S)
    assert "write_file" in _names(HardwareTier.M)
    assert "write_file" in _names(HardwareTier.L)


@pytest.mark.unit
def test_command_tool_is_only_exposed_on_strongest_tier() -> None:
    """命令执行只在 L 档暴露（能力最强的档位才拿到最危险的工具）。"""
    assert "run_command" not in _names(HardwareTier.S)
    assert "run_command" not in _names(HardwareTier.M)
    assert "run_command" in _names(HardwareTier.L)


@pytest.mark.unit
def test_network_tool_is_never_exposed_by_any_tier() -> None:
    """联网工具在任何档位都**不**自动暴露（出站 default-deny，只能显式授权）。"""
    for tier in HardwareTier:
        assert Capability.NETWORK_OUTBOUND not in TIER_CAPABILITY_BUDGET[tier]
        assert "fetch_url" not in _names(tier)


@pytest.mark.unit
def test_tool_without_declared_capability_is_never_exposed() -> None:
    """未声明任何能力属声明缺陷 ⇒ 不暴露（不替它猜一个能力，少暴露优先）。"""
    for tier in HardwareTier:
        assert "mystery_tool" not in _names(tier)


@pytest.mark.unit
def test_tool_with_partially_out_of_budget_capability_is_hidden_entirely() -> None:
    """只要有一个能力超出预算，整个工具都不暴露（不做"裁掉超额能力再暴露"）。"""
    assert "read_and_run" not in _names(HardwareTier.S)
    assert "read_and_run" not in _names(HardwareTier.M)
    assert "read_and_run" in _names(HardwareTier.L)


@pytest.mark.unit
def test_selection_is_deterministic_and_order_independent() -> None:
    """同一集合的不同顺序必得同一输出，且按工具名排序（确定性 + 可缓存）。"""
    forward = select_tools(ALL_SPECS, HardwareTier.L)
    backward = select_tools(tuple(reversed(ALL_SPECS)), HardwareTier.L)

    assert forward == backward
    assert [spec.name for spec in forward] == sorted(spec.name for spec in forward)


@pytest.mark.unit
def test_selection_returns_the_same_immutable_spec_objects() -> None:
    """裁剪是**只读**的：返回的就是入参那些 ``ToolSpec`` 对象，不做拷贝与改写。"""
    selected = select_tools(ALL_SPECS, HardwareTier.L)

    by_name = {spec.name: spec for spec in ALL_SPECS}
    assert all(spec is by_name[spec.name] for spec in selected)


@pytest.mark.unit
def test_empty_candidate_set_yields_empty_selection() -> None:
    """没有候选工具时返回空元组（空结果是合法状态，不是错误）。"""
    assert select_tools((), HardwareTier.L) == ()
    assert exposed_tool_names((), HardwareTier.L) == frozenset()


@pytest.mark.unit
def test_unknown_tier_is_rejected_instead_of_guessed() -> None:
    """未知档位 ⇒ 报错，**不回落**到任何档位的预算（不猜就等于不给多余权限）。"""
    with pytest.raises(ValueError, match="未知的硬件档位"):
        select_tools(ALL_SPECS, "xl")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="未知的硬件档位"):
        exposed_tool_names(ALL_SPECS, "xl")  # type: ignore[arg-type]


@pytest.mark.unit
def test_exposed_names_match_the_selected_specs() -> None:
    """名字集合与所选描述一一对应（调用点据此判定"未知工具"）。"""
    for tier in HardwareTier:
        selected = select_tools(ALL_SPECS, tier)

        assert exposed_tool_names(ALL_SPECS, tier) == {spec.name for spec in selected}


@pytest.mark.unit
def test_trimmed_out_tool_name_is_not_exposed_to_the_caller() -> None:
    """被裁剪掉的工具名**不在**暴露集合里 ⇒ 调用点必须按未知工具默认拒绝。

    这正是 ``REQ-SEC-01`` 在本模块上的取向：裁剪后仍然"能被调用"就等于裁剪无效。
    """
    weak_names = exposed_tool_names(ALL_SPECS, HardwareTier.S)

    assert "run_command" not in weak_names
    assert "write_file" not in weak_names
    assert "fetch_url" not in weak_names


@pytest.mark.unit
def test_budget_mapping_is_read_only() -> None:
    """档位预算只读：调用方不得就地放宽某个档位的能力。"""
    with pytest.raises(TypeError):
        TIER_CAPABILITY_BUDGET[HardwareTier.S] = frozenset(Capability)  # type: ignore[index]
