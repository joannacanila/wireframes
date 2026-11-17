"""v2.17: what does strand assembly cost each wire class? (validation caches, no models loaded)

Labelled centerline of class C that the *pixel* output finds as C but the confident strands of class C do not:
    strand_other   a confident strand of another class covers it (class swap at strand level)
    strand_unc     an uncertain strand covers it
    no_strand      no strand covers it (dropped by the min_px / min_len / stitched-length gates)
and confident strand-C centerline that is not near a C label:
    near_other     near a label of another wire class (swap)
    unlabelled     near no wire label (false positive or a wire missing from the labels)

    python -m ml.experiments.v217_strand_loss --cache runs/cache/val-v216
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from app.core.config import CLASSES, ROOT_DIR, load_analysis_config
from app.pipeline import centerline_eval as ce
from app.pipeline.wire_instances import strand_spans
from app.pipeline.wirenet import span_params
from ml.experiments.v216_strands import WIRE_IDS

MAIN = ("power-distribution-lines", "telecom-cable")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path("runs/cache/val-v216"))
    ap.add_argument("--min-share", type=float, default=0.6)
    a = ap.parse_args()
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml").segmentation
    params = span_params(cfg)
    rec = {c: dict.fromkeys(("pixel_hit", "strand_hit", "strand_other", "strand_unc", "no_strand"), 0) for c in MAIN}
    prec = {c: dict.fromkeys(("hit", "near_other", "unlabelled"), 0) for c in MAIN}
    lengths = {c: [] for c in MAIN}  # skeleton length of strands per class: hit vs miss
    for f in sorted(a.cache.glob("*.npz")):
        z = np.load(f)
        p = z["probs"].astype(np.float32)
        shape, tol = p.shape[1:], ce.tolerance(p.shape[1:])
        gts = [(c, m) for c, m in zip(z["gt_classes"].tolist(), z["gt_masks"], strict=True) if c in ce.WIRE_CLASSES]
        am = p.argmax(0)
        strands = strand_spans(p, WIRE_IDS, params, min_class_share=a.min_share)
        z0 = np.zeros(shape, bool)
        conf = {c: z0.copy() for c in CLASSES}
        unc = z0.copy()
        for s in strands:
            if s.class_status == "uncertain":
                unc |= s.mask
            else:
                conf[s.wire_class] |= s.mask
        gt = {c: np.any([m for k, m in gts if k == c], axis=0) if any(k == c for k, _ in gts) else z0 for c in CLASSES}
        for c in MAIN:
            i = CLASSES.index(c) + 1
            sk = ce._skel(gt[c])
            pix = sk & ce._near(am == i, tol)
            on = sk & ce._near(conf[c], tol)
            other = z0.copy()
            for k in CLASSES:
                if k != c and k != "Vegetation":
                    other |= conf[k]
            lost = pix & ~on
            r = rec[c]
            r["pixel_hit"] += int(pix.sum())
            r["strand_hit"] += int(on.sum())
            r["strand_other"] += int((lost & ce._near(other, tol)).sum())
            r["strand_unc"] += int((lost & ~ce._near(other, tol) & ce._near(unc, tol)).sum())
            r["no_strand"] += int((lost & ~ce._near(other, tol) & ~ce._near(unc, tol)).sum())
            sp = ce._skel(conf[c])
            ng = ce._near(gt[c], tol)
            other_gt = z0.copy()
            for k in ("power-distribution-lines", "telecom-cable", "abandoned-wire"):
                if k != c:
                    other_gt |= gt[k]
            no = ce._near(other_gt, tol)
            prec[c]["hit"] += int((sp & ng).sum())
            prec[c]["near_other"] += int((sp & ~ng & no).sum())
            prec[c]["unlabelled"] += int((sp & ~ng & ~no).sum())
            for s in strands:
                if s.wire_class == c and s.class_status != "uncertain":
                    ss = ce._skel(s.mask)
                    n = int(ss.sum())
                    if n:
                        lengths[c].append((n, float((ss & ng).sum() / n)))
    out = {"recall_loss": rec, "precision_loss": prec}
    for c in MAIN:
        L = np.array(lengths[c])
        bins = [0, 150, 300, 600, 10_000]
        out.setdefault("strand_precision_by_length", {})[c] = [
            {"len": f"{lo}-{hi}", "n": int(((L[:, 0] >= lo) & (L[:, 0] < hi)).sum()),
             "px_share": float(L[(L[:, 0] >= lo) & (L[:, 0] < hi), 0].sum() / L[:, 0].sum()),
             "on_label": float(np.average(L[(L[:, 0] >= lo) & (L[:, 0] < hi), 1], weights=L[(L[:, 0] >= lo) & (L[:, 0] < hi), 0]))
             if ((L[:, 0] >= lo) & (L[:, 0] < hi)).any() else None} for lo, hi in zip(bins, bins[1:], strict=False)]
    print(json.dumps(out, indent=1))
    Path("docs/thesis/results/v217_strand_loss_val.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
