"""v2.20 telecom class-prior sweep on cached validation probabilities (no model loaded; the test split is never read).

The telecom class is under-recalled (v2.17 validation centerline recall 0.749, test 0.706). A per-class prior
multiplies the telecom probability by s before the strand extractor (equivalent to a logit bias of log s), the
standard post-hoc correction for a minority class. s is chosen on validation only, by the mean of power and telecom
strand centerline F1 with power F1 not allowed to fall; s = 1 reproduces the shipped pipeline.

    python -m ml.experiments.v220_telecom_prior --cache runs/cache/_fused
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
from ml.experiments import harness
from ml.experiments.v216_strands import WIRE_IDS

TEL = CLASSES.index("telecom-cable") + 1


def run(items, s: float, params: dict, min_share: float) -> dict:
    per = []
    for probs, gts in items:
        p = probs.copy()
        p[TEL] *= s
        p /= p.sum(0, keepdims=True)
        preds = [ce.Pred(x.wire_class, x.mask, x.class_status != "uncertain")
                 for x in strand_spans(p, WIRE_IDS, params, min_class_share=min_share)]
        per.append(ce.image_counts(preds, gts, p.shape[1:]))
    return ce.summarize(per, 0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path("runs/cache/_fused"))
    ap.add_argument("--scales", default="1,1.25,1.5,2,2.5,3")
    ap.add_argument("--out", type=Path, default=Path("docs/thesis/results/v220_telecom_prior_val.json"))
    a = ap.parse_args()
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml").segmentation
    items = []
    for _, z in harness.load(a.cache):
        gts = [(c, m) for c, m in zip(z["gt_classes"].tolist(), z["gt_masks"], strict=True) if c in ce.WIRE_CLASSES]
        items.append((z["probs"].astype(np.float32), gts))
    out = {"cache": str(a.cache), "images": len(items), "rows": []}
    for s in [float(x) for x in a.scales.split(",")]:
        r = run(items, s, span_params(cfg), cfg.span_class_min_share)
        row = {"scale": s, **{k: {m: r[k][m] for m in ("precision", "recall", "f1")} for k in
                              ("wire", "power-distribution-lines", "telecom-cable")}}
        out["rows"].append(row)
        print(s, {k: round(v["f1"], 4) for k, v in row.items() if isinstance(v, dict)}, flush=True)
    a.out.write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
