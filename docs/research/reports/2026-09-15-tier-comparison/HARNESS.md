# S/M/L 三档对比：harness 逐字附录

> **逐字收录**本轮实际运行的测量与判定脚本，供复现。内容**未经任何改写**——
> 包括与仓库 lint 规则不符的写法。理由同 [W1 附录](../2026-09-15-w1/HARNESS.md)：
> 为过 lint 而改写证据会破坏归档的完整性。

关联：[研究笔记](../../2026-09-15-tiers-s-m-l-comparison.md)、
[devlog 0010](../../../devlog/0010-2026-09-15-动态硬件适配与分层Harness.md)。

## 与 W1 harness 的关系

| 文件 | 来源 | 作用 |
| --- | --- | --- |
| `t1_input.py` / `t2_bug.py` / `t2_test.py` / `t3_pure.py` | [W1 附录](../2026-09-15-w1/HARNESS.md) | 两项实验**共用同一组任务输入** |
| `tier_bench.py`（本文件） | 本轮新增 | 启停 llama-server、测加载/内存/吞吐、按档位归档模型输出 |
| `eval_tier.py`（本文件） | 本轮新增 | 按档位做客观判定（AST / mypy / pytest / 覆盖率 / 变异测试） |

> **可比性设计**：三档使用**同一脚本、同一环境、同一启动参数**
> （`-c 4096 -t 8 -tb 8 --reasoning off`），仅模型不同，因此差异可归因于模型。

## 运行方式

````bash
cd /workspace && bash .ide/fetch-assets.sh models /root/models
python /root/w1/tier_bench.py --model /root/models/Qwen3-8B-Q4_K_M.gguf --tier L --ctx 4096 --threads 8
python /root/w1/eval_tier.py --dir /root/w1/reports/L
````

## 已知的判据缺陷（**下一轮必须修**）

1. `eval_tier.py` 的变异测试用 `pytest` 退出码判定「是否杀死变异体」，
   会把「测试根本没跑起来」误判为「具备检出力」（本轮 S 档即如此）。
   修法：读取结构化结果（测试数量、passed/failed/error），而非只看退出码。
2. T3 的提示词措辞会显著影响结果（本轮修正后才产出可运行文件）。
   提示词原文必须随之归档——见研究笔记 §3。

---

## 1. `tier_bench.py`

````text
"""档位统一测量：对任一模型跑同一组任务，产出可比数据（用于降级曲线）。

与 run_w1.py 的区别：本脚本自行启停 llama-server、记录加载耗时与内存峰值，
并把结果按档位归档。三个档位使用**同一套代码与同一组任务**，以保证可比性。

用法：
  python tier_bench.py --model /root/models/Qwen3-8B-Q4_K_M.gguf --tier L --ctx 4096
"""

import argparse
import json
import pathlib
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

SERVER = "llama-server"
BASE = "http://127.0.0.1:8080"
W1 = pathlib.Path("/root/w1")


def read(name: str) -> str:
    return (W1 / name).read_text(encoding="utf-8")


def build_tasks() -> dict:
    return {
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
            "以下是一个纯函数模块。请为其中的函数编写一个**完整、可直接运行**的 pytest 测试文件："
            "必须包含必要的 import 语句（含从该模块导入被测函数），并覆盖正常情况与边界情况。"
            "只输出该文件的完整内容，放在一个 ```python 代码块中，不要任何解释。\n\n"
            "模块名：t3_pure（与测试文件位于同目录，需从中导入被测函数）\n"
            "```python\n" + read("t3_pure.py") + "```\n"
        ),
    }


def wait_ready(deadline_s: int = 900) -> float:
    started = time.time()
    while time.time() - started < deadline_s:
        try:
            with urllib.request.urlopen(f"{BASE}/health", timeout=2) as resp:
                if json.loads(resp.read()).get("status") == "ok":
                    return time.time() - started
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(0.25)
    raise TimeoutError("服务未在预期时间内就绪")


def chat(prompt: str, max_tokens: int = 2048) -> dict:
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "stream": False,
    }
    req = urllib.request.Request(
        f"{BASE}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=1800) as resp:
        body = json.loads(resp.read())
    elapsed = time.time() - started
    usage = body.get("usage", {})
    return {
        "elapsed_s": round(elapsed, 2),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "content": body["choices"][0]["message"]["content"],
    }


def vmhwm_kb(pid: int):
    try:
        for line in pathlib.Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1])
    except OSError:
        return None
    return None


