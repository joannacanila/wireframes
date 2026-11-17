"""v2.8: hazard verdict, chord precision gate, reliability grade, diagnostic figure, batch audit."""

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.core.config import QualityConfig
from app.pipeline.hazard import HIGH, MODERATE, NOT, assess, clearance_scale, congestion
from app.pipeline.quality import grade, reliability
from app.pipeline.runner import run_analysis
from app.pipeline.segment import WireDetection, _bbox, parse_yolo_seg_labels

SHAPE = (400, 800)


def _wire(y, t=3, x0=20, x1=780):
    m = np.zeros(SHAPE, np.uint8)
    cv2.line(m, (x0, y), (x1, y + 20), 1, t)
    return m.astype(bool)


def _row(i, cls, mask, level="low", status="scored"):
    return {"index": i, "class": cls, "mask": mask, "level": level, "status": status}


def _veg(y0, y1, x0=300, x1=420):
    m = np.zeros(SHAPE, bool)
    m[y0:y1, x0:x1] = True
    return m


P, T = "power-distribution-lines", "telecom-cable"


@pytest.mark.test_reference
def test_vegetation_contact_with_power_is_high_and_explained():
    from tests.conftest import TEST_ONLY_CONDUCTORS
    rule = type("Rule", (), {"vegetation_verdict": True})()
    hz = assess([_row(1, P, _wire(100)), _row(2, T, _wire(250))], [_veg(60, 112)], SHAPE,
                TEST_ONLY_CONDUCTORS[P]["diameter_m"], rule=rule)  # TEST-ONLY diameter (S4: none shipped)
    assert hz.verdict == HIGH and any("contact with power span 1" in r for r in hz.reasons)
    near = hz.factors["vegetation"][0]
    assert near["span"] == 1 and near["gap_px"] == 0 and "wire_point" in near and "gap_m_approx" in near


def test_close_vegetation_is_moderate_and_far_is_not_at_risk():
    near = assess([_row(1, P, _wire(100))], [_veg(60, 96)], SHAPE)      # ~10 px above the wire (it is at y~107 there)
    assert near.verdict == MODERATE
    far = assess([_row(1, P, _wire(100))], [_veg(300, 380)], SHAPE)
    assert far.verdict == NOT and far.reasons[-1].startswith("NOT AT RISK")
    tel = assess([_row(1, T, _wire(100))], [_veg(60, 112)], SHAPE)
    assert tel.verdict == MODERATE  # telecom contact is moderate, power contact is high


def test_sag_levels_drive_the_verdict_and_too_short_spans_do_not():
    assert assess([_row(1, P, _wire(100), "high")], [], SHAPE).verdict == HIGH
    assert assess([_row(1, P, _wire(100), "medium")], [], SHAPE).verdict == MODERATE
    hz = assess([_row(1, P, _wire(100), None, "too_short")], [], SHAPE)
    assert hz.verdict == NOT and hz.factors["sag"]["too_short_spans"] == 1
    assert "no span long enough" in hz.reasons[-1]


def test_clearance_scale_and_congestion():
    s = clearance_scale([_wire(100, t=4)], 0.0101)
    assert 3 <= s["median_wire_width_px"] <= 6 and s["m_per_px_low"] < s["m_per_px"] < s["m_per_px_high"]
    assert clearance_scale([], 0.0101) is None
    a, b = np.zeros(SHAPE, np.uint8), np.zeros(SHAPE, np.uint8)
    cv2.line(a, (20, 50), (780, 350), 1, 2)
    cv2.line(b, (20, 350), (780, 50), 1, 2)
    c = congestion([a.astype(bool), b.astype(bool)], SHAPE)
    assert c["spans"] == 2 and c["crossings"] >= 1
    assert congestion([], SHAPE)["spans"] == 0


