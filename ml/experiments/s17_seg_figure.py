"""S17 Part 3: representative instance figures, baseline vs a candidate variant, from the stored dumps (no inference).

Picks the val/test images where the two variants differ most in strictly fragmented wires and draws each predicted
instance in its own colour over the cropped photo, with the labelled wires as thin white outlines.

    PYTHONPATH=. python -m ml.experiments.s17_seg_figure cluster12
"""
from __future__ import annotations

import json
import sys

import cv2
import numpy as np

from app.core.config import ROOT_DIR
from ml.experiments import s17_seg_eval as E

OUT = ROOT_DIR / "docs/thesis/figures/s17"


def _img(split, stem):
    bx = E.sa.boxes(split)
    p = next(p for p in E.v._paths(split) if p.stem == stem)
    y0, y1, x0, x1 = bx[stem]
    return cv2.imread(str(p))[y0:y1, x0:x1]


def _draw(img, row, gt):
    out = (img * 0.55).astype(np.uint8)
    rng = np.random.default_rng(7)
    for w in row["wires"]:
        col = tuple(int(c) for c in rng.integers(80, 255, 3))
        for poly in w["polygons"]:
            cv2.fillPoly(out, [np.array(poly, np.int32).reshape(-1, 1, 2)], col)
    for c, m in gt:
        if c in E.WIRE_CLASSES:
            cs, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(out, cs, -1, (255, 255, 255), 1)
    return out


def main(cand: str, n: int = 3) -> None:
    res = json.loads((ROOT_DIR / "docs/thesis/results" / f"s17_seg_{cand}.json").read_text())
    base = json.loads((ROOT_DIR / "docs/thesis/results/s17_seg_baseline.json").read_text())
    picks = []
    for split in ("val", "test"):
        pb, pc = base["splits"][split]["_per_image"], res["splits"][split]["_per_image"]
        picks += [(abs(pb[k]["fragmented"] - pc[k]["fragmented"]), split, k, pb[k]["fragmented"], pc[k]["fragmented"]) for k in pb if k in pc]
    picks.sort(reverse=True)
    for i, (_, split, stem, fb, fc) in enumerate(picks[:n], 1):
        rb = next(r for r in json.loads((E.DUMP / f"baseline-{split}.json").read_text()) if r["stem"] == stem)
        rc = next(r for r in json.loads((E.DUMP / f"{cand}-{split}.json").read_text()) if r["stem"] == stem)
        img, gt = _img(split, stem), E.sa.gts(split, "field")[stem]
        a, b = _draw(img, rb, gt), _draw(img, rc, gt)
        for im, t in ((a, f"baseline: {len(rb['wires'])} spans, {fb} fragmented"), (b, f"{cand}: {len(rc['wires'])} spans, {fc} fragmented")):
            cv2.rectangle(im, (0, 0), (im.shape[1], 26), (0, 0, 0), -1)
            cv2.putText(im, t, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        f = OUT / f"seg_{cand}_vs_baseline_{i}.jpg"
        cv2.imwrite(str(f), np.hstack([a, np.full((a.shape[0], 6, 3), 255, np.uint8), b]), [cv2.IMWRITE_JPEG_QUALITY, 85])
        print(f, split, stem[:40], fb, fc)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "cluster12")
