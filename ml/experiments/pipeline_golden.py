"""Whole-pipeline golden-output check (v2.10 latency work).

    python -m ml.experiments.pipeline_golden --write ref.json [--images N]
    python -m ml.experiments.pipeline_golden --check ref.json

Runs run_analysis with the shipped configuration on validation photos (the N densest by default
order: all) and records, per photo, a digest of everything the screener reports: every wire's
index, class, status, CRS, level, polygons, centreline and fit; the vegetation regions; the hazard
verdict, reasons and factors; the reliability grade. --check fails on any difference and reports
the run time per photo. The reference for v2.10 was written from commit 5989657 (v2.9) in a
separate git worktree, so an optimisation is accepted only if v2.10 reproduces v2.9 exactly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import cv2
import yaml

from app.core.config import ROOT_DIR, load_analysis_config


def digest(o) -> dict:
    h = lambda x: hashlib.sha256(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()[:16]  # noqa: E731
    return {"status": o.status, "wires": [[w.index, w.wire_class, w.status, w.crs, w.level, h(w.polygons),
                                           h(w.centerline), h(w.fit)] for w in o.wires],
            "vegetation": h([[v.polygons, v.touches] for v in o.vegetation]),
            "hazard": h(o.hazard), "verdict": (o.hazard or {}).get("verdict"),
            "quality": h({k: v for k, v in (o.quality or {}).items()}), "flags": sorted(o.flags)}


def main() -> None:
    from app.pipeline.runner import run_analysis
    from app.pipeline.wirenet import make_segmenter
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", type=Path)
    g.add_argument("--check", type=Path)
    ap.add_argument("--split", default="val")
    a = ap.parse_args()
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    seg = make_segmenter(cfg.segmentation, ROOT_DIR)
    assert seg.load(), seg.error
    spec = yaml.safe_load(open("datasets/wireframe_seg/data.yaml"))
    out, times = {}, {}
    for p in sorted((Path("datasets/wireframe_seg") / spec[a.split]).glob("*.jpg")):
        rgb = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
        t0 = time.perf_counter()
        o = run_analysis(rgb, cfg, seg)
        times[p.stem] = round((time.perf_counter() - t0) * 1e3)
        out[p.stem] = digest(o)
    if a.write:
        a.write.write_text(json.dumps({"root": str(ROOT_DIR), "photos": out, "ms": times}, indent=0))
        print("written", len(out), "photos; total ms", sum(times.values()))
        return
    ref = json.loads(a.check.read_text())
    bad = [k for k in ref["photos"] if ref["photos"][k] != out.get(k)]
    print(f"identical: {not bad} ({len(out) - len(bad)}/{len(ref['photos'])}); total ms {sum(ref['ms'].values())} -> "
          f"{sum(times.values())}")
    for k in bad[:5]:
        r, n = ref["photos"][k], out[k]
        print(k[-12:], {f: (r[f], n[f]) for f in r if r[f] != n[f] and f != "wires"},
              "wires differ" if r["wires"] != n["wires"] else "")
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
