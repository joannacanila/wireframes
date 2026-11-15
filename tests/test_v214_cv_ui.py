"""v2.14: observed vs inferred span geometry, short reasons, and the two topology options (off by default)."""

from __future__ import annotations

import numpy as np

from app.core.config import CLASSES
from app.pipeline.segment import mask_to_polygons
from app.pipeline.wire_instances import extract_spans, lateral_offset, spans_from_class_mask, stitch_spans
from app.services.provenance import span_provenance
from app.services.reasons import REASONS, wire_reason_short

TEL = CLASSES.index("telecom-cable") + 1


def _line(shape, y, x0, x1):
    m = np.zeros(shape, bool)
    m[y, x0:x1] = True
    return m


def test_provenance_separates_bridge_pixels_from_model_pixels():
    label = np.zeros((40, 200), np.uint8)
    label[20:22, 10:80] = TEL
    label[20:22, 120:190] = TEL                   # the model saw two pieces; the span bridges 80..120
    span = _line(label.shape, 20, 10, 190) | _line(label.shape, 21, 10, 190)
    p = span_provenance(label, "telecom-cable", mask_to_polygons(span), [[x, 20] for x in range(10, 190)])
    assert p["inferred_px"] > 0 and p["observed_px"] > 0
    assert len(p["inferred_segments"]) == 1
    (x0, _), (x1, _) = p["inferred_segments"][0]
    assert min(x0, x1) >= 79 and max(x0, x1) <= 121
    assert 0.15 < p["centerline_inferred_share"] < 0.3


def test_provenance_of_a_fully_detected_span_is_empty():
    label = np.zeros((30, 100), np.uint8)
    label[10:12, 5:95] = TEL
    span = label == TEL
    p = span_provenance(label, "telecom-cable", mask_to_polygons(span), [[x, 10] for x in range(5, 95)])
    assert p["inferred_px"] == 0 and p["inferred_segments"] == [] and p["centerline_inferred_share"] == 0.0


def test_short_reasons_cover_every_code():
    for code in REASONS:
        assert wire_reason_short(code)
    assert wire_reason_short("chord_below_precision_gate") == "Insufficient visible length"
    assert wire_reason_short("residual_too_large") == "Ambiguous wire path"
    assert wire_reason_short(None) is None


def test_lateral_offset_measures_sideways_distance():
    a = (np.array([0.0, 0.0]), np.array([0.0, -1.0]), np.array([0.0, 10.0]), np.array([0.0, 1.0]), 10.0)
    b = (np.array([20.0, 40.0]), np.array([0.0, -1.0]), np.array([20.0, 80.0]), np.array([0.0, 1.0]), 40.0)
    assert lateral_offset(a, b, a[2], b[0]) == 20.0
    c = (np.array([0.0, 40.0]), np.array([0.0, -1.0]), np.array([0.0, 80.0]), np.array([0.0, 1.0]), 40.0)
    assert lateral_offset(a, c, a[2], c[0]) == 0.0


def test_lateral_gate_keeps_a_dropout_and_rejects_a_parallel_neighbour():
    shape = (80, 300)
    left = _line(shape, 30, 10, 120)
    same = _line(shape, 30, 160, 290)           # same wire after a 40 px dropout
    parallel = _line(shape, 45, 160, 290)       # a different wire 15 px lower (gap angle 20 deg < 25)
    spans = [(left, 0.9), (same, 0.9)]
    assert len(stitch_spans(spans, max_gap=70, max_angle_deg=25, max_lateral_px=5)) == 1
    spans = [(left, 0.9), (parallel, 0.9)]
    assert len(stitch_spans(spans, max_gap=70, max_angle_deg=25)) == 1            # shipped rule joins them
    assert len(stitch_spans(spans, max_gap=70, max_angle_deg=25, max_lateral_px=5)) == 2


def test_bridges_out_reports_each_accepted_bridge():
    shape = (60, 300)
    out: list = []
    stitch_spans([(_line(shape, 30, 10, 120), 0.9), (_line(shape, 30, 160, 290), 0.9)], max_gap=70,
                 max_angle_deg=25, bridges_out=out)
    assert len(out) == 1


def test_options_default_off_keep_the_shipped_extractor():
    rng = np.random.default_rng(0)
    m = np.zeros((120, 400), bool)
    m[40, 10:390] = True
    m[41, 10:390] = True
    m[40:42, 150:170] = False
    m[80, 30:300] = True
    prob = rng.random(m.shape).astype(np.float32)
    kw = dict(max_gap=70, gap_angle_deg=25, min_span_len=90, max_turn_deg=35, cluster_radius=5, min_len=15)
    a = extract_spans(m, prob, **kw)
    b = extract_spans(m, prob, **kw, bridge_min_prob=0.0, max_lateral_px=0.0, own_component=False)
    assert len(a) == len(b) and all((x[0] == y[0]).all() for x, y in zip(a, b, strict=True))


def test_own_component_keeps_a_detached_blob_out_of_the_nearest_span():
    m = np.zeros((60, 200), bool)
    m[20, 10:190] = True                       # a wire
    m[24:26, 60:66] = True                     # a separate small blob, too small to keep its own piece
    off = spans_from_class_mask(m, np.ones(m.shape, np.float32), min_px=5, min_len=30)
    on = spans_from_class_mask(m, np.ones(m.shape, np.float32), min_px=5, min_len=30, own_component=True)
    assert off and on
    assert max(s.sum() for s, _ in on) <= max(s.sum() for s, _ in off)
