"""S17 (register D-37): the canonical sag classification and the pole-level aggregation.

* Power lines: three bands from ERB Res. 95-21 s.19 (3 %, regulatory) and a 2 % descriptive subdivision. The middle
  band is inclusive at both ends: exactly 3.00 % is within the reference, anything greater exceeds it.
* Telecom / fiber keep their own 2 % / 5 % typical-range bands; neither reference is ever applied to the other type.
* Classification uses the unrounded ratio, never a rounded display string.
* CRS is 250 x the ratio (3 % = 7.5, 2 % = 5.0) and never decides a class.
* A pole with any exceeding span is "exceeded" whatever else is unavailable; an unavailable span only ever makes a
  clean result incomplete.
"""

from __future__ import annotations

import pytest

from app.core.config import ROOT_DIR
from app.services.comparison import classify, compare_wire, crs_index, reference_info, wire_states
from app.services.outcome import DISCLAIMER, attach, pole_assessment
from tests.test_s12_results_ui import _w

POWER, TEL = "power-distribution-lines", "telecom-cable"


def _wire(cfg, index, ratio, cls=POWER, **kw):
    w = _w(ratio=ratio, cls=cls, **kw)
    w["index"] = index
    w["comparison"] = compare_wire(w, cfg)
    w["states"] = wire_states(w, cfg, w["comparison"])
    return w


# ----------------------------------------------------------------------------- boundary tables (Part 2.6)
POWER_CASES = [(0.0120, "WITHIN_LOWER_REFERENCE_RANGE"), (0.0200, "WITHIN_REFERENCE_THRESHOLD"),
               (0.0295, "WITHIN_REFERENCE_THRESHOLD"), (0.0300, "WITHIN_REFERENCE_THRESHOLD"),
               (0.0301, "REFERENCE_THRESHOLD_EXCEEDED"), (0.0450, "REFERENCE_THRESHOLD_EXCEEDED"),
               (0.0500, "REFERENCE_THRESHOLD_EXCEEDED")]
TELECOM_CASES = [(0.0120, "BELOW_TYPICAL_RANGE"), (0.0200, "WITHIN_TYPICAL_RANGE"), (0.0295, "WITHIN_TYPICAL_RANGE"),
                 (0.0300, "WITHIN_TYPICAL_RANGE"), (0.0301, "WITHIN_TYPICAL_RANGE"), (0.0450, "WITHIN_TYPICAL_RANGE"),
                 (0.0500, "WITHIN_TYPICAL_RANGE"), (0.0501, "ABOVE_TYPICAL_RANGE")]


@pytest.mark.parametrize("ratio,code", POWER_CASES)
def test_power_bands_at_every_boundary(shipped_cfg, ratio, code):
    c = _wire(shipped_cfg, 1, ratio)["comparison"]
    assert c["classification"] == code and c["reference_id"] == "power" and c["reference_version"]


@pytest.mark.parametrize("ratio,code", TELECOM_CASES)
def test_telecom_bands_at_every_boundary(shipped_cfg, ratio, code):
    c = _wire(shipped_cfg, 1, ratio, cls=TEL)["comparison"]
    assert c["classification"] == code and c["reference_id"] == "telecom"


def test_the_two_references_never_cross(shipped_cfg):
    # 4.5 %: power exceeds its 3 % reference; the same ratio is inside telecom's typical range
    assert _wire(shipped_cfg, 1, 0.045)["comparison"]["position"] == "above"
    assert _wire(shipped_cfg, 1, 0.045, cls=TEL)["comparison"]["position"] == "at"
    assert reference_info(shipped_cfg, POWER)["upper_boundary"] == 0.03
    assert reference_info(shipped_cfg, TEL)["upper_boundary"] == 0.05
    assert reference_info(shipped_cfg, "abandoned-wire") is None


def test_classification_uses_the_unrounded_ratio(shipped_cfg):
    # 3.004 % displays as "3.0%" but exceeds 3 %; 2.9996 % displays as "3.0%" and is within
    over, under = _wire(shipped_cfg, 1, 0.03004), _wire(shipped_cfg, 2, 0.029996)
    assert over["comparison"]["classification"] == "REFERENCE_THRESHOLD_EXCEEDED"
    assert under["comparison"]["classification"] == "WITHIN_REFERENCE_THRESHOLD"
    assert f"{0.03004 * 100:.1f}" == f"{0.029996 * 100:.1f}" == "3.0"
    assert classify(0.019999999, 0.02, 0.03) == "below" and classify(0.02, 0.02, 0.03) == "at"


def test_uncertainty_is_reported_but_never_moves_the_class(shipped_cfg):
    c = _wire(shipped_cfg, 1, 0.031, res=0.002, sag_ratio_uncertainty=0.01)["comparison"]
    assert c["classification"] == "REFERENCE_THRESHOLD_EXCEEDED" and c["uncertainty_overlaps"] and c["uncertainty"] == 0.01


@pytest.mark.parametrize("ratio,crs", [(0.012, 3.0), (0.02, 5.0), (0.03, 7.5), (0.045, 10.0), (0.05, 10.0)])
def test_crs_is_250_times_the_ratio_capped_at_10(shipped_cfg, ratio, crs):
    assert crs_index(ratio) == pytest.approx(crs)
    assert _wire(shipped_cfg, 1, ratio)["comparison"]["crs_index"] == pytest.approx(crs)
    assert _wire(shipped_cfg, 1, ratio, cls=TEL)["comparison"]["crs_index"] is None  # power only


