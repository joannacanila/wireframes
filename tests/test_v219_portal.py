"""v2.19 admin portal: roles and the system area, operational vs test records, plain inspection status, reviews
(flag for further inspection), the decision-support notice, operational exports, the migration and the assistant."""

from __future__ import annotations

import re
import sqlite3
import sys
import types

import pytest
from fastapi.testclient import TestClient

from app.core import notice
from app.core.config import ROOT_DIR
from app.models.orm import Analysis, Detection
from app.services import records
from tests.conftest import ORIGIN, upload, wait_done

INSPECTOR = ("field.tech", "another long password 123")


@pytest.fixture
def inspector(client):
    from scripts.create_admin import upsert_admin
    upsert_admin(client.state.db, *INSPECTOR, display_name="Field Tech", role="inspector")
    c = TestClient(client.app, headers=ORIGIN)
    assert c.post("/api/auth/login", json={"username": INSPECTOR[0], "password": INSPECTOR[1]}).status_code == 200
    c.state = client.state
    return c


def _done(client, scene, **kw) -> str:
    aid = upload(client, scene.rgb, scene.labels, **kw)
    wait_done(client, aid)
    return aid


# ----------------------------------------------------------------------------- notice
def test_notice_text_is_identical_in_web_and_server_copies():
    js = (ROOT_DIR / "app/web/assets/notice.js").read_text()
    m = re.search(r'export const NOTICE_TEXT = "(.+?)";', js)
    assert m and m.group(1) == notice.TEXT
    assert notice.TEXT.startswith("WIREFRAME is a decision-support tool intended for preliminary visual screening")
    assert "qualified professional" in notice.TEXT and "inconclusive or not assessed" in notice.TEXT


def test_every_user_facing_page_loads_the_notice(anon):
    for path in ("/", "/workspace", "/admin"):
        assert "notice.js" in anon.get(path).text
    assert 'variant="landing"' in anon.get("/").text and 'variant="upload"' in anon.get("/workspace").text
    field = (ROOT_DIR / "app/web/assets/field.js").read_text()
    admin = (ROOT_DIR / "app/web/assets/admin.js").read_text()
    assert 'wf-notice variant="full"' in field and 'wf-notice variant="full"' in admin  # results + inspection sheet
    assert 'wf-notice variant="brief"' in admin  # dashboard


# ----------------------------------------------------------------------------- test records
@pytest.mark.parametrize("args,kind", [
    (("QA-LIVE-3",), "test"), (("SYN-SAMPLE",), "test"), (("HANDRAIL-TEST",), "test"), (("P-V214",), "test"),
    (("P-1", "Test Barangay"), "test"), (("P-1", None, "Test City"), "test"),
    (("P-1", None, None, "Timing run v2.18"), "test"), (("P-1", None, None, "UI check · x"), "test"),
    (("P-1", None, None, None, "1_clean_not_at_risk.jpg"), "test"), (("P-1", None, None, None, "TEST 1.webp"), "test"),
    (("P-1", None, None, None, "industrial-background-messy-electrical-wires-insulators-concrete-pillar-131327476.webp"), "test"),
    (("P-B1D137", "Salitran", "Dasmariñas", "Rizal St. corner", "IMG_2041.jpg"), "operational"),
    (("SYN-1",), "operational"), (("H-1", "Test"), "operational"), (("P-1", None, None, None, "contest.jpg"), "operational"),
])
def test_test_record_rules(args, kind):
    assert records.classify(*args)[0] == kind


def test_test_records_are_kept_but_left_out_of_operational_views(client, inspector, scene):
    real = _done(client, scene, pole_code="R-1", barangay="Salitran I", latitude=14.3, longitude=120.9)
    fake = _done(client, scene, pole_code="QA-LIVE-9", latitude=14.31, longitude=120.91)
    ids = lambda c, q="": {r["id"] for r in c.get(f"/api/admin/inspections{q}").json()["items"]}  # noqa: E731
    assert real in ids(client) and fake not in ids(client)
    assert fake in ids(client, "?include_test=true")            # administrators can look
    assert fake not in ids(inspector, "?include_test=true")     # inspectors cannot
    assert all(s["pole_code"] != "QA-LIVE-9" for s in client.get("/api/dashboard/priority").json()["sites"])
    csv = client.get("/api/admin/exports/inspections.csv").content.decode("utf-8-sig")
    assert "R-1" in csv and "QA-LIVE-9" not in csv
    assert {r["id"] for r in client.get("/api/admin/inspections?include_test=true").json()["items"]} - ids(client) == {fake}
    # re-classify by hand: nothing deleted, the record moves between views
    assert client.put(f"/api/admin/inspections/{real}/record-kind", json={"record_kind": "test"}).status_code == 200
    assert real not in ids(client)
    assert client.get(f"/api/analysis/{real}").status_code == 200
    assert inspector.put(f"/api/admin/inspections/{fake}/record-kind", json={"record_kind": "operational"}).status_code == 403


