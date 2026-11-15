"""Span extraction from a full-resolution wire class map (v2.7)."""

import cv2
import numpy as np

from app.pipeline.wire_instances import spans_from_class_mask


def _draw(h, w, lines, t=2):
    m = np.zeros((h, w), np.uint8)
    for p, q in lines:
        cv2.line(m, p, q, 1, t)
    return m.astype(bool)


def test_crossing_wires_become_two_spans():
    m = _draw(300, 600, [((10, 100), (590, 160)), ((10, 220), (590, 60))])  # an X
    spans = spans_from_class_mask(m, m.astype(float))
    assert len(spans) == 2
    for s, conf in spans:
        xs = np.nonzero(s)[1]
        assert np.ptp(xs) > 500 and conf == 1.0       # each span runs the full width through the crossing
    assert sum(s.sum() for s, _ in spans) == m.sum()  # every pixel assigned exactly once


def test_parallel_wires_stay_separate_and_tiny_blobs_are_dropped():
    m = _draw(200, 500, [((10, 50), (490, 60)), ((10, 120), (490, 130))])
    m[180:183, 10:15] = True  # a speck
    spans = spans_from_class_mask(m, np.full(m.shape, 0.7))
    assert len(spans) == 2 and all(abs(c - 0.7) < 1e-9 for _, c in spans)


def test_a_t_junction_keeps_the_through_wire_whole():
    m = _draw(300, 600, [((10, 150), (590, 150)), ((300, 150), (300, 290))])  # a drop wire from a span
    spans = sorted(spans_from_class_mask(m, m.astype(float)), key=lambda s: -s[0].sum())
    assert len(spans) == 2
    assert np.ptp(np.nonzero(spans[0][0])[1]) > 550     # the horizontal span is not cut at the T
