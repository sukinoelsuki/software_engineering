"""真模型端到端用例：让"能跑起来"成为**可复现证据**（``tests/integration/``）。

被测对象是**产品自己的路径**：``agent_sec_perf.cli.app`` 的 :class:`RunRequest` +
:func:`~agent_sec_perf.cli.app.execute`（契约 ``docs/design/interfaces/harness.md``
§5.1 的装配清单 / §5.2 的退出码表与输出通道两条硬规定），**不是**直接调
``harness.session.Session``。真实 ``llama-server`` + 真实 GGUF，**全程无替身**：
本阶段环境已实测可用（``docs/devlog/0017`` §2.2）。

覆盖（契约条目逐条对应）：

1. **文本模式**：退出码 ``0``（``TASK_FINISHED.status is COMPLETED``）；事件流里
   **恰好一条** ``TASK_FINISHED`` 且**在最后**（``I6``）；``seq`` 从 ``0`` 连续无空洞
   （``I9``）；审计**真实落盘**且**每行可** ``json.loads``；
2. **JSON 模式**（``--output-format json``）：stdout **只出 JSONL**（逐行 ``json.loads``
   成功、无任何非 JSON 行），且行数与事件数相等（一条不漏、一条不多）；
3. **无凭据泄漏**：事件 / 审计 / 两条输出流里都不出现凭据标记，且子进程环境 canary
   不回流（与 ``tests/security/test_spawn_credentials_canary.py`` 同一取证取向，
   但此处只做**我方通道**的检查，安全断言仍归 ``tests/security/``）。
4. **多步会话**（`M2` 出口判据 `M2-1` / `M2-4` 的载体，2026-09-20 新增）：任务是
   "先列目录、再读列出来的文件"——**第二步的输入来自第一步的输出**，因此"两次调用"
   不是把同一件事说两遍，而是一条真实的 ReAct 链路。断言分两侧：
   **审计侧**（`M2-1`）落盘文件里 ``kind=TOOL_CALL`` 且 ``outcome=OK`` 的事件 **≥ 2 条**，
   且覆盖 **> 1** 个不同工具或不同参数；**回放侧**（`M2-4`）对该会话审计文件里
   **每一条** ``TOOL_CALL`` 事件的 ``event_id`` 调 ``query_by_id`` 从**落盘文件**还原，
   并逐条与事件流对齐（``kind`` / ``call_id`` / ``outcome``）——**不得只验单步**。

**可用性参数（实测依据，2026-09-19，本容器）**：模型日志里 ``tg`` 稳定在 ``3.5 t/s``，
而 ``harness/loop.py`` 调 ``chat`` 时**不传** ``timeout_s`` ⇒ 每次补全都用
``ModelClient`` 协议签名上的默认值 ``60.0`` ⇒ 无上限的补全必然以 ``ModelUnavailableError``
结束（``loop`` 按 ``REQ-HARNESS-06`` 重试到预算耗尽 ⇒ ``TASK_FINISHED(FAILED)``）。
故本模块必须显式给出**有界的完成上限**与**足够的请求超时**（取值依据见
``_MAX_COMPLETION_TOKENS`` / ``_MODEL_REQUEST_TIMEOUT_S`` 的定义处）。
这**不是**为让用例变绿而放宽断言：断言一字未改，改的只是"同一套断言在慢硬件上如何跑完"。

**为什么给会话配一份最小领域包（``_PACK_TOML``）**：本项目的风险声明**只能**来自领域包
（``harness.md`` §4、``REQ-HARNESS-08``）；不配包时 ``PolicyEngine`` 对**所有**工具取
``DEFAULT_TOOL_RISK = HIGH``（``security/policy.py``）⇒ 每次调用都 ``requires_confirmation``，
而本用例的会话是**非交互**的（``interactive=False``）⇒ ``R1`` 一律拒绝 ⇒ 工具**永远执行不了**，
``test_text_mode_executes_a_real_tool_call`` 那条断言**根本无法成立**。因此这里走**产品设计的
正规通路**：一份只读包，把 ``read_file`` / ``list_dir`` 声明为 ``low`` 风险。
**没有**放宽任何校验——风险仍在 ``PolicyEngine`` 里逐次求值，拒绝与审计路径原样保留。

**默认不跑（两道锁，二者都满足才执行）**：

* **锁一（门禁侧）**：``pyproject.toml`` 已注册 ``slow`` marker，且 ``Makefile`` 的
  ``make test`` / ``make test-cov`` 已把 ``slow`` 并入默认排除集（``0b5def7``，F 类变更，
  所有者 2026-09-19 批准）⇒ 默认回归**不会**选中本模块
  （口径同步见 ``docs/engineering/testing-strategy.md`` §8）；
* **锁二（用例侧）**：环境变量开关 ``AGENT_SEC_PERF_E2E=1``，**未设置即 skip**。它拦的正是
  门禁侧拦不住的那条路：显式 ``uv run pytest -m integration``（或直接指定本文件）时 ``slow``
  **仍会被选中**——只有锁一时，**误起真模型**不会被拦下（真模型只在本环境可用；
  耗时口径以 ``docs/engineering/testing-strategy.md`` §8 为准，本文件不复制第二份数字）。

两道锁**互不替代**，**不得**只留一道：删掉锁一 ⇒ 丢掉"默认回归不跑真模型"；删掉锁二 ⇒
丢掉"显式指定 marker 也会被拦"。同样**不得**写成"门禁已排除 ``slow`` ⇒ 不需要 env 开关"。

**注入与隔离（不绕过任何校验）**：``working_dir`` / ``allowed_roots`` 用 ``tmp_path``；
审计落点用 ``JsonlAuditSink(tmp_path/"audit", roots=(tmp_path,))`` —— 走**构造器自带的
白名单参数**，既不改 ``ALLOWED_AUDIT_ROOTS`` 常量，也不把越界路径硬塞进去；模型日志落
``tmp_path``；硬超时（``signal.setitimer``）与端口探测双重兜底，进程泄漏会被判为失败。

跑法（唯一入口，默认不跑）::

    AGENT_SEC_PERF_E2E=1 uv run pytest tests/integration/test_end_to_end.py -m integration

**观测缝（如实登记）**：文本模式 stdout 只渲染部分事件（``render_event`` 对
"无正文的 ``MODEL_RESPONSE``"等返回 ``None``），**无法**从渲染行恢复 ``seq``。
故本模块在文本模式下用 ``monkeypatch`` 给 ``cli.app._write_event`` 装一个**只记录、
不改行为**的观察者，取得"CLI 实际消费的那条事件流"。它不改任何校验、不替换模型，
只是把不可见的事件流变成可断言的对象。
"""

