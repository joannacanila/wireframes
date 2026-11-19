"""S15 measurement fidelity: how close is each measured image sag ratio to the one its labelled wire gives?

For every predicted wire in a dump (ml/experiments/s15_aspect.py) that lies on one labelled wire, the same
centerline + catenary procedure is run on the labelled polygon restricted to the predicted wire's extent (the part of
the wire the prediction covers). The difference isolates the measurement chain - mask quality, centerline, fit - from
fragmentation; ``full`` compares with the whole labelled wire instead (what a fragment costs). Offline: no model.

    python -m ml.experiments.s15_measure --dump runs/s15/shipped-square-test.json --split test --form square
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from app.core.config import ROOT_DIR, load_analysis_config
from app.pipeline.catenary import fit_catenary
from app.pipeline.skeleton import extract_centerline
from ml.experiments import s15_aspect as sa
from ml.experiments import v217_eval as v

K9 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))


def measure(mask: np.ndarray, cfg, fitter=fit_catenary, **kw) -> dict | None:
    cl = extract_centerline(mask, cfg.centerline.l_min_px, cfg.centerline.n_min_points)
    if not cl.ok:
        return None
    h, w = mask.shape
    f = fitter(cl.points, image_size=(w, h), max_rel_residual=cfg.catenary.max_rel_residual,
               border_margin_frac=cfg.catenary.border_margin_frac, **kw)
    return {"ok": f.ok, "reason": f.reason, "ratio": f.sag_ratio, "chord": f.chord_px, "rel_res": f.rel_residual,
            "excluded": getattr(f, "excluded_frac", 0.0)}


def pairs(rows: list[dict], gts: dict, cfg) -> list[dict]:
    out = []
    for r in rows:
        shape = tuple(r["shape"])
        g = [(c, m) for c, m in gts[r["stem"]] if c != "Vegetation"]
        gd = [cv2.dilate(m.astype(np.uint8), K9).astype(bool) for _, m in g]
        for w in r["wires"]:
            pm = v._raster(w["polygons"], shape)
            n = pm.sum()
            if n < 50 or not g:
                continue
            ov = [int((pm & d).sum()) for d in gd]
            j = int(np.argmax(ov))
            if ov[j] < 0.6 * n:  # not on one labelled wire (unlabelled, or a merge of several)
                continue
            ext = cv2.dilate(pm.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))).astype(bool)
            ref = measure(g[j][1] & ext, cfg)
            full = measure(g[j][1], cfg)
            out.append({"stem": r["stem"], "cls": w["cls"], "status": w["status"], "reason": w.get("reason"),
                        "pred": w.get("sag"), "pred_mask": pm, "ref": ref, "full": full})
    return out


def summary(err: np.ndarray) -> dict:
    if not len(err):
        return {"n": 0}
    a = np.abs(err)
    return {"n": int(len(a)), "mae_pp": round(float(a.mean()), 3), "median_pp": round(float(np.median(a)), 3),
            "p90_pp": round(float(np.percentile(a, 90)), 3), "within_0.5pp": round(float((a <= 0.5).mean()), 3),
            "within_1pp": round(float((a <= 1.0).mean()), 3), "bias_pp": round(float(err.mean()), 3)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True)
    ap.add_argument("--split", default="test")
    ap.add_argument("--form", default="square")
    ap.add_argument("--out", default=None)
    ap.add_argument("--fitter", default="stored", choices=["stored", "catenary", "span"])
    a = ap.parse_args()
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    ps = pairs(json.loads(Path(a.dump).read_text()), sa.gts(a.split, a.form), cfg)
    if a.fitter != "stored":  # re-measure every matched prediction from its polygon with the chosen fitter
        from app.pipeline.span_fit import fit_span
        fn = fit_span if a.fitter == "span" else fit_catenary
        for p in ps:
            q = measure(p["pred_mask"], cfg, fn)
            p["pred"] = q["ratio"] if q and q["ok"] else None
            p["status"] = "measured" if q and q["ok"] else "fit_failed"
            p["split"] = bool(q and q.get("excluded"))
    ok = [p for p in ps if p["pred"] is not None and p["status"] != "fit_failed" and p["ref"] and p["ref"]["ok"]]
    err = np.array([(p["pred"] - p["ref"]["ratio"]) * 100 for p in ok])
    okf = [p for p in ok if p["full"] and p["full"]["ok"]]
    errf = np.array([(p["pred"] - p["full"]["ratio"]) * 100 for p in okf])
    rep = {"pairs": len(ps), "measured_vs_label_same_extent": summary(err), "measured_vs_full_label": summary(errf),
           "label_fit_ok_rate": round(float(np.mean([bool(p["ref"] and p["ref"]["ok"]) for p in ps])), 3),
           "by_status": {s: sum(p["status"] == s for p in ps) for s in {p["status"] for p in ps}},
           "measured_share": round(len(ok) / max(1, len(ps)), 3), "split": sum(bool(p.get("split")) for p in ps)}
    for c in ("power-distribution-lines", "telecom-cable"):
        sel = [i for i, p in enumerate(ok) if p["cls"] == c]
        rep[f"same_extent_{c}"] = summary(err[sel]) if sel else {"n": 0}
    print(json.dumps(rep, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
