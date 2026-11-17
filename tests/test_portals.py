"""Dual-portal architecture: open field screener (side A) and authenticated admin centre (side B)."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pypdfium2 as pdfium
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import COOKIE, token_digest
from app.models.orm import AdminSession, PoleLocation
from tests.conftest import ADMIN, ORIGIN, jpeg_bytes, upload, wait_done

ADMIN_ONLY_GET = ["/api/dashboard/priority", "/api/admin/dashboard", "/api/admin/inspections", "/api/admin/audit",
                  "/api/admin/queue", "/api/admin/models", "/api/admin/exports/manifest.csv",
                  "/api/admin/exports/wires.csv", "/api/admin/exports/sites.pdf"]


def pdf_text(content: bytes) -> str:
    doc = pdfium.PdfDocument(content)
    text = "".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    doc.close()
    return text


# ----------------------------------------------------------------------------- access control


@pytest.mark.parametrize("path", ADMIN_ONLY_GET)
def test_admin_routes_refuse_anonymous(anon, path):
    r = anon.get(path)
    assert r.status_code == 401 and r.json()["error"]["code"] == "auth_required", path


@pytest.mark.parametrize("path", ADMIN_ONLY_GET)
def test_admin_routes_serve_signed_in_admin(client, path):
    assert client.get(path).status_code == 200, path


def test_field_user_can_screen_without_signing_in(anon, scene):
    aid = upload(anon, scene.rgb, scene.labels, pole_code="F-1")
    assert wait_done(anon, aid)["status"] == "complete"
    r = anon.get(f"/api/analysis/{aid}/result").json()
    assert r["submitted_via"] == "field" and r["public"] is True and r["wires"]
    # v2.20: the public view carries no scores, fit parameters, model names, configuration hash or timings
    assert not {"summary", "telemetry", "parameters", "timings_ms", "ranking", "acquisition"} & set(r)
    assert all("crs" not in w and "confidence" not in w and set(w["fit"]) <= {"endpoints", "sag_point", "chord_foot"} for w in r["wires"])
    assert anon.get(f"/api/analysis/{aid}/image").status_code == 200
    assert anon.get(f"/api/reports/{aid}/pdf").status_code == 200
    # but cannot delete, retry or enumerate
    assert anon.delete(f"/api/analysis/{aid}").status_code == 401
    assert anon.post(f"/api/analysis/{aid}/retry").status_code == 401


def test_pages_route_to_the_two_portals(anon):
    assert "Open the Field Screener" in anon.get("/").text
    assert "Admin sign-in" in anon.get("/").text
    for p in ("/workspace", "/workspace/result/" + "0" * 32):
        assert "Field Screener" in anon.get(p).text
    for p in ("/admin", "/admin/login", "/admin/analyses/abc"):
        r = anon.get(p)
        assert "Admin" in r.text and r.headers["x-frame-options"] == "DENY"


# ----------------------------------------------------------------------------- authentication


def test_login_logout_and_session_storage(anon):
    assert anon.get("/api/auth/me").json() == {"user": None}
    bad = anon.post("/api/auth/login", json={"username": ADMIN[0], "password": "wrong password!!"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "bad_credentials"
    ghost = anon.post("/api/auth/login", json={"username": "nobody", "password": "whatever-whatever"})
    assert ghost.json()["error"]["message"] == bad.json()["error"]["message"]  # no username enumeration
    ok = anon.post("/api/auth/login", json={"username": ADMIN[0].upper(), "password": ADMIN[1]})
    assert ok.status_code == 200 and ok.json()["user"]["display_name"] == "Test Engineer"
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    token = anon.cookies.get(COOKIE)
    with anon.state.db.session() as s:
        stored = s.scalars(select(AdminSession.token_sha256)).all()
    assert token_digest(token) in stored and token not in stored  # only the hash is kept
    assert anon.get("/api/admin/dashboard").status_code == 200
    assert anon.post("/api/auth/logout").status_code == 204
    replay = TestClient(anon.app, headers=ORIGIN, cookies={COOKIE: token})
    assert replay.get("/api/admin/dashboard").status_code == 401  # revoked server-side


def test_login_is_throttled(anon):
    for _ in range(5):
        anon.post("/api/auth/login", json={"username": ADMIN[0], "password": "nope nope nope"})
    r = anon.post("/api/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]})
    assert r.status_code == 429 and "retry-after" in r.headers


def test_expired_session_is_rejected(client):
    with client.state.db.session() as s:
        for row in s.scalars(select(AdminSession)):
            row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    assert client.get("/api/admin/dashboard").status_code == 401


def test_cross_site_writes_are_refused(client, scene):
    aid = upload(client, scene.rgb, scene.labels)
    wait_done(client, aid)
    no_origin = TestClient(client.app, cookies=client.cookies)
    r = no_origin.post(f"/api/admin/analyses/{aid}/reviews", json={"decision": "confirmed"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "bad_origin"
    evil = TestClient(client.app, cookies=client.cookies, headers={"origin": "https://evil.example"})
    assert evil.delete(f"/api/analysis/{aid}").status_code == 403
    assert evil.post("/api/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]}).status_code == 403


# ----------------------------------------------------------------------------- field-upload protections


def test_anonymous_uploads_are_rate_limited(anon, scene):
    anon.state.upload_limiter.limit = 2
    for _ in range(2):
        upload(anon, scene.rgb, scene.labels)
    r = anon.post("/api/analysis/upload", files={"file": ("p.jpg", jpeg_bytes(scene.rgb)),
                                                  "labels": ("p.txt", scene.labels.encode())})
    assert r.status_code == 429 and "retry-after" in r.headers


def test_anonymous_upload_cannot_move_a_recorded_pole(client, anon, scene):
    upload(client, scene.rgb, scene.labels, pole_code="P-9", barangay="Salitran I", latitude=14.30, longitude=120.90)
    upload(anon, scene.rgb, scene.labels, pole_code="P-9", barangay="Elsewhere", latitude=10.0, longitude=100.0)
    with client.state.db.session() as s:
        from app.models.orm import Pole
        p = s.scalar(select(Pole).where(Pole.pole_code == "P-9"))
        loc = s.get(PoleLocation, p.id)
        assert (p.barangay, loc.latitude) == ("Salitran I", 14.30)
    upload(client, scene.rgb, scene.labels, pole_code="P-9", latitude=14.31, longitude=120.91)
    with client.state.db.session() as s:
        assert s.get(PoleLocation, p.id).latitude == pytest.approx(14.31)  # an admin may correct it


def test_require_reference_masks_switch(client, anon, scene):
    class Ready:  # a model that is "loaded"
        source, ready = "model", True
        def load(self): return True
        def segment(self, x, lb): return []
    client.state.segmenter = Ready()
    r = client.put("/api/admin/settings", json={"require_reference_masks": True})
    assert r.status_code == 200 and r.json()["mode"] == "validation"
    refused = anon.post("/api/analysis/upload", files={"file": ("p.jpg", jpeg_bytes(scene.rgb))})
    assert refused.status_code == 503
    client.put("/api/admin/settings", json={"require_reference_masks": False})
    ok = anon.post("/api/analysis/upload", files={"file": ("p.jpg", jpeg_bytes(scene.rgb))})
    assert ok.status_code == 202
    assert wait_done(anon, ok.json()["id"])["status"] == "no_wires_detected"


# ----------------------------------------------------------------------------- command-centre features


def test_reviews_are_recorded_beside_scores_and_reach_the_pdf(client, scene):
    aid = upload(client, scene.rgb, scene.labels, pole_code="R-1")
    wait_done(client, aid)
    computed = client.get(f"/api/analysis/{aid}/result").json()["summary"]["level"]
    override = "low" if computed != "low" else "high"  # always a level different from the computed one
    url = f"/api/admin/analyses/{aid}/reviews"
    assert client.post(url, json={"decision": "overridden"}).status_code == 422          # no level
    assert client.post(url, json={"decision": "overridden", "override_level": "low"}).status_code == 422  # no note
    r = client.post(url, json={"decision": "overridden", "override_level": override, "note": "Wire 5 is a drip loop."})
    assert r.status_code == 201 and r.json()[-1]["by"] == "Test Engineer"
    res = client.get(f"/api/analysis/{aid}/result").json()
    assert res["summary"]["level"] == computed  # the computed level is never changed by a review
    assert res["reviews"][-1]["override_level"] == override
    text = pdf_text(client.get(f"/api/reports/{aid}/pdf").content)
    assert f"Changed the risk level to {override.upper()}" in text and "drip loop" in text
    row = next(i for i in client.get("/api/admin/inspections").json()["items"] if i["id"] == aid)
    assert row["review"] == "overridden" and row["override_level"] == override


def test_unreviewed_pdf_says_so(client, scene):
    aid = upload(client, scene.rgb, scene.labels)
    wait_done(client, aid)
    assert "Not yet reviewed" in pdf_text(client.get(f"/api/reports/{aid}/pdf").content)


def test_pole_history_tracks_inspections(client):
    from app.devtools.synthetic import make_scene
    aids = []
    for k in (0.25, 0.4, 0.55):  # the same pole photographed as its sag deepens
        sc = make_scene(seed=3, sag_scale=k)
        aids.append(upload(client, sc.rgb, sc.labels, pole_code="H-1"))
        wait_done(client, aids[-1])
    crs = [client.get(f"/api/analysis/{a}/result").json()["summary"]["max_crs"] for a in aids]
    assert crs[0] < crs[1] < crs[2]
    h = client.get(f"/api/admin/inspections/{aids[-1]}/context").json()["history"]
    assert [x["id"] for x in h] == aids[::-1]  # the inspection page lists every inspection of the pole, newest first


def test_exports(client, scene):
    for code in ("E-1", "E-2"):
        wait_done(client, upload(client, scene.rgb, scene.labels, pole_code=code, latitude=14.3, longitude=120.9))
    man = client.get("/api/admin/exports/manifest.csv")
    assert man.headers["content-type"].startswith("text/csv") and "attachment" in man.headers["content-disposition"]
    lines = man.content.decode("utf-8-sig").strip().splitlines()
    assert lines[0].startswith("rank,pole_code,barangay,mspi") and len(lines) == 3
    wires = client.get("/api/admin/exports/wires.csv").content.decode("utf-8-sig").strip().splitlines()
    assert "rms_residual_px" in wires[0] and len(wires) == 1 + 10
    pdf = client.get("/api/admin/exports/sites.pdf?alpha=0.2&beta=0.3&gamma=0.5")
    text = pdf_text(pdf.content)
    assert "MULTI-SITE PRIORITY REPORT" in text and "E-1" in text and "Important Notice" in text  # technical
    plain = pdf_text(client.get("/api/admin/exports/sites.pdf").content)  # v2.19 default: the dispatch report
    assert "INSPECTION PRIORITY" in plain and "E-1" in plain and "Important Notice" in plain
    assert "MSPI" not in plain and "Spearman" not in plain
    assert client.get("/api/admin/exports/sites.pdf?alpha=0.5&beta=0.5&gamma=0.5").status_code == 422


def test_models_panel_and_checkpoint_guard(client):
    m = client.get("/api/admin/models").json()
    assert m["mode"] == "validation" and m["iou_nms"] == 0.45 and isinstance(m["checkpoints"], list)
    for bad in ("../config/wireframe.yaml", "missing.pt"):
        r = client.post("/api/admin/models/active", json={"name": bad})
        assert r.status_code == 422 and r.json()["error"]["code"] == "checkpoint_rejected"


def test_queue_reports_processed_analyses(client, scene):
    aid = upload(client, scene.rgb, scene.labels)
    wait_done(client, aid)
    q = client.get("/api/admin/queue").json()
    assert q["depth"] == 0 and q["processed_since_start"] >= 1


def test_live_sensitivity_baseline(client):
    from app.devtools.synthetic import make_scene
    for i, k in enumerate((0.5, 1.2, 1.8)):
        sc = make_scene(seed=i, sag_scale=k, clutter=2 + 5 * i)
        wait_done(client, upload(client, sc.rgb, sc.labels, pole_code=f"S{i}"))
    from app.services import sites  # the weight sensitivity printed in the technical priority report
    with client.state.db.session() as s:
        base = sites.sensitivity_payload(s, client.state.config)
        live = sites.sensitivity_payload(s, client.state.config, baseline=(1.0, 0.0, 0.0))
    assert live["baseline"] == {"alpha": 1, "beta": 0, "gamma": 0} and len(live["grid"]) == 66
    cell = next(g for g in live["grid"] if (g["alpha"], g["beta"], g["gamma"]) == (1.0, 0.0, 0.0))
    assert cell["rho"] == pytest.approx(1.0)
    assert base["baseline"]["alpha"] == pytest.approx(1 / 3)
    _ = np  # numpy imported for parity with other test modules