from __future__ import annotations

import io
import json
import os
import signal
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from agent_sec_perf.cli import app as cli_app
from agent_sec_perf.cli.app import EXIT_OK, RunRequest, execute
from agent_sec_perf.cli.render import serialize_event
from agent_sec_perf.contracts.audit import AuditEventKind, AuditOutcome
from agent_sec_perf.contracts.harness import SessionEvent, SessionEventKind, TaskStatus
from agent_sec_perf.contracts.tools import ToolResult
from agent_sec_perf.foundation.config import AppConfig, PolicyConfig
from agent_sec_perf.observability.audit import JsonlAuditSink

pytestmark = [pytest.mark.integration, pytest.mark.slow]

#: 环境变量开关：未设置（或不是 ``1``）⇒ 本模块整体 skip（见模块 docstring 的"默认不跑"）。
_ENABLE_ENV: Final = "AGENT_SEC_PERF_E2E"

#: 已实测存在的本地后端与模型（``docs/devlog/0017`` §2.2）。
_LLAMA_SERVER_BINARY: Final = Path("/opt/llama.cpp/build/bin/llama-server")

#: 候选模型，按顺序取**第一个存在**的。两者都是已实测的本地 GGUF（同 §2.2）。
_MODEL_CANDIDATES: Final = (
    Path("/opt/models/Qwen3-4B-Q4_K_M.gguf"),
    Path("/opt/models/MiniCPM5-2B-Q4_K_M.gguf"),
)

_HOST: Final = "127.0.0.1"

#: 硬超时：``signal.setitimer`` 兜底。内层已有各自的上限（就绪超时 + ``_MODEL_REQUEST_TIMEOUT_S``
#: 的每次模型调用），这里只是"绝不挂住"的最后一层。实测一轮会话约 2 分钟量级（见下），
#: 600s 给出 3 倍以上余量。
_HARD_TIMEOUT_S: Final = 600.0

#: 触发一次真实工具调用所需的最小步数预算（读文件 → 复述内容 → 收尾）。
_MAX_STEPS: Final = 4
_TOOL_TIMEOUT_S: Final = 15.0

#: 单次补全的 token 上限。**取值依据（2026-09-19，本容器实测）**：直接驱动
#: ``LocalLlamaClient.chat``（同样的 SYSTEM 提示 + 任务 + ``read_file`` 工具描述，
#: ``prompt_tokens=552``）时，模型在 ``max_tokens=256`` 处才产出
#: ``finish_reason=tool_calls``（``tool_calls=['read_file']``，耗时 81.3 s）⇒ 取 384
#: （约 1.5 倍余量）：既让模型能收尾，又给服务端一个**硬上限**——
#: 没有上限时它会一直生成（``max_tokens=None`` 的那次实测在 60 s 处超时，n_gen 才到 210）。
#: 上限**只影响生成长度**，不改变任何权限或校验语义。
_MAX_COMPLETION_TOKENS: Final = 384

#: 单次模型请求的超时（秒）。**取值依据（同上实测）**：``tg ≈ 3.5 t/s``，384 token 约 110 s；
#: 加上最坏情况下的 prefill（实测 552 token 提示耗时约 12 s，第二轮提示更长）⇒ 上界约 140 s，
#: 故取 240 s（不少于 1.7 倍余量）。**不得**回落成协议默认的 60 s：那正是本轮 5 failed 的根因
#: （超时 ⇒ ``ModelUnavailableError`` ⇒ 重试耗尽 ⇒ ``FAILED``）。
_MODEL_REQUEST_TIMEOUT_S: Final = 240.0

#: 领域包：把工具**风险**声明出来（``harness.md`` §4 / ``REQ-HARNESS-08``）。
#: 只读工具 + ``low`` 风险 ⇒ 非交互会话也能经策略求值后执行；理由见模块 docstring。
_PACK_DIRNAME: Final = "e2e-readonly-pack"
_PACK_TOML: Final = """\
[pack]
name = "e2e-readonly"
version = "0.0.1"

[tools]
allowlist = ["read_file", "list_dir"]

[security]
capabilities = ["read_file"]

[security.risk_overrides]
read_file = "low"
list_dir = "low"
"""

#: 工作目录里预置的哨兵文件（用于证明"工具真的读到了磁盘上的内容"）。
_SENTINEL_FILE: Final = "hello.txt"
_SENTINEL: Final = "E2E-SENTINEL-7f3a"

#: 环境变量 canary：它**不应**出现在任何我方通道里（``proc.minimal_env`` 不继承父环境）。
_CANARY_ENV: Final = "AGENT_SEC_PERF_E2E_CANARY"
_CANARY_VALUE: Final = "CANARY-9f3a7c21-e2e-should-never-appear"

#: 任务文本：明确要求"必须真的调用工具"，否则审计面为空、用例也拿不到"能跑起来"的证据。
_TASK: Final = (
    "工作目录下有一个文件 hello.txt。请调用 read_file 工具读取它（path 取值为 hello.txt），"
    "然后把文件内容原样作为最终回答。必须先调用工具，不要凭猜测回答。"
)

#: 凭据标记（**小写**比较；这些串都不应出现在我方通道里）。
_CREDENTIAL_MARKERS: Final = (
    "sk-",
    "bearer ",
    "authorization",
    "api_key",
    "apikey",
    "password",
    "passwd",
    "secret",
    "credential",
    "private_key",
    "-----begin",
)

