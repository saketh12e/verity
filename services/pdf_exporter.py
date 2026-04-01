"""
PDF export service for Verity research reports.
Uses ReportLab (pure Python, no system dependencies).
Uses ONLY built-in ReportLab fonts and approved TableStyle commands.
"""
import asyncio
import re
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable, Table, TableStyle
)

from agents.contracts import ResearchReport

# Badge colors (text RGB for PDF)
_BADGE_COLORS = {
    "VERIFIED":           colors.Color(34/255, 197/255, 94/255),     # green
    "PARTIALLY_VERIFIED": colors.Color(234/255, 179/255, 8/255),     # yellow
    "CONTESTED":          colors.Color(239/255, 68/255, 68/255),     # red
    "UNVERIFIED":         colors.Color(249/255, 115/255, 22/255),    # orange
    "UNSUPPORTED":        colors.Color(156/255, 163/255, 175/255),   # grey
}

_BADGE_BG = {
    "VERIFIED":           colors.HexColor("#dcfce7"),
    "PARTIALLY_VERIFIED": colors.HexColor("#fef3c7"),
    "CONTESTED":          colors.HexColor("#fee2e2"),
    "UNVERIFIED":         colors.HexColor("#fff7ed"),
    "UNSUPPORTED":        colors.HexColor("#f3f4f6"),
}

# Strip verdict tags from text
_TAG_RE = re.compile(r'\[(VERIFIED|PARTIALLY_VERIFIED|CONTESTED|UNVERIFIED|UNSUPPORTED)\]')


def _strip_tags(text: str) -> str:
    """Remove [VERDICT] tags for plain text rendering."""
    return _TAG_RE.sub('', text).strip()


def _escape_xml(text: str) -> str:
    """Escape text for ReportLab XML/HTML paragraphs."""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


