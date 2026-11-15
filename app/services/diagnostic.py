"""Multi-panel diagnostic figure (v2.8, v2.9): original | segmentation | spans, clearance and verdict.

v2.9: panel 1 carries the reliability grade badge with its plain-English reason; panel 2 a
class legend; panel 3 tells primary spans (filled by CRS level, "S<n>") from steep service
drops (cyan outline, "D<n>", not scored for sag) and has a level legend; a strip under the
panels prints the verdict reasons, so the figure reads on its own in the PDF and the batch tool.

Panel 2 colours pixels by class (power blue, telecom violet, abandoned orange, vegetation green).
Panel 3 colours each span by its CRS level (green low, amber medium, red high, grey not scored),
draws vegetation clearance lines with their pixel gap (and the approximate metre range), marks
loose-end candidates, and shows the verdict as a badge. Pure OpenCV, deterministic.
"""

from __future__ import annotations

import cv2
import numpy as np

CLASS_BGR = {"power-distribution-lines": (255, 150, 60), "telecom-cable": (220, 110, 190),
             "abandoned-wire": (40, 140, 255), "Vegetation": (70, 190, 80)}
LEVEL_BGR = {"low": (60, 180, 60), "medium": (20, 170, 230), "high": (50, 50, 230), None: (150, 150, 150)}
VERDICT_BGR = {"AT RISK - HIGH": (40, 40, 210), "AT RISK - MODERATE": (20, 150, 230), "NOT AT RISK": (60, 160, 60),
               "INCONCLUSIVE - RETAKE PHOTO": (110, 110, 110)}


def _fill(canvas: np.ndarray, polygons, color, alpha: float) -> None:
    if not polygons:
        return
    m = np.zeros(canvas.shape[:2], np.uint8)
    for poly in polygons:
        if len(poly) >= 3:
            cv2.fillPoly(m, [np.asarray(poly, np.int32)], 1)
    sel = m.astype(bool)
    canvas[sel] = (canvas[sel] * (1 - alpha) + np.array(color) * alpha).astype(np.uint8)


def _label(img, text, org, scale, color=(255, 255, 255), bg=(0, 0, 0)) -> None:
    th = max(1, int(round(scale * 2)))
    (w, h), b = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, th)
    x, y = org
    cv2.rectangle(img, (x - 3, y - h - 4), (x + w + 3, y + b + 2), bg, -1)
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, th, cv2.LINE_AA)


DROP_BGR = (230, 220, 40)
GRADE_BGR = {"A": (60, 160, 60), "B": (40, 150, 200), "C": (20, 120, 230), "Retake recommended": (50, 50, 200)}


def _legend(img, items, fs, top=None) -> None:
    """items: [(colour, text)], stacked bottom-right above the panel title."""
    th = max(1, int(round(fs * 1.6)))
    lh = int(26 * fs) + 6
    y = (top if top is not None else img.shape[0] - 18 - lh * (len(items) + 1))
    wmax = max(cv2.getTextSize(t, cv2.FONT_HERSHEY_SIMPLEX, fs * 0.75, th)[0][0] for _, t in items)
    x = img.shape[1] - wmax - int(36 * fs) - 10
    cv2.rectangle(img, (x - 6, y - 4), (img.shape[1] - 4, y + lh * len(items) + 2), (0, 0, 0), -1)
    for i, (c, t) in enumerate(items):
        yy = y + i * lh
        cv2.rectangle(img, (x, yy + 4), (x + int(22 * fs), yy + lh - 6), c, -1)
        cv2.putText(img, t, (x + int(30 * fs), yy + lh - 8), cv2.FONT_HERSHEY_SIMPLEX, fs * 0.75, (255, 255, 255), th,
                    cv2.LINE_AA)


def _wrap(text: str, width_px: int, scale: float) -> list[str]:
    out, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and cv2.getTextSize(trial, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)[0][0] > width_px:
            out.append(line)
            line = word
        else:
            line = trial
    return out + ([line] if line else [])


def _centroid(polys, s):
    pts = np.concatenate([np.asarray(p, np.float64).reshape(-1, 2) for p in polys if len(p)]) if polys else None
    if pts is None or not len(pts):
        return None
    return int(np.median(pts[:, 0]) * s), int(np.median(pts[:, 1]) * s)


