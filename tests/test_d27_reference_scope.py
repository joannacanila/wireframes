"""Register D-27: the provisional 3 % reference covers power lines only, on the restored S8 page.

ERB 95-21 s.19 (superseded by the DSOAR) covers wires on electric utility poles; no source gives a 3 % limit for
telecom / fibre cable, and a wire whose type the photo does not show clearly is not assumed to be a power line. Those
wires keep their image sag ratio and resolution, drawn on the same meter in their wire-type colour with no zones, no
reference tick and no comparison word; every view says the comparison is not applicable.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from app.core.config import ROOT_DIR
from app.services.comparison import compare_wire, counts_text
from tests.conftest import upload, wait_done

FIELD = ROOT_DIR / "app/web/assets/field.js"


def _w(**kw):
    return {"index": 7, "class": "power-distribution-lines", "status": "not_assessed", "reason_code": "no_validated_reference",
            "sag_ratio": 0.057, "sag_ratio_resolution": 0.004, **kw}


def _meter(cases: list[dict]) -> list[str]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    src = FIELD.read_text()
    block = src[src.index("const pp = (x) =>"):src.index("/** The selected wire section")]
    js = f"""
const esc = (s) => String(s); const group = (w) => w.level || "none";
const measuredText = (w) => `${{(w.sag_ratio * 100).toFixed(1)}}%${{w.sag_ratio_resolution ? ` ±${{(w.sag_ratio_resolution * 100).toFixed(1)}}` : ""}}`;
{block}
console.log(JSON.stringify({json.dumps(cases)}.map((w) => sagMeter(w, "rgb(156,54,181)"))));
"""
    return json.loads(subprocess.run([node, "-e", js], capture_output=True, text=True, check=True).stdout)


def test_only_power_lines_of_a_known_type_are_compared(shipped_cfg):
    ref = shipped_cfg.comparison_reference
    assert ref.classes == ["power-distribution-lines"] and ref.sag_ratio == 0.03 and ref.status == "provisional"
    assert "power lines" in ref.label and "superseded" in ref.source
    assert compare_wire(_w(), shipped_cfg)["position"] == "above"
    assert compare_wire(_w(**{"class": "telecom-cable"}), shipped_cfg) is None
    assert compare_wire(_w(**{"class": "abandoned-wire"}), shipped_cfg) is None
    assert compare_wire(_w(class_status="uncertain"), shipped_cfg) is None  # unknown type: never assumed power
    assert compare_wire(_w(reason_code="class_uncertain"), shipped_cfg) is None
    assert compare_wire(_w(flags=["class_uncertain"]), shipped_cfg) is None
    assert compare_wire(_w(status="service_drop"), shipped_cfg) is None


def test_counts_sentence_says_when_nothing_is_covered():
    pc = {"label": "3% sag-to-span reference for power lines (provisional)", "counts": {"above": 0, "at": 0, "below": 0, "not_measured": 0}}
    assert counts_text(pc) == "No wire in this photo is covered by the 3% sag-to-span reference for power lines (provisional), so none is compared"


def test_uncovered_wire_keeps_a_coloured_measurement_meter_without_zones_or_reference():
    tel = _w(**{"class": "telecom-cable"}, comparison=None)
    unk = _w(class_status="uncertain", comparison=None)
    nm = _w(**{"class": "telecom-cable"}, comparison=None, sag_ratio=None, status="fit_failed")
    h_tel, h_unk, h_nm = _meter([tel, unk, nm])
    for h in (h_tel, h_unk):
        assert 'class="rx-meter type"' in h and "--wc:rgb(156,54,181)" in h and "rx-meter-fill" in h
        assert "rx-meter-dot" in h and "rx-meter-res" in h and "This wire 5.7% ±0.4" in h
        assert not re.findall(r'class="z z-', h) and "rx-meter-ref" not in h and "rx-zones" not in h
        assert "Comparison unavailable" in h and "no condition" in h
        assert not re.search(r"\b(Above|Below|At reference|Critical|Minor sag|safe|unsafe|compliant)\b", h)
    assert "telecom / fibre cable" in h_tel and "type clearly" in h_unk
    assert h_nm == ""  # nothing measured: nothing drawn


def test_power_line_keeps_the_original_three_colour_meter():
    (h,) = _meter([_w(comparison={"reference": 0.03, "status": "provisional", "label": "x", "state": "measured", "position": "above",
                                  "difference_pp": 2.7, "zones": {"at_from": 0.026, "above_from": 0.034}})])
    assert re.findall(r'class="z (z-low|z-medium|z-high)"', h) == ["z-low", "z-medium", "z-high"] and "rx-meter-ref" in h


def test_api_and_csv_apply_the_same_scope(client, scene):
    aid = upload(client, scene.rgb, scene.labels)
    assert wait_done(client, aid)["status"] == "complete"
    r = client.get(f"/api/analysis/{aid}/result").json()
    for w in r["wires"]:
        covered = w["class"] == "power-distribution-lines" and w["status"] != "service_drop" and w.get("class_status") != "uncertain"
        assert (w["comparison"] is not None) == covered, w["index"]
    csv = client.get(f"/api/admin/inspections/{aid}/findings.csv").text
    if any(w["comparison"] is None and w["status"] != "service_drop" for w in r["wires"]):
        assert "Not compared (no applicable reference)" in csv
