"""Landing/console routes, field/validation mode, retry, evaluation API, sample assets."""

import json

import pytest

from app.core.config import ROOT_DIR
from app.models.orm import Analysis
from tests.conftest import upload, wait_done

SAMPLES = ROOT_DIR / "app" / "web" / "assets" / "samples"


def test_landing_and_console_routes(client):
    landing = client.get("/")
    assert landing.status_code == 200 and "Launch Field Screener" in landing.text
    for path in ("/admin/sites", "/admin/evaluation", "/admin/analyses/" + "0" * 32):
        r = client.get(path)
        assert r.status_code == 200 and "WIREFRAME Admin" in r.text, path
    for old in ("/sites", "/evaluation", "/analysis/" + "0" * 32):  # the v2.1 console routes are retired
        assert client.get(old).status_code == 404, old
    for asset in ("landing.js", "landing.css", "status.js", "evaluation.js", "spectral.js", "logo.svg",
                  "field.js", "field.css", "admin.js", "admin.css", "lib.js"):
        assert client.get(f"/assets/{asset}").status_code == 200, asset


def test_health_reports_validation_mode_without_weights(client):
    h = client.get("/api/health").json()
    assert h["mode"] == "validation" and h["model"]["ready"] is False


def test_retry_requeues_a_failed_analysis(client, scene):
    aid = upload(client, scene.rgb, scene.labels)
    assert wait_done(client, aid)["status"] == "complete"
    assert client.post(f"/api/analysis/{aid}/retry").status_code == 409  # only failed analyses
    with client.state.db.session() as s:
        a = s.get(Analysis, aid)
        a.status, a.error_code, a.error_message = "error", "pipeline_exception", "simulated"
        a.detections.clear()
    r = client.post(f"/api/analysis/{aid}/retry")
    assert r.status_code == 202
    assert wait_done(client, aid)["status"] == "complete"
    assert client.get(f"/api/analysis/{aid}/result").json()["summary"]["scored_count"] == 5


def test_retry_without_labels_waits_for_the_model(client, scene):
    aid = upload(client, scene.rgb, scene.labels)
    wait_done(client, aid)
    with client.state.db.session() as s:
        a = s.get(Analysis, aid)
        a.status, a.error_code, a.label_name = "error", "model_unavailable", None
        a.detections.clear()
    r = client.post(f"/api/analysis/{aid}/retry")
    assert r.status_code == 503 and r.json()["error"]["code"] == "model_unavailable"


def test_evaluation_metrics_not_run_then_reported(client, tmp_path):
    m = client.get("/api/evaluation/metrics").json()
    assert m["targets"]["map50"]["value"] == 0.70 and m["targets"]["h3_rmse"]["op"] == "<="
    assert all(s["status"] == "not_run" and "command" in s for s in m["sections"].values())
    from ml.evaluate import geometry_benchmark, write_section
    path = client.state.settings.data_dir / "evaluation" / "report.json"
    write_section("geometry_synthetic", geometry_benchmark(n_scenes=2), path)
    g = client.get("/api/evaluation/metrics").json()["sections"]["geometry_synthetic"]
    assert g["status"] == "complete" and g["synthetic"] is True and g["wires"] == 10
    assert g["fit_failures"] == 0 and g["relative_sag_ratio_error"]["median"] < 0.05


def test_pdf_carries_validation_mode_badge(client, scene):
    import pypdfium2 as pdfium
    aid = upload(client, scene.rgb, scene.labels)
    wait_done(client, aid)
    pdf = pdfium.PdfDocument(client.get(f"/api/reports/{aid}/pdf").content)
    text = pdf[0].get_textpage().get_text_range()
    pdf.close()
    assert "VALIDATION MODE" in text and "REFERENCE MASKS" in text


def test_sample_assets_are_real_pipeline_output(cfg):
    res = json.loads((SAMPLES / "synthetic-pole.result.json").read_text())
    assert res["synthetic"] is True and res["segmentation_source"] == "annotation"
    assert {w["level"] for w in res["wires"]} == {"low", "medium", "high"}
    from app.pipeline.mspi import mspi
    crs = [w["crs"] for w in res["wires"] if w["crs"] is not None]
    assert res["mspi"]["value"] == pytest.approx(mspi(res["vni"]["value"], crs, *res["mspi"]["weights"]))
    labels = (SAMPLES / "synthetic-pole.txt").read_text().strip().splitlines()
    assert len(labels) == len(res["wires"])
