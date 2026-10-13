"""PDF araçları: markdown'dan biçimli PDF, PDF birleştirme ve sayfa ayırma.

Türkçe karakterler için matplotlib ile gelen DejaVu yazı tipi kullanılır (her platformda mevcut).
"""

from __future__ import annotations

import os
import re
from html import escape

from . import ToolError, ToolResult, output_path, rel, resolve_path, tool

_FONTS_READY = False


def _fonts() -> None:
    global _FONTS_READY
    if _FONTS_READY:
        return
    import matplotlib
    from reportlab.lib.fonts import addMapping
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    d = os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data", "fonts", "ttf")
    for name, file in (("DejaVu", "DejaVuSans.ttf"), ("DejaVu-Bold", "DejaVuSans-Bold.ttf"),
                       ("DejaVu-Oblique", "DejaVuSans-Oblique.ttf"), ("DejaVu-BoldOblique", "DejaVuSans-BoldOblique.ttf")):
        pdfmetrics.registerFont(TTFont(name, os.path.join(d, file)))
    addMapping("DejaVu", 0, 0, "DejaVu")
    addMapping("DejaVu", 1, 0, "DejaVu-Bold")
    addMapping("DejaVu", 0, 1, "DejaVu-Oblique")
    addMapping("DejaVu", 1, 1, "DejaVu-BoldOblique")
    _FONTS_READY = True


