"""`scripts/apply_ide_settings.py` 的单测。

这组用例存在的理由：该脚本是**环境启动期**唯一把 Agent 权限写进运行中 User 设置的入口，
它若静默失效，表现是"配置看起来是对的、运行时照样弹确认"——正是本项目头号失败模式
（静默失败）的典型形状。因此这里除了测脚本本身，还钉住一条**不变式**：

    `.ide/settings.json`（仓库里那一份）必须能通过脚本的全部断言。

即："改了设置文件却没同步改断言" 或 "把黑名单写空" 这类改动，会在单测里直接变红，
而不是等到下次拉起环境才发现权限没落地。
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "apply_ide_settings.py"
SETTINGS_PATH = REPO_ROOT / ".ide" / "settings.json"


def _load_module() -> Any:
    """加载 `scripts/apply_ide_settings.py`（纯函数模块）。

    路径由本文件位置推导、固定指向仓库内文件，不涉及外部输入；
    返回模块对象故标注为 `Any`（与 `test_release_policy.py` 加载 `release_edit.py` 同一惯例）。
    """
    spec = importlib.util.spec_from_file_location("apply_ide_settings", SCRIPT_PATH)
    assert spec is not None, f"无法为 {SCRIPT_PATH} 构造模块规格"
    assert spec.loader is not None, f"{SCRIPT_PATH} 没有可用的模块加载器"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(name="mod")
def fixture_mod() -> Any:
    return _load_module()


# ---------------------------------------------------------------------------
# 1. JSONC 解析：注释要能被剥掉，字符串里的 `//` 不能被误伤
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_strip_jsonc_removes_comments_but_keeps_slashes_inside_strings(mod: Any) -> None:
    """`.ide/settings.json` 里有 `https://…` 这类值，按行裁剪会把值剪坏（真实风险，非假想）。"""
    text = """{
  // 这是注释
  "yaml.schemas": { "https://docs.cnb.cool/conf-schema-zh.json": ".cnb.yml" },
  /* 块注释也允许 */
  "a": 1
}"""
    parsed = json.loads(mod.strip_jsonc(text))
    assert parsed["yaml.schemas"] == {"https://docs.cnb.cool/conf-schema-zh.json": ".cnb.yml"}
    assert parsed["a"] == 1


@pytest.mark.unit
def test_strip_jsonc_allows_trailing_commas(mod: Any) -> None:
    """JSONC 允许尾逗号，严格 JSON 不允许 —— 剥注释之后必须一并处理。"""
    assert json.loads(mod.strip_jsonc('{"a": [1, 2,],}')) == {"a": [1, 2]}


# ---------------------------------------------------------------------------
# 2. 仓库里那份设置必须真的通过断言（不变式）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_repo_settings_file_satisfies_every_guard(mod: Any) -> None:
    """把 `.ide/settings.json` 当作环境启动时要写进去的那份，逐项核对断言。"""
    source = mod.load_json_object(SETTINGS_PATH, required=True)
    merged = mod.merge_settings({}, source)

    assert merged["codingcopilot.autoRun"] is True
    assert merged["codingcopilot.autoRunMode"] == "runEverything"
    assert merged["codingcopilot.safeDeleteEnabled"] is True
    assert merged["codingcopilot.customBlacklistCommands"], (
        "自定义黑名单不得为空（关了 12 个类别后它是唯一闸门）"
    )
    assert "custom" not in merged["codingcopilot.disabledSecurityCategories"]
    assert mod.guard_failures(merged) == []


@pytest.mark.unit
def test_repo_blacklist_patterns_are_valid_regex(mod: Any) -> None:
    """黑名单是"按正则匹配整条命令"，写坏一条会在运行期抛异常或静默放行。"""
    source = mod.load_json_object(SETTINGS_PATH, required=True)
    patterns = source["codingcopilot.customBlacklistCommands"]
    for pattern in patterns:
        re.compile(pattern)  # 不合法会在这里抛 re.error


@pytest.mark.unit
def test_repo_blacklist_covers_the_irreversible_baseline(mod: Any) -> None:
    """对齐参考实现（compute-matrix）的 5 条不可逆红线：删根 / 强推 / 改写历史 / 格式化 / 写裸盘。"""
    source = mod.load_json_object(SETTINGS_PATH, required=True)
    patterns = [re.compile(p) for p in source["codingcopilot.customBlacklistCommands"]]
    probes = [
        "rm -rf /",
        "git push --force origin develop",
        "git reset --hard HEAD~1",
        "mkfs.ext4 /dev/sdb1",
        "dd if=/dev/zero of=/dev/sda",
    ]
    for probe in probes:
        assert any(pattern.search(probe) for pattern in patterns), (
            f"红线未被任何黑名单条目覆盖：{probe}"
        )


# ---------------------------------------------------------------------------
# 3. 合并语义
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_merge_keeps_platform_keys_and_drops_comment_keys(mod: Any) -> None:
    """平台写在 User settings 里的键必须保留；`//` 开头的注释键不得写入。"""
    target = {"yaml.schemas": {"x": "y"}, "cnb-welcome.locale": "zh-cn"}
    source = {"//": "注释键", "workbench.colorTheme": "Dark Modern"}
    merged = mod.merge_settings(target, source)

    assert merged["yaml.schemas"] == {"x": "y"}
    assert merged["cnb-welcome.locale"] == "zh-cn"
    assert merged["workbench.colorTheme"] == "Dark Modern"
    assert "//" not in merged


@pytest.mark.unit
def test_merge_rejects_source_without_real_settings(mod: Any) -> None:
    with pytest.raises(SystemExit, match="没有任何有效设置"):
        mod.merge_settings({}, {"//": "只有注释"})


# ---------------------------------------------------------------------------
# 4. 断言失败必须非零退出（fail-secure）
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("broken", "reason"),
    [
        ({"codingcopilot.autoRun": False}, "autoRun 被关掉"),
        ({"codingcopilot.safeDeleteEnabled": False}, "回收站防删库被关掉"),
        ({"codingcopilot.customBlacklistCommands": []}, "黑名单为空 ⇒ 没有任何闸门"),
        (
            {"codingcopilot.disabledSecurityCategories": ["custom"]},
            "禁用了 custom 类别 ⇒ 黑名单失效",
        ),
    ],
)
def test_guard_failures_detects_broken_configuration(
    mod: Any, broken: dict[str, Any], reason: str
) -> None:
    merged = {
        "codingcopilot.autoRun": True,
        "codingcopilot.autoRunMode": "runEverything",
        "codingcopilot.safeDeleteEnabled": True,
        "codingcopilot.customBlacklistCommands": ["rm\\s+-rf\\s+/"],
        "codingcopilot.disabledSecurityCategories": ["injection", "custom"],
    }
    merged.update(broken)
    if broken.get("codingcopilot.customBlacklistCommands") == []:
        merged["codingcopilot.customBlacklistCommands"] = []
    assert mod.guard_failures(merged), reason


@pytest.mark.unit
def test_main_returns_zero_and_writes_merged_file(mod: Any, tmp_path: Path) -> None:
    """端到端：目标里已有的平台键保留、设置被写入、退出码 0、落盘为合法 JSON。"""
    target = tmp_path / "User" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps({"cnb-welcome.locale": "zh-cn"}, ensure_ascii=False), encoding="utf-8"
    )

    assert mod.main([str(SETTINGS_PATH), "--target", str(target)]) == 0

    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["cnb-welcome.locale"] == "zh-cn"
    assert written["codingcopilot.autoRun"] is True
    assert oct(target.stat().st_mode & 0o777) == "0o644"


# ---------------------------------------------------------------------------
# 6. 默认目标 = 两个客户端的 User 路径（WebIDE 与 Remote-SSH 都要覆盖）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_writes_every_default_target(
    mod: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缺省时不只写一个目标：两个客户端的 User 设置都要落地（否则 Remote-SSH 侧静默不生效）。"""
    first = tmp_path / "code-server" / "User" / "settings.json"
    second = tmp_path / "vscode-server" / "data" / "User" / "settings.json"
    monkeypatch.setattr(mod, "DEFAULT_TARGETS", (first, second))

    assert mod.main([str(SETTINGS_PATH)]) == 0

    for target in (first, second):
        written = json.loads(target.read_text(encoding="utf-8"))
        assert written["codingcopilot.autoRun"] is True