@pytest.mark.every_class_assessed
def test_chord_gate_removes_noisy_short_spans_from_scoring(cfg, scene):
    h, w = scene.rgb.shape[:2]
    dets = parse_yolo_seg_labels(scene.labels, w, h)

    class Empty:
        source = "model"
        def segment(self, x, lb): return []

    base = run_analysis(scene.rgb, cfg, Empty(), detections=dets)
    gated_cfg = cfg.model_copy(update={"crs": cfg.crs.model_copy(update={"min_chord_px": 10_000})})
    gated = run_analysis(scene.rgb, gated_cfg, Empty(), detections=dets)
    assert all(x.crs is not None for x in base.wires)
    assert all(x.crs is None and x.fail_reason == "chord_below_precision_gate" for x in gated.wires)


@pytest.mark.test_reference
def test_hazard_and_quality_flow_through_the_runner(cfg, scene):
    h, w = scene.rgb.shape[:2]
    on = cfg.model_copy(update={"hazard": cfg.hazard.model_copy(update={"enabled": True, "loose_ends": True}),
                                "quality": cfg.quality.model_copy(update={"enabled": True})})

    class Stub:
        source = "model"
        last_uncertainty = {"wire_pixels": 5000, "entropy": 0.1, "disagreement": 0.05}

        def segment(self, x, lb):
            return [WireDetection(d.wire_class, 0.9, d.mask, d.bbox) for d in parse_yolo_seg_labels(scene.labels, w, h)]

    o = run_analysis(scene.rgb, on, Stub())
    assert o.hazard["verdict"] in (HIGH, MODERATE, NOT) and o.hazard["reasons"]
    assert o.quality["grade"] == "A" and o.quality["reliability"] == pytest.approx(0.925)


@pytest.mark.parametrize("unc,qual,expected", [
    ({"wire_pixels": 5000, "entropy": 0.10, "disagreement": 0.05}, {"mean_l": 50, "clipped_high": 0.0}, "A"),
    ({"wire_pixels": 5000, "entropy": 0.30, "disagreement": 0.30}, {"mean_l": 50, "clipped_high": 0.0}, "B"),
    ({"wire_pixels": 5000, "entropy": 0.40, "disagreement": 0.40}, {"mean_l": 50, "clipped_high": 0.0}, "C"),
    ({"wire_pixels": 5000, "entropy": 0.60, "disagreement": 0.60}, {"mean_l": 50, "clipped_high": 0.0}, "Retake recommended"),
    ({"wire_pixels": 10, "entropy": 0.05, "disagreement": 0.0}, {"mean_l": 50, "clipped_high": 0.0}, "Retake recommended"),
    ({"wire_pixels": 5000, "entropy": 0.05, "disagreement": 0.0}, {"mean_l": 3, "clipped_high": 0.0}, "Retake recommended"),
    ({"wire_pixels": 5000, "entropy": 0.05, "disagreement": 0.0}, {"mean_l": 90, "clipped_high": 0.95}, "Retake recommended"),
    (None, None, "Retake recommended"),
])
def test_quality_grades(unc, qual, expected):
    assert grade(unc, qual, QualityConfig())["grade"] == expected


def test_reliability_without_tta_uses_entropy_only():
    assert reliability({"entropy": 0.2, "disagreement": None}) == pytest.approx(0.8)
    assert reliability(None) is None


def test_diagnostic_renders_empty_and_full_results(scene):
    from app.services.diagnostic import render_diagnostic
    empty = render_diagnostic(scene.rgb, {"wires": [], "vegetation": [], "hazard": None})
    assert empty.ndim == 3 and empty.shape[1] > 2 * empty.shape[0]
    wire = {"index": 1, "class": P, "mask_polygons": [[[10, 10], [500, 12], [500, 16], [10, 14]]],
            "catenary_curve": [[10, 12], [500, 14]], "level": "high", "crs": 8.0}
    res = {"wires": [wire], "vegetation": [{"mask_polygons": [[[100, 20], [200, 20], [200, 80], [100, 80]]]}],
           "hazard": {"verdict": HIGH, "reasons": [], "factors": {"vegetation": [
               {"span": 1, "gap_px": 4.0, "wire_point": [14, 150], "vegetation_point": [20, 150], "gap_m_approx": [0.01, 0.02]}],
               "loose_ends": [{"x": 500, "y": 14, "score": 0.9}]}}, "quality": {"grade": "B"}}
    full = render_diagnostic(scene.rgb, res)
    assert full.shape == empty.shape and not np.array_equal(full, empty)
    tall = render_diagnostic(np.zeros((1200, 300, 3), np.uint8), res)  # extreme aspect ratio
    assert tall.shape[0] == 900


