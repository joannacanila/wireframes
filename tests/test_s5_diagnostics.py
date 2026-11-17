"""S5 diagnostic restoration (18 Nov 2026).

S4 removed the unsupported thresholds correctly, but every renderer showed, drew and selected only *assessed* wires,
so with no validated reference the measured curve, chord, sag point and ratio of every wire disappeared from the
result page, the PDF figure and the admin sheet. These tests pin the restored behaviour: every measured wire is
explained (app/services/diagnostics.py) and drawn, a measurement is never a severity, a missing reference is never
reported as a photo problem, and historical levels from the withdrawn placeholder are never counted.
"""

from __future__ import annotations

import io
import re

import numpy as np
import pytest

from app.models.orm import Analysis
from app.services import records
from app.services.diagnostics import measurement_state, pole_diagnosis, wire_diagnosis
from tests.conftest import upload, wait_done

SEVERITY_WORDS = re.compile(r"\b(critical|minor sag|within reference|safe|unsafe|low risk|high risk|at risk|"
                            r"violation|non-?compliant|compliant)\b", re.I)


def _w(**kw):
    base = {"index": 1, "class": "telecom-cable", "class_status": "confident", "status": "not_assessed",
            "reason_code": "no_validated_reference", "sag_ratio": 0.031, "sag_ratio_resolution": 0.002,
            "flags": ["no_validated_reference"], "fit": {"endpoints": [[0, 0], [500, 0]]},
            "provenance": {"centerline_inferred_share": 0.0}}
    return {**base, **kw}


# ----------------------------------------------------------------------------- per-wire diagnosis
def test_measured_wire_is_explained_without_any_severity(shipped_cfg):
    d = wire_diagnosis(_w(), shipped_cfg)
    text = " ".join(x["text"] for x in d["lines"])
    assert d["state"] == "measured" and d["assessed"] is False and d["resolvable"] is True
    assert "Image-derived sag ratio 3.1% ± 0.2 points" in text and "chord 500 px" in text
    assert "Physical sag not established" in text
    assert "no validated sag reference exists for a telecom / fibre cable" in text
    assert not SEVERITY_WORDS.search(text), text
    assert any("sag-tension" in n for n in d["needed"])  # what would complete it: cable data, span, validation


def test_ratio_below_its_resolution_is_straight_within_resolution_not_a_small_sag(shipped_cfg):
    d = wire_diagnosis(_w(sag_ratio=0.0005, sag_ratio_resolution=0.004), shipped_cfg)
    assert d["resolvable"] is False
    assert any(x["key"] == "resolution" and "straight to within the 1 pixel resolution" in x["text"] for x in d["lines"])


def test_partial_span_keeps_its_visible_part_measurement(shipped_cfg):
    d = wire_diagnosis(_w(reason_code="endpoint_unavailable", flags=["truncated", "endpoint_unavailable"]), shipped_cfg)
    text = " ".join(x["text"] for x in d["lines"])
    assert d["state"] == "measured_partial" and "visible part only" in text and "3.1%" in text
    assert "A photo with both supporting poles of this span in the frame" in d["needed"]


@pytest.mark.parametrize("code, needed", [("residual_too_large", "separates"), ("chord_below_precision_gate", "closer"),
                                          ("empty_mask", "better light")])
def test_unmeasured_wire_says_why_and_what_photo_would_help(shipped_cfg, code, needed):
    d = wire_diagnosis(_w(status="fit_failed", reason_code=code, sag_ratio=None, fit={}), shipped_cfg)
    assert d["state"] == "not_measured"
    assert any(x["key"] == "measurement" and x["text"].startswith("Not measured:") for x in d["lines"])
    assert any(needed in n for n in d["needed"])


def test_uncertain_type_claims_no_type_and_no_reference(shipped_cfg):
    d = wire_diagnosis(_w(class_status="uncertain", reason_code="class_uncertain"), shipped_cfg)
    text = " ".join(x["text"] for x in d["lines"])
    assert "no type is claimed" in text and "type is uncertain" in text and "telecom / fibre" not in text
    assert any("Confirmation of the wire type" in n for n in d["needed"])


