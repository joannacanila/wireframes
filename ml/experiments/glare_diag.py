"""Glare failure-mode diagnosis (v2.10, validation only).

    python -m ml.experiments.glare_diag --out docs/thesis/results/v210_glare_diag_val.jsonl

Every validation photo under every stress condition goes through the shipped pipeline. Recorded
per (photo, condition): the photo measurements the pipeline already makes (mean L*, contrast,
clipped share, sharpness), candidate glare features, WireNet's uncertainty and coverage, the
reliability R and grade, span counts by CRS level, and the verdict. The clean run of the same
photo is the reference for "what glare erased".

Candidate features (computed on the photo, model-independent unless noted):
  bright_share     share of content pixels with L* > 90 (near-white, not only clipped)
  line_contrast    thin dark-line visibility: mean of the top 1% black-top-hat response (5 px
                   ellipse) on L*, in L* units -- how dark thin structures are against their
                   surroundings; glare compresses exactly this
  wire_px          WireNet wire pixels (model)
  entropy / disagreement / R   (model, as in the reliability grade)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import yaml

from app.core.config import ROOT_DIR, load_analysis_config
from ml.stress import CONDITIONS, apply_condition


def photo_features(rgb: np.ndarray, fill: int = 114) -> dict:
    from app.pipeline.enhance import _content
    content = _content(rgb, fill)
    L = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)[..., 0].astype(np.float32) * (100.0 / 255.0)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    tophat = cv2.morphologyEx(L, cv2.MORPH_BLACKHAT, k)[content]
    top = np.sort(tophat)[-max(1, tophat.size // 100):]
    return {"bright_share": float((L[content] > 90).mean()), "line_contrast": float(top.mean())}


def main() -> None:
    from app.pipeline.runner import run_analysis
    from app.pipeline.wirenet import make_segmenter
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--split", default="val")
    ap.add_argument("--conditions", nargs="*", default=list(CONDITIONS))
    ap.add_argument("--glare-gate", choices=["config", "off"], default="config", help="off = v2.9 behaviour")
    a = ap.parse_args()
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    if a.glare_gate == "off":
        cfg = cfg.model_copy(update={"quality": cfg.quality.model_copy(update={"glare_gate": False})})
    seg = make_segmenter(cfg.segmentation, ROOT_DIR)
    assert seg.load(), seg.error
    spec = yaml.safe_load(open("datasets/wireframe_seg/data.yaml"))
    for p in sorted((Path("datasets/wireframe_seg") / spec[a.split]).glob("*.jpg")):
        rgb0 = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
        for cond in a.conditions:
            rgb = apply_condition(rgb0, cond, p.name)
            o = run_analysis(rgb, cfg, seg)
            lv = [w.level for w in o.wires if w.level]
            q, unc = o.input_quality or {}, getattr(seg, "last_uncertainty", None) or {}
            row = {"split": a.split, "image": p.stem, "condition": cond, "glare_gate": cfg.quality.glare_gate,
                   **photo_features(rgb),
                   **{k: q.get(k) for k in ("mean_l", "contrast", "clipped_high", "sharpness", "motion_coherence")},
                   "wire_px": unc.get("wire_pixels"), "entropy": unc.get("entropy"), "disagreement": unc.get("disagreement"),
                   "R": (o.quality or {}).get("reliability"), "grade": (o.quality or {}).get("grade"),
                   "spans": len(o.wires), "scored": len(lv), "high": lv.count("high"), "medium": lv.count("medium"),
                   "verdict": (o.hazard or {}).get("verdict"), "timings_ms": o.timings_ms}
            with open(a.out, "a") as fh:
                fh.write(json.dumps(row, default=float) + "\n")
            print(p.stem[-8:], cond, row["verdict"], row["grade"], row["scored"], round(row["line_contrast"], 2),
                  round(row["bright_share"], 3), flush=True)


if __name__ == "__main__":
    main()
