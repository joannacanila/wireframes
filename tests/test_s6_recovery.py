"""S6 recovery (18 Nov 2026, register D-24): the v2.18 result experience restored on top of the S4/S5 corrections.

The v2.19-v2.23 redesign (commit 7c5a716) removed the v2.18 result page (side-by-side table, Both view, overlay
opacity, vegetation and layer controls, Reset, measurement details, specific photo limitations, technical drawer);
S4 then hid every measurement. These tests pin what the recovery restored and the defects it found on the way."""

from __future__ import annotations

import numpy as np

from app.services import plain
from app.services.render import CLASS_RGB, LEVEL_RGB, render_overlay, wire_rgb


def _w(**kw):
    base = {"index": 1, "class": "telecom-cable", "class_status": "confident", "status": "not_assessed",
            "reason_code": "no_validated_reference", "sag_ratio": 0.03, "level": None, "mask_polygons": [],
            "catenary_curve": [[50, 60], [200, 90], [350, 60]], "bbox": [50, 60, 350, 90],
            "fit": {"endpoints": [[50, 60], [350, 60]], "sag_point": [200, 90], "chord_foot": [200, 60]}}
    return {**base, **kw}


def test_pdf_overlay_colours_measured_wires_by_type_never_by_severity():
    assert wire_rgb(_w()) == CLASS_RGB["telecom-cable"]
    assert wire_rgb(_w(**{"class": "power-distribution-lines"})) == CLASS_RGB["power-distribution-lines"]
    assert wire_rgb(_w(class_status="uncertain")) not in CLASS_RGB.values()
    assert wire_rgb(_w(status="fit_failed", sag_ratio=None)) == LEVEL_RGB[None]  # not measured: grey
    # a stored level on an unassessed (withdrawn, pre-S4) wire is never drawn as a severity colour
    assert wire_rgb(_w(level="high")) == CLASS_RGB["telecom-cable"]
    assert wire_rgb(_w(status="scored", level="high")) == LEVEL_RGB["high"]
    rgb = np.full((200, 400, 3), 30, np.uint8)
    drawn = render_overlay(rgb, [_w()], max_side=400)
    assert (np.abs(drawn.astype(int) - np.array(CLASS_RGB["telecom-cable"])).sum(axis=2) < 40).any()


def _incon(retake: bool):
    return {"status": "complete", "hazard": {"verdict": "INCONCLUSIVE - RETAKE PHOTO", "reasons": []},
            "wires": [_w()], "diagnosis": {"retake_helps": retake}}


def test_report_summary_does_not_contradict_the_retake_advice():
    """S5 PDF said 'a retake will not complete the assessment' while section 3.2 asked for a new photo of the
    wires that ran off the frame. The summary now follows diagnosis.retake_helps."""
    yes, no = plain.summary(_incon(True)), plain.summary(_incon(False))
    text = lambda d: " ".join(str(x) for x in (d.get("lines") or []))  # noqa: E731
    assert "A new photo can complete the measurement" in text(yes) and "retake will not complete" not in text(yes)
    assert "retake will not complete" in text(no)

def test_short_finding_of_a_measured_wire_states_its_measurement():
    """S5 regression: the table's short finding returned 'Could not be measured' for measured, unassessed wires."""
    from app.core.config import ROOT_DIR
    js = (ROOT_DIR / "app/web/assets/findings.js").read_text()
    body = js[js.index("export function finding("):js.index("export function limitations(")]
    assert body.index('w.reason_code === "endpoint_unavailable"') < body.index('if (short && w.status !== "scored")')
    assert body.index("MEASURED_ONLY.has(w.reason_code)") < body.index('if (short && w.status !== "scored")')


def test_restored_result_page_structure_and_styles():
    from app.core.config import ROOT_DIR
    js = (ROOT_DIR / "app/web/assets/field.js").read_text()
    css = (ROOT_DIR / "app/web/assets/field.css").read_text()
    for needle in ("const PRESETS = {", 'overlay: { wiremask: 1, masks: 1', "const LAYERS = [", "function spansTable(r, mode)",
                   "function spanDetail(r, w, mode)", "function diagnostics(r)", 'setView("spans")', "viewer.setOpacity(",
                   'viewer.setMode("class")', "data-filter-go"):
        assert needle in js, needle
    for sel in (".fx-toolbar", ".fx-seg", ".fx-opacity", ".fx-menu", ".fx-spans", ".fx-filters", ".fx-detail", ".fx-dl",
                ".fx-more", ".fx-recs", ".fx-limits", ".fx-drawer", ".fx-counts", ".fx-risk.measured"):
        assert sel in css, sel
