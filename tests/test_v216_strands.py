"""v2.16: one class per physical strand, scene gates (structure edges, non-vegetation), the ultra-wide lens rule and
the resolution-aware chord precision gate."""

from __future__ import annotations

import numpy as np
import pytest

from app.core.config import CLASSES, ROOT_DIR, VEGETATION, load_analysis_config
from app.pipeline.scene import side_contrast, vegetation_share
from app.pipeline.wire_instances import extract_spans, strand_spans

POWER, TELECOM = CLASSES.index("power-distribution-lines") + 1, CLASSES.index("telecom-cable") + 1
WIRE_IDS = [CLASSES.index(c) + 1 for c in CLASSES if c != VEGETATION]
PARAMS = {"min_px": 10, "min_len": 10, "max_turn_deg": 35, "cluster_radius": 5, "max_gap": 0, "min_span_len": 0}


def _probs(h=60, w=200):
    p = np.zeros((len(CLASSES) + 1, h, w), np.float32)
    p[0] = 1.0
    return p


def _paint(p, rows, cols, cls, val=0.8):
    p[:, rows, cols] = (1 - val) / (p.shape[0] - 1)
    p[cls, rows, cols] = val


def test_wire_whose_pixels_flip_class_is_one_strand_with_one_class():
    """The reported defect: a continuous cable labelled power on its left part and telecom on its right."""
    p = _probs()
    _paint(p, slice(29, 32), slice(10, 130), POWER)
    _paint(p, slice(29, 32), slice(130, 190), TELECOM)
    # the v2.15 per-class extractor gives two spans of different classes on one wire
    cls = p.argmax(0)
    old = [(c, m) for c in WIRE_IDS for m, _ in extract_spans(cls == c, p[c], **PARAMS)]
    assert {c for c, _ in old} == {POWER, TELECOM}
    strands = strand_spans(p, WIRE_IDS, PARAMS)
    assert len(strands) == 1
    s = strands[0]
    assert s.wire_class == "power-distribution-lines"          # 120 px of power evidence vs 60 px of telecom
    assert s.mask[30, 10:190].all()                             # one mask along the whole cable
    assert abs(sum(s.class_share.values()) - 1) < 1e-6


def test_split_evidence_is_marked_uncertain_not_forced():
    p = _probs()
    _paint(p, slice(29, 32), slice(10, 100), POWER)
    _paint(p, slice(29, 32), slice(100, 190), TELECOM)
    s, = strand_spans(p, WIRE_IDS, PARAMS, min_class_share=0.9)
    assert s.class_status == "uncertain"
    s, = strand_spans(p, WIRE_IDS, PARAMS, min_class_share=0.0)
    assert s.class_status == "confident"


def test_crossing_wires_keep_their_own_classes():
    """An X of a power and a telecom wire: two strands, each classified from its own pixels."""
    h = w = 160
    p = _probs(h, w)
    for i in range(10, 150):
        _paint(p, slice(i - 1, i + 2), slice(i, i + 1), POWER)            # falling diagonal
    for i in range(10, 150):
        _paint(p, slice(i - 1, i + 2), slice(w - i, w - i + 1), TELECOM)  # rising diagonal (wins the crossing)
    strands = strand_spans(p, WIRE_IDS, PARAMS)
    assert len(strands) == 2
    for s in strands:
        ys, xs = np.nonzero(s.mask)
        falling = np.corrcoef(xs, ys)[0, 1] > 0
        assert s.wire_class == ("power-distribution-lines" if falling else "telecom-cable")
        assert s.class_status == "confident" or max(s.class_share.values()) > 0.5


def test_parallel_wires_are_not_merged():
    p = _probs()
    _paint(p, slice(20, 23), slice(10, 190), TELECOM)
    _paint(p, slice(36, 39), slice(10, 190), POWER)
    strands = strand_spans(p, WIRE_IDS, PARAMS)
    assert sorted(s.wire_class for s in strands) == ["power-distribution-lines", "telecom-cable"]


def test_side_contrast_separates_a_structure_edge_from_a_wire():
    lab = np.full((200, 400), 2, np.uint8)        # 2 = sky
    lab[100:] = 7                                 # 7 = bridge below row 100
    edge = np.zeros_like(lab, bool)
    edge[99:101, 20:380] = True                   # a "wire" lying exactly on the deck outline
    wire = np.zeros_like(lab, bool)
    wire[40:42, 20:380] = True                    # a wire in the open sky
    front = np.zeros_like(lab, bool)
    front[150:152, 20:380] = True                 # a wire seen in front of the deck
    assert side_contrast(edge, lab) >= 0.9
    assert side_contrast(wire, lab) == 0.0
    assert side_contrast(front, lab) == 0.0


def test_vegetation_share():
    lab = np.zeros((10, 10), np.uint8)
    lab[:, :3] = 4
    m = np.ones((10, 10), bool)
    assert vegetation_share(m, lab, [4]) == pytest.approx(0.3)
    assert vegetation_share(np.zeros_like(m), lab, [4]) == 0.0


