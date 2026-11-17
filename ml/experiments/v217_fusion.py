"""v2.17: centerline metrics of the v2.12 WireNet members and their probability fusions (validation caches only).

The v2.12 fusion (0.25 R34 + 0.5 MiT-B1 + 0.25 ConvNeXt) was chosen on mask IoU and left opt-in; this re-asks
the v2.17 question (centerline precision / recall / F1 per class, strand classification) for each member and a
small fixed set of fusions. Every candidate is scored on the 22 validation photos; the test split is not read.

    python -m ml.experiments.v217_fusion
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from app.core.config import CLASSES, ROOT_DIR, load_analysis_config
from app.pipeline import centerline_eval as ce
from app.pipeline.wire_instances import strand_spans
from app.pipeline.wirenet import span_params
from ml.experiments.v216_strands import WIRE_IDS

CACHES = {"r34": "runs/cache/val-v216", "mitb1": "runs/cache/val-mitb1", "cnx": "runs/cache/val-cnx"}
FUSIONS = {
    "r34": {"r34": 1}, "mitb1": {"mitb1": 1}, "cnx": {"cnx": 1},
    "v212": {"r34": 0.25, "mitb1": 0.5, "cnx": 0.25},
    "equal": {"r34": 1 / 3, "mitb1": 1 / 3, "cnx": 1 / 3},
    "r34+mitb1": {"r34": 0.5, "mitb1": 0.5},
    "r34+cnx": {"r34": 0.5, "cnx": 0.5},
    "mitb1+cnx": {"mitb1": 0.5, "cnx": 0.5},
}


def main(min_share: float = 0.6) -> None:
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml").segmentation
    params = span_params(cfg)
    stems = sorted(p.stem for p in Path(CACHES["r34"]).glob("*.npz"))
    per = {k: {"pixel": [], "strands": []} for k in FUSIONS}
    tallies = {k: ce.StrandTally() for k in FUSIONS}
    for stem in stems:
        zs = {m: np.load(f"{d}/{stem}.npz") for m, d in CACHES.items()}
        probs = {m: z["probs"].astype(np.float32) for m, z in zs.items()}
        z = zs["r34"]
        gts = [(c, m) for c, m in zip(z["gt_classes"].tolist(), z["gt_masks"], strict=True) if c in ce.WIRE_CLASSES]
        for k, wts in FUSIONS.items():
            p = sum(w * probs[m] for m, w in wts.items())
            shape = p.shape[1:]
            am = p.argmax(0)
            per[k]["pixel"].append(ce.image_counts([ce.Pred(CLASSES[i - 1], am == i) for i in WIRE_IDS], gts, shape))
            preds = [ce.Pred(s.wire_class, s.mask, s.class_status != "uncertain")
                     for s in strand_spans(p, WIRE_IDS, params, min_class_share=min_share)]
            tallies[k].add(preds, gts, shape)
            per[k]["strands"].append(ce.image_counts(preds, gts, shape))
        print(stem[:40], flush=True)
    out = {"min_share": min_share, "images": len(stems), "fusions": FUSIONS, "results": {}}
    f = lambda m: f"P {m['precision']:.3f} R {m['recall']:.3f} F1 {m['f1']:.3f}"  # noqa: E731
    for k in FUSIONS:
        px, st = ce.summarize(per[k]["pixel"], 500), ce.summarize(per[k]["strands"], 500)
        out["results"][k] = {"pixel": px, "strands": st, "strand_class": tallies[k].report()}
        print(f"{k:10s} pixel tel {f(px['telecom-cable'])} pow {f(px['power-distribution-lines'])} | strands wire "
              f"{f(st['wire'])} pow {f(st['power-distribution-lines'])} tel {f(st['telecom-cable'])} | "
              f"acc {tallies[k].report()['accuracy']:.3f}", flush=True)
    Path("docs/thesis/results/v217_fusion_val.json").write_text(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
