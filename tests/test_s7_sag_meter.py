"""The v2.21 three-zone sag meter (7c5a716, shown at the handoff commit 0916fd2) on the handoff v2.23 result page.

S7 (D-25) restored it; S10 (D-26 / D-27) lights it from the server's power-line comparison with the provisional 3 %
reference, and draws a measurement-only meter in the wire-type colour for a wire with no applicable reference. These
tests run the page's own meter functions (Node) and check what the server gives them, so a regression cannot bring
back the withdrawn severity bands.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from app.core.config import ROOT_DIR
from app.models.orm import Analysis
from tests.conftest import upload, wait_done

FIELD = ROOT_DIR / "app/web/assets/field.js"


def _run_meter(cases: list[dict], photo_scale: float) -> list[str]:
    """Evaluate meter(), meterRef(), meterType() and meterEmpty() exactly as written in field.js, with plain stand-ins
    for the imported helpers (escape, class label, wire colour, percentage text)."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    src = FIELD.read_text()
    meter = re.search(r"function meter\(w, g\) \{.*?\n\}\n", src, re.S).group(0)
    unlit = src[src.index("let photoScale = 0;"):src.index("/** S5: the wire's badge.")]  # meterRef, meterType, cmpLine, meterEmpty
    js = f"""
const esc = (s) => String(s); const classLabel = (w) => "Telecom / fiber cable"; const wireColor = () => "rgb(156,54,181)";
const pc = (x) => `${{(x * 100).toFixed(1)}}%`;
const measuredText = (w) => `${{pc(w.sag_ratio)}}${{w.sag_ratio_resolution ? ` ±${{(w.sag_ratio_resolution * 100).toFixed(1)}}` : ""}}`;
const pp = (x) => `${{(x * 100).toFixed(1)}}%`;
{meter}
{unlit}
photoScale = {photo_scale};
const cases = {json.dumps(cases)};
console.log(JSON.stringify(cases.map((c) => c.kind === "lit" ? meter(c.w, "high") : c.kind === "ref" ? meterRef(c.w) : c.kind === "type" ? meterType(c.w) : meterEmpty(c.w))));
"""
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _left(html: str, cls: str) -> float:
    return float(re.search(rf'class="{cls}[^"]*" style="left:([0-9.]+)%', html).group(1))


def test_measurement_only_meter_places_each_wire_on_one_photo_scale_in_its_type_colour():
    a = {"sag_ratio": 0.058, "sag_ratio_resolution": 0.003, "class": "telecom-cable", "class_status": "confident", "reason_code": "no_validated_reference"}
    b = {"sag_ratio": 0.024, "sag_ratio_resolution": 0.004, "class": "telecom-cable", "class_status": "confident", "reason_code": "endpoint_unavailable"}
    scale = (0.058 + 0.003) * 1.3
    ha, hb = _run_meter([{"kind": "type", "w": a}, {"kind": "type", "w": b}], scale)
    for h in (ha, hb):
        assert 'class="rx-meter type"' in h and "--wc:rgb(156,54,181)" in h and "rx-meter-fill" in h
        assert 'class="z ' not in h and "rx-meter-ref" not in h and "rx-zones" not in h  # no zones, no reference
        assert "Comparison unavailable" in h and "telecom / fibre cable" in h and "no condition" in h
        assert not re.search(r"\b(Above|Below|At reference|Critical|Minor sag|safe|compliant)\b", h)
    assert _left(ha, "rx-meter-dot") == pytest.approx(0.058 / scale * 100, abs=0.01)
    assert _left(hb, "rx-meter-dot") == pytest.approx(0.024 / scale * 100, abs=0.01)  # the dot moves with the wire
    assert "This wire 5.8% ±0.3" in ha and "This wire 2.4% ±0.4 (visible part)" in hb
    assert 'rx-meter-dot partial"' in hb and "visible part only" in hb  # partly measured wires are marked
    res = re.search(r'rx-meter-res" style="left:([0-9.]+)%;width:([0-9.]+)%', ha)
    assert float(res.group(1)) == pytest.approx(0.055 / scale * 100, abs=0.01) and float(res.group(2)) == pytest.approx(0.006 / scale * 100, abs=0.01)
    (hu,) = _run_meter([{"kind": "type", "w": {**a, "class": "power-distribution-lines", "class_status": "uncertain"}}], scale)
    assert "type clearly" in hu and "rx-meter-ref" not in hu  # unknown type: never compared


