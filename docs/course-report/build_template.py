#!/usr/bin/env python3
"""按课程模板生成报告：复制模板 docx，在副本上填正文，保留模板版式。

用法：uv run python docs/course-report/build_template.py [--no-pdf]
正文源是 report-template.md；图片由 make_figures.py 生成。
模板格式见模板"任务书"：章标题黑体小2居中、二级黑体4号、正文宋体小4、
固定 1.5 倍行距、表题在表上方、图题在图下方、章间分页。
"""

from __future__ import annotations

import argparse
import copy
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TEMPLATE = ROOT / "《软件工程》项目报告撰写内容及规范化参考样本.docx"
MD = HERE / "report-template.md"
DOCX = HERE / "软件工程课程报告-能力受限的端侧智能体.docx"
PDF = DOCX.with_suffix(".pdf")

BODY, HEAD = "宋体", "黑体"
TEXT_W = 21 - 3.175 * 2
CJK = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")


def _say(msg):
    sys.stdout.write(msg + "\n")


def split_row(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line)]


def parse(text):
    lines = text.replace("\r\n", "\n").splitlines()
    title, i = "", 0
    if lines[0].strip() == "---":
        i = 1
        while lines[i].strip() != "---":
            if lines[i].startswith("title:"):
                title = lines[i].split(":", 1)[1].strip()
            i += 1
        i += 1
    blocks = []
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        m = re.match(r"^\[\[fig:([^|]+)\|([^\]]+)\]\]$", s)
        if m:
            blocks.append(("fig", (m.group(1), m.group(2))))
            i += 1
            continue
        m = re.match(r"^\[\[tab:([^\]]+)\]\]$", s)
        if m:
            blocks.append(("tabcap", m.group(1)))
            i += 1
            continue
        if s.startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            blocks.append(("code", buf))
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
            aligns = [
                "center"
                if c.startswith(":") and c.endswith(":")
                else "right"
                if c.endswith(":")
                else "left"
                for c in rows[1]
            ]
            blocks.append(("table", (rows[0], rows[2:], aligns)))
            continue
        if s.startswith("- "):
            blocks.append(("li", s[2:]))
            i += 1
            continue
        buf = []
        while i < len(lines):
            t = lines[i].strip()
            if not t or t.startswith(("#", "|", "```", "[[", "- ")) or t == "---":
                break
            buf.append(
                t if not buf else ("" if CJK.match(buf[-1][-1:]) and CJK.match(t[:1]) else " ") + t
            )
            i += 1
        blocks.append(("p", "".join(buf)))
    return title, blocks


def set_font(run, name="Times New Roman", ea=BODY, size=12, bold=None):
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts")
        rpr.append(rf)
    rf.set(qn("w:ascii"), name)
    rf.set(qn("w:hAnsi"), name)
    rf.set(qn("w:eastAsia"), ea)
    run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold


def indent2(p):
    ppr = p._p.get_or_add_pPr()
    ind = ppr.find(qn("w:ind"))
    if ind is None:
        ind = OxmlElement("w:ind")
        ppr.append(ind)
    ind.set(qn("w:firstLineChars"), "200")
    ind.set(qn("w:firstLine"), "480")


INLINE = re.compile(r"(\*\*.+?\*\*|`[^`]+`|<https?://[^>]+>|\*\*.+\*\*)")


def add_inline(p, text, size=12, ea=BODY, name="Times New Roman", bold=False):
    pos = 0
    for m in INLINE.finditer(text):
        if m.start() > pos:
            set_font(p.add_run(text[pos : m.start()]), name, ea, size, bold or None)
        tok = m.group(0)
        if tok.startswith("**"):
            set_font(p.add_run(tok[2:-2]), name, ea, size, True)
        elif tok.startswith("`"):
            set_font(p.add_run(tok[1:-1]), "Consolas", ea, size - 2, bold or None)
        else:
            set_font(p.add_run(tok[1:-1]), name, ea, size - 1, bold or None)
        pos = m.end()
    if pos < len(text):
        set_font(p.add_run(text[pos:]), name, ea, size, bold or None)


