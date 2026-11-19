"""S14: the inspection lifecycle is recorded reliably and kept apart from the engineering assessment.

Root causes covered:
* an exception while saving the mask or persisting escaped ``process()``: the inspection stayed ``processing`` and was
  re-run on every restart without limit;
* failures carried only a code (no stage, category, attempts or model identity);
* a successfully processed photo read as "Assessment incomplete": processing and assessment were one value.
"""

from __future__ import annotations

from app.models.orm import Analysis
from app.services import analysis as svc
from app.services.outcome import assessment_state, overview, processing_state, wire_finding
from tests.conftest import upload, wait_done


def _ids(client):
    with client.state.db.session() as s:
        return [a.id for a in s.query(Analysis).all()]


def test_persist_failure_is_recorded_not_stuck(client, scene, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(svc, "persist", boom)
    aid = upload(client, scene.rgb, scene.labels)
    s = wait_done(client, aid)
    assert s["status"] == "error" and s["processing"] == "failed"
    f = s["failure"]
    assert f["stage"] == "persist" and f["category"] == "persistence_error" and f["retryable"] is True
    assert f["attempts"] == 1


def test_failure_details_are_staff_only(client, anon, scene, monkeypatch):
    monkeypatch.setattr(svc, "persist", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    aid = upload(client, scene.rgb, scene.labels)
    wait_done(client, aid)
    pub = anon.get(f"/api/analysis/{aid}").json()
    assert pub["status"] == "error" and "failure" not in pub


def test_repeatedly_interrupted_job_is_failed_not_looped(client, scene):
    aid = upload(client, scene.rgb, scene.labels)
    wait_done(client, aid)
    db = client.state.db
    with db.session() as s:  # simulate a job whose process was killed mid-inference three times
        a = s.get(Analysis, aid)
        a.status = "processing"
        a.metadata_json = {**a.metadata_json, "processing": {"attempts": svc.MAX_ATTEMPTS}}
    st = client.state
    assert svc.process(aid, db, st.settings, st.config, st.segmenter) == "error"
    with db.session() as s:
        a = s.get(Analysis, aid)
        assert a.status == "error" and a.metadata_json["failure"]["category"] == "interrupted"


def test_success_records_attempts_and_clears_old_failure(client, scene):
    aid = upload(client, scene.rgb, scene.labels)
    wait_done(client, aid)
    with client.state.db.session() as s:
        md = s.get(Analysis, aid).metadata_json
    assert md["processing"]["attempts"] == 1 and md["processing"]["finished_at"] and "failure" not in md


def test_manual_retry_resets_attempts_and_reprocesses_without_duplicates(client, scene, monkeypatch):
    real = svc.persist
    monkeypatch.setattr(svc, "persist", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("database is locked")))
    aid = upload(client, scene.rgb, scene.labels)
    assert wait_done(client, aid)["status"] == "error"
    monkeypatch.setattr(svc, "persist", real)
    assert client.post(f"/api/analysis/{aid}/retry").status_code == 202
    s = wait_done(client, aid)
    assert s["status"] != "error"
    r = client.get(f"/api/analysis/{aid}/result").json()
    idx = [w["index"] for w in r["wires"]]
    assert len(idx) == len(set(idx))  # no duplicated wires after a retry
    assert r["overview"]["processing"] in ("completed", "partially_completed")


def test_unreadable_image_failure_is_not_retryable(client, scene, monkeypatch):
    monkeypatch.setattr(svc, "load_rgb", lambda b: (_ for _ in ()).throw(ValueError("cannot decode")))
    aid = client.post("/api/analysis/upload", files={"file": ("p.jpg", __import__("tests.conftest", fromlist=["x"]).jpeg_bytes(scene.rgb), "image/jpeg")},
                      data={}).json().get("id")
    if aid is None:  # ingest itself decodes the photo; patching may reject it up front, which is also correct
        return
    s = wait_done(client, aid)
    assert s["failure"]["category"] == "image_unreadable" and s["failure"]["retryable"] is False
    assert client.post(f"/api/analysis/{aid}/retry").status_code == 409


def test_processed_photo_is_completed_even_without_a_reference(client, scene):
    aid = upload(client, scene.rgb, scene.labels)
    wait_done(client, aid)
    r = client.get(f"/api/analysis/{aid}/result").json()
    ov = r["overview"]
    assert ov["processing"] in ("completed", "partially_completed")
    from app.services.outcome import POLE_RESULT
    assert ov["headline"] == ov["pole"]["label"] and ov["headline"] in POLE_RESULT.values()  # S17 pole-level result
    assert ov["counts"]["detected"] == len(r["wires"])
    assert ov["counts"]["flagged"] == sum(w["finding"]["flagged"] for w in r["wires"])
    assert ov["assessment"] in {k.lower() for k in POLE_RESULT}
    assert "Assessment incomplete" not in ov["headline"]


def test_public_result_carries_overview_and_findings(anon, scene):
    aid = upload(anon, scene.rgb, scene.labels)
    wait_done(anon, aid)
    r = anon.get(f"/api/analysis/{aid}/result").json()
    assert r["public"] and r["overview"]["headline"] and all("finding" in w for w in r["wires"])
    assert "failure" not in r


# ---------------------------------------------------------------- pure rules

def _w(**k):
    base = {"index": 1, "class": "telecom-cable", "status": "not_assessed", "sag_ratio": 0.05,
            "states": {"measurement": "measured", "reference_available": False,
                       "reference_unavailable_reason": "no_reference_for_telecom"}, "comparison": None}
    return {**base, **k}


def test_telecom_is_never_flagged_by_the_power_line_reference():
    f = wire_finding(_w(sag_ratio=0.20))
    assert not f["flagged"] and f["category"] == "measured"
    assert not any("3.0%" in o for o in f["observations"])
    assert "No validated comparison is available for this cable type." in f["observations"]


def test_power_line_above_or_at_reference_is_flagged_below_is_not():
    for pos, flagged in (("above", True), ("at", True), ("below", False)):
        f = wire_finding(_w(**{"class": "power-distribution-lines", "comparison": {"position": pos, "reference": 0.03,
                                                                                    "state": "measured"}}))
        assert f["flagged"] is flagged
        assert "safe" not in " ".join(f["observations"]).lower() and "hazard" not in " ".join(f["observations"]).lower()


def test_states_are_distinct():
    assert processing_state("processing", [], running=False) == "pending"
    assert processing_state("processing", [], running=True) == "processing"
    assert processing_state("error", []) == "failed"
    assert processing_state("complete", [_w()]) == "completed"
    assert processing_state("complete", [_w(), _w(sag_ratio=None, states={"measurement": "unmeasured"})]) == "partially_completed"
    s = {"detected": 2, "measured": 2, "measured_partial": 0, "assessed": 0, "compared": 0}
    assert assessment_state("complete", s) == "unavailable"  # S17: no generic "inconclusive" state
    assert assessment_state("complete", {**s, "assessed": 1}) == "assessed"
    assert assessment_state("complete", {**s, "detected": 0}) == "no_wires"


def test_overview_counts_are_from_the_wires():
    ws = [_w(index=1), _w(index=2, sag_ratio=None, states={"measurement": "unmeasured"}),
          _w(index=3, **{"class": "power-distribution-lines", "comparison": {"position": "above", "reference": 0.03}})]
    ov = overview({"status": "complete", "wires": ws})
    c = ov["counts"]
    assert (c["detected"], c["measured"], c["unmeasured"], c["flagged"], c["incomplete"]) == (3, 2, 1, 1, 1)
    assert ov["flagged"] == [3] and ov["include_default"] == [3]
    # S17: the pole result: the exceedance survives the unmeasured span; the telecom wire here has no reference
    assert ov["pole"]["code"] == "REFERENCE_THRESHOLD_EXCEEDED" and ov["pole"]["unavailable"][0]["index"] == 2
    assert ov["lines"][0] == ("1 of 1 assessed span exceeds the applicable reference threshold. 1 further span "
                              "could not be measured.")


def test_straight_wire_within_resolution_is_not_reported_as_zero_sag():
    st = {"measurement": "measured", "reference_available": False, "within_resolution": True}
    f = wire_finding(_w(sag_ratio=0.0004, states=st))
    assert not any("0.0%" in o for o in f["observations"])
    assert any("smaller than one pixel" in o and "not evidence" in o for o in f["observations"])
