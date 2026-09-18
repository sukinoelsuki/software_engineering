"""结构化日志装配与脱敏管线（``REQ-OBS-01`` / ``REQ-SEC-07``）。

由 ``structlog`` 承担（ADR-0015 §5.2.5 行 E1）：它的 **processor 管线**天然适配
"**先脱敏、再输出**"的顺序，而脱敏正是安全需求（``REQ-SEC-07``：敏感信息不入日志）。

三条设计取舍（写的是"为什么"，不是"是什么"）：

1. **与 stdlib ``logging`` 共用一条输出管线**：``structlog.stdlib.LoggerFactory`` +
   ``ProcessorFormatter`` ⇒ 结构化日志与 ``bench/`` 等既有的 ``logging.getLogger(...)``
   调用点走**同一个 handler**，不会出现"两种格式混在一个 stdout 里"。
   代价是必须给"外来记录"单独配一条 ``foreign_pre_chain``（structlog 只对自家记录跑
   主 processor 链），否则**绕过 structlog 直接 ``logging``** 就成了脱敏的漏洞。
2. **脱敏做**宽**匹配**（键名子串、大小写不敏感）：漏脱一个密钥的代价（凭据外泄）
   远高于多脱一个普通字段（如 ``keyword``）；因此宁可过脱，**不**做"值看起来像密钥"的
   内容启发式——后者会漏掉真正的密钥，制造"以为脱过"的假象。
3. **``cache_logger_on_first_use=False``**：``configure_logging()`` 必须能在同一进程内
   **重复调用且立即生效**（CLI 重配与测试都是这个用法）；缓存会让先前的 logger 继续用旧配置，
   产生"日志不知道去哪了"这类只能靠猜的排查陷阱。

本模块不读凭据、不自行写终端（写到哪由 handler 决定），只依赖标准库与 ``structlog``
（ADR-0015 §7.1 R1：``foundation/`` 不得依赖业务包）。
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping
from typing import IO, cast

import structlog
from structlog.stdlib import BoundLogger
from structlog.typing import EventDict, WrappedLogger

#: 命中敏感键名时替换成的占位文本（固定值，便于测试与肉眼检索）。
REDACTED = "[REDACTED]"

#: 敏感键名标记：键名**子串**命中即整值脱敏。``key`` 一项即覆盖 ``api_key`` /
#: ``private_key`` / ``apikey`` 等写法；其余项为便于检索与审计而显式列出。
SENSITIVE_KEY_MARKERS: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "authorization",
    "cookie",
    "private_key",
    "api_key",
    "apikey",
    "key",
)

#: 打在自建 handler 上的标记属性：``configure_logging()`` 只回收**自己**装过的 handler，
#: 不动调用方（或 ``bench/``）已装的 handler。
_HANDLER_ATTR = "_lowspec_structured_handler"

__all__ = [
    "REDACTED",
    "SENSITIVE_KEY_MARKERS",
    "configure_logging",
    "get_logger",
    "is_sensitive_key",
    "redact_event",
    "redact_sensitive",
    "sanitize_for_display",
]


def is_sensitive_key(key: object) -> bool:
    """键名是否命中敏感标记（子串、大小写不敏感）。

    非 ``str`` 键先转成文本再判断——日志事件的键来自 ``**kwargs``，不保证是 ``str``。
    """
    name = str(key).lower()
    return any(marker in name for marker in SENSITIVE_KEY_MARKERS)


def redact_sensitive(value: object) -> object:
    """递归脱敏：命中敏感键名 ⇒ 整值替换为 :data:`REDACTED`，其余递归下探。

    只按**键名**判定（理由见模块 docstring 第 2 条）。映射返回**新的** ``dict``，
    不改动入参；列表 / 元组保持顺序，元素逐个处理。
    """
    if isinstance(value, Mapping):
        return {
            key: REDACTED if is_sensitive_key(key) else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact_sensitive(item) for item in value]
    return value


def sanitize_for_display(text: str, *, limit: int = 64) -> str:
    """把**不可信**文本压成"可以安全放进一行日志 / 终端"的形式。

    两件事，各有明确对手：

    * **控制字符（含换行、回车、ESC）替换为 ``?``**：它们能被用来伪造日志行（log injection），
      或注入 ANSI 转义序列篡改终端显示——安全层要把工具名一类"来自模型输出"的文本展示给用户
      （``REQ-UX-02``），展示前必须先净化；
    * **超长截断并加省略号**：防止一条理由被塞进海量文本。

    只做**展示层**净化：审计与日志的原始字段不改（那里的转义由 JSON 渲染负责），
    否则证据会被改写。
    """
    stripped = "".join(char if char.isprintable() else "?" for char in text)
    if len(stripped) <= limit:
        return stripped
    return stripped[:limit] + "…"


def redact_event(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """structlog processor：对事件字典整体做一次递归脱敏。

    必须排在渲染类 processor（如 ``JSONRenderer``）**之前**：脱敏要发生在"值被写成文本"
    之前，否则替换的是已渲染好的字符串，等于没脱。
    """
    del logger, method_name  # processor 协议要求的形参，本 processor 不使用
    return cast("EventDict", redact_sensitive(dict(event_dict)))


def configure_logging(*, stream: IO[str] | None = None, level: int | str = logging.INFO) -> None:
    """装配日志管线：JSON 输出 + 脱敏，structlog 与 stdlib ``logging`` 共用。

    Args:
        stream: 输出流；默认 ``sys.stdout``。之所以可注入：非交互模式要另写一份 JSON 流，
            测试要拿到确定性输出——两者都是真实用法，不是测试专用分支。
        level: 根 logger 级别；接受 ``logging`` 常量或名称（如 ``"INFO"``），
            取值口径与 ``foundation.config.LoggingConfig.level`` 一致。

    **幂等**：重复调用只替换本函数此前装过的 handler（标记见 :data:`_HANDLER_ATTR`），
    既不叠加重复输出，也不移除调用方自己装的 handler。
    """
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            # 外来（stdlib logging）记录的补齐与脱敏：两条支路都必须脱敏。
            foreign_pre_chain=[
                structlog.stdlib.add_log_level,
                structlog.stdlib.add_logger_name,
                structlog.processors.format_exc_info,
                redact_event,
            ],
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(sort_keys=True),
            ],
        )
    )
    setattr(handler, _HANDLER_ATTR, True)

    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, _HANDLER_ATTR, False):
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            redact_event,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> BoundLogger:
    """取一个结构化 logger。

    ``structlog.get_logger`` 的签名返回 ``Any``（它对参数不做约束），因此这里 ``cast``
    到装配时使用的 wrapper 类型——否则 ``mypy --strict`` 的 ``warn_return_any`` 会报错，
    且调用点会丢掉全部类型检查。
    """
    if name is None:
        return cast("BoundLogger", structlog.get_logger())
    return cast("BoundLogger", structlog.get_logger(name))
