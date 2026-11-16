"""v2.16 span assembly under the eight capture conditions (validation; ml.stress degradations, cached probabilities).

Compares the v2.15 extractor (per-class spans, 25 deg bridging, no lateral limit) with the v2.16 strand extractor
(union geometry, one class per strand, 35 deg + 15 px lateral limit) on the same probability maps, so the only
difference is span assembly. Caches: ``python -m ml.experiments.cache --split val --condition <c> --tag=-v216``.

    python -m ml.experiments.v216_stress_spans --out docs/thesis/results/v216_stress_spans_val.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.config import ROOT_DIR, load_analysis_config
from app.pipeline.wirenet import span_params
from ml.experiments import harness
from ml.experiments.v216_strands import baseline, consistency, strand
from ml.stress import CONDITIONS


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("docs/thesis/results/v216_stress_spans_val.json"))
    a = ap.parse_args()
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml").segmentation
    v216 = span_params(cfg)
    v215 = {**v216, "gap_angle_deg": 25, "max_lateral_px": 0.0}
    rows = {}
    for c in CONDITIONS:
        cache = Path("runs/cache") / ("val-v216" if c == "clean" else f"val-{c}-v216")
        if not cache.exists():
            print(f"{c}: no cache at {cache}")
            continue
        rows[c] = {}
        for name, fn in (("v215", baseline(v215)), ("v216", strand(v216, min_share=cfg.span_class_min_share))):
            r = harness.score(cache, fn, conf=cfg.conf_min)
            k = consistency(cache, fn)
            t = r["tolerant"]["per_class"]
            rows[c][name] = {"tol_map50": r["tolerant"]["map50"], "strict_map50": r["strict"]["map50"],
                             "power_f1": t["power-distribution-lines"]["f1"], "telecom_f1": t["telecom-cable"]["f1"],
                             "mixed_rate": k["mixed_rate"], "fragments": k["fragments"]}
        b, n = rows[c]["v215"], rows[c]["v216"]
        print(f"{c:9s} tol mAP50 {b['tol_map50']:.4f} -> {n['tol_map50']:.4f} | mixed {b['mixed_rate']:.3f} -> "
              f"{n['mixed_rate']:.3f} | frag {b['fragments']:.2f} -> {n['fragments']:.2f}", flush=True)
    m = {v: sum(rows[c][v]["tol_map50"] for c in rows) / max(len(rows), 1) for v in ("v215", "v216")}
    print(f"mean over {len(rows)} conditions: tol mAP50 {m['v215']:.4f} -> {m['v216']:.4f}")
    a.out.write_text(json.dumps({"conditions": rows, "mean_tol_map50": m}, indent=1))


if __name__ == "__main__":
    main()
