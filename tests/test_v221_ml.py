"""v2.21 measurement rules and inference memory fix.

- Severity bands from the configuration (brief table): sag < 2.4% Within reference, 2.4% <= sag < 4.0% Minor sag,
  sag >= 4.0% Critical, at the 4.0% telecom reference.
- Validity gates: a trace that reaches the photo border, a wire of uncertain type and a power line (no configured
  reference) are measured but not assessed, each with its reason code.
- Low-memory ensemble inference returns the same probabilities as the fused forward pass.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.core.config import get_analysis_config
from app.pipeline.crs import catenary_risk_score, risk_level
from tests.conftest import with_test_reference  # noqa: E402

CFG = with_test_reference(get_analysis_config())  # TEST-ONLY reference (S4: none is shipped)
EXPECTED = {"low": "Within reference", "medium": "Minor sag", "high": "Critical"}


@pytest.mark.parametrize("sag_pct,label", [
    (1.2, "Within reference"), (1.3, "Within reference"), (2.2, "Within reference"), (2.39, "Within reference"),
    (2.4, "Minor sag"), (2.5, "Minor sag"), (2.7, "Minor sag"), (3.3, "Minor sag"), (3.6, "Minor sag"),
    (3.8, "Minor sag"), (3.9, "Minor sag"), (4.0, "Critical"), (4.9, "Critical"),
])
@pytest.mark.test_reference
def test_telecom_severity_boundaries(sag_pct, label):
    lim = CFG.crs.delta_lim0["telecom-cable"]
    assert lim == pytest.approx(0.04)
    crs = catenary_risk_score(sag_pct / 100, lim, CFG.crs.class_weights["telecom-cable"])
    assert EXPECTED[risk_level(crs, CFG.crs.levels)] == label


@pytest.mark.test_reference
def test_sag_bands_report_the_same_cut_offs():
    from app.services.analysis import sag_bands
    b = sag_bands(CFG, "telecom-cable", 0.04)
    assert b == {"minor_from": pytest.approx(0.024), "critical_from": pytest.approx(0.04), "critical_inclusive": True}


def _det(mask, cls="telecom-cable", status="confident"):
    from app.pipeline.segment import WireDetection, _bbox
    return WireDetection(wire_class=cls, score=0.9, mask=mask, bbox=_bbox(mask), class_status=status)


def _wire(h=600, w=1000, x0=200, x1=800, sag=20):
    m = np.zeros((h, w), bool)
    xs = np.arange(x0, x1)
    ys = (200 + sag * (1 - ((xs - (x0 + x1) / 2) / ((x1 - x0) / 2)) ** 2)).astype(int)
    for dy in range(-2, 3):
        m[ys + dy, xs] = True
    return m


@pytest.mark.test_reference
def test_validity_gates():
    from app.pipeline.runner import score_wire
    from app.pipeline.sag_limits import EngineeringContext
    ctx = EngineeringContext()
    ok = score_wire(_det(_wire()), 1, (1000, 600), CFG, ctx)
    assert ok.status == "scored" and ok.level in ("low", "medium", "high")
    edge = score_wire(_det(_wire(x0=0, x1=700)), 2, (1000, 600), CFG, ctx)
    assert (edge.status, edge.fail_reason, edge.level) == ("not_assessed", "endpoint_unavailable", None)
    power = score_wire(_det(_wire(), cls="power-distribution-lines"), 3, (1000, 600), CFG, ctx)
    assert (power.status, power.fail_reason, power.crs) == ("not_assessed", "no_validated_reference", None)
    unsure = score_wire(_det(_wire(), status="uncertain"), 4, (1000, 600), CFG, ctx)
    assert (unsure.status, unsure.fail_reason) == ("not_assessed", "class_uncertain")
    assert edge.fit.get("sag_ratio") is not None  # the measurement is kept for audit, not shown as a finding


def test_lowmem_inference_matches_fused_forward():
    torch = pytest.importorskip("torch")
    from app.pipeline.wirenet import _fused_module, _probs_lowmem
    torch.manual_seed(0)
    nets = [torch.nn.Conv2d(3, 5, 3, padding=1).eval() for _ in range(3)]
    fused = _fused_module([(n, w) for n, w in zip(nets, (0.5, 0.3, 0.2), strict=True)]).eval()
    x = np.random.default_rng(0).standard_normal((2, 3, 32, 64)).astype(np.float32)
    with torch.no_grad():
        ref = fused(torch.from_numpy(x)).softmax(1).numpy()
    got = _probs_lowmem(fused, x, "cpu")
    assert np.abs(got - ref).max() < 1e-5 and (got.argmax(1) == ref.argmax(1)).all()


@pytest.mark.test_reference
def test_mostly_unassessed_pole_is_inconclusive_not_safe():
    from app.pipeline.hazard import INCONCLUSIVE, NOT, assess
    m = np.zeros((50, 50), bool)
    row = lambda i, st, lv=None, fl=(): {"index": i, "class": "telecom-cable", "mask": m, "level": lv, "status": st,  # noqa: E731
                                         "flags": list(fl)}
    few = [row(1, "scored", "low")] + [row(i, "not_assessed", fl=["endpoint_unavailable"]) for i in range(2, 6)]
    hz = assess(few, [], (50, 50), rule=CFG.hazard)
    assert hz.verdict == INCONCLUSIVE and "1 of 5" in hz.reasons[0] and "not low risk" in hz.reasons[0]
    most = [row(i, "scored", "low") for i in range(1, 5)] + [row(5, "not_assessed", fl=["no_reference_for_class"])]
    hz = assess(most, [], (50, 50), rule=CFG.hazard)
    assert hz.verdict == NOT and any("1 measured span not assessed" in r for r in hz.reasons)


@pytest.mark.test_reference
def test_partial_span_with_visible_sag_keeps_the_pole_under_review():
    from app.pipeline.hazard import INCONCLUSIVE, assess
    from app.pipeline.runner import score_wire
    from app.pipeline.sag_limits import EngineeringContext
    edge = score_wire(_det(_wire(x0=0, x1=700, sag=60)), 1, (1000, 600), CFG, EngineeringContext())
    assert edge.status == "not_assessed" and edge.fit["unverified_level"] in ("medium", "high") and edge.level is None
    m = np.zeros((50, 50), bool)
    rows = [{"index": i, "class": "telecom-cable", "mask": m, "level": "low", "status": "scored", "flags": []} for i in range(2, 6)]
    rows.append({"index": 1, "class": "telecom-cable", "mask": m, "level": None, "status": "not_assessed",
                 "flags": ["endpoint_unavailable"], "unverified_level": edge.fit["unverified_level"]})
    hz = assess(rows, [], (50, 50), rule=CFG.hazard)
    assert hz.verdict == INCONCLUSIVE and "span 1 run off the photo" in hz.reasons[0]