def _inline(text: str) -> str:
    """**kalın**, *italik*, `kod` → reportlab paragraf işaretlemesi (HTML kaçışlı)."""
    t = escape(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<i>\1</i>", t)
    t = re.sub(r"`(.+?)`", r'<font face="Courier">\1</font>', t)
    return t


@tool(
    "create_pdf",
    "Markdown içerikten biçimli PDF belgesi oluşturur (rapor, teklif, tutanak, fatura özeti vb.). "
    "Desteklenen markdown: # başlıklar, - madde işaretleri, 1. numaralı liste, **kalın**, *italik*, "
    "| tablolar |, --- sayfa sonu, ![açıklama](outputs/grafik.png) görsel.",
    {
        "filename": {"type": "string"},
        "title": {"type": "string", "description": "Belge başlığı (ilk sayfada)"},
        "markdown": {"type": "string"},
        "landscape": {"type": "boolean", "description": "Yatay sayfa (geniş tablolar için)"},
    },
    ["filename", "markdown"],
)
def create_pdf(filename: str, markdown: str, title: str = "", landscape: bool = False) -> ToolResult:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.pagesizes import landscape as to_landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (Image, ListFlowable, ListItem, PageBreak, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)

    _fonts()
    base = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=base["BodyText"], fontName="DejaVu", fontSize=10.5, leading=15)
    heads = {
        1: ParagraphStyle("h1", parent=base["Heading1"], fontName="DejaVu-Bold", fontSize=17, spaceAfter=8),
        2: ParagraphStyle("h2", parent=base["Heading2"], fontName="DejaVu-Bold", fontSize=14, spaceAfter=6),
        3: ParagraphStyle("h3", parent=base["Heading3"], fontName="DejaVu-Bold", fontSize=12, spaceAfter=4),
    }
    title_style = ParagraphStyle("title", parent=base["Title"], fontName="DejaVu-Bold", fontSize=22, spaceAfter=14)
    cell = ParagraphStyle("cell", parent=body, fontSize=9, leading=12)
    header_cell = ParagraphStyle("hdr", parent=cell, fontName="DejaVu-Bold", textColor=colors.white)

    pagesize = to_landscape(A4) if landscape else A4
    width = pagesize[0] - 4 * cm
    story = [Paragraph(escape(title), title_style)] if title else []

    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith("|"):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            block = [r for r in block if not all(re.fullmatch(r":?-{2,}:?", c) for c in r)]
            if block:
                ncol = max(len(r) for r in block)
                data = [
                    [Paragraph(_inline(r[j]) if j < len(r) else "", header_cell if k == 0 else cell) for j in range(ncol)]
                    for k, r in enumerate(block)
                ]
                t = Table(data, colWidths=[width / ncol] * ncol, repeatRows=1)
                t.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F5597")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BBBBBB")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F5FA")]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]))
                story += [t, Spacer(1, 8)]
            continue
        if not s:
            story.append(Spacer(1, 6))
        elif s == "---":
            story.append(PageBreak())
        elif m := re.match(r"^!\[(.*?)\]\((.+?)\)$", s):
            p = resolve_path(m.group(2), must_exist=True)
            img = Image(str(p))
            ratio = min(width / img.imageWidth, 1.0)
            img.drawWidth, img.drawHeight = img.imageWidth * ratio, img.imageHeight * ratio
            story.append(img)
            if m.group(1):
                story.append(Paragraph(f"<i>{escape(m.group(1))}</i>", body))
        elif m := re.match(r"^(#{1,6})\s+(.*)", s):
            story.append(Paragraph(_inline(m.group(2)), heads[min(len(m.group(1)), 3)]))
        elif re.match(r"^([-*•]|\d+[.)])\s+", s):
            numbered = bool(re.match(r"^\d+[.)]", s))
            same_kind = r"^\s*\d+[.)]\s+" if numbered else r"^\s*[-*•]\s+"  # madde ve numaralı listeler karışmaz
            items = []
            while i < len(lines) and re.match(same_kind, lines[i]):
                items.append(ListItem(Paragraph(_inline(re.sub(r"^\s*([-*•]|\d+[.)])\s+", "", lines[i])), body), leftIndent=12))
                i += 1
            story.append(ListFlowable(items, bulletType="1" if numbered else "bullet", start="1" if numbered else None,
                                      bulletFormat="%s." if numbered else None, bulletFontName="DejaVu",
                                      bulletFontSize=10.5, leftIndent=16))
            continue
        else:
            story.append(Paragraph(_inline(s), body))
        i += 1

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("DejaVu", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawRightString(pagesize[0] - 2 * cm, 1.2 * cm, f"Sayfa {doc.page}")
        canvas.restoreState()

    path = output_path(filename, ".pdf")
    doc = SimpleDocTemplate(str(path), pagesize=pagesize, leftMargin=2 * cm, rightMargin=2 * cm,
                            topMargin=2 * cm, bottomMargin=2 * cm, title=title or path.stem)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return ToolResult(f"PDF oluşturuldu: {rel(path)}", files=[rel(path)])


@tool(
    "pdf_merge",
    "Birden çok PDF'i verilen sırayla tek PDF'te birleştirir.",
    {"paths": {"type": "array", "items": {"type": "string"}}, "filename": {"type": "string"}},
    ["paths", "filename"],
)
def pdf_merge(paths: list[str], filename: str) -> ToolResult:
    from pypdf import PdfWriter

    if len(paths) < 2:
        raise ToolError("En az iki PDF gerekli.")
    writer = PdfWriter()
    for p in paths:
        writer.append(str(resolve_path(p, must_exist=True)))
    out = output_path(filename, ".pdf")
    with open(out, "wb") as f:
        writer.write(f)
    return ToolResult(f"{len(paths)} PDF birleştirildi ({len(writer.pages)} sayfa): {rel(out)}", files=[rel(out)])


def _parse_pages(spec: str, total: int) -> list[int]:
    pages: list[int] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a or 1), int(b or total)
        else:
            start = end = int(part)
        if not (1 <= start <= end <= total):
            raise ToolError(f"Geçersiz sayfa aralığı: {part} (belge {total} sayfa)")
        pages.extend(range(start, end + 1))
    if not pages:
        raise ToolError("Sayfa belirtilmedi.")
    return pages


@tool(
    "pdf_extract_pages",
    "PDF'ten belirli sayfaları yeni bir PDF'e çıkarır. pages örn. '1-3,5,8-'.",
    {"path": {"type": "string"}, "pages": {"type": "string"}, "filename": {"type": "string"}},
    ["path", "pages", "filename"],
)
def pdf_extract_pages(path: str, pages: str, filename: str) -> ToolResult:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(resolve_path(path, must_exist=True)))
    wanted = _parse_pages(pages, len(reader.pages))
    writer = PdfWriter()
    for n in wanted:
        writer.add_page(reader.pages[n - 1])
    out = output_path(filename, ".pdf")
    with open(out, "wb") as f:
        writer.write(f)
    return ToolResult(f"{len(wanted)} sayfa çıkarıldı: {rel(out)}", files=[rel(out)])
