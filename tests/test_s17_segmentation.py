"""S17 (Part 3): controlled segmentation-to-span cases with the SHIPPED span parameters.

These are synthetic masks with a known answer, not model accuracy. They pin the behaviour the field evaluation
(ml/experiments/s17_seg_eval.py, docs/thesis/results/s17_seg_*.json) depends on: one span per wire through a
crossing, each span staying on its own wire (no identity switch), close parallel wires kept apart, 1 px wires kept,
short occlusion gaps bridged and long ones not, and identical output for identical input. A case the extractor does
not yet handle is a strict xfail with its reason, never a silent omission.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.core.config import get_analysis_config
from app.pipeline.wire_instances import extract_spans
from app.pipeline.wirenet import span_params

P = span_params(get_analysis_config().segmentation)


def _draw(h, w, polys, t=2):
    m = np.zeros((h, w), np.uint8)
    for pts in polys:
        cv2.polylines(m, [np.array(pts, np.int32)], False, 1, t)
    return m.astype(bool)


def _arc(x0, x1, y, sag, n=80):
    xs = np.linspace(x0, x1, n)
    return [(int(x), int(round(y + sag * 4 * (x - x0) * (x1 - x) / (x1 - x0) ** 2))) for x in xs]


def _line_y(pts, x):
    xs, ys = zip(*pts, strict=True)
    return float(np.interp(x, xs, ys))


def _spans(m):
    return [s for s, _ in extract_spans(m, m.astype(float), **P)]


def _owner_errors(span, polys, tol=3.0):
    """Share of the span's pixels that lie nearer another drawn wire than the one it mostly follows."""
    ys, xs = np.nonzero(span)
    d = np.array([[abs(y - _line_y(p, x)) for p in polys] for y, x in zip(ys, xs, strict=True)])
    own = np.bincount(d.argmin(1)).argmax()
    near_other = (d.argmin(1) != own) & (np.abs(d[:, own] - d.min(1)) > tol)
    return near_other.mean()


def _x(angle_deg, cy=160):
    dy = int(np.tan(np.radians(angle_deg / 2)) * 290)
    return [[(10, cy - dy), (590, cy + dy)], [(10, cy + dy), (590, cy - dy)]]


SHALLOW = pytest.mark.xfail(strict=True, reason=(
    "S17 known limitation: below ~25 deg two crossing wires share a short skeleton segment between two Y "
    "junctions and the greedy join swaps them about half the time. The X-junction option does not resolve it "
    "(probed at 20-30 deg tolerance). Needs a two-junction pairing rule, evaluated on labelled crossings."))


@pytest.mark.parametrize("name,polys", [
    ("crossing_60deg", [[(10, 40), (590, 260)], [(10, 260), (590, 40)]]),
    ("crossing_45deg", _x(45)),
    ("crossing_30deg", _x(30)),
    pytest.param("crossing_20deg", _x(20), marks=SHALLOW),
    pytest.param("crossing_16deg", [[(10, 120), (590, 200)], [(10, 200), (590, 120)]], marks=SHALLOW),
    ("sagging_crossing", [_arc(10, 590, 60, 120), [(10, 150), (590, 140)]]),
])
def test_each_wire_through_a_crossing_is_one_span_on_its_own_wire(name, polys):
    m = _draw(300, 600, polys)
    spans = _spans(m)
    assert len(spans) == len(polys), name
    for s in spans:
        assert np.ptp(np.nonzero(s)[1]) > 550, name               # whole width: not cut at the junction
        assert _owner_errors(s, polys) < 0.02, name                # no identity switch at the crossing


@pytest.mark.parametrize("gap_px", [6, 10])
def test_close_parallel_wires_stay_apart(gap_px):
    polys = [[(10, 80), (590, 90)], [(10, 80 + gap_px), (590, 90 + gap_px)]]
    spans = _spans(_draw(200, 600, polys))
    assert len(spans) == 2 and all(_owner_errors(s, polys) < 0.02 for s in spans)


def test_one_pixel_wire_is_kept_whole():
    spans = _spans(_draw(200, 600, [_arc(10, 590, 50, 30)], t=1))
    assert len(spans) == 1 and np.ptp(np.nonzero(spans[0])[1]) > 560


def test_short_occlusion_is_bridged_and_long_gap_is_not():
    g = int(P["max_gap"])
    short = _draw(200, 600, [_arc(10, 590, 50, 30)]); short[:, 300 - (g - 20) // 2:300 + (g - 20) // 2] = False
    long_ = _draw(200, 600, [_arc(10, 590, 50, 30)]); long_[:, 300 - (g + 40) // 2:300 + (g + 40) // 2] = False
    assert len(_spans(short)) == 1                          # under span_max_gap: one wire behind an occluder
    assert len(_spans(long_)) == 2                          # well over it: two pieces, never invented across


def test_span_extraction_is_deterministic():
    m = _draw(300, 600, [_arc(10, 590, 60, 120), [(10, 150), (590, 140)], [(10, 40), (590, 260)]])
    a, b = _spans(m), _spans(m)
    assert len(a) == len(b) and all(np.array_equal(x, y) for x, y in zip(a, b, strict=True))


def test_shipped_span_parameters_are_the_evaluated_baseline():
    # S17 evaluation: X-junction matching and a 25 deg join limit were measured against the baseline on the labelled
    # val/test splits and did not improve fragmentation without regressions; neither is enabled (see S17 report s.7)
    assert P["xjunction_tol_deg"] == 0.0 and P["max_turn_deg"] == 35.0
