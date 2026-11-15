"""Score span-extraction variants on cached predictions (ml.experiments.cache)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

from app.core.config import CLASSES, VEGETATION
from app.pipeline import evaluation as ev

WIRES = [c for c in CLASSES if c != VEGETATION]


def load(split_dir: Path):
    for f in sorted(split_dir.glob("*.npz")):
        z = np.load(f, allow_pickle=False)
        yield f.stem, z


def score(split_dir: Path, extractor: Callable, conf: float = 0.2, tol_px: int = 2) -> dict:
    """extractor(probs (C,H,W float32), class_map (H,W)) -> [(class, mask, score)] for wire classes."""
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * tol_px + 1, 2 * tol_px + 1))
    widen = lambda m: cv2.dilate(m.astype(np.uint8), k).astype(bool)  # noqa: E731
    strict, tolerant, n_spans = [], [], {c: 0 for c in WIRES}
    for _, z in load(split_dir):
        probs = z["probs"].astype(np.float32)
        spans = extractor(probs, probs.argmax(0))
        preds = [(VEGETATION, m, float(s)) for m, s in zip(z["veg_masks"], z["veg_scores"], strict=True)] + spans
        for c, _, _ in spans:
            n_spans[c] += 1
        gts = list(zip(z["gt_classes"].tolist(), z["gt_masks"], strict=True))
        strict.append(([ev.Instance(c, m, s) for c, m, s in preds], [ev.Instance(c, m) for c, m in gts]))
        tolerant.append(([ev.Instance(c, widen(m), s) for c, m, s in preds], [ev.Instance(c, widen(m)) for c, m in gts]))
    rs, rt = ev.evaluate_segmentation(strict, conf), ev.evaluate_segmentation(tolerant, conf)

    def pack(r):
        return {"map50": r.map50, "per_class": {c: {"ap50": v.ap50, "f1": v.f1, "precision": v.precision, "recall": v.recall,
                                                    "tp": v.tp, "n_pred": v.n_pred, "n_gt": v.n_gt}
                                                for c, v in r.per_class.items()}}
    return {"strict": pack(rs), "tolerant": pack(rt), "spans_all_scores": n_spans}


def summary(r: dict) -> str:
    s, t = r["strict"], r["tolerant"]
    f = lambda x: "  -  " if x is None else f"{x:.3f}"  # noqa: E731
    p, tc = t["per_class"]["power-distribution-lines"], t["per_class"]["telecom-cable"]
    return (f"strict mAP50 {s['map50']:.4f} | tol mAP50 {t['map50']:.4f} | power tol AP50 {f(p['ap50'])} F1 {f(p['f1'])} "
            f"R {f(p['recall'])} (pred {p['n_pred']}/gt {p['n_gt']}) | telecom tol AP50 {f(tc['ap50'])} F1 {f(tc['f1'])} "
            f"R {f(tc['recall'])} (pred {tc['n_pred']}/gt {tc['n_gt']})")
