# W1 harness 逐字附录

> 本文件**逐字收录** 2026-09-15 W1 验证实际运行的脚本与输入夹具，供复现与审计。
> 内容**未经任何改写**——包括与仓库 lint 规则不符的写法（如 `print`、直接
> `urllib.request.urlopen`）。原因：它们是「实际运行过的证据」，而不是需要长期维护的代码；
> 为过 lint 而改写证据会破坏归档的完整性。

关联：[研究笔记](../../2026-09-15-w1-4b-model-capability.md)、
[开发日志 0007](../../../devlog/0007-2026-09-15-开发环境验证与W1风险验证.md)。

---

## 归档约定（重要）

| 内容类型 | 承载格式 | 是否受格式化约束 |
| --- | --- | --- |
| **作者撰写**的文档 | `.md` | 是（`ruff format` 会格式化 Markdown 内嵌的 Python 代码块） |
| **采集得到**的原始产物 | `.txt` | 否——逐字保留，不得被工具改写 |

因此 `out_*.txt` 用 `.txt` 而非 `.md`：它们是模型输出，不是我们撰写的文档。
本文件内嵌的脚本同样属于「采集得到的证据」，故用**四反引号 + `text` 标签**围栏，
以避免被 Markdown 格式化器改写（脚本内含三反引号，用三反引号围栏会误闭合）。

---

## 目录与运行顺序

| 文件 | 角色 |
| --- | --- |
| `t1_input.py` | T1 任务输入（修正递归缺陷后的版本） |
| `t1_input.run1.py` | T1 首轮输入（**含递归缺陷**，见研究笔记 §4.3） |
| `t2_bug.py` | T2 含缺陷模块 |
| `t2_test.py` | T2 失败测试 |
| `t3_pure.py` | T3 纯函数模块 |
| `run_w1.py` | 采集模型输出（HTTP 调用 llama-server） |
| `eval_w1.py` | 客观判定（AST / mypy / pytest / 覆盖率 / 变异测试） |

````text
1) 放置夹具 + 启动 llama-server（命令见研究笔记 §2）
2) python run_w1.py        # 产出 out_t1.txt / out_t2.txt / out_t3.txt / summary.json
3) python eval_w1.py       # 产出 eval.json
````

---

## 1. `t1_input.py`

t1_input.py（修正后，实际用于 T1 第二轮）

````text
"""Sensor report helpers used by the device pipeline."""

SCALE_FACTORS = {"celsius": 1.0, "fahrenheit": 1.8, "kelvin": 1.0}


def convert_temperature(value, unit, target="celsius"):
    """Convert a temperature reading between supported units."""
    if unit not in SCALE_FACTORS or target not in SCALE_FACTORS:
        raise ValueError("unsupported unit")
    if unit == target:
        return value
    if unit == "celsius":
        celsius = value
    elif unit == "fahrenheit":
        celsius = (value - 32) / 1.8
    else:
        celsius = value - 273.15
    if target == "celsius":
        return celsius
    if target == "fahrenheit":
        return celsius * 1.8 + 32
    return celsius + 273.15


def summarize_readings(readings, window=3, drop_zero=True, target="celsius"):
    """Return per-group averages of the most recent sensor readings.

    `readings` is a list of (group, value, unit) triples. Zero readings are
    dropped when `drop_zero` is true, and each value is converted to `target`
    before averaging. Returns a dict mapping group to average value.
    """
    buckets = {}
    for group, value, unit in readings:
        if drop_zero and value == 0:
            continue
        converted = convert_temperature(value, unit, target)
        buckets.setdefault(group, []).append(converted)

    result = {}
    for group, values in buckets.items():
        recent = values[-window:]
        result[group] = round(sum(recent) / len(recent), 4)
    return result
````

## 2. `t1_input.run1.py`

t1_input.run1.py（首轮夹具，含递归缺陷；由 out_t1_run1.txt 还原）

````text
"""Sensor report helpers used by the device pipeline."""

SCALE_FACTORS = {"celsius": 1.0, "fahrenheit": 1.8, "kelvin": 1.0}


