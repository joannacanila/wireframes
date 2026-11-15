"""Server-side overlay rendering (for the printable report).

Draws the same layers as the web canvas: mask polygons filled by risk level, the
fitted catenary, the chord, and the perpendicular sag segment, with wire numbers.
"""

from __future__ import annotations

import cv2
import numpy as np

# RGB; same tokens as the web UI (app/web/styles.css)
LEVEL_RGB = {"low": (46, 189, 115), "medium": (240, 192, 48), "high": (229, 72, 77), None: (138, 143, 152)}


def _pts(points, scale: float) -> np.ndarray:
    return np.round(np.asarray(points, dtype=np.float64) * scale * 16).astype(np.int32).reshape(-1, 1, 2)


def render_overlay(rgb: np.ndarray, wires: list[dict], max_side: int = 1600) -> np.ndarray:
    h, w = rgb.shape[:2]
    s = min(1.0, max_side / max(h, w))
    base = cv2.resize(rgb, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else rgb.copy()
    fill = base.copy()
    lw = max(2, round(max(base.shape[:2]) / 500))
    for wire in wires:
        col = LEVEL_RGB.get(wire.get("level"), LEVEL_RGB[None])
        polys = [_pts(p, s) for p in wire.get("mask_polygons") or [] if len(p) >= 3]
        if polys:
            cv2.fillPoly(fill, polys, col, cv2.LINE_AA, shift=4)
    out = cv2.addWeighted(fill, 0.45, base, 0.55, 0)

    for wire in wires:
        col = LEVEL_RGB.get(wire.get("level"), LEVEL_RGB[None])
        curve = wire.get("catenary_curve") or []
        if len(curve) > 1:
            cv2.polylines(out, [_pts(curve, s)], False, (255, 255, 255), lw + 2, cv2.LINE_AA, shift=4)
            cv2.polylines(out, [_pts(curve, s)], False, col, lw, cv2.LINE_AA, shift=4)
        fit = wire.get("fit") or {}
        ends, sp, foot = fit.get("endpoints"), fit.get("sag_point"), fit.get("chord_foot")
        if ends and wire.get("status") == "scored":
            p0, p1 = (np.asarray(e) * s for e in ends)
            n = max(2, int(np.hypot(*(p1 - p0)) / (lw * 5)))
            for i in range(0, n, 2):  # dashed chord
                a = p0 + (p1 - p0) * i / n
                b = p0 + (p1 - p0) * min(i + 1, n) / n
                cv2.line(out, tuple(np.round(a).astype(int)), tuple(np.round(b).astype(int)), (255, 255, 255), max(1, lw - 1), cv2.LINE_AA)
            if sp and foot:
                a, b = np.round(np.asarray(sp) * s).astype(int), np.round(np.asarray(foot) * s).astype(int)
                cv2.line(out, tuple(a), tuple(b), (255, 255, 255), max(1, lw - 1), cv2.LINE_AA)
                cv2.circle(out, tuple(a), lw + 2, col, -1, cv2.LINE_AA)
        anchor = (curve[len(curve) // 2] if curve else
                  ((wire["bbox"][0] + wire["bbox"][2]) / 2, (wire["bbox"][1] + wire["bbox"][3]) / 2))
        cx, cy = int(anchor[0] * s), int(anchor[1] * s)
        r = lw * 6
        cv2.circle(out, (cx, cy - r - lw * 2), r, (20, 22, 26), -1, cv2.LINE_AA)
        cv2.circle(out, (cx, cy - r - lw * 2), r, col, max(1, lw // 2 + 1), cv2.LINE_AA)
        label = str(wire["index"])
        fs = r / 14
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_DUPLEX, fs, 1)
        cv2.putText(out, label, (cx - tw // 2, cy - r - lw * 2 + th // 2), cv2.FONT_HERSHEY_DUPLEX, fs,
                    (255, 255, 255), max(1, lw // 2), cv2.LINE_AA)
    return out
