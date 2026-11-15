"""Per-photo risk outcome distribution: v2.7 behaviour vs v2.8 (the "always high" diagnosis).

    python -m ml.experiments.verdict_dist --splits val test --out docs/thesis/results/v28_verdicts.json

v2.7 behaviour = no gap bridging, no stitched-length gate, no chord precision gate (the v2.7
config values); v2.8 = v2.8's rules (no motion route, no service-drop split, any-span sag rule);
v2.9 = the shipped config. Recorded per photo: worst CRS level (Chapter III,
Table 1.2), the hazard verdict, scored / too-short span counts and the chord lengths that
produced the worst score. The distribution is an outcome, not something thresholds were tuned
to reach.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import cv2
import yaml

from app.core.config import ROOT_DIR, load_analysis_config


def v27_like(cfg):
    seg = cfg.segmentation.model_copy(update={"span_max_gap": 0.0, "span_min_stitched_len": 0, "span_min_len": 30,
                                              "wirenet_tta": "none"})
    enh = cfg.enhancement.model_copy(update={"edge_denoise": False, "dark_l_max": 35.0})
    return cfg.model_copy(update={"segmentation": seg, "enhancement": enh,
                                  "crs": cfg.crs.model_copy(update={"min_chord_px": 0.0})})


def v28_like(cfg):
    return cfg.model_copy(update={
        "enhancement": cfg.enhancement.model_copy(update={"motion_route": False, "deblock": False}),
        "crs": cfg.crs.model_copy(update={"max_span_inclination_deg": 0.0}),
        "hazard": cfg.hazard.model_copy(update={"sag_rule": "any", "low_frame_frac": 0.0})})


def main() -> None:
    from app.pipeline.runner import run_analysis
    from app.pipeline.wirenet import make_segmenter
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", nargs="*", default=["val", "test"])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--variants", nargs="*", default=["v2.8", "v2.9"])
    a = ap.parse_args()
    base = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    data = Path("datasets/wireframe_seg/data.yaml")
    spec = yaml.safe_load(data.read_text())
    out = {}
    all_variants = {"v2.7 behaviour": lambda: v27_like(v28_like(base)), "v2.8": lambda: v28_like(base), "v2.9": lambda: base}
    for name in a.variants:
        cfg = all_variants[name]()
        seg = make_segmenter(cfg.segmentation, ROOT_DIR)
        assert seg.load(), seg.error
        for split in a.splits:
            rows = []
            for p in sorted((data.parent / spec[split]).glob("*.jpg")):
                rgb = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
                o = run_analysis(rgb, cfg, seg)
                scored = [w for w in o.wires if w.crs is not None]
                worst = max(scored, key=lambda w: w.crs, default=None)
                rows.append({"image": p.stem, "status": o.status, "worst_level": worst.level if worst else None,
                             "worst_crs": worst.crs if worst else None,
                             "worst_chord_px": round(worst.fit["chord_px"], 1) if worst else None,
                             "verdict": (o.hazard or {}).get("verdict"), "spans": len(o.wires), "scored": len(scored),
                             "too_short": sum(w.status == "too_short" for w in o.wires),
                             "service_drops": sum(w.status == "service_drop" for w in o.wires),
                             "levels": dict(Counter(w.level for w in scored)),
                             "reasons": (o.hazard or {}).get("reasons"), "grade": (o.quality or {}).get("grade")})
            out[f"{name} | {split}"] = {"photos": rows,
                                        "worst_level": dict(Counter(str(r["worst_level"]) for r in rows)),
                                        "verdict": dict(Counter(str(r["verdict"]) for r in rows)),
                                        "span_levels": dict(sum((Counter(r["levels"]) for r in rows), Counter())),
                                        "grade": dict(Counter(str(r["grade"]) for r in rows))}
            s = out[f"{name} | {split}"]
            print(f"{name} | {split}: worst level {s['worst_level']} | verdict {s['verdict']} | span levels {s['span_levels']} | grades {s['grade']}", flush=True)
            a.out.write_text(json.dumps(out, indent=1, default=float))


if __name__ == "__main__":
    main()
