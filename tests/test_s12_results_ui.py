"""S12 (register D-28): the approved results page, the three-zone meter with explicit boundaries, and one set of states
for every view.

The meter's boundaries come from ``comparison_reference`` only. The evidence supports one boundary (the provisional 3 %
reference) and no engineering tolerance, so both boundaries are 3 % and the amber region is the wire's own measurement
uncertainty; a validated tolerance would be configured, never hardcoded. The page functions are run as written (Node).
"""

from __future__ import annotations

import io

import pytest
from pydantic import ValidationError

from app.core.config import ROOT_DIR, ComparisonReference
from app.services.comparison import compare_wire, counts_text, pole_comparison, reference_info, wire_states, zone_note
from app.services.methodology import summary_lines, topics
from tests.conftest import upload, wait_done

FIELD = ROOT_DIR / "app/web/assets/field.js"
CSS = ROOT_DIR / "app/web/assets/field.css"


def _cfg(base, **ref):
    """The shipped configuration with a different comparison_reference (validated like the YAML)."""
    data = base.comparison_reference.model_dump() | ref
    return base.model_copy(update={"comparison_reference": ComparisonReference(**data)})


def _w(ratio=0.031, res=0.002, cls="power-distribution-lines", **kw):
    return {"index": 4, "class": cls, "class_status": "confident", "status": "not_assessed",
            "reason_code": "no_validated_reference", "sag_ratio": ratio, "sag_ratio_resolution": res,
            "fit": {"endpoints": [[0, 0], [500, 0]]}, **kw}


# ----------------------------------------------------------------------------- configuration: one source, honest bounds

def _no_telecom_ref(cfg):
    """The shipped configuration with the S15 telecom reference switched off: the no-reference path."""
    return cfg.model_copy(update={"telecom_reference": cfg.telecom_reference.model_copy(update={"enabled": False})})

def test_shipped_boundaries_are_the_reference_itself_with_no_invented_tolerance(s16_cfg):
    ref = reference_info(s16_cfg)
    assert ref["sag_ratio"] == ref["lower_boundary"] == ref["upper_boundary"] == 0.03
    assert ref["status"] == "provisional" and ref["classification_enabled"] is False and ref["uncertainty"] == "resolution"
    assert ref["unit"] and ref["source"] and ref["status_note"] and ref["effective_date"] and ref["tolerance_basis"].startswith("none")
    assert ref["meter_scale_max"] == 0.10
    note = zone_note(ref)
    assert "no engineering tolerance or upper limit is validated" in note and "not severity classes" in note
    yaml = (ROOT_DIR / "config/wireframe.yaml").read_text()
    block = yaml[yaml.index("comparison_reference:"):yaml.index("\nhazard:")]
    assert "0.024" not in block and "0.04 " not in block and "2.4" not in block.replace("2.4 %", "")  # withdrawn bands


@pytest.mark.parametrize("bad", [
    {"lower_boundary": 0.035},                                  # lower above the reference
    {"upper_boundary": 0.025},                                  # upper below the reference
    {"classification_enabled": True},                           # a severity class from a provisional reference
    {"meter_scale_max": 0.03},                                  # the scale must show the red zone
])
def test_configuration_rejects_boundaries_the_evidence_does_not_support(s16_cfg, bad):
    with pytest.raises(ValidationError):
        _cfg(s16_cfg, **bad)


# ----------------------------------------------------------------------------- applicability
def test_only_power_lines_of_a_known_type_are_compared_by_default(s16_cfg):
    assert compare_wire(_w(), s16_cfg)["position"]
    # S15 (D-31): telecom now has its own reference (the telecom path is covered with it switched off)
    noref = _no_telecom_ref(s16_cfg)
    w = _w(cls="telecom-cable")
    assert compare_wire(w, s16_cfg)["reference_id"] == "telecom" and compare_wire(w, noref) is None
    assert wire_states(w, noref, None)["reference_unavailable_reason"] == "no_reference_for_telecom"
    for w, why in ((_w(cls="abandoned-wire"), "no_reference_for_abandoned"),
                   (_w(class_status="uncertain"), "type_uncertain"), (_w(status="service_drop"), "drop_wire")):
        assert compare_wire(w, s16_cfg) is None
        s = wire_states(w, s16_cfg, None)
        assert s["reference_available"] is False and s["reference_unavailable_reason"] == why and s["comparison"] is None
        assert s["assessed"] is False and s["classification_available"] is False


