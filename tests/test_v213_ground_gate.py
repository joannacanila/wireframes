"""v2.13 ground gate (app/pipeline/scene.py, HybridSegmenter._ground_gate): spans lying mostly on ADE20K ground
classes are suppressed and reported, never scored. The pretrained scene parser is replaced by a fake so these
tests run offline in milliseconds (no UperNet download)."""

from __future__ import annotations

import builtins
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.pipeline import scene as S
from app.pipeline import wirenet as W
from app.pipeline.preprocess import letterbox_geometry
from app.pipeline.segment import WireDetection, _bbox

ROAD = 6  # any id; the fake parser declares it as its only ground class


class FakeScene:
    """Labels every pixel at or below `ground_row` (photo coordinates) as road, everything above as sky."""

    def __init__(self, ground_row: int | None, available: bool = True):
        self.ground_row, self.available, self.calls = ground_row, available, 0
        self.ground_ids = [ROAD]

    def labels(self, rgb):
        self.calls += 1
        if not self.available:
            return None
        lab = np.full(rgb.shape[:2], 2, np.uint8)  # 2 = "sky"
        if self.ground_row is not None:
            lab[self.ground_row:] = ROAD
        return lab

    def ground_mask(self, labels):
        return np.isin(labels, self.ground_ids)


def _hybrid(cfg, gate=True, share=0.5):
    seg_cfg = cfg.segmentation.model_copy(update={"ground_gate": gate, "ground_gate_share": share})

    class Yolo:
        error, names, path = None, {}, Path("y.pt")
        cfg = seg_cfg

    return W.HybridSegmenter(Yolo(), Path("w.pt"), instances="skeleton")


def _span(h, w, p0, p1, thick=3, cls="telecom-cable"):
    m = np.zeros((h, w), np.uint8)
    cv2.line(m, p0, p1, 1, thick)
    m = m.astype(bool)
    return WireDetection(cls, 0.9, m, _bbox(m))


# photo 200 x 100 letterboxed into a 200 px square: scale 1, 50 px padding top and bottom
W_, H_ = 200, 100
LB = letterbox_geometry(W_, H_, 200)
X = np.zeros((200, 200, 3), np.float32)


def test_ground_share():
    span = np.zeros((10, 10), bool)
    span[5, :] = True
    ground = np.zeros((10, 10), bool)
    ground[5, :4] = True
    assert S.ground_share(span, ground) == pytest.approx(0.4)
    assert S.ground_share(np.zeros((10, 10), bool), ground) == 0.0  # empty span: nothing on the ground


def test_ground_mask_uses_declared_ground_ids():
    p = S.SceneParser()
    p.ground_ids = [3, 6]
    lab = np.array([[1, 3], [6, 2]], np.uint8)
    assert p.ground_mask(lab).tolist() == [[False, True], [True, False]]


def test_ground_classes_cover_street_surfaces():
    for name in ("road", "sidewalk", "floor", "earth", "path", "grass"):
        assert name in S.GROUND
    assert "fence" not in S.GROUND and "railing" not in S.GROUND  # documented limitation: no safe cue
    assert S.INPUT % 32 == 0 and (S.INPUT // 32) % 6 == 0       # UperNet PPM (1, 2, 3, 6) on MPS


def test_span_on_road_is_suppressed_and_recorded(cfg):
    h = _hybrid(cfg)
    h._scene = FakeScene(ground_row=60)
    sky = _span(H_, W_, (10, 20), (190, 25))
    kerb = _span(H_, W_, (10, 80), (190, 85))
    kept = h._ground_gate([sky, kerb], X, LB)
    assert kept == [sky]
    assert len(h.last_suppressed) == 1
    s = h.last_suppressed[0]
    assert s["reason_code"] == "ground_structure" and s["ground_share"] == 1.0
    assert s["wire_class"] == "telecom-cable" and s["confidence"] == 0.9
    assert s["bbox"][1] >= 60 and s["polygons"]
    assert "road or pavement" in s["reason"]


def test_share_threshold_is_inclusive(cfg):
    """A span with exactly half its pixels on the ground is suppressed at ground_gate_share = 0.5."""
    h = _hybrid(cfg, share=0.5)
    m = np.zeros((H_, W_), bool)
    m[58:62, 50:150] = True            # rows 58-59 sky, 60-61 road: share 0.5
    d = WireDetection("power-distribution-lines", 0.8, m, _bbox(m))
    h._scene = FakeScene(ground_row=60)
    assert h._ground_gate([d], X, LB) == []
    h = _hybrid(cfg, share=0.51)
    h._scene = FakeScene(ground_row=60)
    assert h._ground_gate([d], X, LB) == [d] and h.last_suppressed == []


def test_gate_off_keeps_everything_and_never_runs_the_parser(cfg):
    h = _hybrid(cfg, gate=False)
    fake = h._scene = FakeScene(ground_row=0)
    spans = [_span(H_, W_, (10, 80), (190, 85))]
    assert h._ground_gate(spans, X, LB) == spans
    assert fake.calls == 0 and h.last_suppressed == []


def test_parser_skipped_when_no_span_reaches_lower_half(cfg):
    h = _hybrid(cfg)
    fake = h._scene = FakeScene(ground_row=0)  # everything "road": would suppress if it ran
    spans = [_span(H_, W_, (10, 10), (190, 30))]
    assert h._ground_gate(spans, X, LB) == spans
    assert fake.calls == 0


def test_parser_unavailable_fails_open(cfg):
    h = _hybrid(cfg)
    fake = h._scene = FakeScene(ground_row=0, available=False)
    spans = [_span(H_, W_, (10, 80), (190, 85))]
    assert h._ground_gate(spans, X, LB) == spans
    assert fake.calls == 1 and h.last_suppressed == [{"unavailable": True}]


def test_no_spans_is_a_no_op(cfg):
    h = _hybrid(cfg)
    fake = h._scene = FakeScene(ground_row=0)
    assert h._ground_gate([], X, LB) == [] and fake.calls == 0


def test_scene_parser_load_failure_is_reported(monkeypatch):
    real = builtins.__import__

    def no_transformers(name, *a, **k):
        if name == "transformers":
            raise ImportError("No module named 'transformers'")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_transformers)
    p = S.SceneParser(device="cpu")
    assert p.load() is False and "scene parser unavailable" in p.error
    assert p.labels(np.zeros((8, 8, 3), np.uint8)) is None


def test_shipped_config_enables_the_gate(cfg):
    s = cfg.segmentation
    assert s.ground_gate is True and s.ground_gate_share == 0.5
