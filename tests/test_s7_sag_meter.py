"""The v2.21 three-zone sag meter (7c5a716, shown at the handoff commit 0916fd2) on the handoff v2.23 result page.

S7 (D-25) restored it for assessed wires; S12 (D-28) draws the reference and no-reference meters (tested in
test_s12_results_ui.py). S10 (D-26 / D-27) lit it from the server's power-line comparison with the provisional 3 %
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
    unlit = src[src.index("/** S12 meters"):src.index("/** S5: the wire's badge.")]  # meterRef, meterType, cmpLine, meterEmpty
    js = f"""
const esc = (s) => String(s); const classLabel = (w) => "Telecom / fiber cable"; const wireColor = () => "rgb(156,54,181)";
const pc = (x) => `${{(x * 100).toFixed(1)}}%`;
const measuredText = (w) => `${{pc(w.sag_ratio)}}${{w.sag_ratio_resolution ? ` ±${{(w.sag_ratio_resolution * 100).toFixed(1)}}` : ""}}`;
const pp = (x) => `${{(x * 100).toFixed(1)}}%`;
{meter}
{unlit}
void {photo_scale};
const cases = {json.dumps(cases)};
console.log(JSON.stringify(cases.map((c) => c.kind === "lit" ? meter(c.w, "high") : c.kind === "ref" ? meterRef(c.w) : c.kind === "type" ? meterType(c.w) : meterEmpty(c.w))));
"""
    out = subprocess.run([node, "-e", js], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _left(html: str, cls: str) -> float:
    return float(re.search(rf'class="{cls}[^"]*" style="left:([0-9.]+)%', html).group(1))


# S12: the power-line and no-reference meters are tested in tests/test_s12_results_ui.py (configured boundaries).


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
