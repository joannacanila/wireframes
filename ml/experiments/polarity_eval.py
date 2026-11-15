"""Dual-polarity WireNet inference (v2.11), validation only.

    python -m ml.experiments.polarity_eval --out docs/thesis/results/v211_polarity_val.jsonl

Daytime training photos show dark conductors against a bright sky. At night, street-lit conductors
are lighter than the black sky and WireNet misses most of them. Candidate fix: also run WireNet on the
photo with CIELAB L* inverted (chroma kept) and keep whichever polarity yields clearly more wire.

Per validation photo and condition (the 8 stress conditions plus the synthetic "night" control):
mean L*, wire pixels and tolerant pixel IoU (power / telecom, 2 px) of the as-uploaded pass and the
inverted pass, both with the shipped preprocessing and flip TTA. The selection rule is then
evaluated on these rows: how often it switches, and whether a switch helps or hurts accuracy.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import yaml

from app.core.config import CLASSES, ROOT_DIR, load_analysis_config
from ml.stress import CONDITIONS, apply_condition

WIRES = {"power-distribution-lines": CLASSES.index("power-distribution-lines") + 1,
         "telecom-cable": CLASSES.index("telecom-cable") + 1}


def invert_luminance(rgb: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    lab[..., 0] = 255 - lab[..., 0]
    return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)


def main() -> None:
    import torch

    from app.pipeline.enhance import measure
    from app.pipeline.preprocess import preprocess
    from app.pipeline.wirenet import load_wirenet, predict_probs_tta
    from ml.wirenet import semantic_target
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--conditions", nargs="*", default=list(CONDITIONS) + ["night"])
    a = ap.parse_args()
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    net = load_wirenet(ROOT_DIR / cfg.segmentation.wirenet_weights, dev)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    spec = yaml.safe_load(open("datasets/wireframe_seg/data.yaml"))
    for p in sorted((Path("datasets/wireframe_seg") / spec["val"]).glob("*.jpg")):
        rgb0 = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
        lbl = Path(str(p).replace("/images/", "/labels/")).with_suffix(".txt")
        tgt = semantic_target(lbl.read_text() if lbl.exists() else "", *rgb0.shape[:2])
        for cond in a.conditions:
            rgb = apply_condition(rgb0, cond, p.name)
            row = {"image": p.stem, "condition": cond, "mean_l": measure(rgb).mean_l}
            for name, img in (("orig", rgb), ("inv", invert_luminance(rgb))):
                pre = preprocess(img, cfg.preprocessing, cfg.enhancement, cfg.segmentation.imgsz)
                x = np.clip(pre.x_rgb * 255 + 0.5, 0, 255).astype(np.uint8)
                pred = predict_probs_tta(net, x, dev, cfg.segmentation.wirenet_tta).argmax(0)
                row[f"{name}_wire_px"] = int(np.isin(pred, list(WIRES.values())).sum())
                for c, idx in WIRES.items():
                    pm, tm = pred == idx, tgt == idx
                    pd = cv2.dilate(pm.astype(np.uint8), k).astype(bool)
                    td = cv2.dilate(tm.astype(np.uint8), k).astype(bool)
                    row[f"{name}_{c[:5]}_inter"] = int((pd & td).sum())
                    row[f"{name}_{c[:5]}_union"] = int((pd | td).sum())
            with open(a.out, "a") as fh:
                fh.write(json.dumps(row) + "\n")
            print(p.stem[-8:], cond, round(row["mean_l"], 1), row["orig_wire_px"], row["inv_wire_px"], flush=True)
            if dev == "mps":
                torch.mps.empty_cache()


if __name__ == "__main__":
    main()
