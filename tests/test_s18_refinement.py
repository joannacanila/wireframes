"""S18 refinement: admin inspection cleanup, wire navigation, mask legend, review validation, archive-based deletion,
barangay clearing, real activity, sort / filter controls, naming conventions, admin location capture, citizen cleanup,
loading facts, the five-column provider directory and the "Other" metrics."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.orm import AuditEvent
from app.services import contacts
from app.services.diagnostics import name_wires
from app.services.outcome import report_id, wire_id
from tests.conftest import upload, wait_done
from tests.test_v219_portal import inspector  # noqa: F401, F811  (fixture)

ASSETS = Path(__file__).resolve().parents[1] / "app" / "web" / "assets"
NODE = shutil.which("node")


def _done(client, scene, code):
    aid = upload(client, scene.rgb, scene.labels, pole_code=code)
    wait_done(client, aid)
    return aid


def _audit(client, aid=None, target=None):
    with client.state.db.session() as s:
        q = select(AuditEvent)
        if aid:
            q = q.where(AuditEvent.analysis_id == aid)
        if target:
            q = q.where(AuditEvent.target == target)
        return [(e.action, e.detail) for e in s.scalars(q.order_by(AuditEvent.id)).all()]


def _ids(client, **params):
    return {r["id"] for r in client.get("/api/admin/inspections", params={"per_page": 500, **params}).json()["items"]}


# --------------------------------------------------------------------------- 5.1 Other documented decision
@pytest.mark.parametrize("text", ["", "   ", "abc", "  ..  ...  "])
def test_other_decision_needs_a_written_decision(client, scene, text):
    aid = _done(client, scene, f"S18R-OTH{len(text)}")
    r = client.post(f"/api/admin/inspections/{aid}/decision",
                    json={"decision": "other", "rationale": "Checked on site with the city.", "other_text": text})
    assert r.status_code == 422 and "Describe the documented decision" in r.text
    assert client.get(f"/api/admin/inspections/{aid}/review").json()["status"] != "REVIEW_COMPLETE"


def test_other_decision_text_is_saved_as_the_decision_and_audited(client, scene):
    aid = _done(client, scene, "S18R-OTHOK")
    r = client.post(f"/api/admin/inspections/{aid}/decision",
                    json={"decision": "other", "rationale": "Pole is on private land.",
                          "other_text": "  Refer to the  city engineering office  "})
    assert r.status_code == 201, r.text
    d = r.json()["decision"]
    assert d["code"] == "other" and d["label"] == "Other: Refer to the city engineering office"
    fin = [x for a, x in _audit(client, aid) if a == "review.finalized"]
    assert fin and fin[0]["new"]["label"] == "Other: Refer to the city engineering office"


# --------------------------------------------------------------------------- 5.2 barangay assign / clear
def test_barangay_can_be_cleared_and_reassigned_without_touching_the_location(client, scene):
    aid = _done(client, scene, "S18R-BRGY")
    assert client.put("/api/admin/poles/S18R-BRGY/location", json={"latitude": 14.33, "longitude": 120.94}).status_code == 200
    assert client.put("/api/admin/poles/S18R-BRGY/barangay", json={"barangay": "Salitran I"}).status_code == 200
    assert client.delete("/api/admin/poles/S18R-BRGY/barangay").json() == {"pole_code": "S18R-BRGY", "barangay": None}
    res = client.get(f"/api/analysis/{aid}/result").json()
    assert res["pole"]["barangay"] is None and res["pole"]["location"]["lat"] == pytest.approx(14.33)  # GPS kept
    assert client.get(f"/api/analysis/{aid}").status_code == 200  # the inspection is untouched
    assert client.put("/api/admin/poles/S18R-BRGY/barangay", json={"barangay": "Salitran II"}).json()["barangay"] == "Salitran II"
    acts = [a for a, _ in _audit(client, target="S18R-BRGY")]
    assert acts.count("barangay.set") == 2 and acts.count("barangay.unset") == 1


# --------------------------------------------------------------------------- 6 deletion = audited archive
def test_delete_inspection_archives_restorably_and_keeps_everything(client, scene):
    aid = _done(client, scene, "S18R-ARCH")
    assert aid in _ids(client)
    assert client.post(f"/api/admin/inspections/{aid}/archive", json={"reason": "  "}).status_code == 422
    r = client.post(f"/api/admin/inspections/{aid}/archive", json={"reason": "Duplicate upload of the same photo"})
    assert r.status_code == 200 and r.json()["archived"]["reason"] == "Duplicate upload of the same photo"
    assert aid not in _ids(client) and aid not in _ids(client, include_test=True)  # left out everywhere
    assert client.get(f"/api/analysis/{aid}/result").status_code == 200  # nothing removed
    assert client.get(f"/api/analysis/{aid}/image").status_code == 200
    ctx = client.get(f"/api/admin/inspections/{aid}/context").json()
    assert ctx["archived"]["by"] and ctx["can"]["restore"] and not ctx["can"]["delete"] and not ctx["can"]["review"]
    assert client.post(f"/api/admin/inspections/{aid}/archive", json={"reason": "again"}).status_code == 409
    assert client.post(f"/api/admin/inspections/{aid}/restore").status_code == 200
    assert aid in _ids(client)
    acts = [a for a, _ in _audit(client, aid)]
    assert "inspection.archived" in acts and "inspection.restored" in acts
    from app.services import audit
    with client.state.db.session() as s:
        feed = audit.activity(s, 10)
    ev = {e["action"]: e for e in feed}
    assert ev["inspection.archived"]["kind"] == "archive" and not ev["inspection.archived"]["automated"]
    assert ev["inspection.restored"]["analysis_id"] == aid and ev["inspection.archived"]["actor"] == "Test Engineer"


def test_inspector_cannot_archive(inspector, client, scene):  # noqa: F811
    aid = _done(client, scene, "S18R-ARCH-I")
    assert inspector.post(f"/api/admin/inspections/{aid}/archive", json={"reason": "not mine"}).status_code == 403


# --------------------------------------------------------------------------- naming conventions
def test_wire_ids_are_stable_and_derived_from_the_report_reference(client, scene):
    aid = _done(client, scene, "S18R-WID")
    res = client.get(f"/api/analysis/{aid}/result").json()
    assert res["report_id"] == report_id(aid) == f"WF-{aid[:8].upper()}"
    assert res["wires"] and all(w["wire_id"] == f"{res['report_id']}-W{w['index']:02d}" for w in res["wires"])
    assert wire_id("WF-415C41C6", 3) == "WF-415C41C6-W03"


def test_observations_name_their_wires():
    assert name_wires([3]) == "wire 3" and name_wires([3, 5]) == "wires 3 and 5"
    assert name_wires([1, 2, 3]) == "wires 1, 2 and 3"
    assert name_wires(list(range(1, 15))).endswith("and 2 more")


def test_pdf_names_wires_like_every_page():
    from app.services.plain import span_id
    assert span_id(3) == "Wire 3"


# --------------------------------------------------------------------------- Other metrics and mask legend
def test_other_metrics_come_from_the_configuration(client, scene):
    aid = _done(client, scene, "S18R-MET")
    res = client.get(f"/api/analysis/{aid}/result").json()
    cfg = client.state.config
    by = {m["key"]: m for m in res["metrics"]}
    v = by["vni"]
    assert [b["to"] for b in v["bands"]] == [b.max for b in cfg.vni.bands]
    assert [b["label"] for b in v["bands"]] == [b.label for b in cfg.vni.bands]
    assert v["value"] == pytest.approx(res["vni"]["value"], abs=1e-3) and v["provisional"] is cfg.vni.provisional
    for k in ("what", "inputs", "how", "meaning", "reference", "cannot"):
        assert v[k], k
    if "crs" in by:  # CRS marks are the power reference boundaries x 250 (2% -> 5.0, 3% -> 7.5); no new threshold
        assert by["crs"]["marks"] == [5.0, 7.5] and by["crs"]["scale_max"] == 10.0
        assert "never decides" in by["crs"]["cannot"]


def test_mask_legend_lists_only_classes_present(client, anon, scene):
    aid = _done(client, scene, "S18R-LEG")
    res = client.get(f"/api/analysis/{aid}/result").json()
    keys = [x["key"] for x in res["mask_legend"]]
    present = {w["class"] for w in res["wires"]} | ({"Vegetation"} if res.get("vegetation") else set())
    assert set(k for k in keys if k != "uncertain") == present
    from app.core.config import CLASSES
    order = [c for c in CLASSES if c in keys]
    assert [k for k in keys if k != "uncertain"] == order  # model class order
    pub = anon.get(f"/api/analysis/{aid}/result").json()
    assert pub["mask_legend"] == res["mask_legend"] and "metrics" not in pub  # the legend is public, metrics are staff


# --------------------------------------------------------------------------- admin upload location capture
def test_device_location_is_recorded_apart_from_typed_text(client, scene):
    aid = upload(client, scene.rgb, scene.labels, pole_code="S18R-LOC", latitude="14.3294", longitude="120.9367",
                 location_method="device", location_accuracy_m="12.5", geocoded_city="Dasmariñas",
                 geocoded_barangay="Salitran I", city="Dasmariñas")
    wait_done(client, aid)
    loc = client.get(f"/api/analysis/{aid}/result").json()["pole"]["location"]
    assert loc["source"] == "manual" and loc["capture"]["method"] == "device" and loc["capture"]["accuracy_m"] == 12.5
    assert loc["capture"]["geocoded"] == {"city": "Dasmariñas", "barangay": "Salitran I"}
    from tests.conftest import jpeg_bytes
    bad = client.post("/api/analysis/upload", files={"file": ("p.jpg", jpeg_bytes(scene.rgb), "image/jpeg"),
                                                      "labels": ("p.txt", scene.labels.encode(), "text/plain")},
                      data={"latitude": "14.3", "longitude": "120.9", "location_method": "guess"})
    assert bad.status_code == 422  # an unknown capture method is rejected, never stored


# --------------------------------------------------------------------------- provider directory
def test_directory_area_never_widens_the_source():
    class C:
        def __init__(self, cities, org_type="telecom_provider"):
            self.cities, self.org_type = cities, org_type
    assert contacts.area_label(C(["national"])) == "Coverage in the study area not published"
    assert contacts.area_label(C(["province:Cavite"])) == "Cavite: province listed; towns not published"
    assert contacts.area_label(C(["City of Imus"])) == "City of Imus"
    assert contacts.area_label(C(["national"], "emergency")) == "Nationwide emergency hotline"


def test_directory_api_carries_the_update_label(client):
    d = client.get("/api/admin/contacts").json()
    assert d["updated"] == "November 19, 2026" and "City of Dasmariñas" in d["study_area"]
    assert all("area_label" in c for c in d["items"])
    assert not any(c["area_label"] == "Nationwide" for c in d["items"])


def test_directory_ui_has_five_columns_and_no_banner():
    js = (ASSETS / "admin.js").read_text()
    page = js[js.index("function mountDirectory()"):js.index("function auditDetail(")]
    head = page[page.index("<thead>"):page.index("</thead>")]
    assert [h for h in ("Organization", "Category", "Area", "Phone", "Website") if f"<th>{h}</th>" in head] == \
        ["Organization", "Category", "Area", "Phone", "Website"] and head.count("<th>") == 5
    assert "Updated as of" in page
    assert "Coverage of an area is not ownership" not in page and "not shown to the public until" not in page
    assert "Area coverage shows which companies operate here" not in js


# --------------------------------------------------------------------------- admin inspection page
def test_inspection_page_sections():
    js = (ASSETS / "admin.js").read_text()
    sheet = js[js.index("function mountInspection(id)"):]
    for gone in ("Technical diagnostics", "diagnosticsHtml", "Acquisition envelope", "Not available from one photo",
                 "Mark as test record", "more-menu"):
        assert gone not in sheet, gone
    for kept in ("Photo limitations", "Observed in the image", "Recommended steps", "otherMetricsHtml(r)",
                 "recordAdminHtml(c, row)", 'id="clear-brgy"'):
        assert kept in sheet, kept
    assert "confirmDelete(row, r)" in js and "/archive`" in js and 'method: "DELETE" }); toast.success("Inspection deleted")' not in js


def test_barangay_picker_can_always_be_closed():
    js = (ASSETS / "admin.js").read_text()
    pick = js[js.index("export function pickBarangay("):js.index("// ------------------------------------------------------------------ dashboard")]
    assert 'type="button" data-close>Cancel</button>' in pick and 'aria-label="Close without saving"' in pick
    assert "required" not in pick.split("<input id=\"pk-q\"")[1].split(">")[0]  # Cancel was blocked by "required"


def test_sort_controls_cycle_and_filters_are_separate():
    js = (ASSETS / "admin.js").read_text()
    assert "cycleSort = (sort, key, dflt) => (sort.key !== key ? { key, desc: false } : !sort.desc ? { key, desc: true } : { ...dflt })" in js
    assert js.count("cycleSort(st.sort") == 2
    assert 'colFilter("risk", "Filter by risk level"' in js and 'colFilter("review", "Filter by review status"' in js
    assert '"Clear sorting (default order)"' in js


# --------------------------------------------------------------------------- citizen page and loading facts
def test_citizen_result_has_no_method_section_or_barangay_hall_line():
    js = (ASSETS / "field.js").read_text()
    page = js[js.index("function renderResult(r) {"):js.index("const root = stages.result;")]
    assert "How this result was produced" not in page
    assert "Your barangay hall" not in js and "rv-brgy" not in js


def test_fact_pool_is_sourced():
    src = (ASSETS / "facts.js").read_text()
    n_text, n_source = src.count(" text: "), src.count("source: ")
    assert n_text >= 8 and n_text == n_source


# --------------------------------------------------------------------------- JS behaviour (Node)
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")


def _node(code: str) -> object:
    out = subprocess.run([NODE, "--input-type=module", "-e", code], capture_output=True, text=True, cwd=ASSETS, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_find_wire_resolves_one_wire_and_never_guesses():
    res = _node("""
      import { findWire } from "./result-view.js";
      const ws = [1, 2, 3, 12].map((i) => ({ index: i, wire_id: `WF-ABCDEF12-W${String(i).padStart(2, "0")}`,
        class: i < 3 ? "telecom-cable" : "power-distribution-lines", finding: { type: i < 3 ? "Telecom cable" : "Power line" } }));
      const k = (q) => { const r = findWire(q, ws); return r.kind === "one" ? r.w.index : r.kind === "many" ? r.ws.map((w) => w.index) : r.kind; };
      console.log(JSON.stringify(["3", " Wire 3 ", "w3", "#3", "03", "WIRE 12", "wf-abcdef12-w12", "WF-00000000-W03",
        "9", "telecom", "power", "", "zzz"].map(k)));""")
    assert res == [3, 3, 3, 3, 3, 12, 12, "none", "none", [1, 2], [3, 12], "empty", "none"]


@needs_node
def test_wire_refs_link_only_existing_wires():
    res = _node("""
      import { wireRefs } from "./result-view.js";
      console.log(JSON.stringify(wireRefs("Vegetation touches wires 2 and 9; wire 3 reaches the edge", [{ index: 2 }, { index: 3 }])));""")
    assert 'data-w="2"' in res and 'data-w="3"' in res and 'data-w="9"' not in res and " and 9;" in res


@needs_node
def test_fact_order_is_a_shuffle_without_repeats_and_avoids_the_last_fact():
    res = _node("""
      const store = {}; globalThis.localStorage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; } };
      const { FACTS, factOrder, rememberFact } = await import("./facts.js");
      const runs = [];
      for (let s = 0; s < 40; s++) { const o = factOrder(); runs.push(o.map((f) => f.id)); rememberFact(o[o.length - 1]); }
      const firstAfterLast = runs.slice(1).every((r, i) => r[0] !== runs[i][runs[i].length - 1]);
      console.log(JSON.stringify({ n: FACTS.length, sizes: [...new Set(runs.map((r) => new Set(r).size))],
        distinctOrders: new Set(runs.map((r) => r.join())).size, firstAfterLast }));""")
    assert res["sizes"] == [res["n"]]  # every session shows each fact once
    assert res["distinctOrders"] > 30 and res["firstAfterLast"]