def test_an_explicitly_configured_alternative_reference_is_applied_everywhere_it_is_configured(s16_cfg):
    alt = _cfg(s16_cfg, sag_ratio=0.015, lower_boundary=0.012, upper_boundary=0.018, status="manufacturer",
               classes=["telecom-cable"], label="1.5% installation sag for ADSS cable (manufacturer)",
               tolerance_basis="manufacturer installation tolerance")
    tel, pow_ = _w(0.016, 0.001, cls="telecom-cable"), _w(0.016, 0.001)
    c = compare_wire(tel, alt)
    assert c["position"] == "at" and c["zones"]["lower"] == 0.012 and c["zones"]["upper"] == 0.018
    assert compare_wire(pow_, alt) is None  # the power line is no longer covered
    assert wire_states(pow_, alt, None)["reference_unavailable_reason"] == "no_reference_for_class"
    note = zone_note(reference_info(alt))
    assert "from 1.2% to 1.8%" in note and "manufacturer installation tolerance" in note


# ----------------------------------------------------------------------------- boundaries and uncertainty
@pytest.mark.parametrize(("ratio", "res", "pos", "overlap"), [
    (0.020, 0.002, "below", False),   # clearly below
    (0.028, 0.002, "at", True),       # ratio + uncertainty reaches the reference exactly: cannot be told apart
    (0.030, 0.002, "at", True),       # on the reference
    (0.032, 0.002, "at", True),       # ratio - uncertainty reaches the reference exactly
    (0.040, 0.002, "above", False),   # clearly above
    (0.029, 0.0, "below", False),     # no uncertainty: the point estimate alone
    (0.030, 0.0, "at", False),        # exactly at the boundary with no band: "at", no overlap to report
])
def test_position_uses_the_configured_boundaries_and_the_wire_uncertainty(s16_cfg, ratio, res, pos, overlap):
    c = compare_wire(_w(ratio, res), s16_cfg)
    assert c["position"] == pos and c["uncertainty_overlaps"] is overlap
    assert c["difference_pp"] == pytest.approx((ratio - 0.03) * 100)


def test_a_validated_tolerance_region_widens_amber_and_overlap_is_reported_per_boundary(s16_cfg):
    # S17 (D-37): a classifying reference places the wire by its point estimate (bounds inclusive); the band is
    # reported (uncertainty_overlaps) and never moves the class
    tol = _cfg(s16_cfg, lower_boundary=0.025, upper_boundary=0.035, status="validated",
               tolerance_basis="test tolerance", classification_enabled=True,
               classifications={"below": "B", "at": "A", "above": "X"})
    assert compare_wire(_w(0.030, 0.001), tol)["position"] == "at"               # within the tolerance
    assert compare_wire(_w(0.030, 0.001), tol)["uncertainty_overlaps"] is False  # band inside the region
    at_upper = compare_wire(_w(0.035, 0.001), tol)
    assert at_upper["position"] == "at" and at_upper["uncertainty_overlaps"] is True
    over = compare_wire(_w(0.0351, 0.001), tol)  # band reaches back inside, the value is over: exceeded
    assert over["position"] == "above" and over["classification"] == "X" and over["uncertainty_overlaps"] is True
    assert compare_wire(_w(0.0365, 0.001), tol)["position"] == "above"
    assert compare_wire(_w(0.0249, 0.001), tol)["position"] == "below"
    assert wire_states(_w(), tol, compare_wire(_w(), tol))["classification_available"] is True
    assert compare_wire(_w(0.03, 0.001), _cfg(s16_cfg, uncertainty="none"))["uncertainty"] is None