def test_vegetation_is_an_image_observation_never_contact(shipped_cfg):
    d = wire_diagnosis(_w(veg_clearance={"gap_px": 0}), shipped_cfg)
    t = next(x["text"] for x in d["lines"] if x["key"] == "vegetation")
    assert "in the image" in t and "does not show contact" in t
    d = wire_diagnosis(_w(veg_clearance={"gap_px": 12}), shipped_cfg)
    assert "12 px (an image distance, not a clearance)" in next(x["text"] for x in d["lines"] if x["key"] == "vegetation")


def test_assessed_wire_under_a_test_reference_is_reported_as_assessed(shipped_cfg):
    from tests.conftest import with_test_reference
    cfg = with_test_reference(shipped_cfg)
    d = wire_diagnosis(_w(status="scored", reason_code=None, level="medium", delta_lim=0.04), cfg)
    assert d["assessed"] is True and not d["needed"]


# ----------------------------------------------------------------------------- per-photo diagnosis
def test_pole_diagnosis_counts_and_does_not_blame_the_photo_for_a_missing_reference(shipped_cfg):
    r = {"status": "complete", "wires": [_w(index=1), _w(index=2, sag_ratio=0.0001)]}
    d = pole_diagnosis(r, shipped_cfg)
    assert d["stage"] == "measured_not_assessed"
    assert d["counts"] == {"detected": 2, "measured": 2, "measured_partial": 0, "not_measured": 0,
                           "not_applicable": 0, "assessed": 0, "within_resolution": 1}
    assert d["retake_helps"] is False  # every wire fully measured: only a reference is missing
    assert "2 of 2 detected wires were measured" in d["why"] and "not a low-risk result" in d["why"]
    assert any("Sag severity" in u for u in d["unavailable"]) and any("Physical sag" in u for u in d["unavailable"])


def test_processing_failure_is_never_an_assessment_limitation(shipped_cfg):
    d = pole_diagnosis({"status": "error", "wires": []}, shipped_cfg)
    assert d["stage"] == "failed" and "processing failure, not a finding" in d["why"] and not d["unavailable"]


def test_no_wire_detected_is_not_reported_as_no_hazard(shipped_cfg):
    d = pole_diagnosis({"status": "no_wires_detected", "wires": []}, shipped_cfg)
    assert d["stage"] == "no_wires" and "does not mean there are no wires or no hazard" in d["why"]


def test_measurement_state_of_a_drop_wire_is_not_applicable():
    assert measurement_state({"status": "service_drop", "sag_ratio": None}) == "not_applicable"


# ----------------------------------------------------------------------------- end to end (shipped configuration)
def _complete(client, scene):
    aid = upload(client, scene.rgb, scene.labels)
    assert wait_done(client, aid)["status"] == "complete"
    return aid


def test_result_carries_the_diagnosis_for_staff_and_public(client, anon, scene):
    aid = _complete(client, scene)
    for c in (client, anon):
        r = c.get(f"/api/analysis/{aid}/result").json()
        assert r["diagnosis"]["stage"] == "measured_not_assessed" and r["diagnosis"]["why"]
        measured = [w for w in r["wires"] if w["sag_ratio"] is not None]
        assert measured and all(w["diagnosis"]["state"] in ("measured", "measured_partial") for w in measured)
        for w in r["wires"]:
            assert not SEVERITY_WORDS.search(" ".join(x["text"] for x in w["diagnosis"]["lines"]))
            assert "confidence" not in " ".join(x["text"] for x in w["diagnosis"]["lines"])  # no model internals
            if w["sag_ratio"] is not None:  # the drawing geometry stays available to the page
                assert w["fit"].get("endpoints") and w["catenary_curve"]


def test_records_report_measured_not_assessed(client, scene):
    aid = _complete(client, scene)
    with client.state.db.session() as s:
        a = s.get(Analysis, aid)
        assert records.completeness(a) == "measured" and records.COMPLETENESS_LABEL["measured"]
        c = records.wire_counts(a)
        assert c["measured"] > 0 and c["high"] == c["medium"] == c["low"] == 0


