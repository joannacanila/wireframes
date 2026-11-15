"""Does perspective (depth recession along a span) drive high sag readings? (v2.12, validation, CPU)

    python -m ml.experiments.depth_sag --out docs/thesis/results/v212_depth_sag_val.json

Spans are extracted from the cached v2.8 validation probabilities with the shipped constants and scored
with Chapter III's CRS (CPU). Depth Anything V2 Small gives a relative inverse depth per photo; for each
scored span, recession = |depth(end A) - depth(end B)| (0-1 scale). Reported: Spearman correlation of
recession with sag ratio, and the high-band share by recession tercile. There is no ground-truth sag, so
nothing here can calibrate a correction; it only tests whether perspective is a plausible confound.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.stats import spearmanr

from app.core.config import ROOT_DIR, load_analysis_config


def main() -> None:
    from app.pipeline.runner import EngineeringContext, score_wire
    from app.pipeline.segment import WireDetection, _bbox
    from ml.experiments.ablation_final import FINAL
    from ml.experiments.depth_veg_guard import depth_model, relative_depth
    from ml.experiments.sweep_spans import make
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path("runs/cache/val-v28"))
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    pipe, ex, rows = depth_model(), make(FINAL), []
    for f in sorted(a.cache.glob("*.npz")):
        z = np.load(f)
        probs = z["probs"].astype(np.float32)
        rgb = cv2.cvtColor(cv2.imread(f"datasets/wireframe_seg/images/val/{f.stem}.jpg"), cv2.COLOR_BGR2RGB)
        depth = relative_depth(pipe, rgb)
        h, w = rgb.shape[:2]
        for i, (cls, m, s) in enumerate(ex(probs, probs.argmax(0))):
            if s < cfg.segmentation.conf_min:
                continue
            wo = score_wire(WireDetection(cls, s, m, _bbox(m)), i + 1, (w, h), cfg, EngineeringContext())
            if wo.crs is None or not wo.fit.get("endpoints"):
                continue
            (x0, y0), (x1, y1) = wo.fit["endpoints"]
            d0 = depth[int(np.clip(round(y0), 0, h - 1)), int(np.clip(round(x0), 0, w - 1))]
            d1 = depth[int(np.clip(round(y1), 0, h - 1)), int(np.clip(round(x1), 0, w - 1))]
            rows.append({"image": f.stem, "class": cls, "sag_ratio": wo.fit["sag_ratio"], "level": wo.level,
                         "chord_px": wo.fit["chord_px"], "recession": float(abs(d0 - d1))})
        print(f.stem[-10:], len(rows), flush=True)
    rec = np.array([r["recession"] for r in rows])
    sag = np.array([r["sag_ratio"] for r in rows])
    rho, p = spearmanr(rec, sag)
    terc = np.quantile(rec, [1 / 3, 2 / 3])
    bands = []
    for lo, hi, name in ((-1, terc[0], "low recession"), (terc[0], terc[1], "middle"), (terc[1], 2, "high recession")):
        sel = [r for r in rows if lo < r["recession"] <= hi]
        bands.append({"tercile": name, "spans": len(sel), "high_share": sum(r["level"] == "high" for r in sel) / max(len(sel), 1),
                      "median_sag_ratio": float(np.median([r["sag_ratio"] for r in sel])) if sel else None})
    out = {"spans": len(rows), "spearman_recession_vs_sag": {"rho": float(rho), "p": float(p)}, "terciles": bands}
    print(json.dumps(out, indent=1))
    a.out.write_text(json.dumps({**out, "rows": rows}, indent=1, default=float))


if __name__ == "__main__":
    main()
