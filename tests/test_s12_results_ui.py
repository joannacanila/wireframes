"""S12 (register D-28): the approved results page, the three-zone meter with explicit boundaries, and one set of states
for every view.

The meter's boundaries come from ``comparison_reference`` only. The evidence supports one boundary (the provisional 3 %
reference) and no engineering tolerance, so both boundaries are 3 % and the amber region is the wire's own measurement
uncertainty; a validated tolerance would be configured, never hardcoded. The page functions are run as written (Node).
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess

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
def test_shipped_boundaries_are_the_reference_itself_with_no_invented_tolerance(shipped_cfg):
    ref = reference_info(shipped_cfg)
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
def test_configuration_rejects_boundaries_the_evidence_does_not_support(shipped_cfg, bad):
    with pytest.raises(ValidationError):
        _cfg(shipped_cfg, **bad)


# ----------------------------------------------------------------------------- applicability
def test_only_power_lines_of_a_known_type_are_compared_by_default(shipped_cfg):
    assert compare_wire(_w(), shipped_cfg)["position"]
    for w, why in ((_w(cls="telecom-cable"), "no_reference_for_telecom"), (_w(cls="abandoned-wire"), "no_reference_for_abandoned"),
                   (_w(class_status="uncertain"), "type_uncertain"), (_w(status="service_drop"), "drop_wire")):
        assert compare_wire(w, shipped_cfg) is None
        s = wire_states(w, shipped_cfg, None)
        assert s["reference_available"] is False and s["reference_unavailable_reason"] == why and s["comparison"] is None
        assert s["assessed"] is False and s["classification_available"] is False


def test_an_explicitly_configured_alternative_reference_is_applied_everywhere_it_is_configured(shipped_cfg):
    alt = _cfg(shipped_cfg, sag_ratio=0.015, lower_boundary=0.012, upper_boundary=0.018, status="manufacturer",
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
def test_position_uses_the_configured_boundaries_and_the_wire_uncertainty(shipped_cfg, ratio, res, pos, overlap):
    c = compare_wire(_w(ratio, res), shipped_cfg)
    assert c["position"] == pos and c["uncertainty_overlaps"] is overlap
    assert c["difference_pp"] == pytest.approx((ratio - 0.03) * 100)


def test_a_validated_tolerance_region_widens_amber_and_overlap_is_reported_per_boundary(shipped_cfg):
    tol = _cfg(shipped_cfg, lower_boundary=0.025, upper_boundary=0.035, status="validated",
               tolerance_basis="test tolerance", classification_enabled=True)
    assert compare_wire(_w(0.030, 0.001), tol)["position"] == "at"               # within the tolerance
    assert compare_wire(_w(0.030, 0.001), tol)["uncertainty_overlaps"] is False  # band inside the region
    at_upper = compare_wire(_w(0.035, 0.001), tol)
    assert at_upper["position"] == "at" and at_upper["uncertainty_overlaps"] is True
    assert compare_wire(_w(0.0365, 0.001), tol)["position"] == "above"
    assert compare_wire(_w(0.0235, 0.001), tol)["position"] == "below"
    assert wire_states(_w(), tol, compare_wire(_w(), tol))["classification_available"] is True
    assert compare_wire(_w(0.03, 0.001), _cfg(shipped_cfg, uncertainty="none"))["uncertainty"] is None


def test_missing_measurement_or_resolution_never_becomes_a_zero(shipped_cfg):
    c = compare_wire(_w(None, None, status="fit_failed", reason_code="residual_too_large"), shipped_cfg)
    assert c["position"] is None and c["difference_pp"] is None and c["zones"] is None
    s = wire_states(_w(None, None, status="fit_failed"), shipped_cfg, c)
    assert s["measurement"] == "unmeasured" and s["coverage"] is None and s["within_resolution"] is False
    c2 = compare_wire(_w(0.02, None), shipped_cfg)
    assert c2["uncertainty"] is None and c2["uncertainty_overlaps"] is False and c2["position"] == "below"
    part = wire_states(_w(0.01, 0.02, reason_code="endpoint_unavailable"), shipped_cfg, None)
    assert part["coverage"] == "edge_clipped" and part["within_resolution"] is True


# ----------------------------------------------------------------------------- the page's own functions (Node)
STUBS = """
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
const classLabel = (w) => ({ "power-distribution-lines": "Power line", "telecom-cable": "Telecom cable" })[w.class] || "Wire";
const wireColor = () => "rgb(1,2,3)"; const spanId = (i) => `SP-${String(i).padStart(3, "0")}`;
const pp = (x) => `${(x * 100).toFixed(1)}%`; const pc = pp;
const measuredText = (w) => `${pc(w.sag_ratio)}${w.sag_ratio_resolution ? ` ±${(w.sag_ratio_resolution * 100).toFixed(1)}` : ""}`;
const isMeasured = (w) => w.sag_ratio != null && (w.status === "scored" || w.status === "not_assessed") && !!w.fit?.endpoints;
const group = (w) => w.level || "none"; const SEVERITY = { high: "Critical", medium: "Minor sag", low: "Within reference", none: "Not assessed" };
const BAND = SEVERITY; const plainFinding = () => "Unable to assess due to overlapping wires";
"""


def _page(expr_cases: list[tuple[str, dict]]) -> list[str]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    src = FIELD.read_text()
    meter = re.search(r"function meter\(w, g\) \{.*?\n\}\n", src, re.S).group(0)
    meters = src[src.index("/** S12 meters"):src.index("/** S5: the wire's badge.")]
    card = src[src.index("// S12: coverage and assessment are separate"):src.index("function renderResult(r) {")]
    js = STUBS + meter + meters + card + f"""