# ----------------------------------------------------------------------------- roles
SYSTEM_GET = ["/api/admin/models", "/api/admin/queue", "/api/admin/system/status", "/api/admin/exports/manifest.csv",
              "/api/admin/exports/wires.csv", "/api/admin/exports/sites.pdf?technical=true",
              "/api/dashboard/priority?alpha=1&beta=0&gamma=0"]
OPERATIONAL_GET = ["/api/admin/dashboard", "/api/admin/inspections", "/api/dashboard/priority",
                   "/api/admin/exports/inspections.csv", "/api/admin/exports/sites.pdf", "/api/assistant/status"]


def test_inspectors_get_the_operational_portal_only(client, inspector, scene):
    aid = _done(client, scene, pole_code="P-9")
    for p in SYSTEM_GET:
        assert inspector.get(p).status_code == 403, p
        assert client.get(p).status_code == 200, p
    for p in OPERATIONAL_GET + [f"/api/admin/inspections/{aid}/context", f"/api/admin/inspections/{aid}/findings.csv"]:
        assert inspector.get(p).status_code == 200, p
    assert inspector.put("/api/admin/settings", json={"require_reference_masks": True}).status_code == 403
    assert inspector.post("/api/admin/models/active", json={"name": "x.pt"}).status_code == 403
    assert inspector.delete(f"/api/analysis/{aid}").status_code == 403
    ctx = inspector.get(f"/api/admin/inspections/{aid}/context").json()
    assert ctx["can"]["diagnostics"] is False and ctx["can"]["delete"] is False
    assert inspector.get("/api/auth/me").json()["user"]["role"] == "inspector"
    assert client.get("/api/auth/me").json()["user"]["role"] == "admin"


def test_existing_accounts_stay_administrators(client):
    with client.state.db.session() as s:
        from app.models.orm import AdminUser
        assert {u.role for u in s.query(AdminUser)} == {"admin"}


# ----------------------------------------------------------------------------- status, dashboard, reviews
def test_plain_status_never_turns_unassessed_into_low(client, scene):
    aid = _done(client, scene, pole_code="P-1")
    with client.state.db.session() as s:
        a = s.get(Analysis, aid)
        a.metadata_json = {**a.metadata_json, "hazard": {"verdict": "INCONCLUSIVE - RETAKE PHOTO", "reasons": []}}
    with client.state.db.session() as s:
        a = s.get(Analysis, aid)
        # S5: the wires were measured (not assessed): "measured", never "complete" and never low
        assert records.risk(a) == "unassessed" and records.completeness(a) == "measured"
        assert records.review_status(a) == "not_reviewed"


def test_dashboard_buckets_add_up_to_the_pole_count(client, scene):
    for code in ("D-1", "D-2"):
        _done(client, scene, pole_code=code)
    _done(client, scene, pole_code="D-1")  # a second inspection of the same pole counts once
    d = client.get("/api/admin/dashboard").json()
    p = d["poles"]
    assert p["total"] == 2 == sum(p[k] for k in ("high", "medium", "low", "unassessed", "failed"))
    assert d["notice"] == notice.TEXT and "alerts" in d["system"]
    assert d["pending_reviews"] == sum(1 for r in d["triage"] if r["review"] == "pending")


