"""Failure-mode audit (v2.8): which images and wires fail, and with what are failures associated.

    python -m ml.experiments.failure_audit --split val --cache runs/cache/val-v28

Per image: tolerant pixel IoU of power + telecom (2 px), labelled span count (density), mean
labelled wire width, contrast / sharpness / exposure of the photo, and the share of labelled
wire pixels that WireNet missed or hallucinated. Spearman correlations between each variable and
accuracy are reported; the 3 hardest and 2 easiest images are rendered (green = predicted and
labelled, red = predicted only, blue = labelled only, within the 2 px tolerance) into
docs/thesis/figures/failure_*.jpg.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.stats import spearmanr

from app.core.config import CLASSES
from app.pipeline.enhance import measure

WIRE_IDS = [CLASSES.index(c) + 1 for c in ("power-distribution-lines", "telecom-cable")]
K = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))


def main() -> None:
    from ml.wirenet import semantic_target
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val")
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument("--figures", type=Path, default=Path("docs/thesis/figures"))
    ap.add_argument("--out", type=Path, default=Path("docs/thesis/results/v28_failure_audit_val.json"))
    a = ap.parse_args()
    a.figures.mkdir(parents=True, exist_ok=True)
    rows = []
    for f in sorted(a.cache.glob("*.npz")):
        z = np.load(f)
        cls = z["probs"].astype(np.float32).argmax(0)
        img = cv2.imread(f"datasets/wireframe_seg/images/{a.split}/{f.stem}.jpg")
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        lbl = Path(f"datasets/wireframe_seg/labels/{a.split}/{f.stem}.txt")
        tgt = semantic_target(lbl.read_text() if lbl.exists() else "", *rgb.shape[:2])
        p = np.isin(cls, WIRE_IDS)
        t = np.isin(tgt, WIRE_IDS)
        if not t.any():
            continue
        pd, td = cv2.dilate(p.astype(np.uint8), K).astype(bool), cv2.dilate(t.astype(np.uint8), K).astype(bool)
        iou = float((pd & td).sum() / max((pd | td).sum(), 1))
        gt_cls = z["gt_classes"].tolist()
        spans = sum(c in ("power-distribution-lines", "telecom-cable") for c in gt_cls)
        widths = []
        for c, m in zip(gt_cls, z["gt_masks"], strict=True):
            if c in ("power-distribution-lines", "telecom-cable") and m.sum() > 30:
                from app.pipeline.hazard import mean_width_px
                widths.append(mean_width_px(m))
        q = measure(rgb)
        content = ~((np.abs(rgb.astype(np.int16) - 114) <= 6).all(axis=2))
        rows.append({"image": f.stem, "tolerant_iou": iou, "labelled_spans": spans,
                     "spans_per_mp_content": spans / max(content.sum() / 1e6, 1e-6),
                     "mean_wire_width_px": float(np.median(widths)) if widths else None,
                     "missed_share": float((td & ~pd).sum() / max(td.sum(), 1)),
                     "false_share": float((pd & ~td).sum() / max(pd.sum(), 1)),
                     "contrast": q.contrast, "sharpness": q.sharpness, "mean_l": q.mean_l,
                     "content_share": float(content.mean())})
    keys = ["labelled_spans", "spans_per_mp_content", "mean_wire_width_px", "contrast", "sharpness", "mean_l", "content_share"]
    corr = {}
    for k in keys:
        x = [(r[k], r["tolerant_iou"]) for r in rows if r[k] is not None]
        rho, pv = spearmanr([v for v, _ in x], [i for _, i in x])
        corr[k] = {"spearman_rho": round(float(rho), 3), "p_value": round(float(pv), 4), "n": len(x)}
    rows.sort(key=lambda r: r["tolerant_iou"])
    picks = rows[:3] + rows[-2:]
    for r in picks:
        z = np.load(a.cache / f"{r['image']}.npz")
        cls = z["probs"].astype(np.float32).argmax(0)
        img = cv2.imread(f"datasets/wireframe_seg/images/{a.split}/{r['image']}.jpg")
        lbl = Path(f"datasets/wireframe_seg/labels/{a.split}/{r['image']}.txt")
        tgt = semantic_target(lbl.read_text() if lbl.exists() else "", *img.shape[:2])
        p, t = np.isin(cls, WIRE_IDS), np.isin(tgt, WIRE_IDS)
        pd, td = cv2.dilate(p.astype(np.uint8), K).astype(bool), cv2.dilate(t.astype(np.uint8), K).astype(bool)
        ov = (img * 0.45).astype(np.uint8)
        ov[p & td] = (60, 220, 60)
        ov[p & ~td] = (60, 60, 230)
        ov[t & ~pd] = (230, 140, 40)
        tag = "hard" if r in rows[:3] else "easy"
        both = np.hstack([img, np.full((img.shape[0], 8, 3), 255, np.uint8), ov])
        cv2.putText(both, f"{tag}: tolerant IoU {r['tolerant_iou']:.2f}, {r['labelled_spans']} labelled spans",
                    (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.imwrite(str(a.figures / f"failure_{tag}_{r['image'][-28:]}.jpg"), cv2.resize(both, None, fx=0.6, fy=0.6),
                    [cv2.IMWRITE_JPEG_QUALITY, 85])
    a.out.write_text(json.dumps({"correlations_with_tolerant_iou": corr, "images": rows}, indent=1, default=float))
    print(json.dumps(corr, indent=1))
    for r in rows[:3] + rows[-2:]:
        print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()})


if __name__ == "__main__":
    main()
