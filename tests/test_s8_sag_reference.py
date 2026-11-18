"""S8 (register D-26): the configured 3 % sag reference and the full-colour three-zone sag meter.

The reference is one configured value (config/wireframe.yaml ``comparison_reference``) that the server compares
every measured wire with (app/services/comparison.py). The page, PDF, admin sheet, CSV exports and assistant all read
that comparison, so changing the value changes every view. It is a comparison of the IMAGE sag ratio, never an
assessment: no level, CRS or pole verdict comes from it. The three meter zones need only the reference and the wire's
own 1 px resolution. These tests run the page's meter functions in Node and check every consumer.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess

import pytest

from app.core.config import ROOT_DIR
from app.models.orm import Analysis
from app.services.comparison import compare_wire
from tests.conftest import upload, wait_done

FIELD = ROOT_DIR / "app/web/assets/field.js"


def _w(**kw):
    base = {"index": 1, "class": "power-distribution-lines", "status": "not_assessed", "reason_code": "no_validated_reference",
            "sag_ratio": 0.05, "sag_ratio_resolution": 0.003}
    return {**base, **kw}


# ----------------------------------------------------------------------------- the comparison rule
def test_shipped_reference_is_three_percent_and_provisional(shipped_cfg):
    c = shipped_cfg.comparison_reference
    assert c.enabled and c.sag_ratio == 0.03 and c.status == "provisional" and "provisional" in c.label
    assert "ERB Res. 95-21" in c.source and "superseded" in c.source and "not validated" in c.source
    # it is not a scoring reference: S4 stands (no validated reference, nothing is assessed)
    assert not any(shipped_cfg.crs.delta_lim0.values()) if shipped_cfg.crs.delta_lim0 else True


@pytest.mark.parametrize("ratio, res, pos", [
    (0.050, 0.003, "above"),   # 5.0 - 0.3 > 3.0
    (0.032, 0.003, "at"),      # 3.0 inside 2.9 .. 3.5
    (0.028, 0.003, "at"),      # 3.0 inside 2.5 .. 3.1
    (0.026, 0.003, "below"),   # 2.6 + 0.3 < 3.0
    (0.0331, 0.003, "above"),  # 3.31 - 0.3 = 3.01 > 3.0
    (0.0269, 0.003, "below"),  # 2.69 + 0.3 = 2.99 < 3.0
    (0.040, 0.011, "at"),      # wire 23 of P-B1D137: 4.0 +/- 1.1 contains 3.0
])
def test_position_uses_only_the_reference_and_the_wires_resolution(shipped_cfg, ratio, res, pos):
    c = compare_wire(_w(sag_ratio=ratio, sag_ratio_resolution=res), shipped_cfg)
    assert c["position"] == pos and c["reference"] == 0.03 and c["status"] == "provisional"
    assert c["difference_pp"] == pytest.approx((ratio - 0.03) * 100, abs=0.01)
    assert c["zones"] == {"at_from": pytest.approx(0.03 - res), "above_from": pytest.approx(0.03 + res)}


def test_partial_unmeasured_drop_and_uncovered_wires(shipped_cfg):
    part = compare_wire(_w(reason_code="endpoint_unavailable"), shipped_cfg)
    assert part["state"] == "measured_partial" and part["position"] == "above"
    none = compare_wire(_w(status="fit_failed", reason_code="residual_too_large", sag_ratio=None), shipped_cfg)
    assert none["state"] == "not_measured" and none["position"] is None and none["zones"] is None
    assert compare_wire(_w(status="service_drop", sag_ratio=None), shipped_cfg) is None
    # S9 (register D-27): the shipped reference covers power lines only; telecom and abandoned wires are not compared
    assert shipped_cfg.comparison_reference.classes == ["power-distribution-lines"]
    for cls in ("telecom-cable", "abandoned-wire"):
        assert compare_wire(_w(**{"class": cls}), shipped_cfg) is None
    only_telecom = shipped_cfg.model_copy(update={"comparison_reference": shipped_cfg.comparison_reference.model_copy(
        update={"classes": ["telecom-cable"]})})
    assert compare_wire(_w(), only_telecom) is None
    off = shipped_cfg.model_copy(update={"comparison_reference": shipped_cfg.comparison_reference.model_copy(update={"enabled": False})})
    assert compare_wire(_w(), off) is None


def test_reference_status_must_be_a_known_kind(shipped_cfg):
    with pytest.raises(ValueError):
        type(shipped_cfg.comparison_reference)(status="official")
    with pytest.raises(ValueError):
        type(shipped_cfg.comparison_reference)(classes=["fence"])


# ---------------------------------------------------------- the S8 page meter tests moved to test_s7_sag_meter.py (S10)
def _run(cases: list[dict]) -> list[str]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    src = FIELD.read_text()
    block = src[src.index("const pp = (x) =>"):src.index("/** The selected wire section")]
    js = f"""
