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
from xml.sax.saxutils import escape

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

from app.core import notice
from app.services.render import render_overlay

INK = colors.HexColor("#14161A")
MUTED = colors.HexColor("#5B616B")
RULE = colors.HexColor("#D9DCE1")
PANEL = colors.HexColor("#F4F5F7")
ACCENT = colors.HexColor("#B8892B")
LEVEL = {"low": colors.HexColor("#1F9D5C"), "medium": colors.HexColor("#B8860B"),
         "high": colors.HexColor("#D13B40"), None: MUTED}

# v2.19: the approved notice (app/core/notice.py) opens every report; SCOPE adds what this report does not determine.
DISCLAIMER = notice.TEXT
SCOPE = (
    "The report does not determine the "
    "owner of any wire or name any utility or telecommunication company as responsible for it. "
    "A wire labelled abandoned-wire shows visible signs of abandonment only; its operating status is not "
    "verified. Vegetation contact is a visual observation, not a clearance measurement."
)
FOOTER = notice.SHORT

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


def build_technical_report(result: dict[str, Any], rgb: np.ndarray, generated_at: datetime | None = None) -> bytes:
    """The v2.8-v2.19 technical screening report (scores, fit parameters, diagnostics). Staff only since v2.20."""
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
    insp = result.get("inspection") or {}
    place = ", ".join(x for x in (insp.get("barangay"), insp.get("city")) if x)
    story: list[Any] = [
        Paragraph("WIREFRAME · OVERHEAD WIRE SCREENING REPORT" + (f" &#160;&#160;|&#160;&#160; {mode}" if mode else ""),
                  st["eyebrow"]),
        Spacer(1, 3),
        Paragraph(escape(insp.get("name") or f"Pole {result['pole']['code']}"), st["title"]),
        Paragraph(
            f"{escape(place) or 'Location not specified'} · pole {escape(result['pole']['code'])} · "
            f"file {escape(result['image'].get('original_filename') or '–')} · analysed "
            f"{result['completed_at'][:16].replace('T', ' ') if result['completed_at'] else '–'} UTC · "
            f"generated {when}", st["small"]),
        Spacer(1, 8),
    ]
    disc = Table([[Paragraph(f"<b>{notice.TITLE}</b><br/>{DISCLAIMER}<br/><font size=7.6 color='#5B616B'>{SCOPE}</font>", st["disc"])]], colWidths=[width])
    disc.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FBF6EA")),
        ("LINEBEFORE", (0, 0), (0, -1), 2.2, ACCENT),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story += [disc, Spacer(1, 10)]
    g = result.get("guardrail") or {}
    if g.get("requires_retake"):  # v2.17: the warning shown in the app travels with the report
        choice = (g.get("choice") or {}).get("action")
        text = ("<b>Assessment may be inaccurate.</b> Some wires could not be identified reliably from this photo; "
                "review the findings carefully. A retake was recommended"
                + {"proceed": " and the user chose to proceed with this photo.",
                   "retake": " and the photo was retaken (see the linked analysis)."}.get(choice, ".")
                + "".join(f"<br/>&bull; {_esc(x)}" for x in g.get("quality_reasons") or []))
        warn = Table([[Paragraph(text, st["disc"])]], colWidths=[width])
        warn.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FDF0E6")),
            ("LINEBEFORE", (0, 0), (0, -1), 2.2, colors.HexColor("#B7791F")),
            ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        story += [warn, Spacer(1, 10)]

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
                       generated_at: datetime | None = None, technical: bool = False) -> bytes:
    """Multi-site report. Default (v2.19): the inspection order with risk level, review and location in plain
    terms. ``technical`` (system area): the MSPI scores, the weights and the weight sensitivity as before."""
    if not technical:
        return _dispatch_report(payload, prepared_by, generated_at)
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
    disc = Table([[Paragraph(f"<b>{notice.TITLE}</b><br/>{DISCLAIMER}<br/><font size=7.6 color='#5B616B'>{SCOPE}</font>", st["disc"])]], colWidths=[width])
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


RISK_TEXT = {"high": "High risk", "medium": "Moderate risk", "low": "Low risk", "unassessed": "Assessment incomplete",
             "failed": "Processing failed", "processing": "Processing"}
