"""Paired bootstrap over validation photos for fusion candidates vs the single WireNet-R34 (v2.12).

    python -m ml.experiments.ensemble_bootstrap --caches r34=runs/cache/val-v28 mitb1=runs/cache/val-mitb1 \
        cnx=runs/cache/val-cnx --out docs/thesis/results/v212_ensemble_bootstrap_val.json

With 22 photos a span-mAP difference of a few thousandths can be noise. For each candidate the per-image,
per-class matching at IoU 0.50 (2 px tolerant masks, the v2.8 span extractor) is computed once; then the 22
photos are resampled with replacement B times, the SAME resample for every candidate (paired), and tolerant
mAP50 is recomputed from the pooled (score, true-positive) lists exactly as app.pipeline.evaluation does.
Reported: full-sample mAP50 (must equal ensemble_eval), the bootstrap 95% interval of the difference to R34,
and the share of resamples in which the candidate beats R34.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from app.core.config import CLASSES
from app.pipeline import evaluation as ev
from ml.experiments.ablation_final import FINAL
from ml.experiments.ensemble_eval import FusedCache
from ml.experiments.harness import load
from ml.experiments.sweep_spans import make

CANDIDATES = [
    {"r34": 1.0, "mitb1": 0.0, "cnx": 0.0},
    {"r34": 0.75, "mitb1": 0.25, "cnx": 0.0},
    {"r34": 0.25, "mitb1": 0.5, "cnx": 0.25},
    {"r34": 0.25, "mitb1": 0.25, "cnx": 0.5},
    {"r34": 0.0, "mitb1": 0.5, "cnx": 0.5},
]


def per_image(cache: Path, extractor) -> list[dict]:
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    widen = lambda m: cv2.dilate(m.astype(np.uint8), k).astype(bool)  # noqa: E731
    rows = []
    for _, z in load(cache):
        probs = z["probs"].astype(np.float32)
        spans = extractor(probs, probs.argmax(0))
        preds = [("Vegetation", m, float(s)) for m, s in zip(z["veg_masks"], z["veg_scores"], strict=True)] + spans
        pi = [ev.Instance(c, widen(m), s) for c, m, s in preds]
        gi = [ev.Instance(c, widen(m)) for c, m in zip(z["gt_classes"].tolist(), z["gt_masks"], strict=True)]
        row = {}
        for c in CLASSES:
            pc, gc = [p for p in pi if p.cls == c], [g for g in gi if g.cls == c]
            flags, _ = ev._match(pc, gc, 0.50)
            row[c] = ([p.score for p in pc], list(flags), len(gc))
        rows.append(row)
    return rows


def map50(rows: list[dict], idx) -> float:
    aps = []
    for c in CLASSES:
        s, f, n = [], [], 0
        for i in idx:
            s += rows[i][c][0]
            f += rows[i][c][1]
            n += rows[i][c][2]
        if n:
            aps.append(ev.average_precision(s, f, n))
    return float(np.mean(aps))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--caches", nargs="+", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--b", type=int, default=2000)
    ap.add_argument("--tmp", type=Path, default=Path("runs/cache/_fused"))
    a = ap.parse_args()
    caches = {k: Path(v) for k, v in (s.split("=", 1) for s in a.caches)}
    ex = make(FINAL)
    per = []
    for w in CANDIDATES:
        per.append(per_image(FusedCache(caches, w, a.tmp).build(), ex))
        print(w, f"full tolerant mAP50 {map50(per[-1], range(len(per[-1]))):.4f}", flush=True)
    n = len(per[0])
    rng = np.random.default_rng(0)
    draws = [rng.integers(0, n, n) for _ in range(a.b)]
    base = np.array([map50(per[0], d) for d in draws])
    out = []
    for w, rows in zip(CANDIDATES, per, strict=True):
        v = np.array([map50(rows, d) for d in draws])
        diff = v - base
        rec = {"weights": w, "tolerant_map50": map50(rows, range(n)), "boot_mean": float(v.mean()),
               "diff_ci95": [float(np.percentile(diff, 2.5)), float(np.percentile(diff, 97.5))],
               "p_beats_r34": float((diff > 0).mean()), "p_ties_r34": float((diff == 0).mean())}
        out.append(rec)
        print(rec, flush=True)
    a.out.write_text(json.dumps({"photos": n, "resamples": a.b, "seed": 0, "candidates": out}, indent=1))


if __name__ == "__main__":
    main()
