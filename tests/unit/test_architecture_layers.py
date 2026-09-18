"""架构约束：分层与依赖方向只能单向。

ADR-0015 §7.1 把"分层共识"落成**机器检查**（V1~V6），对抗"约定只写在文档里"
这一失败模式（devlog 0013 §6 的教训）。本文件用 ``ast`` 扫描 ``src/``：

* **V1 / R3**：``contracts/`` 只依赖标准库与自身；
* **V2 / R4**：``subprocess`` 只出现在 ``foundation/proc.py``，全仓无 ``shell=True``；
* **V3 / R2**：``security/`` 与 ``observability/`` 不 import 业务层；
* **V4 / R1**：``foundation/`` 不 import 任何业务包；
* **V5 / D-4**：``src/`` 下无 ``print(``（AST 判定）；
* **V6 / R4**：``bench/`` 不再保留 ``proc.py`` / ``paths.py`` / ``errors.py`` 的独立实现。

另有两条 R4 的补充检查（唯一路径校验入口、逐层依赖白名单）。

> V2 与 V5 在 ``test_bench_encapsulation.py`` 中亦有等价断言——该测试是子进程封装
> 约束的历史落点，二者**刻意保留**为交叉防护；本文件额外把封装层的判定从"文件名"
> 升级为"``foundation/`` 下的相对路径"（ADR-0015 §7.1），防止同名文件绕过。
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

SRC_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src"
PACKAGE_ROOT = SRC_ROOT / "agent_sec_perf"
ROOT_PACKAGE = "agent_sec_perf"

ENCAPSULATION_MODULE = pathlib.Path("agent_sec_perf") / "foundation" / "proc.py"
PATHS_MODULE = pathlib.Path("agent_sec_perf") / "foundation" / "paths.py"
PROMOTED_MODULES = ("proc.py", "paths.py", "errors.py")

CROSS_CUTTING_LAYERS = frozenset({"security", "observability"})
BUSINESS_LAYERS = frozenset(
    {"cli", "harness", "model", "tools", "security", "observability", "bench"}
)

#: 每个层**允许**依赖的兄弟层（自身与标准库总是允许）。
#: 依据 ADR-0015 §5.1.1 的依赖图与 §7.1 的 R1~R5；未列出的目标一律视为违规。
ALLOWED_DEPENDENCIES: dict[str, frozenset[str]] = {
    "contracts": frozenset(),
    "foundation": frozenset({"contracts"}),
    "security": frozenset({"contracts", "foundation"}),
    "observability": frozenset({"contracts", "foundation"}),
    "model": frozenset({"contracts", "foundation", "security", "observability"}),
    "tools": frozenset({"contracts", "foundation", "security", "observability"}),
    "harness": frozenset(
        {"contracts", "foundation", "model", "tools", "security", "observability"}
    ),
    "cli": frozenset(
        {"contracts", "foundation", "model", "tools", "harness", "security", "observability"}
    ),
    # bench 是评测子系统，与产品的唯一接缝是共享 foundation（ADR-0015 §5.4.1）。
    "bench": frozenset({"contracts", "foundation"}),
}


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _source_files() -> list[pathlib.Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse(path: pathlib.Path) -> ast.AST:
    return ast.parse(_read(path), filename=str(path))


def _module_parts(path: pathlib.Path) -> tuple[str, ...]:
    """文件相对 ``agent_sec_perf`` 的模块分量（``__init__`` 已折叠）。"""
    parts = list(path.relative_to(PACKAGE_ROOT).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return tuple(parts)


def _resolve_import_from(node: ast.ImportFrom, module: tuple[str, ...]) -> str | None:
    """把 ``from ... import ...`` 解析为绝对模块名；非本项目模块返回 ``None``。"""
    if node.level == 0:
        if node.module is None:
            return None
        is_project = node.module == ROOT_PACKAGE or node.module.startswith(f"{ROOT_PACKAGE}.")
        return node.module if is_project else None
    package = module[:-1]
    if node.level - 1 > len(package):
        return None
    base = package[: len(package) - (node.level - 1)]
    parts = (*base, *node.module.split(".")) if node.module else base
    return ".".join((ROOT_PACKAGE, *parts))


def _imported_project_modules(path: pathlib.Path) -> set[str]:
    """本文件导入的**本项目**模块名（绝对形式）。"""
    module = _module_parts(path)
    found: set[str] = set()
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            found.update(
                alias.name
                for alias in node.names
                if alias.name == ROOT_PACKAGE or alias.name.startswith(f"{ROOT_PACKAGE}.")
            )
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_import_from(node, module)
            if resolved is not None:
                found.add(resolved)
    return found


def _layer_of(dotted: str) -> str | None:
    """绝对模块名所属的层；根包本身或非本项目返回 ``None``。"""
    parts = dotted.split(".")
    if len(parts) < 2 or parts[0] != ROOT_PACKAGE:
        return None
    return parts[1]


def _imported_layers(path: pathlib.Path) -> set[str]:
    layers: set[str] = set()
    for dotted in _imported_project_modules(path):
        layer = _layer_of(dotted)
        if layer is not None:
            layers.add(layer)
    return layers


def _files_in_layer(layer: str) -> list[pathlib.Path]:
    return sorted((PACKAGE_ROOT / layer).rglob("*.py"))


def _imported_top_level_names(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def _third_party_imports(path: pathlib.Path) -> set[str]:
    return {
        name
        for name in _imported_top_level_names(path)
        if name != ROOT_PACKAGE and name not in sys.stdlib_module_names
    }


def _defines_function(path: pathlib.Path, name: str) -> bool:
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        for node in ast.walk(_parse(path))
    )


def _has_print_call(text: str) -> bool:
    """源码里是否存在真正的 ``print(...)`` 调用（AST 判定，避免误伤字符串）。"""
    return any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"
        for node in ast.walk(ast.parse(text))
    )


# ---------------------------------------------------------------------------
# V1 / R3：契约层只依赖标准库与自身
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_contracts_depend_only_on_stdlib_and_itself() -> None:
    """``contracts/`` 不得依赖第三方包，也不得依赖本项目其它模块。"""
    offenders: list[str] = []
    for path in _files_in_layer("contracts"):
        rel = path.relative_to(SRC_ROOT)
        offenders.extend(f"{rel} -> {layer}" for layer in _imported_layers(path) - {"contracts"})
        offenders.extend(f"{rel} -> {name}（第三方）" for name in _third_party_imports(path))

    assert offenders == [], f"contracts/ 违反 R3（仅标准库与本包内）：{offenders}"


# ---------------------------------------------------------------------------
# R1 / R2：逐层依赖白名单
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_layers_only_depend_on_allowed_layers() -> None:
    """任何层的 import 目标都必须落在 ADR-0015 §5.1.1 的依赖白名单内。"""
    offenders: list[str] = []
    for path in _source_files():
        parts = _module_parts(path)
        if not parts:
            continue
        allowed = ALLOWED_DEPENDENCIES.get(parts[0])
        if allowed is None:
            continue
        for imported in _imported_layers(path):
            if imported != parts[0] and imported not in allowed:
                offenders.append(f"{path.relative_to(SRC_ROOT)} -> {imported}")

    assert offenders == [], f"以下 import 违反分层依赖方向（R1/R2）：{offenders}"


@pytest.mark.unit
def test_cross_cutting_layers_do_not_import_business_layers() -> None:
    """``security/`` 与 ``observability/`` 不得反向依赖业务层（R2）。"""
    forbidden = frozenset({"harness", "tools", "model", "cli"})
    offenders: list[str] = []
    for own_layer in sorted(CROSS_CUTTING_LAYERS):
        for path in _files_in_layer(own_layer):
            for imported in sorted(_imported_layers(path) & forbidden):
                offenders.append(f"{path.relative_to(SRC_ROOT)} -> {imported}")

    assert offenders == [], f"横切层反向依赖业务层（R2）：{offenders}"


@pytest.mark.unit
def test_foundation_does_not_import_business_layers() -> None:
    """``foundation/`` 只允许 ``contracts`` 与标准库，不得 import 业务包（R1）。"""
    offenders = [
        f"{path.relative_to(SRC_ROOT)} -> {layer}"
        for path in _files_in_layer("foundation")
        for layer in _imported_layers(path) & BUSINESS_LAYERS
    ]

    assert offenders == [], f"foundation/ 依赖了业务包（R1）：{offenders}"


# ---------------------------------------------------------------------------
# V2 / R4：唯一子进程入口
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_subprocess_is_used_only_in_foundation_proc() -> None:
    """``subprocess`` 字样只允许出现在 ``foundation/proc.py``。"""
    offenders = [
        str(path.relative_to(SRC_ROOT))
        for path in _source_files()
        if "subprocess" in _read(path) and path.relative_to(SRC_ROOT) != ENCAPSULATION_MODULE
    ]

    assert offenders == [], f"以下模块绕过了子进程封装层：{offenders}"


@pytest.mark.unit
def test_no_shell_execution_anywhere_in_source() -> None:
    """任何地方都不得用 shell 执行命令。"""
    offenders = [
        str(path.relative_to(SRC_ROOT)) for path in _source_files() if "shell=True" in _read(path)
    ]

    assert offenders == [], f"以下模块使用了 shell 执行：{offenders}"


@pytest.mark.unit
def test_path_validation_is_defined_only_in_foundation_paths() -> None:
    """路径白名单校验入口 ``resolve_within`` 只能定义在 ``foundation/paths.py``（R4）。"""
    offenders = [
        str(path.relative_to(SRC_ROOT))
        for path in _source_files()
        if path.relative_to(SRC_ROOT) != PATHS_MODULE and _defines_function(path, "resolve_within")
    ]

    assert offenders == [], f"以下模块重复定义了路径校验入口：{offenders}"


# ---------------------------------------------------------------------------
# V5 / D-4：源码不得残留 print
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_source_has_no_print_calls() -> None:
    """``src/`` 下不得残留 ``print(``（日志一律走 logging）。"""
    offenders = [
        str(path.relative_to(SRC_ROOT)) for path in _source_files() if _has_print_call(_read(path))
    ]

    assert offenders == [], f"以下模块使用了 print：{offenders}"


# ---------------------------------------------------------------------------
# V6 / R4：bench 不再保留地基模块的独立实现
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_bench_has_no_independent_foundation_modules() -> None:
    """提升完成后，``bench/`` 不得再保留 ``proc.py`` / ``paths.py`` / ``errors.py``。"""
    residual = [name for name in PROMOTED_MODULES if (PACKAGE_ROOT / "bench" / name).is_file()]
    missing = [
        name for name in PROMOTED_MODULES if not (PACKAGE_ROOT / "foundation" / name).is_file()
    ]

    assert residual == [], f"bench/ 仍保留已提升模块的独立实现：{residual}"
    assert missing == [], f"foundation/ 缺少被提升的模块：{missing}"
