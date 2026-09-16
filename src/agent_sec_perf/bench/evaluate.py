"""模型产物的客观判定。

判定方式与 W1 / 三档对比实验同源（AST 标注完备性、``mypy --strict``、行为等价、
``pytest``、变异测试），但补上了两条**判据修正**：

1. **不看退出码，看结构化结果**：``pytest`` 退出码是复合信号——"测试根本没跑起来"
   也会非零。因此解析 ``passed/failed/error`` 与 ``no tests ran``，变异测试要求
   "至少一个断言失败"才算杀死变异体（``docs/notes/evaluation-pitfalls.md`` 情形二）。
2. **变异点必须命中**：夹具一旦变化导致替换落空，直接抛错而不是把"没变异"当成
   "杀不死变异体"——那会把夹具变更伪装成模型能力下降。

**不可信边界**：本模块执行的是**模型生成**的代码（``pytest`` 会真的 import 并运行它），
因此执行型调用一律走 :func:`agent_sec_perf.bench.proc.run` 的隔离路径；
只有不执行被测代码的静态检查（``mypy``）走普通路径。
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys
from dataclasses import dataclass

from agent_sec_perf.bench import proc
from agent_sec_perf.bench.errors import ProtocolError
from agent_sec_perf.bench.paths import make_writable_by_all
from agent_sec_perf.bench.protocol import read_fixture

CODEFENCE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)
_IMPORT_RE = re.compile(r"^\s*(from\s+\S+\s+)?import\s+.*merge_intervals", re.M)
_MUTATION_FROM = "if start <= merged[-1][1]:"
_MUTATION_TO = "if start < merged[-1][1]:"

EXEC_TIMEOUT_S = 120.0
STATIC_TIMEOUT_S = 180.0

_BEHAVIOR_SNIPPET = (
    "import t1_orig as a, t1_fixed as b;"
    "r=[('a',5,'celsius'),('a',0,'celsius'),('b',300,'kelvin'),"
    "('a',68,'fahrenheit'),('b',1,'celsius')];"
    "assert a.summarize_readings(r)==b.summarize_readings(r), 'DIFF';"
    "assert a.summarize_readings(r,target='kelvin')==b.summarize_readings(r,target='kelvin');"
    "print('behavior-same')"
)


@dataclass(frozen=True)
class TaskVerdict:
    """单个任务的判定结果。"""

    task: str
    passed: bool
    detail: dict[str, object]

    def to_json(self) -> dict[str, object]:
        """转为可写入 JSON 的字典。"""
        return {"task": self.task, "passed": self.passed, **self.detail}


def extract_code(text: str) -> tuple[str, bool]:
    """从模型输出中提取代码块（取最长的一段）。

    返回 ``(代码, 是否带围栏)``；不带围栏时返回原文（此时"没按格式输出"本身就是
    一条要记录的信息）。
    """
    blocks = CODEFENCE.findall(text)
    if blocks:
        return max(blocks, key=len), True
    return text, False


def parse_pytest_counts(output: str) -> dict[str, int]:
    """从 pytest 输出中解析结构化结果。

    这是"退出码不可信"的落地：只有拿到 passed/failed/error 与 ``no tests ran``
    才能区分"测试失败"与"测试没跑"。
    """
    counts = {"passed": 0, "failed": 0, "errors": 0}
    for key, pattern in (
        ("passed", r"(\d+) passed"),
        ("failed", r"(\d+) failed"),
        ("errors", r"(\d+) error"),
    ):
        match = re.search(pattern, output)
        if match is not None:
            counts[key] = int(match.group(1))
    counts["no_tests"] = 1 if "no tests ran" in output else 0
    return counts


def _missing_annotations(tree: ast.AST) -> list[str]:
    """列出缺少参数或返回值标注的函数名。"""
    missing: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            args = [arg for arg in node.args.args if arg.arg not in ("self", "cls")]
            if any(arg.annotation is None for arg in args) or node.returns is None:
                missing.append(node.name)
    return missing


def _tail(text: str, lines: int = 3) -> str:
    """取输出末尾若干行（完整输出太长，且日志里会重复）。"""
    return "\n".join(text.strip().splitlines()[-lines:])


class Evaluator:
    """按固定口径判定模型产物。"""

    def __init__(self, *, root: pathlib.Path, isolation: str) -> None:
        self._root = root
        self._isolation = isolation
        self._python = sys.executable

    def evaluate(self, task: str, content: str, *, tag: str) -> TaskVerdict:
        """判定一个任务产物。

        Args:
            task: ``t1`` / ``t2`` / ``t3``。
            content: 模型输出原文。
            tag: 本次判定的独立标签（如 ``S_r03_t1``），用于隔离工作目录。

        Raises:
            ProtocolError: 任务名未知。
        """
        workdir = self._prepare(tag)
        if task == "t1":
            return self._evaluate_t1(content, workdir)
        if task == "t2":
            return self._evaluate_t2(content, workdir)
        if task == "t3":
            return self._evaluate_t3(content, workdir)
        msg = f"未知任务：{task}"
        raise ProtocolError(msg)

    # -- 基础设施 ---------------------------------------------------------

    def _prepare(self, tag: str) -> pathlib.Path:
        """创建（或复用）本次判定的工作目录，并保证非特权用户可进入。"""
        if not all(char.isalnum() or char in "-_" for char in tag):
            msg = f"非法的工作目录标签：{tag!r}"
            raise ProtocolError(msg)
        workdir = self._root / tag
        make_writable_by_all(workdir)
        return workdir

    def _write(self, path: pathlib.Path, text: str) -> None:
        """写入待判定文件，并放开读权限（隔离执行时需要）。"""
        path.write_text(text, encoding="utf-8")
        path.chmod(0o644)

    def _pytest(self, workdir: pathlib.Path, target: str, *extra: str) -> proc.CommandResult:
        """执行 pytest（隔离路径：它会 import 并运行模型生成的代码）。"""
        return proc.run(
            [self._python, "-m", "pytest", target, "-q", "-p", "no:cacheprovider", *extra],
            cwd=workdir,
            timeout_s=EXEC_TIMEOUT_S,
            isolation=self._isolation,
        )

    # -- 各任务 -----------------------------------------------------------

    def _evaluate_t1(self, content: str, workdir: pathlib.Path) -> TaskVerdict:
        """类型标注任务：AST 完备性 + ``mypy --strict`` + 行为等价。"""
        code, fenced = extract_code(content)
        target = workdir / "t1_fixed.py"
        self._write(target, code)
        detail: dict[str, object] = {"fenced": fenced}
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            detail.update({"parse": f"SyntaxError: {exc}", "behavior_ok": False})
            return TaskVerdict("t1", False, detail)
        missing = _missing_annotations(tree)
        detail["missing_annotations"] = missing

        mypy = proc.run_inherit_env(
            [self._python, "-m", "mypy", "--strict", "--no-incremental", str(target)],
            cwd=workdir,
            timeout_s=STATIC_TIMEOUT_S,
        )
        detail["mypy_rc"] = mypy.returncode
        detail["mypy_tail"] = _tail(mypy.output())

        self._write(workdir / "t1_orig.py", read_fixture("t1_input.py"))
        behavior = proc.run(
            [self._python, "-c", _BEHAVIOR_SNIPPET],
            cwd=workdir,
            timeout_s=EXEC_TIMEOUT_S,
            isolation=self._isolation,
        )
        detail["behavior_rc"] = behavior.returncode
        detail["behavior_ok"] = behavior.returncode == 0
        passed = not missing and mypy.returncode == 0 and behavior.returncode == 0
        return TaskVerdict("t1", passed, detail)

    def _evaluate_t2(self, content: str, workdir: pathlib.Path) -> TaskVerdict:
        """修缺陷任务：把产物当模块跑原始失败测试。"""
        code, fenced = extract_code(content)
        self._write(workdir / "t2_bug.py", code)
        self._write(workdir / "t2_test.py", read_fixture("t2_test.py"))
        result = self._pytest(workdir, "t2_test.py")
        counts = parse_pytest_counts(result.output())
        detail: dict[str, object] = {
            "fenced": fenced,
            "pytest_rc": result.returncode,
            **counts,
            "tail": _tail(result.output()),
        }
        passed = (
            result.returncode == 0
            and counts["failed"] == 0
            and counts["errors"] == 0
            and counts["passed"] > 0
        )
        return TaskVerdict("t2", passed, detail)

    def _evaluate_t3(self, content: str, workdir: pathlib.Path) -> TaskVerdict:
        """生成测试任务：可运行性 + 断言正确性 + 变异检出能力。"""
        code, fenced = extract_code(content)
        self._write(workdir / "t3_pure.py", read_fixture("t3_pure.py"))
        self._write(workdir / "test_gen.py", code)

        delivered = self._pytest(workdir, "test_gen.py")
        delivered_counts = parse_pytest_counts(delivered.output())
        delivered_importable = bool(_IMPORT_RE.search(code))

        detail: dict[str, object] = {
            "fenced": fenced,
            "delivered_importable": delivered_importable,
            "delivered_rc": delivered.returncode,
            "delivered_counts": delivered_counts,
        }

        if delivered_importable:
            assertions_ok = (
                delivered.returncode == 0
                and delivered_counts["failed"] == 0
                and delivered_counts["errors"] == 0
                and delivered_counts["passed"] > 0
            )
            mutation_target = "test_gen.py"
        else:
            # 补上 import 再跑一次：把"缺 import"与"断言写错"分开——两者处理方式不同。
            self._write(
                workdir / "test_inj.py",
                "from t3_pure import merge_intervals\n\n" + code,
            )
            injected = self._pytest(workdir, "test_inj.py")
            injected_counts = parse_pytest_counts(injected.output())
            assertions_ok = (
                injected.returncode == 0
                and injected_counts["failed"] == 0
                and injected_counts["errors"] == 0
                and injected_counts["passed"] > 0
            )
            detail["injected_counts"] = injected_counts
            mutation_target = "test_inj.py"
        detail["assertions_ok"] = assertions_ok

        detail["killed_mutant"] = self._killed_mutant(workdir, code, mutation_target)
        passed = delivered_importable and assertions_ok and bool(detail["killed_mutant"])
        return TaskVerdict("t3", passed, detail)

    def _killed_mutant(self, workdir: pathlib.Path, code: str, mutation_target: str) -> bool:
        """把实现改坏，看测试能否发现。

        判据是"**至少一个断言失败**"，而不是"退出码非零"：后者会把
        "测试根本没跑起来"也记为"有检出力"（本轮 S 档曾因此被误判）。
        """
        original = read_fixture("t3_pure.py")
        mutated = original.replace(_MUTATION_FROM, _MUTATION_TO)
        if mutated == original:
            msg = (
                "变异点未命中：夹具 t3_pure.py 可能已变化。"
                "请在变更夹具的同时提升 PROTOCOL_VERSION 并复核变异点。"
            )
            raise ProtocolError(msg)
        mutdir = workdir / "mut"
        make_writable_by_all(mutdir)
        self._write(mutdir / "t3_pure.py", mutated)
        self._write(mutdir / mutation_target, code)
        result = self._pytest(mutdir, mutation_target)
        counts = parse_pytest_counts(result.output())
        return counts["failed"] >= 1


__all__ = ["Evaluator", "TaskVerdict", "extract_code", "parse_pytest_counts"]
