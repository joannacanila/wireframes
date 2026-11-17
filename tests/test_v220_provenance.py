"""v2.20: span provenance is computed inside each span's box (it used to scan the whole mask once per gap component,
which made one 135-wire inspection take ~45 s to open). Same result as the straightforward full-image version."""

import time

import cv2
import numpy as np

from app.core.config import CLASSES, VEGETATION
from app.services.provenance import span_provenance


def _reference(label, polygons, centerline):
    span = np.zeros(label.shape, np.uint8)
    cv2.fillPoly(span, [np.asarray(p, np.int32) for p in polygons if len(p) >= 3], 1)
    span = span.astype(bool)
    wire_ids = [i + 1 for i, c in enumerate(CLASSES) if c != VEGETATION]
    inferred = span & ~np.isin(label, wire_ids)
    n, lab = cv2.connectedComponents(inferred.astype(np.uint8), connectivity=8)
    segs = []
    for k in range(1, n):
        ys, xs = np.nonzero(lab == k)
        pts = np.stack([xs, ys], 1).astype(np.float64)
        if len(pts) == 1:
            seg = [pts[0].tolist(), pts[0].tolist()]
        else:
            c = pts.mean(0)
            t = (pts - c) @ np.linalg.svd(pts - c, full_matrices=False)[2][0]
            seg = [[round(v, 1) for v in pts[t.argmin()]], [round(v, 1) for v in pts[t.argmax()]]]
        if np.hypot(seg[1][0] - seg[0][0], seg[1][1] - seg[0][1]) >= 3:
            segs.append(seg)
    cl = np.rint(np.asarray(centerline, np.float64)).astype(int)
    share = round(float(inferred[cl[:, 1].clip(0, label.shape[0] - 1), cl[:, 0].clip(0, label.shape[1] - 1)].mean()), 4)
    return int(span.sum()) - int(inferred.sum()), int(inferred.sum()), sorted(map(str, segs)), share


def _scene(h=900, w=1600, gaps=40, seed=0):
    rng = np.random.default_rng(seed)
    label = np.zeros((h, w), np.uint8)
    y = 300
    poly = [[50, y - 8], [w - 50, y + 52], [w - 50, y + 68], [50, y + 8]]
    wire_id = next(i + 1 for i, c in enumerate(CLASSES) if c != VEGETATION)
    cv2.fillPoly(label, [np.asarray(poly, np.int32)], wire_id)  # a wire WireNet labelled
    xs = np.sort(rng.choice(np.arange(80, w - 120), gaps, replace=False))
    for x in xs:  # holes in the label: bridge pixels the span assembler would draw
        label[:, x:x + 12] = 0
    centre = [[x, y + 60 * (x - 50) / (w - 100)] for x in range(50, w - 50, 7)]
    return label, [poly], centre


def test_same_result_as_the_full_image_reference():
    label, polys, centre = _scene()
    got = span_provenance(label, "power-distribution-lines", polys, centre)
    obs, inf, segs, share = _reference(label, polys, centre)
    assert (got["observed_px"], got["inferred_px"]) == (obs, inf) and inf > 0
    assert sorted(map(str, got["inferred_segments"])) == segs and len(segs) >= 20
    assert got["centerline_inferred_share"] == share
    # deterministic order: running it twice gives the same list
    assert got["inferred_segments"] == span_provenance(label, "power-distribution-lines", polys, centre)["inferred_segments"]


def test_polygon_partly_outside_the_image_and_empty_inputs():
    label = np.zeros((100, 100), np.uint8)
    got = span_provenance(label, "power-distribution-lines", [[[-20, 40], [140, 40], [140, 60], [-20, 60]]], [[50, 50]])
    obs, inf, segs, share = _reference(label, [[[-20, 40], [140, 40], [140, 60], [-20, 60]]], [[50, 50]])
    assert (got["observed_px"], got["inferred_px"], got["centerline_inferred_share"]) == (obs, inf, share)
    assert span_provenance(label, "power-distribution-lines", [], [])["inferred_px"] == 0
    assert span_provenance(label, "power-distribution-lines", [[[200, 200], [300, 200], [300, 300]]], [])["observed_px"] == 0


def test_many_gaps_on_a_large_mask_stay_fast():
    label, polys, centre = _scene(h=3000, w=4000, gaps=150, seed=1)
    t = time.perf_counter()
    for _ in range(5):
        span_provenance(label, "power-distribution-lines", polys, centre)
    assert (time.perf_counter() - t) / 5 < 0.5  # was several seconds per span at 12 MP