def test_historical_levels_are_never_counted_or_shown_as_severities(client, scene, shipped_cfg):
    """A pre-S4 record (no assessment_basis): its stored levels came from the withdrawn placeholder. They are not
    counted in the admin list or by the assistant, and stay withdrawn even if a reference is configured later."""
    from app.assistant.service import facts_summary, inspection_facts
    from app.services.basis import level_withdrawn
    from tests.conftest import with_test_reference
    aid = _complete(client, scene)
    with client.state.db.session() as s:
        a = s.get(Analysis, aid)
        d = a.detections[0]
        d.status, d.level, d.crs, d.delta_lim = "scored", "high", 10.0, 0.04
        a.metadata_json = {k: v for k, v in a.metadata_json.items() if k != "assessment_basis"}
    with client.state.db.session() as s:
        a = s.get(Analysis, aid)
        assert records.wire_counts(a)["high"] == 0
        assert "high risk" not in facts_summary(inspection_facts(a)).replace("0 high risk", "")
        assert level_withdrawn("high", "telecom-cable", with_test_reference(shipped_cfg), a.metadata_json)
        assert not level_withdrawn("high", "telecom-cable", with_test_reference(shipped_cfg),
                                   {"assessment_basis": "S4-2026-11-18"})


def test_assistant_reports_measurements_and_image_overlap_wording(client, scene):
    from app.assistant.service import facts_summary, inspection_facts
    aid = _complete(client, scene)
    with client.state.db.session() as s:
        f = inspection_facts(s.get(Analysis, aid))
    assert f["public"]["wire_sections"]["measured_in_image"] > 0
    assert "measured in the image" in facts_summary(f) and "touches" not in facts_summary(f)


def test_pdf_draws_measured_wires_and_explains_the_analysis(client, scene):
    import pypdfium2 as pdfium
    aid = _complete(client, scene)
    pdf = client.get(f"/api/reports/{aid}/pdf")
    assert pdf.status_code == 200
    doc = pdfium.PdfDocument(io.BytesIO(pdf.content))
    text = "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    assert "Figure 1. Measured wires (not assessed)" in text and "No wire was flagged, so none is drawn" not in text
    assert "4.1 What the Analysis Found" in text and "Not available from one photo" in text
    assert "Observations" in text  # per-wire observation column
    assert not re.search(r"\b(Critical|Minor sag)\b", text.split("Important Notice")[0])


def test_overlay_draws_the_chord_of_a_measured_unassessed_wire():
    from app.services.render import render_overlay
    rgb = np.full((200, 400, 3), 30, np.uint8)
    w = {"index": 1, "level": None, "status": "not_assessed", "sag_ratio": 0.05, "mask_polygons": [],
         "catenary_curve": [[50, 60], [200, 90], [350, 60]],
         "fit": {"endpoints": [[50, 60], [350, 60]], "sag_point": [200, 90], "chord_foot": [200, 60]}, "bbox": [50, 60, 350, 90]}
    drawn = render_overlay(rgb, [w], max_side=400)
    unmeasured = render_overlay(rgb, [{**w, "sag_ratio": None}], max_side=400)
    # the dashed chord and the sag segment are drawn only for the measured wire
    assert int(np.abs(drawn.astype(int) - unmeasured.astype(int)).sum()) > 0


def test_result_page_scripts_draw_and_list_measured_wires():
    """The page logic that hid every measurement after S4 (attention-only view, scored-only drawing). S6 restored the
    v2.18 page: the Wires view draws every wire (no visibility filter) and the table lists every wire section."""
    from app.core.config import ROOT_DIR
    viewer = (ROOT_DIR / "app/web/assets/viewer.js").read_text()
    field = (ROOT_DIR / "app/web/assets/field.js").read_text()
    assert "export const isMeasured" in viewer and "const scored = isMeasured(w);" in viewer
    assert "L.sag && isMeasured(w) && f.sag_point" in viewer
    assert "setVisible(" not in field  # nothing is hidden by severity or assessment
    assert "const first = tbl.rows.find((w) => (w.status === \"scored\" ? w.level !== \"low\" : isMeasured(w)));" in field
    assert "These wires were not measured, so nothing is known about their sag" not in field
    assert "${diagHtml(r)}" in field