def test_missing_measurement_or_resolution_never_becomes_a_zero(s16_cfg):
    c = compare_wire(_w(None, None, status="fit_failed", reason_code="residual_too_large"), s16_cfg)
    assert c["position"] is None and c["difference_pp"] is None and c["zones"] is None
    s = wire_states(_w(None, None, status="fit_failed"), s16_cfg, c)
    assert s["measurement"] == "unmeasured" and s["coverage"] is None and s["within_resolution"] is False
    c2 = compare_wire(_w(0.02, None), s16_cfg)
    assert c2["uncertainty"] is None and c2["uncertainty_overlaps"] is False and c2["position"] == "below"
    part = wire_states(_w(0.01, 0.02, reason_code="endpoint_unavailable"), s16_cfg, None)
    assert part["coverage"] == "edge_clipped" and part["within_resolution"] is True


# S18: the Field Screener's separate technical view (and the Node tests of its meter, card and status
# functions) was removed at the owner's request; the shared result view's meter is tested in test_s14_ux.py.


def test_results_page_reading_order_and_single_methodology_section():
    js = FIELD.read_text()
    page = js[js.index("function renderResult(r) {"):js.index("const root = stages.result;")]
    # S18 (Part 2): What we know -> What you should do next -> How did we know?, from the shared result view. The page
    # builds section B (next steps, contacts, one emergency line) and the folded methodology, then renders the header
    # actions, the guard banner, the shared view (A, B, C) and the notice, in that order.
    # S18 (Phase 11): the folded "How this result was produced" section was removed from the citizen page and is not
    # replaced by another technical explanation.
    order = ['class="rv-next" id="next"', 'class="rv-steps"', 'id="contacts"', 'class="rv-emerg"',
             'class="rv-top"', 'id="pdf"', 'id="copy-msg"',
             "${guardBanner(r)}", 'resultViewHtml(r, { mode: "citizen", nextHtml: next })',
             "${NOTICE_FOOTER}"]
    pos = [page.index(x) for x in order]
    assert pos == sorted(pos), dict(zip(order, pos, strict=True))
    assert "How this result was produced" not in page and "rv-method" not in page and "methodology(" not in page
    rv = (FIELD.parent / "result-view.js").read_text()
    assert '${knowHtml(r, o)}${o.nextHtml || ""}' in rv and "howHtml(r, o)}</div>`" in rv  # A, then B, then C
    assert page.count("rv-emerg") == 1 and page.count("tel:911") == 1  # one emergency instruction
    for staff in ("Not yet reviewed", "Reviewed by", "staff review", "czDiagnosis", "rx-diag"):
        assert staff not in page, staff
    # no metric tiles, no engineering controls and no wire table on the citizen page; they stay in the staff view
    for tech in ('class="rx-kpi"', 'id="adv"', 'data-view="mask"', 'id="opacity"', 'id="inventory"', "measureDetails(",
                 "meterRef(", "statusPills("):
        assert tech not in page, tech
    # S18: no separate technical view in the Field Screener, for anyone (staff details are on the admin inspection page)
    assert "renderTechnical" not in js and "view=technical" not in js and "Technical view" not in js
    # S17: the notice markup exists once (NOTICE_FOOTER), at the foot of each view, never inside a collapsed section
    assert "<wf-notice" not in page and js.count('<wf-notice variant="full">') == 1
    assert js.count("${NOTICE_FOOTER}") == 1
    for gone in ("rx-phototip", "rx-retake", "rx-callouts", "rx-guide", "rx-limits", "Photo limitations</h2>"):
        assert gone not in page, gone
    # S14: no sag-percentage browse filter (it read like a threshold); the secondary filters are categorical only
    assert "BROWSE_SAG" not in js and "Notable sag" not in js
    # the citizen safety caveats come from the server (plain.py), not from page code
    plain = (FIELD.parent.parent.parent / "services" / "plain.py").read_text()
    assert "This does not mean the location is electrically safe" in plain and "not a safety inspection" in plain
    css = CSS.read_text()
    s12 = css[css.index("S12 results page"):]
    assert "gradient(" not in s12 and "box-shadow: 0 12px" not in s12


