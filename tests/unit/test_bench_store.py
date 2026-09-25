"""轮次数据的 schema 校验与保留策略。

重点覆盖"坏数据必须被拒绝"这条 fail-secure 约束：数据是机器写入、无人复核即入库的，
校验是唯一的闸门。
"""

from __future__ import annotations

import ast
import copy
import pathlib
from typing import Any

import pytest
from conftest import make_record

from agent_sec_perf.bench.rounds import round_dir
from agent_sec_perf.bench.store import (
    append_index,
    load_index,
    load_json,
    merge_index_files,
    prune_daily,
    today_local,
    validate_round,
)
from agent_sec_perf.foundation.errors import SchemaError

#: 生产侧的轮次落盘实现（静态检查用：目录必须由轮次 id 推出，不得按日期命名）
ROUNDS_PY = (
    pathlib.Path(__file__).resolve().parents[2] / "src" / "agent_sec_perf" / "bench" / "rounds.py"
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
def test_merge_index_files_keeps_history(tmp_path: pathlib.Path) -> None:
    """合并索引必须**保留**已发布的轮次——这是"跨夜序列"能累积起来的前提。"""
    published = tmp_path / "published"
    incoming = tmp_path / "incoming"
    append_index(published, {"round_id": "d0"})
    append_index(published, {"round_id": "d1"})
    append_index(incoming, {"round_id": "d2"})

    merged = merge_index_files(published / "index.json", incoming / "index.json")

    assert [entry["round_id"] for entry in merged] == ["d0", "d1", "d2"]
    assert [entry["round_id"] for entry in load_index(published)] == ["d0", "d1", "d2"]


@pytest.mark.unit
def test_merge_index_files_updates_duplicate_round_id(tmp_path: pathlib.Path) -> None:
    """同一轮次重跑时，合并的结果是"更新那一条"，而不是出现两条。"""
    published = tmp_path / "published"
    incoming = tmp_path / "incoming"
    append_index(published, {"round_id": "d1", "status": "partial"})
    append_index(incoming, {"round_id": "d1", "status": "complete"})

    merge_index_files(published / "index.json", incoming / "index.json")

    entries = load_index(published)
    assert len(entries) == 1
    assert entries[0]["status"] == "complete"


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
def test_prune_daily_also_cleans_round_id_named_dirs(tmp_path: pathlib.Path) -> None:
    """轮次目录名带标签（`<日期>-<标签>`）时，保留期**同样**要生效。

    这里是一个"静默失效"的写法：若按 `date.fromisoformat(dirname)` 解析日期，
    带标签的名字会解析失败、被 `continue` 跳过 ⇒ 那些轮次的日志与产物**永远不会被清理**，
    且不报任何错（保留期形同虚设）。故把"带标签的目录也要被清"钉住。
    """
    tagged = tmp_path / "daily" / "2020-01-02-amd64-8"
    (tagged / "logs").mkdir(parents=True)
    (tagged / "logs" / "S.server.log").write_text("old", encoding="utf-8")
    (tagged / "report.md").write_text("keep", encoding="utf-8")

    removed = prune_daily(tmp_path, keep_days=30, subdirs=("logs", "artifacts"))

    assert removed == ["daily/2020-01-02-amd64-8/logs"]
    assert not (tagged / "logs").exists()
    assert (tagged / "report.md").is_file()


@pytest.mark.unit
def test_round_dir_is_keyed_by_round_id_not_by_date(tmp_path: pathlib.Path) -> None:
    """轮次目录 = `daily/<轮次 id>`：同日两轮必须落在**不同**目录（2026-09-25 的覆盖缺陷）。

    反例（按**日期**命名）会让同日第二轮与第一轮共用目录，而 `scripts/bench/publish.sh`
    对目标目录是"先删后拷" ⇒ 第一轮的服务端日志与模型产物被删，且索引里第一轮的
    `report` 仍指向该目录 ⇒ **索引指向错报告、原件永久丢失**。
    发现时数据分支上正有 `2026-09-25-nightly` 一轮（目录内 124 个文件）。
    """
    same_day = ("2026-09-25-nightly", "2026-09-25-amd64-8")
    dirs = [round_dir(tmp_path, round_id) for round_id in same_day]

    assert len(set(dirs)) == len(same_day), "同日两轮的目录不得相同（否则会互相覆盖）"
    for round_id, directory in zip(same_day, dirs, strict=True):
        assert directory.name == round_id, "目录名必须与 round_id 逐字一致"
        # 索引里的 report 由同一个轮次 id 推出 ⇒ 必然落在本轮自己的目录内
        report_rel = (directory.relative_to(tmp_path) / "report.md").as_posix()
        assert report_rel == f"daily/{round_id}/report.md"


@pytest.mark.unit
def test_round_writer_derives_daily_dir_through_round_dir_helper() -> None:
    """`run_round` 的轮次目录必须经 `round_dir()` 构造，不得退回"日期直接拼目录名"。

    为什么还要一条**静态**检查：上面的测试只钉住 `round_dir` 的契约；把调用点改回
    `data_root / DAILY_DIRNAME / date` 时它照样通过（覆盖缺陷原样复活）。
    而 `run_round` 需要 llama-server 与模型（本环境没有），无法用行为测试覆盖；
    这条不变量的失效方式又恰好是**静默的数据丢失**——不是报错，所以必须机器钉住。
    同类做法见 `tests/unit/test_cnb_config.py`（配置/结构性不变量用文本级检查）。
    """
    tree = ast.parse(ROUNDS_PY.read_text(encoding="utf-8"))
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "daily_dir" for target in node.targets
        )
    ]

    assert len(assignments) == 1, "daily_dir 应当只被赋值一次（否则这条检查会失去意义）"
    value = assignments[0].value
    assert isinstance(value, ast.Call) and isinstance(value.func, ast.Name), (
        "daily_dir 必须是函数调用的结果（经 round_dir 构造），而不是直接拼接的路径"
    )
    assert value.func.id == "round_dir", "轮次目录必须经 round_dir() 构造（否则会退回按日期命名）"


@pytest.mark.unit
def test_record_builder_matches_production_shape() -> None:
    """测试构造器本身要与生产记录同形——避免"用生产代码出题"造成的假通过。"""
    record = make_record(status="partial", finish_reasons=["length"])

    validate_round(record)
    assert record["env"]["status"] == "partial"
