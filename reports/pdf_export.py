"""
Phase 19 v8 (v3.17.326): generic PDF export helper for the analytics
reports.

The four most-used Phase 19 reports (procurement summary, AR aging,
MRR forecast, KPI dashboard) need a one-page printable PDF for
attaching to scheduled-report emails or pasting into a client review
deck. Rather than rewriting the per-report HTML in ReportLab, this
module exposes a small structured-data API: each calling view passes
a `title`, `subtitle`, optional list of `kpis` (label/value cards),
and any number of `tables` (header row + body rows). The renderer
emits a clean letter-size PDF with the same brand colors used by
`psa.pdf` so all PDFs feel like one product.
"""
from __future__ import annotations

import io
from typing import Any, Iterable, List, Mapping, Optional, Sequence

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
)


BRAND_PRIMARY = colors.HexColor('#2c3e50')
BRAND_ACCENT = colors.HexColor('#3498db')
BRAND_MUTED = colors.HexColor('#7f8c8d')
BRAND_WARN = colors.HexColor('#e67e22')


def _styles():
    base = getSampleStyleSheet()
    return {
        'title': ParagraphStyle(
            'rep_title', parent=base['Heading1'], fontSize=18,
            textColor=BRAND_PRIMARY, leading=22, spaceAfter=6),
        'subtitle': ParagraphStyle(
            'rep_subtitle', parent=base['Normal'], fontSize=10,
            textColor=BRAND_MUTED, leading=12, spaceAfter=10),
        'h2': ParagraphStyle(
            'rep_h2', parent=base['Heading2'], fontSize=12,
            textColor=BRAND_PRIMARY, leading=15, spaceAfter=4),
        'normal': ParagraphStyle(
            'rep_normal', parent=base['Normal'], fontSize=9, leading=12),
        'small': ParagraphStyle(
            'rep_small', parent=base['Normal'], fontSize=8,
            textColor=BRAND_MUTED, leading=10),
        'kpi_label': ParagraphStyle(
            'rep_kpi_label', parent=base['Normal'], fontSize=8,
            textColor=BRAND_MUTED, leading=10),
        'kpi_value': ParagraphStyle(
            'rep_kpi_value', parent=base['Normal'], fontSize=14,
            textColor=BRAND_PRIMARY, leading=16),
    }


def render_pdf(
    *,
    title: str,
    subtitle: str = '',
    kpis: Optional[Sequence[Mapping[str, Any]]] = None,
    tables: Optional[Sequence[Mapping[str, Any]]] = None,
    filename: str = 'report.pdf',
):
    """
    Render a structured report as a PDF and return a Django HttpResponse.

    `kpis` is a list of dicts: `[{label, value}, ...]`.
    `tables` is a list of dicts: `[{heading, header_row, body_rows,
        align_right_cols (optional)}, ...]`. `header_row` and each item
    of `body_rows` is a flat list of strings.
    """
    from django.http import HttpResponse

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
        title=title,
    )
    s = _styles()

    story = []
    story.append(Paragraph(title, s['title']))
    if subtitle:
        story.append(Paragraph(subtitle, s['subtitle']))

    # KPI cards as a 4-column table
    if kpis:
        kpi_cells = []
        for k in kpis:
            inner = [
                Paragraph(str(k.get('label', '')), s['kpi_label']),
                Paragraph(str(k.get('value', '')), s['kpi_value']),
            ]
            kpi_cells.append(inner)
        # Pad to a multiple of 4 for clean rows
        while len(kpi_cells) % 4 != 0:
            kpi_cells.append([Paragraph('', s['kpi_label'])])
        rows = [kpi_cells[i:i + 4] for i in range(0, len(kpi_cells), 4)]
        kpi_table = Table(rows, colWidths=[1.75 * inch] * 4)
        kpi_table.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 0.5, BRAND_MUTED),
            ('INNERGRID', (0, 0), (-1, -1), 0.25, BRAND_MUTED),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(kpi_table)
        story.append(Spacer(1, 12))

    if tables:
        for t in tables:
            heading = t.get('heading')
            if heading:
                story.append(Paragraph(heading, s['h2']))
            header = list(t.get('header_row') or [])
            body = [list(r) for r in (t.get('body_rows') or [])]
            data = [header] + body if header else body
            if not data:
                continue
            tbl = Table(data, repeatRows=1 if header else 0)
            style = [
                ('GRID', (0, 0), (-1, -1), 0.25, BRAND_MUTED),
                ('FONTSIZE', (0, 0), (-1, -1), 8.5),
                ('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ]
            if header:
                style += [
                    ('BACKGROUND', (0, 0), (-1, 0), BRAND_PRIMARY),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ]
            for col in (t.get('align_right_cols') or []):
                style.append(('ALIGN', (col, 0), (col, -1), 'RIGHT'))
            tbl.setStyle(TableStyle(style))
            story.append(tbl)
            story.append(Spacer(1, 12))

    doc.build(story)
    buf.seek(0)
    resp = HttpResponse(buf.read(), content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp
