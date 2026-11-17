"""v2.16 regression run on the field photos that showed the reported defects (runs/v216/regression/*.jpg).

Runs the deployed pipeline (run_analysis, same segmenter and config as the app) with configuration overrides, writes
a JSON summary per photo and variant, and an overlay coloured by wire class (grey = type uncertain, dashed white =
inferred bridge pixels, i.e. span pixels WireNet did not label as wire).

    python -m ml.experiments.v216_regression --variants v215,v216 --out runs/v216/regression
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from app.core.config import CLASSES, ROOT_DIR, VEGETATION, load_analysis_config

COLORS = {"power-distribution-lines": (255, 161, 106), "telecom-cable": (255, 140, 180), "abandoned-wire": (87, 145, 255)}
UNKNOWN = (166, 160, 154)

VARIANTS = {
    # v2.15 as shipped: per-class spans, no structure / vegetation gates, lens envelope not applied to sag
    "v215": {"segmentation": {"span_strand_classes": False, "span_class_min_share": 0.0, "structure_gate": False,
                              "vegetation_gate": False, "span_gap_angle_deg": 25, "span_max_lateral_px": 0.0},
             "_envelope": False},
    "v216": {"segmentation": {"wirenet_weights": "models/wirenet-r34.pt", "span_wire_tau": None, "strand_head": None}},
    "v217": {},  # v2.17 as shipped (config/wireframe.yaml)
}


def _cfg(over: dict):
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    upd = {}
    for block, vals in over.items():
        if block.startswith("_"):
            continue
        upd[block] = getattr(cfg, block).model_copy(update=vals)
    return cfg.model_copy(update=upd)


def summarize(o) -> dict:
    ws = o.wires
    sags = sorted((w.fit.get("sag_ratio") for w in ws if w.status == "scored"), reverse=True)
    return {
        "status": o.status, "wires": len(ws),
        "by_class": {c: sum(w.wire_class == c for w in ws) for c in CLASSES if c != VEGETATION},
        "class_uncertain": sum("class_uncertain" in w.flags for w in ws),
        "scored": sum(w.status == "scored" for w in ws),
        "status_counts": {s: sum(w.status == s for w in ws) for s in sorted({w.status for w in ws})},
        "worst_crs": max((w.crs for w in ws if w.crs is not None), default=None),
        "crs_ge_6": sum(1 for w in ws if w.crs is not None and w.crs > 6),
        "top_sag_pct": [round(100 * s, 2) for s in sags[:8]],
        "verdict": (o.hazard or {}).get("verdict"),
        "vegetation_regions": len(o.vegetation),
        "vegetation_contacts": sum(bool(v.touches) for v in o.vegetation),
        "suppressed": {c: sum(x.get("reason_code") == c for x in o.suppressed)
                       for c in ("ground_structure", "structure_edge", "not_vegetation")},
        "flags": o.flags,
        "timings_ms": o.timings_ms,
    }


def render(rgb: np.ndarray, o, path: Path) -> None:
    img = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR).copy()
    label = o.wire_mask
    wire_ids = [i + 1 for i, c in enumerate(CLASSES) if c != VEGETATION]
    for v in o.vegetation:
        for p in v.polygons:
            cv2.polylines(img, [np.asarray(p, np.int32)], True, (90, 190, 95), 2)
    for w in o.wires:
        col = UNKNOWN if "class_uncertain" in w.flags else COLORS[w.wire_class]
        m = np.zeros(rgb.shape[:2], np.uint8)
        cv2.fillPoly(m, [np.asarray(p, np.int32) for p in w.polygons if len(p) >= 3], 1)
        obs = m.astype(bool) if label is None else m.astype(bool) & np.isin(label, wire_ids)
        img[obs] = (0.25 * img[obs] + 0.75 * np.array(col)).astype(np.uint8)
        inf = m.astype(bool) & ~obs
        ys, xs = np.nonzero(inf)
        keep = ((xs + ys) // 4) % 2 == 0  # dashed
        img[ys[keep], xs[keep]] = (255, 255, 255)
        if w.centerline:
            x, y = w.centerline[len(w.centerline) // 2]
            cv2.putText(img, str(w.index), (int(x), int(y)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1, cv2.LINE_AA)
    cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 90])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="v215,v216")
    ap.add_argument("--images", default="runs/v216/regression/street?.jpg")
    ap.add_argument("--out", type=Path, default=Path("runs/v216/regression"))
    a = ap.parse_args()
    from app.pipeline.runner import run_analysis
    from app.pipeline.wirenet import make_segmenter
    paths = sorted(Path().glob(a.images))
    env_file = Path(a.out) / "envelope.json"  # acquisition checks the app recorded at upload (EXIF is stripped)
    envelopes = json.loads(env_file.read_text()) if env_file.exists() else {}
    res = {}
    for v in a.variants.split(","):
        cfg = _cfg(VARIANTS[v])
        seg = make_segmenter(cfg.segmentation, ROOT_DIR)
        assert seg.load(), seg.error
        for p in paths:
            rgb = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
            env = envelopes.get(p.stem) if VARIANTS[v].get("_envelope", True) else None
            o = run_analysis(rgb, cfg, seg, envelope=env)
            res.setdefault(p.stem, {})[v] = s = summarize(o)
            render(rgb, o, a.out / f"{p.stem}.{v}.jpg")
            print(p.stem, v, {k: s[k] for k in ("wires", "by_class", "class_uncertain", "scored", "worst_crs",
                                                "crs_ge_6", "top_sag_pct", "verdict", "vegetation_contacts", "suppressed")}, flush=True)
    (a.out / "summary.json").write_text(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