#: 状态行前缀（文本模式用于确认"只有一条终态行"，即渲染侧的 ``I6``）。
_STATUS_PREFIXES: Final = tuple(f"{status.value}：" for status in TaskStatus)

# ---------------------------------------------------------------------------
# 多步会话（`M2-1` / `M2-4`）的参数：任务的**第二步依赖第一步的结果**
# ---------------------------------------------------------------------------

#: 多步会话的领域包。形状与基线包一致，只换名字（包名会出现在那条 ``role=USER`` 的数据消息里）；
#: 白名单同时含 ``read_file`` 与 ``list_dir``，两者都声明 ``READ_FILE``（BASIC 档的能力预算）。
_MULTI_STEP_PACK_DIRNAME: Final = "e2e-multistep-pack"
_MULTI_STEP_PACK_TOML: Final = """\
[pack]
name = "e2e-multistep"
version = "0.0.1"

[tools]
allowlist = ["read_file", "list_dir"]

[security]
capabilities = ["read_file"]

[security.risk_overrides]
read_file = "low"
list_dir = "low"
"""

#: 工作目录里预置的两个文件，**内容互不相同**。为什么要两个：``M2-1`` 的判据是
#: "覆盖 **> 1** 个不同工具或不同参数"，而**参数本身进不了事件流与审计**
#: （``I8`` 禁止 ``arguments_json`` 进事件、``D2`` 禁止调用参数进审计）⇒
#: "参数不同"唯一的可观察证据是"**读到的内容不同**"。只预置一个文件时，
#: 同一工具读同一文件两次会得到两份相同内容，用例就该红——那正是判据要拦的情形。
_MULTI_STEP_FILES: Final = (
    ("hello.txt", "E2E-MULTI-A-2b19"),
    ("notes.txt", "E2E-MULTI-B-7d42"),
)

#: 读取类工具（``M2-1`` 要求 ≥ 1 次为读取类工具，如 ``read_file`` / ``list_dir``）。
#: BASIC 档的能力预算只含 ``READ_FILE`` ⇒ 能**执行成功**的工具名必然落在这个集合里。
_MULTI_STEP_READ_TOOLS: Final = frozenset({"read_file", "list_dir"})

#: 多步任务：第一步列目录、第二步读**列出来的**那个文件 ⇒ 两次调用之间有真实的数据依赖。
#: 措辞沿用 BASIC 档提示的"单次单工具、等结果再决定下一步"，并要求"必须先列目录"，
#: 以免模型跳过第一步直读文件（那样就只有一次调用，用例会如实红）。
_MULTI_STEP_TASK: Final = (
    "工作目录下有若干文件。请严格按顺序完成两步，每一步都必须真的调用工具："
    "第一步，调用 list_dir 工具列出工作目录的条目（path 取值为 .）；"
    "第二步，从列出的条目里找到 hello.txt，调用 read_file 工具读取它（path 取值为 hello.txt）。"
    "必须先做第一步，等它的结果返回后再做第二步；"
    "最后把 hello.txt 的内容原样作为最终回答。"
)

#: 多步会话**复用**基线的 ``max_completion_tokens``（384）与请求超时（240 s）：
#: 实测两次工具调用都在 384 token 内以 ``finish_reason=tool_calls`` 完成
#: （见 :data:`_MULTI_STEP_HARD_TIMEOUT_S` 的实测），且两个常量按"**单次补全**"计量
#: ⇒ 多步只是"调用次数变多"，不构成第二份取值的理由。**不复制**它们，避免两份默认值漂移。
#: 下面两个常量才是多步**新增**的（它们按"整轮会话"计量，与单步不同量级）。

#: 多步会话的**模型往返**预算。取值依据：这条链路最少需要 3 次往返（列目录 → 读文件 →
#: 给出结论），另留 3 次余量给"模型多绕一圈"（``loop`` 的瞬时重试**不**新开一步，
#: 由 ``errors.MAX_TRANSIENT_RETRIES`` 独立兜住）⇒ 取 6。**只影响上限**，
#: 不改变任何权限或校验语义；步数耗尽会以 ``LIMIT_REACHED``（退出码 2）响亮失败。
_MULTI_STEP_MAX_STEPS: Final = 6

#: 多步会话的硬超时（``signal.setitimer`` 兜底）。**取值依据（2026-09-20，本容器实测）**：
#: 实测一轮多步会话 **≈ 190 s**（2026-09-20 两次实测：``pytest -k multi_step`` 185.7 s、
#: 同 spec 的探针复跑 188.8 s），事件流为 ``list_dir`` → ``read_file`` → 结论的 3 次模型往返。
#: 但硬超时是**兜底**，必须盖住"每次都恰好撞上请求超时"的最坏情况：
#: ``_MULTI_STEP_MAX_STEPS``(6) x ``_MODEL_REQUEST_TIMEOUT_S``(240) = 1440 s ⇒ 取 1800 s。
#: **不**复用基线的 600 s：那是按单步会话定的，多步的最坏情况会踩到它，
#: 而硬超时一旦触发，失败信息会变成"超时"而不是"模型没做到两次调用"（归因错误）。
_MULTI_STEP_HARD_TIMEOUT_S: Final = 1800.0


class _HardTimeout(BaseException):
    """硬超时（**刻意不继承** ``Exception``）。

    理由：:func:`~agent_sec_perf.cli.app.execute` 内部把所有 ``Exception`` 收敛为退出码
    （"不抛异常"是它的契约）。若超时异常继承 ``Exception``，它会被当作"未预期异常"吞掉并
    返回 ``5`` —— 用例仍会红，但**看不到"超时"这一事实**。不继承 ``Exception`` ⇒ 直接冒到
    用例，失败信息明确。
    """


@dataclass(frozen=True)
class _SessionOutcome:
    """一次真实会话的可断言快照（全部由**产品路径**产生，无替身）。"""

    exit_code: int
    stdout: str
    stderr: str
    events: tuple[SessionEvent, ...]
    audit_path: Path
    audit_lines: tuple[str, ...]
    model_log_path: Path
    working_dir: Path
    port: int


