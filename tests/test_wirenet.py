"""Hybrid YOLO + WireNet segmentation (v2.7)."""

import numpy as np
import pytest

from app.core.config import CLASSES
from app.pipeline.preprocess import letterbox_geometry
from app.pipeline.segment import WireDetection, _bbox
from app.pipeline.wirenet import N_CLASSES, assemble_instances, class_masks_to_original, make_segmenter

POWER = CLASSES.index("power-distribution-lines") + 1
TELECOM = CLASSES.index("telecom-cable") + 1


def test_thin_wire_survives_mapping_to_a_smaller_original():
    lb = letterbox_geometry(700, 350, 1024)          # original smaller than the model input
    cls = np.zeros((1024, 1024), np.int64)
    y = lb.pad_y + lb.new_h // 2
    cls[y, lb.pad_x + 10: lb.pad_x + lb.new_w - 10] = POWER   # a 1 px wire in model space
    m = class_masks_to_original(cls, lb)[POWER]
    assert m.shape == (350, 700)
    cols = m.any(axis=0)
    assert cols[20:680].mean() > 0.95                 # continuous, not dropped by downsampling


def test_upscaled_original_keeps_the_wire_continuous():
    lb = letterbox_geometry(3000, 1500, 1024)
    cls = np.zeros((1024, 1024), np.int64)
    cls[lb.pad_y + 200, lb.pad_x: lb.pad_x + lb.new_w] = TELECOM
    m = class_masks_to_original(cls, lb)[TELECOM]
    assert m.shape == (1500, 3000) and m.any(axis=0)[50:2950].mean() > 0.95


def _det(cls, mask, score=0.5):
    return WireDetection(cls, score, mask, _bbox(mask))


def test_each_detection_takes_only_its_own_class_pixels_near_its_coarse_mask():
    h, w = 200, 400
    power = np.zeros((h, w), bool); power[50:52, 10:390] = True     # thin, full resolution
    telecom = np.zeros((h, w), bool); telecom[120:122, 10:390] = True
    masks = {POWER: power, TELECOM: telecom}
    coarse = np.zeros((h, w), bool); coarse[46:58:4, 12:388] = True   # blocky, slightly off
    far = np.zeros((h, w), bool); far[170:180, 10:390] = True          # a detection on nothing
    veg = np.zeros((h, w), bool); veg[0:30, 0:60] = True
    out = assemble_instances(masks, [_det("power-distribution-lines", coarse), _det("telecom-cable", coarse),
                                     _det("power-distribution-lines", far), _det("Vegetation", veg)], dilate_px=6)
    assert [d.wire_class for d in out] == ["power-distribution-lines", "Vegetation"]
    np.testing.assert_array_equal(out[0].mask[50:52, 12:388], True)  # the crisp wire, not the blocky mask
    assert out[0].mask.sum() <= power.sum() and out[1].mask is veg


def test_hybrid_is_opt_in(cfg, tmp_path):
    from app.pipeline.segment import YoloSegmenter
    plain = cfg.segmentation.model_copy(update={"wirenet_weights": None})
    assert isinstance(make_segmenter(plain, tmp_path), YoloSegmenter)
    on = cfg.segmentation.model_copy(update={"wirenet_weights": "models/missing-wirenet.pt",
                                             "weights": str(tmp_path / "no.pt")})
    seg = make_segmenter(on, tmp_path)
    assert type(seg).__name__ == "HybridSegmenter" and not seg.load() and seg.error


def test_wirenet_forward_shape():
    pytest.importorskip("segmentation_models_pytorch")
    from app.pipeline.wirenet import build_model, predict_probs
    p = predict_probs(build_model().eval(), np.zeros((300, 500, 3), np.uint8), "cpu")
    assert p.shape == (N_CLASSES, 300, 500) and np.allclose(p.sum(0), 1, atol=1e-4)
