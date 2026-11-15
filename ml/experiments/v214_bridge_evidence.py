"""v2.14 evidence-gated gap bridging, measured on validation (frozen WireNet-R34, cached probabilities).

    python -m ml.experiments.v214_bridge_evidence --out docs/thesis/results/v214_bridge_evidence_val.json

The shipped extractor (FINAL) bridges collinear gaps up to 70 px by drawing a straight 2-px line into the span
mask. Those pixels were never labelled wire by WireNet. This experiment asks whether requiring WireNet evidence in
the gap (mean class probability along the bridge >= tau) removes unsupported bridges without losing span
tolerant mAP50. For every tau: span strict / tolerant mAP50, the number of bridges accepted, and the support
distribution of the bridges the shipped extractor accepts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from app.core.config import CLASSES, VEGETATION
from app.pipeline.wire_instances import extract_spans
from ml.experiments.ablation_final import FINAL
from ml.experiments.harness import score

TAUS = (0.0, 0.02, 0.05, 0.1, 0.15, 0.2, 0.3)


def make(params: dict, log: list):
    def ex(probs, cls):
        out = []
        for name in CLASSES:
            if name == VEGETATION:
                continue
            c = CLASSES.index(name) + 1
            m = cls == c
            if m.any():
                br: list = []
                out += [(name, s, conf) for s, conf in extract_spans(m, probs[c], bridges_out=br, **params)]
                log.extend((c, b) for b in br)
        return out
    return ex


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path("runs/cache/val"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--lateral", action="store_true", help="sweep max_lateral_px instead of the probability gate")
    ap.add_argument("--component", action="store_true", help="own_component off / on, alone and with lateral 5 px")
    a = ap.parse_args()
    rows = []
    grid = [{"max_lateral_px": v} for v in (0.0, 3.0, 5.0, 8.0, 12.0)] if a.lateral else [{"bridge_min_prob": t} for t in TAUS]
    if a.component:
        grid = [{"own_component": True}, {"own_component": True, "max_lateral_px": 5.0}]
    for extra in grid:
        log: list = []
        r = score(a.cache, make(dict(FINAL, **extra), log))
        t = r["tolerant"]
        row = {"params": extra, "strict_map50": r["strict"]["map50"], "tolerant_map50": t["map50"],
               "spans": sum(r["spans_all_scores"].values()), "bridges": len(log),
               "power_recall": t["per_class"]["power-distribution-lines"]["recall"],
               "telecom_recall": t["per_class"]["telecom-cable"]["recall"]}
        rows.append(row)
        print(json.dumps(row), flush=True)
    a.out.write_text(json.dumps({"final": FINAL, "rows": rows}, indent=1))


if __name__ == "__main__":
    _ = np
    main()
