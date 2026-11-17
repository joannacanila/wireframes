"""v2.18 pole error analysis on saved detections (``eval_detectors --save-dets``). Diagnostic, not the acceptance metric.

1. False detections split into *localisation* errors (the box overlaps a labelled pole at 0.1 <= IoU < 0.5: right
   pole, box extent disagrees, typically crossarms included or not on a 5-15 px wide shaft) and *background*
   errors (no labelled pole nearby: a lamp post, mast, tree, building edge).
2. Pole-axis localisation, the quantity the wire pipeline would consume (where the pole stands and how far it
   extends vertically): a prediction matches a labelled medium/large pole when its centre x lies within
   max(half the labelled width, 2 % of the image width) of the labelled centre and the vertical extents overlap
   by IoU >= 0.5 (1-D). Defined before looking at results; reported beside, never instead of, box IoU >= 0.5.

    python -m ml.poles.v218_analysis --dets runs/v218/dets/yolo11n-v218_val.json --threshold 0.25
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2

from app.pipeline.poles import _iou, clean_boxes
from ml.poles.eval_detectors import SMALL, _area, _prf, gt_boxes, match_status


def axis_ok(p, g, W) -> bool:
    cx_p, cx_g = (p[0] + p[2]) / 2, (g[0] + g[2]) / 2
    tol = max((g[2] - g[0]) / 2, 0.02 * W)
    inter = max(0.0, min(p[3], g[3]) - max(p[1], g[1]))
    union = max(p[3], g[3]) - min(p[1], g[1])
    return abs(cx_p - cx_g) <= tol and union > 0 and inter / union >= 0.5


def analyse(dets_file: Path, data_root: Path, split: str, thr: float, zero_shot: bool) -> dict:
    d = json.loads(dets_file.read_text())
    loc = bg = tp_axis = fp_axis = n_gt = 0
    for name, ds in zip(d["images"], d["dets"], strict=True):
        p = data_root / "images" / split / name
        h, w = cv2.imread(str(p)).shape[:2]
        g = gt_boxes(Path(str(p).replace("/images/", "/labels/")).with_suffix(".txt"), w, h)
        ds = [x for x in ds if x["confidence"] >= thr]
        if zero_shot:
            ds = clean_boxes(ds)
        ds = sorted(ds, key=lambda x: -x["confidence"])
        for x, s in zip(ds, match_status(ds, g, ignore_small=True), strict=True):
            if s == "fp":
                if any(0.1 <= _iou(x["bbox"], gg) < 0.5 for gg in g):
                    loc += 1
                else:
                    bg += 1
        big = [gg for gg in g if _area(gg) >= SMALL]
        used = set()
        for x in ds:
            j = next((k for k, gg in enumerate(big) if k not in used and axis_ok(x["bbox"], gg, w)), None)
            if j is not None:
                used.add(j)
                tp_axis += 1
            elif not any(axis_ok(x["bbox"], gg, w) for gg in g) and _area(x["bbox"]) >= SMALL:
                fp_axis += 1
        n_gt += len(big)
    return {"dets": str(dets_file), "threshold": thr, "false_detections": {"localisation": loc, "background": bg},
            "axis_localisation_medium_large": _prf(tp_axis, fp_axis, n_gt)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dets", type=Path, required=True)
    ap.add_argument("--data-root", type=Path, default=Path("datasets/external/poles_v218"))
    ap.add_argument("--split", default="val")
    ap.add_argument("--threshold", type=float, required=True)
    ap.add_argument("--zero-shot", action="store_true")
    a = ap.parse_args()
    r = analyse(a.dets, a.data_root, a.split, a.threshold, a.zero_shot)
    print(json.dumps(r, indent=1))
    Path(f"docs/thesis/results/v218_poles_analysis_{a.dets.stem}.json").write_text(json.dumps(r, indent=1))


if __name__ == "__main__":
    main()
