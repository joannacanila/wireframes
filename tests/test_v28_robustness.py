"""v2.8: preprocessing candidates, WireNet TTA and uncertainty, server edge cases."""

import cv2
import numpy as np
import pytest

from app.core.config import EnhancementConfig
from app.pipeline import enhance as E
from ml.stress import CONDITIONS, apply_condition


def test_blockiness_detects_heavy_jpeg(scene):
    g = cv2.cvtColor(scene.rgb, cv2.COLOR_RGB2GRAY)
    ok, buf = cv2.imencode(".jpg", g, [cv2.IMWRITE_JPEG_QUALITY, 15])
    q15 = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
    assert E.blockiness(g) < 1.15 < E.blockiness(q15)


def test_edge_denoise_triggers_on_jpeg_only_and_keeps_wires(scene):
    cfg = EnhancementConfig(edge_denoise=True)
    jpg = apply_condition(scene.rgb, "jpeg", "x.jpg")
    out = E.enhance(jpg, cfg)
    assert "edge_denoise" in out.applied and "edge_denoise" not in E.enhance(scene.rgb, cfg).applied
    # thin dark wire still darker than its surroundings after filtering
    img = np.full((200, 400, 3), 200, np.uint8)
    cv2.line(img, (0, 100), (399, 100), (30, 30, 30), 2)
    f = E.edge_denoise(img, 4.0)
    assert f[100, 50:350].mean() < 120 and abs(int(f[60, 200, 0]) - 200) < 5


def test_dark_trigger_can_be_disabled(scene):
    dark = (scene.rgb * 0.18).astype(np.uint8)
    assert "clahe" in E.enhance(dark, EnhancementConfig()).applied
    assert "clahe" not in E.enhance(dark, EnhancementConfig(dark_l_max=0.0)).applied or \
        E.measure(dark).contrast <= EnhancementConfig().low_contrast_max


def test_retinex_and_steps_order(scene):
    cfg = EnhancementConfig(retinex=True, edge_denoise=True, mode="auto")
    dark = np.clip(scene.rgb.astype(np.float32) * 0.15 + np.random.default_rng(0).normal(0, 4, scene.rgb.shape), 0, 255).astype(np.uint8)
    out = E.enhance(dark, cfg)
    order = {"denoise": 0, "edge_denoise": 0, "retinex": 1, "clahe": 2, "unsharp": 3}
    assert "retinex" in out.applied and [order[s] for s in out.applied] == sorted(order[s] for s in out.applied)
    assert E.measure(out.rgb).mean_l > E.measure(dark).mean_l


@pytest.mark.parametrize("cond", CONDITIONS)
def test_stress_conditions_are_deterministic_and_valid(scene, cond):
    a = apply_condition(scene.rgb, cond, "p1.jpg")
    assert a.shape == scene.rgb.shape and a.dtype == np.uint8
    np.testing.assert_array_equal(a, apply_condition(scene.rgb, cond, "p1.jpg"))


def test_tta_and_uncertainty():
    pytest.importorskip("segmentation_models_pytorch")
    from app.pipeline.wirenet import N_CLASSES, build_model, predict_probs, predict_probs_tta, uncertainty
    m = build_model().eval()
    x = np.random.default_rng(1).integers(0, 255, (128, 160, 3), dtype=np.uint8)
    p = predict_probs(m, x, "cpu")
    pf = predict_probs_tta(m, x, "cpu", "flip")
    assert pf.shape == (N_CLASSES, 128, 160) and np.allclose(pf.sum(0), 1, atol=1e-4)
    assert predict_probs_tta(m, x, "cpu", "flip_ms").shape == pf.shape
    u = uncertainty(p, predict_probs(m, np.ascontiguousarray(x[:, ::-1]), "cpu")[:, :, ::-1])
    assert u["wire_pixels"] == 0 or (0 <= u["entropy"] <= 1 and 0 <= u["disagreement"] <= 1)
    none = np.zeros((N_CLASSES, 8, 8), np.float32)
    none[0] = 1
    assert uncertainty(none)["entropy"] is None


def _labels_for(scene):
    return scene.labels


@pytest.mark.parametrize("shape", [(320, 3000), (1600, 400)])
def test_server_handles_extreme_aspect_ratios(client, scene, shape):
    from tests.conftest import upload, wait_done
    h, w = shape
    img = cv2.resize(scene.rgb, (w, h), interpolation=cv2.INTER_AREA)
    aid = upload(client, img, scene.labels)
    assert wait_done(client, aid)["status"] in {"complete", "fit_failed", "no_wires_detected"}
    res = client.get(f"/api/analysis/{aid}/result").json()
    # v2.21: INCONCLUSIVE is a valid outcome when too few spans of the resized photo can be assessed
    assert res["hazard"]["verdict"] in {"AT RISK - HIGH", "AT RISK - MODERATE", "NOT AT RISK",
                                        "INCONCLUSIVE - RETAKE PHOTO"}
    pdf = client.get(f"/api/reports/{aid}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def test_server_no_wires_and_vegetation_only(client, scene):
    from tests.conftest import upload, wait_done
    aid = upload(client, scene.rgb, "")
    assert wait_done(client, aid)["status"] == "no_wires_detected"
    res = client.get(f"/api/analysis/{aid}/result").json()
    assert res["hazard"]["verdict"] == "NOT AT RISK" and "0 power and 0 telecom" in res["hazard"]["reasons"][-1]
    assert client.get(f"/api/reports/{aid}/pdf").content[:4] == b"%PDF"
    veg_only = "0 0.1 0.1 0.4 0.1 0.4 0.5 0.1 0.5\n"
    aid2 = upload(client, scene.rgb, veg_only)
    wait_done(client, aid2)
    res2 = client.get(f"/api/analysis/{aid2}/result").json()
    assert res2["summary"]["vegetation_count"] == 1 and res2["hazard"]["verdict"] == "NOT AT RISK"
    assert client.get(f"/api/reports/{aid2}/pdf").content[:4] == b"%PDF"
