"""Topological loose-end detector for abandoned / dangling wire (v2.8).

An abandoned wire is distinguished from a live one by context (a free end, slack), not by its
appearance, and 14 labelled training instances were too few to learn that from pixels (Chapter 4
v2.7, section 4.3.6). This detector uses geometry on the stitched spans instead; it needs no
abandoned-wire labels to run (the few available are used only to choose its constants and to
measure it).

A span endpoint is a loose-end candidate when:
1. it is at least ``border_px`` from the image border and from letterbox padding (``content``),
   so a wire merely leaving the frame is not counted;
2. no wire pixel of another span lies within ``isolation_px`` (it is not attached to, or crossing
   into, another wire);
3. fewer than ``cluster_n`` other span endpoints lie within ``cluster_px`` (endpoint clusters mark
   an attachment point: a pole, crossarm or splice; the model has no pole class, so this is the
   proxy).
Score = 0.5 * downward + 0.5 * isolation, where downward = max(0, outward tangent's downward
component) and isolation = min(1, distance to the nearest other wire / ``isolation_norm_px``).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage as ndi

from app.pipeline.wire_instances import span_ends


@dataclass(frozen=True)
class LooseEnd:
    span: int  # index into the spans list
    y: float
    x: float
    score: float
    downward: float
    isolation_px: float


def loose_ends(spans: list[np.ndarray], content: np.ndarray | None = None, border_px: int = 12,
               isolation_px: int = 8, cluster_px: int = 25, cluster_n: int = 2,
               isolation_norm_px: float = 40.0) -> list[LooseEnd]:
    if not spans:
        return []
    h, w = spans[0].shape
    ends = [span_ends(m) for m in spans]
    pts = [(i, e, t) for i, (a, ta, b, tb, _) in enumerate(ends) for e, t in ((a, ta), (b, tb))]
    union = np.zeros((h, w), np.int32)
    for i, m in enumerate(spans):
        union[m & (union == 0)] = i + 1
    inside = np.ones((h, w), bool) if content is None else ndi.binary_erosion(content, iterations=border_px)
    out = []
    for i, e, t in pts:
        y, x = float(e[0]), float(e[1])
        yi, xi = int(round(y)), int(round(x))
        if not (border_px <= yi < h - border_px and border_px <= xi < w - border_px) or not inside[yi, xi]:
            continue
        others = (union > 0) & (union != i + 1)
        r = int(isolation_norm_px)
        win = others[max(0, yi - r): yi + r + 1, max(0, xi - r): xi + r + 1]
        if win.any():
            oy, ox = np.nonzero(win)
            d = float(np.min(np.hypot(oy + max(0, yi - r) - y, ox + max(0, xi - r) - x)))
        else:
            d = float(isolation_norm_px)
        if d < isolation_px:
            continue
        near = sum(1 for j, e2, _ in pts if j != i and np.hypot(*(e2 - e)) <= cluster_px)
        if near >= cluster_n:
            continue
        down = max(0.0, float(t[0]))  # image rows grow downward
        iso = min(1.0, d / isolation_norm_px)
        out.append(LooseEnd(i, y, x, 0.5 * down + 0.5 * iso, down, d))
    return out
