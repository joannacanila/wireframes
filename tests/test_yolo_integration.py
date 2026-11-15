"""YOLO11-seg plumbing with a randomly initialised 4-class network (no download, no training).

Verifies the wiring of Algorithm 3, not detection quality: the letterboxed float tensor is
passed straight to the predictor (no second resize), per-class NMS runs, and 640 x 640
masks are mapped back to the original image. Also checks the guards that reject
detect-only checkpoints (the problem described in Chapter IV) and foreign class sets.
"""

from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("ultralytics")

from ultralytics.nn.tasks import DetectionModel, SegmentationModel  # noqa: E402

from app.core.config import CLASSES  # noqa: E402
from app.pipeline.preprocess import preprocess  # noqa: E402
from app.pipeline.runner import run_analysis  # noqa: E402
from app.pipeline.segment import SegmenterUnavailable, YoloSegmenter  # noqa: E402


def _ckpt(tmp: Path, net, task: str, names=None) -> Path:
    if names is not None:
        net.names = names
    p = tmp / f"{task}-{len(net.names)}.pt"
    torch.save({"model": net.half(), "train_args": {"task": task}}, p)
    return p


@pytest.fixture(scope="module")
def seg_ckpt(tmp_path_factory):
    torch.manual_seed(0)
    net = SegmentationModel("yolo11n-seg.yaml", nc=4, verbose=False)
    return _ckpt(tmp_path_factory.mktemp("w"), net, "segment", dict(enumerate(CLASSES)))


def test_masks_are_mapped_to_original_resolution(cfg, scene, seg_ckpt):
    small = scene.rgb[::2, ::2].copy()  # 800 x 500, non-square: exercises the letterbox inverse
    pre = preprocess(small, cfg.preprocessing, cfg.enhancement, cfg.segmentation.imgsz)  # as the runner does
    probe = YoloSegmenter(cfg.segmentation.model_copy(update={"weights": str(seg_ckpt), "conf_min": 0.0,
                                                               "device": "cpu"}), Path("."))
    assert probe.load(), probe.error
    dets = probe.segment(pre.x_rgb, pre.letterbox)
    assert dets, "random network produced no candidates at conf 0"
    assert all(d.mask.shape == small.shape[:2] and d.mask.dtype == bool for d in dets)
    assert all(d.wire_class in CLASSES for d in dets)
    no_fallback = cfg.model_copy(update={"enhancement": cfg.enhancement.model_copy(update={"fallback": False})})
    out = run_analysis(small, no_fallback, probe)  # the fallback's merge would de-duplicate random masks
    assert out.segmentation_source == "model"
    n_veg = sum(d.wire_class == "Vegetation" for d in dets)  # routed out of sag scoring, not lost
    assert len(out.vegetation) == n_veg and len(out.wires) == len(dets) - n_veg
    assert out.status in {"complete", "fit_failed"} if out.wires else out.status == "no_wires_detected"


def test_rejects_detect_only_and_foreign_class_models(cfg, tmp_path):
    det = _ckpt(tmp_path, DetectionModel("yolo11n.yaml", nc=4, verbose=False), "detect", dict(enumerate(CLASSES)))
    s = YoloSegmenter(cfg.segmentation.model_copy(update={"weights": str(det)}), Path("."))
    assert not s.load() and "segmentation model is required" in s.error
    coco = _ckpt(tmp_path, SegmentationModel("yolo11n-seg.yaml", nc=3, verbose=False), "segment",
                 {0: "person", 1: "car", 2: "wire"})
    s = YoloSegmenter(cfg.segmentation.model_copy(update={"weights": str(coco)}), Path("."))
    assert not s.load() and "are not WIREFRAME classes" in s.error
    old = _ckpt(tmp_path, SegmentationModel("yolo11n-seg.yaml", nc=4, verbose=False), "segment",
                {0: "power_distribution_line", 1: "telecom_cable", 2: "fiber_optic_drop", 3: "dead_wire"})
    assert not YoloSegmenter(cfg.segmentation.model_copy(update={"weights": str(old)}), Path(".")).load()  # old taxonomy
    with pytest.raises(SegmenterUnavailable):
        s.segment(np.zeros((640, 640, 3), np.float32), None)
