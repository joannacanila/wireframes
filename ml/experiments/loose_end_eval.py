"""Evaluate / sweep the topological loose-end detector against the abandoned-wire labels.

    python -m ml.experiments.loose_end_eval --split val --sweep
"""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import cv2
import numpy as np

from app.core.config import CLASSES, VEGETATION
from app.pipeline.loose_ends import loose_ends
from app.pipeline.wire_instances import extract_spans
from ml.wirenet_stage2 import content_mask

SPAN_PARAMS: dict = {}


def prepare(split: str, span_params: dict):
    rows = []
    for f in sorted(Path(f"runs/cache/{split}").glob("*.npz")):
        z = np.load(f)
        probs = z["probs"].astype(np.float32)
        cls = probs.argmax(0)
        spans = []
        for name in CLASSES:
            if name == VEGETATION:
                continue
            c = CLASSES.index(name) + 1
            if (cls == c).any():
                spans += [m for m, _ in extract_spans(cls == c, probs[c], **span_params)]
        img = cv2.imread(f"datasets/wireframe_seg/images/{split}/{f.stem}.jpg")
        content = content_mask(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        gt = [m for c, m in zip(z["gt_classes"].tolist(), z["gt_masks"], strict=True) if c == "abandoned-wire"]
        rows.append((f.stem, spans, content, gt))
    return rows


def evaluate(rows, params: dict, tau: float, near_px: int = 15) -> dict:
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * near_px + 1, 2 * near_px + 1))
    hits = n_gt = cand = cand_ok = 0
    for _, spans, content, gt in rows:
        ends = [e for e in loose_ends(spans, content, **params) if e.score >= tau]
        cand += len(ends)
        zones = [cv2.dilate(g.astype(np.uint8), k).astype(bool) for g in gt]
        n_gt += len(gt)
        for z in zones:
            hits += any(z[int(e.y), int(e.x)] for e in ends)
        cand_ok += sum(any(z[int(e.y), int(e.x)] for z in zones) for e in ends)
    rec = hits / max(n_gt, 1)
    prec = cand_ok / max(cand, 1)
    return {"params": params, "tau": tau, "hits": hits, "n_gt": n_gt, "candidates": cand, "candidates_on_label": cand_ok,
            "recall": rec, "precision_lower_bound": prec, "f": 2 * rec * prec / max(rec + prec, 1e-9)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val")
    ap.add_argument("--span-params", default="{}")
    ap.add_argument("--params", default=None, help="fixed detector params (JSON) + tau")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    rows = prepare(a.split, json.loads(a.span_params))
    results = []
    if a.params:
        p = json.loads(a.params)
        tau = p.pop("tau")
        results.append(evaluate(rows, p, tau))
    else:
        for iso, cpx, cn, tau in itertools.product([4, 8, 12, 20], [15, 25, 40], [1, 2, 3], [0.3, 0.5, 0.7]):
            results.append(evaluate(rows, {"isolation_px": iso, "cluster_px": cpx, "cluster_n": cn}, tau))
    results.sort(key=lambda r: (-r["f"], -r["precision_lower_bound"]))
    for r in results[:8]:
        print(json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()}))
    if a.out:
        a.out.write_text(json.dumps(results, indent=1, default=float))


if __name__ == "__main__":
    main()
