"""v2.21 dispatch route sheet: the stops a dispatcher picked on Map & Locations, in visiting order, printable.

Distances are straight lines between the recorded coordinates (haversine), labelled as such. Road distance and
travel time are not computed here; each stop and the whole route carry a Google Maps link that computes them.
"""

from __future__ import annotations

import io
import math
from datetime import UTC, datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core import notice
from app.services.records import RISK_LABEL

INK, MUTED, RULE = colors.HexColor("#1b1d21"), colors.HexColor("#5d636c"), colors.HexColor("#d5d3cd")
RISK_INK = {"high": "#c62a2f", "medium": "#a8770c", "low": "#1d7a45", "unassessed": "#5d636c"}


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = math.pi / 180
    h = math.sin((b[0] - a[0]) * r / 2) ** 2 + math.cos(a[0] * r) * math.cos(b[0] * r) * math.sin((b[1] - a[1]) * r / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def gmaps_route(points: list[tuple[float, float]]) -> str:
    f = lambda p: f"{p[0]:.6f},{p[1]:.6f}"  # noqa: E731
    url = f"https://www.google.com/maps/dir/?api=1&origin={f(points[0])}&destination={f(points[-1])}&travelmode=driving"
    if len(points) > 2:
        url += "&waypoints=" + "%7C".join(f(p) for p in points[1:-1])
    return url


def build_route_pdf(stops: list[dict], prepared_by: str, share_url: str, generated_at: datetime | None = None) -> bytes:
    """``stops``: ordered dicts with label, pole_code, risk, barangay, lat, lon."""
    now = generated_at or datetime.now(UTC)
    st = {
        "h": ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=INK),
        "p": ParagraphStyle("p", fontName="Helvetica", fontSize=9, leading=12.5, textColor=INK),
        "m": ParagraphStyle("m", fontName="Helvetica", fontSize=8, leading=11, textColor=MUTED),
        "c": ParagraphStyle("c", fontName="Helvetica", fontSize=8.5, leading=11, textColor=INK),
        "cb": ParagraphStyle("cb", fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=INK),
    }
    pts = [(s["lat"], s["lon"]) for s in stops]
    legs = [0.0] + [km(pts[i - 1], pts[i]) for i in range(1, len(pts))]
    rows = [[Paragraph(f"<b>{h}</b>", st["c"]) for h in ("#", "Pole", "Risk", "Barangay", "Coordinates", "From previous")]]
    for i, s in enumerate(stops):
        link = f"https://www.google.com/maps/search/?api=1&query={s['lat']:.6f},{s['lon']:.6f}"
        rows.append([
            Paragraph(str(i + 1), st["cb"]),
            Paragraph(f"<b>{escape(s['label'])}</b><br/><font color='#5d636c'>{escape(s['pole_code'])}</font>", st["c"]),
            Paragraph(f"<font color='{RISK_INK.get(s['risk'], '#5d636c')}'><b>{escape(RISK_LABEL.get(s['risk'], s['risk']))}</b></font>", st["c"]),
            Paragraph(escape(s.get("barangay") or "Unassigned"), st["c"]),
            Paragraph(f"<link href='{escape(link)}' color='#1b1d21'>{s['lat']:.6f}, {s['lon']:.6f}</link>", st["c"]),
            Paragraph("Start" if i == 0 else f"{legs[i]:.2f} km", st["c"]),
        ])
    t = Table(rows, colWidths=[9 * mm, 36 * mm, 26 * mm, 36 * mm, 40 * mm, 27 * mm], repeatRows=1)
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.8, INK), ("LINEBELOW", (0, 1), (-1, -1), 0.4, RULE),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LEFTPADDING", (0, 0), (-1, -1), 3)]))
    route = gmaps_route(pts)
    story = [
        Paragraph("WIREFRAME · Visit route", st["h"]), Spacer(1, 2 * mm),
        Paragraph(f"{len(stops)} stops · prepared by {escape(prepared_by)} · {now.astimezone().strftime('%d %b %Y, %H:%M')}", st["m"]),
        Spacer(1, 5 * mm), t, Spacer(1, 4 * mm),
        Paragraph(f"<b>{sum(legs):.2f} km</b> straight-line distance between the stops, in this order. Road distance and "
                  "travel time are not calculated here; open the route in Google Maps for them.", st["p"]),
        Spacer(1, 2 * mm),
        Paragraph(f"Route in Google Maps: <link href='{escape(route)}' color='#1b1d21'><u>open the route</u></link>", st["p"]),
        Paragraph(f"Same route in WIREFRAME (sign-in required): <link href='{escape(share_url)}' color='#1b1d21'>"
                  f"<u>{escape(share_url)}</u></link>", st["p"]),
        Spacer(1, 6 * mm),
        Paragraph("Risk levels are automated screening results. Verify every pole in the field before any maintenance or "
                  "safety decision; do not touch or climb toward overhead wires.", st["m"]),
    ]
    buf = io.BytesIO()

    def foot(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 10 * mm, notice.SHORT)
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                      title="WIREFRAME dispatch route").build(story, onFirstPage=foot, onLaterPages=foot)
    return buf.getvalue()