def convert_temperature(value, unit, target="celsius"):
    """Convert a temperature reading between supported units."""
    if unit not in SCALE_FACTORS or target not in SCALE_FACTORS:
        raise ValueError("unsupported unit")
    if unit == target:
        return value
    if unit == "celsius" and target == "fahrenheit":
        return value * 1.8 + 32
    if unit == "fahrenheit" and target == "celsius":
        return (value - 32) / 1.8
    if target == "kelvin":
        celsius = convert_temperature(value, unit, "celsius")
        return celsius + 273.15
    return convert_temperature(value, "kelvin", "celsius") - 273.15


def summarize_readings(readings, window=3, drop_zero=True, target="celsius"):
    """Return per-group averages of the most recent sensor readings.

    `readings` is a list of (group, value, unit) triples. Zero readings are
    dropped when `drop_zero` is true, and each value is converted to `target`
    before averaging. Returns a dict mapping group to average value.
    """
    buckets = {}
    for group, value, unit in readings:
        if drop_zero and value == 0:
            continue
        converted = convert_temperature(value, unit, target)
        buckets.setdefault(group, []).append(converted)

    result = {}
    for group, values in buckets.items():
        recent = values[-window:]
        result[group] = round(sum(recent) / len(recent), 4)
    return result
````

## 3. `t2_bug.py`

t2_bug.py

````text
"""Pagination helper (contains a bug that the failing tests expose)."""


def chunk(items, size):
    """Split `items` into consecutive chunks of at most `size` elements."""
    if size <= 0:
        raise ValueError("size must be positive")
    chunks = []
    for start in range(0, len(items) - size, size):
        chunks.append(items[start:start + size])
    return chunks
````

## 4. `t2_test.py`

t2_test.py

````text
import pytest

from t2_bug import chunk


def test_exact_multiple():
    assert chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]


def test_with_remainder():
    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_single_chunk_when_size_exceeds_length():
    assert chunk([1, 2], 5) == [[1, 2]]


def test_empty_input():
    assert chunk([], 3) == []


def test_invalid_size():
    with pytest.raises(ValueError):
        chunk([1, 2], 0)
````

## 5. `t3_pure.py`

t3_pure.py

````text
"""Pure interval-merging helper."""