REVIEW_TEXT = {"pending": "Needs review", "not_reviewed": "Not reviewed", "confirmed": "Confirmed",
               "flagged": "Flagged for inspection", "overridden": "Level changed", "dismissed": "Dismissed"}


def _dispatch_report(payload: dict, prepared_by: str, generated_at: datetime | None = None) -> bytes:
    """v2.19 operational priority report: what to inspect first and where, without scores or formulas."""
    st = _styles()
    when = (generated_at or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M UTC")
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm,
                            bottomMargin=18 * mm, title="WIREFRAME inspection priority report", author="WIREFRAME")
    width = A4[0] - 32 * mm

    def on_page(canvas, d):
        canvas.saveState()
        canvas.setStrokeColor(RULE)
        canvas.line(16 * mm, 12 * mm, A4[0] - 16 * mm, 12 * mm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(16 * mm, 8 * mm, FOOTER)
        canvas.drawRightString(A4[0] - 16 * mm, 8 * mm, f"Page {d.page}")
        canvas.restoreState()

    rows = payload["sites"]
    ranked = [r for r in rows if r["rank"] is not None]
    risk = {k: sum(1 for r in rows if r.get("risk") == k) for k in ("high", "medium", "low", "unassessed")}
    story: list[Any] = [
        Paragraph("WIREFRAME · INSPECTION PRIORITY", st["eyebrow"]), Spacer(1, 3),
        Paragraph("Inspection priority by pole", st["title"]),
        Paragraph(f"Prepared by {_esc(prepared_by)} · generated {when}", st["small"]), Spacer(1, 8),
    ]
    disc = Table([[Paragraph(f"<b>{notice.TITLE}</b><br/>{DISCLAIMER}", st["disc"])]], colWidths=[width])
    disc.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FBF6EA")),
                              ("LINEBEFORE", (0, 0), (0, -1), 2.2, ACCENT), ("LEFTPADDING", (0, 0), (-1, -1), 9),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                              ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    story += [disc, Spacer(1, 10)]
    kv = [("Poles", str(len(rows))), ("High risk", str(risk["high"])), ("Moderate", str(risk["medium"])),
          ("Low", str(risk["low"])), ("Assessment incomplete", str(risk["unassessed"]))]
    t = Table([[Paragraph(k.upper(), st["kv_k"]) for k, _ in kv], [Paragraph(v, st["kv_v"]) for _, v in kv]],
              colWidths=[width / len(kv)] * len(kv))
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PANEL), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                           ("TOPPADDING", (0, 0), (-1, 0), 7), ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
                           ("LINEAFTER", (0, 0), (-2, -1), 0.5, RULE), ("TEXTCOLOR", (1, 1), (1, 1), LEVEL["high"]),
                           ("TEXTCOLOR", (2, 1), (2, 1), LEVEL["medium"]), ("TEXTCOLOR", (3, 1), (3, 1), LEVEL["low"])]))
    story += [t, Spacer(1, 6), Paragraph(
        "Poles are listed in the order WIREFRAME suggests inspecting them. A low-risk result is not a safety "
        "clearance, and a pole that could not be assessed needs a new photograph or a field visit.", st["small"])]
    story.append(Paragraph("Inspection order", st["h2"]))
    head = ["#", "Pole", "Barangay", "Risk level", "Review", "Location", "Inspected"]
    data = [head]
    styles = [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.4), ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
              ("LINEBELOW", (0, 0), (-1, 0), 0.6, INK), ("FONT", (0, 1), (-1, -1), "Helvetica", 7.8)]
    for i, r in enumerate(rows, 1):
        loc = r.get("location")
        data.append([str(r["rank"] or "–"), r["pole_code"], r["barangay"] or "Not recorded",
                     RISK_TEXT.get(r.get("risk"), "–"), REVIEW_TEXT.get(r.get("review"), "–"),
                     f"{loc['lat']:.5f}, {loc['lon']:.5f}" if loc else "No GPS", r["analyzed_at"][:10]])
        lv = {"high": "high", "medium": "medium", "low": "low"}.get(r.get("risk"))
        if lv:
            styles += [("TEXTCOLOR", (3, i), (3, i), LEVEL[lv]), ("FONT", (3, i), (3, i), "Helvetica-Bold", 7.8)]
        styles.append(("LINEBELOW", (0, i), (-1, i), 0.3, RULE))
    cw = [7, 22, 28, 20, 24, 28, 17]
    tb = Table(data, colWidths=[c * width / sum(cw) for c in cw], repeatRows=1)
    tb.setStyle(TableStyle(styles))
    story.append(tb)
    if len(ranked) < len(rows):
        story.append(Spacer(1, 6))
        story.append(Paragraph(f"{len(rows) - len(ranked)} pole(s) have no measured wire and are listed last "
                               "without a position in the order.", st["small"]))
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()