const esc = (s) => String(s); const group = (w) => w.level || "none";
const measuredText = (w) => `${{(w.sag_ratio * 100).toFixed(1)}}%${{w.sag_ratio_resolution ? ` ±${{(w.sag_ratio_resolution * 100).toFixed(1)}}` : ""}}`;
{block}
console.log(JSON.stringify({json.dumps(cases)}.map((w) => sagMeter(w))));
"""
    return json.loads(subprocess.run([node, "-e", js], capture_output=True, text=True, check=True).stdout)


def _left(html: str, cls: str) -> float:
    return float(re.search(rf'class="{cls}[^"]*" style="left:([0-9.]+)%', html).group(1))


def _zones(html: str) -> list[str]:
    return re.findall(r'class="z (z-low|z-medium|z-high)"', html)


# ----------------------------------------------------------------------------- every consumer, one value
def _complete(client, scene):
    aid = upload(client, scene.rgb, scene.labels)
    assert wait_done(client, aid)["status"] == "complete"
    return aid


def test_comparison_is_never_an_assessment(client, anon, scene):
    aid = _complete(client, scene)
    for c in (client, anon):
        r = c.get(f"/api/analysis/{aid}/result").json()
        assert r["comparison"]["sag_ratio"] == 0.03 and r["comparison"]["status"] == "provisional"
        assert sum(r["comparison"]["counts"].values()) == sum(1 for w in r["wires"] if w["comparison"])
        assert not any(w["status"] == "scored" or w["level"] or w["sag_bands"] for w in r["wires"])
        assert not r["hazard"]["verdict"].startswith("AT RISK") and r["hazard"]["verdict"] != "NOT AT RISK"


def test_changing_the_configured_reference_changes_every_view(client, scene):
    import pypdfium2 as pdfium

    from app.assistant.service import facts_summary, inspection_facts
    aid = _complete(client, scene)
    st = client.state
    st.config = st.config.model_copy(update={"comparison_reference": st.config.comparison_reference.model_copy(
        update={"sag_ratio": 0.045, "label": "4.5% check reference (provisional)"})})
    r = client.get(f"/api/analysis/{aid}/result").json()
    assert r["comparison"]["sag_ratio"] == 0.045
    assert all(w["comparison"]["reference"] == 0.045 for w in r["wires"] if w["comparison"])
    pdf = client.get(f"/api/reports/{aid}/pdf?regenerate=1")
    doc = pdfium.PdfDocument(io.BytesIO(pdf.content))
    text = "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    assert "4.5% check reference (provisional)" in text and "3% sag-to-span" not in text
    csv = client.get(f"/api/admin/inspections/{aid}/findings.csv").text
    assert "4.5% (provisional)" in csv and "Compared with reference (image ratio)" in csv
    with st.db.session() as s:
        f = inspection_facts(s.get(Analysis, aid), st.config)
    assert f["public"]["sag_reference_comparison"]["sag_ratio"] == 0.045
    assert "4.5% check reference" in facts_summary(f) and "not an assessment" in facts_summary(f)
    wires = client.get("/api/admin/exports/wires.csv").text.splitlines()
    head = wires[0].split(",") if not wires[0].startswith("#") else next(x for x in wires if x.startswith("analysis_id")).split(",")
    assert {"level_withdrawn", "sag_reference", "sag_reference_position"} <= set(head)


def test_withdrawn_historical_levels_stay_withdrawn_with_the_reference(client, scene):
    """A pre-S4 record keeps its stored level; the 3 % comparison is shown beside it and never re-activates it."""
    aid = _complete(client, scene)
    with client.state.db.session() as s:
        a = s.get(Analysis, aid)
        d = next(x for x in a.detections if x.sag_ratio is not None or (x.fit_json or {}).get("sag_ratio") is not None)
        d.status, d.level, d.crs, d.delta_lim = "scored", "high", 10.0, 0.04
        a.metadata_json = {k: v for k, v in a.metadata_json.items() if k != "assessment_basis"}
        idx = d.wire_index
    w = next(x for x in client.get(f"/api/analysis/{aid}/result").json()["wires"] if x["index"] == idx)
    assert w["historical"] and w["level"] is None and w["status"] != "scored" and w["sag_bands"] is None
    assert w["comparison"]["reference"] == 0.03 and w["comparison"]["position"] in ("below", "at", "above")


def test_records_without_a_stored_resolution_get_it_derived_on_read_and_unchanged(client, scene, shipped_cfg):
    """Pre-S4 records have no stored resolution; the same S4 formula is applied on read, flagged, and not written."""
    aid = _complete(client, scene)
    with client.state.db.session() as s:
        a = s.get(Analysis, aid)
        d = next(x for x in a.detections if (x.fit_json or {}).get("sag_ratio_resolution"))
        stored = d.fit_json["sag_ratio_resolution"]
        d.fit_json = {k: v for k, v in d.fit_json.items() if k != "sag_ratio_resolution"}
        idx = d.wire_index
    w = next(x for x in client.get(f"/api/analysis/{aid}/result").json()["wires"] if x["index"] == idx)
    assert w["sag_ratio_resolution_derived"] is True and w["sag_ratio_resolution"] == pytest.approx(stored, rel=1e-3)
    assert w["comparison"]["zones"]["above_from"] == pytest.approx(0.03 + stored, rel=1e-3)
    with client.state.db.session() as s:
        assert "sag_ratio_resolution" not in s.get(Analysis, aid).detections[[x.wire_index for x in s.get(Analysis, aid).detections].index(idx)].fit_json
