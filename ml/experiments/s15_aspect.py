"""S15 aspect-parity diagnosis: does the deployed pipeline type wires differently on a field-shaped photo?

Every curated image is a 1024 x 1024 letterbox (grey 114 bars) of a non-square original (ml/curate.py). A phone
upload is the original without bars. Cropping the bars back off a held-out image (and its labels with it) gives a
field-shaped photo whose ground truth is still known. WireNet re-letterboxes either version to the same 1024 input,
so any difference in strand typing comes from the stages after segmentation.

    python -m ml.experiments.s15_aspect dump  --split test --form square   # runs/s15/<tag>-<form>-<split>.json
    python -m ml.experiments.s15_aspect dump  --split test --form field
    python -m ml.experiments.s15_aspect score --split test --form field    # docs/thesis/results/s15_aspect_*.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from app.core.config import ROOT_DIR
from ml.experiments import v217_eval as v

DUMP = Path("runs/s15")
TAGS = {"shipped": {"segmentation": {"strand_head_frame": "original"}},  # pre-S15 head inputs
        "letterbox": {"segmentation": {"strand_head_frame": "letterbox"}},  # S15 parity fix
        "clamp": {"segmentation": {"strand_head_frame": "letterbox", "strand_head_clamp": True}}}  # + feature range


def content_box(bgr: np.ndarray, fill: int = 114, tol: float = 4.0) -> tuple[int, int, int, int]:
    """(y0, y1, x0, x1) of the photo inside its letterbox: leading/trailing rows and columns that are flat grey."""
    g = bgr.astype(np.float32)
    flat_r = (np.abs(g - fill).mean((1, 2)) < tol) & (g.std((1, 2)) < tol)
    flat_c = (np.abs(g - fill).mean((0, 2)) < tol) & (g.std((0, 2)) < tol)

    def span(flat):
        idx = np.nonzero(~flat)[0]
        return (int(idx[0]), int(idx[-1]) + 1) if len(idx) else (0, len(flat))
    (y0, y1), (x0, x1) = span(flat_r), span(flat_c)
    return y0, y1, x0, x1


def boxes(split: str) -> dict[str, tuple[int, int, int, int]]:
    return {p.stem: content_box(cv2.imread(str(p))) for p in v._paths(split)}


def dump(split: str, form: str, tag: str, over: dict | None = None) -> Path:
    from app.pipeline.runner import run_analysis
    from app.pipeline.wirenet import make_segmenter
    from ml.experiments.v216_regression import _cfg
    cfg = _cfg(over or {})
    seg = make_segmenter(cfg.segmentation, ROOT_DIR)
    assert seg.load(), seg.error
    bx = boxes(split)
    rows = []
    for p in v._paths(split):
        rgb = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
        if form == "field":
            y0, y1, x0, x1 = bx[p.stem]
            rgb = np.ascontiguousarray(rgb[y0:y1, x0:x1])
        o = run_analysis(rgb, cfg, seg)
        rows.append({"stem": p.stem, "shape": list(rgb.shape[:2]),
                     "wires": [{"cls": w.wire_class, "confident": "class_uncertain" not in w.flags, "conf": w.confidence,
                                "polygons": w.polygons, "status": w.status, "flags": w.flags, "crs": w.crs,
                                "sag": (w.fit or {}).get("sag_ratio"), "reason": w.fail_reason,
                                "p_tel": ((w.fit or {}).get("class_evidence") or {}).get("head_p_telecom")}
                               for w in o.wires],
                     "vegetation": [], "verdict": (o.hazard or {}).get("verdict")})
        print(p.stem[:30], rows[-1]["shape"], len(o.wires),
              sum(w["cls"] == "telecom-cable" for w in rows[-1]["wires"]), "telecom", flush=True)
    DUMP.mkdir(parents=True, exist_ok=True)
    out = DUMP / f"{tag}-{form}-{split}.json"
    out.write_text(json.dumps(rows))
    return out


def gts(split: str, form: str) -> dict:
    g = v.load_gts(split)
    if form == "square":
        return g
    bx = boxes(split)
    return {k: [(c, m[bx[k][0]:bx[k][1], bx[k][2]:bx[k][3]]) for c, m in lst] for k, lst in g.items()}


def score(split: str, form: str, tag: str) -> dict:
    rows = json.loads((DUMP / f"{tag}-{form}-{split}.json").read_text())
    r = v.score_rows(rows, gts(split, form), n_boot=500)
    r.pop("per_image", None)
    out = Path(f"docs/thesis/results/s15_aspect_{tag}_{form}_{split}.json")
    out.write_text(json.dumps(r, indent=1, default=str))
    s = r["strand_class"]
    print(f"{tag} {form} {split}: strand acc {s['accuracy']:.3f} matched {s['matched']}  confusion {s['confusion']}")
    for c in ("power-distribution-lines", "telecom-cable"):
        print("  ", c, {k: (round(x, 3) if isinstance(x, float) else x) for k, x in s[c].items()})
    print("   centerline wire F1", round(r["centerline"]["wire"]["f1"], 3))
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["dump", "score"])
    ap.add_argument("--split", default="test", choices=["val", "test"])
    ap.add_argument("--form", default="field", choices=["square", "field"])
    ap.add_argument("--tag", default="shipped")
    a = ap.parse_args()
    if a.stage == "dump":
        print(dump(a.split, a.form, a.tag, TAGS[a.tag]))
    else:
        score(a.split, a.form, a.tag)


if __name__ == "__main__":
    main()
