"""Stress benchmark of preprocessing / TTA variants: WireNet pixel IoU under 8 conditions.

    python -m ml.experiments.stress_bench --split val --out docs/thesis/results/v28_stress_pixel_val.jsonl

Pixel IoU (strict and 2 px tolerant, accumulated over the split) for power and telecom, the
quantities preprocessing and TTA act on; span-level metrics for the winner are computed
separately (ml.experiments.span_stress).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np
import yaml

from app.core.config import CLASSES, ROOT_DIR, load_analysis_config
from ml.stress import CONDITIONS, apply_condition

VARIANTS = {
    "enhancement_off": {"enh": {"mode": "off", "fallback": False}, "tta": "none"},
    "v2.7": {"enh": {}, "tta": "none"},
    "retinex": {"enh": {"retinex": True}, "tta": "none"},
    "edge_denoise": {"enh": {"edge_denoise": True}, "tta": "none"},
    "retinex+edge": {"enh": {"retinex": True, "edge_denoise": True}, "tta": "none"},
    "v2.7+flip": {"enh": {}, "tta": "flip"},
    "v2.7+flip_ms": {"enh": {}, "tta": "flip_ms"},
    "v2.8": {"enh": {"edge_denoise": True, "dark_l_max": 0.0}, "tta": "flip"},
    "v2.8_keepdark": {"enh": {"edge_denoise": True}, "tta": "flip"},
    # v2.9 routing candidates (on top of v2.8)
    "v2.8+motion": {"enh": {"edge_denoise": True, "dark_l_max": 0.0, "motion_route": True}, "tta": "flip"},
    "v2.8+deblock": {"enh": {"edge_denoise": True, "dark_l_max": 0.0, "deblock": True}, "tta": "flip"},
    "v2.8+deblock_only": {"enh": {"edge_denoise": True, "dark_l_max": 0.0, "deblock": True,
                                  "jpeg_blockiness_min": 99.0}, "tta": "flip"},
    "v2.8+motion_skip": {"enh": {"edge_denoise": True, "dark_l_max": 0.0, "motion_route": True,
                                 "motion_action": "skip"}, "tta": "flip"},
    "v2.8+motion+deblock": {"enh": {"edge_denoise": True, "dark_l_max": 0.0, "motion_route": True,
                                    "deblock": True}, "tta": "flip"},
}
WIRES = {"power-distribution-lines": CLASSES.index("power-distribution-lines") + 1,
         "telecom-cable": CLASSES.index("telecom-cable") + 1}


def load_split(split: str):
    from ml.wirenet import semantic_target
    data = Path("datasets/wireframe_seg/data.yaml")
    spec = yaml.safe_load(data.read_text())
    for p in sorted((data.parent / spec[split]).glob("*.jpg")):
        rgb = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
        lbl = Path(str(p).replace("/images/", "/labels/")).with_suffix(".txt")
        yield p.name, rgb, semantic_target(lbl.read_text() if lbl.exists() else "", *rgb.shape[:2])


def run(split: str, variants: list[str], conditions: list[str], out: Path) -> None:
    import torch

    from app.pipeline.preprocess import preprocess
    from app.pipeline.wirenet import load_wirenet, predict_probs_tta
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    net = load_wirenet(ROOT_DIR / "models" / "wirenet-r34.pt", dev)
    items = list(load_split(split))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    for cond in conditions:
        imgs = [(n, apply_condition(rgb, cond, n), t) for n, rgb, t in items]
        for v in variants:
            spec = VARIANTS[v]
            enh = cfg.enhancement.model_copy(update=spec["enh"])
            acc = {c: np.zeros(4) for c in WIRES}
            t0, applied = time.time(), {}
            for _, rgb, tgt in imgs:
                pre = preprocess(rgb, cfg.preprocessing, enh, cfg.segmentation.imgsz)
                for s in pre.enhancement or ["none"]:
                    applied[s] = applied.get(s, 0) + 1
                x = np.clip(pre.x_rgb * 255 + 0.5, 0, 255).astype(np.uint8)
                pred = predict_probs_tta(net, x, dev, spec["tta"]).argmax(0)
                for c, idx in WIRES.items():
                    p, t = pred == idx, tgt == idx
                    pd = cv2.dilate(p.astype(np.uint8), k).astype(bool)
                    td = cv2.dilate(t.astype(np.uint8), k).astype(bool)
                    acc[c] += [(p & t).sum(), (p | t).sum(), (pd & td).sum(), (pd | td).sum()]
            res = {c: {"iou": a[0] / max(a[1], 1), "tolerant_iou": a[2] / max(a[3], 1)} for c, a in acc.items()}
            sec = (time.time() - t0) / len(imgs)
            row = {"split": split, "condition": cond, "variant": v, "per_class": res,
                   "seconds_per_image": round(sec, 2), "steps_applied": applied}
            with open(out, "a") as fh:
                fh.write(json.dumps(row, default=float) + "\n")
            print(f"{split} {cond:9s} {v:15s} power tol {res['power-distribution-lines']['tolerant_iou']:.3f} "
                  f"telecom tol {res['telecom-cable']['tolerant_iou']:.3f} | strict {res['power-distribution-lines']['iou']:.3f}/"
                  f"{res['telecom-cable']['iou']:.3f} | {sec:.2f}s/img | {applied}", flush=True)
            if dev == "mps":
                torch.mps.empty_cache()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--variants", nargs="*", default=list(VARIANTS))
    ap.add_argument("--conditions", nargs="*", default=list(CONDITIONS))
    a = ap.parse_args()
    run(a.split, a.variants, a.conditions, a.out)


if __name__ == "__main__":
    main()
