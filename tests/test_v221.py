"""v2.21: barangay validation, coordinate checks, dispatch route PDF, sag thresholds, assistant intent routing."""

from __future__ import annotations

import pytest

from app.assistant.intent import classify
from app.services import barangay, geo
from tests.conftest import ORIGIN, upload, wait_done


@pytest.mark.parametrize("typed,official", [("anabu 2-D", "Anabu II-D"), ("Brgy. Salitran 3", "Salitran III"),
                                            ("malagasang ii-c", "Malagasang II-C"), ("joanna", None),
                                            ("Test Barangay", None), ("", None)])
def test_barangay_matches_the_official_list_only(typed, official):
    assert barangay.resolve(typed) == official


@pytest.mark.parametrize("lat,lon,ok", [(14.3677, 120.9306, True), (0, 0, False), (120.93, 14.36, False),
                                        (10.0, 100.0, False), (None, 120.9, False), (91, 0, False)])
def test_coordinate_checks(lat, lon, ok):
    assert (geo.problem(lat, lon) is None) == ok


def test_pin_endpoint_rejects_unusable_points_and_barangay_names(client, scene):
    aid = upload(client, scene.rgb, scene.labels, pole_code="PIN-1")
    wait_done(client, aid)
    bad = client.put("/api/admin/poles/PIN-1/location", json={"latitude": 0, "longitude": 0}, headers=ORIGIN)
    assert bad.status_code == 422
    good = client.put("/api/admin/poles/PIN-1/location", json={"latitude": 14.3677, "longitude": 120.9306}, headers=ORIGIN)
    assert good.status_code == 200
    assert client.put("/api/admin/poles/PIN-1/barangay", json={"barangay": "joanna"}, headers=ORIGIN).status_code == 422
    r = client.put("/api/admin/poles/PIN-1/barangay", json={"barangay": "malagasang 2-c"}, headers=ORIGIN)
    assert r.json()["barangay"] == "Malagasang II-C"
    assert [x["name"] for x in client.get("/api/admin/barangays?q=malagasang ii").json()][:1] == ["Malagasang II-A"]


def test_route_pdf_lists_located_stops_in_order(client, scene):
    for code, lat in (("RT-1", 14.36), ("RT-2", 14.37)):
        a = upload(client, scene.rgb, scene.labels, pole_code=code, latitude=lat, longitude=120.93)
        wait_done(client, a)
    r = client.get("/api/admin/routes/pdf?stops=RT-2,RT-1,NOPE")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(r.content)
    text = " ".join(doc[0].get_textpage().get_text_range().split())
    doc.close()
    assert text.index("RT-2") < text.index("RT-1") and "straight-line" in text and "NOPE" not in text
    assert client.get("/api/admin/routes/pdf?stops=NOPE").status_code == 422


@pytest.mark.every_class_assessed
def test_sag_bands_follow_the_configured_crs_levels(client, scene):
    aid = upload(client, scene.rgb, scene.labels)
    wait_done(client, aid)
    for w in client.get(f"/api/analysis/{aid}/result").json()["wires"]:
        if w["status"] == "scored" and w.get("sag_bands"):
            b = w["sag_bands"]
            level = "high" if w["sag_ratio"] >= b["critical_from"] else "medium" if w["sag_ratio"] >= b["minor_from"] else "low"
            assert level == w["level"]


@pytest.mark.parametrize("q,kind,section", [
    ("How can I go to the poles?", "navigation", "nav-poles"), ("how can i go to the poles", "navigation", "nav-poles"),
    ("Who made you?", "identity", "authors"), ("who made u", "identity", "authors"),
    ("Who developed this system?", "identity", "authors"), ("What can you do?", "assistant", "assistant"),
    ("How do I build a dispatch route?", "workflow", "route-build"), ("optimize the route", "workflow", "route-build"),
    ("Where can I find the inspection reports?", "navigation", "nav-reports"),
    ("How do I pin a pole without GPS?", "workflow", "nav-pinning"),
])
def test_intent_routes_before_keyword_retrieval(q, kind, section):
    r = classify(q)
    assert r.kind == kind and r.sections[0] == section


def test_assistant_answers_navigation_with_steps_and_real_links(client, monkeypatch):
    monkeypatch.setattr(client.state.settings, "assistant_provider", "local")
    r = client.post("/api/assistant/ask", json={"question": "How can I go to the poles?", "page": "/admin/map"}).json()
    assert r["intent"] == "navigation" and "1. Open **Map & Dispatch**" in r["answer"]
    assert r["nav"] == [{"label": "Map & Dispatch", "href": "/admin/map"}] and r["sources"][0]["link"] == "/admin/map"
    r = client.post("/api/assistant/ask", json={"question": "Who made you?"}).json()
    assert "Canila, Tubianosa and Villamar" in r["answer"]
    r = client.post("/api/assistant/ask", json={"question": "poles"}).json()
    assert r["mode"] == "clarify" and r["suggest"]
    r = client.post("/api/assistant/ask", json={"question": "Why does the model miss telecom wires?"}).json()
    assert r["sources"][0]["id"] == "telecom"


def test_every_frontend_script_parses_as_a_module():
    """A template-literal slip once broke the whole result page; check every asset as an ES module."""
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    for f in sorted((Path(__file__).resolve().parents[1] / "app" / "web" / "assets").glob("*.js")):
        r = subprocess.run([node, "--input-type=module", "--check"], input=f.read_bytes(), capture_output=True)
        assert r.returncode == 0, f"{f.name}: {r.stderr.decode()[:300]}"


