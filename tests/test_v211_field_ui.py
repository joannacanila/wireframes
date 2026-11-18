"""v2.11: full WireNet mask in the API, per-span reasons, dual-polarity inference, field UI assets."""

from __future__ import annotations

import io
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import CLASSES, Settings
from app.core.state import build_state
from app.main import create_app
from app.pipeline.segment import WireDetection, _bbox
from tests.conftest import sign_in_staff

ORIGIN = {"origin": "http://testserver"}
TEL = CLASSES.index("telecom-cable") + 1


class MaskSegmenter:
    """A model stand-in that finds two wires (one long, one short) and exposes its class masks,
    like HybridSegmenter does."""
    source, error, names, path = "model", None, {}, Path("stub.pt")
    last_uncertainty = {"wire_pixels": 9000, "entropy": 0.1, "disagreement": 0.05}
    last_polarity = "normal"

    def __init__(self):
        self.last_class_masks = None

    def load(self) -> bool:
        return True

    def segment(self, x, lb):
        h, w = lb.orig_h, lb.orig_w
        long_ = np.zeros((h, w), np.uint8)
        cv2.line(long_, (20, 120), (w - 20, 160), 1, 3)
        short = np.zeros((h, w), np.uint8)
        cv2.line(short, (40, 300), (100, 305), 1, 3)          # chord ~60 px < 100 px precision gate
        frag = np.zeros((h, w), np.uint8)
        cv2.line(frag, (400, 380), (420, 382), 1, 2)          # a fragment that never becomes a span
        tel = (long_ | short | frag).astype(bool)
        self.last_class_masks = {TEL: tel}
        return [WireDetection("telecom-cable", 0.9, m.astype(bool), _bbox(m.astype(bool))) for m in (long_, short)]


@pytest.fixture
def mclient(tmp_path, cfg):
    st = build_state(Settings(data_dir=tmp_path, start_worker=True), cfg, segmenter=MaskSegmenter())
    with TestClient(create_app(st), headers=ORIGIN) as c:
        c.state = st
        sign_in_staff(c, st)
        yield c


def _photo():
    rng = np.random.default_rng(3)
    img = np.full((480, 640, 3), 200, np.uint8)
    img[:240] = 230
    return np.clip(img.astype(int) + rng.integers(-5, 6, img.shape), 0, 255).astype(np.uint8)


def _upload_and_wait(c) -> dict:
    b = io.BytesIO()
    Image.fromarray(_photo()).save(b, "PNG")
    r = c.post("/api/analysis/upload", files={"file": ("p.png", b.getvalue(), "image/png")})
    assert r.status_code == 202
    aid = r.json()["id"]
    for _ in range(600):
        if c.get(f"/api/analysis/{aid}").json()["status"] != "processing":
            break
        time.sleep(0.05)
    res = c.get(f"/api/analysis/{aid}/result")
    assert res.status_code == 200
    return res.json()


def test_result_exposes_full_wirenet_mask_and_coverage(mclient):
    res = _upload_and_wait(mclient)
    wm = res["wire_mask"]
    assert wm["url"].endswith("/wiremask.png") and wm["wire_pixels"] > 0
    assert 0 < wm["span_coverage"] < 1          # the fragment is in the mask but in no span
    png = mclient.get(wm["url"])
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(png.content))
    assert img.size == (640, 480) and img.mode == "P"
    a = np.asarray(img)
    assert set(np.unique(a)) <= {0, TEL} and (a == TEL).sum() == wm["wire_pixels"]
    tel = res["telemetry"]
    assert tel["model"].startswith("WireNet-R34") and tel["resolution"] == [640, 480] and tel["inference_s"] is not None


