"""轮次数据的 schema 校验与保留策略。

重点覆盖"坏数据必须被拒绝"这条 fail-secure 约束：数据是机器写入、无人复核即入库的，
校验是唯一的闸门。
"""

from __future__ import annotations

import copy
import pathlib
from typing import Any

import pytest
from conftest import make_record

from agent_sec_perf.bench.errors import SchemaError
from agent_sec_perf.bench.store import (
    append_index,
    load_index,
    load_json,
    prune_daily,
    today_local,
    validate_round,
)


@pytest.mark.unit
def test_valid_record_passes(valid_record: dict[str, Any]) -> None:
    """完整记录应通过校验。"""
    validate_round(valid_record)


@pytest.mark.unit
def test_missing_protocol_is_rejected(valid_record: dict[str, Any]) -> None:
    """缺少协议版本即拒绝：没有协议就无法判断数据是否可比。"""
    broken = copy.deepcopy(valid_record)
    del broken["env"]["protocol"]

    with pytest.raises(SchemaError):
        validate_round(broken)


@pytest.mark.unit
def test_unknown_status_is_rejected(valid_record: dict[str, Any]) -> None:
    """状态只能是 complete / partial。"""
    broken = copy.deepcopy(valid_record)
    broken["env"]["status"] = "ok"

    with pytest.raises(SchemaError):
        validate_round(broken)


@pytest.mark.unit
def test_inconsistent_spread_is_rejected(valid_record: dict[str, Any]) -> None:
    """极差必须与 min/median/max 自洽——否则报告会拿错数字做判据。"""
    broken = copy.deepcopy(valid_record)
    broken["perf"]["S"]["prefill_tok_per_s"]["spread_pct"] = 99.0

    with pytest.raises(SchemaError):
        validate_round(broken)


@pytest.mark.unit
def test_passed_greater_than_total_is_rejected(valid_record: dict[str, Any]) -> None:
    """通过数不得大于总次数。"""
    broken = copy.deepcopy(valid_record)
    broken["capability"]["S"]["tasks"]["t1"]["passed"] = 99

    with pytest.raises(SchemaError):
        validate_round(broken)


@pytest.mark.unit
def test_empty_perf_is_rejected(valid_record: dict[str, Any]) -> None:
    """没有任何档位数据的记录不得入库。"""
    broken = copy.deepcopy(valid_record)
    broken["perf"] = {}

    with pytest.raises(SchemaError):
        validate_round(broken)


@pytest.mark.unit
def test_short_sha256_is_rejected(valid_record: dict[str, Any]) -> None:
    """模型摘要必须是完整 sha256（否则无法追溯到具体权重）。"""
    broken = copy.deepcopy(valid_record)
    broken["env"]["models"]["S"]["sha256"] = "abc"

    with pytest.raises(SchemaError):
        validate_round(broken)


@pytest.mark.unit
def test_load_json_missing_file_returns_empty(tmp_path: pathlib.Path) -> None:
    """索引不存在时按空处理（首次运行）。"""
    assert load_json(tmp_path / "index.json") == {}
    assert load_index(tmp_path) == []


@pytest.mark.unit
def test_append_index_deduplicates_by_round_id(tmp_path: pathlib.Path) -> None:
    """同一轮次重复写入时只保留最新一份（幂等重跑）。"""
    for index in range(3):
        append_index(tmp_path, {"round_id": f"r{index}"})
    append_index(tmp_path, {"round_id": "r1", "status": "complete"})

    entries = load_index(tmp_path)
    assert len(entries) == 3
    assert entries[-1]["round_id"] == "r1"
    assert entries[-1]["status"] == "complete"


@pytest.mark.unit
def test_append_index_respects_limit(tmp_path: pathlib.Path) -> None:
    """索引超过上限时丢弃最旧的条目。"""
    for index in range(5):
        append_index(tmp_path, {"round_id": f"r{index}"}, limit=3)

    assert [entry["round_id"] for entry in load_index(tmp_path)] == ["r2", "r3", "r4"]


@pytest.mark.unit
def test_prune_daily_keeps_aggregates_and_drops_old_heavy_dirs(tmp_path: pathlib.Path) -> None:
    """保留期只清理日志/产物，聚合数据与报告长期保留。"""
    old_day = tmp_path / "daily" / "2020-01-01"
    (old_day / "logs").mkdir(parents=True)
    (old_day / "logs" / "S.server.log").write_text("old", encoding="utf-8")
    (old_day / "report.md").write_text("keep", encoding="utf-8")

    fresh_day = tmp_path / "daily" / today_local()
    (fresh_day / "logs").mkdir(parents=True)
    (fresh_day / "logs" / "S.server.log").write_text("fresh", encoding="utf-8")

    removed = prune_daily(tmp_path, keep_days=30, subdirs=("logs", "artifacts"))

    assert removed == ["daily/2020-01-01/logs"]
    assert not (old_day / "logs").exists()
    assert (old_day / "report.md").is_file()
    assert (fresh_day / "logs" / "S.server.log").is_file()


@pytest.mark.unit
def test_record_builder_matches_production_shape() -> None:
    """测试构造器本身要与生产记录同形——避免"用生产代码出题"造成的假通过。"""
    record = make_record(status="partial", finish_reasons=["length"])

    validate_round(record)
    assert record["env"]["status"] == "partial"
