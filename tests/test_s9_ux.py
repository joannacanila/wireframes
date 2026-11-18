"""S9 (register D-27): the system-wide UI/UX restructuring and the power-line scope of the 3 % reference.

The reference applies to power lines only (ERB 95-21 s.19 covers wires on electric utility poles; no source gives a
3 % limit for telecom / fibre cable). A measured telecom wire keeps its image sag ratio and resolution, drawn on a
neutral measurement-only bar with no zones, no reference tick and no comparison word. The page keeps every v2.18 and
S8 control; the information is reordered (overview, photo with the selected wire, paged inventory, next steps, folded
reasoning, one notice) and the Field Screener lays out at phone width instead of refusing it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from app.core.config import ROOT_DIR
from app.services.comparison import compare_wire
from tests.conftest import upload, wait_done

WEB = ROOT_DIR / "app/web"
FIELD = WEB / "assets/field.js"


def _meter(cases: list[dict]) -> list[str]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    src = FIELD.read_text()
    block = src[src.index("const pp = (x) =>"):src.index("/** The selected wire section")]
    js = f"""
const esc = (s) => String(s); const group = (w) => w.level || "none";
const isMeasured = (w) => w.sag_ratio != null; const hasRatio = (w) => isMeasured(w) && w.sag_ratio != null;
const measuredText = (w) => `${{(w.sag_ratio * 100).toFixed(1)}}%${{w.sag_ratio_resolution ? ` ±${{(w.sag_ratio_resolution * 100).toFixed(1)}}` : ""}}`;
{block}
console.log(JSON.stringify({json.dumps(cases)}.map((w) => sagMeter(w))));
"""
    return json.loads(subprocess.run([node, "-e", js], capture_output=True, text=True, check=True).stdout)


def _w(**kw):
    return {"index": 7, "class": "telecom-cable", "status": "not_assessed", "reason_code": "no_validated_reference",
            "sag_ratio": 0.057, "sag_ratio_resolution": 0.004, **kw}


# ----------------------------------------------------------------------------- reference scope
def test_reference_covers_power_lines_only(shipped_cfg):
    ref = shipped_cfg.comparison_reference
    assert ref.classes == ["power-distribution-lines"] and ref.sag_ratio == 0.03 and ref.status == "provisional"
    assert "power lines" in ref.label and "power lines only" in ref.source
    assert compare_wire(_w(), shipped_cfg) is None
    assert compare_wire(_w(**{"class": "abandoned-wire"}), shipped_cfg) is None
    assert compare_wire(_w(**{"class": "power-distribution-lines"}), shipped_cfg)["position"] == "above"


def test_a_telecom_wire_gets_the_measurement_only_bar_never_a_comparison():
    w = _w()
    w["comparison"] = None
    (h,) = _meter([w])
    assert 'class="rx-meter solo"' in h and "rx-meter-dot" in h and "rx-meter-res" in h and "This wire 5.7% ±0.4" in h
    assert not re.findall(r'class="z z-', h) and "rx-meter-ref" not in h  # no zones, no reference tick
    assert "not compared" in h and "no condition" in h
    assert not re.search(r"\b(Above|Below|At reference|Critical|Minor sag|safe|unsafe|compliant)\b", h)
    n = _w(sag_ratio=None, status="fit_failed", reason_code="residual_too_large")
    n["comparison"] = None
    assert _meter([n]) == [""]  # nothing measured, nothing drawn


def test_the_api_compares_only_power_lines(client, scene, shipped_cfg):
    aid = upload(client, scene.rgb, scene.labels)
    assert wait_done(client, aid)["status"] == "complete"
    r = client.get(f"/api/analysis/{aid}/result").json()
    for w in r["wires"]:
        assert (w["comparison"] is not None) == (w["class"] == "power-distribution-lines" and w["status"] != "service_drop"), w["index"]
    assert sum(r["comparison"]["counts"].values()) == sum(1 for w in r["wires"] if w["comparison"])


# ----------------------------------------------------------------------------- page structure
def test_result_page_reading_order_and_one_notice():
    js = FIELD.read_text()
    page = js[js.index("function renderResult(r) {"):js.index("const root = stages.result;")]
    order = ['class="fx-crumbs"', 'class="fx-insp"', 'class="fx-summary', 'class="fx-work"', 'id="inventory"',
             'class="fx-guide"', "${loc ?", "${diagHtml(r)}", 'class="fx-drawer"', 'wf-notice variant="full"']
    pos = [page.index(x) for x in order]
    assert pos == sorted(pos), dict(zip(order, pos, strict=True))
    assert page.count("<wf-notice") == 1 and "Automated screening: results can be wrong" not in page
    for needle in ('"Fully measured"', '"Partially measured"', '"Not measured"', '"Detected"', '"Assessed"',
                   "data-sort=", 'id="inv-pager"', "const PER_PAGE = [25, 50, 0]", "Photo limitations",
                   "Field and engineering information", "How this result was reached"):
        assert needle in js, needle


def test_the_overview_counts_add_up_and_never_count_a_missing_value_as_zero():
    js = FIELD.read_text()
    assert '["unmeasured", "Not measured", c.unmeasured], ...(c.drop ? [["drop", "Drop wires", c.drop]] : [])' in js
    assert "const withRatio = total - c.unmeasured - c.drop;" in js
    assert 'title="Not measured: there is no value, not a zero">—</span>' in js


def test_field_screener_lays_out_on_phones_instead_of_refusing_them():
    html = (WEB / "field.html").read_text()
    css = (WEB / "assets/field.css").read_text()
    assert "fx-unsupported" not in html and "Desktop browser required" not in html and "fx-unsupported" not in css
    assert '<header class="fx-idle-head"><h1>New inspection</h1>' in html
    assert "@media (max-width: 640px)" in css and "aspect-ratio: var(--ar)" in css


def test_admin_has_one_export_menu_and_one_notice_on_the_sheet():
    js = (WEB / "assets/admin.js").read_text()
    head = js[js.index('<header class="pg-head pg-head-line">'):js.index('<section class="batch"')]
    assert head.count('class="btn"') == 2 and ">Export<" in head and 'id="csv"' in head and "Technical exports" in head
    sheet = js[js.index("const render = (r, c) => {"):js.index("// ---- viewer")]
    assert sheet.count("<wf-notice") == 1 and sheet.index("Technical diagnostics") < sheet.index("wf-notice")
    assert "How this result was reached" in sheet and "Field and engineering information" in sheet