def test_batch_audit_handles_bad_inputs_and_exports_review_queue(tmp_path, cfg, scene):
    from scripts.batch_inspect import run
    h, w = scene.rgb.shape[:2]
    src = tmp_path / "in"
    src.mkdir()
    ok, jpg = cv2.imencode(".jpg", cv2.cvtColor(scene.rgb, cv2.COLOR_RGB2BGR))
    (src / "a_pole.jpg").write_bytes(jpg.tobytes())
    (src / "b_duplicate.jpg").write_bytes(jpg.tobytes())                    # exact duplicate
    (src / "c_corrupt.jpg").write_bytes(b"\xff\xd8 not really a jpeg")
    cv2.imwrite(str(src / "d_tiny.png"), np.zeros((40, 60, 3), np.uint8) + 120)
    cv2.imwrite(str(src / "e_constant.png"), np.full((600, 800, 3), 77, np.uint8))
    (src / "notes.txt").write_text("not an image")
    on = cfg.model_copy(update={"hazard": cfg.hazard.model_copy(update={"enabled": True}),
                                "quality": cfg.quality.model_copy(update={"enabled": True})})

    class Stub:
        source, error = "model", None
        last_uncertainty = {"wire_pixels": 5000, "entropy": 0.5, "disagreement": 0.5}  # low reliability -> review

        def load(self):
            return True

        def segment(self, x, lb):
            return [WireDetection(d.wire_class, 0.9, d.mask, _bbox(d.mask)) for d in parse_yolo_seg_labels(scene.labels, w, h)]

    out = tmp_path / "out"
    s = run(src, out, cfg=on, segmenter=Stub())
    assert s["analysed"] == 1 and s["duplicates_skipped"] == 1 and s["rejected"] == 3
    rej = (out / "rejected.csv").read_text()
    assert "undecodable" in rej and "resolution_too_low" in rej and "constant_image" in rej
    rows = (out / "inspection.csv").read_text().splitlines()
    assert len(rows) == 2 and "verdict" in rows[0]
    lm = json.loads(next((out / "review_queue").glob("*.json")).read_text())
    assert lm["shapes"] and lm["imagePath"].endswith(".jpg") and lm["shapes"][0]["shape_type"] == "polygon"
    assert (out / "summary.md").read_text().startswith("# Street audit summary")
    assert list((out / "diagnostics").glob("*.jpg"))
    _ = Path


def test_unreadable_photo_is_never_reported_as_safe():
    from app.pipeline.hazard import INCONCLUSIVE, apply_reliability
    safe = {"verdict": NOT, "reasons": ["NOT AT RISK: 0 power and 0 telecom spans detected"], "factors": {}}
    retake = {"grade": "Retake recommended", "reason": "unreadable exposure"}
    out = apply_reliability(safe, retake)
    assert out["verdict"] == INCONCLUSIVE and out["reasons"][0].startswith(INCONCLUSIVE)
    risky = {"verdict": HIGH, "reasons": ["AT RISK - HIGH: sag"], "factors": {}}
    kept = apply_reliability(risky, retake)
    assert kept["verdict"] == HIGH and kept["reasons"][-1].startswith("NOTE: photo reliability low")
    assert apply_reliability(safe, {"grade": "A"}) is safe and apply_reliability(None, retake) is None