@pytest.mark.test_reference
def test_every_unmeasured_span_has_a_reason(mclient):
    res = _upload_and_wait(mclient)
    unmeasured = [w for w in res["wires"] if w["crs"] is None]
    assert unmeasured, "the short wire must be unmeasured"
    for w in unmeasured:
        assert w["reason_code"] and w["reason"] and w["reason"] != "Not measured"
    short = next(w for w in unmeasured if w["reason_code"] == "chord_below_precision_gate")
    assert "100 px" in short["reason"]
    assert all(w["reason"] is None for w in res["wires"] if w["crs"] is not None)
    assert res["parameters"]["hazard_rule"]["sag_rule"] == "corroborated"


def test_wire_mask_404_without_mask_and_deleted_with_analysis(tmp_path, cfg, mclient):
    from app.services.analysis import wire_mask_path
    res = _upload_and_wait(mclient)
    p = wire_mask_path(mclient.state.settings, res["id"])
    assert p.exists()
    assert mclient.get("/api/analysis/" + "0" * 32 + "/wiremask.png").status_code == 404


@pytest.mark.parametrize("code,needle", [("chord_below_precision_gate", "100 px"), ("residual_too_large", "crossing"),
                                         ("non_positive_a", "upward"), ("no_convergence", "converge"),
                                         ("too_short", "centreline"), ("unknown_new_code", "Unknown new code")])
def test_reason_texts(code, needle):
    from app.services.reasons import wire_reason
    c, t = wire_reason("too_short", code, [], 100)
    assert c == code and needle.lower() in t.lower()
    assert wire_reason("scored", None, ["truncated"]) == (None, None)
    assert wire_reason("service_drop", None, [])[0] == "service_drop"


def test_invert_luminance_keeps_letterbox_padding():
    from app.pipeline.preprocess import letterbox_geometry
    from app.pipeline.wirenet import invert_luminance, mean_l_star
    lb = letterbox_geometry(200, 100, 256)
    x = np.full((256, 256, 3), 114, np.uint8)
    x[lb.pad_y: lb.pad_y + lb.new_h, lb.pad_x: lb.pad_x + lb.new_w] = 20      # dark photo
    y = invert_luminance(x, lb)
    assert (y[: lb.pad_y] == 114).all() and (y[lb.pad_y + lb.new_h:] == 114).all()
    content = y[lb.pad_y: lb.pad_y + lb.new_h, lb.pad_x: lb.pad_x + lb.new_w]
    assert mean_l_star(content) > 80 > mean_l_star(x[lb.pad_y: lb.pad_y + lb.new_h])


def _hybrid(cfg, monkeypatch, wire_px_normal, wire_px_inverted):
    """HybridSegmenter with YOLO and WireNet replaced: the first WireNet pass is the photo as taken, a second
    one (if any) is the inverted photo."""
    from app.pipeline import wirenet as W
    seg_cfg = cfg.segmentation.model_copy(update={"dual_polarity": True, "dual_polarity_max_mean_l": 40.0,
                                                  "dual_polarity_ratio": 1.2})

    class Yolo:
        error, names, path = None, {}, Path("y.pt")
        cfg = seg_cfg

        def load(self):
            return True

        def segment(self, x, lb):
            return []

    h = W.HybridSegmenter(Yolo(), Path("w.pt"), instances="skeleton", tta="flip")
    h._net = object()
    monkeypatch.setattr(W.HybridSegmenter, "load", lambda self: True)

    calls = []

    def fake_infer(self, x):
        calls.append(1)
        n = wire_px_normal if len(calls) == 1 else wire_px_inverted   # first pass as photographed, then inverted
        p = np.zeros((W.N_CLASSES, 64, 64), np.float32)
        p[0] = 1
        flat = p.reshape(W.N_CLASSES, -1)
        flat[0, :n], flat[TEL, :n] = 0, 1
        return p, {"wire_pixels": n, "entropy": 0.1, "disagreement": 0.0}
    monkeypatch.setattr(W.HybridSegmenter, "_infer", fake_infer)
    monkeypatch.setattr(W, "skeleton_instances", lambda *a, **k: [])
    monkeypatch.setattr(W, "class_masks_to_original", lambda cls, lb: {TEL: cls == TEL})
    return h


