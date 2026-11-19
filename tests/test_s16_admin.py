"""S16 admin harmonisation: every dashboard count names its entity and agrees with the list it links to; a barangay
recorded under another city is listed (never corrected); directory edits record what changed."""

from __future__ import annotations

from tests.conftest import ORIGIN, upload, wait_done


def test_fleet_counts_name_their_entity_and_match_the_lists(client, scene):
    a = upload(client, scene.rgb, scene.labels, pole_code="P-S16A", barangay="Salitran II", city="Imus")
    b = upload(client, scene.rgb, scene.labels, pole_code="P-S16A", barangay="Salitran II", city="Imus")
    c = upload(client, scene.rgb, scene.labels, pole_code="P-S16B", barangay="Salitran II", city="Dasmariñas")
    for x in (a, b, c):
        wait_done(client, x)
    d = client.get("/api/admin/operations").json()
    f = d["fleet"]
    assert f["poles"] == d["metrics"]["registered_poles"] == 2 and f["inspections"] == 3  # a pole can have several
    assert f["queue"] == len(d["queue"]) and f["located"] + f["needs_pinning"] == f["poles"]
    assert f["location_mismatch"] == 1
    lst = client.get("/api/admin/inspections").json()
    assert lst["total"] == f["inspections"] and lst["mismatch"] == 2  # per inspection: both uploads of P-S16A
    mm = client.get("/api/admin/inspections?mismatch=true").json()
    assert {r["id"] for r in mm["items"]} == {a, b}
    assert all("City of Dasmariñas" in r["location_check"]["message"] for r in mm["items"])
    latest = client.get("/api/admin/inspections?latest_only=true").json()
    assert latest["matched"] == f["poles"]
    sites = client.get("/api/dashboard/priority").json()["sites"]
    assert len(sites) == f["poles"] and {s["op_status"] for s in sites} <= set(d["status_labels"])
    row = next(r for r in client.get("/api/admin/inspections?q=P-S16A").json()["items"] if r["id"] == b)
    assert row["barangay"] == "Salitran II"  # reported, not corrected


def test_directory_edit_records_the_verification_change(client):
    items = client.get("/api/admin/contacts").json()["items"]
    c = next(x for x in items if x["verification"] == "unverified")
    body = {k: c.get(k) for k in ("organization", "org_type", "cities", "barangays", "phone", "url", "assistance",
                                  "instructions", "source_url", "last_verified", "source_note", "service_area")}
    body.update(verification="secondary_source", url=body.get("url") or "https://example.org/report", source_url="https://example.org/contact", last_verified="2026-11-18")
    r = client.put(f"/api/admin/contacts/{c['id']}", json=body, headers=ORIGIN)
    assert r.status_code == 200, r.text
    ev = next(e for g in client.get("/api/admin/operations").json()["activity"].values() for e in g
              if e.get("action") == "contact.updated" or "contact" in (e.get("detail") or {}))
    det = ev.get("detail") or {}
    assert det.get("verification") == ["unverified", "secondary_source"] and "source_url" in det.get("fields", [])


def test_result_carries_one_report_id_used_by_page_and_pdf(client, scene):
    aid = upload(client, scene.rgb, scene.labels)
    wait_done(client, aid)
    r = client.get(f"/api/analysis/{aid}/result").json()
    assert r["report_id"] == f"WF-{aid[:8].upper()}"
    js = client.get("/assets/field.js").text
    assert "r.report_id" in js and "Copy message" in js and "overview.display" in js or "ov.display" in js
