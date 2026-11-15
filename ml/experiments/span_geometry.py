"""Span geometry and test-retest of CRS levels (v2.9, validation only).

    python -m ml.experiments.span_geometry --split val --out docs/thesis/results/v29_span_geometry_val.json

Two measurements of each validation photo are taken with the shipped v2.8 configuration but
without flip TTA (otherwise the original and mirrored inputs give the same averaged map): the
photo and its mirror image, the second mapped back. Spans of the same class are matched by 2 px
tolerant mask IoU >= 0.3 (greedy, best first). Recorded per span: class, chord, chord
inclination (from the centreline end points, 0 deg = horizontal), CRS, level, status.

Used for two v2.9 decisions, both on validation:
* the share of high-band spans by inclination band (are steep wires the ones that read high?),
* the test-retest discordance of the high and >= medium levels, i.e. how often a span's level
  is not reproduced by a second measurement of the same photo: the per-span false-alarm rate
  p0 that the pole-level corroboration rule (app/pipeline/hazard.py) is built on.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import yaml

from app.core.config import ROOT_DIR, load_analysis_config

K = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))


def inclination_deg(points) -> float | None:
    if points is None or len(points) < 2:
        return None
    (x0, y0), (x1, y1) = points[0], points[-1]
    return float(np.degrees(np.arctan2(abs(y1 - y0), abs(x1 - x0))))


def _mask(polys, shape) -> np.ndarray:
    m = np.zeros(shape, np.uint8)
    for p in polys:
        cv2.fillPoly(m, [np.asarray(p, np.int32).reshape(-1, 1, 2)], 1)
    return m.astype(bool)


def spans(outcome, shape, mirrored=False):
    rows = []
    for w in outcome.wires:
        m = _mask(w.polygons, shape)
        if mirrored:
            m = m[:, ::-1]
        rows.append({"class": w.wire_class, "status": w.status, "level": w.level, "crs": w.crs,
                     "chord_px": (w.fit or {}).get("chord_px"), "inclination_deg": inclination_deg(w.centerline),
                     "mask": cv2.dilate(m.astype(np.uint8), K).astype(bool)})
    return rows


def match(a, b, thr=0.3):
    cand = []
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            if x["class"] != y["class"]:
                continue
            inter = (x["mask"] & y["mask"]).sum()
            if inter:
                iou = inter / (x["mask"] | y["mask"]).sum()
                if iou >= thr:
                    cand.append((iou, i, j))
    used_a, used_b, pairs = set(), set(), []
    for iou, i, j in sorted(cand, reverse=True):
        if i not in used_a and j not in used_b:
            used_a.add(i)
            used_b.add(j)
            pairs.append((i, j, float(iou)))
    return pairs


def main() -> None:
    from app.pipeline.runner import run_analysis
    from app.pipeline.wirenet import make_segmenter
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    assert a.split == "val", "rule-design evidence is taken on validation only"
    base = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    cfg = base.model_copy(update={"segmentation": base.segmentation.model_copy(update={"wirenet_tta": "none"}),
                                  "hazard": base.hazard.model_copy(update={"enabled": False}),
                                  "quality": base.quality.model_copy(update={"enabled": False})})
    seg = make_segmenter(cfg.segmentation, ROOT_DIR)
    assert seg.load(), seg.error
    data = Path("datasets/wireframe_seg/data.yaml")
    spec = yaml.safe_load(data.read_text())
    out = []
    for p in sorted((data.parent / spec[a.split]).glob("*.jpg")):
        rgb = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
        A = spans(run_analysis(rgb, cfg, seg), rgb.shape[:2])
        B = spans(run_analysis(np.ascontiguousarray(rgb[:, ::-1]), cfg, seg), rgb.shape[:2], mirrored=True)
        pairs = match(A, B)
        strip = lambda r: {k: v for k, v in r.items() if k != "mask"}  # noqa: E731
        out.append({"image": p.stem, "a": [strip(r) for r in A], "b": [strip(r) for r in B],
                    "pairs": [[i, j, round(iou, 3)] for i, j, iou in pairs]})
        print(p.stem[-30:], len(A), len(B), len(pairs), flush=True)
    a.out.write_text(json.dumps(out, indent=1, default=float))


if __name__ == "__main__":
    main()
