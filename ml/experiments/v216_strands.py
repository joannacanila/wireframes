"""v2.16 strand-level classification experiment (validation split, cached WireNet probabilities).

Defect under test: the shipped extractor builds spans separately per wire class from WireNet's per-pixel argmax, so
one physical cable whose pixels flip between power and telecom becomes two spans of different classes (two colours
on one wire) and each half has a gap where the other class won (fragmentation).

Variants
    baseline   per-class masks from argmax (shipped v2.15)
    strand     class-agnostic geometry: wire mask = argmax is any wire class, spans built once on that mask with
               P(wire) = sum of wire-class probabilities; each span then takes the class with the largest summed
               probability over its observed pixels (one class per strand)
    strand-tau as strand, but a pixel is wire when P(wire) >= tau (recovers pixels where the wire probability is
               split between two wire classes and background wins the argmax)

Metrics (besides span mAP50 / F1 from ml.experiments.harness), computed against the human span polygons:
    mixed_rate      share of labelled wires covered by predicted spans of >= 2 classes (each >= 20% of the overlap)
    fragments       mean number of predicted spans covering one labelled wire (overlap >= 20 px), over covered wires
    class_acc       overlap-weighted share of covered labelled-wire pixels whose span has the labelled class
    merge_rate      share of predicted spans whose pixels fall >= 30% on each of two different labelled wires
    px_p / px_r     class-agnostic wire-pixel precision / recall with a 2 px tolerance

    python -m ml.experiments.v216_strands --cache runs/cache/val-v216 --out docs/thesis/results/v216_strands_val.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from app.core.config import CLASSES, ROOT_DIR, VEGETATION, load_analysis_config
from app.pipeline.wire_instances import extract_spans, strand_spans
from app.pipeline.wirenet import span_params
from ml.experiments import harness

WIRE_IDS = [CLASSES.index(c) + 1 for c in CLASSES if c != VEGETATION]


def baseline(params):
    def f(probs, cls_map):
        out = []
        for c in WIRE_IDS:
            m = cls_map == c
            if m.any():
                out += [(CLASSES[c - 1], s, conf) for s, conf in extract_spans(m, probs[c], **params)]
        return out
    return f


def strand(params, tau=None, min_share=0.0):
    def f(probs, cls_map):
        return [(s.wire_class, s.mask, s.confidence) for s in strand_spans(probs, WIRE_IDS, params, tau=tau,
                                                                           min_class_share=min_share)]
    return f


def consistency(cache: Path, extractor, tol_px: int = 2) -> dict:
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * tol_px + 1, 2 * tol_px + 1))
    widen = lambda m: cv2.dilate(m.astype(np.uint8), k).astype(bool)  # noqa: E731
    n_gt = n_cov = n_mixed = 0
    frags, acc_num, acc_den = [], 0.0, 0.0
    n_spans = n_merged = 0
    tp_px = fp_px = fn_px = 0
    for _, z in harness.load(cache):
        probs = z["probs"].astype(np.float32)
        spans = extractor(probs, probs.argmax(0))
        gts = [(c, m) for c, m in zip(z["gt_classes"].tolist(), z["gt_masks"], strict=True) if c != VEGETATION]
        gw = [widen(m) for _, m in gts]
        for (gc, _), g in zip(gts, gw, strict=True):
            n_gt += 1
            ov = [(c, int((m & g).sum())) for c, m, _ in spans]
            ov = [(c, o) for c, o in ov if o >= 20]
            if not ov:
                continue
            n_cov += 1
            frags.append(len(ov))
            tot = sum(o for _, o in ov)
            per = {}
            for c, o in ov:
                per[c] = per.get(c, 0) + o
            if sum(1 for v in per.values() if v >= 0.2 * tot) >= 2:
                n_mixed += 1
            acc_num += per.get(gc, 0)
            acc_den += tot
        for _, m, _ in spans:
            n_spans += 1
            a = m.sum()
            shares = sorted((float((m & g).sum()) / max(a, 1) for g in (gm for _, gm in gts)), reverse=True)
            if len(shares) >= 2 and shares[1] >= 0.3:
                n_merged += 1
        pred = np.zeros(probs.shape[1:], bool)
        for _, m, _ in spans:
            pred |= m
        gt = np.zeros_like(pred)
        for _, m in gts:
            gt |= m
        tp_px += int((pred & widen(gt)).sum())
        fp_px += int((pred & ~widen(gt)).sum())
        fn_px += int((gt & ~widen(pred)).sum())
    return {"gt_wires": n_gt, "covered": n_cov, "mixed_rate": round(n_mixed / max(n_cov, 1), 4),
            "fragments": round(float(np.mean(frags)) if frags else 0.0, 3),
            "class_acc": round(acc_num / max(acc_den, 1), 4), "spans": n_spans,
            "merge_rate": round(n_merged / max(n_spans, 1), 4),
            "px_p": round(tp_px / max(tp_px + fp_px, 1), 4), "px_r": round(1 - fn_px / max(tp_px + fn_px, 1), 4)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path("runs/cache/val-v216"))
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--only", default=None)
    a = ap.parse_args()
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml").segmentation
    params = span_params(cfg)
    variants = {"baseline": baseline(params), "strand": strand(params)}
    for tau in (0.45, 0.40, 0.35, 0.30):
        variants[f"strand-tau{tau:.2f}"] = strand(params, tau=tau)
    rows = {}
    for name, fn in variants.items():
        if a.only and name not in a.only.split(","):
            continue
        r = harness.score(a.cache, fn, conf=cfg.conf_min)
        c = consistency(a.cache, fn)
        rows[name] = {"score": r, "consistency": c}
        print(f"{name:16s} {harness.summary(r)}")
        print(f"{'':16s} {c}", flush=True)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