def _build_pdf(report: ResearchReport, query: str = "") -> bytes:
    """Build PDF bytes from a ResearchReport using ReportLab."""
    buf = BytesIO()

    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=1.0 * inch,
        rightMargin=1.0 * inch,
        topMargin=0.8 * inch,
        bottomMargin=0.8 * inch,
        title=report.get("title", "Verity Research Report"),
        author="Verity",
    )

    styles = getSampleStyleSheet()

    # ── Custom styles (built-in fonts only) ───────────────────────────────
    title_style = ParagraphStyle(
        "verity_title",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=18,
        textColor=colors.HexColor("#111827"),
        leading=24,
        spaceAfter=10,
    )
    meta_style = ParagraphStyle(
        "verity_meta",
        parent=styles["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=9,
        textColor=colors.HexColor("#6b7280"),
        spaceAfter=6,
    )
    badge_label_style = ParagraphStyle(
        "badge_label",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        spaceAfter=4,
    )
    summary_label_style = ParagraphStyle(
        "summary_label",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        textColor=colors.HexColor("#3b82f6"),
        spaceAfter=5,
    )
    summary_style = ParagraphStyle(
        "verity_summary",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        textColor=colors.HexColor("#1e3a5f"),
        leading=16,
    )
    h2_style = ParagraphStyle(
        "verity_h2",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        textColor=colors.HexColor("#111827"),
        spaceBefore=14,
        spaceAfter=6,
        leading=18,
    )
    body_style = ParagraphStyle(
        "verity_body",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        textColor=colors.HexColor("#374151"),
        leading=16,
        spaceAfter=8,
    )
    oq_style = ParagraphStyle(
        "open_question",
        parent=styles["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=10,
        textColor=colors.HexColor("#4b5563"),
        leading=16,
        leftIndent=14,
        spaceAfter=8,
    )
    claim_style = ParagraphStyle(
        "claim_text",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        textColor=colors.HexColor("#374151"),
        leading=13,
    )
    source_url_style = ParagraphStyle(
        "source_url",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        textColor=colors.HexColor("#6b7280"),
        leading=11,
    )
    footer_style = ParagraphStyle(
        "footer",
        parent=styles["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=8,
        textColor=colors.HexColor("#9ca3af"),
        alignment=TA_CENTER,
    )
    graph_note_style = ParagraphStyle(
        "graph_note",
        parent=styles["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=9,
        textColor=colors.HexColor("#6b7280"),
        alignment=TA_CENTER,
        spaceAfter=8,
    )

    story = []

    # ── 1. Report Title ───────────────────────────────────────────────────
    story.append(Paragraph(_escape_xml(report.get("title", "Research Report")), title_style))

    # ── 2. Metadata bar ──────────────────────────────────────────────────
    sources_crawled = report.get("sources_crawled", 0)
    sources_after_dedup = report.get("sources_after_dedup", 0)
    sources_used = report.get("sources_used", 0)
    verified_count = report.get("verified_count", 0)
    time_window = report.get("query_recency_window_hours", 8760)

    if time_window <= 24:
        window_str = f"{time_window}h"
    elif time_window <= 168:
        window_str = f"{time_window // 24}d"
    else:
        window_str = f"{time_window // 720}mo" if time_window < 8760 else "1yr"

    meta_text = (
        f"Sources crawled: {sources_crawled} · "
        f"After dedup: {sources_after_dedup} · "
        f"Sources used: {sources_used} · "
        f"Verified: {verified_count} · "
        f"Time window: {window_str}"
    )
    story.append(Paragraph(meta_text, meta_style))
    story.append(Spacer(1, 6))

    # ── 3. Badge summary row ─────────────────────────────────────────────
    pv_count = report.get("claims_partially_verified", 0)
    unverified_count = report.get("unverified_count", 0)
    unsupported_count = report.get("claims_unsupported", 0)

    badge_items = [
        ("VERIFIED", verified_count),
        ("PARTIALLY_VERIFIED", pv_count),
        ("UNVERIFIED", unverified_count),
        ("UNSUPPORTED", unsupported_count),
    ]

    badge_cells = []
    for badge_name, count in badge_items:
        color = _BADGE_COLORS.get(badge_name, colors.gray)
        badge_cells.append(
            Paragraph(
                f'<font color="#{int(color.red*255):02x}{int(color.green*255):02x}{int(color.blue*255):02x}">'
                f'<b>{badge_name} {count}</b></font>',
                badge_label_style,
            )
        )

    badge_table = Table([badge_cells], colWidths=[1.5 * inch] * 4)
    badge_table.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("BACKGROUND", (0, 0), (0, 0), _BADGE_BG["VERIFIED"]),
        ("BACKGROUND", (1, 0), (1, 0), _BADGE_BG["PARTIALLY_VERIFIED"]),
        ("BACKGROUND", (2, 0), (2, 0), _BADGE_BG["UNVERIFIED"]),
        ("BACKGROUND", (3, 0), (3, 0), _BADGE_BG["UNSUPPORTED"]),
    ]))
    story.append(badge_table)
    story.append(Spacer(1, 12))

    # ── 4. Executive Summary ─────────────────────────────────────────────
    story.append(Paragraph("EXECUTIVE SUMMARY", summary_label_style))
    summary_text = report.get("executive_summary", report.get("summary", ""))
    summary_table = Table(
        [[Paragraph(_escape_xml(summary_text), summary_style)]],
        colWidths=[6.0 * inch],
    )
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eff6ff")),
        ("LINEBEFORE", (0, 0), (0, -1), 4, colors.HexColor("#3b82f6")),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 16))

    # ── 5. Report Sections ───────────────────────────────────────────────
    for sec in report.get("sections", []):
        heading = sec.get("heading", "")
        content = _strip_tags(sec.get("content", ""))
        if heading:
            story.append(Paragraph(_escape_xml(heading), h2_style))
            story.append(HRFlowable(
                width="100%", thickness=0.5,
                color=colors.HexColor("#e5e7eb"), spaceAfter=6,
            ))
        if content:
            # Split into paragraphs for better rendering
            paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
            if not paragraphs:
                paragraphs = [content]
            for para in paragraphs:
                story.append(Paragraph(_escape_xml(para), body_style))
        story.append(Spacer(1, 6))

    # ── 6. Claims and Source Evidence Table ───────────────────────────────
    claims = report.get("claims", [])
    if claims:
        story.append(Spacer(1, 10))
        story.append(Paragraph("CLAIMS AND SOURCE EVIDENCE", summary_label_style))
        story.append(Spacer(1, 6))

        # Table header
        table_data = [[
            Paragraph("<b>Badge</b>", badge_label_style),
            Paragraph("<b>Claim</b>", badge_label_style),
            Paragraph("<b>Sources</b>", badge_label_style),
        ]]

        for c in claims:
            badge = c.get("badge", "UNVERIFIED")
            badge_color = _BADGE_COLORS.get(badge, colors.gray)
            color_hex = f"#{int(badge_color.red*255):02x}{int(badge_color.green*255):02x}{int(badge_color.blue*255):02x}"

            badge_cell = Paragraph(
                f'<font color="{color_hex}"><b>{badge}</b></font>',
                badge_label_style,
            )

            claim_text = _escape_xml(c.get("claim", "")[:300])
            claim_cell = Paragraph(claim_text, claim_style)

            source_urls = c.get("sources", [])[:4]
            source_lines = []
            for url in source_urls:
                domain = url.split("/")[2] if len(url.split("/")) > 2 else url[:40]
                source_lines.append(_escape_xml(domain))
            source_cell = Paragraph("<br/>".join(source_lines) if source_lines else "—", source_url_style)

            table_data.append([badge_cell, claim_cell, source_cell])

        claims_table = Table(
            table_data,
            colWidths=[1.2 * inch, 3.4 * inch, 1.8 * inch],
        )
        claims_table.setStyle(TableStyle([
            # Header row
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
            ("TOPPADDING", (0, 0), (-1, 0), 8),
            # All cells
            ("ALIGN", (0, 0), (0, -1), "CENTER"),
            ("ALIGN", (1, 0), (1, -1), "LEFT"),
            ("ALIGN", (2, 0), (2, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 1), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            # Row alternation
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
            # Grid lines
            ("LINEBELOW", (0, 0), (-1, 0), 1, colors.HexColor("#d1d5db")),
            ("LINEBELOW", (0, 1), (-1, -2), 0.5, colors.HexColor("#e5e7eb")),
            ("LINEBELOW", (0, -1), (-1, -1), 1, colors.HexColor("#d1d5db")),
        ]))
        story.append(claims_table)

    # ── Open Questions ───────────────────────────────────────────────────
    oqs = report.get("open_questions", [])
    if oqs:
        story.append(Spacer(1, 14))
        story.append(HRFlowable(
            width="100%", thickness=0.5,
            color=colors.HexColor("#e5e7eb"), spaceAfter=10,
        ))
        story.append(Paragraph("OPEN QUESTIONS", summary_label_style))
        for q in oqs:
            story.append(Paragraph(f"— {_escape_xml(q)}", oq_style))

    # ── 7. Citation Graph Note ───────────────────────────────────────────
    story.append(Spacer(1, 16))
    story.append(Paragraph(
        "Full interactive citation graph available in the web application.",
        graph_note_style,
    ))

    # ── 8. Footer ────────────────────────────────────────────────────────
    story.append(Spacer(1, 12))
    story.append(HRFlowable(
        width="100%", thickness=0.5,
        color=colors.HexColor("#e5e7eb"), spaceAfter=8,
    ))
    generated_at = report.get("generated_at", datetime_now_iso())
    footer_text = f"Generated by Verity · {generated_at}"
    story.append(Paragraph(footer_text, footer_style))

    doc.build(story)
    buf.seek(0)
    return buf.read()


def datetime_now_iso() -> str:
    """Return current UTC datetime as ISO string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


async def export_to_pdf(report: ResearchReport, query: str = "") -> bytes:
    """
    Render the report to PDF bytes asynchronously.
    Runs ReportLab in a thread pool to avoid blocking the event loop.
    """
    return await asyncio.to_thread(_build_pdf, report, query)
