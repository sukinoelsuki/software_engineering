#!/usr/bin/env python3
"""生成课程报告用的图片（Graphviz + Pillow）。

为什么单独一个脚本
------------------
报告正文只引用 PNG，图怎么来必须可复现。本脚本把三类图分开：

1. **结构类**（实体关系、数据流、用例、分层、类图、流程图、调用关系）用 Graphviz 的
   `dot` 排版：布局由工具算，脚本里只有节点与边的事实。
2. **终端类**（CLI 输出、提交记录、测试输出）**不画**，而是把命令的真实输出抓下来
   渲染成图片；图里出现的每一行都能用附录 B 的命令复跑。
3. **顺序图** Graphviz 不支持，用 Pillow 按消息序列画。

依赖（仅本机生成图片用，不进 `pyproject.toml`）：Graphviz 的 `dot`、
Noto Sans CJK 与 DejaVu Sans Mono 字体、Pillow。

用法
----
    uv run python docs/course-report/make_figures.py
    uv run python docs/course-report/make_figures.py --only 3-1
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE / "figures"

CJK = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
CJK_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
CJK_INDEX = 2  # NotoSansCJK-Regular.ttc 里 index 2 = 简体中文

G_FONT = "Noto Sans CJK SC"
INK = (32, 32, 32)
GREY = (96, 96, 96)
FILL = "#F7F8FA"
FILL_ALT = "#FBF4E4"
FILL_CTR = "#EAF1F8"
EDGE = "#5A5A5A"


# ============================================================================
# 1. Graphviz 渲染
# ============================================================================


def _say(msg):
    sys.stdout.write(msg + "\n")


def dot(src: str, name: str, engine: str = "dot") -> Path:
    exe = shutil.which(engine)
    if exe is None:
        sys.exit(f"缺少 Graphviz 可执行文件：{engine}")
    out = OUT / name
    # 缩放到 A4 正文宽度后仍能读：统一把图内字号放大三成
    src = re.sub(r"fontsize=(\d+)", lambda m: "fontsize=" + str(round(int(m.group(1)) * 1.3)), src)
    # 参数是常量，不含外部输入
    subprocess.run(  # noqa: S603
        [exe, "-Tpng", "-Gdpi=160", f"-o{out}"],
        input=src.encode("utf-8"),
        check=True,
    )
    return out


HEAD = f'''  graph [fontname="{G_FONT}", bgcolor="white", pad="0.25"];
  node  [fontname="{G_FONT}", fontsize=12, color="{EDGE}", penwidth=1.2];
  edge  [fontname="{G_FONT}", fontsize=10, color="{EDGE}", arrowsize=0.7];
'''


def fig_2_1() -> Path:
    """数据对象关系（本项目的 E-R 替代视图）。"""
    src = (
        "digraph ER {\n"
        "  rankdir=LR; nodesep=0.28; ranksep=0.62; splines=true;\n"
        + HEAD
        + f'''
  node [shape=box, style="rounded,filled", fillcolor="{FILL}", margin="0.14,0.08"];
  edge [dir=none];

  REQ  [label="需求条目\\nREQ-*（49 条）"];
  THR  [label="威胁条目\\nT-01 ~ T-15"];
  ADR  [label="架构决策\\nADR 0001 ~ 0035"];
  MOD  [label="模块\\ncontracts / foundation / …", fillcolor="{FILL_CTR}"];

  PACK_ [label="领域包\\npack.toml"];
  CASE [label="案例\\ncase.toml"];
  CRIT [label="判据\\n[case] / [selftest]"];
  BUND [label="bundle\\nmanifest.json"];
  EVID [label="证据\\nevidence/*.json"];
  REV  [label="审查报告\\nreports/*.md"];

  SESS [label="会话\\nSession"];
  AUD  [label="审计事件\\nAuditEvent（JSONL）"];

  REQ  -> MOD  [label=" 1 : n 由模块实现"];
  THR  -> MOD  [label=" n : n 落到模块"];
  ADR  -> MOD  [label=" 1 : n 约束"];
  CASE -> PACK_ [label=" 1 : 1 声明动作面"];
  CASE -> CRIT [label=" 1 : n 声明判据"];
  CASE -> BUND [label=" 1 : 1 编译"];
  BUND -> EVID [label=" 1 : n 运行产出"];
  EVID -> REV  [label=" n : 1 汇总"];
  CASE -> SESS [label=" 1 : n 按阶段运行"];
  SESS -> AUD  [label=" 1 : n 每次决策/调用"];
}}'''
    )
    return dot(src, "fig-2-1-data-objects.png")


def fig_2_2() -> Path:
    """数据流图（第 1 层）。"""
    src = (
        "digraph DFD {\n"
        "  rankdir=TB; nodesep=0.32; ranksep=0.42; splines=true;\n"
        + HEAD
        + f'''
  node [shape=box, style="rounded,filled", fillcolor="{FILL}", margin="0.16,0.09"];
  edge [fontsize=9];

  E1 [label="用户 / CI 脚本", shape=parallelogram, fillcolor="{FILL_ALT}"];
  E2 [label="llama-server\\n（回环 127.0.0.1）", shape=parallelogram, fillcolor="{FILL_ALT}"];
  E3 [label="工作目录文件系统", shape=parallelogram, fillcolor="{FILL_ALT}"];

  P1 [label="P1  配置与装配\\n读配置 → 能力集 / 工具集 / 审计落点"];
  P2 [label="P2  编排循环\\n组装上下文 → 调模型 → 分发"];
  P3 [label="P3  参数校验\\n按 parameters_schema 严格校验"];
  P4 [label="P4  策略判定\\nPolicyEngine.decide()"];
  P5 [label="P5  工具执行\\n路径与子进程唯一入口"];
  P6 [label="P6  审计落盘\\n一行一事件，只追加"];

  D1 [label="D1  配置\\nTOML", shape=note, fillcolor="#FFFFFF"];
  D2 [label="D2  审计\\nJSONL", shape=note, fillcolor="#FFFFFF"];

  P7 [label="P7  打包与审查\\n离线子命令，不进主流程", style="rounded,dashed,filled"];
  D3 [label="D3  bundle / 证据", shape=note, fillcolor="#FFFFFF"];

  E1 -> P1 [label="任务 / 参数"];
  D1 -> P1 [label="只读"];
  P1 -> P2 [label="装配结果"];
  P2 -> E2 [label="chat(messages, tools)"];
  E2 -> P2 [label="tool_call / 文本"];
  P2 -> P3 [label="原始 JSON 文本"];
  P3 -> P4 [label="已校验参数"];
  P4 -> P5 [label="放行"];
  P4 -> P6 [label="POLICY_DECISION"];
  P5 -> E3 [label="resolve_within 后读写"];
  P5 -> P6 [label="TOOL_CALL"];
  P6 -> D2 [label="追加写 + fsync"];
  P2 -> E1 [label="事件流 / 退出码", style=dashed];
  P7 -> D3 [label="写", style=dashed];
}}'''
    )
    return dot(src, "fig-2-2-dfd.png")


def fig_2_3() -> Path:
    """用例图。"""
    src = (
        "digraph UC {\n"
        "  rankdir=LR; nodesep=0.26; ranksep=0.7;\n"
        + HEAD
        + f'''
  node [shape=plaintext, style="", fontsize=12];

  A1 [label="离线开发者\\n（群体 A）"];
  A2 [label="CI / 脚本"];
  A3 [label="审计与复核者"];
  A4 [label="攻击者\\n（滥用视角）", fontcolor="#8B1A1A"];
  A5 [label="产品所有者"];

  subgraph cluster_sys {{
    label="系统边界：agent-sec-perf 命令行客户端"; fontsize=13; color="{EDGE}"; style="rounded";
    node [shape=ellipse, style="filled", fillcolor="{FILL}", fontsize=11, margin="0.02,0.02"];
    UC1 [label="UC-01 完成多步只读任务"];
    UC2 [label="UC-02 高危操作确认"];
    UC3 [label="UC-03 越权调用被拒并留痕"];
    UC4 [label="UC-04 不可信内容不得改变控制流"];
    UC5 [label="UC-05 审计回放与排障"];
    UC6 [label="UC-06 跑一轮基准并归档"];
  }}

  A1 -> UC1; A1 -> UC2; A1 -> UC5;
  A2 -> UC1 [label="非交互"]; A2 -> UC6;
  A3 -> UC5;
  A5 -> UC2 [label="验收"];
  A4 -> UC3 [style=dashed, color="#8B1A1A", label=" 试图绕过", fontcolor="#8B1A1A"];
  A4 -> UC4 [style=dashed, color="#8B1A1A", label=" 试图诱导", fontcolor="#8B1A1A"];
  A4 -> UC5 [style=dashed, color="#8B1A1A", label=" 试图抹掉痕迹", fontcolor="#8B1A1A"];
}}'''
    )
    return dot(src, "fig-2-3-usecase.png")


def fig_3_1() -> Path:
    """分层结构图。"""
    src = (
        "digraph LAYER {\n"
        "  rankdir=TB; nodesep=0.24; ranksep=0.34;\n"
        + HEAD
        + f'''
  node [shape=box, style="rounded,filled", fillcolor="{FILL}", margin="0.16,0.10", fontsize=12];

  L4A [label="L4 表现层  cli/\\l  Typer 应用 · Rich 渲染 · 交互/非交互 · 权限确认\\l"];
  L4B [label="L4 生产层  pack/\\l  案例契约 · 判据自证 · bundle 摘要 · 状态机 · 审查报告\\l"];
  L3  [label="L3 编排层  harness/\\l  会话循环 · 工具裁剪 · 提示分级 · 检查点 · 上下文 · 领域包\\l"];
  L2  [label="L2 能力层  model/ · tools/\\l  统一模型客户端（本地回环）· 工具注册与执行\\l"];
  L1  [label="L1 基础设施层  foundation/\\l  errors · paths · proc · config · logging\\l"];

  subgraph cluster_x {{
    label="横切能力层（被上层调用，不反向依赖业务层）"; fontsize=11; color="{EDGE}"; style="rounded,dashed";
    SEC [label="security/\\l 能力 · 策略 · 沙箱探针\\l", fillcolor="{FILL_ALT}"];
    OBS [label="observability/\\l 审计 · 回放\\l", fillcolor="{FILL_ALT}"];
  }}

  CTR [label="contracts  零行为契约层：纯类型 + Protocol，只依赖标准库；被各层依赖", fillcolor="{FILL_CTR}"];
  BENCH [label="bench/  评测子系统（不在产品分层内；唯一接缝 = foundation/）", fillcolor="#F2F2F2", fontsize=11];

  L4A -> L4B [label="  注册子命令"];
  L4A -> L3;
  L4B -> L3 [label="  注入 runner"];
  L3 -> L2;
  L2 -> L1;
  L1 -> CTR [style=invis, weight=8];
  CTR -> BENCH [style=invis];
  L3 -> SEC [style=dashed, label=" 判定", constraint=false];
  L3 -> OBS [style=dashed, constraint=false];
  L2 -> SEC [style=dashed, constraint=false];
  L2 -> OBS [style=dashed, constraint=false];
}}'''
    )
    return dot(src, "fig-3-1-layers.png")


def fig_3_2() -> Path:
    """类图（契约类型与实现类）。"""

    def esc(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def cls(name: str, fields: list[str], methods: list[str], color: str = FILL) -> str:
        rows = "".join(
            f'<tr><td align="left" balign="left">{esc(f)}</td></tr>' for f in fields + methods
        )
        return (
            f'<table border="0" cellborder="1" cellspacing="0" cellpadding="3">'
            f'<tr><td bgcolor="{color}"><b>{name}</b></td></tr>{rows}</table>'
        )

    src = (
        "digraph CLS {\n"
        "  rankdir=TB; nodesep=0.30; ranksep=0.48; splines=true;\n"
        + HEAD
        + "  node [shape=plaintext];\n"
        + "  edge [arrowhead=vee, style=solid];\n"
        + f'''
  subgraph cluster_h {{
    label="编排层 harness/"; fontsize=12; color="{EDGE}"; style="rounded";
    RS  [label=<{cls("Session", ["config: SessionConfig", "engine / registry / client"], ["run(task) -> Iterator[SessionEvent]", "close() -> None"])}>, fillcolor="{FILL_CTR}"];
  }}

  subgraph cluster_s {{
    label="安全层 security/"; fontsize=12; color="{EDGE}"; style="rounded";
    PE  [label=<{cls("PolicyEngine", ["granted: CapabilitySet", "sink: AuditSink", "tool_risk: Mapping"], ["decide(req) -> PolicyDecision", "risk_of(tool, args) -> RiskLevel"])}>, fillcolor="{FILL_CTR}"];
    PR  [label=<{cls("PolicyRequest", ["requested: frozenset[Capability]", "arguments: Mapping"], [])}>];
    PD  [label=<{cls("PolicyDecision", ["allow: bool", "requires_confirmation: bool", "risk_level / reason / audit_id"], [])}>];
    CS  [label=<{cls("CapabilitySet", ["granted: frozenset[Capability]"], ["parse(names)", "missing(req)"])}>];
  }}

  subgraph cluster_o {{
    label="可观测层 observability/"; fontsize=12; color="{EDGE}"; style="rounded";
    SK  [label=<{cls("JsonlAuditSink", ["directory: Path", "allowed_roots: tuple"], ["emit(event)", "flush()", "query_by_id(id)"])}>, fillcolor="{FILL_CTR}"];
    AE  [label=<{cls("AuditEvent", ["event_id / kind / timestamp", "session_id / outcome / call_id", "tool_name / capability / risk_level / detail"], [])}>];
  }}

  subgraph cluster_t {{
    label="能力层 tools/ · model/"; fontsize=12; color="{EDGE}"; style="rounded";
    TR  [label=<{cls("ToolRegistry", ["tools: Mapping[str, Tool]"], ["resolve(name)", "specs(caps)"])}>, fillcolor="{FILL_CTR}"];
    TL  [label=<{cls("Tool (Protocol)", ["name / spec / capabilities"], ["invoke(args, ctx) -> ToolResult"])}>];
    MC  [label=<{cls("ModelClient (Protocol)", [], ["chat(messages, tools) -> ModelResponse"])}>];
    LC  [label=<{cls("LocalLlamaClient", ["host: 回环 only", "timeout_s > 0"], ["chat(...)", "close()"])}>, fillcolor="{FILL_CTR}"];
  }}

  subgraph cluster_p {{
    label="生产层 pack/"; fontsize=12; color="{EDGE}"; style="rounded";
    CA  [label=<{cls("CaseSpec", ["scene / acts / task", "criteria / budget"], ["load_case_spec(dir)"])}>];
    MB  [label=<{cls("Manifest", ["files: list[FileEntry]", "digest: str"], ["build(dir)", "verify(dir)"])}>];
  }}

  RS -> PE [label="每个特权操作点调用"];
  RS -> TR [label="resolve(name)"];
  RS -> MC [label="chat(messages, tools)"];
  PE -> PR [label="消费"];
  PE -> PD [label="产出"];
  PE -> CS [label="读授权集合"];
  PE -> SK [label="emit（不包 try）"];
  SK -> AE [label="一行 JSON"];
  TR -> TL [label="注册 n 个"];
  LC -> MC [label="实现", style=dashed];
  CA -> MB [label="编译为"];
}}'''
    )
    return dot(src, "fig-3-2-class.png")


def fig_3_4() -> Path:
    """PolicyEngine.decide() 的判定流程。"""
    src = (
        "digraph DECIDE {\n"
        "  rankdir=TB; nodesep=0.30; ranksep=0.30;\n"
        + HEAD
        + f'''
  node [shape=box, style="rounded,filled", fillcolor="{FILL}", margin="0.14,0.07"];
  edge [fontsize=9];

  S  [label="decide(PolicyRequest)", shape=oval, fillcolor="{FILL_CTR}"];
  T  [label="求值（try 包裹\\n任何异常收敛为拒绝）", shape=box];
  Q1 [label="1a  requested 为空集？", shape=diamond, style="filled", fillcolor="#FFFFFF"];
  Q2 [label="1b  含非 Capability 成员？", shape=diamond, style="filled", fillcolor="#FFFFFF"];
  Q3 [label="1c  missing = requested - granted\\n      为空？", shape=diamond, style="filled", fillcolor="#FFFFFF"];
  Q4 [label="1c  风险等级？", shape=diamond, style="filled", fillcolor="#FFFFFF"];

  H1 [label="硬拒绝\\n（理由：未声明所需能力）", fillcolor="#FBE9E7"];
  H2 [label="硬拒绝\\n（理由：含非法能力成员）", fillcolor="#FBE9E7"];
  H3 [label="硬拒绝\\n（理由：能力未授予）", fillcolor="#FBE9E7"];
  H4 [label="需人工确认\\nreason 写明风险", fillcolor="{FILL_ALT}"];
  A1 [label="放行\\nallow=True, confirm=False", fillcolor="#E8F5E9"];
  CR [label="硬拒绝（CRITICAL）", fillcolor="#FBE9E7"];
  ESC[label="拒绝且可升级\\nreason=策略求值失败/异常名", fillcolor="{FILL_ALT}"];

  R1 [label="计算 risk_level\\n领域包前缀 > 工具声明 > 默认 HIGH"];
  R2 [label="生成 audit_id（uuid4）"];
  R3 [label="sink.emit(AuditEvent)\\n不包 try：失败必须冒泡"];
  R4 [label="返回 PolicyDecision", shape=oval, fillcolor="{FILL_CTR}"];

  S -> T;
  T -> Q1 [label="正常"];
  T -> ESC [label="抛出异常", style=dashed];
  Q1 -> H1 [label="是"];
  Q1 -> Q2 [label="否"];
  Q2 -> H2 [label="是"];
  Q2 -> Q3 [label="否"];
  Q3 -> H3 [label="否"];
  Q3 -> Q4 [label="是"];
  Q4 -> A1 [label="LOW"];
  Q4 -> H4 [label="MEDIUM / HIGH"];
  Q4 -> CR [label="CRITICAL"];
  H1 -> R1; H2 -> R1; H3 -> R1; H4 -> R1; A1 -> R1; CR -> R1; ESC -> R1;
  R1 -> R2 -> R3 -> R4;
}}'''
    )
    return dot(src, "fig-3-4-decide.png")


def fig_4_2() -> Path:
    """关键函数调用关系。"""
    src = (
        "digraph CALL {\n"
        "  rankdir=TB; nodesep=0.30; ranksep=0.44; splines=true;\n"
        + HEAD
        + f'''
  node [shape=box, style="rounded,filled", fillcolor="{FILL}", margin="0.12,0.06", fontsize=11];
  edge [fontsize=9];

  subgraph cluster_cli {{
    label="cli/"; fontsize=11; color="{EDGE}"; style="rounded";
    M1 [label="app.main\\n命令分发", fillcolor="{FILL_CTR}"];
    M2 [label="app._assembly\\n装配（能力集·工具集·审计落点）", fillcolor="{FILL_CTR}"];
  }}

  subgraph cluster_h {{
    label="harness/"; fontsize=11; color="{EDGE}"; style="rounded";
    M3 [label="Session.run\\n会话主循环", fillcolor="{FILL_CTR}"];
    M4 [label="loop.run_turn\\n单步：组装→调模型→分发"];
    M5 [label="arguments.parse_arguments\\n严格校验"];
    M12[label="trimming.select_tools\\n按档位裁剪"];
  }}

  subgraph cluster_mt {{
    label="model/ · tools/"; fontsize=11; color="{EDGE}"; style="rounded";
    M11[label="LocalLlamaClient.chat\\n回环 HTTP"];
    M8 [label="Tool.invoke\\n文件 / 命令 / 报告工具"];
  }}

  subgraph cluster_so {{
    label="security/ · observability/"; fontsize=11; color="{EDGE}"; style="rounded";
    M6 [label="PolicyEngine.decide\\n允许 / 确认 / 拒绝"];
    M7 [label="JsonlAuditSink.emit\\n与 flush"];
  }}

  subgraph cluster_f {{
    label="foundation/"; fontsize=11; color="{EDGE}"; style="rounded";
    M9 [label="paths.resolve_within\\n路径唯一入口"];
    M10[label="proc.run / spawn\\n子进程唯一入口"];
  }}

  M1 -> M2 [label="1"];
  M2 -> M3 [label="2 构造并进入"];
  M3 -> M11 [label="3 chat(messages, tools)"];
  M3 -> M4 [label="4 每一步"];
  M4 -> M12 [label="5 先裁剪工具集"];
  M4 -> M5 [label="6 校验原始 JSON"];
  M5 -> M6 [label="7 decide()"];
  M6 -> M7 [label="8 emit（不可吞）"];
  M4 -> M8 [label="9 放行后执行"];
  M8 -> M9 [label="文件工具"];
  M8 -> M10 [label="命令工具"];
  M11 -> M10 [label="spawn llama-server", style=dashed];
  M3 -> M7 [label="退出时 flush", style=dashed];
}}'''
    )
    return dot(src, "fig-4-2-calls.png")


# ============================================================================
# 2. 终端输出 → 图片（内容一律取自真实命令）
# ============================================================================


def run_capture(argv: list[str]) -> str:
    exe = shutil.which(argv[0]) or argv[0]
    # 非 tty 时 Rich 按 COLUMNS 排版；不放大宽度会把表格右边界截掉
    env = dict(os.environ, COLUMNS="120", LANG="C.UTF-8", PYTHONIOENCODING="utf-8")
    # 参数是常量列表，不经 shell
    proc = subprocess.run(  # noqa: S603
        [exe, *argv[1:]],
        capture_output=True,
        text=True,
        cwd=HERE.parent.parent,
        timeout=300,
        env=env,
    )
    return (proc.stdout or "") + (proc.stderr or "")


def _wide(ch: str) -> bool:
    return unicodedata.east_asian_width(ch) in ("W", "F")


# 制表符一律画线：字体里的边框字形宽度与单元格对不齐，画出来会断成小段
BOX: dict[str, tuple[str, int]] = {
    "─": ("lr", 1),
    "━": ("lr", 2),
    "╌": ("lr", 1),
    "┈": ("lr", 1),
    "│": ("ud", 1),
    "┃": ("ud", 2),
    "╎": ("ud", 1),
    "┌": ("rd", 1),
    "┏": ("rd", 2),
    "╭": ("rd", 1),
    "┐": ("ld", 1),
    "┓": ("ld", 2),
    "╮": ("ld", 1),
    "└": ("ru", 1),
    "┗": ("ru", 2),
    "╰": ("ru", 1),
    "┘": ("lu", 1),
    "┛": ("lu", 2),
    "╯": ("lu", 1),
    "├": ("udr", 1),
    "┣": ("udr", 2),
    "┤": ("udl", 1),
    "┫": ("udl", 2),
    "┬": ("lrd", 1),
    "┳": ("lrd", 2),
    "┴": ("lru", 1),
    "┻": ("lru", 2),
    "┼": ("lrud", 1),
    "╋": ("lrud", 2),
}


def draw_box(d, ch: str, x: int, y: int, cell: int, lh: int) -> None:
    dirs, weight = BOX[ch]
    w = 1 if weight == 1 else 2
    cx, cy = x + cell // 2, y + lh // 2
    if "l" in dirs:
        d.line((x, cy, cx, cy), fill=INK, width=w)
    if "r" in dirs:
        d.line((cx, cy, x + cell, cy), fill=INK, width=w)
    if "u" in dirs:
        d.line((cx, y, cx, cy), fill=INK, width=w)
    if "d" in dirs:
        d.line((cx, cy, cx, y + lh), fill=INK, width=w)


def draw_line(d, ln: str, x: int, y: int, cell: int, lh: int, mono, cjk) -> None:
    for ch in ln:
        if ch in BOX:
            draw_box(d, ch, x, y, cell, lh)
        elif _wide(ch):
            d.text((x, y), ch, font=cjk, fill=INK)
        else:
            d.text((x, y), ch, font=mono, fill=INK)
        x += cell * 2 if _wide(ch) else cell


def render_terminal(text: str, name: str, title: str, cols: int | None = None) -> Path:
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    width = cols or max(sum(2 if _wide(c) else 1 for c in ln) for ln in lines)

    size = 15
    cell = 9
    lh = 22
    pad = 18
    bar = 34
    mono = ImageFont.truetype(MONO, size)
    cjk = ImageFont.truetype(CJK, size, index=CJK_INDEX)

    img = Image.new(
        "RGB", (pad * 2 + width * cell, bar + pad * 2 + len(lines) * lh), (252, 252, 252)
    )
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, img.width, bar), fill=(238, 238, 238))
    d.text((pad, 7), title, font=ImageFont.truetype(CJK, 14, index=CJK_INDEX), fill=(70, 70, 70))
    for i in range(3):
        cx = img.width - pad - 44 + i * 16
        d.ellipse((cx, 13, cx + 8, 21), fill=(190, 190, 190))
    d.line((0, bar, img.width, bar), fill=(200, 200, 200))

    y = bar + pad
    for ln in lines:
        draw_line(d, ln, pad, y, cell, lh, mono, cjk)
        y += lh
    path = OUT / name
    img.save(path)
    return path


def fig_2_4() -> Path:
    help_txt = run_capture(["uv", "run", "agent-sec-perf", "--help"])
    keep = [ln for ln in help_txt.splitlines() if ln.strip()]
    text = "\n".join(keep[:20])
    return render_terminal(
        text, "fig-2-4-cli-help.png", "agent-sec-perf --help   （2026-11-07 实取）"
    )


def fig_2_5() -> Path:
    txt = run_capture(["uv", "run", "agent-sec-perf", "doctor"])
    keep = [ln for ln in txt.splitlines() if ln.strip()]
    return render_terminal(
        "\n".join(keep[:22]),
        "fig-2-5-cli-doctor.png",
        "agent-sec-perf doctor   （2026-11-07 实取）",
    )


def fig_4_1() -> Path:
    txt = run_capture(
        [
            "git",
            "--no-pager",
            "log",
            "--graph",
            "--decorate",
            "--date=short",
            "--pretty=format:%h %ad %s",
            "-n",
            "24",
        ]
    )
    return render_terminal(
        txt, "fig-4-1-git-log.png", "git log --graph   （2026-11-07 实取，节选）"
    )


def fig_4_3() -> Path:
    txt = run_capture(
        [
            "uv",
            "run",
            "pytest",
            "-v",
            "--no-header",
            "-p",
            "no:cacheprovider",
            "tests/security/test_policy_empty_and_multi.py",
            "tests/security/test_s1_replayable_audit_link.py",
            "tests/security/test_path_traversal_rejected.py",
        ]
    )
    keep = [ln for ln in txt.splitlines() if ln.strip()]
    return render_terminal(
        "\n".join(keep[-16:]),
        "fig-4-3-pytest.png",
        "pytest -v（安全层对抗性用例，2026-11-07 实取）",
    )


# ============================================================================
# 3. 顺序图（Graphviz 不支持，用 Pillow 画）
# ============================================================================


def fig_3_3() -> Path:
    actors = [
        ("人", 80),
        ("CLI", 250),
        ("Session", 450),
        ("策略引擎", 660),
        ("本地模型", 880),
        ("工具", 1050),
        ("审计", 1200),
    ]
    msgs = [
        ("人", "CLI", 'run "读 app.log 并写报告"', False),
        ("CLI", "Session", "run(task)", False),
        ("Session", "本地模型", "chat(messages, tools=裁剪后)", False),
        ("本地模型", "Session", "tool_call read_file(app.log)", True),
        ("Session", "Session", "按 schema 严格校验原始 JSON", False),
        ("Session", "策略引擎", "decide(PolicyRequest)", False),
        ("策略引擎", "审计", "emit(POLICY_DECISION)", False),
        ("策略引擎", "Session", "PolicyDecision(allow, confirm, audit_id)", True),
        ("Session", "工具", "invoke(args, ctx)", False),
        ("工具", "审计", "emit(TOOL_CALL)", False),
        ("工具", "Session", "ToolResult(ok, audit_id)", True),
        ("Session", "本地模型", "回喂观察（按数据装配）", False),
        ("本地模型", "Session", "TASK_FINISHED", True),
        ("Session", "CLI", "SessionEvent 流", True),
        ("CLI", "审计", "flush()（fsync）", False),
    ]
    pos = dict(actors)
    width, height = 1320, 1180
    img = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(img)
    head_f = ImageFont.truetype(CJK_BOLD, 18, index=CJK_INDEX)
    msg_f = ImageFont.truetype(CJK, 14, index=CJK_INDEX)
    note_f = ImageFont.truetype(CJK, 13, index=CJK_INDEX)

    top, bottom = 30, 1090
    for name, x in actors:
        w = d.textlength(name, font=head_f)
        d.rounded_rectangle(
            (x - w / 2 - 12, top, x + w / 2 + 12, top + 38),
            radius=6,
            fill=(238, 241, 245),
            outline=(120, 120, 120),
        )
        d.text((x - w / 2, top + 8), name, font=head_f, fill=INK)
        y = top + 40
        while y < bottom:
            d.line((x, y, x, min(y + 8, bottom)), fill=(178, 178, 178), width=1)
            y += 16

    def arrow(x0, x1, y, dashed):
        if dashed:
            step = 10
            n = max(int(abs(x1 - x0) // step), 1)
            for k in range(n):
                a = x0 + (x1 - x0) * k / n
                b = x0 + (x1 - x0) * (k + 0.5) / n
                d.line((a, y, b, y), fill=(70, 70, 70), width=2)
        else:
            d.line((x0, y, x1, y), fill=(70, 70, 70), width=2)
        s = 8 if x1 > x0 else -8
        d.polygon([(x1, y), (x1 - s, y - 5), (x1 - s, y + 5)], fill=(70, 70, 70))

    y = top + 80
    for src_, dst_, label, dashed in msgs:
        x0, x1 = pos[src_], pos[dst_]
        if src_ == dst_:
            d.line((x0, y, x0 + 60, y), fill=(70, 70, 70), width=2)
            d.line((x0 + 60, y, x0 + 60, y + 20), fill=(70, 70, 70), width=2)
            arrow(x0 + 60, x0 + 2, y + 20, False)
            d.text((x0 + 70, y - 8), label, font=msg_f, fill=INK)
            y += 62
            continue
        arrow(x0, x1, y, dashed)
        tw = d.textlength(label, font=msg_f)
        cx = (x0 + x1) / 2
        d.rectangle((cx - tw / 2 - 4, y - 21, cx + tw / 2 + 4, y - 3), fill="white")
        d.text((cx - tw / 2, y - 21), label, font=msg_f, fill=(40, 40, 40))
        y += 62

    d.rounded_rectangle(
        (30, height - 78, 1290, height - 22), radius=8, fill=(255, 249, 224), outline=(200, 170, 60)
    )
    d.text(
        (46, height - 66),
        "实线为请求，虚线为返回；拒绝与审计在同一次调用内完成：先 decide() 并落审计，再决定是否执行。",
        font=note_f,
        fill=(90, 70, 20),
    )
    path = OUT / "fig-3-3-sequence.png"
    img.save(path)
    return path


# ============================================================================
# 4. 入口
# ============================================================================

FIGS = {
    "2-1": fig_2_1,
    "2-2": fig_2_2,
    "2-3": fig_2_3,
    "2-4": fig_2_4,
    "2-5": fig_2_5,
    "3-1": fig_3_1,
    "3-2": fig_3_2,
    "3-3": fig_3_3,
    "3-4": fig_3_4,
    "4-1": fig_4_1,
    "4-2": fig_4_2,
    "4-3": fig_4_3,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="只生成某一张，例如 3-1")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    keys = [args.only] if args.only else list(FIGS)
    for k in keys:
        if k not in FIGS:
            sys.exit(f"未知图号：{k}")
        p = FIGS[k]()
        _say(f"图 {k}: {p.relative_to(HERE)}")


if __name__ == "__main__":
    main()
