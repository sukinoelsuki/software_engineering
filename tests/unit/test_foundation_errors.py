"""异常层次的契约：模型异常归 ``foundation/``，且不与基准异常合并。

依据 ``docs/design/interfaces/model.md`` §3（Q4）：两个模型异常都必须是 ``BenchError``
的（直接）子类，且**不得**继承 ``ProtocolError``——两者处置相反（重试/降级 vs 中止），
合并会让同一个 ``except`` 误捕。

同理，``ConfigError``（``ADR-0015`` §5.1.2 的边界表里唯一允许冒泡到 CLI 的异常）
也**只有一处定义**：在 ``foundation/errors.py``。同名异常定义两处 = 漂移源。
"""

from __future__ import annotations

import pytest

from agent_sec_perf.foundation import config as config_module
from agent_sec_perf.foundation.errors import (
    BenchError,
    ConfigError,
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


@pytest.mark.unit
def test_config_error_lives_in_the_shared_hierarchy_once() -> None:
    """``ConfigError`` 在共享层次内，且 ``config`` 模块暴露的是**同一个类**（无第二处定义）。"""
    assert issubclass(ConfigError, BenchError)
    assert config_module.ConfigError is ConfigError
