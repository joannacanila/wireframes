"""S4 threshold resolution (18 Nov 2026; docs/handoff/RESEARCH_AND_DECISION_REGISTER.md D-02..D-21).

No sag reference, conductor data or pixel vegetation rule is supported by an applicable source, so the shipped
configuration assesses no span: every span is measured (image-derived sag ratio with its resolution) and reported as
not assessed (no_validated_reference); the pole verdict is never NOT AT RISK or AT RISK from these factors. Stored
results from before S4 are shown without their withdrawn severities.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.core.config import WIRE_CLASSES
from app.pipeline.hazard import INCONCLUSIVE, NOT, assess
from app.pipeline.runner import score_wire
from app.pipeline.sag_limits import EngineeringContext
from app.pipeline.segment import WireDetection, _bbox


def _wire(sag_frac: float, h=600, w=1000, x0=200, x1=800):
    """A parabolic wire whose sag is ``sag_frac`` of its 600 px chord."""
    m = np.zeros((h, w), bool)
    xs = np.arange(x0, x1)
    s = sag_frac * (x1 - x0)
    ys = np.round(200 + s * (1 - ((xs - (x0 + x1) / 2) / ((x1 - x0) / 2)) ** 2)).astype(int)
    for dy in range(-2, 3):
        m[ys + dy, xs] = True
    return m


def _det(mask, cls="telecom-cable"):
    return WireDetection(wire_class=cls, score=0.9, mask=mask, bbox=_bbox(mask))


def test_shipped_configuration_has_no_unsupported_threshold(shipped_cfg):
    c = shipped_cfg
    assert c.crs.delta_lim0 == {} and not any(c.crs.has_reference(k) for k in WIRE_CLASSES)
    assert c.crs.levels.minor_from is None and c.crs.levels.critical_from is None  # 2.4 % / 4.0 % bands removed
    assert c.crs.min_chord_px == 0  # D-09: gate derived from the removed reference
    assert c.engineering.conductors == {}  # D-14: illustrative conductor data removed
    assert c.hazard.vegetation_verdict is False and c.hazard.min_assessed_share == 1.0  # D-15, D-08


@pytest.mark.parametrize("sag_pct", [1.0, 2.39, 2.4, 2.41, 3.0, 3.99, 4.0, 4.01, 6.0])
@pytest.mark.parametrize("cls", ["telecom-cable", "power-distribution-lines", "abandoned-wire"])
def test_no_boundary_exists_at_the_former_cut_offs(shipped_cfg, sag_pct, cls):
    """Below, at and above 2.4 % and 4.0 % (the removed bands) the outcome is the same: measured, not assessed."""
    o = score_wire(_det(_wire(sag_pct / 100), cls), 1, (1000, 600), shipped_cfg, EngineeringContext())
    assert (o.status, o.fail_reason, o.crs, o.level, o.delta_lim) == ("not_assessed", "no_validated_reference",
                                                                      None, None, None)
    assert o.fit["sag_ratio"] == pytest.approx(sag_pct / 100, abs=0.004)  # the measurement is kept
    assert o.fit["sag_ratio_resolution"] == pytest.approx(1 / o.fit["chord_px"], abs=1e-6)
    assert o.curve and "unverified_level" not in o.fit


def test_short_chord_is_measured_with_its_coarser_resolution(shipped_cfg):
    o = score_wire(_det(_wire(0.03, x0=450, x1=530)), 1, (1000, 600), shipped_cfg, EngineeringContext())
    assert o.status == "not_assessed" and o.fit["sag_ratio_resolution"] > 0.01  # 80 px chord: +/- 1.25 points


def test_pole_with_only_unassessed_spans_is_inconclusive(shipped_cfg):
    rows = [{"index": i, "class": "telecom-cable", "mask": _wire(0.01), "level": None, "status": "not_assessed",
             "flags": ["no_validated_reference"]} for i in (1, 2, 3)]
    hz = assess(rows, [], (600, 1000), rule=shipped_cfg.hazard)
    assert hz.verdict == INCONCLUSIVE and "without a validated sag reference" in hz.reasons[0]


def test_vegetation_overlap_is_an_observation_not_a_hazard(shipped_cfg):
    wire = _wire(0.01)
    veg = np.zeros_like(wire)
    veg[150:215, 450:560] = True
    rows = [{"index": 1, "class": "power-distribution-lines", "mask": wire, "level": None, "status": "not_assessed",
             "flags": ["no_validated_reference"]}]
    hz = assess(rows, [veg], (600, 1000), rule=shipped_cfg.hazard)
    assert hz.verdict == INCONCLUSIVE
    assert any("overlaps or adjoins span 1 in the image" in r for r in hz.reasons)
    assert hz.factors["vegetation"][0]["gap_px"] == 0 and "gap_m_approx" not in hz.factors["vegetation"][0]


def test_no_measured_wire_is_never_not_at_risk(shipped_cfg):
    assert assess([], [], (600, 1000), rule=shipped_cfg.hazard).verdict == INCONCLUSIVE
    # the mechanism is unchanged when no rule is given (pre-v2.21 callers)
    assert assess([], [], (600, 1000)).verdict == NOT


def test_historical_result_is_shown_without_withdrawn_severity(client):
    """A result stored before S4 (no assessment_basis): stored rows unchanged, severity and verdict not shown."""
    from app.devtools.synthetic import make_scene as scene_of
    from app.models.orm import Analysis
    from app.services import records
    from app.services.basis import BASIS
    from tests.conftest import upload, wait_done

    sc = scene_of()
    aid = upload(client, sc.rgb, sc.labels)
    assert wait_done(client, aid)["status"] == "complete"  # measured spans make the analysis complete
    with client.state.db.session() as s:
        a = s.get(Analysis, aid)
        assert a.metadata_json["assessment_basis"] == BASIS
        d = a.detections[0]
        d.status, d.level, d.crs, d.delta_lim = "scored", "high", 10.0, 0.04  # as stored by v2.23
        a.metadata_json = {**{k: v for k, v in a.metadata_json.items() if k != "assessment_basis"},
                           "hazard": {**a.metadata_json["hazard"], "verdict": "AT RISK - HIGH", "reasons": ["x"]}}
        idx = d.wire_index
    r = client.get(f"/api/analysis/{aid}/result").json()
    w = next(x for x in r["wires"] if x["index"] == idx)
    assert w["level"] is None and w["crs"] is None and w["reason_code"] == "withdrawn_reference"
    assert w["historical"]["level"] == "high"
    assert r["hazard"]["verdict"] == INCONCLUSIVE and r["hazard"]["historical_verdict"] == "AT RISK - HIGH"
    assert r["assessment_basis"]["notice"] and r["summary"]["scored_count"] == 0
    with client.state.db.session() as s:
        a = s.get(Analysis, aid)
        assert records.risk(a) == "unassessed" and a.valid_crs == []
        assert next(x for x in a.detections if x.wire_index == idx).level == "high"  # stored row unchanged
    assert records.RISK_LABEL["unassessed"] == "Assessment incomplete"
    pdf = client.get(f"/api/reports/{aid}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
