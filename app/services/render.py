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
    badges: list[tuple[int, int, str, tuple]] = []

    for wire in wires:
        col = LEVEL_RGB.get(wire.get("level"), LEVEL_RGB[None])
        curve = wire.get("catenary_curve") or []
        if len(curve) > 1:
            cv2.polylines(out, [_pts(curve, s)], False, (255, 255, 255), lw + 2, cv2.LINE_AA, shift=4)
            cv2.polylines(out, [_pts(curve, s)], False, col, lw, cv2.LINE_AA, shift=4)
        fit = wire.get("fit") or {}
        ends, sp, foot = fit.get("endpoints"), fit.get("sag_point"), fit.get("chord_foot")
        # S5: chord and sag point for every measured wire (scored, or measured and not assessed)
        if ends and wire.get("sag_ratio") is not None and wire.get("status") in ("scored", "not_assessed"):
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
        badges.append((int(anchor[0] * s), int(anchor[1] * s), str(wire["index"]), col))
    return _draw_badges(out, badges, lw)


def _draw_badges(out: np.ndarray, badges: list[tuple[int, int, str, tuple]], lw: int) -> np.ndarray:
    """v2.21: wire numbers centred in their circles. Pillow draws the text with ``anchor="mm"`` (the middle of the
    glyph box, horizontally and vertically) in tabular digits; every circle is sized for the longest number on the
    photo, so 2- and 3-digit numbers sit inside the same uniform circle without touching the border."""
    if not badges:
        return out
    from PIL import Image, ImageDraw, ImageFont
    longest = max(len(b[2]) for b in badges)
    fsize = max(10, lw * 6)
    font = ImageFont.load_default(size=fsize)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    x0, t, r_, b_ = probe.textbbox((0, 0), "8" * longest, font=font, anchor="mm")
    radius = int(np.ceil(max(r_ - x0, b_ - t) / 2 + max(5, lw * 2.6)))
    border = max(1, lw // 2 + 1)
    img = Image.fromarray(out)
    d = ImageDraw.Draw(img)
    for cx, cy, text, col in badges:
        cy = cy - radius - lw * 2  # above the wire, as before
        d.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(20, 22, 26), outline=tuple(int(c) for c in col),
                  width=border)
        d.text((cx, cy), text, fill=(255, 255, 255), font=font, anchor="mm")
    return np.asarray(img).copy()
