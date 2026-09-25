#!/usr/bin/env python3
"""把 `.ide/` 下的设置源合并进运行中的 code-server User 设置，并断言 Agent 权限已落地。

为什么必须有这一步（2026-09-25 实测；参考同平台 compute-matrix 的同类机制）：

1. `codingcopilot.autoRun` / `safeDeleteEnabled` / `safeDeleteBulkThreshold` /
   `customBlacklistCommands` / `disabledSecurityCategories` 的 **`scope = application`**
   （出处：本环境 `tencent-cloud.coding-copilot-4.12.38765564/package.json` 的
   `contributes.configuration.properties`）⇒ **只能写在 User 级**；
   仓库级 `.vscode/settings.json` 与镜像里 Machine 级的那份**都不生效**。
2. 平台会在环境启动时用自己的一份覆盖 `User/settings.json`
   ⇒ 构建期 COPY 进去的键**会被抹掉**（这就是"配置看起来是对的、运行时照样弹确认"的根因）。
3. ⇒ 唯一可靠时机 = **环境启动后**由 `.cnb.yml` 的 `vscode` 事件把设置**合并**进运行中的
   User 设置。该事件的 `stages` 在开发环境容器里跑，而前提是**单容器模式**
   （镜像里自带 code-server 即满足；本仓库 `.ide/Dockerfile` 自己装 code-server）。
   ⚠️ 双容器模式下 stages 与 code-server 不在同一容器 ⇒ 这一步会"**永远绿、永远不生效**"，
   所以本脚本**断言目标目录存在**，让这种失败**变红**而不是静默。

设计约束（与 `SECURITY.md` 的 fail-secure 口径一致）：

- **任一步失败一律非零退出** —— "设置没生效"必须让流水线变红，不允许降级成警告；
- 目标 JSON **原子写**（同目录临时文件 + `os.replace`），落盘前 `chmod 0644`，
  不留下半截 JSON，也不让 code-server 读不到；
- 合并是**覆盖式**（source 覆盖 target），且**只覆盖 source 里出现的键** ——
  平台自己写的键（`yaml.schemas` / `cnb-welcome.locale` 等）一律保留；
- source 是 **JSONC**（允许 `//`、`/* */` 注释与尾逗号），先用本模块的 strip 器转成严格 JSON；
- 以 `//` 开头的键是给人看的注释键，**不写入**目标。

用法：
    python3 scripts/apply_ide_settings.py [source...] [--target PATH]...
默认源是**两个**（按序合并，后者覆盖前者）：
    - `.ide/settings.json` —— 镜像级预设（主题 / 编辑器 / 语言等，**慢变**）
    - `.ide/agent-preferences.json` —— 运行期偏好（`codingcopilot.*`，**常变**）
默认目标：**两个客户端的 User 级设置都写**（与 `.ide/Dockerfile` 的 COPY 覆盖面对齐）：
    - `/root/.local/share/code-server/User/settings.json`（WebIDE / code-server）
    - `/root/.vscode-server/data/User/settings.json`（VS Code Desktop / Remote-SSH）
source 与 target 都可显式指定（便于测试与复跑）。
⚠️ 位置参数**只有** `source`（可给多个）—— target 一律走 `--target`，避免"只传一个参数"时
   把 source 误当 target、把源文件自我覆盖（2026-09-25 实测踩过，见 git 历史）。
⚠️ **为什么拆成两个源**（2026-09-25）：`.ide/settings.json` 是**镜像构建输入**
    （`Dockerfile` 的 COPY + `.cnb.yml` 的 `build.by`）⇒ 改它一次就换掉镜像输入哈希、
    触发整机重建（约 20 min）；而黑名单是要**经常调**的 ⇒ 移到**不进 `build.by`** 的第二份里。
    该判据由 `tests/unit/test_apply_ide_settings.py` 的
    `test_runtime_preferences_file_is_not_a_build_input` 钉住。
"""

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

