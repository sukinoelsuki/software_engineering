"""仓库内置示例领域包的行为断言（``examples/packs/``）。

被测对象是**仓库里真实存在的那两份包**：本模块用生产代码
``harness.domain_pack.load_pack`` 真加载它们，断言"能加载"且"不越过只读边界"。
临时目录里合成的包只用于**反向断言**——证明这些检查不是恒过的。

为什么这条边界必须由用例钉住：示例包是本产品"让工具真的能被执行"的正规通路
（不配包时 ``PolicyEngine`` 对所有工具取 ``DEFAULT_TOOL_RISK = HIGH`` ⇒ 非交互会话一律拒绝）。
因此"顺手给 ``run_command`` 也声明 ``low`` 更方便"是最可能发生的改动，
而它同时是**降级安全默认**——本模块的用例在那个改动下必须翻红。

变异探针（逐条能被一个具体改动杀死）：

* 往任一示例包的 ``[security.risk_overrides]`` 加 ``run_command = "low"`` ⇒
  :func:`test_readonly_downgrade_check_rejects_a_synthetic_shell_downgrade` 之外的
  :func:`test_risk_overrides_stay_within_the_readonly_allowlist` 失败；
* 把 ``run_command`` 加进 ``[tools] allowlist`` ⇒
  :func:`test_tool_allowlist_matches_the_registered_tools` 失败；
* 把 ``execute_command`` 加进 ``[security] capabilities`` ⇒
  :func:`test_capabilities_are_readonly_only` 失败；
* 把只读白名单测试里的 ``_readonly_downgrade_offenders`` 换成"恒返回空列表" ⇒
  反向断言 :func:`test_readonly_downgrade_check_rejects_a_synthetic_shell_downgrade` 失败；
* 删掉 ``examples/README.md`` 里的"不是安全默认的替代品" ⇒
  :func:`test_readme_documents_every_example_pack` 失败。

安全断言的分工：本模块只做**实现侧的功能断言**（示例包不越界）。
"示例包被加载后仍不能扩权 / 不能绕过 default-deny"一类对抗性断言属 ``tests/security/``，
由验证角色独立完成。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_sec_perf.contracts.audit import AuditEvent
from agent_sec_perf.contracts.policy import Capability, RiskLevel
from agent_sec_perf.contracts.tools import Tool
from agent_sec_perf.harness.domain_pack import DomainPack, load_pack
from agent_sec_perf.tools.files import ListDirTool, ReadFileTool, WriteFileTool
from agent_sec_perf.tools.shell import ShellCommandTool

#: 仓库根（本文件位于 ``tests/unit/``）。
REPO_ROOT = Path(__file__).resolve().parents[2]

#: 示例包目录（``load_pack`` 接的是**目录**，不是 ``pack.toml`` 文件）。
PACKS_ROOT = REPO_ROOT / "examples" / "packs"

#: 仓库内置的示例包目录名（与各自的 ``pack.name`` 同名，用例会断言这一点）。
EXAMPLE_PACKS: tuple[str, ...] = ("coding-readonly", "tech-manual-qna")

#: **写死**的"允许在示例包中降级的只读工具名"白名单（独立于被测文件，不由它推导）。
#: 判据是"这个工具只读文件系统、不执行命令、不写出站流量"。
READONLY_TOOL_ALLOWLIST = frozenset({"read_file", "list_dir"})

#: **写死**的"允许在示例包中出现的能力"白名单（只读能力）。
READONLY_CAPABILITIES = frozenset({Capability.READ_FILE})

#: 只读示例包**不得**出现的能力（出现即越权面）。
FORBIDDEN_CAPABILITIES = frozenset(
    {Capability.WRITE_FILE, Capability.EXECUTE_COMMAND, Capability.NETWORK_OUTBOUND}
)

#: 与 ``domain_pack`` 同一口径的形状期望（这里**独立**写一遍：期望不应由被测实现给出）。
_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")

#: 反向断言用的合成包：把 ``run_command`` 也暴露并声明为 ``low``。
_SYNTHETIC_DOWNGRADE_PACK = """\
[pack]
name = "synthetic-downgrade"
version = "0.1.0"

[tools]
allowlist = ["read_file", "list_dir", "run_command"]

[security]
capabilities = ["read_file", "execute_command"]

