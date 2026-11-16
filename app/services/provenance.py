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


def _raster(polys: list, shape: tuple[int, int]) -> np.ndarray:
    m = np.zeros(shape, np.uint8)
    if polys:
        cv2.fillPoly(m, [np.asarray(p, np.int32) for p in polys if len(p) >= 3], 1)
    return m.astype(bool)


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
    span = _raster(polygons, label.shape)
    # v2.16: a strand is built on the union of the wire classes, so any wire-class pixel is an observed detection
    # (a power strand may contain pixels WireNet labelled telecom); only bridge pixels with no wire label are inferred
    wire_ids = [i + 1 for i, c in enumerate(CLASSES) if c != VEGETATION]
    inferred = span & ~np.isin(label, wire_ids)
    n_inf = int(inferred.sum())
    out = {"observed_px": int(span.sum()) - n_inf, "inferred_px": n_inf, "inferred_segments": [],
           "inferred_polygons": [], "centerline_inferred_share": 0.0}
    if not n_inf:
        return out
    n, lab = cv2.connectedComponents(inferred.astype(np.uint8), connectivity=8)
    for k in range(1, n):
        ys, xs = np.nonzero(lab == k)
        seg = _segment(ys, xs)
        if np.hypot(seg[1][0] - seg[0][0], seg[1][1] - seg[0][1]) >= 3:  # a bridge, not polygon edge slack
            out["inferred_segments"].append(seg)
    contours, _ = cv2.findContours(inferred.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    out["inferred_polygons"] = [c.reshape(-1, 2).tolist() for c in contours if len(c) >= 3]
    if centerline:
        cl = np.rint(np.asarray(centerline, np.float64)).astype(int)
        cl[:, 0] = cl[:, 0].clip(0, label.shape[1] - 1)
        cl[:, 1] = cl[:, 1].clip(0, label.shape[0] - 1)
        out["centerline_inferred_share"] = round(float(inferred[cl[:, 1], cl[:, 0]].mean()), 4)
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