def shade(cell, color="F2F2F2"):
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color)
    tcpr.append(shd)


def disp_len(s):
    return sum(2 if CJK.match(c) else 1 for c in s)


def add_table(doc, header, rows, aligns):
    n = len(header)
    t = doc.add_table(rows=1, cols=n)
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement("w:" + edge)
        e.set(qn("w:val"), "single")
        e.set(qn("w:sz"), "4")
        e.set(qn("w:color"), "000000")
        borders.append(e)
    t._tbl.tblPr.append(borders)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    lay = OxmlElement("w:tblLayout")
    lay.set(qn("w:type"), "fixed")
    t._tbl.tblPr.append(lay)
    widths = [
        max(
            6,
            max(disp_len(v) for v in [header[c]] + [r[c] if c < len(r) else "" for r in rows])
            ** 0.7,
        )
        for c in range(n)
    ]
    total = sum(widths)
    cm = [max(2.4, TEXT_W * w / total) for w in widths]
    while sum(cm) > TEXT_W + 0.01:
        k = cm.index(max(cm))
        cm[k] = max(2.4, cm[k] - (sum(cm) - TEXT_W))

    def fill(cell, text, bold=False, align="left"):
        p = cell.paragraphs[0]
        pf = p.paragraph_format
        pf.space_before = pf.space_after = Pt(1)
        pf.line_spacing = 1.0
        p.alignment = {
            "left": WD_ALIGN_PARAGRAPH.LEFT,
            "center": WD_ALIGN_PARAGRAPH.CENTER,
            "right": WD_ALIGN_PARAGRAPH.RIGHT,
        }[align]
        add_inline(p, text, size=10.5, bold=bold)

    hdr = t.rows[0]
    trpr = hdr._tr.get_or_add_trPr()
    trpr.append(OxmlElement("w:tblHeader"))
    for i, text in enumerate(header):
        fill(hdr.cells[i], text, True, "center")
        shade(hdr.cells[i])
    for row in rows:
        cells = t.add_row().cells
        for i in range(n):
            fill(cells[i], row[i] if i < len(row) else "", align=aligns[i])
    for i, col in enumerate(t.columns):
        col.width = Cm(cm[i])
    for i, gc in enumerate(t._tbl.tblGrid.gridCol_lst):
        gc.set(qn("w:w"), str(int(cm[i] * 567)))
    for row in t.rows:
        for i, cell in enumerate(row.cells):
            cell.width = Cm(cm[i])
    sp = doc.add_paragraph()
    sp.paragraph_format.space_after = Pt(0)
    sp.paragraph_format.line_spacing = 0.5


def add_heading(doc, level, text):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt({1: 20, 2: 14, 3: 10}[level])
    pf.space_after = Pt({1: 10, 2: 6, 3: 6}[level])
    pf.line_spacing = 1.5
    pf.keep_with_next = True
    if level == 1:
        pf.page_break_before = True
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(p.add_run(text), HEAD, HEAD, {1: 18, 2: 14, 3: 12}[level], True)


def add_para(doc, text, first_chapter=False):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = pf.space_after = Pt(2)
    pf.line_spacing = 1.5
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    indent2(p)
    add_inline(p, text)
    if first_chapter:
        pf.keep_with_next = False


def add_caption(doc, text, above=False):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(4 if above else 2)
    pf.space_after = Pt(3 if above else 8)
    pf.line_spacing = 1.0
    pf.keep_with_next = above
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(p.add_run(text), HEAD, HEAD, 12, False)


def add_code(doc, lines):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = pf.space_after = Pt(4)
    pf.line_spacing = 1.0
    pf.left_indent = Cm(0.4)
    ppr = p._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), "F6F6F6")
    ppr.append(shd)
    for idx, ln in enumerate(lines):
        r = p.add_run(ln)
        set_font(r, "Consolas", BODY, 9)
        if idx != len(lines) - 1:
            r.add_break()


