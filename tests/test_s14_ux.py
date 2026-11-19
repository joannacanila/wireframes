"""S14 UX overhaul (register D-30): the sag meter is decided by the server for every measured wire, power and telecom
alike; the 3% reference is never applied to telecom / fiber; the admin list and dashboard speak the operational
statuses; the report uses the page's words."""

from __future__ import annotations

import io

from app.core.config import ROOT_DIR
from app.services.comparison import compare_wire, wire_states
from app.services.outcome import FINDING_TEXT, meter_state, wire_finding
from tests.conftest import upload, wait_done
from tests.test_s12_results_ui import _cfg, _w


def _wire(cfg, **kw):
    w = _w(**kw)
    w["comparison"] = compare_wire(w, cfg)
    w["states"] = wire_states(w, cfg, w["comparison"])
    return w


# ----------------------------------------------------------------------------- the meter kind (server)
def test_power_line_gets_the_reference_meter_with_a_non_legal_note(s16_cfg):
    m = meter_state(_wire(s16_cfg, ratio=0.049, res=0.003))
    assert m["kind"] == "reference"
    assert "3.0%" in m["note"] and "power lines" in m["note"] and "not a legal safety limit" in m["note"]


def test_telecom_and_fiber_get_their_own_bands_and_never_the_3_percent(s16_cfg):
    """S15 (D-31): telecom / fiber is compared with its own contextual bands (2% / 5%); the 3% power value is never
    used. S16 (D-34): beyond 5% is "notable" (worth a closer look), never attention / Needs review, because that rule
    was right in 1 of 11 labelled cases. With the telecom reference off, the S14 neutral meter returns."""
    for ratio, zone, notable in ((0.01, "below", False), (0.03, "at", False), (0.049, "at", False), (0.117, "above", True)):
        w = _wire(s16_cfg, ratio=ratio, res=0.003, cls="telecom-cable")
        m = meter_state(w)
        assert m["kind"] == "reference" and m["banded"] and m["zone"] == zone, ratio
        assert "3%" not in m["note"] and "3.0%" not in m["note"]
        f = wire_finding(w)
        assert f["flagged"] is False and f["notable"] is notable and f["meter"] == m and f["sag"]["ratio"] == ratio
        assert f["priority"]["group"] == ("notable" if notable else "measured")
        assert w["comparison"]["reference"] == 0.02 and w["comparison"]["upper_boundary"] == 0.05
    off = s16_cfg.model_copy(update={"telecom_reference": s16_cfg.telecom_reference.model_copy(update={"enabled": False})})
    m = meter_state(_wire(off, ratio=0.049, res=0.003, cls="telecom-cable"))
    assert m["kind"] == "neutral" and m["reason"] == "no_reference_for_telecom" and "3" not in m["note"]


def test_uncertain_and_abandoned_types_are_neutral_and_unmeasured_wires_get_no_meter(s16_cfg):
    unk = meter_state(_wire(s16_cfg, ratio=0.04, res=0.003, class_status="uncertain"))
    assert unk["kind"] == "neutral" and unk["reason"] == "type_uncertain"
    ab = meter_state(_wire(s16_cfg, ratio=0.04, res=0.003, cls="abandoned-wire"))
    assert ab["kind"] == "neutral"
    nm = _wire(s16_cfg, ratio=None, res=None, status="fit_failed", reason_code="residual_too_large", fit=None)
    assert meter_state(nm)["kind"] == "none" and wire_finding(nm)["sag"] is None  # missing is never a zero


def test_a_validated_reference_added_later_switches_the_meter_without_page_changes(s16_cfg):
    """If a reference is configured for telecom later, the same wire gets the reference meter: the page only reads
    finding.meter.kind and the stored comparison's zones."""
    alt = _cfg(s16_cfg, sag_ratio=0.015, lower_boundary=0.012, upper_boundary=0.018, status="manufacturer",
               classes=["telecom-cable"], label="1.5% installation sag for ADSS cable (manufacturer)",
               tolerance_basis="manufacturer installation tolerance")
    m = meter_state(_wire(alt, ratio=0.016, res=0.001, cls="telecom-cable"))
    assert m["kind"] == "reference" and "manufacturer 1.5%" in m["note"] and "this wire type" in m["note"]


