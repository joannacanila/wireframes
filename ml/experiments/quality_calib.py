"""Per-image reliability vs measured accuracy, for calibrating the quality grade (Feature B).

    python -m ml.experiments.quality_calib --split val --out docs/thesis/results/v28_quality_val.jsonl

For each image x condition: v2.8 preprocessing, WireNet plain + mirrored passes, the fused class
map, and per-image tolerant IoU of power and telecom (mean over the classes present in the
labels), next to the uncertainty (entropy U, flip disagreement D) and image-quality measures.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from app.core.config import ROOT_DIR, load_analysis_config
from ml.experiments.stress_bench import WIRES, load_split
from ml.stress import CONDITIONS, apply_condition


def main() -> None:
    import torch

    from app.pipeline.preprocess import preprocess
    from app.pipeline.wirenet import load_wirenet, predict_probs, uncertainty
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--enh", default='{"edge_denoise": true, "dark_l_max": 0.0}')
    a = ap.parse_args()
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    enh = cfg.enhancement.model_copy(update=json.loads(a.enh))
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    net = load_wirenet(ROOT_DIR / "models" / "wirenet-r34.pt", dev)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    items = list(load_split(a.split))
    with open(a.out, "w") as fh:
        for cond in CONDITIONS:
            for name, rgb0, tgt in items:
                rgb = apply_condition(rgb0, cond, name)
                pre = preprocess(rgb, cfg.preprocessing, enh, cfg.segmentation.imgsz)
                x = np.clip(pre.x_rgb * 255 + 0.5, 0, 255).astype(np.uint8)
                p1 = predict_probs(net, x, dev)
                p2 = predict_probs(net, np.ascontiguousarray(x[:, ::-1]), dev)[:, :, ::-1]
                pred = ((p1 + p2) / 2).argmax(0)
                ious = []
                for idx in WIRES.values():
                    t = tgt == idx
                    if not t.any():
                        continue
                    pd = cv2.dilate((pred == idx).astype(np.uint8), k).astype(bool)
                    td = cv2.dilate(t.astype(np.uint8), k).astype(bool)
                    ious.append((pd & td).sum() / max((pd | td).sum(), 1))
                u = uncertainty(p1, p2)
                fh.write(json.dumps({"image": name, "condition": cond, "tolerant_iou": float(np.mean(ious)) if ious else None,
                                     **u, "quality": pre.quality}, default=float) + "\n")
            print(cond, "done", flush=True)
            if dev == "mps":
                torch.mps.empty_cache()


if __name__ == "__main__":
    main()