def parse_server_timings(log_path: pathlib.Path) -> dict:
    """从 llama-server 日志提取 prefill 与生成速率（比客户端端到端计时更准）。"""
    text = log_path.read_text(errors="replace") if log_path.exists() else ""
    pre = [float(m.group(1)) for m in re.finditer(r"prompt eval time =.*?([\d.]+) tokens per second", text)]
    # 负向后行断言：排除 "prompt eval time" 行，只取真正的生成计时
    gen = [float(m.group(1)) for m in re.finditer(r"(?<!prompt )eval time =.*?([\d.]+) tokens per second", text)]
    return {"prefill": pre, "gen": gen}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--tier", required=True)
    ap.add_argument("--ctx", type=int, default=4096)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--tasks", default="t1,t2,t3")
    args = ap.parse_args()

    out = pathlib.Path(args.outdir or f"/root/w1/reports/{args.tier}")
    out.mkdir(parents=True, exist_ok=True)
    log = out / "server.log"
    model = pathlib.Path(args.model)
    if not model.exists():
        print(f"[{args.tier}] 模型不存在：{model}")
        return 2

    print(f"[{args.tier}] 模型 {model.name}（{model.stat().st_size / 1e9:.2f} GB）")
    cmd = [
        SERVER, "-m", str(model), "-c", str(args.ctx),
        "-t", str(args.threads), "-tb", str(args.threads),
        "--host", "127.0.0.1", "--port", "8080",
        "--reasoning", "off", "--no-webui", "--metrics",
    ]
    with log.open("w") as fh:
        proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        load_s = wait_ready()
        print(f"[{args.tier}] 加载耗时 {load_s:.2f} s")
        tasks = build_tasks()
        wanted = [t.strip() for t in args.tasks.split(",") if t.strip()]
        rows = []
        for name in wanted:
            r = chat(tasks[name], max_tokens=args.max_tokens)
            (out / f"out_{name}.md").write_text(r["content"], encoding="utf-8")
            rows.append({
                "task": name,
                "elapsed_s": r["elapsed_s"],
                "prompt_tokens": r["prompt_tokens"],
                "completion_tokens": r["completion_tokens"],
            })
            print(f"[{args.tier}] {name}: {r['elapsed_s']}s  prompt={r['prompt_tokens']}  out={r['completion_tokens']}")
        peak = vmhwm_kb(proc.pid)
        t = parse_server_timings(log)
        summary = {
            "tier": args.tier,
            "model": model.name,
            "size_gb": round(model.stat().st_size / 1e9, 2),
            "ctx": args.ctx,
            "threads": args.threads,
            "load_s": round(load_s, 2),
            "vmhwm_gib": round(peak / 1024 / 1024, 2) if peak else None,
            "prefill_tok_per_s": round(sum(t["prefill"]) / len(t["prefill"]), 2) if t["prefill"] else None,
            "gen_tok_per_s": round(sum(t["gen"]) / len(t["gen"]), 2) if t["gen"] else None,
            "tasks": rows,
        }
        (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(
            f"[{args.tier}] 峰值内存 {summary['vmhwm_gib']} GiB  "
            f"prefill {summary['prefill_tok_per_s']} tok/s  生成 {summary['gen_tok_per_s']} tok/s"
        )
        return 0
    finally:
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=30)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
````

## 2. `eval_tier.py`

````text
"""按档位评估模型产物的客观正确性（与 tier_bench.py 配套）。

用法：
  python eval_tier.py --dir /root/w1/reports/L

判定方式与 W1 一致，全部自动化：
  T1  类型标注  → AST 标注完备性 + mypy --strict + 行为等价（对照原始实现）
  T2  修 bug    → 注入修复后的模块后跑原始测试，须全绿
  T3  生成测试  → ① 交付即可运行？② 通过正确实现？③ 分支覆盖率？④ 能否杀死变异体？
"""

import argparse
import ast
import json
import pathlib
import re
import shutil
import subprocess

W1 = pathlib.Path("/root/w1")
PY = "/workspace/.venv/bin/python"
CODEFENCE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def extract_code(text: str) -> tuple:
    blocks = CODEFENCE.findall(text)
    if blocks:
        return max(blocks, key=len), True
    return text, False


def run(cmd, cwd):
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=900)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def read_out(d, name):
    p = d / f"out_{name}.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def check_t1(d, work):
    code, fenced = extract_code(read_out(d, "t1"))
    path = work / "t1_fixed.py"
    path.write_text(code, encoding="utf-8")
    res = {"fenced": fenced}
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        res.update({"parse": f"SyntaxError: {exc}", "pass": False})
        return res
    res["parse"] = "ok"
    missing = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            args = [a for a in node.args.args if a.arg not in ("self", "cls")]
            if [a.arg for a in args if a.annotation is None] or node.returns is None:
                missing.append(node.name)
    res["missing_annotations"] = missing
    rc, out = run([PY, "-m", "mypy", "--strict", "--no-incremental", str(path)], work)
    res["mypy_rc"] = rc
    res["mypy_last"] = out.splitlines()[-1] if out else ""
    shutil.copy(W1 / "t1_input.py", work / "t1_orig.py")
    rc2, out2 = run([PY, "-c",
                     "import t1_orig as a, t1_fixed as b;"
                     "r=[('a',5,'celsius'),('a',0,'celsius'),('b',300,'kelvin'),"
                     "('a',68,'fahrenheit'),('b',1,'celsius')];"
                     "assert a.summarize_readings(r)==b.summarize_readings(r), 'DIFF';"
                     "assert a.summarize_readings(r,target='kelvin')==b.summarize_readings(r,target='kelvin');"
                     "print('behavior-same')"], work)
    res["behavior"] = out2.splitlines()[-1] if out2 else ""
    res["behavior_ok"] = rc2 == 0
    res["pass"] = (not missing) and rc == 0 and rc2 == 0
    return res