@dataclass(frozen=True)
class _SessionSpec:
    """一次真实会话的**输入参数**（任务 / 预置文件 / 领域包 / 步数与硬超时）。

    为什么把它抽出来：多步用例与既有单步用例必须共用**同一套**驱动逻辑（装配顺序、
    注入点、硬超时、进程回收、审计读取）。复制一份驱动只会立刻产生两份 teardown /
    超时实现，而它们**必然漂移**（本项目的既有取向：同一事实不得两处表述）。

    默认值由 :data:`_BASELINE_SPEC` 给出并**逐字保留既有单步用例的参数**——
    既有 fixture 与断言的行为一字不改（这只是把常量搬到参数上，没有换判据）。
    """

    label: str
    task: str
    files: tuple[tuple[str, str], ...]
    pack_dirname: str
    pack_toml: str
    max_steps: int
    hard_timeout_s: float


#: 既有单步用例的参数（原样搬入；**语义与取值一字未改**）。
_BASELINE_SPEC: Final = _SessionSpec(
    label="baseline",
    task=_TASK,
    files=((_SENTINEL_FILE, _SENTINEL),),
    pack_dirname=_PACK_DIRNAME,
    pack_toml=_PACK_TOML,
    max_steps=_MAX_STEPS,
    hard_timeout_s=_HARD_TIMEOUT_S,
)

#: 多步用例的参数（`M2-1` / `M2-4`）；可用性参数复用基线的取值，理由见其定义处。
_MULTI_STEP_SPEC: Final = _SessionSpec(
    label="multistep",
    task=_MULTI_STEP_TASK,
    files=_MULTI_STEP_FILES,
    pack_dirname=_MULTI_STEP_PACK_DIRNAME,
    pack_toml=_MULTI_STEP_PACK_TOML,
    max_steps=_MULTI_STEP_MAX_STEPS,
    hard_timeout_s=_MULTI_STEP_HARD_TIMEOUT_S,
)


# ---------------------------------------------------------------------------
# 环境门槛
# ---------------------------------------------------------------------------


def _require_enabled() -> None:
    """环境变量开关：未设置即 skip（见模块 docstring 的"默认不跑"）。"""
    if os.environ.get(_ENABLE_ENV) != "1":
        pytest.skip(f"需要真实模型：设置 {_ENABLE_ENV}=1 后再运行")


def _require_assets() -> tuple[Path, Path]:
    """校验本地后端与模型**确实存在**；缺一即**失败**（不是 skip）。

    理由（派活口径）：环境变量已显式打开时，"资产不存在"属**环境自助检失败**——
    静默 skip 会让"以为跑了真实端到端、其实什么都没跑"与"确实跑过了"同形。
    这里选择响亮失败并给出可复现的下一步，而不是放宽门槛。
    """
    if not _LLAMA_SERVER_BINARY.is_file():
        pytest.fail(
            f"环境自助检失败：找不到本地后端 {_LLAMA_SERVER_BINARY}"
            "（见 docs/devlog/0017 §2.2 的环境事实）",
            pytrace=False,
        )
    for candidate in _MODEL_CANDIDATES:
        if candidate.is_file():
            return _LLAMA_SERVER_BINARY, candidate
    missing = "、".join(str(path) for path in _MODEL_CANDIDATES)
    pytest.fail(f"环境自助检失败：候选模型都不存在（{missing}）", pytrace=False)


# ---------------------------------------------------------------------------
# 进程与端口工具（硬超时 / 清理取证）
# ---------------------------------------------------------------------------


@contextmanager
def _hard_timeout(seconds: float) -> Iterator[None]:
    """在**主线程**安装 ``SIGALRM`` 硬超时；非主线程退化为"依赖被测代码自身的超时"。"""
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _on_alarm(signum: int, frame: object) -> None:
        del signum, frame
        raise _HardTimeout(f"端到端会话超过硬超时 {seconds:.0f}s（进程可能未清理）")

    previous = signal.signal(signal.SIGALRM, _on_alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous)


def _free_port(host: str) -> int:
    """取一个当前空闲的本地端口（避免与默认 8080 上的既有服务撞车）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def _can_bind(host: str, port: int) -> bool:
    """该端口是否**没有监听者**（``SO_REUSEADDR`` 下仍能 bind ⇒ 没有活着的监听套接字）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def _reap_llama_server(*, port: int, model_path: Path) -> tuple[int, ...]:
    """尽力回收本用例启动的 ``llama-server``（**兜底**，正常路径由 teardown 完成）。

    正常路径是 ``with Session(...)`` → ``Session.close()`` → ``ModelClient.close()``
    （契约 §2.9 的可观察两步）。硬超时那类异常路径会让 ``LocalLlamaClient._start`` 的
    清理分支走不到，于是留下一个常驻进程 ⇒ 这里按 **环境变量不可控的两个事实**
    （命令行里既有 ``-m <本次模型路径>`` 又有 ``--port <本次临时端口>``）定位并 SIGTERM。
    匹配条件刻意收紧到两条同时命中，避免误伤其它进程；非 Linux（无 ``/proc``）时为空操作。
    """
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return ()
    wanted_model = str(model_path)
    wanted_port = str(port)
    killed: list[int] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        argv = [token.decode("utf-8", "replace") for token in raw.split(b"\0") if token]
        if wanted_model not in argv or "--port" not in argv:
            continue
        index = argv.index("--port")
        if argv[index + 1 : index + 2] != [wanted_port]:
            continue
        pid = int(entry.name)
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
        killed.append(pid)
    return tuple(killed)


def _assert_port_released(*, port: int, timeout_s: float = 10.0) -> None:
    """断言该端口已无监听者（⇒ ``llama-server`` 已被清理，不只是"关掉了客户端"）。"""
    deadline = time.monotonic() + timeout_s
    while not _can_bind(_HOST, port):
        if time.monotonic() >= deadline:
            pytest.fail(f"llama-server 未被清理：{_HOST}:{port} 仍被占用", pytrace=False)
        time.sleep(0.1)


# ---------------------------------------------------------------------------
# 解析辅助
# ---------------------------------------------------------------------------