def test_pages_take_the_meter_kind_from_the_server_and_hold_no_thresholds():
    field = (ROOT_DIR / "app/web/assets/field.js").read_text()
    admin = (ROOT_DIR / "app/web/assets/admin.js").read_text()
    rv = (ROOT_DIR / "app/web/assets/result-view.js").read_text()
    # S18: one meter for every page (result-view.js); the band is the server's finding.meter_level and the zones are
    # the stored comparison's boundaries; no threshold is written in the browser
    assert "result-view.js" in field and "result-view.js" in admin and "function sagMeter(" not in admin
    body = rv[rv.index("export function sagMeter("):rv.index("export function riskChip(")]
    assert "f.meter_level" in body and "c.lower_boundary" in body and "c.upper_boundary" in body
    for num in ("0.02", "0.03", "0.05", "2%", "3%"):
        assert num not in body, num
    assert FINDING_TEXT["flagged"] == "Reference threshold exceeded"  # S17 (D-37) canonical class wording
    for old in ("Flagged for review", "Unable to fully assess"):
        assert old not in field, old


# ----------------------------------------------------------------------------- admin list and dashboard
def test_inspection_list_filters_on_operational_status_and_assignee(client, scene):
    aid = upload(client, scene.rgb, scene.labels, barangay="Salitran III", city="Dasmariñas")
    wait_done(client, aid)
    d = client.get("/api/admin/inspections").json()
    row = next(r for r in d["items"] if r["id"] == aid)
    st = row["op_status"]
    assert set(d["op_tabs"]) == {"all", "attention", "flagged", "incomplete", "failed", "reviewed"}  # S16: Incomplete tab
    inc = client.get("/api/admin/inspections?op_tab=incomplete").json()
    assert inc["matched"] == d["op_tabs"]["incomplete"] and all(r["op_status"] == "incomplete" for r in inc["items"])
    assert aid in {r["id"] for r in client.get(f"/api/admin/inspections?status={st}").json()["items"]}
    assert aid not in {r["id"] for r in client.get("/api/admin/inspections?op_tab=reviewed").json()["items"]}
    assert aid in {r["id"] for r in client.get("/api/admin/inspections?op_tab=attention").json()["items"]}
    assert aid in {r["id"] for r in client.get("/api/admin/inspections?assignee=none").json()["items"]}
    me = client.get("/api/admin/users").json()[0]
    client.post("/api/admin/inspections/bulk", json={"ids": [aid], "action": "assign", "assignee_id": me["id"]})
    assert aid in {r["id"] for r in client.get(f"/api/admin/inspections?assignee={me['id']}").json()["items"]}
    assert aid not in {r["id"] for r in client.get("/api/admin/inspections?assignee=none").json()["items"]}
    assert client.get("/api/admin/inspections?assignee=bob").status_code == 422
    ov = client.get("/api/admin/operations").json()
    assert ov["me"] == me["id"] and ov["mine"] >= 1 and any(r["id"] == aid for r in ov["recent"])


def test_report_uses_the_page_words_and_draws_a_meter_for_every_measured_wire(client, scene):
    import pypdfium2 as pdfium
    aid = upload(client, scene.rgb, scene.labels)
    wait_done(client, aid)
    r = client.get(f"/api/analysis/{aid}/result").json()
    every = ",".join(str(w["index"]) for w in r["wires"])
    pdf = client.get(f"/api/reports/{aid}/pdf?include={every}")
    assert pdf.status_code == 200
    doc = pdfium.PdfDocument(io.BytesIO(pdf.content))
    text = "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    assert "Wires identified" in text and "Need review" in text and "Estimated sag" in text
    assert "Flagged for review" not in text and "Unable to fully assess" not in text