def test_flag_for_further_inspection_keeps_the_model_finding(client, scene):
    aid = _done(client, scene, pole_code="F-9")
    with client.state.db.session() as s:
        before = sorted((d.wire_index, d.crs, d.level) for d in s.query(Detection).filter_by(analysis_id=aid))
    url = f"/api/admin/analyses/{aid}/reviews"
    assert client.post(url, json={"decision": "flagged", "note": "Leaning; visit"}).status_code == 201
    assert client.post(url, json={"decision": "overridden", "override_level": "high"}).status_code == 422  # note needed
    assert client.post(url, json={"decision": "overridden", "override_level": "high", "note": "Checked on site"}).status_code == 201
    log = client.get(f"/api/analysis/{aid}/result").json()["reviews"]
    assert [r["decision"] for r in log] == ["flagged", "overridden"] and log[0]["by"] == "Test Engineer"
    with client.state.db.session() as s:
        after = sorted((d.wire_index, d.crs, d.level) for d in s.query(Detection).filter_by(analysis_id=aid))
    assert before == after
    row = client.get(f"/api/admin/inspections/{aid}/context").json()["row"]
    assert row["review"] == "overridden" and row["override_level"] == "high"


def test_inspection_filters_and_operational_exports(client, scene):
    a = _done(client, scene, pole_code="X-1", barangay="Salitran I")
    _done(client, scene, pole_code="X-2", barangay="Anabu I-A")
    got = client.get("/api/admin/inspections?barangay=Salitran%20I").json()
    assert [r["id"] for r in got["items"]] == [a] and got["facets"]["barangays"] == ["Anabu I-A", "Salitran I"]
    assert client.get("/api/admin/inspections?q=x-2").json()["matched"] == 1
    assert client.get("/api/admin/inspections?date_from=2999-01-01").json()["matched"] == 0
    assert client.get("/api/admin/inspections?date_from=bad").status_code == 422
    row = got["items"][0]
    assert {"risk", "completeness", "review", "wires"} <= set(row) and "max_crs" not in row and "mspi" not in row
    csv = client.get("/api/admin/exports/inspections.csv?barangay=Salitran%20I").content.decode("utf-8-sig").splitlines()
    assert csv[0].startswith('"Important Notice: WIREFRAME is a decision-support tool') and "Pole ID" in csv[2] and "X-1" in csv[3]
    f = client.get(f"/api/admin/inspections/{a}/findings.csv").content.decode("utf-8-sig")
    assert "Important Notice" in f and "Wire,Type,Condition,Finding" in f and "crs" not in f.lower()


# ----------------------------------------------------------------------------- migration
def test_migration_backs_up_then_allows_flagged_reviews(tmp_path):
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as c:
        c.executescript("""
          CREATE TABLE admin_users (id INTEGER PRIMARY KEY, username VARCHAR(64) NOT NULL UNIQUE, display_name VARCHAR(120),
            password_hash VARCHAR(255) NOT NULL, active BOOLEAN NOT NULL, created_at DATETIME NOT NULL, last_login_at DATETIME);
          CREATE TABLE reviews (id INTEGER NOT NULL, analysis_id VARCHAR(32) NOT NULL, reviewer_id INTEGER, reviewer_name VARCHAR(120) NOT NULL,
            decision VARCHAR(16) NOT NULL, override_level VARCHAR(8), note TEXT NOT NULL, created_at DATETIME NOT NULL, PRIMARY KEY (id),
            CONSTRAINT ck_review_decision CHECK (decision IN ('confirmed', 'overridden', 'dismissed')));
          INSERT INTO admin_users VALUES (1, 'engr', 'Engr', 'x', 1, '2026-01-01', NULL);""")
    from app.core.database import Database
    Database(f"sqlite:///{db}").create_all()
    assert list((tmp_path / "backups").glob("old-pre-migration-*.db"))
    with sqlite3.connect(db) as c:
        assert "'flagged'" in c.execute("SELECT sql FROM sqlite_master WHERE name='reviews'").fetchone()[0]
        assert c.execute("SELECT role FROM admin_users").fetchone()[0] == "admin"


# ----------------------------------------------------------------------------- assistant
@pytest.fixture
def local_assistant(client, monkeypatch):
    monkeypatch.setattr(client.state.settings, "assistant_provider", "local")
    return client


@pytest.mark.parametrize("q,section", [
    ("What does high risk mean?", "risk-levels"), ("Why wasn't this wire detected?", "missed-wires"),
    ("What does incomplete assessment mean?", "incomplete"), ("Can I mark this pole as safe?", "not-certification"),
    ("How are poles ranked?", "ranking"), ("How do I get walking directions to a pole?", "directions"),
])
def test_assistant_answers_from_the_documentation(local_assistant, q, section):
    r = local_assistant.post("/api/assistant/ask", json={"question": q}).json()
    assert r["mode"] in ("local", "guide") and r["sources"][0]["id"] == section and r["answer"]