def add_fig(doc, rel_path, caption):
    path = HERE / rel_path
    if not path.exists():
        sys.exit(f"缺图：{path}")
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(6)
    pf.space_after = Pt(2)
    pf.keep_with_next = True
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    with Image.open(path) as im:
        iw, ih = im.size
    width = 14.4
    height = width * ih / iw
    if height > 17.5:
        height = 17.5
        width = height * iw / ih
    p.add_run().add_picture(str(path), width=Cm(width))
    add_caption(doc, caption)


def sectpr_of(p):
    ppr = p._p.pPr
    return copy.deepcopy(ppr.find(qn("w:sectPr"))) if ppr is not None else None


def add_field(paragraph, instr, size=9):
    r = paragraph.add_run()
    set_font(r, size=size)
    r._r.append(OxmlElement("w:fldChar"))
    r._r[-1].set(qn("w:fldCharType"), "begin")
    r = paragraph.add_run()
    it = OxmlElement("w:instrText")
    it.set(qn("xml:space"), "preserve")
    it.text = instr
    r._r.append(it)
    r = paragraph.add_run()
    e = OxmlElement("w:fldChar")
    e.set(qn("w:fldCharType"), "end")
    r._r.append(e)


def task_lines():
    d = Document(TEMPLATE)
    out = []
    for p in d.paragraphs[31:43]:
        t = re.sub(
            r"（(?=[^）]*(?:黑体|宋体|加粗|居中|Time|行距|缩进|字号|小2|4号|5号))[^）]*）",
            "",
            p.text,
        ).strip()
        if t:
            out.append(t)
    return out


def add_task(doc, lines):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(16)
    p.paragraph_format.line_spacing = 1.5
    set_font(p.add_run("任 务 书"), HEAD, HEAD, 18, True)
    for ln in lines[1:]:
        q = doc.add_paragraph()
        q.paragraph_format.line_spacing = 1.5
        q.paragraph_format.space_after = Pt(3)
        q.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        indent2(q)
        add_inline(q, ln)


