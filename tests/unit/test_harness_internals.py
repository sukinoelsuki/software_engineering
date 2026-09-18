"""``harness/`` 内部结构约束（契约 ``H-7``）：H1 不依赖 L2/L4 实现、H2 叶子零依赖、
以及"能力档位轴不得回退到硬件档位"的结构性守卫。

为什么要有这个文件：契约 §3.2 的两条禁止项（H1/H2）原先**只写在文档里**；
"约定只写在文档里"是本项目**已复现过的**失败模式（``ADR-0015`` §7.1 的动机、
``harness.md`` §3.2 的"建议的机器检查"）。这里把它们落成 ``ast`` 扫描，
使违反者**当场变红**，而不是等评审时被肉眼发现。

变异探针（逐条能被一个具体改动杀死）：

* 在 ``harness/`` 任一文件里加 ``from agent_sec_perf.tools.files import ReadFileTool`` ⇒
  :func:`test_harness_does_not_import_l2_l4_implementations` 失败；
* 在 ``trimming.py`` 里加 ``from agent_sec_perf.harness.errors import ErrorDisposition`` ⇒
  :func:`test_leaf_modules_have_no_mutual_dependencies` 失败；
* 把 ``select_tools`` 的 ``tier`` 注记改回 ``HardwareTier`` ⇒
  :func:`test_capability_tier_axis_is_not_replaced_by_the_hardware_axis` 失败。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SRC_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src"
PACKAGE_ROOT = SRC_ROOT / "agent_sec_perf"
HARNESS_ROOT = PACKAGE_ROOT / "harness"
ROOT_PACKAGE = "agent_sec_perf"
HARNESS_LAYER = "harness"

#: H1：``harness/**`` 不得 import 这些层的**实现模块**——它们一律以构造注入的
#: Protocol 到达（契约 §3.2）。这条比对 ``architecture.md`` §2.3 的白名单**更严**。
FORBIDDEN_LAYERS = frozenset({"model", "tools", "security", "observability", "cli"})

#: H2：叶子模块集合。两两之间**互不 import**（数据由 ``session`` 取出后以参数传入）。
LEAF_UNITS = frozenset({"prompts", "trimming", "context", "checkpoint", "domain_pack", "errors"})

#: 已落地的叶子模块：用于保证上面的检查**不是空集通过**。
LANDED_LEAF_FILES = ("errors.py", "prompts.py", "trimming.py")

#: H2 的补充：``loop`` **不** import ``session``、**不** import ``domain_pack``。
FORBIDDEN_INTERNAL_IMPORTS = frozenset({("loop", "session"), ("loop", "domain_pack")})

#: 档位轴守卫的对象：这两个模块按契约 §3.1 收 ``CapabilityTier``。
AXIS_MODULES = ("prompts.py", "trimming.py")
FORBIDDEN_AXIS_NAME = "HardwareTier"


# ---------------------------------------------------------------------------
# AST 辅助：只判定"本项目模块"，并同时给出层名与 harness 内部单元名
# ---------------------------------------------------------------------------


def _harness_files() -> list[pathlib.Path]:
    return sorted(HARNESS_ROOT.rglob("*.py"))


def _unit_of(path: pathlib.Path) -> str:
    """文件所属的 harness 内部单元（叶子模块名 / 子包名）。"""
    return path.relative_to(HARNESS_ROOT).parts[0].removesuffix(".py")


def _module_parts(path: pathlib.Path) -> tuple[str, ...]:
    """文件相对 ``agent_sec_perf`` 的模块分量（``__init__`` 已折叠）。"""
    parts = list(path.relative_to(PACKAGE_ROOT).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return tuple(parts)


def _is_project(module: str) -> bool:
    return module == ROOT_PACKAGE or module.startswith(f"{ROOT_PACKAGE}.")


def _resolve_import_from(node: ast.ImportFrom, module: tuple[str, ...]) -> str | None:
    """把 ``from ... import ...`` 解析为绝对模块名；非本项目模块返回 ``None``。"""
    if node.level == 0:
        if node.module is None:
            return None
        return node.module if _is_project(node.module) else None
    package = module[:-1]
    keep = len(package) - (node.level - 1)
    if keep < 0:
        return None
    base = package[:keep]
    parts = (*base, *node.module.split(".")) if node.module else base
    if not parts:
        return None
    return ".".join((ROOT_PACKAGE, *parts))


def _imported_project_modules(path: pathlib.Path) -> set[str]:
    """本文件 import 的**本项目**模块名（绝对形式，含 ``from X import y`` 的成员名）。

    把成员名一并展开是刻意的：``from agent_sec_perf.harness import errors`` 这种写法里，
    被依赖的**单元**藏在 ``node.names`` 而不是 ``node.module``——漏掉它，
    这条检查就存在一条能被绕过的写法。
    """
    module = _module_parts(path)
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names if _is_project(alias.name))
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_import_from(node, module)
            if resolved is None:
                continue
            found.add(resolved)
            found.update(f"{resolved}.{alias.name}" for alias in node.names if alias.name != "*")
    return found


def _imported_layers(path: pathlib.Path) -> set[str]:
    """本文件 import 的兄弟层名集合。"""
    layers: set[str] = set()
    for dotted in _imported_project_modules(path):
        parts = dotted.split(".")
        if len(parts) >= 2 and parts[0] == ROOT_PACKAGE:
            layers.add(parts[1])
    return layers


def _imported_units(path: pathlib.Path) -> set[str]:
    """本文件 import 的 **harness 内部单元**集合。"""
    units: set[str] = set()
    for dotted in _imported_project_modules(path):
        parts = dotted.split(".")
        if len(parts) >= 3 and parts[0] == ROOT_PACKAGE and parts[1] == HARNESS_LAYER:
            units.add(parts[2])
    return units


def _annotated_expressions(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.expr]:
    """函数的参数与返回注记（用于档位轴守卫）。"""
    args = node.args
    parameters = [
        arg
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg)
        if arg is not None and arg.annotation is not None
    ]
    expressions = [arg.annotation for arg in parameters if arg.annotation is not None]
    if node.returns is not None:
        expressions.append(node.returns)
    return expressions


# ---------------------------------------------------------------------------
# H1：harness 不依赖 L2/L4 的实现模块（契约 §3.2）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_harness_does_not_import_l2_l4_implementations() -> None:
    """``harness/**`` 只能通过构造注入的 Protocol 到达 L2/L4，**不得** import 其实现模块。

    这条比对 ``architecture.md`` §2.3 的层白名单**更严**（白名单是"允许"而不是"必须"）：
    它让"只读契约写 fake 跑单测"（``ADR-0015`` §7.3）成立，并挡住
    "顺手 ``from ...tools.files import ReadFileTool``"这类把 L3 与 L2 焊死的改动（H1）。
    """
    offenders = [
        f"{path.relative_to(SRC_ROOT)} -> {layer}"
        for path in _harness_files()
        for layer in sorted(_imported_layers(path) & FORBIDDEN_LAYERS)
    ]

    assert offenders == [], f"harness 依赖了 L2/L4 的实现模块（H1）：{offenders}"


# ---------------------------------------------------------------------------
# H2：叶子模块之间零依赖（契约 §3.2）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_landed_leaf_modules_are_all_present() -> None:
    """三个叶子模块必须存在——否则下面那条检查会**空集通过**，退化成"没有检查"。"""
    missing = [name for name in LANDED_LEAF_FILES if not (HARNESS_ROOT / name).is_file()]

    assert missing == [], f"harness/ 缺少已落地的叶子模块：{missing}"


@pytest.mark.unit
def test_leaf_modules_have_no_mutual_dependencies() -> None:
    """``prompts`` / ``trimming`` / ``context`` / ``checkpoint`` / ``domain_pack`` / ``errors``
    两两**互不 import**（数据由 ``session`` 取出后以参数传入）。

    harness 内部若长成一张网，8 件模块的并行开工立刻退化为串行（契约 §3.2 的 H2）。
    """
    offenders: list[str] = []
    for path in _harness_files():
        own = _unit_of(path)
        if own not in LEAF_UNITS:
            continue
        offenders.extend(
            f"{path.relative_to(SRC_ROOT)} -> {other}"
            for other in sorted(_imported_units(path) & (LEAF_UNITS - {own}))
        )

    assert offenders == [], f"叶子模块之间出现了依赖（H2）：{offenders}"


@pytest.mark.unit
def test_loop_does_not_import_session_or_domain_pack() -> None:
    """``loop`` 不 import ``session`` 也不 import ``domain_pack``（契约 §3.2 的 H2）。

    ``loop.py`` 尚未落地时本检查不生效——这是**已知**的空集，不是"已通过"；
    因此它与 :func:`test_the_landed_leaf_modules_are_all_present` 成对阅读。
    """
    offenders: list[str] = []
    for path in _harness_files():
        own = _unit_of(path)
        forbidden = {target for source, target in FORBIDDEN_INTERNAL_IMPORTS if source == own}
        offenders.extend(
            f"{path.relative_to(SRC_ROOT)} -> {other}"
            for other in sorted(_imported_units(path) & forbidden)
        )

    assert offenders == [], f"loop 依赖了被禁止的兄弟模块（H2）：{offenders}"


# ---------------------------------------------------------------------------
# 档位轴守卫：不得回退到硬件档位轴（契约 §7.1 第 1、2 项 / R-1 的裁决）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_capability_tier_axis_is_not_replaced_by_the_hardware_axis() -> None:
    """``prompts`` / ``trimming`` 的公开函数签名里**不出现** ``HardwareTier``。

    ``CapabilityTier``（模型能力）与 ``HardwareTier``（硬件）是两条正交的轴，
    契约明令不得互相转换；把注记改回硬件档位是本轮收口**唯一**要防的回归方向
    （``R-1``），所以它必须是机器检查而不是注释。
    """
    offenders: list[str] = []
    for filename in AXIS_MODULES:
        path = HARNESS_ROOT / filename
        assert path.is_file(), f"{path} 不存在：本守卫的前提被破坏"

        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if node.name.startswith("_"):
                continue
            for annotation in _annotated_expressions(node):
                if FORBIDDEN_AXIS_NAME in ast.unparse(annotation):
                    offenders.append(f"{filename}::{node.name}")

    assert offenders == [], (
        f"公开函数签名用了硬件档位轴（应为 CapabilityTier，契约 §3.1 / R-1）：{offenders}"
    )


@pytest.mark.unit
def test_the_axis_guard_can_actually_fire() -> None:
    """元测试：守卫对"注记里出现 ``HardwareTier``"是**可触发**的。

    没有这一条，上面那条断言在"扫描器写错了（例如 walk 没生效、注记没被读到）"时
    会静默通过——这正是"声称的缓解必须能被实测"（``Makefile`` 对 ``LOCAL_HOOKS``
    的同一条教训）。
    """
    sample = ast.parse("def select_tools(specs, *, tier: HardwareTier) -> tuple[int, ...]: ...\n")
    functions = [node for node in ast.walk(sample) if isinstance(node, ast.FunctionDef)]

    assert len(functions) == 1
    annotations = _annotated_expressions(functions[0])

    assert any(FORBIDDEN_AXIS_NAME in ast.unparse(item) for item in annotations)
