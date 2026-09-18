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
