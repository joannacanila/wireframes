"""Visual audit of wires classed as service drops (chord > 35 deg), validation (v2.9).

    python -m ml.experiments.drop_audit --n 24 --out docs/thesis/figures/v29_drop_audit_val.jpg

Random sample (seed 0) of drops from the shipped pipeline; each tile shows the photo region
around the wire with the wire outlined, for manual labelling as a true service drop / guy wire
or a pole-to-pole span seen steeply in perspective.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
import yaml

from app.core.config import ROOT_DIR, load_analysis_config


def main() -> None:
    from app.pipeline.runner import run_analysis
    from app.pipeline.wirenet import make_segmenter
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    seg = make_segmenter(cfg.segmentation, ROOT_DIR)
    assert seg.load(), seg.error
    spec = yaml.safe_load(open("datasets/wireframe_seg/data.yaml"))
    items = []
    for p in sorted((Path("datasets/wireframe_seg") / spec["val"]).glob("*.jpg")):
        rgb = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
        for w in run_analysis(rgb, cfg, seg).wires:
            if w.status == "service_drop":
                items.append((p, w))
    rng = np.random.default_rng(0)
    pick = [items[i] for i in rng.choice(len(items), min(a.n, len(items)), replace=False)]
    tiles = []
    for k, (p, w) in enumerate(pick):
        img = cv2.imread(str(p))
        ov = img.copy()
        for poly in w.polygons:
            cv2.polylines(ov, [np.asarray(poly, np.int32)], True, (0, 255, 255), 2)
        pts = np.concatenate([np.asarray(q).reshape(-1, 2) for q in w.polygons])
        x0, y0 = np.maximum(pts.min(0) - 60, 0)
        x1, y1 = np.minimum(pts.max(0) + 60, [img.shape[1], img.shape[0]])
        t = cv2.resize(ov[y0:y1, x0:x1], (300, 300))
        cv2.putText(t, f"{k + 1}", (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
        tiles.append(t)
    while len(tiles) % 6:
        tiles.append(np.zeros((300, 300, 3), np.uint8))
    rows = [np.hstack(tiles[i:i + 6]) for i in range(0, len(tiles), 6)]
    cv2.imwrite(str(a.out), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 85])
    print(len(items), "drops on validation;", len(pick), "sampled")


if __name__ == "__main__":
    main()