@pytest.mark.parametrize("dark,normal,inverted,expected", [
    (True, 100, 400, "inverted"),     # night: the inverted pass finds clearly more wire
    (True, 100, 110, "normal"),       # dark day photo: not enough more (needs 1.2x)
    (False, 100, 400, None),          # bright photo: never checked, no extra cost
])
def test_dual_polarity_selection(cfg, monkeypatch, dark, normal, inverted, expected):
    from app.pipeline.preprocess import letterbox_geometry
    h = _hybrid(cfg, monkeypatch, normal, inverted)
    lb = letterbox_geometry(64, 64, 64)
    x = np.full((64, 64, 3), 0.05 if dark else 0.8, np.float32)
    h.segment(x, lb)
    assert h.last_polarity == expected
    assert h.last_uncertainty["wire_pixels"] == (inverted if expected == "inverted" else normal)


def test_shipped_config_enables_dual_polarity(cfg):
    s = cfg.segmentation
    assert s.dual_polarity and s.dual_polarity_max_mean_l == 40.0 and s.dual_polarity_ratio == 1.2


def test_field_page_and_assets_are_the_v215_screener(client):
    # v2.15 (supersedes v2.14): dark landing identity with the gold accent, no server-status label, no sample button,
    # centred upload card with inspection name / city / barangay, plain-language results with a three-level risk
    # summary, photo limitations, next steps, and technical diagnostics collapsed.
    page = client.get("/workspace")
    assert page.status_code == 200
    html = page.text
    assert 'class="fs-root"' in html and 'data-theme="dark"' in html and "glass" not in html
    assert "family=Geist" in html and 'id="working"' in html and "sk-scan" in html
    for gone in ("mode-pill", "Model ready", "Load the sample", 'name="pole_code"', "One photo of the pole",
                 "A field verdict in seconds", "New inspection</p>"):
        assert gone not in html, gone
    for field in ('name="inspection_name"', 'name="city"', 'name="barangay"', 'id="locate"', 'id="clear-file"'):
        assert field in html, field
    js = client.get("/assets/field.js").text + client.get("/assets/findings.js").text  # v2.19: shared wording
    # S6 (register D-24): the v2.18 result page is restored (photo and wire table side by side, four views, overlay
    # opacity, vegetation, layers, zoom with reset, filterable table, measurement details, specific limitations and
    # next steps, staff-only technical drawer) with the S4/S5 corrections (no severity without a validated reference)
    for needle in ('data-view="photo"', 'data-view="spans"', 'data-view="mask"', 'data-view="overlay"', 'id="opacity"',
                   'data-layer="vegetation"', 'id="layers-btn"', 'data-zoom="fit"', 'data-zoom="reset"', 'id="zoom-pct"',
                   'id="span-detail"', 'class="fx-spans"', 'data-filter="all"', "Measurement details", "Photo limitations",
                   "What to do next", "Why this result", "What the analysis found", 'wf-notice variant="full"',
                   "inspection_name", "Do not touch", "Technical diagnostics", "Not applicable", "Colour shows wire type, not severity"):
        assert needle in js, needle
    # the technical drawer is rendered only for signed-in staff (the public record carries no internals, v2.20)
    assert '${r.public ? "" : `<section class="fx-drawer" id="diag">' in js
    for gone in ("Model ready", "Inspection quality", "#sample", "HIGH RISK FOUND", "Overlapping wires can prevent reliable separation"):
        assert gone not in client.get("/assets/field.js").text.split("// --------------------------------------------------------------"
                                                                     "---- result view")[-1], gone
    css = client.get("/assets/field.css").text
    assert "fx-shimmer" in css and ".fx-summary" in css and ".fx-spans" in css and ".fx-drawer" in css and "backdrop-filter" not in css
    assert "gradient(" not in css.replace("linear-gradient(90deg, transparent", "")
