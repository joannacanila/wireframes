"""Calibrate the motion-blur router on the validation stress set (v2.9).

    python -m ml.experiments.motion_calib --out docs/thesis/results/v29_motion_calib_val.json

For every validation photo under every stress condition: the enhancement measurements
(Laplacian variance, motion coherence, clipped share). Reported: how many photos that would get
the unsharp mask (sharpness <= blur_laplacian_max) are routed as motion blur at each coherence
threshold, per condition. The threshold is the smallest that keeps defocus and clean photos
out of the motion route while catching the motion condition.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import yaml

from app.core.config import EnhancementConfig
from app.pipeline.enhance import measure
from ml.stress import CONDITIONS, apply_condition

THRESHOLDS = (0.10, 0.12, 0.14, 0.16, 0.18, 0.20, 0.25)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    cfg = EnhancementConfig()
    spec = yaml.safe_load(open("datasets/wireframe_seg/data.yaml"))
    photos = sorted((Path("datasets/wireframe_seg") / spec["val"]).glob("*.jpg"))
    rows = []
    for c in CONDITIONS:
        for p in photos:
            q = measure(apply_condition(cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB), c, p.name))
            rows.append({"condition": c, "image": p.stem, "sharpness": q.sharpness, "motion_coherence": q.motion_coherence,
                         "clipped_high": q.clipped_high})
    table = {}
    for t in THRESHOLDS:
        table[str(t)] = {}
        for c in CONDITIONS:
            r = [x for x in rows if x["condition"] == c and x["sharpness"] <= cfg.blur_laplacian_max]
            routed = [x for x in r if x["motion_coherence"] >= t and x["clipped_high"] < cfg.overexposed_frac_min]
            table[str(t)][c] = [len(routed), len(r)]
        print(t, " ".join(f"{c}:{v[0]}/{v[1]}" for c, v in table[str(t)].items()))
    a.out.write_text(json.dumps({"routed_of_soft": table, "rows": rows}, indent=1, default=float))


if __name__ == "__main__":
    main()
