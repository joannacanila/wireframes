"""S18: machine findings, administrative review and decisions, the triage rubric, and the provider directory.

Machine observations, review progress and the administrator's decision are three records; none of them is ever read
out of another. Total wires and problem wires are never interchangeable.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.models.orm import AdminDecision, AuditEvent, ReviewProgress
from app.services import contacts, review_flow, screening
from tests.conftest import ORIGIN, upload, wait_done

DECISION = {"decision": "field_inspection", "rationale": "Sag on span 1 needs a site check."}


def _row(client, aid):
    items = client.get("/api/admin/inspections", params={"per_page": 500}).json()["items"]
    return next(r for r in items if r["id"] == aid)


def _audit(client, aid):
    with client.state.db.session() as s:
        return [e.action for e in s.scalars(select(AuditEvent).where(AuditEvent.analysis_id == aid)
                                            .order_by(AuditEvent.id)).all()]


def _second_reviewer(client):
    from scripts.create_admin import upsert_admin
    upsert_admin(client.state.db, "second.rev", "second reviewer passphrase", display_name="Second Reviewer")
    c = TestClient(client.app, headers=ORIGIN)
    assert c.post("/api/auth/login", json={"username": "second.rev", "password": "second reviewer passphrase"}
                  ).status_code == 200
    return c


# --------------------------------------------------------------------------- review status transitions
def test_a_new_inspection_is_not_yet_reviewed_even_when_processed(client, scene):
    aid = upload(client, scene.rgb, scene.labels, pole_code="S18-NEW")
    wait_done(client, aid)
    row = _row(client, aid)
    assert row["review_status"] == "NOT_YET_REVIEWED" and row["decision"] is None
    assert row["machine_state"] == "processed"


def test_opening_starts_the_review_once_and_refresh_records_nothing(client, scene):
    aid = upload(client, scene.rgb, scene.labels, pole_code="S18-OPEN")
    wait_done(client, aid)
    r = client.post(f"/api/admin/inspections/{aid}/review/start").json()
    assert r["status"] == "PARTIALLY_REVIEWED" and r["recorded"] and r["started_by"] == "Test Engineer"
    again = client.post(f"/api/admin/inspections/{aid}/review/start").json()
    assert again["recorded"] is False and again["status"] == "PARTIALLY_REVIEWED"
    assert _audit(client, aid).count("review.started") == 1
    assert _row(client, aid)["review_status"] == "PARTIALLY_REVIEWED"  # opened is never complete


def test_finalize_completes_and_amend_keeps_the_previous_decision(client, scene):
    aid = upload(client, scene.rgb, scene.labels, pole_code="S18-DEC")
    wait_done(client, aid)
    client.post(f"/api/admin/inspections/{aid}/review/start")
    r = client.post(f"/api/admin/inspections/{aid}/decision", json=DECISION)
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["status"] == "REVIEW_COMPLETE" and v["decision"]["code"] == "field_inspection"
    assert v["decision"]["machine"]["detected"] == _row(client, aid)["detected"]  # what the reviewer saw is kept
    # reopen, then a different decision: the first is superseded, not overwritten
    assert client.post(f"/api/admin/inspections/{aid}/review/reopen", json={"reason": "new photo"}).json()[
        "status"] == "PARTIALLY_REVIEWED"
    v2 = client.post(f"/api/admin/inspections/{aid}/decision",
                     json={"decision": "monitoring", "rationale": "Within reference after re-check."}).json()
    assert v2["status"] == "REVIEW_COMPLETE" and v2["decision"]["code"] == "monitoring"
    assert [h["code"] for h in v2["history"]] == ["field_inspection", "monitoring"]
    assert v2["history"][0]["superseded_at"] and not v2["history"][1]["superseded_at"]
    acts = _audit(client, aid)
    assert acts.count("review.finalized") == 1 and "review.reopened" in acts and "decision.amended" in acts
    with client.state.db.session() as s:
        ev = s.scalars(select(AuditEvent).where(AuditEvent.analysis_id == aid,
                                                AuditEvent.action == "decision.amended")).one()
        assert ev.detail["previous"]["decision"] == "field_inspection" and ev.detail["new"]["decision"] == "monitoring"
    # the machine screening is unchanged by any decision
    assert _row(client, aid)["result"] == _row(client, aid)["result"]


def test_decision_validation(client, scene):
    aid = upload(client, scene.rgb, scene.labels, pole_code="S18-VAL")
    wait_done(client, aid)
    assert client.post(f"/api/admin/inspections/{aid}/decision", json={"decision": "nope", "rationale": "x"}
                       ).status_code == 422
    assert client.post(f"/api/admin/inspections/{aid}/decision", json={"decision": "monitoring", "rationale": ""}
                       ).status_code == 422
    assert client.post(f"/api/admin/inspections/{aid}/review/reopen", json={"reason": "x"}).status_code == 422
    assert _row(client, aid)["review_status"] == "NOT_YET_REVIEWED"  # a refused decision changes nothing


def test_multiple_reviewers_are_recorded_individually(client, scene):
    aid = upload(client, scene.rgb, scene.labels, pole_code="S18-MULTI")
    wait_done(client, aid)
    client.post(f"/api/admin/inspections/{aid}/review/start")
    other = _second_reviewer(client)
    r = other.post(f"/api/admin/inspections/{aid}/review/start").json()
    assert r["reviewers"] == ["Test Engineer", "Second Reviewer"] and r["started_by"] == "Test Engineer"
    other.post(f"/api/admin/inspections/{aid}/decision", json=DECISION)
    v = client.get(f"/api/admin/inspections/{aid}/review").json()
    assert v["completed_by"] == "Second Reviewer" and v["decision"]["by"] == "Second Reviewer"
    with client.state.db.session() as s:
        actors = {(e.action, e.actor) for e in s.scalars(select(AuditEvent).where(AuditEvent.analysis_id == aid))}
    assert ("review.started", "Test Engineer") in actors and ("review.joined", "Second Reviewer") in actors


def test_a_failed_inspection_stays_accessible_and_decidable(client, scene):
    aid = upload(client, scene.rgb, scene.labels, pole_code="S18-FAIL")
    wait_done(client, aid)
    with client.state.db.session() as s:
        s.execute(text("UPDATE analyses SET status='error', error_code='inference_error' WHERE id=:i"), {"i": aid})
    assert client.get(f"/api/analysis/{aid}/result").status_code == 200
    ctx = client.get(f"/api/admin/inspections/{aid}/context").json()
    assert ctx["can"]["review"] is True
    row = _row(client, aid)
    assert row["machine_state"] == "failed" and row["risk_level"] is None and row["review_status"] == "NOT_YET_REVIEWED"
    lst = client.get("/api/admin/inspections", params={"per_page": 500, "screen": "processing_issue"}).json()
    assert aid in [r["id"] for r in lst["items"]]
    assert client.post(f"/api/admin/inspections/{aid}/decision", json=DECISION).status_code == 201


def test_review_tabs_reconcile_with_all(client, scene):
    ids = [upload(client, scene.rgb, scene.labels, pole_code=f"S18-T{i}") for i in range(3)]
    for a in ids:
        wait_done(client, a)
    client.post(f"/api/admin/inspections/{ids[0]}/review/start")
    client.post(f"/api/admin/inspections/{ids[1]}/decision", json=DECISION)
    d = client.get("/api/admin/inspections", params={"per_page": 500}).json()
    t = d["review_tabs"]
    assert t["NOT_YET_REVIEWED"] + t["PARTIALLY_REVIEWED"] + t["REVIEW_COMPLETE"] == t["all"] == d["matched"]
    assert t["PARTIALLY_REVIEWED"] >= 1 and t["REVIEW_COMPLETE"] >= 1
    only = client.get("/api/admin/inspections", params={"per_page": 500, "review_status": "REVIEW_COMPLETE"}).json()
    assert {r["review_status"] for r in only["items"]} == {"REVIEW_COMPLETE"}


def test_legacy_review_route_is_mirrored_once(client, scene):
    aid = upload(client, scene.rgb, scene.labels, pole_code="S18-LEG")
    wait_done(client, aid)
    assert client.post(f"/api/admin/analyses/{aid}/reviews", json={"decision": "flagged", "note": ""}).status_code == 201
    v = client.get(f"/api/admin/inspections/{aid}/review").json()
    assert v["status"] == "REVIEW_COMPLETE" and v["decision"]["code"] == "field_inspection"
    with client.state.db.engine.begin() as conn:
        assert review_flow.backfill_legacy(conn) == 0  # linked by legacy_review_id: never duplicated


def test_migration_maps_pre_s18_reviews_without_changing_them(client, scene):
    aid = upload(client, scene.rgb, scene.labels, pole_code="S18-MIG")
    other = upload(client, scene.rgb, scene.labels, pole_code="S18-MIG2")
    wait_done(client, aid)
    wait_done(client, other)
    with client.state.db.engine.begin() as conn:  # a review written before S18: no decision, no progress row
        conn.execute(text("INSERT INTO reviews (analysis_id, reviewer_name, decision, note, created_at) VALUES "
                          "(:a, 'Old Reviewer', 'dismissed', 'misdetection', '2026-01-02 03:04:05')"), {"a": aid})
        n = review_flow.backfill_legacy(conn)
        assert n == 1 and review_flow.backfill_legacy(conn) == 0
        assert conn.execute(text("SELECT note FROM reviews WHERE analysis_id=:a"), {"a": aid}).scalar() == "misdetection"
    with client.state.db.session() as s:
        p = s.get(ReviewProgress, aid)
        d = s.scalars(select(AdminDecision).where(AdminDecision.analysis_id == aid)).one()
        assert p.status == "REVIEW_COMPLETE" and p.source == "legacy_review"
        assert d.decision == "no_further_action" and d.rationale == "misdetection" and d.source == "legacy_review"
        assert s.get(ReviewProgress, other) is None  # nobody reviewed it: still NOT_YET_REVIEWED


# --------------------------------------------------------------------------- risk rubric
def _p(n):
    return [{"index": i, "types": ["sag_exceeded"]} for i in range(n)]


@pytest.mark.parametrize("n,level", [(0, "normal"), (1, "moderate"), (9, "moderate"), (10, "high"), (25, "high")])
def test_rubric_boundaries(n, level):
    r = screening.risk(30, _p(n), [], screening.DEFAULT_RUBRIC)
    assert r["level"] == level and r["problem_wires"] == n and r["total_wires"] == 30
    assert r["short"].endswith(f"{n} / 30 problem wires") and r["basis"] == "operational_triage_rubric"


def test_many_wires_zero_problems_is_normal():
    r = screening.risk(18, [], [], screening.DEFAULT_RUBRIC)
    assert r["level"] == "normal" and r["total_wires"] == 18 and r["problem_wires"] == 0


@pytest.mark.parametrize("crit", [[{"type": "snapped_hanging"}], [{"type": "sparking_arcing"}, {"type": "dangling_into_path"}]])
def test_a_confirmed_critical_hazard_overrides_the_count(crit):
    r = screening.risk(8, [], crit, screening.DEFAULT_RUBRIC)
    assert r["level"] == "high" and r["critical_override"] and r["problem_wires"] == 0
    assert r["short"].startswith("High · ")


def test_low_clearance_needs_a_measurement_and_a_documented_reference():
    with pytest.raises(screening.RubricError, match="documented clearance reference"):
        screening.validate_critical([{"type": "low_clearance_measured", "note": "looks low in the photo"}])
    rub = {**screening.DEFAULT_RUBRIC, "clearance_references": [
        {"id": "TEST-REF", "source": "documented test reference", "category": "test", "min_height_m": 5.0}]}
    with pytest.raises(screening.RubricError, match="height"):
        screening.validate_critical([{"type": "low_clearance_measured", "measurement": {"reference_id": "TEST-REF"}}],
                                    rub)
    with pytest.raises(screening.RubricError, match="not below"):
        screening.validate_critical([{"type": "low_clearance_measured", "measurement": {
            "reference_id": "TEST-REF", "height_m": 6.0, "method": "measuring pole"}}], rub)
    ok = screening.validate_critical([{"type": "low_clearance_measured", "measurement": {
        "reference_id": "TEST-REF", "height_m": 4.1, "method": "measuring pole"}}], rub)
    assert ok[0]["measurement"]["min_height_m"] == 5.0


def test_problem_wires_need_a_supported_observation():
    from app.services.outcome import needs_attention  # noqa: F401  (the rule wire_problems uses)
    power_over = {"index": 1, "class": "power-distribution-lines", "comparison": {"position": "above",
                  "attention_zones": ["above"], "notable_zones": []}}
    tel_typical = {"index": 2, "class": "telecom-cable", "comparison": {"position": "at", "banded": True,
                   "attention_zones": [], "notable_zones": ["above"]}}
    tel_beyond = {"index": 3, "class": "telecom-cable", "comparison": {"position": "above", "banded": True,
                  "attention_zones": [], "notable_zones": ["above"]}}
    abandoned_unsure = {"index": 4, "class": "abandoned-wire", "class_status": "uncertain"}
    veg = {"index": 5, "class": "telecom-cable", "flags": ["vegetation_contact"]}
    plain = {"index": 6, "class": "power-distribution-lines"}
    hz = {"factors": {"loose_ends": [{"span": 6}]}}
    got = {p["index"]: p["types"] for p in screening.problem_wires(
        [power_over, tel_typical, tel_beyond, abandoned_unsure, veg, plain], hz, screening.DEFAULT_RUBRIC)}
    assert got == {1: ["sag_exceeded"], 3: ["sag_beyond_typical"], 5: ["vegetation_contact"],
                   6: ["possible_dangling_end"]}


def test_rubric_changes_are_versioned_and_audited(client):
    bad = client.put("/api/admin/settings/risk-rubric", json={"moderate_from": 5, "high_from": 3,
                                                              "effective_date": "2026-12-01", "reason": "x"})
    assert bad.status_code == 422
    no_reason = client.put("/api/admin/settings/risk-rubric", json={"moderate_from": 1, "high_from": 8,
                                                                    "effective_date": "2026-12-01"})
    assert no_reason.status_code == 422
    r = client.put("/api/admin/settings/risk-rubric", json={"moderate_from": 1, "high_from": 8,
                                                            "effective_date": "2026-12-01",
                                                            "reason": "Barangay pilot: triage setting"}).json()
    assert r["version"] == 2 and r["high_from"] == 8
    assert screening.risk(10, _p(8), [])["level"] == "high"
    with client.state.db.session() as s:
        ev = s.scalars(select(AuditEvent).where(AuditEvent.action == "settings.risk_rubric")).one()
        assert ev.detail["previous"]["high_from"] == 10 and ev.detail["new"]["high_from"] == 8
    screening.CURRENT = dict(screening.DEFAULT_RUBRIC)  # module state: restore for the other tests


def test_decision_options_are_data_and_never_deleted(client):
    opts = client.get("/api/admin/settings/decision-options").json()
    keep = [o for o in opts if o["code"] != "monitoring"] + [{"code": "barangay_referral", "label": "Refer to barangay"}]
    out = client.put("/api/admin/settings/decision-options", json={"options": keep}).json()
    by = {o["code"]: o for o in out}
    assert by["barangay_referral"]["active"] and by["monitoring"]["active"] is False  # retired, still readable


def test_result_carries_what_we_know_separately_from_review(client, scene):
    aid = upload(client, scene.rgb, scene.labels, pole_code="S18-WK")
    wait_done(client, aid)
    r = client.get(f"/api/analysis/{aid}/result").json()
    sc = r["screening"]
    assert sc["total_wires"] == len(r["wires"]) and sc["problem_count"] == len(sc["problem_wires"])
    assert sc["risk"]["total_wires"] == sc["total_wires"] and sc["finding"]
    assert set(r["timeline"]) == {"captured_at", "received_at", "completed_at"}
    assert _row(client, aid)["problem_count"] == sc["problem_count"]  # one count, every view
    assert _row(client, aid)["detected"] == sc["total_wires"]


def test_dashboard_feed_is_real_events_only(client, scene):
    aid = upload(client, scene.rgb, scene.labels, pole_code="S18-FEED")
    wait_done(client, aid)
    before = client.get("/api/admin/dashboard").json()["feed"]
    again = client.get("/api/admin/dashboard").json()["feed"]
    assert before == again  # reading the dashboard writes nothing
    kinds = {e["action"] for e in before if e["analysis_id"] == aid}
    assert {"inspection.received", "inspection.processed"} <= kinds
    assert all(e["automated"] for e in before if e["action"].startswith("inspection."))
    client.post(f"/api/admin/inspections/{aid}/review/start")
    human = [e for e in client.get("/api/admin/dashboard").json()["feed"] if e["action"] == "review.started"]
    assert human and human[0]["automated"] is False and human[0]["actor"] == "Test Engineer"


# --------------------------------------------------------------------------- photo tips
def test_photo_tips_come_from_measured_conditions_only():
    clean = {"segmentation_input": {"quality": {"mean_l": 50, "sharpness": 3000, "clipped_high": 0.0}},
             "quality": {"uncertainty": {"wire_pixels": 90000}}, "image": {"width": 1000, "height": 800}, "wires": []}
    assert screening.photo_tips(clean) == []
    dark = {**clean, "segmentation_input": {"quality": {"mean_l": 12, "sharpness": 3000}}}
    assert [t["code"] for t in screening.photo_tips(dark)] == ["dark"]
    cut = {**clean, "wires": [{"reason_code": "endpoint_unavailable", "fit": {"endpoints": [[400, 3], [700, 300]]}}]}
    tips = screening.photo_tips(cut)
    assert [t["code"] for t in tips] == ["top_cut"] and "pole top" in tips[0]["action"]


# --------------------------------------------------------------------------- providers
@pytest.mark.parametrize("city,brgy,expect", [("City of Dasmariñas", None, "FIBERBLAZE Dasmariñas"),
                                              ("Dasmarinas City", None, "FIBERBLAZE Dasmariñas"),
                                              ("Imus", None, "FIBERBLAZE Imus"),
                                              (None, "Anabu I-A", "FIBERBLAZE Imus")])
def test_providers_follow_the_location_and_its_aliases(client, city, brgy, expect):
    with client.state.db.session() as s:
        p = contacts.providers(s, brgy, city, {"telecom-cable"})
    names = [x["organization"] for g in p["groups"] for x in g["items"]]
    assert expect in names and p["place"]["resolved"]
    other = "FIBERBLAZE Imus" if expect == "FIBERBLAZE Dasmariñas" else "FIBERBLAZE Dasmariñas"
    assert other not in names  # a branch in another city is not "serving this area"
    assert all(x["relation"] != "confirmed_responsible" for g in p["groups"] for x in g["items"])
    assert "not who owns a particular wire" in p["ownership_note"]


def test_provider_categories_follow_the_conductor_type(client):
    with client.state.db.session() as s:
        power = contacts.providers(s, None, "City of Imus", {"power-distribution-lines"})
        tel = contacts.providers(s, None, "City of Imus", {"telecom-cable"})
        unknown = contacts.providers(s, None, "City of Imus", {"uncertain"})
    assert power["groups"][0]["category"] == "electricity" and power["groups"][0]["relevant"]
    assert tel["groups"][0]["category"] in ("telecom", "broadband", "catv")
    assert unknown["unidentified_wires"] and all(g["relevant"] for g in unknown["groups"])


def test_unresolved_or_unlisted_locations_say_so(client):
    with client.state.db.session() as s:
        none = contacts.providers(s, None, None, {"telecom-cable"})
        far = contacts.providers(s, None, "Ternate", {"power-distribution-lines"})
    assert not none["place"]["resolved"] and none["groups"] == [] and "nationwide" in none["gap"]
    assert far["place"]["resolved"]
    assert all(x["relation"] != "serves_area" or x["organization"] != "Meralco (Manila Electric Company)"
               for g in far["groups"] for x in g["items"])
    assert all(x["verification"] != "unverified" for x in none["nationwide"])


def test_confirmed_owner_only_from_an_authoritative_record(client, scene):
    aid = upload(client, scene.rgb, scene.labels, pole_code="S18-OWN", city="City of Imus")
    wait_done(client, aid)
    with client.state.db.session() as s:
        fb = s.scalar(select(contacts.Contact).where(contacts.Contact.seed_key == "fiberblaze-imus"))
        fid = fb.id
    before = client.get(f"/api/analysis/{aid}/contacts").json()["providers"]
    assert not any(x["relation"] == "confirmed_responsible" for g in before["groups"] for x in g["items"])
    assert client.post("/api/admin/poles/S18-OWN/attachments", json={"contact_id": fid, "record_type": "hearsay",
                                                                      "record_reference": "x"}).status_code == 422
    r = client.post("/api/admin/poles/S18-OWN/attachments", json={
        "contact_id": fid, "wire_class": "telecom-cable", "record_type": "attachment_agreement",
        "record_reference": "Joint-use agreement JU-2026-014"})
    assert r.status_code == 201, r.text
    after = client.get(f"/api/analysis/{aid}/contacts").json()["providers"]
    conf = [x for g in after["groups"] for x in g["items"] if x["relation"] == "confirmed_responsible"]
    assert [x["organization"] for x in conf] == ["FIBERBLAZE Imus"] and conf[0]["confirmed"]["record_reference"]


def test_public_provider_data_never_exposes_internal_notes(client, anon, scene):
    aid = upload(client, scene.rgb, scene.labels, pole_code="S18-PUB", city="City of Dasmariñas")
    wait_done(client, aid)
    p = anon.get(f"/api/analysis/{aid}/contacts").json()["providers"]
    for x in [i for g in p["groups"] for i in g["items"]] + p["nationwide"]:
        assert "source_note" not in x and "admin_notes" not in x and x["verification"] != "unverified"


# --------------------------------------------------------------------------- data integrity
def test_the_database_refuses_invalid_review_records(client, scene):
    import sqlalchemy.exc
    aid = upload(client, scene.rgb, scene.labels, pole_code="S18-CHK")
    wait_done(client, aid)
    with client.state.db.engine.begin() as c:
        for sql in ("INSERT INTO review_progress (analysis_id, status, source, reviewers, updated_at) "
                    "VALUES (:a, 'REVIEWED', 'workflow', '[]', CURRENT_TIMESTAMP)",
                    "INSERT INTO review_progress (analysis_id, status, source, reviewers, updated_at) "
                    "VALUES (:a, 'REVIEW_COMPLETE', 'workflow', '[]', CURRENT_TIMESTAMP)"):  # complete needs a time
            with pytest.raises(sqlalchemy.exc.IntegrityError):
                with c.begin_nested():
                    c.execute(text(sql), {"a": aid})


def test_amending_appends_and_never_rewrites_a_decision(client, scene):
    aid = upload(client, scene.rgb, scene.labels, pole_code="S18-APP")
    wait_done(client, aid)
    client.post(f"/api/admin/inspections/{aid}/decision", json=DECISION)
    first = client.get(f"/api/admin/inspections/{aid}/review").json()["decision"]
    client.post(f"/api/admin/inspections/{aid}/decision",
                json={"decision": "monitoring", "rationale": "After the field visit: monitor for six months."})
    with client.state.db.session() as s:
        rows = s.scalars(select(AdminDecision).where(AdminDecision.analysis_id == aid)
                         .order_by(AdminDecision.created_at)).all()
        assert [d.decision for d in rows] == ["field_inspection", "monitoring"]
        assert rows[0].id == first["id"] and rows[0].rationale == DECISION["rationale"]  # unchanged
        assert rows[0].superseded_at is not None and rows[1].superseded_at is None
        assert rows[0].machine_json and rows[0].machine_json["detected"] is not None  # what the reviewer saw


def test_bulk_mark_reviewed_records_the_same_decision(client, scene):
    aid = upload(client, scene.rgb, scene.labels, pole_code="S18-BULK")
    wait_done(client, aid)
    assert client.post("/api/admin/inspections/bulk", json={"ids": [aid], "action": "mark_reviewed"}).status_code == 200
    row = _row(client, aid)
    assert row["review_status"] == "REVIEW_COMPLETE" and row["decision"]["code"] == "legacy_confirmed"


# --------------------------------------------------------------------------- lists, search, exports
def test_search_matches_file_name_id_and_coordinates():
    from app.services.records import matches
    r = {"id": "415c41c607954ed4ad1196998c828a0d", "filename": "IMG_2024.HEIC", "pole_code": "P-1",
         "label": None, "inspection_name": None, "barangay": "Salitran I", "city": "Dasmariñas",
         "place": {"label": "Salitran I, Dasmariñas", "coords": [14.3271234, 120.9365678]}}
    for q in ("img_2024", "415c41c6", "WF-415C41C6", "14.32712", "14.327123, 120.93", "14.327123,120.936568", "salitran"):
        assert matches(r, q), q
    for q in ("14.33", "IMG_2025", "Imus"):
        assert not matches(r, q), q


def test_inspections_list_has_the_s18_columns_and_no_assigned(client, scene):
    aid = upload(client, scene.rgb, scene.labels, pole_code="S18-COL")
    wait_done(client, aid)
    row = _row(client, aid)
    for k in ("filename", "detected", "problem_count", "risk_level", "review_status", "decision"):
        assert k in row, k
    assert row["risk_level"]["problem_wires"] == row["problem_count"] <= row["detected"]
    csv = client.get("/api/admin/exports/inspections.csv").text
    head = next(line for line in csv.splitlines() if line.startswith("Pole ID"))
    for col in ("File name", "Wires detected", "Problem wires", "Triage risk (operational rubric)", "Rubric version",
                "Review status", "Administrative decision", "Decided by"):
        assert col in head, col
    only = client.get("/api/admin/exports/inspections.csv", params={"review_status": "REVIEW_COMPLETE"}).text
    assert aid not in only  # the same filter as the list


def test_dashboard_counts_reconcile_with_its_rows(client, scene):
    for code in ("S18-D1", "S18-D2"):
        wait_done(client, upload(client, scene.rgb, scene.labels, pole_code=code))
    d = client.get("/api/admin/operations").json()
    assert sum(d["review_counts"].values()) == len(d["poles"]) == d["fleet"]["poles"]
    assert all(e["kind"] for e in d["feed"]) and any(e["automated"] for e in d["feed"])


# --------------------------------------------------------------------------- one result component everywhere
def test_one_shared_result_view_for_citizen_sheet_and_quick_review():
    from app.core.config import ROOT_DIR
    a = ROOT_DIR / "app/web/assets"
    field, admin = (a / "field.js").read_text(), (a / "admin.js").read_text()
    assert 'resultViewHtml(r, { mode: "citizen"' in field and "bindResultView(root, r)" in field
    assert 'resultViewHtml(r, { mode: "admin"' in admin and 'resultViewHtml(r, { mode: "compact"' in admin
    assert admin.count("mountReviewPanel(") == 3  # sheet, failed sheet, quick review
    rv = (a / "result-view.js").read_text()
    for layer in ("Original", "Pole detection", "Segmentation mask", "Drawn wires", "Problem wires", "Combined"):
        assert f'"{layer}"' in rv, layer
    for t in ("Photo captured", "Photo received", "Screening completed"):
        assert t in rv, t
    # the browser draws server decisions: no rubric thresholds or sag cut-offs in any page script
    for js in (field, admin, rv, (a / "admin-map.js").read_text()):
        assert "high_from" not in js and "moderate_from" not in js and "sag_ratio > 0.0" not in js


def test_dashboard_and_inspections_tables_have_no_assigned_or_reason_column():
    from app.core.config import ROOT_DIR
    admin = (ROOT_DIR / "app/web/assets/admin.js").read_text()
    dash = admin[admin.index("function mountDashboard()"):admin.index("function opsBulk(")]
    insp = admin[admin.index("function mountInspections()"):admin.index("function quickReview(")]
    for body in (dash, insp):
        thead = body[body.index("<thead>"):body.index("</thead>")]
        assert "Assigned" not in thead and "Reason" not in thead
        assert '"Risk"' in thead or "Risk" in thead
    rk = screening.risk(12, _p(3), [], screening.DEFAULT_RUBRIC)
    assert rk["short"] == "Moderate · 3 / 12 problem wires" and "${riskCell(r)}" in dash and "${riskCell(r)}" in insp
    for k in ("NOT_YET_REVIEWED", "PARTIALLY_REVIEWED", "REVIEW_COMPLETE"):
        assert f'["{k}",' in admin
