"""异常层次的契约：模型异常归 ``foundation/``，且不与基准异常合并。

依据 ``docs/design/interfaces/model.md`` §3（Q4）：两个模型异常都必须是 ``BenchError``
的（直接）子类，且**不得**继承 ``ProtocolError``——两者处置相反（重试/降级 vs 中止），
合并会让同一个 ``except`` 误捕。
"""

from __future__ import annotations

import pytest

from agent_sec_perf.foundation.errors import (
    BenchError,
    ModelProtocolError,
    ModelUnavailableError,
    ProtocolError,
)


@pytest.mark.unit
def test_model_errors_are_bench_errors() -> None:
    """两个模型异常必须落在单一异常层次内（否则 ``except BenchError`` 会漏接）。"""
    assert issubclass(ModelUnavailableError, BenchError)
    assert issubclass(ModelProtocolError, BenchError)


@pytest.mark.unit
def test_model_errors_do_not_inherit_protocol_error() -> None:
    """不得继承 ProtocolError：处置不同，合并会让同一个 ``except`` 误捕。"""
    assert not issubclass(ModelUnavailableError, ProtocolError)
    assert not issubclass(ModelProtocolError, ProtocolError)
