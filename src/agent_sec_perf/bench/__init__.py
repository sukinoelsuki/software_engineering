"""基准自动化子系统：协议、测量、判定、报告与数据落盘。

定位与决策见 ``docs/adr/0014-benchmark-automation.md``，运行方式见
``docs/engineering/benchmark-automation.md``。

模块划分（按数据流）：

``protocol`` → 协议常量、任务集与参数校验（数据可比性的锚点）
``assets``   → 资产清单解析（sha256 的唯一真源）
``runner``   → llama-server 生命周期与服务端计时采集（子进程经 ``foundation.proc``）
``evaluate`` → 模型产物的客观判定（不可信边界所在）
``stats``    → 重复测量统计（单次采样不得用于判据）
``store``    → schema 校验、落盘与索引（入库前的 fail-secure 闸门）
``report``   → 人读报告与可比性判定
``rounds``   → 一轮的编排与命令行入口

共享地基（``proc`` / ``paths`` / ``errors``）已提升至 ``agent_sec_perf.foundation``
（ADR-0015 §5.4.1），本子系统与产品层共用同一份实现。
"""

from __future__ import annotations

from agent_sec_perf.foundation.errors import BenchError

__all__ = ["BenchError"]