def _cmp(ratio, res, pos, state="measured"):
    return {"sag_ratio": ratio, "sag_ratio_resolution": res, "class": "power-distribution-lines",
            "comparison": {"reference": 0.03, "status": "provisional", "label": "3% sag-to-span reference for power lines (provisional)",
                           "state": state, "position": pos, "difference_pp": round((ratio - 0.03) * 100, 2),
                           "zones": {"at_from": 0.03 - res, "above_from": 0.03 + res}}}


def test_power_line_meter_is_the_original_three_zones_from_the_reference_and_resolution():
    hb, ha, hat = _run_meter([{"kind": "ref", "w": _cmp(0.018, 0.002, "below", "measured_partial")},
                              {"kind": "ref", "w": _cmp(0.05, 0.003, "above")}, {"kind": "ref", "w": _cmp(0.031, 0.004, "at")}], 0)
    for h, pos in ((hb, "below"), (ha, "above"), (hat, "at")):
        assert re.findall(r'class="z (z-low|z-medium|z-high)"', h) == ["z-low", "z-medium", "z-high"]
        assert f"rx-meter cmp {pos}" in h and "rx-meter-ref" in h and "Reference 3.0%" in h
        assert "Below reference" in h and "At reference (±resolution)" in h and "Above reference" in h
        assert "Minor sag" not in h and "Critical" not in h and "Within reference" not in h  # withdrawn labels
        assert "not a validated limit" in h and "not physical sag" in h and "not an assessment" in h
    max_ = max(0.05 + 0.003, 0.033) * 1.3
    assert _left(ha, "rx-meter-ref") == pytest.approx(0.03 / max_ * 100, abs=0.01)
    assert _left(ha, "rx-meter-dot") == pytest.approx(0.05 / max_ * 100, abs=0.01)
    assert "partial" in hb and "(visible part)" in hb


def test_unmeasured_wire_gets_an_empty_meter_without_a_dot():
    (h,) = _run_meter([{"kind": "empty", "w": {"sag_ratio": None}}], 0.08)
    assert 'class="rx-meter empty"' in h and "rx-meter-dot" not in h and "Not measured" in h


def test_lit_meter_is_the_original_three_zones_from_the_server_bands():
    w = {"sag_ratio": 0.05, "delta_lim": 0.04, "sag_bands": {"minor_from": 0.02, "critical_from": 0.04}}
    (h,) = _run_meter([{"kind": "lit", "w": w}], 0)
    zones = re.findall(r'class="z (z-low|z-medium|z-high)"', h)
    assert zones == ["z-low", "z-medium", "z-high"] and "rx-meter-ref" in h and "Within reference" in h
    max_ = 0.05 * 1.3
    assert _left(h, "rx-meter-ref") == pytest.approx(0.04 / max_ * 100, abs=0.01)
    assert _left(h, "rx-meter-dot") == pytest.approx(0.05 / max_ * 100, abs=0.01)


def test_the_card_uses_the_reference_meter_only_for_a_compared_wire():
    src = FIELD.read_text()
    card = src[src.index("function wireCard(w)"):src.index("function renderResult(r)")]
    assert "const lim = scored && w.delta_lim ? w.delta_lim : null;" in card and "${lim != null ? `${meter(w, g)}" in card
    assert "${w.comparison?.position ? meterRef(w) : meterType(w)}" in card and "meterUnlit" not in src
    assert "Not applicable" in card and "photoScale = Math.max(0, ...measuredWs.map(" in src


def test_server_sends_no_zones_without_a_validated_reference(client, scene):
    aid = upload(client, scene.rgb, scene.labels)
    assert wait_done(client, aid)["status"] == "complete"
    r = client.get(f"/api/analysis/{aid}/result").json()
    assert r["wires"] and all(w.get("sag_bands") is None and w.get("delta_lim") is None for w in r["wires"])


def test_withdrawn_historical_levels_never_light_the_meter(client, scene):
    """A pre-S4 record keeps its stored 4.0 % reference in the database; the page must not receive it as zones."""
    aid = upload(client, scene.rgb, scene.labels)
    assert wait_done(client, aid)["status"] == "complete"
    with client.state.db.session() as s:
        a = s.get(Analysis, aid)
        d = a.detections[0]
        d.status, d.level, d.crs, d.delta_lim = "scored", "high", 10.0, 0.04
        a.metadata_json = {k: v for k, v in a.metadata_json.items() if k != "assessment_basis"}
    w = next(x for x in client.get(f"/api/analysis/{aid}/result").json()["wires"] if x["historical"])
    assert w["sag_bands"] is None and w["delta_lim"] is None and w["status"] != "scored"
