"""v2.16: re-tune span joining for the strand extractor (validation, cached probabilities).

Each row changes one setting from the shipped values; scored by tolerant mAP50 (selection metric since v2.8) with the
v2.16 consistency metrics (fragments per labelled wire, mixed-class rate, merge rate) reported alongside.

    python -m ml.experiments.v216_join_sweep --out docs/thesis/results/v216_join_sweep_val.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.config import ROOT_DIR, load_analysis_config
from app.pipeline.wirenet import span_params
from ml.experiments import harness
from ml.experiments.v216_strands import consistency, strand

GRID = [
    {},
    {"max_gap": 100}, {"max_gap": 140},
    {"max_gap": 100, "bridge_min_prob": 0.15}, {"max_gap": 140, "bridge_min_prob": 0.15},
    {"max_gap": 140, "bridge_min_prob": 0.3},
    {"gap_angle_deg": 15}, {"gap_angle_deg": 35},
    {"max_turn_deg": 25}, {"max_turn_deg": 45},
    {"cluster_radius": 8},
    {"min_len": 10}, {"min_span_len": 60}, {"min_span_len": 120},
    {"max_lateral_px": 8}, {"max_lateral_px": 15},
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path("runs/cache/val-v216"))
    ap.add_argument("--out", type=Path, default=Path("docs/thesis/results/v216_join_sweep_val.jsonl"))
    a = ap.parse_args()
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml").segmentation
    base = span_params(cfg)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("w") as f:
        for ch in GRID:
            fn = strand({**base, **ch})
            r = harness.score(a.cache, fn, conf=cfg.conf_min)
            c = consistency(a.cache, fn)
            row = {"change": ch, "tol_map50": r["tolerant"]["map50"], "strict_map50": r["strict"]["map50"],
                   "power_f1": r["tolerant"]["per_class"]["power-distribution-lines"]["f1"],
                   "telecom_f1": r["tolerant"]["per_class"]["telecom-cable"]["f1"], **c}
            f.write(json.dumps(row) + "\n")
            f.flush()
            print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
