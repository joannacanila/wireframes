"""Probability fusion of WireNet backbones (v2.12, validation only).

    python -m ml.experiments.ensemble_eval --caches r34=runs/cache/val-v28 mitb1=runs/cache/val-mitb1 \
        cnx=runs/cache/val-cnx --out docs/thesis/results/v212_ensemble_val.jsonl

Each cache holds one model's flip-TTA class probabilities for the 22 validation photos, produced by the
deployed preprocessing (ml.experiments.cache --wirenet ...). For each weight vector on a coarse simplex
grid (step 0.25) the fused map is p = sum_i w_i p_i, and it is scored like v2.8-v2.11:
  * span level: strict and 2 px tolerant mAP50 with the v2.8 span constants (the screener's spans),
  * pixel level: tolerant IoU and F1 for power and telecom, and power-vs-telecom confusion (the share of
    labelled power pixels predicted as telecom and vice versa).
A candidate is adopted only if it beats the single WireNet-R34 (weight 1, 0, 0) on validation.
"""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import cv2
import numpy as np

from app.core.config import CLASSES
from ml.experiments.ablation_final import FINAL
from ml.experiments.harness import score
from ml.experiments.sweep_spans import make

POWER, TEL = CLASSES.index("power-distribution-lines") + 1, CLASSES.index("telecom-cable") + 1
K = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))


class FusedCache:
    """Looks like a cache directory to ml.experiments.harness.score: yields fused npz-like dicts."""

    def __init__(self, caches: dict[str, Path], weights: dict[str, float], tmp: Path):
        self.caches, self.weights, self.tmp = caches, weights, tmp

    def build(self) -> Path:
        self.tmp.mkdir(parents=True, exist_ok=True)
        for old in self.tmp.glob("*.npz"):
            old.unlink()
        ref = next(iter(self.caches.values()))
        for f in sorted(ref.glob("*.npz")):
            z = dict(np.load(f))
            probs = sum(w * np.load(c / f.name)["probs"].astype(np.float32) for k, c in self.caches.items()
                        if (w := self.weights[k]) > 0)
            z["probs"] = probs.astype(np.float16)
            np.savez(self.tmp / f.name, **z)
        return self.tmp


def pixel_scores(cache: Path) -> dict:
    """Tolerant (2 px) IoU, precision, recall and F1 for power and telecom, and power<->telecom confusion."""
    from ml.wirenet import semantic_target
    acc = {c: np.zeros(6) for c in ("power", "telecom")}  # inter, union, pred-in-tol, pred, gt-in-tol, gt
    conf = np.zeros(4)  # power px called telecom, power px, telecom px called power, telecom px
    for f in sorted(cache.glob("*.npz")):
        pred = np.load(f)["probs"].astype(np.float32).argmax(0)
        lbl = Path(f"datasets/wireframe_seg/labels/val/{f.stem}.txt")
        tgt = semantic_target(lbl.read_text() if lbl.exists() else "", *pred.shape)
        for name, idx in (("power", POWER), ("telecom", TEL)):
            p, t = pred == idx, tgt == idx
            pd, td = cv2.dilate(p.astype(np.uint8), K).astype(bool), cv2.dilate(t.astype(np.uint8), K).astype(bool)
            acc[name] += [(pd & td).sum(), (pd | td).sum(), (p & td).sum(), p.sum(), (t & pd).sum(), t.sum()]
        conf += [((tgt == POWER) & (pred == TEL)).sum(), (tgt == POWER).sum(),
                 ((tgt == TEL) & (pred == POWER)).sum(), (tgt == TEL).sum()]
    out = {}
    for name, (inter, union, ptp, npred, ttp, ngt) in acc.items():
        prec, rec = ptp / max(npred, 1), ttp / max(ngt, 1)
        out[f"{name}_tol_iou"] = inter / max(union, 1)
        out[f"{name}_tol_f1"] = 2 * prec * rec / max(prec + rec, 1e-9)
        out[f"{name}_tol_precision"], out[f"{name}_tol_recall"] = prec, rec
    out["power_as_telecom"] = conf[0] / max(conf[1], 1)
    out["telecom_as_power"] = conf[2] / max(conf[3], 1)
    return out


def simplex(names, step=0.25):
    n = int(round(1 / step))
    for c in itertools.product(range(n + 1), repeat=len(names)):
        if sum(c) == n:
            yield dict(zip(names, (x / n for x in c), strict=True))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--caches", nargs="+", required=True, help="name=path")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--step", type=float, default=0.25)
    ap.add_argument("--tmp", type=Path, default=Path("runs/cache/_fused"))
    a = ap.parse_args()
    caches = {k: Path(v) for k, v in (x.split("=", 1) for x in a.caches)}
    for w in simplex(list(caches), a.step):
        d = FusedCache(caches, w, a.tmp).build()
        r = score(d, make(FINAL))
        px = pixel_scores(d)
        row = {"weights": w, "strict_map50": r["strict"]["map50"], "tolerant_map50": r["tolerant"]["map50"],
               "power": r["tolerant"]["per_class"]["power-distribution-lines"],
               "telecom": r["tolerant"]["per_class"]["telecom-cable"], **px}
        with open(a.out, "a") as fh:
            fh.write(json.dumps(row, default=float) + "\n")
        print(json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()
                          if k not in ("power", "telecom")}), flush=True)


if __name__ == "__main__":
    main()
