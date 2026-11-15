"""Latency profile of the screener on dense poles (v2.10).

    python -m ml.experiments.latency_profile --images <stem> ... [--cprofile]

Per photo: wall time of run_analysis by stage (the pipeline's own timings_ms), repeated --reps
times after one warm-up, plus optionally a cProfile of one run (top functions by cumulative
time). Photos are found in datasets/wireframe_seg/images/{val,test} by a substring of the name.
"""

from __future__ import annotations

import argparse
import cProfile
import io
import json
import pstats
import time
from pathlib import Path

import cv2
import numpy as np

from app.core.config import ROOT_DIR, load_analysis_config


def find(stem: str) -> Path:
    if Path(stem).is_file():  # v2.13: any photo by path (e.g. an unlabelled live upload)
        return Path(stem)
    for split in ("val", "test"):
        for p in sorted(Path(f"datasets/wireframe_seg/images/{split}").glob("*.jpg")):
            if stem in p.name:
                return p
    raise FileNotFoundError(stem)


def main() -> None:
    from app.pipeline.runner import run_analysis
    from app.pipeline.wirenet import make_segmenter
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", nargs="+", required=True)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--cprofile", action="store_true")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--wirenet", default=None, help="v2.12: weights or ensemble spec instead of the configured ones")
    ap.add_argument("--no-ground-gate", action="store_true", help="v2.13: switch the ground gate off")
    a = ap.parse_args()
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    if a.wirenet:
        cfg = cfg.model_copy(update={"segmentation": cfg.segmentation.model_copy(update={"wirenet_weights": a.wirenet})})
    if a.no_ground_gate:
        cfg = cfg.model_copy(update={"segmentation": cfg.segmentation.model_copy(update={"ground_gate": False})})
    seg = make_segmenter(cfg.segmentation, ROOT_DIR)
    assert seg.load(), seg.error
    rows = []
    for stem in a.images:
        p = find(stem)
        rgb = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
        o = run_analysis(rgb, cfg, seg)  # warm-up
        walls, stages = [], []
        for _ in range(a.reps):
            t0 = time.perf_counter()
            o = run_analysis(rgb, cfg, seg)
            walls.append((time.perf_counter() - t0) * 1e3)
            stages.append(o.timings_ms)
        med = {k: float(np.median([s[k] for s in stages])) for k in stages[0]}
        row = {"image": p.stem, "spans": len(o.wires), "wall_ms": float(np.median(walls)), "stages_ms": med}
        rows.append(row)
        print(json.dumps(row), flush=True)
        if a.cprofile:
            pr = cProfile.Profile()
            pr.enable()
            run_analysis(rgb, cfg, seg)
            pr.disable()
            buf = io.StringIO()
            pstats.Stats(pr, stream=buf).sort_stats("cumulative").print_stats(30)
            print(buf.getvalue())
    if a.out:
        a.out.write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
