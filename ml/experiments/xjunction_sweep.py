"""X-junction tangent-continuity matching (v2.9) on the v2.8 validation cache.

    python -m ml.experiments.xjunction_sweep --cache runs/cache/val-v28 --out docs/thesis/results/v29_xjunction_val.jsonl

All other span constants are fixed at v2.8's final values; the tangent tolerance is swept over
15, 20 and 25 deg (crossing angle > 30 deg). Adopted only if it beats v2.8's 0.0397 tolerant
mAP50 on validation by more than the 0.002 noise band (Chapter 4 v2.8, decision rule).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ml.experiments.ablation_final import FINAL
from ml.experiments.harness import score, summary
from ml.experiments.sweep_spans import make


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path("runs/cache/val-v28"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--tols", nargs="*", type=float, default=[0, 15, 20, 25])
    ap.add_argument("--cross", nargs="*", type=float, default=[30])
    ap.add_argument("--near-tangent", action="store_true", help="ablation: k-nearest end tangents")
    a = ap.parse_args()
    for cross in a.cross:
        for tol in a.tols:
            p = dict(FINAL, xjunction_tol_deg=tol, xjunction_min_cross_deg=cross,
                     xjunction_far_tangent=not a.near_tangent)
            r = score(a.cache, make(p))
            with open(a.out, "a") as fh:
                fh.write(json.dumps({"params": p, "tolerant_map50": r["tolerant"]["map50"],
                                     "strict_map50": r["strict"]["map50"],
                                     "power": r["tolerant"]["per_class"]["power-distribution-lines"],
                                     "telecom": r["tolerant"]["per_class"]["telecom-cable"],
                                     "spans": r["spans_all_scores"]}, default=float) + "\n")
            print(f"tol {tol} cross {cross}:", summary(r), flush=True)


if __name__ == "__main__":
    main()