def _loads(text: str, *, context: str) -> dict[str, object]:
    """``json.loads`` 的带上下文包装：任何失败都带**位置**回报（便于定位坏行）。"""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        pytest.fail(f"{context} 不是合法 JSON：{text!r}（{exc}）", pytrace=False)
    assert isinstance(parsed, dict), f"{context} 不是 JSON 对象：{type(parsed).__name__}"
    return parsed


def _jsonl(stdout: str) -> list[dict[str, object]]:
    """把 stdout 解析成 JSONL；**任何非 JSON 行都会让用例失败**（这正是被测的道口）。"""
    payloads: list[dict[str, object]] = []
    for number, line in enumerate(stdout.splitlines(), start=1):
        if not line.strip():
            continue
        payloads.append(_loads(line, context=f"stdout 第 {number} 行"))
    return payloads


def _event_text(event: SessionEvent) -> str:
    """事件的序列化文本（用**产品自己的** ``serialize_event``，与 JSON 模式同源）。"""
    return serialize_event(event)


# ---------------------------------------------------------------------------
# 真实会话
# ---------------------------------------------------------------------------


def _run_real_session(
    tmp_path_factory: pytest.TempPathFactory,
    *,
    output_format: str,
    spec: _SessionSpec = _BASELINE_SPEC,
) -> _SessionOutcome:
    """按契约 §5.1 驱动一次**真实**会话并返回可断言快照。

    注入的点只有两处，且都在 :func:`execute` 的公开签名内：``config``（能力授予按需显式
    给出，default-deny 的默认空集在这里**不**够用）与 ``sink``（落点必须能通过
    ``JsonlAuditSink`` 自己的白名单校验）。模型由 ``execute`` 用 ``RunRequest`` 的字段
    自己构造 —— **不走替身、不直接调 ``Session``**。

    ``spec`` 缺省即既有单步用例（``_BASELINE_SPEC``，取值与重构前逐字相同）。
    """
    _require_enabled()
    binary, model_path = _require_assets()

    working_dir = tmp_path_factory.mktemp(f"e2e-{spec.label}-{output_format}")
    for name, content in spec.files:
        (working_dir / name).write_text(f"{content}\n", encoding="utf-8")
    # 领域包必须落在 allowed_roots 内（``load_pack`` 自己会经 ``resolve_within`` 校验）。
    pack_directory = working_dir / spec.pack_dirname
    pack_directory.mkdir()
    (pack_directory / "pack.toml").write_text(spec.pack_toml, encoding="utf-8")
    audit_dir = working_dir / "audit"
    sink = JsonlAuditSink(audit_dir, roots=(working_dir,))
    model_log = working_dir / "llama-server.log"
    port = _free_port(_HOST)

    request = RunRequest(
        task=spec.task,
        working_dir=working_dir,
        allowed_roots=(working_dir,),
        output_format=output_format,
        pack_directory=pack_directory,
        model_binary=str(binary),
        model_path=model_path,
        model_log=model_log,
        host=_HOST,
        port=port,
        max_steps=spec.max_steps,
        tool_timeout_s=_TOOL_TIMEOUT_S,
        # 慢硬件的可用性参数（取值依据见常量定义处）；断言不因它们而改变。
        max_completion_tokens=_MAX_COMPLETION_TOKENS,
        model_request_timeout_s=_MODEL_REQUEST_TIMEOUT_S,
    )
    # 显式授予只读能力：BASIC 档的暴露面就是 read_file / list_dir（trimming 的档位预算），
    # 而 default-deny 的默认授予是空集（`AppConfig()`）⇒ 不显式给出的话每次调用都会被策略拒绝。
    config = AppConfig(policy=PolicyConfig(granted_capabilities=("read_file",)))

    out = io.StringIO()
    err = io.StringIO()
    events: list[SessionEvent] = []

    patch = pytest.MonkeyPatch()
    patch.setenv(_CANARY_ENV, _CANARY_VALUE)
    original_write = cli_app._write_event

    def _record(event: SessionEvent, *, stdout: io.StringIO, json_mode: bool) -> None:
        events.append(event)
        original_write(event, stdout=stdout, json_mode=json_mode)

    patch.setattr(cli_app, "_write_event", _record)
    try:
        with _hard_timeout(spec.hard_timeout_s):
            exit_code = execute(request, stdout=out, stderr=err, config=config, sink=sink)
    finally:
        patch.undo()
        _reap_llama_server(port=port, model_path=model_path)
        _assert_port_released(port=port)

    audit_path = sink.path
    audit_lines = (
        tuple(audit_path.read_text(encoding="utf-8").splitlines()) if audit_path.is_file() else ()
    )
    return _SessionOutcome(
        exit_code=exit_code,
        stdout=out.getvalue(),
        stderr=err.getvalue(),
        events=tuple(events),
        audit_path=audit_path,
        audit_lines=audit_lines,
        model_log_path=model_log,
        working_dir=working_dir,
        port=port,
    )


@pytest.fixture(scope="module")
def text_session(tmp_path_factory: pytest.TempPathFactory) -> _SessionOutcome:
    """文本模式（默认输出格式）的一次真实会话。"""
    return _run_real_session(tmp_path_factory, output_format="text")


@pytest.fixture(scope="module")
def json_session(tmp_path_factory: pytest.TempPathFactory) -> _SessionOutcome:
    """``--output-format json`` 的一次真实会话。"""
    return _run_real_session(tmp_path_factory, output_format="json")


@pytest.fixture(scope="module")
def multi_step_session(tmp_path_factory: pytest.TempPathFactory) -> _SessionOutcome:
    """**多步**（≥ 2 次工具调用）的一次真实会话：第二步的输入来自第一步的输出。

    与上面两个 fixture **并列**（互不替代）：它们替 `0.1.0` 的单步判据取证，
    本 fixture 替 `M2-1` / `M2-4` 的多步判据取证。参数见 :data:`_MULTI_STEP_SPEC`。
    """
    return _run_real_session(tmp_path_factory, output_format="text", spec=_MULTI_STEP_SPEC)


