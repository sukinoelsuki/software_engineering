"""基准协议：固定参数、任务集与夹具。

**协议版本是数据可比性的锚点。** 任何会改变测量口径的改动（启动参数、提示词、
判定方式、任务集）都必须同时提升 :data:`PROTOCOL_VERSION`；跨协议版本的数据
不得放在同一条序列上比较——否则"改进"与"口径变了"无法区分
（同族教训见 ``docs/notes/evaluation-pitfalls.md`` 第 4 条）。

夹具（fixture）用 ``.txt`` 后缀保存，原因与 ``docs/research/reports/*/HARNESS.md``
一致：它们**故意**含有缺陷且没有类型标注，是"被测对象"而不是"要维护的代码"。
若以 ``.py`` 保存，``ruff`` / ``mypy`` 会试图"修好"它们，破坏测量意图。
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

from agent_sec_perf.foundation.errors import ProtocolError

# ---------------------------------------------------------------------------
# 协议常量
# ---------------------------------------------------------------------------

PROTOCOL_VERSION = "bench-v2"

TIERS: tuple[str, ...] = ("S", "M", "L")

MODELS: dict[str, str] = {
    "S": "MiniCPM5-2B-Q4_K_M.gguf",
    "M": "Qwen3-4B-Q4_K_M.gguf",
    "L": "Qwen3-8B-Q4_K_M.gguf",
}

TASK_IDS: tuple[str, ...] = ("t1", "t2", "t3")

DEFAULT_MODEL_DIR = pathlib.Path("/opt/models")
LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8080

#: 最小值 3：判据要求"至少重复 3 次并报极差"，低于此值的数据不得用于阈值判断。
MIN_REPEATS = 3

#: 一次 prefill 至少要评估这么多 token，才算"真的做了 prefill"。
#:
#: 为什么需要这条：llama-server 会复用同一槽位上的提示前缀。同一提示词第二次发送时，
#: 日志给出的是 ``prompt eval time = 34.89 ms / 1 tokens``——那不是 prefill 速率，
#: 而是"命中缓存后的 1 个 token"。2026-09-16 的首次试跑就因此得到一条极差 326% 的
#: 假 prefill 序列。结构上的对策是**每次重复重启服务进程**（KV 为空）；
#: 这条阈值是第二道闸门：一旦计时行看起来像缓存命中，就拒绝计入统计。
MIN_PREFILL_TOKENS = 32

#: 每次重复的期望耗时行数（三个任务各一次）。行数不符即视为"口径可能已变"。
EXPECTED_TIMING_LINES_PER_REPEAT = 3

TASKS_DIR = pathlib.Path(__file__).resolve().parent / "tasks"

FIXTURE_NAMES: tuple[str, ...] = ("t1_input.py", "t2_bug.py", "t2_test.py", "t3_pure.py")


def read_fixture(name: str) -> str:
    """读取夹具原文。

    Raises:
        ProtocolError: 夹具名不在白名单内，或文件缺失。
    """
    if name not in FIXTURE_NAMES:
        msg = f"未知夹具：{name}（允许：{FIXTURE_NAMES}）"
        raise ProtocolError(msg)
    path = TASKS_DIR / f"{name}.txt"
    if not path.is_file():
        msg = f"夹具文件缺失：{path}"
        raise ProtocolError(msg)
    return path.read_text(encoding="utf-8")


def build_prompts() -> dict[str, str]:
    """构造三个任务的提示词（与三档对比实验的措辞一致，便于对照）。"""
    return {
        "t1": (
            "以下是一个 Python 模块的完整内容。请为其中所有函数补全完整的类型标注"
            "（参数与返回值），保持行为完全不变。只输出修改后的完整文件内容，"
            "放在一个 ```python 代码块中，不要任何解释。\n\n"
            "```python\n" + read_fixture("t1_input.py") + "```\n"
        ),
        "t2": (
            "以下是一个 Python 模块与它的测试文件。测试当前是失败的。"
            "请修复模块中的缺陷，使全部测试通过。只输出修复后的完整模块内容，"
            "放在一个 ```python 代码块中，不要任何解释。\n\n"
            "模块 t2_bug.py：\n```python\n" + read_fixture("t2_bug.py") + "```\n\n"
            "测试 t2_test.py：\n```python\n" + read_fixture("t2_test.py") + "```\n\n"
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
            "```python\n" + read_fixture("t3_pure.py") + "```\n"
        ),
    }


# ---------------------------------------------------------------------------
# 运行参数
# ---------------------------------------------------------------------------

ISOLATION_MODES: tuple[str, ...] = ("user", "root")


@dataclass(frozen=True)
class RunParams:
    """一轮基准的全部可变参数（默认值即"夜轮"配置）。"""

    ctx: int = 4096
    threads: int = 8
    repeats: int = 10
    tiers: tuple[str, ...] = TIERS
    host: str = LOOPBACK_HOST
    port: int = DEFAULT_PORT
    max_tokens: int = 2048
    request_timeout_s: float = 1800.0
    ready_timeout_s: float = 300.0
    label: str = "nightly"
    isolation: str = "user"

    def validate(self) -> None:
        """校验参数，非法即中止（fail-secure）。

        Raises:
            ProtocolError: 任一参数越界或组合不合法。
        """
        if self.host != LOOPBACK_HOST:
            msg = f"只允许回环地址 {LOOPBACK_HOST}，当前为 {self.host}（不得对外监听）"
            raise ProtocolError(msg)
        if not 1 <= self.port <= 65535:
            msg = f"端口越界：{self.port}"
            raise ProtocolError(msg)
        if self.ctx < 512:
            msg = f"上下文过小：{self.ctx}"
            raise ProtocolError(msg)
        if self.threads < 1:
            msg = f"线程数非法：{self.threads}"
            raise ProtocolError(msg)
        if self.repeats < MIN_REPEATS:
            msg = f"重复次数 {self.repeats} 少于最小值 {MIN_REPEATS}：单次采样不得用于判据"
            raise ProtocolError(msg)
        if self.max_tokens < 64:
            msg = f"max_tokens 过小：{self.max_tokens}"
            raise ProtocolError(msg)
        if not self.tiers:
            msg = "至少需要指定一个档位"
            raise ProtocolError(msg)
        unknown = [tier for tier in self.tiers if tier not in TIERS]
        if unknown:
            msg = f"未知档位：{unknown}（允许：{TIERS}）"
            raise ProtocolError(msg)
        if self.isolation not in ISOLATION_MODES:
            msg = f"未知隔离模式：{self.isolation}（允许：{ISOLATION_MODES}）"
            raise ProtocolError(msg)
        if not self.label or not all(char.isalnum() or char in "-_" for char in self.label):
            msg = f"标签只允许字母、数字、连字符与下划线：{self.label!r}"
            raise ProtocolError(msg)

    def server_argv(self, model_path: pathlib.Path) -> list[str]:
        """llama-server 的启动参数。

        ``-np 1`` 是本协议相对旧口径的**唯一**变化：服务端不再开 4 个槽位与共享 KV
        （V-14 实测：未指定时默认 ``n_slots = 4, kv_unified = 'true'``）。
        其余参数与三档对比实验保持一致。
        """
        return [
            "llama-server",
            "-m",
            str(model_path),
            "-c",
            str(self.ctx),
            "-t",
            str(self.threads),
            "-tb",
            str(self.threads),
            "-np",
            "1",
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--reasoning",
            "off",
            "--no-webui",
            "--metrics",
        ]

    def to_json(self) -> dict[str, object]:
        """记录到 ``env.json`` 的参数指纹。"""
        return {
            "ctx": self.ctx,
            "threads": self.threads,
            "repeats": self.repeats,
            "tiers": list(self.tiers),
            "host": self.host,
            "port": self.port,
            "max_tokens": self.max_tokens,
            "label": self.label,
            "isolation": self.isolation,
        }


def model_path_for(tier: str, model_dir: pathlib.Path) -> pathlib.Path:
    """返回档位对应的模型文件路径。

    Raises:
        ProtocolError: 档位未知。
    """
    filename = MODELS.get(tier)
    if filename is None:
        msg = f"未知档位：{tier}"
        raise ProtocolError(msg)
    return model_dir / filename