def test_reference_carries_its_provenance(shipped_cfg):
    ref = reference_info(shipped_cfg, POWER)
    assert ref["issuer"] == "Energy Regulatory Board" and ref["published"] == "1995-08-03"
    assert "Section 19" in ref["section"] and ref["source_url"].endswith("/72764")
    assert "descriptive" in ref["boundary_basis"]["lower"] and "regulatory" in ref["boundary_basis"]["upper"]
    assert ref["classification_enabled"] and ref["status"] == "regulatory"


def test_no_threshold_number_lives_in_the_frontend():
    import re
    pat = re.compile(r"(sag_ratio|ratio|\.ratio)\s*[<>]=?\s*(0?\.0[2-5]|[2-5](\.0+)?\b)")
    for f in ("field.js", "admin.js", "findings.js", "admin-map.js", "viewer.js"):
        js = (ROOT_DIR / "app/web/assets" / f).read_text()
        assert not pat.search(js), (f, pat.search(js))
        if f != "viewer.js":  # viewer's 0.02 is its minimum zoom scale
            assert not re.search(r"\b0\.0[235]\b", js), f


# ----------------------------------------------------------------------------- pole aggregation (Part 2.7)
def _pole(cfg, ratios, cls=POWER):
    ws = []
    for i, r in enumerate(ratios, 1):
        if r is None:
            w = _w(ratio=None, cls=cls, reason_code="residual_too_large", status="fit_failed")
            w["index"] = i
            w["comparison"] = compare_wire(w, cfg)
            w["states"] = wire_states(w, cfg, w["comparison"])
            ws.append(w)
        else:
            ws.append(_wire(cfg, i, r, cls=cls))
    return ws


def test_an_unavailable_span_never_erases_an_exceedance(shipped_cfg):
    pa = pole_assessment(_pole(shipped_cfg, [0.015, 0.022, 0.038, None]))
    assert pa["code"] == "REFERENCE_THRESHOLD_EXCEEDED" and pa["exceeded"] == [3]
    assert [u["index"] for u in pa["unavailable"]] == [4] and not pa["complete"]


def test_unavailable_span_without_exceedance_is_incomplete(shipped_cfg):
    pa = pole_assessment(_pole(shipped_cfg, [0.015, 0.022, 0.028, None]))
    assert pa["code"] == "INCOMPLETE_ASSESSMENT" and pa["no_exceedance_in_measured"] and pa["assessed"] == [1, 2, 3]


def test_all_spans_assessed_none_exceeding(shipped_cfg):
    pa = pole_assessment(_pole(shipped_cfg, [0.015, 0.022, 0.03]))
    assert pa["code"] == "NO_THRESHOLD_EXCEEDANCE_DETECTED" and pa["complete"]


def test_no_span_measurable(shipped_cfg):
    assert pole_assessment(_pole(shipped_cfg, [None, None]))["code"] == "MEASUREMENT_UNAVAILABLE"


def test_telecom_above_typical_is_reported_not_an_exceedance(shipped_cfg):
    ws = _pole(shipped_cfg, [0.015]) + [_wire(shipped_cfg, 9, 0.08, cls=TEL)]
    pa = pole_assessment(ws)
    assert pa["code"] == "NO_THRESHOLD_EXCEEDANCE_DETECTED" and pa["above_typical"] == [9]


def test_type_uncertain_is_unavailable_and_abandoned_has_no_reference(shipped_cfg):
    unsure = _wire(shipped_cfg, 1, 0.05, class_status="uncertain")
    aband = _wire(shipped_cfg, 2, 0.05, cls="abandoned-wire")
    assert unsure["comparison"] is None and aband["comparison"] is None
    pa = pole_assessment([unsure, aband, _wire(shipped_cfg, 3, 0.01)])
    assert pa["code"] == "INCOMPLETE_ASSESSMENT" and pa["unavailable"][0]["reason"] == "type_uncertain"
    assert pa["no_reference"] == [2] and pa["applicable_spans"] == 2


def test_result_page_payload_reads_the_aggregation(shipped_cfg):
    r = attach({"status": "complete", "wires": _pole(shipped_cfg, [0.015, 0.022, 0.038, None]), "comparison": None})
    ov = r["overview"]
    assert ov["assessment"] == "reference_threshold_exceeded" and ov["pole"]["exceeded"] == [3]
    assert ov["display"]["attention"] == [3] and ov["counts"]["flagged"] == 1
    cz = ov["citizen"]
    assert "exceeds the 3% sag reference" in cz["title"] and cz["disclaimer"] == DISCLAIMER
    w3 = next(w for w in r["wires"] if w["index"] == 3)["finding"]
    assert w3["classification"] == "REFERENCE_THRESHOLD_EXCEEDED" and "3.8%" in w3["plain"]
    w4 = next(w for w in r["wires"] if w["index"] == 4)["finding"]
    assert w4["span_state"] == "unavailable" and "neighbouring wires" in w4["plain"]


def test_canonical_notice_is_verbatim():
    assert DISCLAIMER == ("WIREFRAME is an image-based decision-support tool designed to help document visible pole and "
                          "wire conditions. Its results are preliminary and may contain errors or limitations. Findings "
                          "should be reviewed by qualified professionals and must not replace an on-site inspection or "
                          "applicable safety assessment.")
