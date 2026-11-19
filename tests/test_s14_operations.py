"""S14: provider directory, location-based suggestions, assignment / review bulk actions and the operations overview."""

from __future__ import annotations

from app.models.orm import Contact
from tests.conftest import upload, wait_done

OK = {"organization": "Test Electric Co", "org_type": "electric_utility", "cities": ["City of Imus"],
      "phone": "(046) 123-4567", "source_url": "https://example.gov.ph/contacts", "verification": "verified"}


def test_seed_rows_are_sourced_and_never_invent_numbers(client):
    with client.state.db.session() as s:
        rows = s.query(Contact).all()
    assert {r.seed_key for r in rows} >= {"ph-911", "meralco", "pldt", "globe", "converge", "dasma-city"}
    for r in rows:
        if r.verification != "unverified":
            assert r.source_url and (r.phone or r.url) and r.last_verified
        else:
            assert r.phone is None  # no number without a source


def test_public_suggestions_exclude_unverified_and_say_they_are_suggestions(anon, scene):
    aid = upload(anon, scene.rgb, scene.labels, barangay="Salitran III", city="Dasmariñas")
    wait_done(anon, aid)
    out = anon.get(f"/api/analysis/{aid}/contacts").json()
    assert out["place"]["city"] == "City of Dasmariñas"
    names = [c["organization"] for c in out["contacts"]]
    assert names[0] == "Emergency 911 National Hotline" and "Your barangay hall" in names
    assert any(n.startswith("Meralco") for n in names)
    assert not any(c.get("verification") == "unverified" for c in out["contacts"])
    assert "not confirmation of who owns a wire" in out["note"]
    # S18: Converge's channel was verified on its official site (19 Nov 2026); DITO's could not be, so it stays out
    assert "DITO Telecommunity" not in names and "City Government of Dasmariñas" not in names


def test_suggestions_without_a_place_offer_only_national_and_barangay(anon, scene):
    aid = upload(anon, scene.rgb, scene.labels)
    wait_done(anon, aid)
    out = anon.get(f"/api/analysis/{aid}/contacts").json()
    assert out["place"]["city"] is None and not any(c["org_type"] == "electric_utility" for c in out["contacts"])
    assert "Add the barangay or city" in out["note"]


def test_directory_is_admin_maintained_and_validated(client, anon):
    assert anon.get("/api/admin/contacts").status_code == 401
    r = client.post("/api/admin/contacts", json=OK)
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert client.post("/api/admin/contacts", json=OK).status_code == 409  # duplicate
    bad = [{**OK, "organization": "X1", "phone": "call us"}, {**OK, "organization": "X2", "source_url": None},
           {**OK, "organization": "X3", "url": "http://insecure.example"}, {**OK, "organization": "X4", "cities": []}]
    for b in bad:
        assert client.post("/api/admin/contacts", json=b).status_code == 422
    assert client.put(f"/api/admin/contacts/{cid}", json={**OK, "phone": "16211"}).json()["phone"] == "16211"
    assert client.delete(f"/api/admin/contacts/{cid}").status_code == 204
    assert cid not in [c["id"] for c in client.get("/api/admin/contacts").json()["items"]]


def test_inspector_cannot_change_the_directory(client):
    from fastapi.testclient import TestClient

    from scripts.create_admin import upsert_admin
    from tests.conftest import ORIGIN
    upsert_admin(client.state.db, "field.tech", "another long passphrase", display_name="J. Cruz", role="inspector")
    c = TestClient(client.app, headers=ORIGIN)
    assert c.post("/api/auth/login", json={"username": "field.tech", "password": "another long passphrase"}).status_code == 200
    assert c.get("/api/admin/contacts").status_code == 200
    assert c.post("/api/admin/contacts", json={**OK, "organization": "Y"}).status_code == 403


def test_operations_overview_assign_and_mark_reviewed(client, scene):
    aid = upload(client, scene.rgb, scene.labels, barangay="Salitran III", city="Dasmariñas")
    wait_done(client, aid)
    ov = client.get("/api/admin/operations").json()
    m = ov["metrics"]
    assert m["registered_poles"] >= 1 and set(m) >= {"flagged", "processing_failed", "incomplete", "reviewed"}
    row = next(r for r in ov["queue"] if r["id"] == aid)
    assert row["op_status"] in ("flagged", "incomplete", "awaiting_review") and row["op_reason"]
    assert row["place"]["label"] == "Brgy. Salitran III, City of Dasmariñas"
    me = client.get("/api/admin/users").json()[0]
    assert client.post("/api/admin/inspections/bulk", json={"ids": [aid], "action": "assign", "assignee_id": me["id"]}).json()["updated"] == 1
    row = next(r for r in client.get("/api/admin/operations").json()["queue"] if r["id"] == aid)
    assert row["assignee"]["name"] == me["name"]
    assert client.post("/api/admin/inspections/bulk", json={"ids": [aid], "action": "mark_reviewed"}).json()["updated"] == 1
    ov = client.get("/api/admin/operations").json()
    assert aid not in [r["id"] for r in ov["queue"]] and ov["metrics"]["reviewed"] >= 1
    team = ov["activity"]["team"]
    assert any(e["action"] == "inspection.assigned" for e in team)
    assert all(e["actor"] != "System" for e in team)


def test_assign_requires_a_real_account(client, scene):
    aid = upload(client, scene.rgb, scene.labels)
    wait_done(client, aid)
    r = client.post("/api/admin/inspections/bulk", json={"ids": [aid], "action": "assign", "assignee_id": 99999})
    assert r.status_code == 422


def test_csv_export_can_be_limited_to_the_selected_rows(client, scene):
    a = upload(client, scene.rgb, scene.labels)
    b = upload(client, scene.rgb, scene.labels)
    wait_done(client, a)
    wait_done(client, b)
    text = client.get(f"/api/admin/exports/inspections.csv?ids={a}").text
    assert a in text and b not in text and "Status reason" in text
    assert client.get("/api/admin/exports/inspections.csv?ids=not-an-id").status_code == 422
