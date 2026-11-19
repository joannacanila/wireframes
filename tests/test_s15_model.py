"""S15 model, measurement and reference repairs (register D-31, D-32).

* The strand-type head is fed the 1024 letterbox frame it was fitted in; a square 1024 photo is unchanged.
* Its hand features are clipped to the range of its fitting strands (sidecar tied to the checkpoint by hash).
* A kinked trace (two straight pieces at an angle) is split, never read as sag; a hanging span is never split.
* Telecom / fiber has its own contextual bands, separate from the power reference; unknown types get neither.
* An INCONCLUSIVE verdict says why: the photo, or a missing reference (not a reason to retake the photo).
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest
from pydantic import ValidationError

from app.core.config import ROOT_DIR
from app.pipeline.catenary import fit_catenary
from app.pipeline.preprocess import Letterbox
from app.pipeline.span_fit import fit_span, two_line
from app.pipeline.strand_head import Head, load_head, to_letterbox
from app.services.comparison import compare_wire, reference_info, references, wire_states

SIZE = (2000, 1200)


# ----------------------------------------------------------------------------- strand head inputs
def _lb(w, h, size=1024):
    s = min(size / w, size / h)
    nw, nh = round(w * s), round(h * s)
    return Letterbox(s, (size - nw) // 2, (size - nh) // 2, nw, nh, size, w, h)


def test_letterbox_mapping_is_the_identity_for_a_square_1024_photo():
    m = np.zeros((1024, 1024), bool)
    m[300, 100:900] = True  # a 1 px strand
    assert np.array_equal(to_letterbox(m, _lb(1024, 1024)), m)


def test_letterbox_mapping_places_a_field_photo_strand_inside_the_content_box():
    lb = _lb(2048, 1536)  # a 4:3 phone photo: 1024 x 768 content, 128 px bars
    m = np.zeros((1536, 2048), bool)
    m[0:2, :] = True  # the top row of the photo
    out = to_letterbox(m, lb)
    ys = np.nonzero(out.any(1))[0]
    assert lb.pad_y == 128 and ys.min() == 128 and ys.max() <= 129 and out[:128].sum() == 0
    thin = np.zeros((1536, 2048), bool)
    thin[700, 10:2000] = True  # 1 px at full size: kept after the 2x reduction
    assert to_letterbox(thin, lb).any()


def test_feature_bounds_belong_to_the_shipped_head_and_clip_out_of_range_inputs():
    path = ROOT_DIR / "models/strand_head_v217.pt"
    if not path.exists() or path.stat().st_size == 0:
        pytest.skip("strand head weights not installed")
    b = json.loads(path.with_suffix(".bounds.json").read_text())
    assert b["checkpoint_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    meta = json.loads(path.with_suffix(".json").read_text())
    assert len(b["lo"]) == len(b["hi"]) == len(meta["stats"]["mu"])
    head = load_head(path, clamp=True)
    plain = load_head(path)
    lo, hi = np.asarray(b["lo"]), np.asarray(b["hi"])
    inside = ((lo + hi) / 2)[None].astype(np.float32)
    dino = np.zeros((1, 384), np.float32)
    assert np.allclose(head.x(inside, dino), plain.x(inside, dino))  # in range: identical
    far = inside.copy()
    far[0, 16] = hi[16] * 3  # e.g. a 3x larger strand count than ever seen
    assert np.allclose(head.x(far, dino), head.x(np.clip(far, lo, hi), dino))
    assert not np.allclose(plain.x(far, dino), head.x(far, dino))


def test_clamping_without_its_sidecar_is_a_load_error_not_a_silent_guess(tmp_path):
    src = ROOT_DIR / "models/strand_head_v217.pt"
    if not src.exists() or src.stat().st_size == 0:
        pytest.skip("strand head weights not installed")
    (tmp_path / "h.pt").write_bytes(src.read_bytes())
    (tmp_path / "h.json").write_text(src.with_suffix(".json").read_text())
    assert isinstance(load_head(tmp_path / "h.pt"), Head)
    with pytest.raises(FileNotFoundError):
        load_head(tmp_path / "h.pt", clamp=True)


# ----------------------------------------------------------------------------- kink-aware span fit
def _hanging(sag_ratio=0.04, n=400, x0=200.0, x1=1800.0, y0=300.0):
    """Image points (y down) of a level span with the given sag ratio (parabola ~ catenary at small sag)."""
    x = np.linspace(x0, x1, n)
    L = x1 - x0
    t = (x - x0) / L
    return np.stack([x, y0 + 4 * sag_ratio * L * t * (1 - t)], 1)


def _vee(n=400, drop=160.0):
    """Two straight pieces meeting at a corner: what a trace that switches cables or meets a support looks like."""
    x = np.linspace(200.0, 1800.0, n)
    y = 300 + drop * (1 - np.abs(x - 1000) / 800)
    return np.stack([x, y], 1)


@pytest.mark.parametrize("ratio", [0.005, 0.02, 0.04, 0.08])
def test_a_hanging_span_is_measured_as_before_and_never_split(ratio):
    pts = _hanging(ratio)
    a, b = fit_catenary(pts, image_size=SIZE), fit_span(pts, image_size=SIZE)
    assert b.ok and not b.kink_split and b.sag_ratio == pytest.approx(a.sag_ratio)
    assert b.sag_ratio == pytest.approx(ratio, rel=0.05)


def test_a_kinked_trace_is_not_read_as_sag():
    pts = _vee()
    one = fit_catenary(pts, image_size=SIZE, max_rel_residual=1.0)  # the old reading, residual gate opened
    assert one.sag_ratio > 0.05  # the V read as a deep dip
    rms2, k = two_line(pts)
    assert rms2 < 0.5 * one.rms_residual_px and abs(k - len(pts) // 2) <= len(pts) // 40
    f = fit_span(pts, image_size=SIZE)
    assert f.kink_split and f.ok and f.sag_ratio < 0.005 and 0.3 < f.excluded_frac < 0.7  # each piece is straight


def test_a_kinked_trace_with_no_piece_that_fits_reports_why():
    rng = np.random.default_rng(0)
    pts = _vee()
    pts[:, 1] += rng.normal(0, 25, len(pts))  # pieces too rough for any curve
    f = fit_span(pts, image_size=SIZE, max_rel_residual=0.005, kink_check_from=0.0, kink_ratio=0.99)
    if f.kink_split:
        assert f.ok or f.reason == "kinked_trace"
    assert not (f.ok and f.sag_ratio and f.sag_ratio > 0.05)  # never a deep dip from a bend


# ----------------------------------------------------------------------------- telecom reference (Part 2)
def _w(cls="telecom-cable", ratio=0.03, res=0.0, **kw):
    return {"index": 1, "class": cls, "status": "not_assessed", "reason_code": "no_validated_reference",
            "sag_ratio": ratio, "sag_ratio_resolution": res, "flags": [], **kw}


@pytest.mark.parametrize("ratio,zone", [(0.0, "below"), (0.0199, "below"), (0.02, "at"), (0.035, "at"),
                                        (0.05, "at"), (0.0501, "above"), (0.12, "above")])
def test_telecom_band_boundaries(s16_cfg, ratio, zone):
    c = compare_wire(_w(ratio=ratio), s16_cfg)
    assert c["position"] == zone and c["reference_id"] == "telecom" and c["banded"]


def test_measurement_uncertainty_widens_the_middle_band(s16_cfg):
    assert compare_wire(_w(ratio=0.018, res=0.003), s16_cfg)["position"] == "at"  # cannot be told from 2%
    assert compare_wire(_w(ratio=0.052, res=0.003), s16_cfg)["position"] == "at"


def test_power_and_telecom_references_stay_separate(s16_cfg):
    assert [r["id"] for r in references(s16_cfg)] == ["power", "telecom"]
    p, t = reference_info(s16_cfg, "power-distribution-lines"), reference_info(s16_cfg, "telecom-cable")
    assert p["sag_ratio"] == 0.03 and p["classes"] == ["power-distribution-lines"] and not p["banded"]
    assert (t["lower_boundary"], t["upper_boundary"]) == (0.02, 0.05) and t["classes"] == ["telecom-cable"]
    assert compare_wire(_w(cls="power-distribution-lines", ratio=0.04), s16_cfg)["reference"] == 0.03
    for cfgref in (s16_cfg.comparison_reference, s16_cfg.telecom_reference):
        assert cfgref.classification_enabled is False  # comparisons, never severity
    clash = s16_cfg.telecom_reference.model_copy(update={"classes": ["power-distribution-lines"]})
    with pytest.raises(ValidationError):
        type(s16_cfg).model_validate({**s16_cfg.model_dump(), "telecom_reference": clash.model_dump()})


def test_unknown_or_uncertain_types_get_neither_reference(s16_cfg):
    for w in (_w(class_status="uncertain"), _w(flags=["class_uncertain"]), _w(cls="abandoned-wire"),
              _w(status="service_drop")):
        assert compare_wire(w, s16_cfg) is None
        assert wire_states(w, s16_cfg, None)["reference_available"] is False


@pytest.mark.parametrize("which", ["s16", "shipped"])
def test_telecom_wording_never_claims_tension_clearance_or_compliance(s16_cfg, shipped_cfg, which):
    from app.services.outcome import wire_finding
    cfg = s16_cfg if which == "s16" else shipped_cfg
    # S17: each sentence now carries the wire's own ratio, so the middle case avoids reading exactly "3.0%"
    for ratio in (0.01, 0.034, 0.09):
        w = _w(ratio=ratio)
        w["comparison"] = compare_wire(w, cfg)
        w["states"] = wire_states(w, cfg, w["comparison"])
        text = " ".join([wire_finding(w)["plain"], wire_finding(w)["meter"]["note"]]).lower()
        for banned in ("tension", "clearance", "complian", "illegal", "violat", "safe", "3%", "3.0%"):
            assert banned not in text, (ratio, banned)


# ----------------------------------------------------------------------------- verdict cause and provenance
def test_an_inconclusive_verdict_says_whether_the_photo_or_the_reference_is_missing():
    from app.pipeline.hazard import INCONCLUSIVE, _cause, apply_reliability
    assert _cause({"no_validated_reference": 5, "endpoint_unavailable": 2}) == "no_reference"
    assert _cause({"endpoint_unavailable": 3}) == "partial_spans" and _cause({}) == "photo"
    hz = apply_reliability({"verdict": "NOT AT RISK", "reasons": [], "factors": {}}, {"grade": "Retake recommended"})
    assert hz["verdict"] == INCONCLUSIVE and hz["factors"]["inconclusive_cause"] == "photo"


def test_every_result_records_the_methods_that_produced_it(client, scene):
    from tests.conftest import upload, wait_done
    aid = upload(client, scene.rgb, scene.labels)
    wait_done(client, aid)
    from app.models.orm import Analysis
    with client.state.db.session() as s:
        md = s.get(Analysis, aid).metadata_json
    m = md["methods"]
    assert m["measurement_algorithm"] == "catenary+kink-split/1" and m["strand_head_frame"] == "letterbox"
    assert m["strand_head_clamp"] is True and m["references"] == ["power", "telecom"] and m["config_fingerprint"]
