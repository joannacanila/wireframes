"""v2.13: span filters on unlabelled street photos (the live-test problem photo, the night pole, demo samples).

    python -m ml.experiments.v213_unlabelled --images runs/v213_inputs/*.jpg runs/demo_samples/* \
        --out docs/thesis/results/v213_unlabelled.json --figures docs/thesis/figures

Shipped preprocessing + WireNet (flip TTA) + the shipped span extractor, in the 1024 letterbox; per span the
v2.13 features (ml/experiments/v213_filters.py) and which rule would suppress it. A figure per photo shows kept
spans (green) and suppressed spans (red, labelled with the rule). No labels: this is a visual check.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from app.core.config import ROOT_DIR, load_analysis_config
from ml.experiments.ablation_final import FINAL
from ml.experiments.sweep_spans import make
from ml.experiments.v213_filters import features

RULES = {"ground": lambda r: r["ground_share"] >= 0.5, "loop": lambda r: r["loop_holes"] >= 1,
         "isolated_below": lambda r: r["isolation_px"] >= 40 and r["below_net_px"] >= 40}


def main() -> None:
    from app.pipeline.preprocess import preprocess
    from app.pipeline.scene import SceneParser
    from app.pipeline.wirenet import make_segmenter, predict_probs_tta
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", nargs="+", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--figures", type=Path)
    a = ap.parse_args()
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    seg = make_segmenter(cfg.segmentation, ROOT_DIR)
    assert seg.load(), seg.error
    sp = SceneParser()
    assert sp.load(), sp.error
    ex, res = make(FINAL), {}
    for p in a.images:
        rgb = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
        pre = preprocess(rgb, cfg.preprocessing, cfg.enhancement, cfg.segmentation.imgsz)
        x = np.clip(pre.x_rgb * 255 + 0.5, 0, 255).astype(np.uint8)
        probs = predict_probs_tta(seg._net, x, seg._device, cfg.segmentation.wirenet_tta)
        spans = ex(probs, probs.argmax(0))
        ground = sp.ground_mask(sp.labels(x))
        feats = features(spans, [], ground)
        hits = [[n for n, f in RULES.items() if f(r)] for r in feats]
        res[p.name] = {"spans": len(spans), "suppressed": {n: sum(n in h for h in hits) for n in RULES},
                       "rows": [{**r, "rules": h} for r, h in zip(feats, hits, strict=True)]}
        print(p.name, len(spans), "spans;", res[p.name]["suppressed"], flush=True)
        if a.figures:
            vis = cv2.cvtColor(x, cv2.COLOR_RGB2BGR)
            ov = vis.copy()
            ov[ground] = (0.6 * ov[ground] + 0.4 * np.array([120, 60, 160])).astype(np.uint8)
            vis = cv2.addWeighted(vis, 0.6, ov, 0.4, 0)
            for (_, m, _), h in zip(spans, hits, strict=True):
                vis[m] = (60, 60, 235) if h else (60, 200, 60)
                if h:
                    ys, xs = np.nonzero(m)
                    cv2.putText(vis, "/".join(h), (int(xs.min()), max(12, int(ys.min()) - 4)), cv2.FONT_HERSHEY_SIMPLEX,
                                0.5, (60, 60, 235), 1, cv2.LINE_AA)
            a.figures.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(a.figures / f"v213_filters_{p.stem}.jpg"), vis, [cv2.IMWRITE_JPEG_QUALITY, 85])
    a.out.write_text(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
