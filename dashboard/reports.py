"""CSV / PDF export helpers."""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import pandas as pd
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def weather_csv_bytes(df: pd.DataFrame) -> bytes:
    if df is None or df.empty:
        body = pd.DataFrame({"status": ["No weather data"]})
        return body.to_csv(index=False).encode("utf-8")
    out = df.copy()
    out.index.name = "time"
    return out.to_csv().encode("utf-8")


def build_pdf(
    *,
    region_name: str,
    season: int,
    summary_lines: list[str],
    figure_paths: list[Path],
) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter
    y = height - 0.75 * inch

    c.setFont("Helvetica-Bold", 16)
    c.drawString(0.75 * inch, y, "Shoreline Fruit harvest report")
    y -= 18
    c.setFont("Helvetica", 11)
    c.drawString(0.75 * inch, y, f"{region_name}  •  {season} season")
    y -= 14
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(0.75 * inch, y, f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}  •  Decision support only, not a spray recommendation")
    y -= 22

    c.setFont("Helvetica", 10)
    for line in summary_lines:
        if y < 1.2 * inch:
            c.showPage()
            y = height - 0.75 * inch
            c.setFont("Helvetica", 10)
        c.drawString(0.75 * inch, y, line[:110])
        y -= 14

    for fig in figure_paths:
        if not fig.exists():
            continue
        if y < 4.2 * inch:
            c.showPage()
            y = height - 0.75 * inch
        try:
            img = ImageReader(str(fig))
            iw, ih = img.getSize()
            max_w = width - 1.5 * inch
            max_h = 3.6 * inch
            scale = min(max_w / iw, max_h / ih)
            w, h = iw * scale, ih * scale
            y -= h + 8
            c.drawImage(img, 0.75 * inch, y, width=w, height=h, preserveAspectRatio=True, mask="auto")
            y -= 16
        except Exception:
            continue

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.getvalue()


def morning_sheet_pdf(
    *,
    region_name: str,
    shake_line: str,
    field_line: str,
    frost_line: str,
    plan_rows: list[str],
    checklist: list[str],
) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter
    y = height - 0.7 * inch
    c.setFont("Helvetica-Bold", 16)
    c.drawString(0.7 * inch, y, "Shoreline Fruit morning sheet")
    y -= 16
    c.setFont("Helvetica", 11)
    c.drawString(0.7 * inch, y, region_name)
    y -= 18
    for line in (shake_line, field_line, frost_line):
        c.setFont("Helvetica", 10)
        c.drawString(0.7 * inch, y, line[:110])
        y -= 14
    y -= 8
    c.setFont("Helvetica-Bold", 12)
    c.drawString(0.7 * inch, y, "Shake days")
    y -= 16
    c.setFont("Helvetica", 10)
    for line in plan_rows:
        if y < 1.1 * inch:
            c.showPage()
            y = height - 0.7 * inch
            c.setFont("Helvetica", 10)
        c.drawString(0.7 * inch, y, line[:110])
        y -= 13
    y -= 10
    c.setFont("Helvetica-Bold", 12)
    c.drawString(0.7 * inch, y, "Hydro-cool / frost checklist")
    y -= 16
    c.setFont("Helvetica", 10)
    for line in checklist:
        c.drawString(0.7 * inch, y, f"[ ] {line[:100]}")
        y -= 14
    c.showPage()
    c.save()
    buf.seek(0)
    return buf.getvalue()