def add_toc(doc, headings, page_map):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(14)
    set_font(p.add_run("目　　录"), HEAD, HEAD, 18, True)
    entries = [(1, "任务书", "I")] + [
        (lvl, text, str(page_map.get(text, 0)) or "—") for lvl, text in headings
    ]
    for lvl, text, num in entries:
        q = doc.add_paragraph()
        pf = q.paragraph_format
        pf.space_before = pf.space_after = Pt(1)
        pf.line_spacing = 1.0
        pf.left_indent = Cm(0 if lvl == 1 else 0.85)
        pf.tab_stops.add_tab_stop(Cm(TEXT_W), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
        set_font(q.add_run(text), name="Times New Roman", ea=BODY, size=10.5, bold=(lvl == 1))
        set_font(q.add_run("\t"), size=10.5)
        set_font(q.add_run(num), size=10.5, bold=(lvl == 1))


def build(page_map):
    _, blocks = parse(MD.read_text(encoding="utf-8"))
    d = Document(TEMPLATE)
    body = d.element.body
    paras = list(d.paragraphs)
    sect1 = sectpr_of(paras[93])
    for idx in (5, 6, 7, 26, 27, 28):
        el = paras[idx]._element
        el.getparent().remove(el)
    for p in paras[30:]:
        p._element.getparent().remove(p._element)
    for t in list(d.tables):
        t._element.getparent().remove(t._element)
    cover = paras[8]
    if len(cover.runs) >= 3:
        cover.runs[2].text = "能力受限的端侧智能体运行时"
        set_font(cover.runs[2], HEAD, HEAD, 14, True)
    if len(paras[21].runs) >= 2:
        paras[21].runs[1].text = " 无（单人独立完成） "
    add_task(d, task_lines())

    p = d.add_paragraph()
    p.paragraph_format.page_break_before = True
    headings = [(int(k[1]), v) for k, v in blocks if k in ("h1", "h2")]
    add_toc(d, headings, page_map)
    last = d.add_paragraph()
    last.paragraph_format.space_after = Pt(0)
    last._p.get_or_add_pPr().append(sect1)
    first_h1 = True
    for kind, payload in blocks:
        if kind in ("h1", "h2", "h3"):
            lvl = int(kind[1])
            add_heading(d, lvl, str(payload))
            if lvl == 1 and first_h1:
                d.paragraphs[-1].paragraph_format.page_break_before = False
                first_h1 = False
        elif kind == "p":
            add_para(d, str(payload))
        elif kind == "li":
            q = d.add_paragraph()
            q.paragraph_format.line_spacing = 1.5
            q.paragraph_format.left_indent = Cm(0.75)
            add_inline(q, "· " + str(payload))
        elif kind == "tabcap":
            add_caption(d, str(payload), above=True)
        elif kind == "table":
            add_table(d, *payload)
        elif kind == "code":
            add_code(d, payload)
        elif kind == "fig":
            add_fig(d, *payload)
    bs = body.find(qn("w:sectPr"))
    pg = OxmlElement("w:pgNumType")
    pg.set(qn("w:fmt"), "decimal")
    pg.set(qn("w:start"), "1")
    cols = bs.find(qn("w:cols"))
    bs.insert(list(bs).index(cols), pg) if cols is not None else bs.append(pg)
    sec = d.sections[-1]
    sec.footer.is_linked_to_previous = False
    ftr = sec.footer._element
    for child in list(ftr):
        ftr.remove(child)
    fp = sec.footer.add_paragraph()
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(fp.add_run("第 "), size=9)
    add_field(fp, " PAGE ")
    set_font(fp.add_run(" 页"), size=9)
    d.save(DOCX)
    return headings


def run(argv, **kwargs):
    exe = shutil.which(argv[0]) or argv[0]
    # 参数是常量命令，不含外部输入
    return subprocess.run([exe, *argv[1:]], check=True, **kwargs)  # noqa: S603


def to_pdf():
    if PDF.exists():
        PDF.unlink()
    run(
        ["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(HERE), str(DOCX)],
        capture_output=True,
        timeout=300,
        env=dict(os.environ, HOME=os.environ.get("HOME", "/root")),
    )
    if not PDF.exists():
        sys.exit("PDF 转换失败")


def pdf_pages():
    info = run(["pdfinfo", str(PDF)], capture_output=True, text=True).stdout
    n = int(re.search(r"Pages:\s+(\d+)", info).group(1))
    texts = []
    for i in range(1, n + 1):
        texts.append(
            run(
                ["pdftotext", "-layout", "-f", str(i), "-l", str(i), str(PDF), "-"],
                capture_output=True,
                text=True,
            ).stdout
        )
    return n, texts


def norm(s):
    return re.sub(r"[\s\u3000]", "", s)


def map_pages(texts, headings):
    pages = [norm(t) for t in texts]
    toc = next((i for i, p in enumerate(pages) if "目录" in p), -1)
    if toc < 0:
        sys.exit("找不到目录页")
    start = toc + 1
    while start < len(pages) and "....." in texts[start]:
        start += 1
    first = norm(headings[0][1])
    if first not in pages[start]:
        sys.exit(f"目录之后不是第一章：{headings[0][1]}")
    result = {}
    for _, text in headings:
        key = norm(text)
        for i in range(start, len(pages)):
            if key in pages[i]:
                result[text] = i - start + 1
                break
    return result, start


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-pdf", action="store_true", help="只生成 docx")
    args = ap.parse_args()
    headings = build({})
    _say(f"已生成 {DOCX.name}（目录 {len(headings)} 条，待回填页码）")
    if args.no_pdf:
        return
    to_pdf()
    n1, texts = pdf_pages()
    page_map, start = map_pages(texts, headings)
    missing = [t for _, t in headings if t not in page_map]
    if missing:
        _say(f"警告：{len(missing)} 个标题未定位，例如 {missing[0]}")
    build(page_map)
    to_pdf()
    n2, _ = pdf_pages()
    _say(f"页数：封面 1 + 任务书 {start - 2} + 目录 {1} + 正文 {n2 - start} = 共 {n2} 页")
    _say(f"目录页码已写入：{len(page_map)}/{len(headings)} 条；两遍页数 {n1} → {n2}")
    _say(f"已生成 {PDF.name}")


if __name__ == "__main__":
    main()
