"""End-to-end API tests: upload -> queue -> result -> map -> report -> delete."""

import io

import numpy as np
import pypdfium2 as pdfium
import pytest

from app.pipeline.acquisition import file_has_gps
from tests.conftest import jpeg_bytes, upload, wait_done
from tests.test_acquisition_preprocess import exif_with


@pytest.mark.every_class_assessed
def test_full_flow_with_reference_masks(client, scene):
    aid = upload(client, scene.rgb, scene.labels, pole_code="SYN-1", barangay="Test",
                 latitude=14.33, longitude=120.94, span_m=40, temperature_c=33)
    assert wait_done(client, aid)["status"] == "complete"
    r = client.get(f"/api/analysis/{aid}/result").json()
    assert r["segmentation_source"] == "annotation"
    assert r["summary"]["wire_count"] == 5 and r["summary"]["scored_count"] == 5
    assert r["ranking"]["rank"] == 1
    assert 0 <= r["vni"]["value"] <= 1 and r["vni"]["band"] in {"Low", "Moderate", "High", "Severe"}
    for w, truth in zip(r["wires"], scene.truth_sag_ratio, strict=True):
        assert w["sag_ratio"] == pytest.approx(truth, rel=0.03)
        assert w["level"] in {"low", "medium", "high"} and 0 <= w["crs"] <= 10
        assert w["mask_polygons"] and len(w["catenary_curve"]) > 10
        assert "development_limits" in w["flags"]
    levels = {w["level"] for w in r["wires"]}
    assert levels == {"low", "medium", "high"}

    site = client.get("/api/dashboard/priority").json()["sites"][0]
    assert site["location"] == {"lat": 14.33, "lon": 120.94, "source": site["location"]["source"]} and site["rank"] == 1

    pdf = client.get(f"/api/reports/{aid}/pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    doc = pdfium.PdfDocument(pdf.content)
    text = "".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    doc.close()
    flat = " ".join(text.split())
    # v2.21: one short footer and three scope points replace the repeated notice block
    # S17 (Part 5.4): the canonical Important Notice, once, and every page footer points to it
    from app.core import notice
    assert flat.count(notice.TEXT) == 1 and "See the Important Notice" in flat
    assert "SYN-1" in text and "Findings Included in This Report" in text and "Field Verification" in text  # v2.20 formal
    tech = client.get(f"/api/reports/{aid}/pdf?technical=true")
    assert tech.status_code == 200 and b"%PDF" in tech.content[:5]

    assert client.delete(f"/api/analysis/{aid}").status_code == 204
    assert client.get(f"/api/analysis/{aid}").status_code == 404
    assert client.get("/api/dashboard/priority").json()["sites"] == []


def test_upload_strips_gps_and_stores_location_separately(client, scene):
    rgb = scene.rgb
    import PIL.Image
    buf = io.BytesIO()
    PIL.Image.fromarray(rgb).save(buf, "JPEG", exif=exif_with(gps=True))
    r = client.post("/api/analysis/upload", files={"file": ("g.jpg", buf.getvalue(), "image/jpeg"),
                                                     "labels": ("g.txt", scene.labels.encode())})
    aid = r.json()["id"]
    wait_done(client, aid)
    st = client.state
    from app.models.orm import Analysis
    with st.db.session() as s:
        a = s.get(Analysis, aid)
        stored = st.settings.upload_dir / a.stored_name
        loc = a.pole.location
        assert loc.source == "exif" and loc.latitude == pytest.approx(14.3294, abs=1e-3)
    assert not file_has_gps(stored)


def test_no_wires_detected_status(client, scene):
    aid = upload(client, scene.rgb, labels="")
    assert wait_done(client, aid)["status"] == "no_wires_detected"
    r = client.get(f"/api/analysis/{aid}/result").json()
    assert r["wires"] == [] and r["ranking"]["rank"] is None
    assert client.get(f"/api/reports/{aid}/pdf").status_code == 200


def test_fit_failed_status(client):
    rgb = np.random.default_rng(0).integers(0, 255, (600, 800, 3), dtype=np.uint8)
    # a straight-sided zig-zag wire: no catenary fits it within the residual limit
    poly = [(0.05, 0.40), (0.30, 0.70), (0.55, 0.40), (0.80, 0.70), (0.95, 0.40),
            (0.95, 0.43), (0.80, 0.73), (0.55, 0.43), (0.30, 0.73), (0.05, 0.43)]
    labels = "1 " + " ".join(f"{x} {y}" for x, y in poly) + "\n"
    aid = upload(client, rgb, labels)
    assert wait_done(client, aid)["status"] == "fit_failed"
    w = client.get(f"/api/analysis/{aid}/result").json()["wires"][0]
    assert w["status"] == "fit_failed" and w["crs"] is None and w["level"] is None


def test_upload_without_model_or_labels_is_refused_before_storage(client, scene):
    """No weights in the test environment: the upload is refused with a hint, nothing is stored."""
    r = client.post("/api/analysis/upload", files={"file": ("p.jpg", jpeg_bytes(scene.rgb), "image/jpeg")})
    assert r.status_code == 503
    err = r.json()["error"]
    assert err["code"] == "model_unavailable" and "reference polygons" in err["hint"]
    assert list(client.state.settings.upload_dir.iterdir()) == []
    assert client.get("/api/admin/inspections?include_test=true").json()["total"] == 0


@pytest.mark.parametrize("files,code", [
    ({"file": ("x.jpg", b"garbage", "image/jpeg")}, "undecodable"),
])
def test_upload_rejections(client, files, code):
    r = client.post("/api/analysis/upload", files=files)
    assert r.status_code == 422 and r.json()["error"]["code"] == code


def test_invalid_labels_and_location_rejected_without_orphan_files(client, scene):
    bad = client.post("/api/analysis/upload", files={"file": ("a.jpg", jpeg_bytes(scene.rgb)),
                                                      "labels": ("a.txt", b"7 0.1 0.1 0.2 0.2 0.3 0.3")})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_labels"
    half = client.post("/api/analysis/upload", files={"file": ("a.jpg", jpeg_bytes(scene.rgb))},
                       data={"latitude": "14.3"})
    assert half.json()["error"]["code"] == "invalid_location"
    assert list(client.state.settings.upload_dir.iterdir()) == []


@pytest.mark.test_reference
def test_priority_weights_validation_and_reranking(client, scene):
    for i, k in enumerate((0.6, 1.6)):
        from app.devtools.synthetic import make_scene
        sc = make_scene(seed=i, sag_scale=k, clutter=2 if i == 0 else 14)
        wait_done(client, upload(client, sc.rgb, sc.labels, pole_code=f"P{i}"))
    assert client.get("/api/dashboard/priority?alpha=0.5&beta=0.5&gamma=0.5").status_code == 422
    assert client.get("/api/dashboard/priority?alpha=0.5").status_code == 422
    r = client.get("/api/dashboard/priority?alpha=0&beta=0&gamma=1").json()
    assert [s["pole_code"] for s in r["sites"]] == ["P1", "P0"]
    from app.services import sites  # the weight sensitivity behind the technical priority report
    with client.state.db.session() as s:
        sens = sites.sensitivity_payload(s, client.state.config)
    assert sens["n_sites"] == 2 and len(sens["grid"]) == 66


def test_health_and_spa(client):
    h = client.get("/api/health").json()
    assert h["model"]["ready"] is False and "vni" in h["provisional"]
    assert client.get("/").headers["content-type"].startswith("text/html")
    assert client.get("/workspace").status_code == 200
    assert client.get("/assets/field.js").status_code == 200