def render_diagnostic(rgb: np.ndarray, result: dict, panel_long: int = 900) -> np.ndarray:
    """result: the serialized analysis (app.services.analysis.serialize_result). Returns BGR."""
    h, w = rgb.shape[:2]
    s = panel_long / max(h, w)
    size = (max(1, round(w * s)), max(1, round(h * s)))
    base = cv2.resize(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), size, interpolation=cv2.INTER_AREA)
    sc = lambda pts: [[int(round(x * s)), int(round(y * s))] for x, y in pts]  # noqa: E731
    fs = max(0.45, panel_long / 1600)
    seg = (base * 0.55).astype(np.uint8)
    for v in result.get("vegetation") or []:
        _fill(seg, [sc(p) for p in v["mask_polygons"]], CLASS_BGR["Vegetation"], 0.55)
    for wr in result.get("wires") or []:
        _fill(seg, [sc(p) for p in wr["mask_polygons"]], CLASS_BGR.get(wr["class"], (200, 200, 200)), 0.9)
    risk = (base * 0.55).astype(np.uint8)
    for v in result.get("vegetation") or []:
        _fill(risk, [sc(p) for p in v["mask_polygons"]], CLASS_BGR["Vegetation"], 0.35)
    drops = 0
    for wr in result.get("wires") or []:
        polys = [sc(p) for p in wr["mask_polygons"]]
        if wr.get("status") == "service_drop":
            drops += 1
            _fill(risk, polys, DROP_BGR, 0.6)
            for p in polys:
                if len(p) >= 3:
                    cv2.polylines(risk, [np.asarray(p, np.int32)], True, DROP_BGR, 1, cv2.LINE_AA)
        else:
            _fill(risk, polys, LEVEL_BGR.get(wr["level"], LEVEL_BGR[None]), 0.95)
        if wr.get("catenary_curve"):
            cv2.polylines(risk, [np.asarray(sc(wr["catenary_curve"]), np.int32)], False, (255, 255, 255), 1, cv2.LINE_AA)
        if wr.get("level") in ("medium", "high") or wr.get("status") == "service_drop":
            c = _centroid(wr["mask_polygons"], s)
            if c:
                tag = f"D{wr['index']}" if wr.get("status") == "service_drop" else f"S{wr['index']}"
                _label(risk, tag, (min(c[0], size[0] - 40), max(c[1], 14)), fs * 0.6)
    hz = result.get("hazard") or {}
    fac = hz.get("factors") or {}
    for row in (fac.get("vegetation") or [])[:5]:
        if "wire_point" not in row or row["gap_px"] > 60:
            continue
        (wy, wx), (vy, vx) = row["wire_point"], row["vegetation_point"]
        a, b = (int(wx * s), int(wy * s)), (int(vx * s), int(vy * s))
        cv2.line(risk, a, b, (255, 255, 0), 2, cv2.LINE_AA)
        txt = f"{row['gap_px']:.0f}px"
        if row.get("gap_m_approx"):
            lo, hi = row["gap_m_approx"]
            txt += f" ~{(lo + hi) / 2:.2f}+/-{(hi - lo) / 2:.2f}m"
        _label(risk, txt, (min(a[0], size[0] - 160), max(a[1] - 6, 14)), fs * 0.8)
    for e in fac.get("loose_ends") or []:
        c = (int(e["x"] * s), int(e["y"] * s))
        cv2.circle(risk, c, max(6, int(10 * fs)), (0, 255, 255), 2, cv2.LINE_AA)
    verdict = hz.get("verdict")
    if verdict:
        _label(risk, verdict, (10, int(30 * fs) + 8), fs * 1.2, (255, 255, 255), VERDICT_BGR.get(verdict, (80, 80, 80)))
    q = result.get("quality") or {}
    if q.get("grade"):
        g = q["grade"]
        badge = f"Grade {g}" if len(g) == 1 else ("INCONCLUSIVE - RETAKE PHOTO" if verdict == "INCONCLUSIVE - RETAKE PHOTO"
                                                 else "RETAKE RECOMMENDED")
        if q.get("reliability") is not None:
            badge += f"  (R = {q['reliability']:.2f})"
        _label(base, badge, (10, int(30 * fs) + 8), fs * 1.1, (255, 255, 255), GRADE_BGR.get(g, (80, 80, 80)))
        for i, line in enumerate(_wrap(q.get("reason") or q.get("advice") or "", size[0] - 30, fs * 0.75)[:3]):
            _label(base, line, (10, int(62 * fs) + 16 + i * int(26 * fs + 8)), fs * 0.75)
    _legend(seg, [(CLASS_BGR["power-distribution-lines"], "power line"), (CLASS_BGR["telecom-cable"], "telecom cable"),
                  (CLASS_BGR["abandoned-wire"], "abandoned wire"), (CLASS_BGR["Vegetation"], "vegetation")], fs)
    _legend(risk, [(LEVEL_BGR["low"], "span: low CRS"), (LEVEL_BGR["medium"], "span: medium"),
                   (LEVEL_BGR["high"], "span: high"), (LEVEL_BGR[None], "span: not scored")]
            + ([(DROP_BGR, f"service drop ({drops})")] if drops else []), fs)
    for img, title in ((base, "Original + photo reliability"), (seg, "Segmentation"),
                       (risk, "Spans vs drops, clearance, verdict" if drops else "Spans, clearance, verdict")):
        _label(img, title, (10, size[1] - 10), fs)
    gap = np.full((size[1], 6, 3), 255, np.uint8)
    panels = np.hstack([base, gap, seg, gap, risk])
    reasons = (hz.get("reasons") or [])[:5]
    if not reasons:
        return panels
    rs = fs * 0.8
    lh = int(26 * rs / 0.8 * 0.8) + 10
    lines = [ln for r in reasons for ln in _wrap("- " + r, panels.shape[1] - 30, rs)][:8]
    strip = np.full((lh * len(lines) + 16, panels.shape[1], 3), 250, np.uint8)
    for i, ln in enumerate(lines):
        cv2.putText(strip, ln, (14, 8 + lh * (i + 1) - 6), cv2.FONT_HERSHEY_SIMPLEX, rs, (30, 30, 30),
                    max(1, int(round(rs * 1.5))), cv2.LINE_AA)
    return np.vstack([panels, strip])


def outcome_to_result(o) -> dict:
    """AnalysisOutcome -> the subset of the API result format the renderer and batch tool use."""
    return {"wires": [{"index": w.index, "class": w.wire_class, "mask_polygons": w.polygons, "catenary_curve": w.curve,
                       "level": w.level, "crs": w.crs, "status": w.status, "flags": w.flags} for w in o.wires],
            "vegetation": [{"mask_polygons": v.polygons, "touches": v.touches} for v in o.vegetation],
            "hazard": o.hazard, "quality": o.quality, "status": o.status, "flags": o.flags,
            "vni": {"value": o.vni, "band": o.vni_band}}