# ============================================================================ v2.20 formal screening report
F_INK = colors.HexColor("#1E293B")
F_RULE = colors.HexColor("#CBD5E1")
F_RED = colors.HexColor("#991B1B")
F_AMBER = colors.HexColor("#B45309")
F_MUTED = colors.HexColor("#475569")
F_HEAD = colors.HexColor("#F1F5F9")


def _fstyles() -> dict[str, ParagraphStyle]:
    b = dict(fontName="Helvetica", textColor=F_INK, fontSize=9.6, leading=13.4, alignment=TA_LEFT)
    return {
        "mast": ParagraphStyle("mast", **{**b, "fontName": "Helvetica-Bold", "fontSize": 15, "leading": 19}),
        "mast2": ParagraphStyle("mast2", **{**b, "fontSize": 9, "textColor": F_MUTED}),
        "h": ParagraphStyle("h", **{**b, "fontName": "Helvetica-Bold", "fontSize": 11.5, "leading": 15, "spaceBefore": 10, "spaceAfter": 5,
                                     "keepWithNext": 1}),
        "body": ParagraphStyle("body", **b),
        "small": ParagraphStyle("small", **{**b, "fontSize": 8.2, "leading": 11, "textColor": F_MUTED}),
        "cell": ParagraphStyle("cell", **{**b, "fontSize": 8.8, "leading": 11.6}),
        "cellb": ParagraphStyle("cellb", **{**b, "fontName": "Helvetica-Bold", "fontSize": 8.8, "leading": 11.6}),
        "th": ParagraphStyle("th", **{**b, "fontName": "Helvetica-Bold", "fontSize": 8.2, "leading": 10.5, "textColor": F_MUTED}),
        "lead": ParagraphStyle("lead", **{**b, "fontName": "Helvetica-Bold", "fontSize": 12, "leading": 16}),
    }


def _kv_table(rows: list[tuple[str, str]], width: float, st, key: float = 0.28) -> Table:
    t = Table([[Paragraph(k, st["th"]), Paragraph(v, st["cell"])] for k, v in rows], colWidths=[width * key, width * (1 - key)])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, F_RULE), ("BACKGROUND", (0, 0), (0, -1), F_HEAD),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LEFTPADDING", (0, 0), (-1, -1), 6)]))
    return t


def _evidence_image(rgb: np.ndarray, flagged: list[dict]) -> np.ndarray:
    """The original photo with only the flagged spans drawn (no full-image mask), numbered as in section 4.0."""
    from app.services.render import render_overlay
    return render_overlay(rgb, flagged, max_side=1800) if flagged else rgb


def _numbered_canvas(report_id: str):
    """A canvas that knows the page count, for the compact "Page X of Y" footer."""
    from reportlab.pdfgen import canvas as rl_canvas

    class Numbered(rl_canvas.Canvas):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._pages = []

        def showPage(self):  # noqa: N802 (reportlab API)
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            n = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self.saveState()
                self.setStrokeColor(F_RULE)
                self.line(18 * mm, 13 * mm, A4[0] - 18 * mm, 13 * mm)
                self.setFont("Helvetica", 7.4)
                self.setFillColor(F_MUTED)
                self.drawString(18 * mm, 9 * mm, f"{report_id} · Preliminary CV screening only. Field verification required")
                self.drawRightString(A4[0] - 18 * mm, 9 * mm, f"Page {self._pageNumber} of {n}")
                self.restoreState()
                super().showPage()
            super().save()
    return Numbered


def _qr(url: str, size: float):
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    w = QrCodeWidget(url)
    x0, y0, x1, y1 = w.getBounds()
    d = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    d.add(w)
    return d


