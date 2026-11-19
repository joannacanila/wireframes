"""The v2.21 three-zone sag meter (7c5a716, shown at the handoff commit 0916fd2) on the handoff v2.23 result page.

S7 (D-25) restored it for assessed wires; S12 (D-28) draws the reference and no-reference meters (tested in
test_s12_results_ui.py). S10 (D-26 / D-27) lit it from the server's power-line comparison with the provisional 3 %
reference, and draws a measurement-only meter in the wire-type colour for a wire with no applicable reference. These
tests run the page's own meter functions (Node) and check what the server gives them, so a regression cannot bring
back the withdrawn severity bands.
"""

from __future__ import annotations

from app.core.config import ROOT_DIR
from app.models.orm import Analysis
from tests.conftest import upload, wait_done

FIELD = ROOT_DIR / "app/web/assets/field.js"


# S18: meter(), meterRef(), meterType() and meterEmpty() lived only in the removed Field Screener technical view;
# the shared result view's meter is tested in test_s14_ux.py. The server-side guarantees below are unchanged.


def test_server_sends_no_zones_without_a_validated_reference(client, scene):
    aid = upload(client, scene.rgb, scene.labels)
    assert wait_done(client, aid)["status"] == "complete"
    r = client.get(f"/api/analysis/{aid}/result").json()
    assert r["wires"] and all(w.get("sag_bands") is None and w.get("delta_lim") is None for w in r["wires"])


def test_withdrawn_historical_levels_never_light_the_meter(client, scene):
    """A pre-S4 record keeps its stored 4.0 % reference in the database; the page must not receive it as zones."""
    aid = upload(client, scene.rgb, scene.labels)
    assert wait_done(client, aid)["status"] == "complete"
    with client.state.db.session() as s:
        a = s.get(Analysis, aid)
        d = a.detections[0]
        d.status, d.level, d.crs, d.delta_lim = "scored", "high", 10.0, 0.04
        a.metadata_json = {k: v for k, v in a.metadata_json.items() if k != "assessment_basis"}
    w = next(x for x in client.get(f"/api/analysis/{aid}/result").json()["wires"] if x["historical"])
    assert w["sag_bands"] is None and w["delta_lim"] is None and w["status"] != "scored"
