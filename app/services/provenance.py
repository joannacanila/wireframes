"""Observed vs inferred span geometry (v2.14).

The span assembler bridges collinear gaps of up to 70 px (config ``segmentation`` / ``ml.experiments.ablation_final``
FINAL) by drawing a straight 2-px line into the span mask, so the skeleton and the catenary fit run through the
gap. Those pixels were never labelled wire by WireNet. They stay in the measurement (the shipped CRS is unchanged),
but the UI and report must not present them as detections.

For each span, inferred pixels = span mask AND NOT WireNet's class mask for the span's class. The class mask is the
stored ``<id>.wiremask.png`` (indexed, 0 = background, i = CLASSES[i - 1]), so this works for every analysis that has
one, including ones run before v2.14. Analyses without a mask (reference-polygon / validation mode) get ``None``:
their geometry comes from human polygons, not from a model, so there is nothing to separate.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from app.core.config import CLASSES, VEGETATION


def _segment(ys: np.ndarray, xs: np.ndarray) -> list[list[float]]:
    """End points of a bridge component along its principal axis (bridges are straight lines)."""
    pts = np.stack([xs, ys], 1).astype(np.float64)
    if len(pts) == 1:
        return [pts[0].tolist(), pts[0].tolist()]
    c = pts.mean(0)
    _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
    t = (pts - c) @ vt[0]
    return [[round(v, 1) for v in pts[t.argmin()]], [round(v, 1) for v in pts[t.argmax()]]]


def span_provenance(label: np.ndarray, wire_class: str, polygons: list, centerline: list) -> dict:
    """v2.20: computed inside the span's bounding box, and bridge components grouped in one pass. Before, every
    component was found with a full-image scan (``lab == k`` over 12 MP), so a photo with many wires and gaps took
    about 45 s to serialize; the output is the same (tests/test_v220_provenance.py; segments now come in row-major order)."""
    out = {"observed_px": 0, "inferred_px": 0, "inferred_segments": [], "inferred_polygons": [],
           "centerline_inferred_share": 0.0}
    h, w = label.shape
    polys = [np.asarray(p, np.int32) for p in polygons if len(p) >= 3]
    if not polys:
        return out
    allp = np.concatenate(polys)
    x0, y0 = max(int(allp[:, 0].min()), 0), max(int(allp[:, 1].min()), 0)
    x1, y1 = min(int(allp[:, 0].max()) + 1, w), min(int(allp[:, 1].max()) + 1, h)
    if x1 <= x0 or y1 <= y0:
        return out
    sub = label[y0:y1, x0:x1]
    span = np.zeros(sub.shape, np.uint8)
    cv2.fillPoly(span, [p - np.array([x0, y0], np.int32) for p in polys], 1)
    span = span.astype(bool)
    # v2.16: a strand is built on the union of the wire classes, so any wire-class pixel is an observed detection
    # (a power strand may contain pixels WireNet labelled telecom); only bridge pixels with no wire label are inferred
    wire_ids = [i + 1 for i, c in enumerate(CLASSES) if c != VEGETATION]
    inferred = span & ~np.isin(sub, wire_ids)
    n_inf = int(inferred.sum())
    out["observed_px"], out["inferred_px"] = int(span.sum()) - n_inf, n_inf
    if not n_inf:
        return out
    n, lab = cv2.connectedComponents(inferred.astype(np.uint8), connectivity=8)
    ys, xs = np.nonzero(lab)  # row-major, as np.nonzero(lab == k) returns each component's pixels
    ks = lab[ys, xs]
    order = np.argsort(ks, kind="stable")
    ys, xs, ks = ys[order] + y0, xs[order] + x0, ks[order]
    bounds = np.searchsorted(ks, np.arange(1, n + 1))
    # components in row-major order of their first pixel (cv2's own label order is an implementation detail)
    first = [(int(ys[bounds[k - 1]]), int(xs[bounds[k - 1]]), k) for k in range(1, n) if bounds[k] > bounds[k - 1]]
    for _, _, k in sorted(first):
        a, b = bounds[k - 1], bounds[k]
        seg = _segment(ys[a:b], xs[a:b])
        if np.hypot(seg[1][0] - seg[0][0], seg[1][1] - seg[0][1]) >= 3:  # a bridge, not polygon edge slack
            out["inferred_segments"].append(seg)
    contours, _ = cv2.findContours(inferred.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE, offset=(x0, y0))
    out["inferred_polygons"] = [c.reshape(-1, 2).tolist() for c in contours if len(c) >= 3]
    if centerline:
        cl = np.rint(np.asarray(centerline, np.float64)).astype(int)
        cx, cy = cl[:, 0].clip(0, w - 1), cl[:, 1].clip(0, h - 1)
        inside = (cx >= x0) & (cx < x1) & (cy >= y0) & (cy < y1)
        hit = np.zeros(len(cl), bool)
        hit[inside] = inferred[cy[inside] - y0, cx[inside] - x0]
        out["centerline_inferred_share"] = round(float(hit.mean()), 4)
    return out


@lru_cache(maxsize=32)
def _label(path: str, mtime: float) -> np.ndarray:
    return np.array(Image.open(path))


def attach(result: dict, mask_path: Path) -> None:
    """Add ``provenance`` to every wire of a serialized result (``None`` when there is no WireNet mask)."""
    wires = result.get("wires") or []
    if not mask_path.exists() or not result.get("wire_mask"):
        for w in wires:
            w["provenance"] = None
        return
    label = _label(str(mask_path), mask_path.stat().st_mtime)
    for w in wires:
        w["provenance"] = span_provenance(label, w["class"], w.get("mask_polygons") or [], w.get("centerline") or [])