def test_methodology_covers_the_eleven_topics_from_the_configured_reference(s16_cfg):
    t = topics(pole_comparison({"wires": []}, s16_cfg))
    assert [x["title"] for x in t] == ["Image sag ratio", "Chord", "Curve fit", "Incomplete visibility", "Interpolation",
                                       "Uncertainty", "Image ratio is not physical sag", "Limits of one 2D photo",
                                       "Reference used", "Wire types without a reference", "Model limitations"]
    ref = t[8]["text"]
    assert "3.0%" in ref and "provisional" in ref and "ERB Res. 95-21" in ref and "upper boundary" in ref
    assert "no engineering tolerance or upper limit is validated" in ref
    assert topics(None)[8]["text"] == "No sag reference is configured, so no wire is compared."


# ----------------------------------------------------------------------------- one set of facts in every output
def test_the_public_result_carries_the_states_and_the_shared_text(client, scene):
    """The signed-out Field Screener gets the result through public_view(): the S12 fields must survive it."""
    aid = upload(client, scene.rgb, scene.labels)
    assert wait_done(client, aid)["status"] == "complete"
    from app.services.analysis import public_view
    r = public_view(client.get(f"/api/analysis/{aid}/result").json())  # the client fixture is signed in
    assert r.get("public") is True
    assert r["summary_lines"] and len(r["methodology"]) == 11 and all("states" in w for w in r["wires"])
    assert "crs" not in r["wires"][0] and "config" not in r  # still no scores or internals


def test_api_csv_pdf_and_assistant_agree(client, scene, s16_cfg):
    import pypdfium2 as pdfium
    aid = upload(client, scene.rgb, scene.labels)
    assert wait_done(client, aid)["status"] == "complete"
    r = client.get(f"/api/analysis/{aid}/result").json()
    dc, pc = r["diagnosis"]["counts"], r["comparison"]
    assert pc["lower_boundary"] == 0.02 and pc["upper_boundary"] == 0.03 and pc["zone_note"]  # S17 bands
    for w in r["wires"]:
        s, c = w["states"], w["comparison"]
        assert s["comparison"] == (c["position"] if c else None)
        assert s["reference_available"] == (c is not None)
        assert s["assessed"] is False  # the legacy CRS level stays withdrawn
        assert s["classification_available"] == (c is not None)
        assert (c["classification"] is not None) == bool(c and c["position"]) if c else True
        if s["coverage"] == "edge_clipped":
            assert w["reason_code"] == "endpoint_unavailable"
    assert sum(w["states"]["coverage"] == "full_span" for w in r["wires"]) == dc["measured"]
    assert sum(w["states"]["coverage"] == "edge_clipped" for w in r["wires"]) == dc["measured_partial"]
    assert sum(sum(x["counts"].values()) for x in pc["by_reference"]) == sum(1 for w in r["wires"] if w["comparison"])
    assert r["summary_lines"] == summary_lines(r) and f"{dc['detected']} wire" in r["summary_lines"][0]
    assert r["methodology"] == topics(pc)
    csv = client.get(f"/api/admin/inspections/{aid}/findings.csv").text
    assert "Coverage,Reference applicability,Assessment" in csv
    pdf = client.get(f"/api/reports/{aid}/pdf")
    doc = pdfium.PdfDocument(io.BytesIO(pdf.content))
    text = " ".join(" ".join(doc[i].get_textpage().get_text_range() for i in range(len(doc))).split())
    assert r["summary_lines"][0] in text and "6.0 Limitations and Methodology" in text
    assert text.index("2.0 What To Do Next") < text.index("3.0 Photograph")
    for t in r["methodology"]:
        assert t["title"] in text, t["title"]
    assert "Keep a safe distance" in text
    # S17: the counts name each canonical class (the S16 "reference-tolerance region" no longer exists)
    assert ("within reference threshold" in counts_text(pc) and "reference threshold exceeded" in counts_text(pc))\
        or "so none is compared" in counts_text(pc)