@pytest.mark.unit
def test_main_creates_missing_parent_directories_for_explicit_target(
    mod: Any, tmp_path: Path
) -> None:
    """`--target` 指向尚不存在的客户端目录 ⇒ 自动建目录（首次 Remote-SSH 连接前的场景）。"""
    target = tmp_path / "vscode-server" / "data" / "User" / "settings.json"
    assert mod.main([str(SETTINGS_PATH), "--target", str(target)]) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["codingcopilot.autoRun"] is True


@pytest.mark.unit
def test_main_never_writes_the_source_file_itself(mod: Any, tmp_path: Path) -> None:
    """回归（2026-09-25 实测事故）：单位置参数曾被当成 target，把源文件自我覆盖、注释丢失。"""
    before = SETTINGS_PATH.read_text(encoding="utf-8")
    assert mod.main([str(SETTINGS_PATH)]) == 0
    assert SETTINGS_PATH.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# 5. 「设置文件」与「启动期 stage」必须成对存在（否则静默失效）
# ---------------------------------------------------------------------------


def _vscode_event_block() -> list[str]:
    """取出 `.cnb.yml` 里 `vscode:` 事件的文本块（到下一个同级事件键为止）。

    刻意按行处理而不解析 YAML：本仓库的 `.cnb.yml` 含平台自定义标签 `!reference`，
    解析会引入一个与本题无关的依赖（`tests/unit/test_cnb_config.py` 出于同一原因也按行断言）。
    """
    lines = (REPO_ROOT / ".cnb.yml").read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines) if line.strip() == "vscode:")
    block: list[str] = []
    for line in lines[start + 1 :]:
        # 同级事件键形如 `  push:`（2 空格缩进 + 字母开头 + 冒号结尾）
        if re.match(r"^ {2}[A-Za-z_][\w.-]*:$", line):
            break
        block.append(line)
    assert block, "没有取到 vscode 事件的正文"
    return block


@pytest.mark.unit
def test_vscode_event_applies_settings_at_environment_start() -> None:
    """`codingcopilot.*` 的 `scope = application` ⇒ 只能由环境启动后的 stage 写进 User 设置。

    这条断言把「设置文件 + 启动期合并步骤」钉成一对：删掉 stage（或改名）时设置会
    **静默不生效**——表现是"配置看着是对的、运行时照样弹确认"，正是本项目要防的失败模式。
    """
    block = "\n".join(_vscode_event_block())
    assert "make apply-ide-settings" in block, "vscode 事件缺少把设置合并进 User 设置的 stage"
    assert "- name: agent-permissions" in block, "该 stage 应具名，便于日志定位"


@pytest.mark.unit
def test_makefile_exposes_the_apply_target() -> None:
    """stage 调的是 make 目标 ⇒ 目标必须存在（且脚本可从仓库根直接跑）。"""
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "apply-ide-settings:" in makefile
    assert "scripts/apply_ide_settings.py" in makefile
    assert SCRIPT_PATH.is_file()