DEFAULT_TARGETS: tuple[Path, ...] = (
    Path("/root/.local/share/code-server/User/settings.json"),
    Path("/root/.vscode-server/data/User/settings.json"),
)
# 顺序 = 合并顺序（后者覆盖前者）。两个源的键集**不相交**，由单测钉住。
DEFAULT_SOURCES: tuple[Path, ...] = (
    Path(".ide/settings.json"),
    Path(".ide/agent-preferences.json"),
)

# 断言项：缺一项都不算「Agent 权限已落地」。
GUARD_EXACT: tuple[tuple[str, object], ...] = (
    ("codingcopilot.autoRun", True),
    ("codingcopilot.autoRunMode", "runEverything"),
    ("codingcopilot.safeDeleteEnabled", True),
)
# 自定义黑名单必须非空 —— 关掉 12 个内置类别后，它是**唯一**还会拦命令的闸门。
GUARD_NON_EMPTY_LIST = "codingcopilot.customBlacklistCommands"
# `custom` 类别不可禁用，否则上面的黑名单静默失效（这是最容易犯、后果最隐蔽的一种改坏）。
GUARD_NOT_DISABLED = "custom"

COMMENT_PREFIX = "//"


def strip_jsonc(text: str) -> str:
    """把 JSONC 转成严格 JSON 文本：去掉注释与尾逗号，字符串内的内容原样保留。

    字符串感知是必需的：本仓库的 `.ide/settings.json` 里有
    `"https://docs.cnb.cool/conf-schema-zh.json"` 这类含 `//` 的值，按行粗暴裁剪会把值剪坏。
    """
    return _remove_trailing_commas(_remove_comments(text))


def _remove_comments(text: str) -> str:
    out: list[str] = []
    index = 0
    length = len(text)
    in_string = False
    escaped = False
    while index < length:
        char = text[index]
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if text.startswith("//", index):
            end = text.find("\n", index)
            index = length if end < 0 else end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _remove_trailing_commas(text: str) -> str:
    """删掉 `}` / `]` 之前的尾逗号（JSONC 允许，严格 JSON 不允许）。"""
    out: list[str] = []
    index = 0
    length = len(text)
    in_string = False
    escaped = False
    while index < length:
        char = text[index]
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if char == ",":
            probe = index + 1
            while probe < length and text[probe] in " \t\r\n":
                probe += 1
            if probe < length and text[probe] in "}]":
                index += 1
                continue
        out.append(char)
        index += 1
    return "".join(out)