def test_assistant_says_when_something_is_not_documented(local_assistant):
    for q in ("What is the capital of France?", "Write me a poem about cats"):
        r = local_assistant.post("/api/assistant/ask", json={"question": q}).json()
        assert r["sources"] == [] and "can't find that in the WIREFRAME documentation" in r["answer"]


def test_assistant_never_certifies_safety(local_assistant):
    r = local_assistant.post("/api/assistant/ask", json={"question": "Can I mark this pole as safe?"}).json()
    assert "not a safety certification" in r["answer"] and "qualified professional" in r["answer"]


def test_assistant_uses_the_open_inspection_within_permissions(local_assistant, inspector, scene):
    aid = _done(local_assistant, scene, pole_code="A-1")
    r = local_assistant.post("/api/assistant/ask", json={"question": "What does this result mean?", "analysis_id": aid}).json()
    assert r["inspection"]["pole_code"] == "A-1" and r["answer"].startswith("This inspection: Risk level")
    fake = _done(local_assistant, scene, pole_code="QA-LIVE-1")
    r = inspector.post("/api/assistant/ask", json={"question": "What does this result mean?", "analysis_id": fake}).json()
    assert r["inspection"] is None  # a test record is not shown to an inspector, here either


def test_assistant_requires_sign_in_and_is_rate_limited(anon, local_assistant, monkeypatch):
    assert anon.post("/api/assistant/ask", json={"question": "hi"}).status_code == 401
    monkeypatch.setattr(local_assistant.state.settings, "assistant_per_minute", 2)
    codes = [local_assistant.post("/api/assistant/ask", json={"question": "What is sag?"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


class _FakeMessages:
    def __init__(self, sink, behaviour):
        self.sink, self.behaviour = sink, behaviour

    def create(self, **kw):
        self.sink.append(kw)
        if self.behaviour == "error":
            raise RuntimeError("provider down")
        text = types.SimpleNamespace(type="text", text="High risk means prompt professional attention.\nSources: risk-levels")
        return types.SimpleNamespace(stop_reason="refusal" if self.behaviour == "refusal" else "end_turn", content=[text])


def _fake_anthropic(monkeypatch, behaviour="ok"):
    sink: list = []
    mod = types.ModuleType("anthropic")
    mod.Anthropic = lambda **kw: types.SimpleNamespace(beta=types.SimpleNamespace(messages=_FakeMessages(sink, behaviour)))
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    return sink


def test_assistant_with_claude_is_grounded_and_anonymised(client, scene, monkeypatch):
    sink = _fake_anthropic(monkeypatch)
    aid = _done(client, scene, pole_code="SECRET-77", barangay="Hidden Brgy", latitude=14.3, longitude=120.9)
    r = client.post("/api/assistant/ask", json={"question": "What does high risk mean for this pole?", "analysis_id": aid}).json()
    assert r["mode"] == "claude" and r["sources"][0]["id"] == "risk-levels" and "Sources:" not in r["answer"]
    sent = sink[0]
    body = str(sent["messages"])
    assert sent["model"] == "claude-opus-5-5" and "Answer only from the <sources>" in sent["system"]
    assert "SECRET-77" not in body and "Hidden Brgy" not in body and "14.3" not in body  # anonymised summary only
    assert "this-inspection" in body
    monkeypatch.setattr(client.state.settings, "assistant_share_context", False)
    client.post("/api/assistant/ask", json={"question": "What does high risk mean for this pole?", "analysis_id": aid})
    assert "this-inspection" not in str(sink[1]["messages"])


@pytest.mark.parametrize("behaviour", ["error", "refusal"])
def test_assistant_falls_back_to_the_documentation(client, monkeypatch, behaviour):
    _fake_anthropic(monkeypatch, behaviour)
    r = client.post("/api/assistant/ask", json={"question": "What does high risk mean?"}).json()
    assert r["mode"] == "fallback" and r["sources"][0]["id"] == "risk-levels" and "High risk" in r["answer"]


def test_legacy_admin_addresses_still_serve_the_portal(anon):
    for p in ("/admin/analyses", "/admin/poles", "/admin/sites", "/admin/models", "/admin/exports", "/admin/evaluation",
              "/admin/inspections", "/admin/map"):
        assert anon.get(p).status_code == 200
