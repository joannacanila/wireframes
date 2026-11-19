"""S16 data integrity and engineering (register D-34).

* One display plan per result: attention / notable / others are disjoint, cover every wire, and are ordered on the
  server; the headline counts are the plan's counts.
* Power and telecom references never meet: telecom "beyond typical ranges" is notable, never attention.
* Summary lines count each reference's own wires.
* Contact resolution accepts every spelling of a city and reports a barangay / city mismatch without fixing it.
* The calibrated 90 % band, the continuation path and the leakage-safe field splits behave as specified.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.comparison import compare_wire, wire_states
from app.services.outcome import attach, display_plan, wire_finding
from tests.conftest import upload, wait_done
from tests.test_s12_results_ui import _w


def _wire(cfg, index, ratio, cls="telecom-cable", res=0.002, unc=None, **kw):
    w = _w(ratio=ratio, res=res, cls=cls, **kw)
    w["index"] = index
    w["sag_ratio_uncertainty"] = unc
    w["comparison"] = compare_wire(w, cfg)
    w["states"] = wire_states(w, cfg, w["comparison"])
    return w


def _result(cfg, wires):
    return attach({"status": "complete", "wires": wires, "comparison": None})


# ----------------------------------------------------------------------------- display plan
def test_display_plan_is_disjoint_complete_and_server_ordered(s16_cfg):
    ws = [_wire(s16_cfg, 47, 0.141, unc=0.05), _wire(s16_cfg, 59, 0.123, unc=0.045),
          _wire(s16_cfg, 40, 0.058, unc=0.024), _wire(s16_cfg, 62, 0.048, unc=0.02),
          _wire(s16_cfg, 7, 0.045, cls="power-distribution-lines", unc=0.004),
          _wire(s16_cfg, 3, 0.01, cls="power-distribution-lines", unc=0.003),
          _wire(s16_cfg, 9, 0.02, status="service_drop")]
    r = _result(s16_cfg, ws)
    plan = r["overview"]["display"]
    lists = [plan["attention"], plan["notable"], plan["others"], plan["not_checked"]]
    flat = [i for x in lists for i in x]
    assert sorted(flat) == sorted(w["index"] for w in ws) and len(flat) == len(set(flat))  # each wire exactly once
    assert plan["attention"] == [7]  # the power line above 3 %
    assert plan["notable"] == [47, 59, 40]  # telecom beyond 5 %, by conservative lower bound
    assert plan["others"][:2] == [62, 3] and plan["not_checked"] == [9]
    c = r["overview"]["counts"]
    assert c["flagged"] == len(plan["attention"]) == 1 and c["notable"] == 3
    assert r["overview"]["include_default"] == plan["attention"]
    cz = r["overview"]["citizen"]
    # S17: the heading is the pole-level result, worded from the aggregation
    assert cz["title"] == "1 power-line span exceeds the 3% sag reference" and "professional inspection" in cz["text"]


def test_an_uncertain_large_number_does_not_outrank_a_precise_one(s16_cfg):
    precise = _wire(s16_cfg, 1, 0.070, unc=0.005)  # lower bound 6.5 %
    shaky = _wire(s16_cfg, 2, 0.120, unc=0.080)  # lower bound 4.0 %
    plan = display_plan([precise, shaky], {w["index"]: wire_finding(w) for w in (precise, shaky)})
    assert plan["notable"] == [1, 2]


def test_telecom_beyond_typical_is_notable_never_attention(s16_cfg):
    f = wire_finding(_wire(s16_cfg, 1, 0.14, unc=0.02))
    assert f["notable"] and not f["flagged"] and f["priority"]["group"] == "notable" and not f["include_default"]
    r = _result(s16_cfg, [_wire(s16_cfg, 1, 0.14, unc=0.02), _wire(s16_cfg, 2, 0.01)])
    cz = r["overview"]["citizen"]
    assert r["overview"]["counts"]["flagged"] == 0 and cz["title"] == "No threshold exceedance detected"
    assert "1 telecom or fiber cable is above the typical sag range" in cz["text"] and cz["status_label"] == "Screened"


def test_sentences_follow_each_wires_own_evidence(shipped_cfg):
    # S17: each sentence states the wire's own ratio and class; a range that includes a boundary is said so
    clear = wire_finding(_wire(shipped_cfg, 1, 0.141, unc=0.02))["plain"]
    unsure = wire_finding(_wire(shipped_cfg, 2, 0.058, unc=0.024))["plain"]
    kinked = wire_finding(_wire(shipped_cfg, 3, 0.03, flags=["kink_split"]))["plain"]
    assert clear != unsure and "14.1%" in clear and "5.8%" in unsure
    assert "measurement range" in unsure and "measurement range" not in clear
    assert "split where it meets another wire" in kinked
    for t in (clear, unsure, kinked):
        assert "3%" not in t and "tension" not in t.lower() and "safe" not in t.lower()


# ----------------------------------------------------------------------------- per-reference summary
def test_summary_lines_count_each_reference_separately(client, scene):
    aid = upload(client, scene.rgb, scene.labels)
    wait_done(client, aid)
    r = client.get(f"/api/analysis/{aid}/result").json()
    text = " ".join(r["summary_lines"])
    per = {}
    for w in r["wires"]:
        c = w.get("comparison") or {}
        if c.get("position"):
            per[c["reference_id"]] = per.get(c["reference_id"], 0) + 1
    for rid, n in per.items():
        label = next(x["label"] for x in r["comparison"]["by_reference"] if x["id"] == rid)
        assert f"{n} compared with the {label}" in text
    assert "applies to" not in text  # the old single-reference sentence
    assert r["interpretation"]["references_now"] == [x["id"] for x in r["comparison"]["by_reference"]]


# ----------------------------------------------------------------------------- contacts and location
@pytest.mark.parametrize("barangay,city", [(None, "Dasmariñas City"), (None, "City of Dasmariñas"),
                                           (None, "dasmarinas"), ("Brgy. Salitran II", None),
                                           ("Barangay Salitran II", "Dasmariñas City"), ("salitran 2", "dasmarinas city")])
def test_every_spelling_of_a_city_resolves(barangay, city):
    from app.services.contacts import city_of
    assert city_of(barangay, city) == "City of Dasmariñas"


def test_contacts_for_dasmarinas_include_the_utility_and_fit_the_wire_types(app_state):
    from app.services import contacts
    with app_state.db.session() as s:
        d = contacts.suggest(s, None, "Dasmariñas City", {"power-distribution-lines"})
        orgs = [c["organization"] for c in d["contacts"]]
        assert any("Meralco" in o for o in orgs) and "not in the directory" not in d["note"]
        assert d["place"]["resolved"] and d["contacts"][0]["org_type"] == "emergency"
        util = next(c for c in d["contacts"] if c["org_type"] == "electric_utility")
        tel = next(c for c in d["contacts"] if c["org_type"] == "telecom_provider")
        assert util["relevant"] and not tel["relevant"]
        assert d["contacts"].index(util) < d["contacts"].index(tel)
        none = contacts.suggest(s, None, None)
        assert not none["place"]["resolved"] and "Add the barangay or city" in none["note"]
        assert "not in the directory" not in none["note"]  # a missing location is not a missing utility
        for c in d["contacts"]:
            assert "S14" not in (c.get("instructions") or "") and "source_note" not in c


def test_a_barangay_from_another_city_is_reported_not_corrected():
    from app.services.contacts import location_check
    bad = location_check("Salitran II", "Imus")
    assert bad["consistent"] is False and "City of Dasmariñas" in bad["message"]
    assert location_check("Salitran II", "Dasmariñas City")["consistent"] is True
    assert location_check(None, "Imus")["consistent"] is None


# ----------------------------------------------------------------------------- measurement engineering
def test_calibrated_band_grows_for_short_and_poorly_fitted_traces(s16_cfg):
    from app.services.analysis import uncertainty90
    long_clean = uncertainty90(1 / 800, 0.001, s16_cfg)
    short_clean = uncertainty90(1 / 40, 0.001, s16_cfg)
    long_rough = uncertainty90(1 / 800, 0.015, s16_cfg)
    assert long_clean < short_clean and long_clean < long_rough
    assert long_rough == pytest.approx(0.05804 / 800 + 3.30469 * 0.015, abs=1e-5)
    assert uncertainty90(None, 0.01, s16_cfg) is None


def test_continuation_path_stays_on_one_wire_through_a_crossing():
    import cv2

    from app.pipeline.skeleton import extract_centerline
    from app.pipeline.span_fit import fit_span
    m = np.zeros((400, 800), np.uint8)
    x = np.arange(50, 750)
    y = (100 + 0.0004 * (350 ** 2 - (x - 400) ** 2)).astype(int)  # 7 % sag
    for a, b in zip(x, y, strict=True):
        cv2.circle(m, (int(a), int(b)), 2, 1, -1)
    cv2.line(m, (200, 20), (620, 380), 1, 4)  # a straight wire crossing it
    cl = extract_centerline(m > 0, 30, 20, path_rule="continuation")
    assert cl.points[:, 0].min() < 60 and cl.points[:, 0].max() > 740  # the whole sagging wire
    assert fit_span(cl.points, image_size=(800, 400)).sag_ratio == pytest.approx(0.07, abs=0.004)
    plain = np.zeros((100, 400), bool)
    plain[50, 10:390] = True
    a = extract_centerline(plain, 30, 20, path_rule="continuation").points
    b = extract_centerline(plain, 30, 20).points
    assert np.array_equal(a, b)  # no junction: the same path as before


def test_field_splits_are_deterministic_and_keep_groups_together(tmp_path):
    from ml.field_dataset import _UF, split_of
    assert split_of("pole:P-1") == split_of("pole:P-1")
    shares = [split_of(f"g{i}") for i in range(3000)]
    assert 0.65 < shares.count("train") / 3000 < 0.75 and shares.count("test") > 300
    uf = _UF()
    uf.union("a", "pole:1")
    uf.union("b", "pole:1")
    uf.union("c", "b")
    assert len({uf.find(x) for x in "abc"}) == 1