# ---------------------------------------------------------------------------
# 1. 文本模式：退出码 / I6 / I9
# ---------------------------------------------------------------------------


def test_text_mode_exits_zero_and_event_stream_ends_with_single_task_finished(
    text_session: _SessionOutcome,
) -> None:
    """退出码 ``0``；``I6``（恰好一条 ``TASK_FINISHED`` 且在最后）；``I9``（``seq`` 连续）。"""
    outcome = text_session
    assert outcome.exit_code == EXIT_OK, (
        f"退出码应为 0（COMPLETED），实际 {outcome.exit_code}；stderr={outcome.stderr!r}"
    )

    events = outcome.events
    assert events, "事件流为空（至少应有 MODEL_RESPONSE 与 TASK_FINISHED）"

    kinds = [event.kind for event in events]
    assert kinds.count(SessionEventKind.TASK_FINISHED) == 1, "I6：TASK_FINISHED 必须恰好一条"
    assert kinds[-1] is SessionEventKind.TASK_FINISHED, "I6：TASK_FINISHED 必须是最后一条"

    finished = events[-1]
    assert finished.status is TaskStatus.COMPLETED, (
        f"TASK_FINISHED.status 应为 COMPLETED，实际 {finished.status}"
    )
    assert finished.text, "I6：TASK_FINISHED.text 必须非空"

    seqs = [event.seq for event in events]
    assert seqs == list(range(len(events))), f"I9：seq 必须从 0 连续无空洞，实际 {seqs}"


def test_text_mode_stdout_renders_exactly_one_terminal_status_line(
    text_session: _SessionOutcome,
) -> None:
    """文本模式 stdout 的**渲染侧** ``I6``：只有一条终态行，且它就是最后一行。"""
    lines = [line for line in text_session.stdout.splitlines() if line.strip()]
    assert lines, "文本模式 stdout 为空（渲染分支一条都没走到？）"

    status_lines = [line for line in lines if line.startswith(_STATUS_PREFIXES)]
    assert len(status_lines) == 1, f"渲染出的终态行应恰好一条，实际 {status_lines}"
    assert lines[-1].startswith(f"{TaskStatus.COMPLETED.value}："), (
        f"最后一行应是 completed 终态行，实际 {lines[-1]!r}"
    )


# ---------------------------------------------------------------------------
# 1'. 文本模式：审计真实落盘 + 工具真的执行过
# ---------------------------------------------------------------------------


def test_text_mode_audit_is_persisted_and_every_line_parses(text_session: _SessionOutcome) -> None:
    """审计**真实存在**且**每行**可 ``json.loads``（``REQ-SEC-06`` 的可回放前提）。"""
    outcome = text_session
    assert outcome.audit_path.is_file(), f"审计未落盘：{outcome.audit_path}"
    assert outcome.audit_lines, "审计文件为空：本次会话没有产生任何审计事件（I6 之外的证据面缺失）"

    for number, line in enumerate(outcome.audit_lines, start=1):
        payload = _loads(line, context=f"审计第 {number} 行")
        assert payload.get("event_id"), f"审计第 {number} 行缺 event_id（回放关联键）"


def test_text_mode_executes_a_real_tool_call(text_session: _SessionOutcome) -> None:
    """模型确实发起了工具调用，且**至少一次真的执行**（读到了磁盘上的哨兵内容）。

    这条是"能跑起来"的核心证据：只有 DENY 路径也能写审计，但没有执行就不算跑通
    工具链路（校验 → ``decide()`` → ``invoke()`` → 回喂）。
    """
    outcome = text_session
    calls = [event for event in outcome.events if event.kind is SessionEventKind.TOOL_CALL]
    assert calls, "模型未发起任何工具调用 ⇒ 工具链路与审计面都没有被验证"

    executed = [
        event
        for event in outcome.events
        if event.kind is SessionEventKind.TOOL_RESULT and event.result is not None
    ]
    assert executed, "所有工具调用都被拒绝/未执行 ⇒ 工具链路未跑通（见审计的 denied_reason）"

    contents = [event.result.content for event in executed if event.result is not None]
    assert any(_SENTINEL in content for content in contents), (
        f"已执行的工具结果里没有 {_SENTINEL_FILE} 的哨兵内容 ⇒ 工具没有真正读到该文件"
    )


def test_text_mode_started_a_real_model_server(text_session: _SessionOutcome) -> None:
    """模型日志**确实**由一次真实 ``llama-server`` 启动写出（防止"跑了假链路"）。"""
    log = text_session.model_log_path
    assert log.is_file(), f"模型日志不存在：{log}（llama-server 没被启动过？）"
    assert log.stat().st_size > 0, f"模型日志为空：{log}"


# ---------------------------------------------------------------------------
# 2. JSON 模式：stdout 只出 JSONL
# ---------------------------------------------------------------------------


def test_json_mode_stdout_is_pure_jsonl(json_session: _SessionOutcome) -> None:
    """``--output-format json``：stdout 逐行都是 JSON，且**一条不漏、一条不多**。"""
    outcome = json_session
    assert outcome.exit_code == EXIT_OK, (
        f"退出码应为 0（COMPLETED），实际 {outcome.exit_code}；stderr={outcome.stderr!r}"
    )

    payloads = _jsonl(outcome.stdout)
    assert payloads, "stdout 没有任何 JSONL 行"

    assert len(payloads) == len(outcome.events), (
        "JSONL 行数必须等于事件数："
        f"{len(payloads)} != {len(outcome.events)}（有事件没被序列化，或有非 JSON 行混入）"
    )
    assert [payload["seq"] for payload in payloads] == list(range(len(payloads))), (
        "JSONL 的 seq 必须从 0 连续且与行序一致"
    )

    kinds = [payload["kind"] for payload in payloads]
    assert kinds.count(SessionEventKind.TASK_FINISHED.value) == 1
    assert kinds[-1] == SessionEventKind.TASK_FINISHED.value
    assert payloads[-1]["status"] == TaskStatus.COMPLETED.value


# ---------------------------------------------------------------------------
# 3. 无凭据泄漏
# ---------------------------------------------------------------------------


