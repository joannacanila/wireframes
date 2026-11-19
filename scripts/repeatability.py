"""Repeatability check (S13): run the server's pipeline on the same photos several times and compare outputs.

    python -m scripts.repeatability PHOTO [PHOTO ...] --runs 3 --out docs/thesis/results/s13_repeatability.json

Uses exactly the server's configuration and segmenter (app.core.state.build_state). Per run it records the model
identity (config fingerprint, checkpoint SHA-256 prefixes, component load errors) and every wire's class, status,
endpoints, chord, sag and sag ratio. Runs are compared exactly and with an explicit tolerance; nothing is averaged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

from app.core.config import get_analysis_config, get_settings
from app.core.state import MODELS_DIR, build_state
from app.pipeline.runner import run_analysis
from app.services.analysis import load_rgb

TOL_RATIO = 1e-6   # sag ratio, absolute
TOL_PX = 1e-3      # chord / sag / endpoints, pixels


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.resolve().open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()[:16]


def model_identity(st) -> dict:
    names = ["wirenet-r34.pt", "v212_mitb1.pt", "v212_convnext_t.pt", "strand_head_v217.pt",
             "poles-yolo11n-v218.pt", "wireframe-yolo11m-seg.pt"]
    seg = st.segmenter
    return {
        "config_fingerprint": st.config.fingerprint,
        "checkpoints": {n: _sha(MODELS_DIR / n) for n in names if (MODELS_DIR / n).exists()},
        "segmenter_error": getattr(seg, "error", None),
        "tta": getattr(seg, "tta", None),
    }


def snapshot(outcome) -> dict:
    wires = []
    for w in outcome.wires:
        fit = getattr(w, "fit", None) or {}
        wires.append({
            "cls": w.wire_class, "status": w.status, "fail": w.fail_reason,
            "chord": fit.get("chord_px"), "sag": fit.get("sag_px"), "ratio": fit.get("sag_ratio"),
            "res": fit.get("sag_ratio_resolution"), "ends": fit.get("endpoints"),
        })
    wires.sort(key=lambda d: (d["cls"], d["chord"] or 0.0, d["ratio"] or 0.0))
    return {"status": outcome.status, "flags": sorted(outcome.flags), "n_wires": len(wires), "wires": wires,
            "mask_sha": hashlib.sha256(np.ascontiguousarray(outcome.wire_mask).tobytes()).hexdigest()[:16]
            if outcome.wire_mask is not None else None}


def _close(a, b, tol) -> bool:
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) <= tol


def compare(ref: dict, other: dict) -> dict:
    if ref == other:
        return {"exact": True, "material": []}
    material = []
    if ref["n_wires"] != other["n_wires"]:
        material.append(f"wire count {ref['n_wires']} vs {other['n_wires']}")
    for k in ("status", "flags"):
        if ref[k] != other[k]:
            material.append(f"{k} differs")
    for i, (a, b) in enumerate(zip(ref["wires"], other["wires"], strict=False)):
        if (a["cls"], a["status"], a["fail"]) != (b["cls"], b["status"], b["fail"]):
            material.append(f"wire {i}: class/status differs")
        elif not (_close(a["ratio"], b["ratio"], TOL_RATIO) and _close(a["chord"], b["chord"], TOL_PX)
                  and _close(a["sag"], b["sag"], TOL_PX)):
            material.append(f"wire {i}: measurement outside tolerance")
    return {"exact": False, "mask_equal": ref["mask_sha"] == other["mask_sha"], "material": material}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.repeatability")
    ap.add_argument("photos", nargs="+", type=Path)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    st = build_state(get_settings(), get_analysis_config(), None)
    loader = getattr(st.segmenter, "load", None)
    if loader:
        loader()
    ident = model_identity(st)
    report = {"identity": ident, "tolerance": {"ratio": TOL_RATIO, "px": TOL_PX}, "photos": {}}
    ok = True
    for p in a.photos:
        rgb = load_rgb(p.read_bytes())
        snaps = [snapshot(run_analysis(rgb, st.config, st.segmenter)) for _ in range(a.runs)]
        cmp = [compare(snaps[0], s) for s in snaps[1:]]
        report["photos"][p.name] = {"n_wires": snaps[0]["n_wires"], "status": snaps[0]["status"],
                                    "runs": a.runs, "comparisons": cmp,
                                    "snapshot_sha": hashlib.sha256(json.dumps(snaps[0], sort_keys=True).encode()).hexdigest()[:16]}
        ok &= all(c["exact"] or not c["material"] for c in cmp)
        print(p.name, snaps[0]["n_wires"], "wires;", ["exact" if c["exact"] else c for c in cmp], flush=True)
    report["repeatable"] = ok
    if a.out:
        a.out.write_text(json.dumps(report, indent=1))
    print(json.dumps(ident, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
