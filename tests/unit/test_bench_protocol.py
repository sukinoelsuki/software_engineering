"""协议、夹具、资产清单与路径白名单。

这些约束的作用是：**让"不可比"或"不安全"的参数尽早失败**，而不是等到数据
已经入库、报告已经生成之后才被发现。
"""

from __future__ import annotations

import pathlib

import pytest

from agent_sec_perf.bench.assets import read_model_manifest
from agent_sec_perf.bench.protocol import (
    PROTOCOL_VERSION,
    TASK_IDS,
    TIERS,
    RunParams,
    build_prompts,
    model_path_for,
    read_fixture,
)
from agent_sec_perf.foundation.errors import PathNotAllowedError, ProtocolError
from agent_sec_perf.foundation.paths import resolve_within


@pytest.mark.unit
def test_default_params_are_valid() -> None:
    """默认参数（夜轮配置）应通过校验，且重复次数不少于 3。"""
    params = RunParams()

    params.validate()
    assert params.repeats >= 3
    assert params.isolation == "user"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repeats", 2),
        ("threads", 0),
        ("ctx", 256),
        ("port", 70000),
        ("max_tokens", 16),
    ],
)
def test_out_of_range_params_are_rejected(field: str, value: int) -> None:
    """越界参数不得进入测量。"""
    params = RunParams(**{field: value})

    with pytest.raises(ProtocolError):
        params.validate()


@pytest.mark.unit
def test_non_loopback_host_is_rejected() -> None:
    """只允许监听/连接回环地址：服务器是本地临时进程，不得对外暴露。"""
    params = RunParams(host="0.0.0.0")

    with pytest.raises(ProtocolError):
        params.validate()


@pytest.mark.unit
def test_unknown_tier_and_isolation_are_rejected() -> None:
    """未知档位与未知隔离模式都必须失败。"""
    with pytest.raises(ProtocolError):
        RunParams(tiers=("XL",)).validate()
    with pytest.raises(ProtocolError):
        RunParams(isolation="none").validate()


@pytest.mark.unit
def test_server_argv_pins_slot_count_and_loopback() -> None:
    """启动参数必须显式固定 `-np 1` 并绑定回环地址（V-14 的口径）。"""
    argv = RunParams().server_argv(pathlib.Path("/opt/models/x.gguf"))

    assert "-np" in argv
    assert argv[argv.index("-np") + 1] == "1"
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    assert argv[0] == "llama-server"


@pytest.mark.unit
def test_model_path_for_rejects_unknown_tier() -> None:
    """未知档位不得映射出模型路径。"""
    with pytest.raises(ProtocolError):
        model_path_for("XL", pathlib.Path("/opt/models"))


@pytest.mark.unit
def test_read_fixture_rejects_unknown_name() -> None:
    """夹具读取走白名单，避免任意文件读取。"""
    with pytest.raises(ProtocolError):
        read_fixture("../../etc/passwd")


@pytest.mark.unit
def test_fixtures_are_present_and_intentionally_untyped() -> None:
    """四个夹具都在，且 t1 夹具**刻意**没有类型标注（否则任务就失去意义）。"""
    assert "def summarize_readings(readings" in read_fixture("t1_input.py")
    assert "def chunk(items, size)" in read_fixture("t2_bug.py")
    assert "from t2_bug import chunk" in read_fixture("t2_test.py")
    assert "def merge_intervals(intervals)" in read_fixture("t3_pure.py")


@pytest.mark.unit
def test_prompts_cover_all_tasks_and_embed_fixtures() -> None:
    """提示词要覆盖全部任务，并把夹具原文嵌进去（提示词是协议的一部分）。"""
    prompts = build_prompts()

    assert tuple(prompts) == TASK_IDS
    assert "merge_intervals" in prompts["t3"]
    assert "必须包含必要的 import 语句" in prompts["t3"]
    assert "chunk" in prompts["t2"]


@pytest.mark.unit
def test_protocol_version_is_labelled() -> None:
    """协议版本是可比性的锚点，必须非空且带版本号。"""
    assert PROTOCOL_VERSION.startswith("bench-v")
    assert set(TIERS) == {"S", "M", "L"}


@pytest.mark.unit
def test_resolve_within_accepts_inside_and_rejects_outside(tmp_path: pathlib.Path) -> None:
    """路径必须规范化后落在白名单根内；穿越与外部路径一律拒绝。"""
    root = tmp_path / "models"
    root.mkdir()
    inside = root / "a.gguf"
    inside.write_text("x", encoding="utf-8")

    assert resolve_within(inside, [root], what="模型") == inside.resolve()

    with pytest.raises(PathNotAllowedError):
        resolve_within(root / ".." / "secret.gguf", [root], what="模型")
    with pytest.raises(PathNotAllowedError):
        resolve_within(pathlib.Path("/etc/passwd"), [root], what="模型")


@pytest.mark.unit
def test_manifest_parsing_reads_real_manifest() -> None:
    """仓库内的真实清单应可解析，且包含三档所需模型。"""
    manifest = pathlib.Path(__file__).resolve().parents[2] / ".ide" / "assets" / "models.txt"

    assets = read_model_manifest(manifest)

    for tier in TIERS:
        filename = model_path_for(tier, pathlib.Path()).name
        assert filename in assets, f"{tier} 档所需 {filename} 不在清单中"
        assert len(assets[filename].sha256) == 64


@pytest.mark.unit
def test_manifest_rejects_malformed_digest(tmp_path: pathlib.Path) -> None:
    """摘要格式非法的清单必须被拒绝（不能拿错摘要去比对权重）。"""
    manifest = tmp_path / "models.txt"
    manifest.write_text(
        "deadbeef|a.gguf|https://example.invalid/a.gguf|Q4_K_M|1 GB\n", encoding="utf-8"
    )

    with pytest.raises(ProtocolError):
        read_model_manifest(manifest)


@pytest.mark.unit
def test_manifest_missing_file_is_rejected(tmp_path: pathlib.Path) -> None:
    """清单缺失即失败，不得"猜一个摘要"。"""
    with pytest.raises(ProtocolError):
        read_model_manifest(tmp_path / "missing.txt")
