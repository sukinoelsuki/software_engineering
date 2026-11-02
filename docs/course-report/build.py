#!/usr/bin/env python3
"""课程报告构建：把 `report.md` 排成 `report.docx`，再转 `report.pdf`。

为什么要有这个脚本
------------------
正文的唯一真源是 Markdown（可 diff、可评审）。docx 是**派生件**，不能手改——
手改之后下一次重建就被覆盖，而且会出现"docx 与 md 各说一套"。
本脚本只支持报告实际用到的 Markdown 子集（标题 / 段落 / 表格 / 代码块 / 两处 Mermaid 图）；
遇到不认识的语法**直接报错**，不静默丢弃。

依赖与边界
----------
- 构建期依赖：`python-docx`、`Pillow`（钉在 `requirements-doc.txt`，**不进** `pyproject.toml`）；
- PDF 由 LibreOffice 转换：`soffice --headless --convert-to pdf`；
- 中文字体用 Noto CJK 替代宋体/黑体（本环境没有 Windows 字库）⇒ 页数是**量级核对**，
  真正交付时的页码以 Word/WPS 为准；
- `report.pdf` 是本地核对产物，已 gitignore，**不入库**。

用法
----
    uv run python docs/course-report/build.py            # docx + pdf
    uv run python docs/course-report/build.py --no-pdf   # 只出 docx
    uv run python docs/course-report/build.py --check    # 只报页数与 TOC 页码核对
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
MD = HERE / "report.md"
DOCX = HERE / "report.docx"
PDF = HERE / "report.pdf"
BUILD = HERE / ".build"

# ---- 版式（与 README §5 一致：A4、五号字、单倍行距、Word 中文默认页边距） ----
PAGE_W, PAGE_H = Cm(21), Cm(29.7)
MARGIN_LR, MARGIN_TB = Cm(3.17), Cm(2.54)
TEXT_W = 21 - 3.17 * 2  # cm，正文可用宽度
BODY_PT = 10.5  # 五号
TABLE_PT = 9.0  # 小五
CODE_PT = 8.5
BODY_FONT, BODY_EA = "Times New Roman", "宋体"
HEAD_FONT, HEAD_EA = "Arial", "黑体"

CJK = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")


# ============================================================================
# 1. Markdown 解析（只认报告用到的子集）
# ============================================================================


def _say(msg: str) -> None:
    sys.stdout.write(msg + "\n")


def split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line)]


def parse(md_text: str) -> tuple[str, list[tuple[str, object]]]:
    lines = md_text.splitlines()
    title = ""
    i = 0
    if lines and lines[0].strip() == "---":
        i = 1
        while i < len(lines) and lines[i].strip() != "---":
            if lines[i].startswith("title:"):
                title = lines[i].split(":", 1)[1].strip()
            i += 1
        i += 1

    blocks: list[tuple[str, object]] = []
    while i < len(lines):
        raw = lines[i]
        s = raw.strip()
        if not s:
            i += 1
            continue
        if s.startswith("```"):
            lang = s[3:].strip()
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            if i >= len(lines):
                sys.exit("Markdown 解析失败：代码块未闭合")
            i += 1
            blocks.append(("mermaid" if lang == "mermaid" else "code", buf))
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", s)
        if m:
            blocks.append((f"h{len(m.group(1))}", m.group(2).strip()))
            i += 1
            continue
        if s.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i]))
                i += 1
            if len(rows) < 3:
                sys.exit(f"Markdown 解析失败：表格行数不足（{rows[0] if rows else ''}）")
            aligns = []
            for cell in rows[1]:
                c = cell.strip()
                if c.startswith(":") and c.endswith(":"):
                    aligns.append("center")
                elif c.endswith(":"):
                    aligns.append("right")
                else:
                    aligns.append("left")
            blocks.append(("table", (rows[0], rows[2:], aligns)))
            continue
        if s.startswith("> "):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            blocks.append(("p", "".join(buf)))
            continue
        if s.startswith("- "):
            blocks.append(("li", s[2:]))
            i += 1
            continue
        if s == "---":
            i += 1
            continue
        buf = []
        while i < len(lines):
            t = lines[i].strip()
            if not t or t.startswith(("#", "|", "```", "> ", "- ")) or t == "---":
                break
            if buf and CJK.match(buf[-1][-1:]) and CJK.match(t[:1]):
                buf.append(t)
            elif buf:
                buf.append(" " + t)
            else:
                buf.append(t)
            i += 1
        blocks.append(("p", "".join(buf)))
    return title, blocks


# ============================================================================
# 2. 两处 Mermaid 图（用 Pillow 画成 PNG，不引入浏览器依赖）
# ============================================================================

FONT_SC = 2  # NotoSansCJK-Regular.ttc 里 index 2 = 简体中文
FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_BOLD_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

INK = (38, 38, 38)
BOX = (242, 242, 242)
BOX_ALT = (251, 243, 228)
BOX_CTR = (234, 241, 248)
EDGE = (90, 90, 90)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD_PATH if bold else FONT_PATH
    return ImageFont.truetype(path, size, index=FONT_SC)


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if _text_w(draw, cur + ch, font) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def _box(draw, xy, title, sub=None, fill=BOX, title_size=30, sub_size=22):
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle(xy, radius=10, fill=fill, outline=EDGE, width=2)
    tf = _font(title_size, bold=True)
    lines = _wrap(draw, title, tf, x1 - x0 - 24)
    sf = _font(sub_size)
    sub_lines = _wrap(draw, sub, sf, x1 - x0 - 24) if sub else []
    total = len(lines) * (title_size + 8) + len(sub_lines) * (sub_size + 6)
    y = y0 + (y1 - y0 - total) // 2
    for ln in lines:
        w = _text_w(draw, ln, tf)
        x = x0 + (x1 - x0 - w) // 2
        draw.text((x, y), ln, font=tf, fill=INK)
        y += title_size + 8
    for ln in sub_lines:
        w = _text_w(draw, ln, sf)
        draw.text((x0 + (x1 - x0 - w) // 2, y), ln, font=sf, fill=(80, 80, 80))
        y += sub_size + 6


def _arrow(draw, start, end, dashed=False, width=2):
    x0, y0 = start
    x1, y1 = end
    if dashed:
        seg = 9
        dx, dy = x1 - x0, y1 - y0
        dist = max((dx * dx + dy * dy) ** 0.5, 1)
        n = int(dist // seg)
        for k in range(0, n, 2):
            a = (x0 + dx * k / n, y0 + dy * k / n)
            b = (x0 + dx * min(k + 1, n) / n, y0 + dy * min(k + 1, n) / n)
            draw.line([a, b], fill=EDGE, width=width)
    else:
        draw.line([start, end], fill=EDGE, width=width)
    ang = (y1 - y0, x1 - x0)
    import math

    theta = math.atan2(ang[0], ang[1])
    size = 7
    for s in (0.5, -0.5):
        draw.line(
            [
                (x1, y1),
                (
                    x1 - size * math.cos(theta - s),
                    y1 - size * math.sin(theta - s),
                ),
            ],
            fill=EDGE,
            width=width,
        )


def fig_layers(path: Path) -> None:
    width, height = 1500, 900
    img = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(img)

    left = (40, 1120)
    rows = [
        ("L4 表现层 cli/", "Typer · Rich · 交互与非交互 · 权限确认", 30, 86),
        ("L4 生产层 pack/", "案例契约 · 判据自证 · bundle 摘要 · 状态机 · 审查报告", 136, 92),
        (
            "L3 编排层 harness/",
            "会话循环 · 工具裁剪 · 提示分级 · 检查点 · 上下文 · 领域包",
            248,
            92,
        ),
        ("L2 能力层 model/ tools/", "统一客户端（本地回环）· 工具注册与执行 · 参数校验", 360, 86),
        ("L1 基础设施层 foundation/", "errors · paths · proc · config · logging", 466, 86),
    ]
    for title, sub, y, h in rows:
        _box(d, (left[0], y, left[1], y + h), title, sub, title_size=34, sub_size=25)

    # 纵向依赖：层间箭头
    for a, b in ((116, 136), (228, 248), (340, 360), (446, 466)):
        _arrow(d, (600, a), (600, b))

    sec = (1160, 225, 1460, 325)
    obs = (1160, 355, 1460, 455)
    _box(d, sec, "security/", "能力 · 策略", BOX_ALT, 32, 24)
    _box(d, obs, "observability/", "审计 · 回放", BOX_ALT, 32, 24)
    d.text((1164, 190), "横切能力层", font=_font(24), fill=(120, 90, 20))

    ctr = (40, 610, 1120, 700)
    _box(d, ctr, "contracts 零行为契约层", "纯类型与 Protocol，只依赖标准库", BOX_CTR, 34, 26)

    # 横切调用（虚线）：harness / 能力层 → 两个横切层
    _arrow(d, (1120, 275), (1160, 270), dashed=True)
    _arrow(d, (1120, 300), (1160, 400), dashed=True)
    _arrow(d, (1120, 390), (1160, 310), dashed=True)
    _arrow(d, (1120, 420), (1160, 430), dashed=True)

    # 横切层 / 基础设施 → 契约层（实线）
    d.line([(1478, 275), (1478, 580)], fill=EDGE, width=2)
    _arrow(d, (1478, 580), (1135, 620))
    _arrow(d, (600, 552), (600, 610))

    # 图例
    lf = _font(25)
    _arrow(d, (60, 740), (170, 740))
    d.text((190, 723), "分层依赖（自上而下，单向）", font=lf, fill=INK)
    _arrow(d, (60, 795), (170, 795), dashed=True)
    d.text(
        (190, 778), "横切调用（单向；横切层与 L1 → contracts 的实线在右侧合并）", font=lf, fill=INK
    )
    d.text(
        (60, 850),
        "说明：本图为报告视图；机器检查以 tests/unit/test_architecture_layers.py 的 ALLOWED_DEPENDENCIES 为准。",
        font=_font(22),
        fill=(110, 110, 110),
    )
    img.save(path)


def fig_sequence(path: Path) -> None:
    width, height = 1200, 1420
    img = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(img)

    actors = [
        ("人", 70),
        ("CLI", 285),
        ("harness", 530),
        ("本地模型", 790),
        ("工具", 990),
        ("审计", 1120),
    ]
    top, bottom = 40, 1350
    f = _font(28, bold=True)
    for name, x in actors:
        w = _text_w(d, name, f)
        box = (x - w // 2 - 14, top, x + w // 2 + 14, top + 52)
        d.rectangle(box, fill=(238, 238, 238), outline=EDGE, width=2)
        d.text((x - w // 2, top + 10), name, font=f, fill=INK)
        y = top + 52
        while y < bottom:
            d.line([(x, y), (x, min(y + 9, bottom))], fill=(175, 175, 175), width=2)
            y += 18

    mf = _font(25)
    msgs = [
        (190, actors[0][1], actors[1][1], "demo --run", False),
        (270, actors[1][1], actors[2][1], "阶段 A 会话", False),
        (350, actors[2][1], actors[3][1], "系统提示 + 任务", False),
        (430, actors[3][1], actors[2][1], "调 read_file app.log", True),
        (510, actors[2][1], actors[4][1], "读文件", False),
        (590, actors[4][1], actors[2][1], "日志内容", True),
        (670, actors[3][1], actors[2][1], "调 read_file service.toml", True),
        (750, actors[2][1], actors[4][1], "读文件", False),
        (830, actors[3][1], actors[2][1], "调 write_incident_report", True),
        (910, actors[2][1], actors[4][1], "写报告初稿", False),
        (990, actors[2][1], actors[5][1], "policy_decision + tool_call", False),
        (1070, actors[1][1], actors[2][1], "阶段 B 会话（新会话）", False),
        (1150, actors[3][1], actors[2][1], "调 read_file report.md", True),
        (1230, actors[3][1], actors[2][1], "调 revise_report VERDICT", True),
    ]
    for y, x0, x1, label, dashed in msgs:
        _arrow(d, (x0, y), (x1, y), dashed=dashed)
        w = _text_w(d, label, mf)
        cx = (x0 + x1) // 2
        d.rectangle((cx - w // 2 - 5, y - 34, cx + w // 2 + 5, y - 6), fill="white")
        d.text((cx - w // 2, y - 32), label, font=mf, fill=INK)

    # 复核意见：放在最后一条消息下方的空白区
    d.rounded_rectangle(
        (60, 1290, 620, 1340), radius=8, fill=(255, 249, 224), outline=(200, 170, 60), width=2
    )
    d.text(
        (80, 1298), "人在两段之间给出复核意见（新会话读回报告）", font=_font(24), fill=(90, 70, 20)
    )
    img.save(path)


# ============================================================================
# 3. 排版
# ============================================================================


def set_font(run, name=BODY_FONT, ea=BODY_EA, size=None, bold=None, color=None):
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rfonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)
    rfonts.set(qn("w:eastAsia"), ea)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color


def first_line_indent(p, chars=200):
    ppr = p._p.get_or_add_pPr()
    ind = ppr.find(qn("w:ind"))
    if ind is None:
        ind = OxmlElement("w:ind")
        ppr.append(ind)
    ind.set(qn("w:firstLineChars"), str(chars))
    ind.set(qn("w:firstLine"), str(int(chars * 2.1)))


INLINE = re.compile(r"(\*\*.+?\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|<https?://[^>]+>)")


def add_inline(p, text, size=BODY_PT, bold=False, mono_size=None):
    pos = 0
    for m in INLINE.finditer(text):
        if m.start() > pos:
            r = p.add_run(text[pos : m.start()])
            set_font(r, size=size, bold=bold or None)
        tok = m.group(0)
        if tok.startswith("**"):
            r = p.add_run(tok[2:-2])
            set_font(r, size=size, bold=True)
        elif tok.startswith("`"):
            r = p.add_run(tok[1:-1])
            set_font(r, name="Consolas", ea=BODY_EA, size=mono_size or max(size - 1, 7.5))
        elif tok.startswith("<"):
            r = p.add_run(tok[1:-1])
            set_font(r, size=max(size - 1, 8))
        else:
            label = re.match(r"\[([^\]]+)\]", tok).group(1)
            add_inline(p, label, size=size, bold=bold, mono_size=mono_size)
        pos = m.end()
    if pos < len(text):
        r = p.add_run(text[pos:])
        set_font(r, size=size, bold=bold or None)


def shade(cell, hex_color="F2F2F2"):
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcpr.append(shd)


def disp_len(s: str) -> int:
    return sum(2 if CJK.match(ch) else 1 for ch in s)


def add_table(doc, header, rows, aligns):
    ncol = len(header)
    table = doc.add_table(rows=1, cols=ncol)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    table._tbl.tblPr.append(layout)

    widths = []
    for c in range(ncol):
        vals = [header[c]] + [r[c] if c < len(r) else "" for r in rows]
        widths.append(max(6, max(disp_len(v) for v in vals)))
    total = sum(widths)
    cm = [max(2.2, TEXT_W * w / total) for w in widths]
    while sum(cm) > TEXT_W + 0.01:
        i = cm.index(max(cm))
        over = sum(cm) - TEXT_W
        cm[i] = max(2.2, cm[i] - over)

    def fill(cell, text, bold=False, align="left"):
        p = cell.paragraphs[0]
        p.paragraph_format.space_before = Pt(1)
        p.paragraph_format.space_after = Pt(1)
        p.paragraph_format.line_spacing = 1.0
        p.alignment = {
            "left": WD_ALIGN_PARAGRAPH.LEFT,
            "center": WD_ALIGN_PARAGRAPH.CENTER,
            "right": WD_ALIGN_PARAGRAPH.RIGHT,
        }[align]
        add_inline(p, text, size=TABLE_PT, bold=bold, mono_size=8.0)

    hdr = table.rows[0]
    trpr = hdr._tr.get_or_add_trPr()
    tblheader = OxmlElement("w:tblheader")
    trpr.append(tblheader)
    for i, text in enumerate(header):
        fill(hdr.cells[i], text, bold=True, align="center")
        shade(hdr.cells[i])

    for row in rows:
        cells = table.add_row().cells
        for i in range(ncol):
            fill(cells[i], row[i] if i < len(row) else "", align=aligns[i])

    for i, col in enumerate(table.columns):
        col.width = Cm(cm[i])
    for i, gc in enumerate(table._tbl.tblGrid.gridCol_lst):
        gc.set(qn("w:w"), str(int(cm[i] * 567)))
    for row in table.rows:
        for i, cell in enumerate(row.cells):
            cell.width = Cm(cm[i])
    return table


def add_field(paragraph, instr: str, size=9):
    run = paragraph.add_run()
    set_font(run, size=size)
    fld = OxmlElement("w:fldChar")
    fld.set(qn("w:fldCharType"), "begin")
    run._r.append(fld)
    run2 = paragraph.add_run()
    set_font(run2, size=size)
    it = OxmlElement("w:instrText")
    it.set(qn("xml:space"), "preserve")
    it.text = instr
    run2._r.append(it)
    run3 = paragraph.add_run()
    set_font(run3, size=size)
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run3._r.append(end)


def new_section(doc, restart=None):
    sec = doc.add_section(WD_SECTION.NEW_PAGE)
    sec.page_width, sec.page_height = PAGE_W, PAGE_H
    sec.left_margin = sec.right_margin = MARGIN_LR
    sec.top_margin = sec.bottom_margin = MARGIN_TB
    if restart is not None:
        pg = OxmlElement("w:pgNumType")
        pg.set(qn("w:start"), str(restart))
        sec._sectPr.append(pg)
    return sec


def _cell_bottom_border(cell):
    tcpr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "8")
    bottom.set(qn("w:color"), "000000")
    borders.append(bottom)
    tcpr.append(borders)


def cover(doc, title):
    """封面：校名用书法体图片；题目与课程名各占一行；五项信息压在等长横线上。"""

    def line(text, size, bold=False, name=BODY_FONT, ea=BODY_EA, before=0, after=0):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(before)
        p.paragraph_format.space_after = Pt(after)
        p.paragraph_format.line_spacing = 1.0
        if text:
            r = p.add_run(text)
            set_font(r, name=name, ea=ea, size=size, bold=bold)
        return p

    logo = HERE / "figures" / "hust-name-calligraphy.jpeg"
    if not logo.exists():
        sys.exit(f"封面校名图片缺失：{logo}")
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(42)
    p.paragraph_format.space_after = Pt(16)
    p.add_run().add_picture(str(logo), width=Cm(8.5))

    line("课程报告", 44, True, "黑体", "黑体", 18, 30)
    line(title, 16, True, "宋体", "宋体", 0, 18)
    line("课程名称：软件工程", 16, True, "宋体", "宋体", 0, 52)

    fields = ["专业班级", "学　号", "姓　名", "指导教师", "报告日期"]
    label_w, value_w = Cm(3.0), Cm(7.2)
    table = doc.add_table(rows=len(fields), cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    for i, w in enumerate((label_w, value_w)):
        table.columns[i].width = w
        table._tbl.tblGrid.gridCol_lst[i].set(qn("w:w"), str(int(w.cm * 567)))

    for r, label in enumerate(fields):
        lc, vc = table.cell(r, 0), table.cell(r, 1)
        lc.width, vc.width = label_w, value_w

        lp = lc.paragraphs[0]
        lp.alignment = WD_ALIGN_PARAGRAPH.LEFT
        lp.paragraph_format.space_before = Pt(8)
        lp.paragraph_format.space_after = Pt(0)
        r1 = lp.add_run(f"{label}：")
        set_font(r1, size=14, bold=True)

        vp = vc.paragraphs[0]
        vp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        vp.paragraph_format.space_before = Pt(8)
        vp.paragraph_format.space_after = Pt(0)
        r2 = vp.add_run(" ")
        set_font(r2, size=14)
        _cell_bottom_border(vc)

    line("", 10.5, before=90)
    line("计算机科学与技术学院", 16, True, "宋体", "宋体")


def add_toc(doc, headings, page_map):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(14)
    r = p.add_run("目　　录")
    set_font(r, name=HEAD_FONT, ea=HEAD_EA, size=16, bold=True)

    for level, text in headings:
        text = text.replace("`", "")
        p = doc.add_paragraph()
        pf = p.paragraph_format
        pf.space_before = Pt(3 if level == 1 else 0)
        pf.space_after = Pt(3 if level == 1 else 0)
        pf.line_spacing = 1.0
        pf.left_indent = Cm(0 if level == 1 else 0.85)
        pf.tab_stops.add_tab_stop(Cm(TEXT_W), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
        r = p.add_run(text)
        set_font(r, size=10.5, bold=(level == 1))
        r = p.add_run("\t")
        set_font(r, size=10.5)
        num = page_map.get(text, 0)
        r = p.add_run(str(num) if num else "—")
        set_font(r, size=10.5, bold=(level == 1))


def add_heading(doc, level, text):
    text = text.replace("`", "")
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt({1: 24, 2: 18, 3: 12}[level])
    pf.space_after = Pt({1: 12, 2: 6, 3: 6}[level])
    pf.line_spacing = 1.0
    pf.keep_with_next = True
    r = p.add_run(text)
    set_font(
        r,
        name=HEAD_FONT,
        ea=HEAD_EA,
        size={1: 16, 2: 14, 3: 12}[level],
        bold=True,
        color=RGBColor(0, 0, 0),
    )


CAPTION = re.compile(r"^(表|图) \d+-\d+")


def render(doc, blocks, fig_paths, page_map):
    headings = [(int(k[1]), v) for k, v in blocks if k in ("h1", "h2")]
    add_toc(doc, headings, page_map)

    new_section(doc, restart=1)
    sec = doc.sections[-1]
    sec.footer.is_linked_to_previous = False
    fp = sec.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = fp.add_run("第 ")
    set_font(r, size=9)
    add_field(fp, " PAGE ")
    r = fp.add_run(" 页")
    set_font(r, size=9)

    fig_iter = iter(fig_paths)
    for kind, payload in blocks:
        if kind == "h1":
            add_heading(doc, 1, str(payload))
        elif kind == "h2":
            add_heading(doc, 2, str(payload))
        elif kind == "h3":
            add_heading(doc, 3, str(payload))
        elif kind == "p":
            text = str(payload)
            if CAPTION.match(text):
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.space_before = Pt(3)
                p.paragraph_format.space_after = Pt(6 if text.startswith("表") else 3)
                p.paragraph_format.keep_with_next = text.startswith("表")
                add_inline(p, text, size=9.0, bold=True)
            else:
                p = doc.add_paragraph()
                p.paragraph_format.space_before = Pt(3)
                p.paragraph_format.space_after = Pt(3)
                p.paragraph_format.line_spacing = 1.0
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                first_line_indent(p)
                add_inline(p, text)
        elif kind == "li":
            p = doc.add_paragraph(style="List Bullet")
            p.paragraph_format.space_before = Pt(1)
            p.paragraph_format.space_after = Pt(1)
            add_inline(p, str(payload))
        elif kind == "table":
            header, rows, aligns = payload
            add_table(doc, header, rows, aligns)
            # 表后留一点空隙
            sp = doc.add_paragraph()
            sp.paragraph_format.space_before = Pt(0)
            sp.paragraph_format.space_after = Pt(0)
            sp.paragraph_format.line_spacing = 0.6
        elif kind == "code":
            lines = payload
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(4)
            p.paragraph_format.space_after = Pt(4)
            p.paragraph_format.line_spacing = 1.0
            p.paragraph_format.left_indent = Cm(0.4)
            ppr = p._p.get_or_add_pPr()
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:color"), "auto")
            shd.set(qn("w:fill"), "F6F6F6")
            ppr.append(shd)
            for idx, ln in enumerate(lines):
                r = p.add_run(ln)
                set_font(r, name="Consolas", size=CODE_PT)
                if idx != len(lines) - 1:
                    r.add_break()
        elif kind == "mermaid":
            path = next(fig_iter)
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(6)
            p.paragraph_format.space_after = Pt(3)
            p.paragraph_format.keep_with_next = True
            with Image.open(path) as im:
                iw, ih = im.size
            width_cm = min(13.6, 17.0 * iw / ih)
            p.add_run().add_picture(str(path), width=Cm(width_cm))
        else:
            sys.exit(f"未知块类型：{kind}")


# ============================================================================
# 4. 构建 / 转换 / 页码核对
# ============================================================================


def build(page_map, figs):
    title, blocks = parse(MD.read_text(encoding="utf-8"))
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = PAGE_W, PAGE_H
    sec.left_margin = sec.right_margin = MARGIN_LR
    sec.top_margin = sec.bottom_margin = MARGIN_TB

    normal = doc.styles["Normal"]
    normal.font.size = Pt(BODY_PT)
    normal.font.name = BODY_FONT
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_EA)
    normal.paragraph_format.line_spacing = 1.0
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(0)

    cover(doc, title)
    new_section(doc)  # 目录单独一节：不编页码，也不与封面共页
    render(doc, blocks, figs, page_map)
    doc.save(DOCX)
    return [t for _, t in [(k, v) for k, v in blocks if k in ("h1", "h2")]]


def _run(argv, **kwargs):
    exe = shutil.which(argv[0]) or argv[0]
    # 参数是常量命令名，不经 shell，也不含外部输入
    return subprocess.run([exe, *argv[1:]], check=True, **kwargs)  # noqa: S603


def to_pdf():
    if PDF.exists():
        PDF.unlink()
    env = dict(os.environ, HOME=os.environ.get("HOME", "/root"))
    _run(
        ["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(HERE), str(DOCX)],
        capture_output=True,
        timeout=300,
        env=env,
    )
    if not PDF.exists():
        sys.exit("PDF 转换失败：LibreOffice 未产出 report.pdf")


def pdf_pages():
    info = _run(["pdfinfo", str(PDF)], capture_output=True, text=True).stdout
    n = int(re.search(r"Pages:\s+(\d+)", info).group(1))
    texts = []
    for i in range(1, n + 1):
        out = _run(
            ["pdftotext", "-layout", "-f", str(i), "-l", str(i), str(PDF), "-"],
            capture_output=True,
            text=True,
        ).stdout
        texts.append(out)
    return n, texts


def norm(s: str) -> str:
    return re.sub(r"[\s\u3000]", "", s)


def map_pages(texts, headings):
    pages = [norm(t) for t in texts]
    toc_idx = next((i for i, p in enumerate(pages) if "目录" in p), -1)
    if toc_idx < 0:
        sys.exit("页码核对失败：在 PDF 里找不到目录页")
    body_start = toc_idx + 1
    # 目录若跨页，续页仍有引导点；正文页没有
    while body_start < len(pages) and "....." in texts[body_start]:
        body_start += 1
    first = norm(headings[0])
    if first not in pages[body_start]:
        sys.exit("页码核对失败：目录之后的第一页不是第一章")
    result = {}
    for text in headings:
        key = norm(text.replace("`", ""))
        for i in range(body_start, len(pages)):
            if key in pages[i]:
                result[text] = i - body_start + 1
                break
    return result, body_start


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-pdf", action="store_true", help="只生成 docx")
    ap.add_argument("--check", action="store_true", help="只重建并报页数")
    args = ap.parse_args()

    if not MD.exists():
        sys.exit(f"找不到正文源：{MD}")

    BUILD.mkdir(exist_ok=True)
    figs = [BUILD / "fig-3-1-layers.png", BUILD / "fig-6-1-sequence.png"]
    fig_layers(figs[0])
    fig_sequence(figs[1])

    headings = build({}, figs)
    if args.no_pdf:
        _say(f"已生成 {DOCX.relative_to(HERE.parent.parent)}（未生成 PDF）")
        return

    to_pdf()
    n1, texts = pdf_pages()
    page_map, _body_start = map_pages(texts, headings)
    missing = [h for h in headings if h not in page_map]
    if missing:
        sys.exit(f"页码核对失败：{len(missing)} 个标题未在 PDF 中定位，例如 {missing[0]}")

    build(page_map, figs)
    to_pdf()
    n2, texts2 = pdf_pages()
    _, body_start2 = map_pages(texts2, headings)

    # 页脚核对：正文第 1 页应有"第 1 页"
    foot = texts2[body_start2].strip().splitlines()
    ok_footer = any("第1页" in norm(ln) for ln in foot)
    toc_pages = body_start2 - 1
    _say(f"页数：封面 1 + 目录 {toc_pages} + 正文 {n2 - body_start2} = 共 {n2} 页")
    _say(f"目录条目：{len(headings)} 条，页码已写入（正文 {n2 - body_start2} 页）")
    _say(f"页脚核对：{'通过' if ok_footer else '未通过（请检查分节与页码域）'}")
    if n1 != n2:
        _say(f"⚠️ 两遍渲染页数不同（{n1} → {n2}），页码可能偏移，请复核")
    if args.check:
        _say("（--check：只核对，不额外输出）")


if __name__ == "__main__":
    main()