def test_wide_lens_is_read_from_the_envelope():
    from app.pipeline.runner import wide_lens
    fail = [{"name": "focal_length_35mm", "status": "fail", "value": 14.0, "expected": [24.0, 50.0]}]
    tele = [{"name": "focal_length_35mm", "status": "fail", "value": 77.0, "expected": [24.0, 50.0]}]
    ok = [{"name": "focal_length_35mm", "status": "pass", "value": 26.0, "expected": [24.0, 50.0]}]
    assert wide_lens(fail) == (14.0, 24.0, 50.0)
    assert wide_lens(tele) is None and wide_lens(ok) is None and wide_lens(None) is None


def _sagging(h, w, sag_frac):
    from app.pipeline.segment import WireDetection, _bbox
    m = np.zeros((h, w), bool)
    xs = np.arange(int(0.1 * w), int(0.9 * w))
    L = xs[-1] - xs[0]
    t = (xs - xs[0]) / L
    ys = (0.3 * h + 4 * sag_frac * L * t * (1 - t)).astype(int)
    for x, y in zip(xs, ys, strict=True):
        m[y - 1: y + 2, x] = True
    return WireDetection("telecom-cable", 0.9, m, _bbox(m))


@pytest.mark.test_reference
def test_lens_outside_envelope_keeps_crs_but_not_the_sag_verdict():
    from app.pipeline.runner import run_analysis
    from tests.conftest import with_test_reference
    cfg = with_test_reference(load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml"))
    rgb = np.full((600, 800, 3), 200, np.uint8)
    det = _sagging(600, 800, 0.08)

    class Seg:
        source = "model"

        def segment(self, x, lb):
            return [det]

    env = [{"name": "focal_length_35mm", "status": "fail", "value": 14.0, "expected": [24.0, 50.0]}]
    base = run_analysis(rgb, cfg, Seg())
    wide = run_analysis(rgb, cfg, Seg(), envelope=env)
    assert base.wires[0].crs == wide.wires[0].crs is not None   # CRS definition unchanged, still stored
    assert base.hazard["verdict"].startswith("AT RISK")
    assert "lens_outside_envelope" in wide.flags and "lens_outside_envelope" in wide.wires[0].flags
    assert wide.hazard["verdict"] == "INCONCLUSIVE - RETAKE PHOTO"
    assert any("ultra-wide lens" in r for r in wide.hazard["reasons"])


@pytest.mark.test_reference
def test_chord_gate_scales_with_the_segmentation_resolution():
    from app.pipeline.runner import score_wire
    from app.pipeline.sag_limits import EngineeringContext
    from tests.conftest import with_test_reference
    cfg = with_test_reference(load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml"))
    det = _sagging(1536, 2048, 0.03)
    # chord ~1640 px: above both gates
    assert score_wire(det, 1, (2048, 1536), cfg, EngineeringContext(), gate_scale=2.0).status == "scored"
    short = _sagging(1536, 190, 0.03)                     # chord ~150 px: passes 100 px, fails 200 px
    a = score_wire(short, 1, (190, 1536), cfg, EngineeringContext(), gate_scale=1.0)
    b = score_wire(short, 1, (190, 1536), cfg, EngineeringContext(), gate_scale=2.0)
    assert a.status == "scored"
    assert b.status == "too_short" and b.fail_reason == "chord_below_precision_gate"
    assert b.fit["chord_gate_px"] == 200.0


def test_long_gap_is_bridged_only_across_a_detected_pole():
    from app.pipeline.wire_instances import stitch_spans
    a = np.zeros((60, 400), bool)
    a[30:32, 10:150] = True
    b = np.zeros_like(a)
    b[30:32, 250:390] = True                       # 100 px gap: longer than max_gap 70
    spans = [(a, 0.9), (b, 0.9)]
    assert len(stitch_spans(spans, max_gap=70, max_angle_deg=25)) == 2
    pole = [(190.0, 0.0, 210.0, 60.0)]
    elsewhere = [(300.0, 0.0, 320.0, 20.0)]
    assert len(stitch_spans(spans, max_gap=70, max_angle_deg=25, occluders=pole, occluder_max_gap=140)) == 1
    assert len(stitch_spans(spans, max_gap=70, max_angle_deg=25, occluders=elsewhere, occluder_max_gap=140)) == 2
    off = np.zeros_like(a)
    off[50:52, 250:390] = True                     # parallel neighbour 20 px lower: still not joined across the pole
    assert len(stitch_spans([(a, 0.9), (off, 0.9)], max_gap=70, max_angle_deg=25, occluders=pole,
                            occluder_max_gap=140, max_lateral_px=15)) == 2


def test_ends_at_pole_counts_endpoints_inside_grown_boxes():
    from app.pipeline.poles import ends_at_pole
    poles = [{"bbox": [100, 0, 120, 300]}, {"bbox": [500, 0, 520, 300]}]
    assert ends_at_pole([(118, 50), (505, 60)], poles, 5) == 2
    assert ends_at_pole([(118, 50), (300, 60)], poles, 5) == 1
    assert ends_at_pole([(90, 50), (300, 60)], poles, 5) == 0
    assert ends_at_pole(None, poles, 5) == 0
