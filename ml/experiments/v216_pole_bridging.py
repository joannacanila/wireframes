"""v2.16: does bridging across a detected pole reduce fragmentation without hurting spans? (validation, cached probs)

Runs the trained pole detector on each validation photo (1024 letterbox = model space), then the v2.16 strand
extractor with ``occluders`` = pole boxes and ``occluder_max_gap`` in {100, 140, 180} px (the normal limit stays
70 px; a longer bridge must cross a pole box and pass every other test). Adopted only if tolerant mAP50 does not drop.

    python -m ml.experiments.v216_pole_bridging --weights runs/detect/runs/poles/yolo11n/weights/best.pt
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2

from app.core.config import ROOT_DIR, load_analysis_config
from app.pipeline.poles import PoleDetector
from app.pipeline.wire_instances import strand_spans
from app.pipeline.wirenet import span_params
from ml.experiments import harness
from ml.experiments.v216_strands import WIRE_IDS, consistency


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default="runs/detect/runs/poles/yolo11n/weights/best.pt")
    ap.add_argument("--cache", type=Path, default=Path("runs/cache/val-v216"))
    ap.add_argument("--conf", type=float, default=0.35)
    ap.add_argument("--out", type=Path, default=Path("docs/thesis/results/v216_pole_bridging_val.json"))
    a = ap.parse_args()
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml").segmentation
    base = span_params(cfg)
    det = PoleDetector(Path(a.weights), a.conf)
    poles = {}
    for stem, _ in harness.load(a.cache):
        img = cv2.cvtColor(cv2.imread(f"datasets/wireframe_seg/images/val/{stem}.jpg"), cv2.COLOR_BGR2RGB)
        poles[stem] = [p["bbox"] for p in det.detect(img) or []]
    n_poles = sum(len(v) for v in poles.values())
    rows = {"poles_detected": n_poles, "photos_with_poles": sum(bool(v) for v in poles.values()), "runs": {}}
    print(f"poles on validation: {n_poles} in {rows['photos_with_poles']} of {len(poles)} photos")
    order = [stem for stem, _ in harness.load(a.cache)]  # harness.load visits the cache in this (sorted) order
    for gap in (0, 100, 140, 180):
        def make(_gap=gap):
            it = iter(order)  # the extractor is called once per photo, in cache order
            return lambda probs, cls_map: _strands(probs, poles[next(it)], base, _gap)
        r = harness.score(a.cache, make(), conf=cfg.conf_min)
        c = consistency(a.cache, make())
        rows["runs"][str(gap)] = {"tol_map50": r["tolerant"]["map50"], "strict_map50": r["strict"]["map50"],
                                  "fragments": c["fragments"], "mixed_rate": c["mixed_rate"], "merge_rate": c["merge_rate"]}
        print(gap, rows["runs"][str(gap)], flush=True)
    a.out.write_text(json.dumps(rows, indent=1))


def _strands(probs, boxes, base, gap):
    p = {**base, **({"occluders": boxes, "occluder_max_gap": gap} if gap and boxes else {})}
    return [(s.wire_class, s.mask, s.confidence) for s in strand_spans(probs, WIRE_IDS, p)]


if __name__ == "__main__":
    main()
