"""v2.16 before/after on the full deployed pipeline (run_analysis: WireNet + YOLO vegetation + scene gates + scoring).

Variants are the v2.15 settings and the v2.16 settings of config/wireframe.yaml. Instance metrics are computed as in
ml.evaluate (strict and 2 px tolerant masks, conf_min), on the wires and vegetation regions the screener reports, plus
the v2.16 consistency metrics and the verdict distribution. Writes docs/thesis/results/v216_pipeline_<split>.json.

    python -m ml.experiments.v216_pipeline_eval --split val
    python -m ml.experiments.v216_pipeline_eval --split test      # once, after every setting was frozen on val
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import yaml

from app.core.config import CLASSES, ROOT_DIR, VEGETATION
from app.pipeline import evaluation as ev
from app.pipeline.segment import parse_yolo_seg_labels
from ml.experiments.v216_regression import VARIANTS, _cfg


def _raster(polys, shape):
    m = np.zeros(shape, np.uint8)
    cv2.fillPoly(m, [np.asarray(p, np.int32) for p in polys if len(p) >= 3], 1)
    return m.astype(bool)


def run(split: str, variants: list[str]) -> dict:
    from app.pipeline.runner import run_analysis
    from app.pipeline.wirenet import make_segmenter
    spec = yaml.safe_load(Path("datasets/wireframe_seg/data.yaml").read_text())
    paths = sorted((Path("datasets/wireframe_seg") / spec[split]).glob("*.jpg"))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    widen = lambda m: cv2.dilate(m.astype(np.uint8), k).astype(bool)  # noqa: E731
    out = {}
    for v in variants:
        cfg = _cfg(VARIANTS[v])
        seg = make_segmenter(cfg.segmentation, ROOT_DIR)
        assert seg.load(), seg.error
        strict, tol = [], []
        n_gt = n_cov = n_mixed = 0
        frags = []
        verdicts: dict[str, int] = {}
        n_scored = n_wires = n_uncertain = n_veg = n_contact = 0
        for p in paths:
            rgb = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
            lbl = Path(str(p).replace("/images/", "/labels/")).with_suffix(".txt")
            gts = parse_yolo_seg_labels(lbl.read_text() if lbl.exists() else "", rgb.shape[1], rgb.shape[0])
            o = run_analysis(rgb, cfg, seg)
            shape = rgb.shape[:2]
            preds = [(w.wire_class, _raster(w.polygons, shape), w.confidence) for w in o.wires]
            preds += [(VEGETATION, _raster(r.polygons, shape), r.confidence) for r in o.vegetation]
            g = [(x.wire_class, x.mask) for x in gts]
            strict.append(([ev.Instance(c, m, s) for c, m, s in preds], [ev.Instance(c, m) for c, m in g]))
            tol.append(([ev.Instance(c, widen(m), s) for c, m, s in preds], [ev.Instance(c, widen(m)) for c, m in g]))
            verdicts[(o.hazard or {}).get("verdict")] = verdicts.get((o.hazard or {}).get("verdict"), 0) + 1
            n_wires += len(o.wires)
            n_scored += sum(w.status == "scored" for w in o.wires)
            n_uncertain += sum("class_uncertain" in w.flags for w in o.wires)
            n_veg += len(o.vegetation)
            n_contact += sum(bool(r.touches) for r in o.vegetation)
            for gc, gm in g:
                if gc == VEGETATION:
                    continue
                n_gt += 1
                gw = widen(gm)
                ov = [(c, int((m & gw).sum())) for c, m, _ in preds if c != VEGETATION]
                ov = [(c, x) for c, x in ov if x >= 20]
                if not ov:
                    continue
                n_cov += 1
                frags.append(len(ov))
                tot = sum(x for _, x in ov)
                per: dict[str, int] = {}
                for c, x in ov:
                    per[c] = per.get(c, 0) + x
                n_mixed += sum(1 for x in per.values() if x >= 0.2 * tot) >= 2
        conf = cfg.segmentation.conf_min
        rs, rt = ev.evaluate_segmentation(strict, conf), ev.evaluate_segmentation(tol, conf)

        def pack(r):
            return {"map50": r.map50, "per_class": {c: {"ap50": x.ap50, "f1": x.f1, "precision": x.precision,
                                                        "recall": x.recall, "n_pred": x.n_pred, "n_gt": x.n_gt}
                                                    for c, x in r.per_class.items()}}
        out[v] = {"images": len(paths), "strict": pack(rs), "tolerant": pack(rt),
                  "mixed_rate": round(n_mixed / max(n_cov, 1), 4), "fragments": round(float(np.mean(frags)), 3),
                  "labelled_wires": n_gt, "covered": n_cov, "wires": n_wires, "scored": n_scored,
                  "class_uncertain": n_uncertain, "vegetation_regions": n_veg, "vegetation_contacts": n_contact,
                  "verdicts": verdicts}
        t = out[v]["tolerant"]["per_class"]
        f = lambda x: "-" if x is None else f"{x:.3f}"  # noqa: E731
        print(f"{v}: strict mAP50 {rs.map50:.4f} tol mAP50 {rt.map50:.4f} | " + " | ".join(
            f"{c[:8]} F1 {f(t[c]['f1'])} P {f(t[c]['precision'])} R {f(t[c]['recall'])}" for c in CLASSES)
              + f" | mixed {out[v]['mixed_rate']} frag {out[v]['fragments']} veg {n_veg} contacts {n_contact} "
              f"verdicts {verdicts}", flush=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val", choices=["val", "test"])
    ap.add_argument("--variants", default="v215,v216")
    a = ap.parse_args()
    res = run(a.split, a.variants.split(","))
    Path(f"docs/thesis/results/v216_pipeline_{a.split}.json").write_text(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