def build_report(result: dict[str, Any], rgb: np.ndarray, generated_at: datetime | None = None) -> bytes:
    """v2.21 formal field screening report: print-first and compact. Every value comes from ``result``
    (serialize_result) through app/services/plain.py, the same reading as the result page: one severity vocabulary
    (Critical / Minor sag / Within reference / Not assessed) and the same counts."""
    from app.services import plain
    st = _fstyles()
    now = generated_at or datetime.now(UTC)
    buf = io.BytesIO()
    pole = result["pole"]["code"]
    report_id = f"WF-{result['id'][:8].upper()}"
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=14 * mm, bottomMargin=18 * mm,
                            title=f"WIREFRAME screening report {report_id}", author="WIREFRAME",
                            subject="Overhead utility screening report")
    width = A4[0] - 36 * mm
    sm = plain.summary(result)
    c = sm["counts"]
    insp = result.get("inspection") or {}
    loc = result["pole"].get("location")
    when = (result.get("completed_at") or result["created_at"])[:16].replace("T", " ")
    mode = "VALIDATION MODE · REFERENCE MASKS" if result.get("segmentation_source") == "annotation" else None
    hexc = lambda col: col.hexval().replace("0x", "#")  # noqa: E731
    SEV_COL = {"high": F_RED, "medium": F_AMBER, "low": colors.HexColor("#166534")}

    story: list[Any] = []
    mast = Table([[Paragraph("WIREFRAME | Overhead Utility Screening Report", st["mast"]),
                   Paragraph(f"Report ID <b>{report_id}</b><br/>Report date {now.strftime('%Y-%m-%d')}", st["mast2"])]],
                 colWidths=[width * 0.68, width * 0.32])
    mast.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 1.6, F_INK), ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                              ("ALIGN", (1, 0), (1, 0), "RIGHT"), ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [mast, Spacer(1, 5)]

    # overview + location side by side
    meta = [("Pole code", _esc(pole)),
            ("Inspection", _esc(insp.get("name") or "Not named")),
            ("Photo", _esc(result["image"].get("original_filename") or "–")),
            ("Date and time", f"{when} UTC")]
    lrows = [("Barangay", _esc(insp.get("barangay") or "Unassigned")), ("City", _esc(insp.get("city") or "Not recorded"))]
    qr = None
    if loc:
        gm = f"https://www.google.com/maps/search/?api=1&query={loc['lat']:.6f},{loc['lon']:.6f}"
        lrows += [("Latitude", f"{loc['lat']:.6f}"), ("Longitude", f"{loc['lon']:.6f}"),
                  ("Source", "photo GPS" if loc["source"] == "exif" else "entered or pinned"),
                  ("Map", f"<link href='{gm}' color='#1E293B'><u>Open in Google Maps</u></link>")]
        qr = _qr(gm, 22 * mm)
    else:
        lrows.append(("Coordinates", "No GPS recorded"))
    half = (width - 6) / 2
    loc_tbl = _kv_table(lrows, half - (24 * mm if qr else 0), st, key=0.38)
    right = Table([[loc_tbl, qr]], colWidths=[half - 24 * mm, 24 * mm]) if qr else loc_tbl
    if qr:
        right.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                   ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("ALIGN", (1, 0), (1, 0), "RIGHT")]))
    top = Table([[Paragraph("Inspection overview", st["th"]), Paragraph("Inspection location", st["th"])],
                 [_kv_table(meta, half, st), right]], colWidths=[half + 6, half])
    top.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("RIGHTPADDING", (0, 0), (0, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, 0), 3)]))
    story.append(top)
    if mode:
        story += [Spacer(1, 4), Paragraph(f"<b>Screening mode:</b> {mode}", st["body"])]

    # 1.0 summary
    story.append(Paragraph("1.0 Inspection Summary", st["h"]))
    lead_col = {"high": F_RED, "medium": F_AMBER}.get(sm["level"], F_INK)
    story.append(Paragraph(f"<font color='{hexc(lead_col)}'>{_esc(sm['headline'])}</font>", st["lead"]))
    for line in sm["lines"]:
        story.append(Paragraph(_esc(line), st["body"]))
    types = ", ".join(f"{k}: {v}" for k, v in sorted(sm["types"].items())) or "none detected"
    measured = [w for w in result.get("wires") or [] if w.get("sag_ratio") is not None]
    # S4: severity counts only when a validated reference produced severities; otherwise measurements only
    sev = (("Critical", c["critical"]), ("Minor sag", c["minor_sag"]), ("Within reference", c["within_reference"])) \
        if sm["assessed"] else (("Sag ratio measured", len(measured)), ("Severity", "none"), ("Sag reference", "none validated"))
    cells = [[Paragraph(f"<b>{v}</b><br/><font size='7.6' color='{hexc(F_MUTED)}'>{k}</font>", st["cell"]) for k, v in (
        ("Wires detected", sm["total"]), ("Assessed", sm["assessed"]), ("Not assessed", c["not_assessed"]), *sev)]]
    ct = Table(cells, colWidths=[width / 6] * 6)
    ct.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, F_RULE), ("TOPPADDING", (0, 0), (-1, -1), 5),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("LEFTPADDING", (0, 0), (-1, -1), 6),
                            *([("TEXTCOLOR", (3, 0), (3, 0), F_RED)] if sm["assessed"] else [])]))
    story += [Spacer(1, 5), ct, Spacer(1, 3), Paragraph(f"Wire types detected: {_esc(types)}.", st["small"])]
    basis = (result.get("assessment_basis") or {}).get("notice")
    if basis:
        bt = Table([[Paragraph(f"<b>Historical result.</b> {_esc(basis)}", st["body"])]], colWidths=[width])
        bt.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, F_AMBER), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                                ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        story += [Spacer(1, 5), bt]
    g = result.get("guardrail") or {}
    if g.get("requires_retake") or (sm["total"] and c["not_assessed"] / sm["total"] > 0.5):
        choice = (g.get("choice") or {}).get("action")
        text = (("<b>Assessment may be inaccurate.</b> Some wires could not be identified reliably from this photo. "
                 if g.get("requires_retake") else "<b>Assessment may be incomplete.</b> ") + (
            f"{c['not_assessed']} of {sm['total']} detected wires could not be assessed. " if c["not_assessed"] else "")
            + "A retake showing both poles and the full span is recommended"
            + {"proceed": "; the user chose to proceed with this photo.", "retake": "; the photo was retaken."}.get(choice, "."))
        w = Table([[Paragraph(text, st["body"])]], colWidths=[width])
        w.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, F_AMBER), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                               ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        story += [Spacer(1, 5), w]

    # 2.0 evidence
    fig_block: list[Any] = [Paragraph("2.0 Annotated Photographic Evidence", st["h"])]
    img = _evidence_image(rgb, sm["flagged"])
    ok, jpg = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
    if ok:
        oh, ow = img.shape[:2]
        iw = width - 4
        ih = iw * oh / ow
        if ih > 88 * mm:
            ih = 88 * mm
            iw = ih * ow / oh
        fig = Table([[Image(io.BytesIO(jpg.tobytes()), width=iw, height=ih)]], colWidths=[width])
        fig.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, F_RULE), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                                 ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        fig_block.append(fig)
    fig_block.append(Paragraph(
        ("Figure 1. Flagged wires only. Red: Critical; amber: Minor sag. The number on each wire is its span ID in "
         "section 4.0 (wire 58 = SP-058). Wires within reference and wires not assessed are not drawn.") if sm["flagged"] else
        "Figure 1. Site photograph. No wire was flagged, so none is drawn.", st["small"]))
    story.append(KeepTogether(fig_block))

    # 3.0 actions
    story.append(Paragraph("3.0 Recommended Field Actions", st["h"]))
    acts = []
    if sm["flagged"]:
        acts.append("Forward this report to the barangay office and to the operator of the affected lines (electric "
                    "utility for power lines, telecommunications provider for cables) for field verification. "
                    "This report does not establish who owns any wire.")
        acts.append("Verify the Critical spans in section 4.0 first; check Minor sag spans at the next routine inspection.")
    elif not sm["assessed"] and measured:
        acts.append("No sag severity was determined: no validated sag reference exists for these wires, so the "
                    "image-derived sag ratios in section 4.0 are measurements only. They do not show that a wire is "
                    "safe or unsafe. Arrange an inspection by a qualified person for any concern.")
    else:
        acts.append("No wire was flagged in the assessed sections. Keep the report on file; it does not certify the "
                    "pole or its wires as safe.")
    no_ref = sum(w.get("reason_code") in ("no_validated_reference", "withdrawn_reference") for w in result.get("wires") or [])
    if c["not_assessed"] - no_ref > 0:
        acts.append(f"{c['not_assessed'] - no_ref} detected wire(s) could not be measured completely. A new photograph "
                    "showing both poles and the full span against open sky, or a field visit, is needed to cover them.")
    acts.append("Do not touch, pull or move any overhead wire; treat every conductor as energised.")
    for i, a in enumerate(acts, 1):
        story.append(Paragraph(f"3.{i} &#160; {_esc(a)}", st["body"]))
        story.append(Spacer(1, 1.5))

    # 4.0 schedule
    story.append(Paragraph("4.0 Flagged Spans Schedule", st["h"]))
    if sm["flagged"]:
        head = ["Span ID", "Severity", "Sag (%)", "Observation", "Recommended action"]
        rows = [[Paragraph(h, st["th"]) for h in head]]
        sty = [("LINEBELOW", (0, 0), (-1, 0), 0.8, F_INK), ("LINEBELOW", (0, 1), (-1, -1), 0.4, F_RULE),
               ("BACKGROUND", (0, 0), (-1, 0), F_HEAD), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
               ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]
        for w in sm["flagged"]:
            lim = w.get("delta_lim")
            col = hexc(SEV_COL.get(w.get("level"), F_INK))
            rows.append([Paragraph(f"<b>{plain.span_id(w['index'])}</b>", st["cell"]),
                         Paragraph(f"<font color='{col}'><b>{plain.SEVERITY[w.get('level')]}</b></font>", st["cell"]),
                         Paragraph(f"{w['sag_ratio'] * 100:.1f}" + (f" <font color='{hexc(F_MUTED)}'>/ {lim * 100:.1f}</font>" if lim else ""), st["cell"]),
                         Paragraph(_esc(plain.short_observation(w)), st["cell"]),
                         Paragraph(_esc(plain.recommended_action(w)), st["cell"])])
        t = Table(rows, colWidths=[width * x for x in (0.10, 0.12, 0.12, 0.36, 0.30)], repeatRows=1)
        t.setStyle(TableStyle(sty))
        story.append(t)
        b = next((w.get("sag_bands") for w in sm["flagged"] if w.get("sag_bands")), None)
        bands = (f" Minor sag from {b['minor_from'] * 100:.1f}% and Critical from {b['critical_from'] * 100:.1f}% of sag "
                 "(configured thresholds)." if b else "")
        story.append(Paragraph("Sag: largest drop below the straight line between the wire's ends, as a percentage of that "
                               "line, shown against the configured reference (e.g. 4.9 / 4.0)." + bands + " Wires not "
                               "assessed are not listed.", st["small"]))
    elif measured:
        # S4: the measurements, never a severity (no validated reference)
        story[-1] = Paragraph("4.0 Measured Spans (not assessed)", st["h"])
        rows = [[Paragraph(h, st["th"]) for h in ("Span ID", "Type", "Image-derived sag ratio", "Status")]]
        for w in sorted(measured, key=lambda x: x["index"]):
            r = w.get("sag_ratio_resolution")
            rows.append([Paragraph(f"<b>{plain.span_id(w['index'])}</b>", st["cell"]),
                         Paragraph(_esc(plain.wire_type(w)), st["cell"]),
                         Paragraph(f"{w['sag_ratio'] * 100:.1f}%" + (f" &#177; {r * 100:.1f}" if r else ""), st["cell"]),
                         Paragraph(_esc(w.get("reason_short") or "Not assessed"), st["cell"])])
        t = Table(rows, colWidths=[width * x for x in (0.12, 0.20, 0.26, 0.42)], repeatRows=1)
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.8, F_INK), ("LINEBELOW", (0, 1), (-1, -1), 0.4, F_RULE),
                               ("BACKGROUND", (0, 0), (-1, 0), F_HEAD), ("TOPPADDING", (0, 0), (-1, -1), 2),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        story.append(t)
        story.append(Paragraph(
            "Image-derived sag ratio: the largest perpendicular distance from the fitted curve to the straight line "
            "between the traced ends, divided by that line's length, both in image pixels. It is not physical sag "
            "(no camera-pose correction, no field calibration) and is not compared with any limit. The +/- value is "
            "the 1 pixel resolution over the chord.", st["small"]))
    else:
        story.append(Paragraph("No span was flagged in the assessed sections of this photograph.", st["body"]))

    # 5.0 scope
    story.append(Paragraph("5.0 Screening Scope", st["h"]))
    for i, b in enumerate((
            "Automated computer-vision screening of a single photograph, intended as a decision-support tool. No "
            "validated sag reference or calibrated clearance is configured: sag ratios and vegetation gaps are image "
            "measurements, not physical sag, clearance or compliance.",
            "Results may contain false positives, false negatives or incomplete assessments; no hazard flagged does "
            "not mean no hazard exists.",
            "All findings must be verified by qualified personnel before any maintenance, repair or safety decision."), 1):
        story.append(Paragraph(f"{i}. &#160;{_esc(b)}", st["body"]))
    # owner's decision-support notice (app/core/notice.py), as on the page and the technical reports
    nt = Table([[Paragraph(f"<b>{notice.TITLE}</b><br/>{_esc(DISCLAIMER)}", st["body"])]], colWidths=[width])
    nt.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, F_RULE), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story += [Spacer(1, 4), nt]
    if sm["unassessed_reasons"]:
        story.append(Spacer(1, 3))
        story.append(Paragraph("Wires not assessed: " + "; ".join(f"{v} {k}" for k, v in sorted(
            sm["unassessed_reasons"].items(), key=lambda x: -x[1])) + ".", st["small"]))

    # 6.0 review + sign-off
    revs = result.get("reviews") or []
    main_story = story
    block: list[Any] = [Paragraph("6.0 Review Record and Field Verification", st["h"])]
    story_append, story = story.append, block  # section 6 stays on one page with its heading
    if revs:
        for r in revs:
            what = {"confirmed": "Confirmed the computed result", "dismissed": "Dismissed the computed result",
                    "flagged": "Flagged for further inspection",
                    "overridden": f"Changed the risk level to <b>{(r.get('override_level') or '').upper()}</b>"}.get(r["decision"], r["decision"])
            story.append(Paragraph(f"{what} · {_esc(r['by'])} · {r['at'][:16].replace('T', ' ')} UTC"
                                   + (f"<br/><font color='#475569'>{_esc(r['note'])}</font>" if r.get("note") else ""), st["body"]))
    else:
        story.append(Paragraph("Not yet reviewed in WIREFRAME.", st["body"]))
    story.append(Spacer(1, 5))
    def opt(label):  # an empty, printable check box beside its label
        bx = Table([[""]], colWidths=[3.4 * mm], rowHeights=[3.4 * mm])
        bx.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, F_INK)]))
        return [bx, Paragraph(label, st["cell"])]
    opts = Table([[Paragraph("<b>Field verification status</b>", st["cell"]), *opt("Cleared"), *opt("Scheduled for repair"),
                   *opt("Escalated to utility")]],
                 colWidths=[40 * mm, 5 * mm, 20 * mm, 5 * mm, 36 * mm, 5 * mm, 36 * mm])
    opts.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                              ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    status = opts

    def field(label):
        return [Paragraph(label, st["th"]), ""]
    sign = Table([[status, "", "", ""],
                  field("Technician name") + field("License / ID"),
                  field("Date") + field("Signature"),
                  [Paragraph("Remarks", st["th"]), "", "", ""]],
                 colWidths=[width * x for x in (0.17, 0.33, 0.15, 0.35)], rowHeights=[9 * mm, 11 * mm, 11 * mm, 16 * mm])
    sign.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.9, F_INK), ("INNERGRID", (0, 1), (-1, -1), 0.5, F_RULE),
                              ("LINEBELOW", (0, 0), (-1, 0), 0.5, F_RULE), ("SPAN", (0, 0), (-1, 0)), ("SPAN", (1, 3), (-1, 3)),
                              ("BACKGROUND", (0, 1), (0, -1), F_HEAD), ("BACKGROUND", (2, 1), (2, 2), F_HEAD),
                              ("VALIGN", (0, 0), (-1, 0), "MIDDLE"), ("VALIGN", (0, 1), (-1, -1), "TOP"),
                              ("TOPPADDING", (0, 0), (-1, -1), 4), ("LEFTPADDING", (0, 0), (-1, -1), 6)]))
    story.append(sign)
    story = main_story
    story_append(KeepTogether(block))
    doc.build(story, canvasmaker=_numbered_canvas(report_id))
    return buf.getvalue()
