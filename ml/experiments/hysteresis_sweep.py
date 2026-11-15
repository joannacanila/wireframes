"""Hysteresis thresholding of WireNet wire probability before span assembly (v2.11, validation).

    python -m ml.experiments.hysteresis_sweep --cache runs/cache/val-v28 --out docs/thesis/results/v211_hysteresis_val.jsonl

v2.10 labels a pixel wire when a wire class wins the argmax. Dense, faint or thin conductors often sit
just below that, so recall may rise if pixels with total wire probability >= low are kept when they are
8-connected to a pixel >= high (Canny-style hysteresis). Each kept pixel takes its most probable wire
class. Scored with the v2.8 span constants on the cached validation probabilities: span mAP50 (strict
and 2 px tolerant), wire-pixel recall (2 px tolerant, power + telecom) and the same on the 6 densest
validation photos (most labelled spans). Adopted only if tolerant mAP50 stays >= 0.0397.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from scipy import ndimage as ndi

from app.core.config import CLASSES, VEGETATION
from ml.experiments.ablation_final import FINAL
from ml.experiments.harness import score
from ml.experiments.sweep_spans import make

WIRE_IDX = [CLASSES.index(c) + 1 for c in CLASSES if c != VEGETATION]
K = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))


def hysteresis_classes(probs: np.ndarray, low: float, high: float) -> np.ndarray:
    cls = probs.argmax(0)
    pw = probs[WIRE_IDX].sum(0)
    strong, weak = pw >= high, pw >= low
    lab, n = ndi.label(weak, structure=np.ones((3, 3)))
    keep = np.isin(lab, np.unique(lab[strong & (lab > 0)]))
    best = np.asarray(WIRE_IDX)[probs[WIRE_IDX].argmax(0)]
    out = cls.copy()
    out[keep & ~np.isin(cls, WIRE_IDX)] = best[keep & ~np.isin(cls, WIRE_IDX)]
    return out


def pixel_recall(cache: Path, low, high, stems=None):
    tp = gt = pred = 0
    for f in sorted(cache.glob("*.npz")):
        if stems is not None and f.stem not in stems:
            continue
        z = np.load(f)
        probs = z["probs"].astype(np.float32)
        cls = probs.argmax(0) if low is None else hysteresis_classes(probs, low, high)
        p = np.isin(cls, WIRE_IDX[1:])                         # power + telecom
        t = np.zeros(p.shape, bool)
        for c, m in zip(z["gt_classes"].tolist(), z["gt_masks"], strict=True):
            if c in ("power-distribution-lines", "telecom-cable"):
                t |= m
        pd, td = cv2.dilate(p.astype(np.uint8), K).astype(bool), cv2.dilate(t.astype(np.uint8), K).astype(bool)
        tp += int((t & pd).sum())
        gt += int(t.sum())
        pred += int(p.sum())
        _ = td
    return tp / max(gt, 1), pred


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path("runs/cache/val-v28"))
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    counts = {f.stem: sum(c in ("power-distribution-lines", "telecom-cable") for c in np.load(f)["gt_classes"].tolist())
              for f in a.cache.glob("*.npz")}
    dense = set(sorted(counts, key=counts.get)[-6:])
    for low, high in [(None, None), (0.4, 0.5), (0.3, 0.5), (0.25, 0.5), (0.2, 0.5), (0.3, 0.6), (0.2, 0.4), (0.15, 0.4)]:
        base = make(FINAL)
        ex = base if low is None else (lambda probs, cls, lo=low, hi=high, b=base: b(probs, hysteresis_classes(probs, lo, hi)))
        r = score(a.cache, ex)
        rec, npred = pixel_recall(a.cache, low, high)
        rec_d, _ = pixel_recall(a.cache, low, high, dense)
        row = {"low": low, "high": high, "strict_map50": r["strict"]["map50"], "tolerant_map50": r["tolerant"]["map50"],
               "power": r["tolerant"]["per_class"]["power-distribution-lines"],
               "telecom": r["tolerant"]["per_class"]["telecom-cable"], "pixel_recall_tol": rec,
               "pixel_recall_tol_dense6": rec_d, "pred_wire_px": npred, "spans": r["spans_all_scores"]}
        with open(a.out, "a") as fh:
            fh.write(json.dumps(row, default=float) + "\n")
        print(f"low {low} high {high}: tol mAP50 {row['tolerant_map50']:.4f} strict {row['strict_map50']:.4f} | "
              f"pixel recall {rec:.3f} dense {rec_d:.3f} | pred px {npred} | power R {row['power']['recall']:.3f} "
              f"telecom R {row['telecom']['recall']:.3f}", flush=True)


if __name__ == "__main__":
    main()
