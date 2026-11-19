"""S17 (Part 6): one screening result everywhere.

The pole-level result (outcome.pole_assessment) is computed once on the server. The result page, the dashboard
queue, the Inspections list, the map and the CSV export must all show that same result for the same inspection,
and keep it separate from the queue status (where the record is in review).
"""

from __future__ import annotations

import csv
import io

from app.services.outcome import POLE_RESULT
from tests.conftest import upload, wait_done

REVIEW = {"decision": "confirmed", "note": "checked on site"}


def _everywhere(client, aid: str) -> dict[str, str | None]:
    page = client.get(f"/api/analysis/{aid}/result").json()["overview"]["pole"]
    ops = client.get("/api/admin/operations").json()
    queue = next(r for r in ops["queue"] if r["id"] == aid)  # unreviewed, so it must be in the queue
    lst = client.get("/api/admin/inspections", params={"per_page": 500}).json()
    row = next(r for r in lst["items"] if r["id"] == aid)
    site = next(x for x in client.get("/api/dashboard/priority").json()["sites"] if x["analysis_id"] == aid)
    body = client.get("/api/admin/exports/inspections.csv", params={"ids": aid}).text
    rows = [r for r in csv.reader(io.StringIO(body)) if r and len(r) > 5]
    head, data = rows[0], rows[1]
    return {"page": POLE_RESULT[page["code"]],
            "queue": (queue.get("result") or {}).get("label"),
            "inspections": (row.get("result") or {}).get("label"),
            "map": (site.get("result") or {}).get("label"),
            "csv": data[head.index("Screening result")]}


def test_the_result_is_identical_on_every_page(client, scene):
    # two different scenes (all reference polygons, then only the first two) so the check is not one constant
    some = "\n".join(scene.labels.strip().splitlines()[:2])
    aids = [upload(client, scene.rgb, scene.labels, pole_code="S17-A"), upload(client, scene.rgb, some, pole_code="S17-B")]
    for aid in aids:
        wait_done(client, aid)
        got = _everywhere(client, aid)
        assert len(set(got.values())) == 1 and None not in got.values(), got


def test_result_and_queue_status_are_separate_fields(client, scene):
    aid = upload(client, scene.rgb, scene.labels, pole_code="S17-C")
    wait_done(client, aid)
    row = next(r for r in client.get("/api/admin/inspections", params={"per_page": 500}).json()["items"] if r["id"] == aid)
    assert row["result"]["code"] in POLE_RESULT and row["op_status"] not in POLE_RESULT
    # a review changes the queue status, never the screening result
    before = row["result"]
    r = client.post(f"/api/admin/analyses/{aid}/reviews", json=REVIEW)
    assert r.status_code == 201, r.text
    after = next(x for x in client.get("/api/admin/inspections", params={"per_page": 500}).json()["items"] if x["id"] == aid)
    assert after["result"] == before and after["op_status"] == "reviewed"


def test_frontends_render_the_server_result_not_their_own():
    from app.core.config import ROOT_DIR
    a = (ROOT_DIR / "app/web/assets/admin.js").read_text()
    m = (ROOT_DIR / "app/web/assets/admin-map.js").read_text()
    # S18: risk and review status are the server's (records._s18); the dashboard, Inspections and the map draw them
    assert a.count("${riskCell(r)}") == 2 and a.count("${reviewCell(r)}") == 2  # dashboard and Inspections
    assert "riskChip(r.risk_level)" in a and "reviewChip(r.review_status, r.review_status_label)" in a
    assert "riskChip(r.risk_level)" in m and "reviewChip(r.review_status, r.review_status_label)" in m
    for js in (a, m):
        assert "problem_count >=" not in js and "high_from" not in js and "moderate_from" not in js
    for js in (a, m):
        assert "pole_assessment" not in js and "REFERENCE_THRESHOLD_EXCEEDED\" ?" not in js
