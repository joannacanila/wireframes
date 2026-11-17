"""v2.17 benchmark of the deployed pipeline: legacy mask mAP@50 (unchanged definition) + centerline P / R / F1,
tolerant IoU / Dice, strand-level classification, fragmentation, mixed-class rate, guardrail inputs.

Two stages so that scoring and guardrail calibration never re-run the models:

    python -m ml.experiments.v217_eval dump  --split val  --variant v216     # run_analysis -> runs/v217/dump/<variant>-<split>.json
    python -m ml.experiments.v217_eval score --split val  --variant v216     # -> docs/thesis/results/v217_eval_<variant>_<split>.json
    python -m ml.experiments.v217_eval dump  --split val  --condition lowlight   # one of ml.stress.CONDITIONS

The test split is dumped and scored once, after every setting is frozen on validation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import yaml

from app.core.config import ROOT_DIR, VEGETATION
from app.pipeline import centerline_eval as ce
from app.pipeline import evaluation as ev
from app.pipeline.segment import parse_yolo_seg_labels

DUMP = Path("runs/v217/dump")
VARIANTS: dict[str, dict] = {
    "v216": {"segmentation": {"wirenet_weights": "models/wirenet-r34.pt", "span_wire_tau": None, "strand_head": None,
                              "span_class_min_share": 0.9}},
    "v217": {},  # config/wireframe.yaml as shipped in v2.17
}


def _paths(split: str) -> list[Path]:
    spec = yaml.safe_load(Path("datasets/wireframe_seg/data.yaml").read_text())
    return sorted((Path("datasets/wireframe_seg") / spec[split]).glob("*.jpg"))


def _raster(polys, shape) -> np.ndarray:
    m = np.zeros(shape, np.uint8)
    cv2.fillPoly(m, [np.asarray(p, np.int32) for p in polys if len(p) >= 3], 1)
    return m.astype(bool)


def dump(split: str, variant: str, condition: str = "clean") -> Path:
    from app.pipeline.runner import run_analysis
    from app.pipeline.wirenet import make_segmenter
    from ml.experiments.v216_regression import _cfg
    cfg = _cfg(VARIANTS[variant])
    seg = make_segmenter(cfg.segmentation, ROOT_DIR)
    assert seg.load(), seg.error
    from ml.stress import apply_condition
    rows = []
    for p in _paths(split):
        rgb = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
        if condition != "clean":
            rgb = apply_condition(rgb, condition, p.name)
        o = run_analysis(rgb, cfg, seg)
        rows.append({
            "stem": p.stem, "shape": list(rgb.shape[:2]),
            "wires": [{"cls": w.wire_class, "confident": "class_uncertain" not in w.flags, "conf": w.confidence,
                       "polygons": w.polygons, "status": w.status, "flags": w.flags, "crs": w.crs,
                       "sag": (w.fit or {}).get("sag_ratio"), "share": (w.fit or {}).get("class_evidence")}
                      for w in o.wires],
            "vegetation": [{"conf": r.confidence, "polygons": r.polygons, "touches": r.touches} for r in o.vegetation],
            "suppressed": [{k: s.get(k) for k in ("reason_code", "wire_class")} for s in o.suppressed],
            "quality": {k: (o.quality or {}).get(k) for k in ("grade", "reliability")},
            "verdict": (o.hazard or {}).get("verdict"), "flags": o.flags,
            "poles": len((o.scene or {}).get("poles_detected") or []),
            "pole_boxes": [d["bbox"] for d in (o.scene or {}).get("poles_detected") or []],
            "suppressed_polygons": [poly for s in o.suppressed for poly in (s.get("polygons") or [])],
            "guardrail": o.guardrail,
            "timings_ms": o.timings_ms,
        })
        print(p.stem, len(o.wires), rows[-1]["verdict"], flush=True)
    DUMP.mkdir(parents=True, exist_ok=True)
    out = DUMP / f"{variant}-{split}{'' if condition == 'clean' else '-' + condition}.json"
    out.write_text(json.dumps(rows))
    return out


def load_gts(split: str) -> dict[str, list[tuple[str, np.ndarray]]]:
    out = {}
    for p in _paths(split):
        im = cv2.imread(str(p))
        h, w = im.shape[:2]
        lbl = Path(str(p).replace("/images/", "/labels/")).with_suffix(".txt")
        out[p.stem] = [(g.wire_class, g.mask) for g in
                       parse_yolo_seg_labels(lbl.read_text() if lbl.exists() else "", w, h)]
    return out


def preds_of(row) -> list[ce.Pred]:
    shape = tuple(row["shape"])
    return [ce.Pred(w["cls"], _raster(w["polygons"], shape), w["confident"]) for w in row["wires"]]


def score_rows(rows: list[dict], gts: dict, n_boot: int = 2000) -> dict:
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    widen = lambda m: cv2.dilate(m.astype(np.uint8), k).astype(bool)  # noqa: E731
    per_image, tally, strict, tol, img_rows = [], ce.StrandTally(), [], [], []
    n_gt = n_cov = n_mixed = 0
    frags = []
    for row in rows:
        shape = tuple(row["shape"])
        g = gts[row["stem"]]
        preds = preds_of(row)
        c = ce.image_counts(preds, g, shape)
        per_image.append(c)
        tally.add(preds, [(x, m) for x, m in g if x != VEGETATION], shape)
        legacy = [(p.wire_class, p.mask, w["conf"]) for p, w in zip(preds, row["wires"], strict=True)]
        legacy += [(VEGETATION, _raster(r["polygons"], shape), r["conf"]) for r in row["vegetation"]]
        strict.append(([ev.Instance(a, m, s) for a, m, s in legacy], [ev.Instance(a, m) for a, m in g]))
        tol.append(([ev.Instance(a, widen(m), s) for a, m, s in legacy], [ev.Instance(a, widen(m)) for a, m in g]))
        w = ce.prf(c["wire"])
        img_rows.append({"stem": row["stem"], "wire_precision": w["precision"], "wire_recall": w["recall"],
                         "wire_f1": w["f1"]})
        for gc, gm in g:  # v2.16 fragmentation / mixed-class definitions, unchanged
            if gc == VEGETATION:
                continue
            n_gt += 1
            gw = widen(gm)
            ov = [(p.wire_class, int((p.mask & gw).sum())) for p in preds]
            ov = [(a, x) for a, x in ov if x >= 20]
            if not ov:
                continue
            n_cov += 1
            frags.append(len(ov))
            tot = sum(x for _, x in ov)
            per: dict[str, int] = {}
            for a, x in ov:
                per[a] = per.get(a, 0) + x
            n_mixed += sum(1 for x in per.values() if x >= 0.2 * tot) >= 2
    rs, rt = ev.evaluate_segmentation(strict, 0.0), ev.evaluate_segmentation(tol, 0.0)
    return {"images": len(rows), "centerline": ce.summarize(per_image, n_boot), "strand_class": tally.report(),
            "mask_map50": {"strict": rs.map50, "tolerant": rt.map50},
            "fragments": float(np.mean(frags)) if frags else None, "mixed_rate": n_mixed / max(n_cov, 1),
            "labelled_wires": n_gt, "covered": n_cov, "per_image": img_rows,
            "verdicts": {v: sum(r["verdict"] == v for r in rows) for v in {r["verdict"] for r in rows}}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["dump", "score"])
    ap.add_argument("--split", default="val", choices=["val", "test"])
    ap.add_argument("--variant", default="v216")
    ap.add_argument("--condition", default="clean")
    a = ap.parse_args()
    if a.stage == "dump":
        print(dump(a.split, a.variant, a.condition))
        return
    suffix = "" if a.condition == "clean" else "-" + a.condition
    rows = json.loads((DUMP / f"{a.variant}-{a.split}{suffix}.json").read_text())
    r = score_rows(rows, load_gts(a.split))
    out = Path(f"docs/thesis/results/v217_eval_{a.variant}_{a.split}{suffix}.json")
    out.write_text(json.dumps(r, indent=1, default=str))
    f = lambda x: "-" if x is None else f"{x:.3f}"  # noqa: E731
    for t, m in r["centerline"].items():
        if isinstance(m, dict):
            print(f"{t:26s} P {f(m['precision'])} {m['precision_ci95']}  R {f(m['recall'])}  F1 {f(m['f1'])}  "
                  f"IoU {f(m['iou'])} Dice {f(m['dice'])}")
    s = r["strand_class"]
    print("strand class: acc", f(s["accuracy"]), "uncertain", f(s["uncertain_rate"]), "matched", s["matched"],
          "unmatched", s["unmatched"])
    for c in ce.WIRE_CLASSES:
        print("  ", c, {k: (f(v) if isinstance(v, float) else v) for k, v in s[c].items()})
    print("mask mAP50", r["mask_map50"], "fragments", f(r["fragments"]), "mixed", f(r["mixed_rate"]))


if __name__ == "__main__":
    main()