[security.risk_overrides]
run_command = "low"
"""


class _NoopSink:
    """仅为构造内置工具实例而存在的最小 ``AuditSink``（只取 ``spec``，不触发任何 I/O）。"""

    def emit(self, event: AuditEvent) -> None:
        return

    def flush(self) -> None:
        return


def _builtin_tools() -> tuple[Tool, ...]:
    """四个内置工具实例（构造期无 I/O；工具名从它们的 ``spec`` 读取）。"""
    sink = _NoopSink()
    return (ReadFileTool(sink), WriteFileTool(sink), ListDirTool(sink), ShellCommandTool(sink))


#: ``src/agent_sec_perf/tools/`` 里**实际**存在的工具名集合。
#: 用工具实例求它、而**不是**在用例里手抄一份：包里的名字与实现漂移时必须被发现。
ACTUAL_TOOL_NAMES = frozenset(tool.spec.name for tool in _builtin_tools())


def _read_example_pack(name: str) -> DomainPack:
    """用生产代码加载仓库内的一份示例包（``roots`` 收窄到仓库根）。"""
    return load_pack(PACKS_ROOT / name, roots=(REPO_ROOT,), known_tools=ACTUAL_TOOL_NAMES)


def _readonly_downgrade_offenders(pack: DomainPack) -> list[str]:
    """被声明了风险、但**不在**只读工具白名单内的工具名（升序；空列表 = 合规）。

    这是本模块真正要守住的那条线：``load_pack`` **不判断**"给某个工具降级是否合规"
    （那属策略决策），所以"示例包里只允许降级只读工具"这条约束必须由用例承担。
    """
    return sorted(set(pack.risk_overrides) - READONLY_TOOL_ALLOWLIST)


# ---------------------------------------------------------------------------
# 正向：真实加载仓库内的两份示例包
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("name", EXAMPLE_PACKS)
def test_example_pack_loads_from_the_repository(name: str) -> None:
    """两个包都能被**真实** ``load_pack`` 加载，且 ``source`` 指向仓库内的 ``pack.toml``。"""
    pack = _read_example_pack(name)

    assert pack.name == name
    assert _NAME_PATTERN.fullmatch(pack.name) is not None
    assert _VERSION_PATTERN.fullmatch(pack.version) is not None
    assert pack.source == (PACKS_ROOT / name / "pack.toml").resolve()
    assert pack.source.is_file()
    assert REPO_ROOT in pack.source.parents
    # 场景指导必须真的带上（空片段等于"这个包只有权限声明、没有场景约束"）。
    assert pack.prompt_fragments
    assert all(fragment.strip() for fragment in pack.prompt_fragments)


@pytest.mark.unit
@pytest.mark.parametrize("name", EXAMPLE_PACKS)
def test_tool_allowlist_matches_the_registered_tools(name: str) -> None:
    """``tools.allowlist`` 只含**实际存在**的工具名，且就是只读白名单本身（不得含未知名）。"""
    pack = _read_example_pack(name)

    unknown = pack.tool_allowlist - ACTUAL_TOOL_NAMES
    assert unknown == frozenset(), f"allowlist 含未注册的工具名：{sorted(unknown)}"
    assert pack.tool_allowlist == READONLY_TOOL_ALLOWLIST


@pytest.mark.unit
@pytest.mark.parametrize("name", EXAMPLE_PACKS)
def test_risk_overrides_stay_within_the_readonly_allowlist(name: str) -> None:
    """``risk_overrides`` 的每个工具都在只读白名单内，且暴露面内**每个**工具都被声明为 ``low``。

    后半句是**可用性**要求，不是安全要求：未声明风险的工具取 ``DEFAULT_TOOL_RISK = HIGH``
    ⇒ 需人工确认 ⇒ 非交互会话一律拒绝 ⇒ 示例包失去意义。前半句才是安全要求。
    """
    pack = _read_example_pack(name)

    assert _readonly_downgrade_offenders(pack) == []
    assert set(pack.risk_overrides) == set(pack.tool_allowlist)
    assert set(pack.risk_overrides.values()) == {RiskLevel.LOW}


@pytest.mark.unit
@pytest.mark.parametrize("name", EXAMPLE_PACKS)
def test_capabilities_are_readonly_only(name: str) -> None:
    """``capabilities_allowlist`` 只含只读能力（写 / 执行 / 出站一律不出现）。"""
    pack = _read_example_pack(name)

    assert pack.capabilities_allowlist <= frozenset(Capability)
    assert pack.capabilities_allowlist == READONLY_CAPABILITIES
    assert not (pack.capabilities_allowlist & FORBIDDEN_CAPABILITIES)


# ---------------------------------------------------------------------------
# 反向断言：白名单检查不是恒过的
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_readonly_downgrade_check_rejects_a_synthetic_shell_downgrade(tmp_path: Path) -> None:
    """合成一份"把 ``run_command`` 降级进包"的配置 ⇒ 白名单检查必须拒绝它。

    合成包放在 ``tmp_path`` 下，**不往 ``examples/`` 写坏包**。
    ``load_pack`` 本身**会加载成功**（它只校验名字与取值，不判断降级是否合规）——
    这正是"必须有一条用例守住这条线"的理由。
    """
    pack_dir = tmp_path / "synthetic-downgrade"
    pack_dir.mkdir()
    (pack_dir / "pack.toml").write_text(_SYNTHETIC_DOWNGRADE_PACK, encoding="utf-8")

    pack = load_pack(pack_dir, roots=(tmp_path,), known_tools=ACTUAL_TOOL_NAMES)

    assert _readonly_downgrade_offenders(pack) == ["run_command"]
    assert pack.tool_allowlist - READONLY_TOOL_ALLOWLIST == {"run_command"}
    assert pack.capabilities_allowlist & FORBIDDEN_CAPABILITIES == {Capability.EXECUTE_COMMAND}
    # 同一套检查对仓库内的真包必须给出"合规"——否则本用例只能证明检查恒真。
    for name in EXAMPLE_PACKS:
        assert _readonly_downgrade_offenders(_read_example_pack(name)) == []


# ---------------------------------------------------------------------------
# 说明文档与包一致（示例包的用户入口是 examples/README.md）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_readme_documents_every_example_pack() -> None:
    """``examples/README.md`` 必须逐个介绍包、写明 ``--pack`` 用法与"不是安全默认的替代品"。"""
    text = (REPO_ROOT / "examples" / "README.md").read_text(encoding="utf-8")

    for name in EXAMPLE_PACKS:
        assert name in text, f"README 未介绍示例包 {name}"
    assert "--pack" in text
    assert "不是安全默认的替代品" in text