def check_t2(d, work):
    code, fenced = extract_code(read_out(d, "t2"))
    (work / "t2_bug.py").write_text(code, encoding="utf-8")
    shutil.copy(W1 / "t2_test.py", work / "t2_test.py")
    rc, out = run([PY, "-m", "pytest", "t2_test.py", "-q", "-p", "no:cacheprovider"], work)
    return {"fenced": fenced, "pytest_rc": rc,
            "pytest_last": out.splitlines()[-1] if out else "", "pass": rc == 0}


def check_t3(d, work):
    code, fenced = extract_code(read_out(d, "t3"))
    shutil.copy(W1 / "t3_pure.py", work / "t3_pure.py")
    (work / "test_gen.py").write_text(code, encoding="utf-8")
    rc, out = run([PY, "-m", "pytest", "test_gen.py", "-q", "-p", "no:cacheprovider"], work)
    res = {"fenced": fenced, "pytest_rc": rc,
           "pytest_last": out.splitlines()[-1] if out else "", "pass": rc == 0}
    has_import = bool(re.search(r"^\s*(from\s+\S+\s+)?import\s+.*merge_intervals", code, re.M))
    res["delivered_importable"] = has_import
    if not has_import:
        (work / "test_inj.py").write_text(
            "from t3_pure import merge_intervals\n\n" + code, encoding="utf-8")
        rc3, out3 = run([PY, "-m", "pytest", "test_inj.py", "-q", "-p", "no:cacheprovider"], work)
        res["assertions_rc_with_import"] = rc3
        res["assertions_last"] = out3.splitlines()[-1] if out3 else ""
        res["assertions_ok"] = rc3 == 0
    # 变异测试：把 >= 重叠判据改坏，测试应当失败（即具备检出力）
    mut = work / "mut"
    mut.mkdir(exist_ok=True)
    src = (W1 / "t3_pure.py").read_text(encoding="utf-8")
    (mut / "t3_pure.py").write_text(
        src.replace("if start <= merged[-1][1]:", "if start < merged[-1][1]:"), encoding="utf-8")
    (mut / "test_gen.py").write_text(code, encoding="utf-8")
    (mut / "test_inj.py").write_text(
        "from t3_pure import merge_intervals\n\n" + code, encoding="utf-8")
    target = "test_gen.py" if has_import else "test_inj.py"
    rc4, _ = run([PY, "-m", "pytest", target, "-q", "-p", "no:cacheprovider"], mut)
    res["killed_mutant"] = rc4 != 0
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    args = ap.parse_args()
    d = pathlib.Path(args.dir)
    work = d / "eval"
    work.mkdir(parents=True, exist_ok=True)

    report = {
        "tier": d.name,
        "T1": check_t1(d, work),
        "T2": check_t2(d, work),
        "T3": check_t3(d, work),
    }
    (d / "eval.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
````

---

## 补充证据（2026-09-15 审计后追加）

以下内容在首次归档时遗漏，经销毁环境前的完整性审计补入。**内容未经任何改写。**

| 文件 | 内容 |
| --- | --- |
| `S/M/L.server.log.txt` | **三档的性能主证据**。研究笔记中的加载耗时、prefill 速率、生成速率均取自这些日志的 `slot print_timing` 行（`prompt eval time` / `eval time`），而非客户端端到端计时 |
| `S_v2/M_v2/L_v2.server.log.txt` | T3 复评轮次（v2）的服务日志 |
| `S_v2/M_v2/L_v2.summary.json` | v2 轮次的采集摘要（首轮 `S/M/L.summary.json` 已在首次归档中） |

> **归一化披露**：`.txt` 与 `.json` 证据文件在归档时统一了行尾空白，并在文件末尾补了换行符
> （原件末尾无换行会触发仓库的 `end-of-file-fixer`）。除此之外**逐字未改**；
> 保真度已用"忽略行尾空白与末尾换行后逐字比较"核验，16 份产物与原件一致。