def merge_intervals(intervals):
    """Merge overlapping closed integer intervals.

    Each interval is a (start, end) tuple with start <= end. Two intervals
    merge when they overlap (share at least one endpoint). Returns a new list
    of disjoint intervals sorted by start, as tuples; the input is not mutated.
    """
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [list(ordered[0])]
    for start, end in ordered[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [tuple(interval) for interval in merged]
````

## 6. `run_w1.py`

run_w1.py

````text
"""W1 risk validation harness: drives llama-server over HTTP and records raw results."""

import json
import pathlib
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8080"
OUT = pathlib.Path("/root/w1")
TEMPERATURE = 0.0
MAX_TOKENS = 2048


def read(name: str) -> str:
    return (OUT / name).read_text(encoding="utf-8")


def wait_ready(timeout_s: int = 300) -> float:
    started = time.time()
    while time.time() - started < timeout_s:
        try:
            with urllib.request.urlopen(f"{BASE}/health", timeout=2) as resp:
                body = json.loads(resp.read())
            if body.get("status") == "ok":
                return time.time() - started
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(0.5)
    raise TimeoutError("server did not become ready")


def chat(prompt: str) -> dict:
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "stream": False,
    }
    request = urllib.request.Request(
        f"{BASE}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.time()
    with urllib.request.urlopen(request, timeout=1800) as resp:
        body = json.loads(resp.read())
    elapsed = time.time() - started
    usage = body.get("usage", {})
    completion = usage.get("completion_tokens", 0)
    return {
        "elapsed_s": round(elapsed, 2),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": completion,
        "tok_per_s": round(completion / elapsed, 2) if elapsed and completion else None,
        "content": body["choices"][0]["message"]["content"],
    }


TASKS = {
    "t1": (
        "以下是一个 Python 模块的完整内容。请为其中所有函数补全完整的类型标注"
        "（参数与返回值），保持行为完全不变。只输出修改后的完整文件内容，"
        "放在一个 ```python 代码块中，不要任何解释。\n\n"
        "```python\n" + read("t1_input.py") + "```\n"
    ),
    "t2": (
        "以下是一个 Python 模块与它的测试文件。测试当前是失败的。"
        "请修复模块中的缺陷，使全部测试通过。只输出修复后的完整模块内容，"
        "放在一个 ```python 代码块中，不要任何解释。\n\n"
        "模块 t2_bug.py：\n```python\n" + read("t2_bug.py") + "```\n\n"
        "测试 t2_test.py：\n```python\n" + read("t2_test.py") + "```\n\n"
        "运行 `pytest t2_test.py` 的失败摘要：\n"
        "```\n"
        "FAILED t2_test.py::test_exact_multiple - assert [[1, 2]] == [[1, 2], [3, 4]]\n"
        "FAILED t2_test.py::test_with_remainder - assert [[1, 2], [3, 4]] == [[1, 2], [3, 4], [5]]\n"
        "FAILED t2_test.py::test_single_chunk_when_size_exceeds_length - assert [] == [[1, 2]]\n"
        "3 failed, 2 passed\n"
        "```\n"
    ),
    "t3": (
        "以下是一个纯函数模块的完整内容。请为其中的函数生成完整的 pytest 单元测试，"
        "覆盖正常情况与边界情况。只输出测试文件内容，放在一个 ```python 代码块中，"
        "不要任何解释。\n\n"
        "```python\n" + read("t3_pure.py") + "```\n"
    ),
}


def main() -> None:
    import sys

    wanted = set(sys.argv[1:]) or set(TASKS)
    load_or_ready = wait_ready()
    print(f"[ready] server healthy after {load_or_ready:.1f}s (since harness start)")
    summary = []
    for name, prompt in TASKS.items():
        if name not in wanted:
            continue
        result = chat(prompt)
        (OUT / f"out_{name}.md").write_text(result["content"], encoding="utf-8")
        summary.append(
            {
                "task": name,
                "elapsed_s": result["elapsed_s"],
                "prompt_tokens": result["prompt_tokens"],
                "completion_tokens": result["completion_tokens"],
                "tok_per_s": result["tok_per_s"],
            }
        )
        print(f"[{name}] {result['elapsed_s']}s, "
              f"prompt={result['prompt_tokens']}tok, out={result['completion_tokens']}tok, "
              f"{result['tok_per_s']} tok/s")
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
````

## 7. `eval_w1.py`

eval_w1.py

````text
"""Evaluate the raw W1 outputs objectively: extract code blocks and run real tools."""

import ast
import pathlib
import re
import shutil
import subprocess

W1 = pathlib.Path("/root/w1")
PY = "/workspace/.venv/bin/python"
CODEFENCE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def extract_code(name: str) -> tuple[str, bool]:
    """Return (code, had_fence) from out_<name>.md."""
    raw = (W1 / f"out_{name}.md").read_text(encoding="utf-8")
    blocks = CODEFENCE.findall(raw)
    if blocks:
        return max(blocks, key=len), True
    return raw, False


def run(cmd: list[str], cwd: pathlib.Path) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def check_t1() -> dict:
    code, fenced = extract_code("t1")
    path = W1 / "t1_fixed.py"
    path.write_text(code, encoding="utf-8")
    status: dict = {"fenced": fenced}
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return {**status, "parse": f"SyntaxError: {exc}"}
    status["parse"] = "ok"
    missing = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            args = [a for a in node.args.args if a.arg not in ("self", "cls")]
            untyped = [a.arg for a in args if a.annotation is None]
            if untyped or node.returns is None:
                missing.append(node.name)
    status["missing_annotations"] = missing
    rc, out = run([PY, "-m", "mypy", "--strict", "--no-incremental", str(path)], W1)
    status["mypy_rc"] = rc
    status["mypy"] = out.splitlines()[-1] if out else ""
    # behavior unchanged?
    shutil.copy(W1 / "t1_input.py", W1 / "t1_orig.py")
    rc2, out2 = run([PY, "-c",
                     "import t1_orig as a, t1_fixed as b;"
                     "r=[('a',5,'celsius'),('a',0,'celsius'),('b',300,'kelvin'),"
                     "('a',68,'fahrenheit'),('b',1,'celsius')];"
                     "assert a.summarize_readings(r)==b.summarize_readings(r), 'DIFF';"
                     "assert a.summarize_readings(r,target='kelvin')==b.summarize_readings(r,target='kelvin');"
                     "print('behavior-same')"], W1)
    status["behavior_rc"] = rc2
    status["behavior"] = out2.splitlines()[-1] if out2 else ""
    return status


def check_t2() -> dict:
    code, fenced = extract_code("t2")
    work = W1 / "eval_t2"
    work.mkdir(exist_ok=True)
    (work / "t2_bug.py").write_text(code, encoding="utf-8")
    shutil.copy(W1 / "t2_test.py", work / "t2_test.py")
    rc, out = run([PY, "-m", "pytest", "t2_test.py", "-q", "-p", "no:cacheprovider"], work)
    return {"fenced": fenced, "pytest_rc": rc, "pytest": out.splitlines()[-1] if out else ""}


def check_t3() -> dict:
    code, fenced = extract_code("t3")
    work = W1 / "eval_t3"
    work.mkdir(exist_ok=True)
    shutil.copy(W1 / "t3_pure.py", work / "t3_pure.py")
    (work / "test_generated.py").write_text(code, encoding="utf-8")
    rc, out = run([PY, "-m", "pytest", "test_generated.py", "-q", "-p", "no:cacheprovider"], work)
    result = {"fenced": fenced, "pytest_rc": rc, "pytest": out.splitlines()[-1] if out else ""}
    # Delivered as-is failed. Separate "missing import" from "wrong assertions".
    has_import = bool(re.search(r"^\s*(from\s+\S+\s+)?import\s+.*merge_intervals", code, re.M))
    injected = "from t3_pure import merge_intervals\n\n" + code
    (work / "test_injected.py").write_text(injected, encoding="utf-8")
    rc3, out3 = run([PY, "-m", "pytest", "test_injected.py", "-q", "-p", "no:cacheprovider"], work)
    result["delivered_importable"] = bool(has_import)
    result["assertions_rc_with_import"] = rc3
    result["assertions"] = out3.splitlines()[-1] if out3 else ""
    rc2, out2 = run([PY, "-m", "pytest", "test_generated.py", "-q", "-p", "no:cacheprovider",
                     "--cov=t3_pure", "--cov-branch", "--cov-report=term"], work)
    match = re.search(r"t3_pure\.py\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)%", out2)
    result["coverage"] = match.group(5) + "%" if match else "n/a"
    result["passed"] = rc == 0
    return result


def check_t3_negative() -> dict:
    """Do the generated tests actually fail on a mutated implementation?"""
    work = W1 / "eval_t3_mut"
    work.mkdir(exist_ok=True)
    src = (W1 / "t3_pure.py").read_text(encoding="utf-8")
    mutated = src.replace("if start <= merged[-1][1]:", "if start < merged[-1][1]:")
    (work / "t3_pure.py").write_text(mutated, encoding="utf-8")
    code, _ = extract_code("t3")
    (work / "test_generated.py").write_text(code, encoding="utf-8")
    rc, out = run([PY, "-m", "pytest", "test_generated.py", "-q", "-p", "no:cacheprovider"], work)
    return {"killed_mutant": rc != 0, "detail": out.splitlines()[-1] if out else ""}


if __name__ == "__main__":
    import json

    report = {
        "T1": check_t1(),
        "T2": check_t2(),
        "T3": check_t3(),
        "T3_mutation": check_t3_negative(),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    (W1 / "eval.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
````
