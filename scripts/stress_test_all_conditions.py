"""Eight-condition stress benchmark of the full screener pipeline (v2.8, v2.9).

    python -m scripts.stress_test_all_conditions --split test --out docs/thesis/results/v28_stress_spans_test.jsonl

Configurations: enhancement OFF, v2.7 (v2.7 enhancement triggers, no TTA, no span stitching),
v2.8 (v2.8 routing: no motion route, no deblocking), the v2.9 routing candidates, and v2.9
(shipped config). The candidates are compared on validation; test is run once with the locked
configuration. Conditions: ml/stress.py. Metrics per run: span-level strict and 2 px
tolerant mAP50 and per-class AP50 / F1 / recall (ml.evaluate.segmentation_eval, the screener's
own path) and seconds per image.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ml.evaluate import segmentation_eval
from ml.stress import CONDITIONS

V27 = {"enhancement": {"edge_denoise": False, "dark_l_max": 35.0},
       "segmentation": {"wirenet_tta": "none", "span_max_gap": 0.0, "span_min_stitched_len": 0, "span_min_len": 30,
                        "span_max_turn_deg": 25.0, "span_cluster_radius": 10, "span_gap_angle_deg": 15.0}}
V28 = {"enhancement": {"motion_route": False, "deblock": False}, "crs": {"max_span_inclination_deg": 0.0}}
CONFIGS = {"enhancement_off": ({"enhancement": {"mode": "off", "fallback": False}}, False),
           "v2.7": (V27, True), "v2.8": (V28, True),
           "v2.8+motion": ({"enhancement": {"motion_route": True, "deblock": False}}, True),
           "v2.8+motion_skip": ({"enhancement": {"motion_route": True, "motion_action": "skip", "deblock": False}}, True),
           "v2.8+deblock": ({"enhancement": {"motion_route": False, "deblock": True}}, True),
           "v2.8+motion+deblock": ({"enhancement": {"motion_route": True, "deblock": True}}, True),
           "v2.8+motion_skip+deblock": ({"enhancement": {"motion_route": True, "motion_action": "skip", "deblock": True}}, True),
           "v2.8+motion_fbu": ({"enhancement": {"motion_route": True, "deblock": False,
                                                "motion_fallback_unsharp": True}}, True),
           "v2.8+motion_skip_fbu": ({"enhancement": {"motion_route": True, "motion_action": "skip", "deblock": False,
                                                     "motion_fallback_unsharp": True}}, True),
           "v2.9": ({"segmentation": {"dual_polarity": False}}, True),
           "v2.10": ({"segmentation": {"dual_polarity": False}}, True),
           "v2.11": ({}, True),
           "v2.12": ({}, True),  # v2.12 ships the v2.11 pipeline (fusion not adopted: latency, Chapter 4 v2.12)
           "v2.13": ({}, True)}  # frozen v2.13: the shipped config, i.e. v2.12 + the ground gate (Chapter 4 v2.13)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", default="test")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--conditions", nargs="*", default=list(CONDITIONS))
    ap.add_argument("--configs", nargs="*", default=list(CONFIGS))
    a = ap.parse_args()
    data = Path("datasets/wireframe_seg/data.yaml")
    for cond in a.conditions:
        for name in a.configs:
            ov, enh = CONFIGS[name]
            if name == "enhancement_off":  # same span extraction as v2.7, enhancement and fallback off
                ov = {"enhancement": {"mode": "off", "fallback": False}, "segmentation": V27["segmentation"]}
            r = segmentation_eval(data, a.split, enhancement=enh, condition=cond, overrides=ov)
            keep = {k: r[k] for k in ("map50", "map50_95", "per_class", "tolerant_2px", "fallback",
                                      "seconds_per_image_incl_eval", "n_images")}
            with open(a.out, "a") as fh:
                fh.write(json.dumps({"split": a.split, "condition": cond, "config": name, **keep}, default=float) + "\n")
            pc, tc = r["tolerant_2px"]["per_class"]["power-distribution-lines"], r["tolerant_2px"]["per_class"]["telecom-cable"]
            print(f"{a.split} {cond:9s} {name:16s} strict mAP50 {r['map50']:.4f} tol mAP50 {r['tolerant_2px']['map50']:.4f} "
                  f"| power tol AP50 {pc['ap50']:.3f} R {pc['recall']:.3f} | telecom tol AP50 {tc['ap50']:.3f} R {tc['recall']:.3f} "
                  f"| {r['seconds_per_image_incl_eval']}s/img", flush=True)


if __name__ == "__main__":
    main()