def test_no_credential_material_leaks_into_any_channel(
    text_session: _SessionOutcome, json_session: _SessionOutcome
) -> None:
    """事件 / 审计 / 两条输出流里都不出现凭据标记，子进程环境 canary 也不回流。"""
    channels: dict[str, str] = {
        "text-stdout": text_session.stdout,
        "text-stderr": text_session.stderr,
        "json-stdout": json_session.stdout,
        "json-stderr": json_session.stderr,
        "events": "\n".join(_event_text(event) for event in text_session.events),
        "audit": "\n".join(text_session.audit_lines),
    }
    canary = _CANARY_VALUE.lower()
    for where, blob in channels.items():
        lowered = blob.lower()
        for marker in _CREDENTIAL_MARKERS:
            assert marker not in lowered, f"{where} 出现疑似凭据标记 {marker!r}"
        assert canary not in lowered, f"{where} 泄漏了子进程环境 canary"

    # 审计的 detail 是"从不可信内容再脱敏一次"的纵深防御面（audit.md）：单独再扫一遍。
    for number, line in enumerate(json_session.audit_lines, start=1):
        assert _CANARY_VALUE.lower() not in line.lower(), f"JSON 模式审计第 {number} 行泄漏 canary"


# ---------------------------------------------------------------------------
# 4. 多步会话（`M2-1` / `M2-4`）：审计侧计数 + **逐条**可回放
# ---------------------------------------------------------------------------


def _audit_reader(outcome: _SessionOutcome) -> JsonlAuditSink:
    """打开一个指向**本次会话已落盘的审计文件**的只读侧。

    刻意**重新构造**一个 :class:`JsonlAuditSink`（走它自己的白名单参数，不改任何常量、
    不把越界路径硬塞进去），而不是复用会话期持有的写侧对象：`M2-4` 的判据是
    "由 ``query_by_id`` 从**落盘文件**还原"——复用写侧对象证明不了"落盘"这一环。
    """
    return JsonlAuditSink(outcome.audit_path.parent, roots=(outcome.working_dir,))


def _expected_audit_outcome(result: ToolResult | None) -> AuditOutcome:
    """把事件流一侧的工具结果映射成"审计该写什么"（`M2-4` 的对齐依据）。

    ``result is None`` 是**未执行**路径（拒绝 ≠ 失败）⇒ ``DENY``；已执行则按 ``ok``
    分 ``OK`` / ``ERROR``。映射与 ``tools/registry.audit_tool_call`` 同源，
    因此这一层比较的是"**审计说做了什么**"与"**事件流说做了什么**"是否一致，
    而不是把同一份数据与自己比较。
    """
    if result is None:
        return AuditOutcome.DENY
    return AuditOutcome.OK if result.ok else AuditOutcome.ERROR


def _describe_events(events: tuple[SessionEvent, ...]) -> str:
    """把事件流压成一行（**只含我方字段**：kind / seq / call_id / tool_name / status / error_kind）。

    存在的理由：这条用例最可能的红法是"模型只做了一次调用"，而那时**必须看得到**
    "模型到底请求了什么、哪些被拒了"。没有它，失败信息只剩"断言 1 >= 2"。
    """
    if not events:
        return "事件流为空"
    rendered: list[str] = []
    for event in events:
        bits = [f"seq={event.seq}", f"kind={event.kind.value}"]
        if event.tool_name is not None:
            bits.append(f"tool={event.tool_name}")
        if event.call_id is not None:
            bits.append(f"call_id={event.call_id}")
        if event.status is not None:
            bits.append(f"status={event.status.value}")
        if event.error_kind is not None:
            bits.append(f"error_kind={event.error_kind.value}")
        rendered.append("[" + " ".join(bits) + "]")
    return "事件流：" + " ".join(rendered)


def test_multi_step_session_exits_zero_and_keeps_stream_invariants(
    multi_step_session: _SessionOutcome,
) -> None:
    """多步会话仍满足既有不变量：退出码 ``0``、``I6``（恰好一条 ``TASK_FINISHED`` 且在最后）、
    ``I9``（``seq`` 从 ``0`` 连续）——与单步用例同一口径，**不复用其 fixture**。"""
    outcome = multi_step_session
    events = outcome.events
    assert outcome.exit_code == EXIT_OK, (
        f"退出码应为 0（COMPLETED），实际 {outcome.exit_code}；"
        f"stderr={outcome.stderr!r}；{_describe_events(events)}"
    )
    assert events, "事件流为空（至少应有 MODEL_RESPONSE 与 TASK_FINISHED）"

    kinds = [event.kind for event in events]
    assert kinds.count(SessionEventKind.TASK_FINISHED) == 1, (
        f"I6：TASK_FINISHED 必须恰好一条；{_describe_events(events)}"
    )
    assert kinds[-1] is SessionEventKind.TASK_FINISHED, (
        f"I6：TASK_FINISHED 必须是最后一条；{_describe_events(events)}"
    )
    assert events[-1].status is TaskStatus.COMPLETED, (
        f"TASK_FINISHED.status 应为 COMPLETED，实际 {events[-1].status}；{_describe_events(events)}"
    )

    seqs = [event.seq for event in events]
    assert seqs == list(range(len(events))), f"I9：seq 必须从 0 连续无空洞，实际 {seqs}"


