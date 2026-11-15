"""Clean one-factor-at-a-time ablation around the final span constants (validation cache).

    python -m ml.experiments.ablation_final --cache runs/cache/val --out docs/thesis/results/v28_span_ablation_final.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ml.experiments.harness import score
from ml.experiments.sweep_spans import make

FINAL = {"max_gap": 70, "gap_angle_deg": 25, "draw_bridge": True, "min_span_len": 90, "max_turn_deg": 35,
         "cluster_radius": 5, "min_len": 15}
GRID = {"max_gap": [0, 35, 50, 100, 150], "gap_angle_deg": [10, 15, 20, 30, 35], "draw_bridge": [False],
        "min_span_len": [0, 45, 60, 120, 200], "max_turn_deg": [15, 25, 45], "cluster_radius": [2, 10, 15],
        "min_len": [30, 45]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path("runs/cache/val"))
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    runs = [("final", None, None)] + [(k, k, v) for k, vs in GRID.items() for v in vs]
    for label, k, v in runs:
        p = dict(FINAL) if k is None else dict(FINAL, **{k: v})
        r = score(a.cache, make(p))
        with open(a.out, "a") as fh:
            fh.write(json.dumps({"varied": k, "value": v, "params": p, "tolerant_map50": r["tolerant"]["map50"],
                                 "strict_map50": r["strict"]["map50"],
                                 "power": r["tolerant"]["per_class"]["power-distribution-lines"],
                                 "telecom": r["tolerant"]["per_class"]["telecom-cable"]}, default=float) + "\n")
        print(label, k, v, round(r["tolerant"]["map50"], 4), flush=True)


if __name__ == "__main__":
    main()