const cases = {json.dumps(expr_cases)};
console.log(JSON.stringify(cases.map(([f, a]) => ({{ ref: () => meterRef(a.w, a.pc), type: () => meterType(a.w, a.pc), empty: () => meterEmpty(),
  card: () => wireCard(a.w, a.pc), status: () => statusPills(a.w) }})[f]())));
"""
    return json.loads(subprocess.run([node, "-e", js], capture_output=True, text=True, check=True).stdout)


def _api_wire(cfg, **kw):
    w = _w(**kw)
    w["comparison"] = compare_wire(w, cfg)
    w["states"] = wire_states(w, cfg, w["comparison"])
    return w


def _pc(cfg):
    return pole_comparison({"wires": []}, cfg)


def _left(h, cls):
    return float(re.search(rf'class="{cls}[^"]*" style="left:([0-9.]+)%', h).group(1))


def test_reference_meter_draws_three_proportional_zones_from_the_server(shipped_cfg):
    pc = _pc(shipped_cfg)
    below, at, above = (_api_wire(shipped_cfg, ratio=r, res=0.002) for r in (0.012, 0.03, 0.06))
    hb, ha, hx = _page([("ref", {"w": below, "pc": pc}), ("ref", {"w": at, "pc": pc}), ("ref", {"w": above, "pc": pc})])
    for h in (hb, ha, hx):
        assert re.findall(r'class="z (z-low|z-medium|z-high)"', h) == ["z-low", "z-medium", "z-high"]
        assert "rx-meter-ref" in h and "Reference 3.0%" in h and "0%" in h and "10.0%" in h
        assert "Below reference" in h and "Reference-tolerance region" in h and "Above reference" in h  # legend
        assert "Provisional comparison: the zones are not severity classes." in h
        assert not re.search(r"\b(Critical|Minor sag|safe|unsafe|compliant|Moderate)\b", h)
    # proportional: amber from 2.8 % to 3.2 % on a 0 to 10 % scale; the dot sits at the ratio
    assert _left(hb, "z z-medium") == pytest.approx(28.0) and _left(hb, "z z-high") == pytest.approx(32.0)
    assert _left(hb, "rx-meter-dot") == pytest.approx(12.0) and _left(hx, "rx-meter-dot") == pytest.approx(60.0)
    assert 'rx-zone below' in hb and 'rx-zone at' in ha and 'rx-zone above' in hx
    assert "Uncertainty overlaps the reference" in ha and "Uncertainty overlaps" not in hb
    assert "2.80% to 3.20%" in hb  # the region's boundaries are written out, not only coloured


def test_reference_meter_handles_overflow_and_a_hairline_tolerance(shipped_cfg):
    pc = _pc(shipped_cfg)
    big = _api_wire(shipped_cfg, ratio=0.17, res=0.002)
    tiny = _api_wire(shipped_cfg, ratio=0.01, res=0.00001)
    hb, ht = _page([("ref", {"w": big, "pc": pc}), ("ref", {"w": tiny, "pc": pc})])
    assert "rx-meter-over" in hb and "overflow" in hb and "10.0%+" in hb and "beyond the 10.0% scale" in hb
    assert _left(hb, "rx-meter-dot") == 100
    w_amber = float(re.search(r'class="z z-medium" style="left:[0-9.]+%;width:([0-9.]+)%', ht).group(1))
    assert w_amber == pytest.approx(0.8)  # visible, and no wider than needed


def test_no_reference_keeps_the_colourful_meter_and_says_no_comparison_applies(shipped_cfg):
    pc = _pc(shipped_cfg)
    tel = _api_wire(shipped_cfg, ratio=0.024, res=0.003, cls="telecom-cable", reason_code="endpoint_unavailable")
    unk = _api_wire(shipped_cfg, ratio=0.024, res=0.003, class_status="uncertain")
    ht, hu, he = _page([("type", {"w": tel, "pc": pc}), ("type", {"w": unk, "pc": pc}), ("empty", {})])
    for h in (ht, hu):
        assert 'class="rx-meter type"' in h and "--wc:rgb(1,2,3)" in h and "rx-meter-fill" in h and "rx-meter-dot" in h
        assert "No applicable comparison" in h and "2.4% ±0.3" in h
        assert 'class="z ' not in h and "rx-meter-ref" not in h and "Reference 3.0%" not in h
    assert "telecom / fibre cable" in ht and "visible part" in ht and "partial" in ht
    assert "does not show clearly" in hu
    assert "Not measured" in he and "rx-meter-dot" not in he


def test_inspector_shows_one_status_and_hides_rows_that_do_not_apply(shipped_cfg):
    pc = _pc(shipped_cfg)
    pw = _api_wire(shipped_cfg, ratio=0.012, res=0.002, provenance={"observed_px": 850, "inferred_px": 150, "centerline_inferred_share": 0.1, "inferred_segments": [[[0, 0], [1, 1]]]})
    tel = _api_wire(shipped_cfg, ratio=0.012, res=0.002, cls="telecom-cable")
    nm = _api_wire(shipped_cfg, ratio=None, res=None, status="fit_failed", reason_code="residual_too_large", fit=None)
    hp, ht, hn = _page([("card", {"w": pw, "pc": pc}), ("card", {"w": tel, "pc": pc}), ("card", {"w": nm, "pc": pc})])
    assert "<dt>Reference</dt>" in hp and "<dt>Difference</dt>" in hp and "−1.8 pp" in hp
    assert "Unassessed (Provisional Comparison Only)" in hp and "rx-meter cmp below" in hp
    assert "<dt>Reference</dt>" not in ht and "<dt>Difference</dt>" not in ht
    assert ht.count("Unassessed (No Validated Reference)") == 1 and "telecom / fibre cable" in ht
    assert "<dt>Chord</dt><dd class=\"\">500 px</dd>" in hp
    for row in ("Traced fraction", "Interpolation", "Chord", "Sag calculation", "Uncertainty", "Pixel resolution", "Fit diagnostics"):
        assert f"<dt>{row}</dt>" in hp, row
    assert "90% of the centreline length" in hp and "10% of the length bridged across 1 gap" in hp  # length, not pixel area and "1 px = 0.200% of the chord" in hp
    assert '<details class="rx-md"><summary>Measurement details</summary>' in hp
    assert "Unassessed (Not Measured)" in hn and "Image sag ratio" not in hn and "<dt>Reason</dt>" in hn


def test_inventory_status_keeps_coverage_and_assessment_badges_separate(shipped_cfg):
    flat = _api_wire(shipped_cfg, ratio=0.0005, res=0.002, cls="telecom-cable")
    pw = _api_wire(shipped_cfg, ratio=0.04, res=0.002)
    hf, hp = _page([("status", {"w": flat}), ("status", {"w": pw})])
    assert "Unassessed" in hf and "Reference Unavailable" in hf and "Within Resolution" in hf and "safe" not in hf.replace("not evidence that the wire is safe", "")
    assert "Unassessed" in hp and "Above reference" in hp and "Reference Unavailable" not in hp
    js = FIELD.read_text()
    assert 'const COVER = { full_span: "Full Span", edge_clipped: "Edge Clipped", unmeasured: "Unmeasured", drop: "Drop Wire" };' in js
    assert "<th scope=\"col\">Wire ID</th><th scope=\"col\">Type</th><th scope=\"col\">Coverage</th><th scope=\"col\" class=\"r\">Image Sag Ratio</th><th scope=\"col\">Status</th>" in js


# ----------------------------------------------------------------------------- page structure
def test_results_page_reading_order_and_single_methodology_section():
    js = FIELD.read_text()
    page = js[js.index("function renderResult(r) {"):js.index("const root = stages.result;")]
    order = ['class="rx-head"', 'class="rx-actions"', 'id="rx-banner"', 'class="rx-summary', 'class="rx-kpi"', 'class="rx-do"',
             'class="rx-photo"', 'id="inventory"', 'id="span-detail"', "${methodology(r, pc, LIMS)}"]
    pos = [page.index(x) for x in order]
    assert pos == sorted(pos), dict(zip(order, pos, strict=True))
    assert "<wf-notice" not in page and js.count('<wf-notice variant="full">') == 1  # inside the methodology section
    meth = js[js.index("function methodology(r, pc, LIMS)"):]
    assert "Methodology &amp; Limitations" in meth and "<details" in meth and "r.methodology" in meth
    for gone in ("rx-phototip", "rx-retake", "rx-callouts", "rx-guide", "rx-limits", "Photo limitations</h2>"):
        assert gone not in page, gone
    # browse filter, not an engineering threshold
    assert "const BROWSE_SAG = 0.02;" in js and "Not an engineering threshold." in js
    assert "${inv.open ? \"Hide\" : \"Show\"} ${flat.length} wire" in js and "with no resolvable sag" in js and "This is not evidence that" in js
    css = CSS.read_text()
    s12 = css[css.index("S12 results page"):]
    assert "gradient(" not in s12 and "box-shadow: 0 12px" not in s12


def test_methodology_covers_the_eleven_topics_from_the_configured_reference(shipped_cfg):
    t = topics(pole_comparison({"wires": []}, shipped_cfg))
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


def test_api_csv_pdf_and_assistant_agree(client, scene, shipped_cfg):
    import pypdfium2 as pdfium
    aid = upload(client, scene.rgb, scene.labels)
    assert wait_done(client, aid)["status"] == "complete"
    r = client.get(f"/api/analysis/{aid}/result").json()
    dc, pc = r["diagnosis"]["counts"], r["comparison"]
    assert pc["lower_boundary"] == pc["upper_boundary"] == 0.03 and pc["zone_note"]
    for w in r["wires"]:
        s, c = w["states"], w["comparison"]
        assert s["comparison"] == (c["position"] if c else None)
        assert s["reference_available"] == (c is not None)
        assert s["assessed"] is False and s["classification_available"] is False
        if s["coverage"] == "edge_clipped":
            assert w["reason_code"] == "endpoint_unavailable"
    assert sum(w["states"]["coverage"] == "full_span" for w in r["wires"]) == dc["measured"]
    assert sum(w["states"]["coverage"] == "edge_clipped" for w in r["wires"]) == dc["measured_partial"]
    assert sum(pc["counts"].values()) == sum(1 for w in r["wires"] if w["comparison"])
    assert r["summary_lines"] == summary_lines(r) and f"{dc['detected']} wire" in r["summary_lines"][0]
    assert r["methodology"] == topics(pc)
    csv = client.get(f"/api/admin/inspections/{aid}/findings.csv").text
    assert "Coverage,Reference applicability,Assessment" in csv
    pdf = client.get(f"/api/reports/{aid}/pdf")
    doc = pdfium.PdfDocument(io.BytesIO(pdf.content))
    text = " ".join(" ".join(doc[i].get_textpage().get_text_range() for i in range(len(doc))).split())
    assert r["summary_lines"][0] in text and "5.0 Methodology and Limitations" in text
    assert text.index("2.0 Recommended Field Actions") < text.index("3.0 Annotated Photographic Evidence")
    for t in r["methodology"]:
        assert t["title"] in text, t["title"]
    assert "Keep distance" in text
    assert "reference-tolerance region" in counts_text(pc) or "so none is compared" in counts_text(pc)
