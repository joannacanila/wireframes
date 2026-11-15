"""Cache WireNet probabilities and YOLO vegetation detections for fast post-processing experiments.

    python -m ml.experiments.cache --split val --out runs/cache

The curated images are 1024 x 1024 letterboxes, so model space == image space (identity
letterbox). Stored per image: WireNet class probabilities (float16, C x H x W), YOLO vegetation
masks with confidences, and the ground-truth instances (class, mask). Inference runs exactly as
the deployed HybridSegmenter does (same preprocessing and enhancement).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from app.core.config import ROOT_DIR, VEGETATION, load_analysis_config


def cache_split(split: str, out: Path, degrade_name: str | None = None, wirenet: str | None = None,
                tta: str | None = None) -> None:
    import cv2
    import yaml

    from app.pipeline.preprocess import preprocess
    from app.pipeline.segment import parse_yolo_seg_labels
    from app.pipeline.wirenet import make_segmenter, predict_probs_tta

    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    upd = {"conf_min": 0.01, **({"wirenet_weights": wirenet} if wirenet else {})}  # v2.12: candidate backbones
    seg = make_segmenter(cfg.segmentation.model_copy(update=upd), ROOT_DIR)
    assert seg.load(), seg.error
    data = Path("datasets/wireframe_seg/data.yaml")
    spec = yaml.safe_load(data.read_text())
    img_dir = data.parent / spec[split]
    out.mkdir(parents=True, exist_ok=True)
    for p in sorted(img_dir.glob("*.jpg")):
        rgb = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
        if degrade_name:
            from ml.stress import apply_condition
            rgb = apply_condition(rgb, degrade_name, p.name)
        lbl = Path(str(p).replace("/images/", "/labels/")).with_suffix(".txt")
        gts = parse_yolo_seg_labels(lbl.read_text() if lbl.exists() else "", rgb.shape[1], rgb.shape[0])
        pre = preprocess(rgb, cfg.preprocessing, cfg.enhancement, cfg.segmentation.imgsz)
        yolo = seg.yolo.segment(pre.x_rgb, pre.letterbox)
        veg = [d for d in yolo if d.wire_class == VEGETATION]
        probs = predict_probs_tta(seg._net, np.clip(pre.x_rgb * 255 + 0.5, 0, 255).astype(np.uint8), seg._device,
                                  tta or cfg.segmentation.wirenet_tta)
        np.savez_compressed(out / f"{p.stem}.npz", probs=probs.astype(np.float16),
                            veg_masks=np.array([d.mask for d in veg], bool).reshape(-1, *rgb.shape[:2]),
                            veg_scores=np.array([d.score for d in veg], np.float32),
                            gt_masks=np.array([g.mask for g in gts], bool).reshape(-1, *rgb.shape[:2]),
                            gt_classes=np.array([g.wire_class for g in gts]),
                            enhancement=np.array(pre.enhancement or []))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val")
    ap.add_argument("--out", type=Path, default=Path("runs/cache"))
    ap.add_argument("--condition", default=None)
    ap.add_argument("--tag", default="")
    ap.add_argument("--wirenet", default=None, help="v2.12: WireNet-style weights to cache instead of the configured ones")
    ap.add_argument("--tta", default=None, choices=["none", "flip", "flip_ms"], help="v2.12: override the configured TTA")
    a = ap.parse_args()
    cache_split(a.split, a.out / (a.split + (f"-{a.condition}" if a.condition else "") + a.tag), a.condition, a.wirenet,
                a.tta)


if __name__ == "__main__":
    main()