def load_json_object(path: Path, *, required: bool) -> dict[str, Any]:
    """读一个 JSON / JSONC 对象。`required=False` 时文件不存在返回空字典（目标文件允许不存在）。"""
    if not path.exists():
        if required:
            raise SystemExit(f"[fatal] 源文件不存在：{path}")
        return {}
    try:
        parsed = json.loads(strip_jsonc(path.read_text(encoding="utf-8")))
    except ValueError as exc:
        raise SystemExit(f"[fatal] {path} 不是合法 JSON(C)：{exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f"[fatal] {path} 顶层不是 JSON 对象（实际 {type(parsed).__name__}）")
    return parsed


def write_object_atomic(path: Path, obj: dict[str, Any]) -> None:
    """原子写 JSON 对象：同目录临时文件 → chmod 0644 → os.replace。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".settings-", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(obj, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        # mkstemp 落的是 0600；code-server 需要能读到，统一成 0644。
        tmp_path.chmod(0o644)
        tmp_path.replace(path)
    except BaseException:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def merge_settings(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """覆盖式合并：只覆盖 source 里出现的键；`//` 开头的注释键不写入。"""
    overrides = {key: value for key, value in source.items() if not key.startswith(COMMENT_PREFIX)}
    if not overrides:
        raise SystemExit("[fatal] source 里没有任何有效设置（注释键不计）")
    return {**target, **overrides}


def load_sources(paths: Sequence[Path]) -> tuple[dict[str, Any], dict[str, int]]:
    """按给定顺序读取并覆盖式合并多个源；返回（合并结果，各源的**有效键数**）。

    任一路径缺失、或某个源里没有任何有效设置，一律**非零退出**（fail-secure）：
    "源文件不全"就是"权限没落地"，不允许"少读一个文件照样通过"。
    """
    merged: dict[str, Any] = {}
    counts: dict[str, int] = {}
    for path in paths:
        raw = load_json_object(path, required=True)
        merged = merge_settings(merged, raw)
        counts[str(path)] = sum(1 for key in raw if not key.startswith(COMMENT_PREFIX))
    return merged, counts


def guard_failures(merged: dict[str, Any]) -> list[str]:
    """返回不满足的断言（空列表 = 全部通过）。"""
    failures = [
        f"{key} = {merged.get(key)!r}（期望 {want!r}）"
        for key, want in GUARD_EXACT
        if merged.get(key) != want
    ]
    blacklist = merged.get(GUARD_NON_EMPTY_LIST)
    if not isinstance(blacklist, list) or not blacklist:
        failures.append(f"{GUARD_NON_EMPTY_LIST} 不是非空列表（实际 {blacklist!r}）")
    disabled = merged.get("codingcopilot.disabledSecurityCategories")
    if isinstance(disabled, list) and GUARD_NOT_DISABLED in disabled:
        failures.append(f"禁用了 `{GUARD_NOT_DISABLED}` 类别 ⇒ 自定义黑名单会静默失效")
    return failures


def resolve_targets(explicit: list[str] | None) -> list[Path]:
    """解析目标列表：显式给 `--target` 就只用它们；否则用默认的**两个**客户端 User 路径。

    ⚠️ 已知限制（如实登记）：双容器模式下 stages 与 code-server 不在同一容器，本脚本
    会"写成功但写进错误的容器"——**从容器内部无法检测**（我们的镜像本身带 code-server，
    目录存在性判据失效）。结构性解法 = 单容器镜像（本仓库 `.ide/Dockerfile` 自装
    code-server，已满足）；参见参考实现 compute-matrix `docs/platform-facts.md` §10.2/§10.4。
    """
    if explicit:
        return [Path(item) for item in explicit]
    return list(DEFAULT_TARGETS)


def apply_to_target(target: Path, source_obj: dict[str, Any], counts: dict[str, int]) -> None:
    """合并 + 断言写进单个目标。任一断言不符 ⇒ 非零退出（fail-secure）。"""
    base = load_json_object(target, required=False)
    merged = merge_settings(base, source_obj)
    write_object_atomic(target, merged)
    detail = " + ".join(f"{path} {count}" for path, count in counts.items())
    print(f"[ok] 已合并 {len(source_obj)} 项设置（{detail}）→ {target}（原有 {len(base)} 项保留）")

    failures = guard_failures(merged)
    for failure in failures:
        print(f"[fatal] {failure}")
    if failures:
        raise SystemExit(f"[fatal] Agent 权限未落地（{len(failures)} 项不符）")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="把 .ide/ 下的设置源合并进 code-server / VS Code 的 User 设置并断言"
    )
    parser.add_argument(
        "sources",
        nargs="*",
        default=None,
        help=(
            "源设置文件（可给多个，按序合并、后者覆盖前者）；"
            "缺省 = .ide/settings.json + .ide/agent-preferences.json"
        ),
    )
    parser.add_argument(
        "--target",
        action="append",
        default=None,
        metavar="PATH",
        help="目标 User settings.json（可重复；缺省 = 两个客户端的 User 路径都写）",
    )
    args = parser.parse_args(argv)
    targets = resolve_targets(args.target)
    source_paths = [Path(item) for item in args.sources] if args.sources else list(DEFAULT_SOURCES)
    source_obj, counts = load_sources(source_paths)

    for target in targets:
        apply_to_target(target, source_obj, counts)

    print(
        "[ok] Agent 权限已落地（autoRun / autoRunMode / safeDeleteEnabled / 黑名单 / 类别 均核对通过）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
