"""Coordinate-descent ablation of span-extraction constants on the validation cache.

    python -m ml.experiments.sweep_spans --out docs/thesis/results/v28_span_sweep.jsonl
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app.core.config import CLASSES, VEGETATION
from app.pipeline.wire_instances import extract_spans
from ml.experiments.harness import score, summary

GRID = {
    "max_gap": [0, 15, 25, 35, 50, 70],
    "gap_angle_deg": [10, 15, 20, 25],
    "draw_bridge": [False, True],
    "min_span_len": [0, 30, 45, 60, 90],
    "max_turn_deg": [15, 20, 25, 35],
    "cluster_radius": [5, 10, 15],
    "min_len": [15, 30, 45],
}
BASE = {"max_gap": 0, "gap_angle_deg": 15, "draw_bridge": True, "min_span_len": 0, "max_turn_deg": 25.0,
        "cluster_radius": 10, "min_len": 30}  # = v2.7


def make(params):
    def ex(probs, cls):
        out = []
        for name in CLASSES:
            if name == VEGETATION:
                continue
            c = CLASSES.index(name) + 1
            m = cls == c
            if m.any():
                out += [(name, s, conf) for s, conf in extract_spans(m, probs[c], **params)]
        return out
    return ex


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--rounds", type=int, default=2)
    a = ap.parse_args()
    cache = Path(f"runs/cache/{a.split}")
    best = dict(BASE)
    seen = {}

    def run(p):
        key = json.dumps(p, sort_keys=True)
        if key not in seen:
            t = time.time()
            r = score(cache, make(p))
            seen[key] = r
            with open(a.out, "a") as fh:
                fh.write(json.dumps({"params": p, "split": a.split, "seconds": round(time.time() - t, 1), **r},
                                    default=float) + "\n")
            print(json.dumps(p), "->", summary(r), flush=True)
        return seen[key]

    base_r = run(best)
    best_score = base_r["tolerant"]["map50"]
    for rnd in range(a.rounds):
        for k, values in GRID.items():
            for v in values:
                p = dict(best, **{k: v})
                r = run(p)
                if r["tolerant"]["map50"] > best_score + 1e-6:
                    best_score, best = r["tolerant"]["map50"], p
                    print(f"  round {rnd + 1}: new best {best_score:.4f} with {k}={v}", flush=True)
    print("BEST", json.dumps(best), best_score, flush=True)


if __name__ == "__main__":
    main()