def test_pinning_one_pole_never_renumbers_the_others(client, scene):
    codes = ["LB-1", "LB-2", "LB-3"]
    for i, c in enumerate(codes):
        kw = {"latitude": 14.36 + i / 1000, "longitude": 120.93} if c != "LB-1" else {}
        wait_done(client, upload(client, scene.rgb, scene.labels, pole_code=c, **kw))
    before = {r["pole_code"]: r["label"] for r in client.get("/api/admin/inspections?per_page=100").json()["items"]}
    assert before["LB-1"].startswith("UNP-") and before["LB-2"].startswith("LOC-")
    client.put("/api/admin/poles/LB-1/location", json={"latitude": 14.37, "longitude": 120.94}, headers=ORIGIN)
    after = {r["pole_code"]: r["label"] for r in client.get("/api/admin/inspections?per_page=100").json()["items"]}
    assert after["LB-2"] == before["LB-2"] and after["LB-3"] == before["LB-3"]
    assert after["LB-1"].startswith("LOC-") and after["LB-1"] not in (after["LB-2"], after["LB-3"])


def test_system_page_is_removed(client, anon):
    """v2.23: no System page. Its addresses are not served, its scripts are gone, nothing links to it; the backend
    capabilities it displayed stay available to administrators (tests/test_v219_portal.py SYSTEM_GET)."""
    for p in ("/admin/system", "/admin/system/", "/admin/system/records", "/admin/system/evaluation"):
        assert client.get(p).status_code == 404 and anon.get(p).status_code == 404, p
    for gone in ("admin-system.js", "evaluation.js", "sites.js"):
        assert client.get(f"/assets/{gone}").status_code == 404, gone
    html = client.get("/admin").text
    js = client.get("/assets/admin.js").text + client.get("/assets/assistant.js").text
    assert "/admin/system" not in html and "/admin/system" not in js and "nav-system" not in html
    assert "Technical exports" in js and "/api/admin/exports/wires.csv" in js  # moved to Inspections
    assert client.get("/assets/admin-diagnostics.js").status_code == 200
    r = client.post("/api/assistant/ask", json={"question": "Where are the technical exports?"}).json()
    assert all("/admin/system" not in (n.get("href") or "") for n in r.get("nav") or [])
    assert "/admin/system" not in r["answer"]


def test_failed_inspections_are_never_rerun_automatically(client):
    """v2.23: the status poller used to re-queue every failed inspection when processing became available and remount
    the page; historical inspections are now re-run only by an explicit Retry on the inspection."""
    js = client.get("/assets/status.js").text
    assert "/retry" not in js and "wf:refresh" not in js


def test_list_views_do_not_decode_wire_geometry():
    """v2.23: list endpoints load only the scalar detection columns (records.list_columns)."""
    from app.services.records import list_columns
    names = {c.key for c in list_columns()}
    assert {"status", "level", "crs"} <= names and not names & {"polygons_json", "centerline_json", "curve_json", "fit_json"}


@pytest.mark.parametrize("path", ["/api/admin/overview", "/api/admin/poles", "/api/admin/poles/P-1", "/api/admin/records",
                                  "/api/dashboard/summary", "/api/dashboard/map", "/api/dashboard/sensitivity",
                                  "/api/evaluation/metrics", "/api/analysis"])
def test_superseded_endpoints_are_removed(client, path):
    """v2.23 release audit: endpoints with no consumer, superseded by /api/admin/dashboard, /inspections and the
    inspection context, were removed (the technical PDF still computes the weight sensitivity internally)."""
    assert client.get(path).status_code in (404, 405)


def test_migration_backup_restores_to_an_identical_database(client, scene, tmp_path):
    """v2.23 research follow-up: a backup is only evidence of recoverability once it has been restored. The
    application's backup (SQLite online backup) is restored into a new file and must match the original exactly.
    It covers the database only; uploaded photos, renders and reports are separate files (thesis reference §9.2)."""
    import sqlite3

    from app.core.database import backup_before_migration

    wait_done(client, upload(client, scene.rgb, scene.labels, pole_code="BK-1"))
    bk = backup_before_migration(client.state.db.engine)
    restored = tmp_path / "restored.db"
    with sqlite3.connect(bk) as a, sqlite3.connect(restored) as b:
        a.backup(b)

    def facts(p):
        c = sqlite3.connect(p)
        tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        out = (c.execute("PRAGMA integrity_check").fetchone()[0], c.execute("PRAGMA foreign_key_check").fetchall(),
               {t: c.execute(f'SELECT * FROM "{t}" ORDER BY 1').fetchall() for t in tables})
        c.close()
        return out

    original = facts(client.state.db.engine.url.database)
    assert facts(restored) == original
    assert original[0] == "ok" and original[1] == [] and len(original[2]["detections"]) > 0


def test_abandoned_wires_are_not_held_to_the_telecom_operating_limit(cfg):
    """v2.23 research follow-up: no engineering criteria exist for abandoned wires, so they are measured but not
    assessed against the provisional telecom limit (reason no_reference_for_class, worded for both classes)."""
    from app.services.reasons import REASONS_SHORT

    assert cfg.crs.assessed_classes == ["telecom-cable"]
    assert "power lines" not in REASONS_SHORT["no_reference_for_class"].lower()