def test_multi_step_session_audit_has_at_least_two_ok_tool_calls(
    multi_step_session: _SessionOutcome,
) -> None:
    """`M2-1` 的判据原文：该会话的审计文件里 ``kind=TOOL_CALL`` 且 ``outcome=OK`` 的事件 **≥ 2 条**，
    且覆盖 **> 1** 个不同工具或不同参数。

    "不同参数"在事件流与审计里都**不可直接观察**（``I8`` 禁止 ``arguments_json`` 进事件、
    ``D2`` 禁止调用参数进审计）⇒ 这里的证据是"两次调用**读到的内容不同**"：预置的
    ``hello.txt`` 与 ``notes.txt`` 内容不同（见 ``_MULTI_STEP_FILES``）。参数相同则内容相同，
    断言就该红——这正是判据要拦的"同一件事说两遍"。
    """
    outcome = multi_step_session
    audit_events = _audit_reader(outcome).read_all()
    ok_calls = [
        event
        for event in audit_events
        if event.kind is AuditEventKind.TOOL_CALL and event.outcome is AuditOutcome.OK
    ]
    executed = [
        event
        for event in outcome.events
        if event.kind is SessionEventKind.TOOL_RESULT and event.result is not None
    ]

    assert len(ok_calls) >= 2, (
        f"M2-1：审计里 kind=TOOL_CALL 且 outcome=OK 的事件应 ≥ 2 条，实际 {len(ok_calls)}；"
        f"{_describe_events(outcome.events)}"
    )

    # 两次调用必须是**两个 call_id**（不是同一次调用被记了两遍）。
    assert len({event.call_id for event in executed}) == len(executed), (
        f"已执行的工具调用必须各有独立 call_id，实际 {[event.call_id for event in executed]}"
    )
    # 审计侧的 OK 条数与事件流侧的"已执行且成功"逐一对上（一条不漏、一条不多）。
    assert len(ok_calls) == sum(
        1 for event in executed if event.result is not None and event.result.ok
    ), (
        f"审计的 OK 条数（{len(ok_calls)}）与事件流里已执行且成功的调用数"
        f"（{sum(1 for event in executed if event.result is not None and event.result.ok)}）不一致"
    )

    names = {event.tool_name for event in executed if event.tool_name is not None}
    contents = {event.result.content for event in executed if event.result is not None}
    assert len(names) > 1 or len(contents) > 1, (
        "M2-1：两次调用必须覆盖 > 1 个不同工具或不同参数"
        f"（工具名={sorted(names)}，读到的内容种类={len(contents)}）；{_describe_events(outcome.events)}"
    )
    # M2-1 的"≥ 1 次为读取类工具"：能**执行成功**的工具名必然落在读取类里
    # （BASIC 档能力预算 + 领域包白名单共同决定，工具侧不自行放宽）。
    assert names <= _MULTI_STEP_READ_TOOLS, (
        f"M2-1：执行的工具名应全部是读取类 {sorted(_MULTI_STEP_READ_TOOLS)}，实际 {sorted(names)}"
    )


def test_multi_step_session_every_tool_call_is_replayable_by_query_by_id(
    multi_step_session: _SessionOutcome,
) -> None:
    """`M2-4`：审计文件里**每一条** ``TOOL_CALL`` 事件都能由 ``query_by_id`` 从落盘文件还原，
    且与事件流逐条对齐（``kind`` / ``call_id`` / ``outcome``）——**不得只验单步**。

    三条独立的对照（缺一条就会退化成"自己和自己比"）：

    1. ``query_by_id(event_id)`` 必须**命中**且字段自洽（回放链路真的从文件走通）；
    2. 该 ``call_id`` 在事件流里**恰好一条** ``TOOL_CALL`` 事件、``tool_name`` 相同
       （审计的调用与模型请求的那次调用是同一件事）；
    3. 该 ``event_id`` 在事件流里**恰好被一条** ``TOOL_RESULT`` 引用，且该结果的
       ``ok`` 与审计的 ``outcome`` 一致（``OK`` / ``ERROR`` / ``DENY`` 三分，见
       :func:`_expected_audit_outcome`）。
    """
    outcome = multi_step_session
    reader = _audit_reader(outcome)
    audit_calls = [event for event in reader.read_all() if event.kind is AuditEventKind.TOOL_CALL]
    assert len(audit_calls) >= 2, (
        f"M2-4：待回放的 TOOL_CALL 审计事件应 ≥ 2 条（多步），实际 {len(audit_calls)}；"
        f"{_describe_events(outcome.events)}"
    )

    session_calls: dict[str, list[SessionEvent]] = {}
    for event in outcome.events:
        if event.kind is SessionEventKind.TOOL_CALL and event.call_id is not None:
            session_calls.setdefault(event.call_id, []).append(event)

    linked_results: dict[str, list[SessionEvent]] = {}
    for event in outcome.events:
        if event.kind is SessionEventKind.TOOL_RESULT and event.audit_id is not None:
            linked_results.setdefault(event.audit_id, []).append(event)

    for audit_event in audit_calls:
        restored = reader.query_by_id(audit_event.event_id)
        assert restored is not None, f"审计事件 {audit_event.event_id} 无法由 query_by_id 还原"
        assert restored.kind is AuditEventKind.TOOL_CALL, (
            f"还原出的事件 kind 应为 tool_call，实际 {restored.kind}"
        )
        assert restored.event_id == audit_event.event_id
        assert restored.call_id == audit_event.call_id, (
            f"还原出的事件 call_id 与落盘事件不一致：{restored.call_id} != {audit_event.call_id}"
        )
        assert restored.outcome is audit_event.outcome, (
            f"还原出的事件 outcome 与落盘事件不一致：{restored.outcome} != {audit_event.outcome}"
        )

        assert audit_event.call_id is not None, "TOOL_CALL 审计事件必须带 call_id（关联键）"
        matching_calls = session_calls.get(audit_event.call_id, [])
        assert len(matching_calls) == 1, (
            f"call_id={audit_event.call_id} 在事件流里必须恰好一条 TOOL_CALL，"
            f"实际 {len(matching_calls)}；{_describe_events(outcome.events)}"
        )
        assert matching_calls[0].tool_name == audit_event.tool_name, (
            f"审计的 tool_name（{audit_event.tool_name}）与事件流（{matching_calls[0].tool_name}）不一致"
        )

        results = linked_results.get(audit_event.event_id, [])
        assert len(results) == 1, (
            f"审计事件 {audit_event.event_id} 必须被事件流里恰好一条 TOOL_RESULT 引用，"
            f"实际 {len(results)}；{_describe_events(outcome.events)}"
        )
        assert audit_event.outcome is _expected_audit_outcome(results[0].result), (
            f"审计 outcome（{audit_event.outcome}）与事件流结果（result={results[0].result}）不一致"
        )
