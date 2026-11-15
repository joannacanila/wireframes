"""Printable screening report (Section 3.3.6, Section 3.14.6).

Contents required by the thesis: wire counts per class, CRS and risk level of each
wire, the VNI and its band, the MSPI rank, the flags raised during processing, and the
statement that the contents are computational screening results that require
professional review. The statement appears on page one and in the footer of every
page, so no page can be separated from it.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from typing import Any

import cv2
import numpy as np
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.services.render import render_overlay

INK = colors.HexColor("#14161A")
MUTED = colors.HexColor("#5B616B")
RULE = colors.HexColor("#D9DCE1")
PANEL = colors.HexColor("#F4F5F7")
ACCENT = colors.HexColor("#B8892B")
LEVEL = {"low": colors.HexColor("#1F9D5C"), "medium": colors.HexColor("#B8860B"),
         "high": colors.HexColor("#D13B40"), None: MUTED}

DISCLAIMER = (
    "This report contains computational screening results produced by WIREFRAME from a single "
    "photograph. They are not an engineering inspection and require review by a qualified "
    "professional before any regulatory or maintenance action. The report does not determine the "
    "owner of any wire or name any utility or telecommunication company as responsible for it. "
    "A wire labelled abandoned-wire shows visible signs of abandonment only; its operating status is not "
    "verified. Vegetation contact is a visual observation, not a clearance measurement."
)
FOOTER = "Computational screening result. Professional review required before any action."

CLASS_LABEL = {"power-distribution-lines": "Power distribution line", "telecom-cable": "Telecom / fiber cable",
               "abandoned-wire": "Abandoned wire"}

FLAG_TEXT = {
    "acquisition_unverified": "No EXIF metadata: acquisition envelope could not be verified.",
    "vegetation_contact": "Vegetation touches or overhangs at least one wire (visual observation only).",
    "vni_uncalibrated": "VNI quantiles q01/q99 are provisional (not yet calibrated on local images).",
    "vni_outside_calibration_range": "Raw VNI fell outside the calibration range and was clipped.",
    "confidence_threshold_provisional": "Detection confidence threshold not yet tuned on the validation set.",
    "development_limits": "Allowable sag limits are development placeholders; CRS is for development only.",
    "default_parameters": "No engineering context (span) supplied: reference sag limit used.",
    "illustrative_conductor_data": "Conductor properties are illustrative, not from local data sheets.",
    "default_temperature": "Capture temperature not supplied: reference temperature used.",
    "default_wind": "Wind speed not supplied: still air assumed.",
    "truncated": "Centerline end lies at the image border; the visible span may be cut off.",
    "too_short_to_fit": "Centerline too short to fit a catenary.",
    "service_drop": "Steep service drop / guy wire: not a pole-to-pole span, so not scored for sag "
                    "(checked for vegetation and low hanging).",
}


def _styles() -> dict[str, ParagraphStyle]:
    base = dict(fontName="Helvetica", textColor=INK, leading=12.5, fontSize=9.2, alignment=TA_LEFT)
    return {
        "eyebrow": ParagraphStyle("eyebrow", **{**base, "fontName": "Helvetica-Bold", "fontSize": 7.5,
                                               "textColor": ACCENT, "leading": 10}),
        "title": ParagraphStyle("title", **{**base, "fontName": "Helvetica-Bold", "fontSize": 19, "leading": 23}),
        "h2": ParagraphStyle("h2", **{**base, "fontName": "Helvetica-Bold", "fontSize": 11, "leading": 14,
                                      "spaceBefore": 10, "spaceAfter": 5}),
        "body": ParagraphStyle("body", **base),
        "small": ParagraphStyle("small", **{**base, "fontSize": 7.8, "leading": 10.5, "textColor": MUTED}),
        "cell": ParagraphStyle("cell", **{**base, "fontSize": 8, "leading": 10}),
        "disc": ParagraphStyle("disc", **{**base, "fontSize": 8.4, "leading": 11.5}),
        "kv_k": ParagraphStyle("kv_k", **{**base, "fontSize": 7.2, "textColor": MUTED, "leading": 9}),
        "kv_v": ParagraphStyle("kv_v", **{**base, "fontName": "Helvetica-Bold", "fontSize": 13, "leading": 16}),
    }


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt(v: float | None, nd: int = 3) -> str:
    return "–" if v is None else f"{v:.{nd}f}"


def _flag_line(f: str) -> str:
    if f.startswith("outside_envelope:"):
        return f"Acquisition outside envelope: {f.split(':', 1)[1].replace('_', ' ')}."
    if f.startswith("unverified:"):
        return f"Acquisition parameter not recorded: {f.split(':', 1)[1].replace('_', ' ')}."
    if f.startswith("fit_failed:"):
        return f"Catenary fit failed ({f.split(':', 1)[1].replace('_', ' ')})."
    return FLAG_TEXT.get(f, f.replace("_", " "))


def build_report(result: dict[str, Any], rgb: np.ndarray, generated_at: datetime | None = None) -> bytes:
    st = _styles()
    when = (generated_at or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M UTC")
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=16 * mm, bottomMargin=18 * mm,
                            title=f"WIREFRAME screening report {result['pole']['code']}",
                            author="WIREFRAME", subject="Overhead wire screening report")
    width = A4[0] - 32 * mm

    def on_page(canvas, d):
        canvas.saveState()
        canvas.setStrokeColor(RULE)
        canvas.line(16 * mm, 12 * mm, A4[0] - 16 * mm, 12 * mm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(16 * mm, 8 * mm, FOOTER)
        canvas.drawRightString(A4[0] - 16 * mm, 8 * mm,
                               f"Analysis {result['id'][:8]}  ·  page {d.page}")
        canvas.restoreState()

    s = result["summary"]
    vni = result["vni"] or {}
    rank = result.get("ranking") or {}
    mode = {"annotation": "VALIDATION MODE · REFERENCE MASKS", "model": "FIELD MODE · YOLO11-SEG MODEL"}.get(
        result["segmentation_source"] or "", "")
    story: list[Any] = [
        Paragraph("WIREFRAME · OVERHEAD WIRE SCREENING REPORT" + (f" &#160;&#160;|&#160;&#160; {mode}" if mode else ""),
                  st["eyebrow"]),
        Spacer(1, 3),
        Paragraph(f"Pole {result['pole']['code']}", st["title"]),
        Paragraph(
            f"{result['pole']['barangay'] or 'Barangay not specified'} · analysed "
            f"{result['completed_at'][:16].replace('T', ' ') if result['completed_at'] else '–'} UTC · "
            f"generated {when}", st["small"]),
        Spacer(1, 8),
    ]
    disc = Table([[Paragraph(f"<b>Screening notice.</b> {DISCLAIMER}", st["disc"])]], colWidths=[width])
    disc.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FBF6EA")),
        ("LINEBEFORE", (0, 0), (0, -1), 2.2, ACCENT),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story += [disc, Spacer(1, 10)]

    level = s.get("level")
    kv = [
        ("Status", result["status"].replace("_", " ")),
        ("VNI", f"{_fmt(vni.get('value'), 2)} · {vni.get('band', '–')}"),
        ("Max CRS", f"{_fmt(s['max_crs'], 1)} · {(level or '–').upper()}"),
        ("Mean CRS", _fmt(s["mean_crs"], 1)),
        ("MSPI rank", f"{rank['rank']} of {rank['of']}" if rank.get("rank") else "not ranked"),
    ]
    cells = [[Paragraph(k.upper(), st["kv_k"]) for k, _ in kv], [Paragraph(v, st["kv_v"]) for _, v in kv]]
    t = Table(cells, colWidths=[width / len(kv)] * len(kv))
    ts = [("BACKGROUND", (0, 0), (-1, -1), PANEL), ("LEFTPADDING", (0, 0), (-1, -1), 8),
          ("TOPPADDING", (0, 0), (-1, 0), 7), ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
          ("LINEAFTER", (0, 0), (-2, -1), 0.5, RULE)]
    if level:
        ts.append(("TEXTCOLOR", (2, 1), (2, 1), LEVEL[level]))
    t.setStyle(TableStyle(ts))
    story += [t, Spacer(1, 10)]

    overlay = render_overlay(rgb, result["wires"])
    ok, png = cv2.imencode(".jpg", cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
    if ok:
        oh, ow = overlay.shape[:2]
        img_w = width
        img_h = img_w * oh / ow
        if img_h > 105 * mm:
            img_h = 105 * mm
            img_w = img_h * ow / oh
        story.append(Image(io.BytesIO(png.tobytes()), width=img_w, height=img_h))
        story.append(Paragraph(
            "Mask fill colour = risk level (green low, yellow medium, red high, grey not scored). "
            "White dashed line = chord L; white solid segment = largest perpendicular sag.", st["small"]))

    hz = result.get("hazard")
    if hz:
        from app.services.diagnostic import render_diagnostic
        story.append(Paragraph("Hazard assessment", st["h2"]))
        story.append(Paragraph(f"<b>{_esc(hz['verdict'])}</b>", st["body"]))
        for r in hz.get("reasons", [])[:8]:
            story.append(Paragraph("&bull; " + _esc(r), st["small"]))
        q = result.get("quality")
        if q:
            why = f" Reason: {_esc(q['reason'])}." if q.get("reason") else ""
            story.append(Paragraph(f"Image and model reliability grade: <b>{_esc(q['grade'])}</b> "
                                   f"({_esc(q.get('advice', ''))}).{why}", st["small"]))
        diag = render_diagnostic(rgb, result, panel_long=700)
        ok2, jpg = cv2.imencode(".jpg", diag, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if ok2:
            dh, dw = diag.shape[:2]
            story.append(Image(io.BytesIO(jpg.tobytes()), width=width, height=width * dh / dw))
            story.append(Paragraph(
                "Left: photograph with the reliability grade and its reason. Middle: segmentation (blue power, "
                "violet telecom, orange abandoned, green vegetation). Right: primary spans coloured by CRS level "
                "(S = span) and, where the drop split is enabled, service drops in cyan (D = drop, not scored for sag); vegetation clearance "
                "lines give the pixel gap and an approximate metre value +/- its uncertainty, derived from the "
                "assumed conductor diameter and a +/-1 px wire-width error. Metre values are estimates, not "
                "survey measurements. Below: the verdict reasons.",
                st["small"]))

    counts = s["counts_by_class"]
    story.append(Paragraph("Wire counts by class", st["h2"]))
    veg = s.get("vegetation_count", 0)
    ct = Table([[CLASS_LABEL[c] for c in CLASS_LABEL] + ["Vegetation areas"],
                [str(counts.get(c, 0)) for c in CLASS_LABEL] + [str(veg)]],
               colWidths=[width / 4] * 4)
    ct.setStyle(TableStyle([("FONT", (0, 0), (-1, 0), "Helvetica", 7.5), ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
                            ("FONT", (0, 1), (-1, 1), "Helvetica-Bold", 12), ("LINEBELOW", (0, 1), (-1, 1), 0.5, RULE),
                            ("BOTTOMPADDING", (0, 1), (-1, 1), 6)]))
    story.append(ct)

    story.append(Paragraph("Per-wire metrics", st["h2"]))
    hs = ParagraphStyle("hd", parent=st["cell"], fontName="Helvetica-Bold", fontSize=7.4, textColor=MUTED)
    # Plain-language headers: the standard PDF fonts have no Greek glyphs, and field staff read these.
    head = ["#", "Class", "Conf.", "Chord L (px)", "Sag (px)", "Sag ratio", "Limit", "CRS", "Level", "Notes"]
    rows = [[Paragraph(h, hs) for h in head]]

    def conf_cell(w: dict) -> str:
        return "ref." if result["segmentation_source"] == "annotation" else _fmt(w["confidence"], 2)
    styles = [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.4), ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
              ("LINEBELOW", (0, 0), (-1, 0), 0.6, INK), ("FONT", (0, 1), (-1, -1), "Helvetica", 7.8),
              ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (2, 0), (7, -1), "RIGHT")]
    for i, w in enumerate(result["wires"], 1):
        notes = "; ".join(_flag_line(f) for f in w["flags"] if f not in {
            "development_limits", "default_parameters", "illustrative_conductor_data",
            "default_temperature", "default_wind"}) or ("" if w["status"] == "scored" else w["status"])
        rows.append([
            str(w["index"]), CLASS_LABEL[w["class"]], conf_cell(w), _fmt(w["chord_px"], 0),
            _fmt(w["sag_px"], 1), _fmt(w["sag_ratio"], 4), _fmt(w["delta_lim"], 4), _fmt(w["crs"], 1),
            (w["level"] or "–").upper(), Paragraph(notes, st["cell"]),
        ])
        if w["level"]:
            styles.append(("TEXTCOLOR", (8, i), (8, i), LEVEL[w["level"]]))
            styles.append(("FONT", (8, i), (8, i), "Helvetica-Bold", 7.8))
        styles.append(("LINEBELOW", (0, i), (-1, i), 0.3, RULE))
    if len(rows) == 1:
        rows.append(["–", "No wire detected above the confidence threshold.", "", "", "", "", "", "", "", ""])
    cw = [7, 32, 12, 14, 13, 15, 15, 11, 15, 44]
    wt = Table(rows, colWidths=[c * mm * width / (sum(cw) * mm) for c in cw], repeatRows=1)
    wt.setStyle(TableStyle(styles))
    story.append(wt)
    story.append(Paragraph(
        "Sag ratio = sag / chord L (Equation 8); it is scale-invariant but assumes the span is viewed roughly "
        "perpendicular to the camera axis. Limit = allowable sag ratio (Equation 9). "
        "CRS = min{10, 10 x (sag ratio / limit) x class weight} (Equation 10); "
        "Low &lt; 3.0 &lt;= Medium &lt;= 6.0 &lt; High.", st["small"]))

    flags = sorted(set(result["flags"]) | {f for w in result["wires"] for f in w["flags"]
                                           if f in FLAG_TEXT and f not in {"truncated", "too_short_to_fit", "service_drop"}})
    env = result["acquisition"]["envelope"]
    block: list[Any] = [Paragraph("Processing flags and acquisition envelope", st["h2"])]
    block += [Paragraph(f"• {_flag_line(f)}", st["body"]) for f in flags] or [Paragraph("None.", st["body"])]
    if env:
        er = [["Check", "Value", "Envelope", "Result"]] + [
            [c["name"].replace("_", " "), "–" if c["value"] is None else str(c["value"]),
             f"{c['expected'][0]:g} – {c['expected'][1]:g}", c["status"]] for c in env]
        et = Table(er, colWidths=[width * x for x in (0.34, 0.2, 0.26, 0.2)])
        et.setStyle(TableStyle([("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.4), ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
                                ("FONT", (0, 1), (-1, -1), "Helvetica", 7.8), ("LINEBELOW", (0, 0), (-1, -1), 0.3, RULE)]))
        block += [Spacer(1, 5), et]
    story.append(KeepTogether(block))

    revs = result.get("reviews") or []
    story.append(Paragraph("Professional review", st["h2"]))
    if not revs:
        story.append(Paragraph("<b>Not yet reviewed.</b> No qualified professional has recorded a decision on this "
                               "analysis. The scores above are computational screening output only.", st["body"]))
    for r in revs:
        what = {"confirmed": "Confirmed the computed result", "dismissed": "Dismissed the computed result",
                "overridden": f"Overrode the risk level to <b>{(r['override_level'] or '').upper()}</b>"}[r["decision"]]
        story.append(Paragraph(f"{what} · {r['by']} · {r['at'][:16].replace('T', ' ')} UTC"
                               + (f"<br/><font color='#5B616B'>{_esc(r['note'])}</font>" if r["note"] else ""), st["body"]))
        story.append(Spacer(1, 4))
    if revs:
        story.append(Paragraph("Computed CRS values are never altered by a review; an override is recorded beside them.",
                               st["small"]))

    p = result["parameters"]
    story.append(Paragraph("Parameters", st["h2"]))
    story.append(Paragraph(
        f"Configuration fingerprint <b>{p['fingerprint']}</b>. Segmentation source: "
        f"<b>{result['segmentation_source'] or '–'}</b>. Provisional (uncalibrated) blocks: "
        f"{', '.join(p['provisional']) or 'none'}. VNI radius factor {vni.get('radius_factor', '–')}; "
        f"raw VNI {_fmt(vni.get('raw'), 5)}.", st["body"]))

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


def build_sites_report(payload: dict, sens: dict, prepared_by: str, provisional: list[str],
                       generated_at: datetime | None = None) -> bytes:
    """Multi-site executive report: MSPI ranking, review state and weight sensitivity."""
    st = _styles()
    when = (generated_at or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M UTC")
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm,
                            bottomMargin=18 * mm, title="WIREFRAME multi-site priority report", author="WIREFRAME")
    width = A4[0] - 32 * mm

    def on_page(canvas, d):
        canvas.saveState()
        canvas.setStrokeColor(RULE)
        canvas.line(16 * mm, 12 * mm, A4[0] - 16 * mm, 12 * mm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(16 * mm, 8 * mm, FOOTER)
        canvas.drawRightString(A4[0] - 16 * mm, 8 * mm, f"Multi-site report · page {d.page}")
        canvas.restoreState()

    w = payload["weights"]
    rows = payload["sites"]
    ranked = [r for r in rows if r["rank"] is not None]
    levels = {k: sum(1 for r in ranked if r["level"] == k) for k in ("high", "medium", "low")}
    story: list[Any] = [
        Paragraph("WIREFRAME · MULTI-SITE PRIORITY REPORT", st["eyebrow"]), Spacer(1, 3),
        Paragraph("Inspection priority across poles", st["title"]),
        Paragraph(f"Prepared by {_esc(prepared_by)} · generated {when}", st["small"]), Spacer(1, 8),
    ]
    disc = Table([[Paragraph(f"<b>Screening notice.</b> {DISCLAIMER}", st["disc"])]], colWidths=[width])
    disc.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FBF6EA")),
                              ("LINEBEFORE", (0, 0), (0, -1), 2.2, ACCENT), ("LEFTPADDING", (0, 0), (-1, -1), 9),
                              ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    story += [disc, Spacer(1, 10)]
    kv = [("Poles ranked", str(len(ranked))), ("High", str(levels["high"])), ("Medium", str(levels["medium"])),
          ("Low", str(levels["low"])), ("Weights", f"{w['alpha']:.2f} / {w['beta']:.2f} / {w['gamma']:.2f}")]
    t = Table([[Paragraph(k.upper(), st["kv_k"]) for k, _ in kv], [Paragraph(v, st["kv_v"]) for _, v in kv]],
              colWidths=[width / len(kv)] * len(kv))
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PANEL), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                           ("TOPPADDING", (0, 0), (-1, 0), 7), ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
                           ("LINEAFTER", (0, 0), (-2, -1), 0.5, RULE), ("TEXTCOLOR", (1, 1), (1, 1), LEVEL["high"]),
                           ("TEXTCOLOR", (2, 1), (2, 1), LEVEL["medium"]), ("TEXTCOLOR", (3, 1), (3, 1), LEVEL["low"])]))
    story += [t, Spacer(1, 6), Paragraph(
        "MSPI = alpha x VNI + beta x mean(CRS)/10 + gamma x max(CRS)/10 (Equation 11); weights are listed as "
        "alpha / beta / gamma. It orders poles only; each pole keeps the risk level of its worst wire.", st["small"])]

    story.append(Paragraph("Ranked poles", st["h2"]))
    rng = sens.get("rank_range", {})
    head = ["Rank", "Pole", "Barangay", "MSPI", "Max CRS", "Level", "VNI", "Wires", "Rank over grid"]
    data = [head]
    styles = [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.4), ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
              ("LINEBELOW", (0, 0), (-1, 0), 0.6, INK), ("FONT", (0, 1), (-1, -1), "Helvetica", 7.8),
              ("ALIGN", (3, 0), (4, -1), "RIGHT"), ("ALIGN", (6, 0), (7, -1), "RIGHT")]
    for i, r in enumerate(rows, 1):
        rr = rng.get(r["pole_code"])
        data.append([str(r["rank"] or "–"), r["pole_code"], r["barangay"] or "–", _fmt(r["mspi"], 3),
                     _fmt(r["max_crs"], 1), (r["level"] or "not ranked").upper(), _fmt(r["vni"], 2), str(r["n_wires"]),
                     f"{rr['best']}–{rr['worst']}" if rr else "–"])
        if r["level"]:
            styles += [("TEXTCOLOR", (5, i), (5, i), LEVEL[r["level"]]), ("FONT", (5, i), (5, i), "Helvetica-Bold", 7.8)]
        styles.append(("LINEBELOW", (0, i), (-1, i), 0.3, RULE))
    cw = [10, 26, 30, 15, 15, 18, 12, 11, 20]
    tb = Table(data, colWidths=[c * width / sum(cw) for c in cw], repeatRows=1)
    tb.setStyle(TableStyle(styles))
    story.append(tb)

    story.append(Paragraph("Sensitivity to the weights", st["h2"]))
    if sens.get("n_sites", 0) >= 2:
        story.append(Paragraph(
            f"Over the {len(sens['grid'])} weight settings on a 0.1 grid, Spearman's rho between each ranking and the "
            f"ranking above has a minimum of <b>{_fmt(sens['min_rho'], 2)}</b> and a mean of <b>{_fmt(sens['mean_rho'], 2)}</b>. "
            "\"Rank over grid\" gives each pole's best and worst rank across all settings; a narrow range means the "
            "pole's priority does not depend on the choice of weights.", st["body"]))
    else:
        story.append(Paragraph("Fewer than two ranked poles: no sensitivity analysis.", st["body"]))
    story.append(Paragraph("Parameters", st["h2"]))
    story.append(Paragraph(f"Provisional (uncalibrated) blocks: {', '.join(provisional) or 'none'}.", st["body"]))
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()
