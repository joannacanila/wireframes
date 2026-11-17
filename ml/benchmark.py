"""End-to-end and per-stage runtime of the deployed pipeline, with memory, for the thesis reference (Section 13).

    python -m ml.benchmark --images datasets/wireframe_seg/images/test/*.jpg var/uploads/<a>.jpg ... --reps 1 \
        --field-reps 3 --out docs/thesis/results/benchmark_v223.json

Procedure: the segmenter is built and loaded once (cold start timed separately), one warm-up analysis is discarded,
then every image is analysed ``--reps`` times (photos outside the dataset ``--field-reps`` times). Each run records the
pipeline's own stage timings (preprocess, vni, segmentation, geometry_and_risk), the sub-stages of segmentation
(YOLO proposals, WireNet ensemble, pole detector, strand extraction with the type head, scene gate; synchronised with
the GPU so the times are not attributed to the next stage), the formal PDF build, process RSS and MPS driver memory.
Statistics: min, max, mean, median, P90, P95 (nearest rank), standard deviation.
"""

from __future__ import annotations

import argparse
import functools
import json
import platform
import resource
import statistics as st
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np


def stats(xs: list[float]) -> dict:
    xs = sorted(xs)
    if not xs:
        return {}
    rank = lambda q: xs[min(len(xs) - 1, max(0, int(np.ceil(q * len(xs))) - 1))]  # noqa: E731  nearest rank
    return {"n": len(xs), "min": round(xs[0], 3), "max": round(xs[-1], 3), "mean": round(st.mean(xs), 3),
            "median": round(st.median(xs), 3), "p90": round(rank(0.9), 3), "p95": round(rank(0.95), 3),
            "std": round(st.pstdev(xs), 3)}


def rss_now_mb() -> float:
    import os
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True).stdout.strip()
    return int(out or 0) / 1024


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20  # macOS reports bytes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--images", nargs="+", required=True)
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--field-reps", type=int, default=3)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    import torch

    from app.core.config import ROOT_DIR, get_analysis_config
    from app.pipeline import wirenet as W
    from app.pipeline.runner import run_analysis
    from app.services.report_pdf import build_report

    cfg = get_analysis_config()
    t0 = time.perf_counter()
    seg = W.make_segmenter(cfg.segmentation, ROOT_DIR)
    assert seg.load(), seg.error
    load_s = time.perf_counter() - t0
    mps = seg._device == "mps"
    sync = (lambda: torch.mps.synchronize()) if mps else (lambda: None)
    sub: dict[str, float] = {}

    def timed(name, fn):
        @functools.wraps(fn)
        def g(*args, **kw):
            sync(); t = time.perf_counter(); r = fn(*args, **kw); sync()
            sub[name] = sub.get(name, 0.0) + time.perf_counter() - t
            return r
        return g

    seg.yolo.segment = timed("yolo_proposals", seg.yolo.segment)
    seg._infer = timed("wirenet_ensemble", seg._infer)
    seg._poles = timed("pole_detector", seg._poles)
    seg._scene_gate = timed("scene_gate", seg._scene_gate)
    W.skeleton_instances = timed("strand_extraction_and_type_head", W.skeleton_instances)

    def one(path: Path) -> dict:
        rgb = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)
        sub.clear()
        t = time.perf_counter(); out = run_analysis(rgb, cfg, seg); wall = time.perf_counter() - t
        # the formal PDF from a result shaped like the API's (wires only; the build cost is dominated by the figure)
        res = {"id": "0" * 32, "created_at": "2026-01-01T00:00:00", "status": "complete", "image": {"original_filename": path.name},
               "pole": {"code": "BENCH", "location": None}, "inspection": {}, "reviews": [], "guardrail": {},
               "hazard": {"verdict": out.hazard.get("verdict") if isinstance(out.hazard, dict) else getattr(out.hazard, "verdict", "")},
               "wires": [{"index": w.index, "class": w.wire_class, "status": w.status, "level": w.level, "sag_ratio": (w.fit or {}).get("sag_ratio"),
                          "delta_lim": w.delta_lim, "flags": w.flags, "mask_polygons": w.polygons, "catenary_curve": w.curve,
                          "fit": w.fit, "bbox": w.bbox} for w in out.wires]}
        t = time.perf_counter(); pdf = build_report(res, rgb); pdf_s = time.perf_counter() - t
        return {"image": path.name, "size": [rgb.shape[1], rgb.shape[0]], "wires": len(out.wires), "wall_s": round(wall, 3),
                "stages_s": {k: round(v / 1000, 3) for k, v in out.timings_ms.items()}, "segmentation_sub_s": {k: round(v, 3) for k, v in sub.items()},
                "pdf_s": round(pdf_s, 3), "pdf_kb": len(pdf) // 1024, "rss_peak_mb": round(rss_mb(), 1), "rss_now_mb": round(rss_now_mb(), 1),
                "mps_driver_mb": round(torch.mps.driver_allocated_memory() / 2**20, 1) if mps else None,
                "polarity": getattr(seg, "last_polarity", None)}

    paths = [Path(p) for p in a.images]
    warm = one(paths[0])  # discarded: first-call kernel compilation and caches
    runs = []
    for p in paths:
        reps = a.reps if "datasets/" in str(p) else a.field_reps
        for _ in range(reps):
            runs.append(one(p))
            print(json.dumps(runs[-1]), flush=True)

    def grp(rows):
        keys = sorted({k for r in rows for k in r["stages_s"]})
        sk = sorted({k for r in rows for k in r["segmentation_sub_s"]})
        return {"end_to_end_s": stats([r["wall_s"] for r in rows]),
                "stages_s": {k: stats([r["stages_s"].get(k, 0.0) for r in rows]) for k in keys},
                "segmentation_sub_s": {k: stats([r["segmentation_sub_s"].get(k, 0.0) for r in rows]) for k in sk},
                "pdf_s": stats([r["pdf_s"] for r in rows])}
    dataset = [r for r in runs if r["size"] == [1024, 1024]]
    field = [r for r in runs if r["size"] != [1024, 1024]]
    rss = [r["rss_peak_mb"] for r in runs]
    report = {
        "environment": {"machine": platform.machine(), "cpu": subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip(),
                        "ram_gb": int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout.strip() or 0) / 2**30,
                        "os": platform.platform(), "python": platform.python_version(), "torch": torch.__version__, "device": seg._device,
                        "config_fingerprint": cfg.fingerprint},
        "procedure": {"warm_up_discarded": warm["image"], "reps_dataset": a.reps, "reps_field": a.field_reps},
        "cold_start_model_load_s": round(load_s, 3), "first_analysis_s": warm["wall_s"],
        "dataset_1024": grp(dataset) if dataset else None, "field_photos": grp(field) if field else None, "all": grp(runs),
        "memory": {"rss_peak_mb_first": rss[0], "rss_peak_mb_last": rss[-1],
                   "rss_now_mb_first": runs[0]["rss_now_mb"], "rss_now_mb_last": runs[-1]["rss_now_mb"],
                   "mps_driver_mb_max": max((r["mps_driver_mb"] or 0) for r in runs)},
        "runs": runs,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(report, indent=1))
    print(json.dumps({k: report[k] for k in ("cold_start_model_load_s", "first_analysis_s", "memory")}))
    for g in ("dataset_1024", "field_photos", "all"):
        if report[g]:
            print(g, "end-to-end", report[g]["end_to_end_s"], "pdf", report[g]["pdf_s"])


if __name__ == "__main__":
    main()
