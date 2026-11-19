"""Register D-27: the provisional 3 % reference covers power lines only, on the restored S8 page.

ERB 95-21 s.19 (superseded by the DSOAR) covers wires on electric utility poles; no source gives a 3 % limit for
telecom / fibre cable, and a wire whose type the photo does not show clearly is not assumed to be a power line. Those
wires keep their image sag ratio and resolution; every view says the comparison is not applicable. The page meters are
tested in test_s7_sag_meter.py.
"""

from __future__ import annotations

from app.core.config import ROOT_DIR
from app.services.comparison import compare_wire, counts_text
from tests.conftest import upload, wait_done

FIELD = ROOT_DIR / "app/web/assets/field.js"


def _w(**kw):
    return {"index": 7, "class": "power-distribution-lines", "status": "not_assessed", "reason_code": "no_validated_reference",
            "sag_ratio": 0.057, "sag_ratio_resolution": 0.004, **kw}


def test_only_power_lines_of_a_known_type_are_compared(s16_cfg, shipped_cfg):
    # S17 (D-37): the shipped reference is ERB 95-21 s.19, power lines only, with its superseded status stated
    shipped = shipped_cfg.comparison_reference
    assert shipped.classes == ["power-distribution-lines"] and shipped.sag_ratio == 0.03
    assert "power-line" in shipped.label and "95-21" in shipped.source and "superseded" in shipped.status_note
    ref = s16_cfg.comparison_reference
    assert ref.classes == ["power-distribution-lines"] and ref.sag_ratio == 0.03 and ref.status == "provisional"
    assert compare_wire(_w(), s16_cfg)["position"] == "above"
    # S15 (D-31): telecom is compared with its OWN bands (2% / 5%), never with the 3% power reference
    tc = compare_wire(_w(**{"class": "telecom-cable"}), s16_cfg)
    assert tc["reference_id"] == "telecom" and tc["reference"] == 0.02 and tc["upper_boundary"] == 0.05
    assert tc["reference"] != ref.sag_ratio and "3%" not in tc["label"]
    assert compare_wire(_w(**{"class": "abandoned-wire"}), s16_cfg) is None
    assert compare_wire(_w(class_status="uncertain"), s16_cfg) is None  # unknown type: never assumed power
    assert compare_wire(_w(reason_code="class_uncertain"), s16_cfg) is None
    assert compare_wire(_w(flags=["class_uncertain"]), s16_cfg) is None
    assert compare_wire(_w(status="service_drop"), s16_cfg) is None


def test_counts_sentence_says_when_nothing_is_covered():
    pc = {"label": "3% sag-to-span reference for power lines (provisional)", "counts": {"above": 0, "at": 0, "below": 0, "not_measured": 0}}
    assert counts_text(pc) == "No wire in this photo is covered by the 3% sag-to-span reference for power lines (provisional), so none is compared"


def test_api_and_csv_apply_the_same_scope(client, scene):
    aid = upload(client, scene.rgb, scene.labels)
    assert wait_done(client, aid)["status"] == "complete"
    r = client.get(f"/api/analysis/{aid}/result").json()
    for w in r["wires"]:
        known = w["status"] != "service_drop" and w.get("class_status") != "uncertain"
        covered = w["class"] in ("power-distribution-lines", "telecom-cable") and known
        assert (w["comparison"] is not None) == covered, w["index"]
        if covered:  # S15: each class only ever against its own reference
            assert w["comparison"]["reference_id"] == ("power" if w["class"].startswith("power") else "telecom")
    csv = client.get(f"/api/admin/inspections/{aid}/findings.csv").text
    if any(w["comparison"] is None and w["status"] != "service_drop" for w in r["wires"]):
        assert "Not compared (no applicable reference)" in csv
