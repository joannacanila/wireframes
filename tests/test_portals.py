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

ADMIN_ONLY_GET = ["/api/analysis", "/api/dashboard/map", "/api/dashboard/priority", "/api/dashboard/sensitivity",
                  "/api/dashboard/summary", "/api/admin/overview", "/api/admin/queue", "/api/admin/models",
                  "/api/admin/poles", "/api/admin/exports/manifest.csv", "/api/admin/exports/wires.csv",
                  "/api/admin/exports/sites.pdf"]


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
    assert r["submitted_via"] == "field" and r["summary"]["level"] in {"low", "medium", "high"}
    assert anon.get(f"/api/analysis/{aid}/image").status_code == 200
    assert anon.get(f"/api/reports/{aid}/pdf").status_code == 200
    # but cannot delete, retry or enumerate
    assert anon.delete(f"/api/analysis/{aid}").status_code == 401
    assert anon.post(f"/api/analysis/{aid}/retry").status_code == 401


def test_pages_route_to_the_two_portals(anon):
    assert "Launch Field Screener" in anon.get("/").text
    assert "Admin portal" in anon.get("/").text
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
    assert anon.get("/api/admin/overview").status_code == 200
    assert anon.post("/api/auth/logout").status_code == 204
    replay = TestClient(anon.app, headers=ORIGIN, cookies={COOKIE: token})
    assert replay.get("/api/admin/overview").status_code == 401  # revoked server-side


def test_login_is_throttled(anon):
    for _ in range(5):
        anon.post("/api/auth/login", json={"username": ADMIN[0], "password": "nope nope nope"})
    r = anon.post("/api/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]})
    assert r.status_code == 429 and "retry-after" in r.headers


def test_expired_session_is_rejected(client):
    with client.state.db.session() as s:
        for row in s.scalars(select(AdminSession)):
            row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    assert client.get("/api/admin/overview").status_code == 401


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
    upload(client, scene.rgb, scene.labels, pole_code="P-9", barangay="Salitran", latitude=14.30, longitude=120.90)
    upload(anon, scene.rgb, scene.labels, pole_code="P-9", barangay="Elsewhere", latitude=10.0, longitude=100.0)
    with client.state.db.session() as s:
        from app.models.orm import Pole
        p = s.scalar(select(Pole).where(Pole.pole_code == "P-9"))
        loc = s.get(PoleLocation, p.id)
        assert (p.barangay, loc.latitude) == ("Salitran", 14.30)
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
    url = f"/api/admin/analyses/{aid}/reviews"
    assert client.post(url, json={"decision": "overridden"}).status_code == 422          # no level
    assert client.post(url, json={"decision": "overridden", "override_level": "low"}).status_code == 422  # no note
    r = client.post(url, json={"decision": "overridden", "override_level": "medium", "note": "Wire 5 is a drip loop."})
    assert r.status_code == 201 and r.json()[-1]["by"] == "Test Engineer"
    res = client.get(f"/api/analysis/{aid}/result").json()
    assert res["summary"]["level"] == "high"  # the computed level is never changed
    assert res["reviews"][-1]["override_level"] == "medium"
    text = pdf_text(client.get(f"/api/reports/{aid}/pdf").content)
    assert "Overrode the risk level to MEDIUM" in text and "drip loop" in text
    listed = client.get("/api/analysis?unreviewed=true").json()["items"]
    assert aid not in {i["id"] for i in listed}


def test_unreviewed_pdf_says_so(client, scene):
    aid = upload(client, scene.rgb, scene.labels)
    wait_done(client, aid)
    assert "Not yet reviewed" in pdf_text(client.get(f"/api/reports/{aid}/pdf").content)


def test_pole_history_tracks_inspections(client):
    from app.devtools.synthetic import make_scene
    for k in (0.25, 0.4, 0.55):  # the same pole photographed as its sag deepens
        sc = make_scene(seed=3, sag_scale=k)
        wait_done(client, upload(client, sc.rgb, sc.labels, pole_code="H-1"))
    h = client.get("/api/admin/poles/H-1").json()["history"]
    assert len(h) == 3 and h[0]["max_crs"] < h[1]["max_crs"] < h[2]["max_crs"]
    assert h[0]["max_sag_ratio"] < h[2]["max_sag_ratio"]
    row = next(p for p in client.get("/api/admin/poles").json() if p["pole_code"] == "H-1")
    assert row["inspections"] == 3 and row["trend_max_crs"] > 0
    assert client.get("/api/admin/poles/NOPE").status_code == 404


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
    assert "MULTI-SITE PRIORITY REPORT" in text and "E-1" in text and "Professional review required" in text
    assert client.get("/api/admin/exports/sites.pdf?alpha=0.5&beta=0.5&gamma=0.5").status_code == 422


def test_models_panel_and_checkpoint_guard(client):
    m = client.get("/api/admin/models").json()
    assert m["mode"] == "validation" and m["iou_nms"] == 0.45 and isinstance(m["checkpoints"], list)
    for bad in ("../config/wireframe.yaml", "missing.pt"):
        r = client.post("/api/admin/models/active", json={"name": bad})
        assert r.status_code == 422 and r.json()["error"]["code"] == "checkpoint_rejected"


def test_spectrum_and_queue(client, scene):
    aid = upload(client, scene.rgb, scene.labels)
    wait_done(client, aid)
    png = client.get(f"/api/admin/analyses/{aid}/spectrum.png")
    assert png.status_code == 200 and png.content[:8] == b"\x89PNG\r\n\x1a\n"
    q = client.get("/api/admin/queue").json()
    assert q["depth"] == 0 and q["processed_since_start"] >= 1
    ov = client.get("/api/admin/overview").json()
    assert ov["poles"] >= 1 and sum(ov["poles_by_level"].values()) >= 1


def test_live_sensitivity_baseline(client):
    from app.devtools.synthetic import make_scene
    for i, k in enumerate((0.5, 1.2, 1.8)):
        sc = make_scene(seed=i, sag_scale=k, clutter=2 + 5 * i)
        wait_done(client, upload(client, sc.rgb, sc.labels, pole_code=f"S{i}"))
    base = client.get("/api/dashboard/sensitivity").json()
    live = client.get("/api/dashboard/sensitivity?alpha=1&beta=0&gamma=0").json()
    assert live["baseline"] == {"alpha": 1, "beta": 0, "gamma": 0} and len(live["grid"]) == 66
    cell = next(g for g in live["grid"] if (g["alpha"], g["beta"], g["gamma"]) == (1.0, 0.0, 0.0))
    assert cell["rho"] == pytest.approx(1.0)
    assert base["baseline"]["alpha"] == pytest.approx(1 / 3)
    _ = np  # numpy imported for parity with other test modules


def test_overview_counts_each_pole_once_by_latest_inspection(client):
    from app.devtools.synthetic import make_scene
    for k in (0.25, 0.4, 0.62):  # medium, medium, high
        sc = make_scene(seed=3, sag_scale=k)
        wait_done(client, upload(client, sc.rgb, sc.labels, pole_code="ONE"))
    ov = client.get("/api/admin/overview").json()
    assert ov["poles"] == 1 and sum(ov["poles_by_level"].values()) == 1
    assert ov["poles_by_level"]["high"] == 1 and len(ov["needs_review"]) == 1
