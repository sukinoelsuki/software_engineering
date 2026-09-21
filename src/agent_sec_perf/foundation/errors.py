"""全项目共享的异常层次。

由 ``bench/errors.py`` 提升而来（``docs/adr/0015-layering-and-reuse-boundary.md``
§5.4.1）；基类 :class:`BenchError` 沿用历史名称，语义上已是全项目的基类异常。

设计原则：**失败一律显式抛出，禁止静默降级**（fail-secure）。
基准确认数据是否可比，一旦参数或路径可疑，宁可整轮失败，也不要产出一份
看起来正常、实际不可比的数据——后者会污染整条时间序列，代价远高于重跑一轮。
"""

from __future__ import annotations


class BenchError(Exception):
    """全项目基类异常（由基准流水线提升为共享层次，名称沿用历史）。"""


class PathNotAllowedError(BenchError):
    """路径不在允许的根目录内（防目录穿越与误指向）。"""


class ProtocolError(BenchError):
    """参数或协议不符合约定——会让数据不可比，必须中止。"""


class SchemaError(BenchError):
    """产出数据不符合 schema：拒绝入库。"""


class IsolationError(BenchError):
    """隔离要求无法满足（例如无法切换到非特权用户）。

    这种情况**不得**回退到普通执行：那等于把"隔离失败"伪装成"隔离成功"。
    """


class ModelUnavailableError(BenchError):
    """模型后端不可达 / 未就绪 / 已达重试上限（``docs/design/interfaces/model.md`` §3）。

    调用方处置：**路由降级**到另一后端（``REQ-MODEL-05``）。
    刻意**不**继承 :class:`ProtocolError`：后者语义是"基准参数/协议不可比"，处置是**中止**；
    合并会让两种不同处置被同一个 ``except`` 捕获。
    """


class ModelProtocolError(BenchError):
    """模型响应不符合契约（缺 ``choices``、``content`` 与 ``tool_calls`` 皆空等）。

    调用方处置：**重试一次**，仍失败则回喂给模型（``REQ-HARNESS-02``）。
    同样**不**继承 :class:`ProtocolError`，理由同上。
    """


class ConfigError(BenchError):
    """配置非法（fail-secure：不得捕获后继续用默认值）；实现见 ``foundation/config.py``。"""


class AuditWriteError(BenchError):
    """**审计写入失败**（证据面损坏）：落盘或 ``fsync`` 失败（磁盘满 / 权限 / 只读挂载）。

    ⚠️ 它与"任务失败"是**两类事件**，不得同形：

    * **本异常** = **证据面损坏**——任务可能其实成功了，但**我们没留下痕迹**；
    * "任务失败" = 业务路径真的没走通。

    同形的代价（``docs/design/threat-model/README.md`` §8.2 的 ``P-3``）：
    消费者会把"证据面坏了"读成"任务失败" ⇒ 排查方向被带偏（去查任务逻辑而不是磁盘），
    且**落盘的事件流会永久地把这次事故记成"任务失败"**——证据文件自己不知道自己是坏的。

    刻意**不**继承 :class:`ProtocolError`：后者语义是"参数/协议不可比"，处置是**中止**；
    本异常的处置是**让异常真的冒泡出 ``run()``**（契约 ``audit.md`` §2.4：
    "``emit`` / ``flush`` 失败必须冒泡；禁止吞异常或降级为告警"）。

    底层原因保留在 ``__cause__``（磁盘满 / 权限 / 只读挂载的具体 ``OSError``）。
    """


class ToolArgumentsInvalidError(BenchError):
    """工具参数的**信任边界校验**失败（模型给出的 ``arguments_json`` 不合法）。

    与 ``tools/registry.py`` 的 ``ToolArgumentError`` **刻意不合并**，分工见
    ``docs/design/interfaces/harness.md`` §3.4：

    * **本异常** = "不可信输入被拒"——由 HARNESS 侧的 ``ArgumentValidator`` 抛出，
      处置是 ``denied_reason="invalid_arguments"`` ⇒ **回喂模型**；
    * ``ToolArgumentError`` = "已校验参数的调用语义缺陷"——工具实现内部抛出，
      处置是 ``ToolResult(ok=False)``。

    两者都要保留：删掉入口会让非法值以更晚、更隐蔽的形态出现；删掉兜底则让工具
    依赖"上游一定校验过"。

    刻意**不**继承 :class:`ProtocolError`：后者的处置是**中止**，本异常的处置是
    **回喂**（把失败当数据交回模型继续，``REQ-HARNESS-06``）——合并会让同一个
    ``except`` 误捕两种相反处置。
    """
