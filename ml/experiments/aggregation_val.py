"""Pole-level verdict under the v2.9 rule variants (validation; test only once, for reporting).

    python -m ml.experiments.aggregation_val --split val --out docs/thesis/results/v29_verdicts_val.json

Each photo is analysed once with the shipped pipeline (all spans scored, no inclination split);
the rule variants are then applied to the same spans and vegetation, so they differ only in the
rule:
    v2.8               any span in a band sets the verdict
    split              + chords steeper than 35 deg are service drops (no sag verdict)
    corroborated       binomial corroboration (p0 from v29_span_geometry_summary_val.json)
    v2.9               split + corroborated
    v2.9+low_frame     v2.9 + service drops reaching the lowest 15% of the frame -> MODERATE
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import yaml

from app.core.config import ROOT_DIR, load_analysis_config
from app.pipeline.hazard import assess
from app.pipeline.runner import chord_inclination_deg

VARIANTS = {"v2.8": (0, "any", 0.0), "split": (35, "any", 0.0), "corroborated": (0, "corroborated", 0.0),
            "v2.9": (35, "corroborated", 0.0), "v2.9+low_frame": (35, "corroborated", 0.15)}


def _mask(polys, shape):
    m = np.zeros(shape, np.uint8)
    for p in polys:
        if len(p) >= 3:
            cv2.fillPoly(m, [np.asarray(p, np.int32).reshape(-1, 1, 2)], 1)
    return m.astype(bool)


def driver(reasons: list[str]) -> str:
    keys = []
    for r in reasons:
        if r.startswith(("AT RISK - HIGH", "AT RISK - MODERATE")):
            lvl = "high" if r.startswith("AT RISK - HIGH") else "moderate"
            what = ("sag" if "CRS band" in r else "vegetation-power" if "power span" in r else
                    "vegetation-telecom" if "telecom span" in r else "low drop" if "service drop" in r else "other")
            keys.append(f"{what}:{lvl}")
    return " + ".join(sorted(set(keys))) or "none"


def main() -> None:
    from app.pipeline.runner import run_analysis
    from app.pipeline.wirenet import make_segmenter
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--p0", type=Path, default=Path("docs/thesis/results/v29_span_geometry_summary_val.json"))
    a = ap.parse_args()
    p0 = json.loads(a.p0.read_text())
    base = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    cfg = base.model_copy(update={"crs": base.crs.model_copy(update={"max_span_inclination_deg": 0.0}),
                                  "hazard": base.hazard.model_copy(update={"enabled": True, "sag_rule": "any"})})
    seg = make_segmenter(cfg.segmentation, ROOT_DIR)
    assert seg.load(), seg.error
    data = Path("datasets/wireframe_seg/data.yaml")
    spec = yaml.safe_load(data.read_text())
    photos = []
    for p in sorted((data.parent / spec[a.split]).glob("*.jpg")):
        rgb = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
        o = run_analysis(rgb, cfg, seg)
        shape = rgb.shape[:2]
        content = np.flatnonzero(~(np.abs(rgb.astype(np.int16) - 114) <= 3).all(axis=2).all(axis=1))
        rows = [{"index": w.index, "class": w.wire_class, "mask": _mask(w.polygons, shape), "level": w.level,
                 "status": w.status, "incl": chord_inclination_deg(w.centerline)} for w in o.wires]
        veg = [_mask(v.polygons, shape) for v in o.vegetation]
        rec = {"image": p.stem, "grade": (o.quality or {}).get("grade")}
        for name, (max_incl, rule, low) in VARIANTS.items():
            rr = []
            for r in rows:
                r2 = dict(r)
                if max_incl and r["status"] != "too_short" and r["incl"] > max_incl:
                    r2.update(status="service_drop", level=None)
                rr.append(r2)
            hcfg = cfg.hazard.model_copy(update={"sag_rule": rule, "p0_high": round(p0["p0_high"], 3),
                                                 "p0_medium": round(p0["p0_medium"], 3), "low_frame_frac": low})
            hz = assess(rr, veg, shape, 0.0101, cfg.hazard.veg_moderate_px, rule=hcfg,
                        frame_rows=(int(content[0]), int(content[-1])) if len(content) else None)
            rec[name] = {"verdict": hz.verdict, "driver": driver(hz.reasons), "sag": {k: (len(v) if isinstance(v, list) else v)
                                                                                     for k, v in hz.factors["sag"].items()}}
        photos.append(rec)
        print(p.stem[-20:], {k: rec[k]["verdict"] for k in VARIANTS}, flush=True)
    summary = {k: {"verdict": dict(Counter(r[k]["verdict"] for r in photos)),
                   "driver": dict(Counter(r[k]["driver"] for r in photos))} for k in VARIANTS}
    a.out.write_text(json.dumps({"split": a.split, "p0": {"high": p0["p0_high"], "medium": p0["p0_medium"]},
                                 "summary": summary, "photos": photos}, indent=1, default=float))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
