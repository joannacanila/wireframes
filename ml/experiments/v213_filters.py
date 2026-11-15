"""v2.13 span-level false-positive filters, measured on validation (cached v2.8 probabilities; no retraining).

    python -m ml.experiments.v213_filters --out docs/thesis/results/v213_span_features_val.jsonl
    python -m ml.experiments.v213_filters --analyse docs/thesis/results/v213_span_features_val.jsonl \
        --summary docs/thesis/results/v213_filters_val.json

Every span the shipped extractor produces on the 22 validation photos is matched to the labels (2 px tolerant,
IoU >= 0.5, same class) and described by the features the v2.13 rules use:
  ground_share   share of its pixels on ADE20K ground classes (app/pipeline/scene.py, UperNet-ConvNeXt-T)
  loop_holes     enclosed holes >= 20 px in its mask (closed rims and frames)
  bulge_up_px    how far a quadratic fit in the chord frame bows ABOVE the chord (a hanging wire bows below)
  quad_rms_px    residual of that fit
  isolation_px   distance to the nearest other wire span
  below_net_px   how far its centroid lies below the lowest pixel of every other wire span
A rule is acceptable only if it removes no labelled (true-positive) span on validation; its effect on span
tolerant mAP50 is reported.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from app.core.config import CLASSES
from app.pipeline import evaluation as ev
from ml.experiments.ablation_final import FINAL
from ml.experiments.sweep_spans import make

K5 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))


def widen(m):
    return cv2.dilate(m.astype(np.uint8), K5).astype(bool)


def iou(a, b):
    u = (a | b).sum()
    return float((a & b).sum() / u) if u else 0.0


def quad_features(mask: np.ndarray) -> tuple[float, float, float]:
    """(bulge_up_px, quad_rms_px, length_px) of a quadratic fitted in the span's principal-axis frame."""
    from skimage.morphology import skeletonize
    ys, xs = np.nonzero(skeletonize(mask))
    if len(xs) < 8:
        return 0.0, 0.0, 0.0
    pts = np.stack([xs, ys], 1).astype(float)
    mu = pts.mean(0)
    _, _, vt = np.linalg.svd(pts - mu, full_matrices=False)
    u, v = vt[0], vt[1]
    t, d = (pts - mu) @ u, (pts - mu) @ v
    coef = np.polyfit(t, d, 2)
    rms = float(np.sqrt(np.mean((np.polyval(coef, t) - d) ** 2)))
    t0, t1 = t.min(), t.max()
    tm = (t0 + t1) / 2
    chord_mid = (np.polyval(coef, t0) + np.polyval(coef, t1)) / 2
    dev = np.polyval(coef, tm) - chord_mid  # signed offset of the curve's middle from the chord, along v
    dy = dev * v[1]  # image-y component: > 0 means the middle hangs below the chord
    return float(max(0.0, -dy)), rms, float(t1 - t0)


def loop_holes(mask: np.ndarray, min_area: int = 20) -> int:
    cont, hier = cv2.findContours(mask.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hier is None:
        return 0
    return sum(1 for c, h in zip(cont, hier[0], strict=True) if h[3] >= 0 and cv2.contourArea(c) >= min_area)


def features(spans, gts, ground):
    wire_masks = [m for _, m, _ in spans]
    out = []
    for i, (c, m, s) in enumerate(spans):
        mw = widen(m)
        tp = any(g_c == c and iou(mw, widen(g)) >= 0.5 for g_c, g in gts)
        others = [w for j, w in enumerate(wire_masks) if j != i]
        if others:
            oth = np.any(others, axis=0)
            dist = cv2.distanceTransform((~oth).astype(np.uint8), cv2.DIST_L2, 3)
            iso = float(dist[m].min())
            ys = np.nonzero(oth)[0]
            below = float(np.nonzero(m)[0].mean() - ys.max())
        else:
            iso, below = 1e9, 0.0
        bulge, rms, length = quad_features(m)
        n = int(m.sum())
        out.append({"cls": c, "score": float(s), "tp": bool(tp), "pixels": n,
                    "ground_share": float((m & ground).sum() / n) if ground is not None and n else 0.0,
                    "loop_holes": loop_holes(m), "bulge_up_px": bulge, "quad_rms_px": rms, "length_px": length,
                    "isolation_px": iso, "below_net_px": below, "centroid_y_frac": float(np.nonzero(m)[0].mean() / m.shape[0])})
    return out


def run(a) -> None:
    from app.pipeline.scene import SceneParser
    from ml.experiments.harness import load
    sp = SceneParser()
    assert sp.load(), sp.error
    sem_dir = Path("runs/cache/sem-val")
    sem_dir.mkdir(parents=True, exist_ok=True)
    ex = make(FINAL)
    a.out.unlink(missing_ok=True)
    for f, z in load(a.cache):
        stem = f.stem if hasattr(f, "stem") else str(f)
        rgb = cv2.cvtColor(cv2.imread(f"datasets/wireframe_seg/images/val/{stem}.jpg"), cv2.COLOR_BGR2RGB)
        sf = sem_dir / f"{stem}.npy"
        lab = np.load(sf) if sf.exists() else sp.labels(rgb)
        np.save(sf, lab)
        probs = z["probs"].astype(np.float32)
        spans = ex(probs, probs.argmax(0))
        gts = [(c, m) for c, m in zip(z["gt_classes"].tolist(), z["gt_masks"], strict=True) if c != "Vegetation"]
        feats = features(spans, gts, sp.ground_mask(lab))
        with open(a.out, "a") as fh:
            for k, r in enumerate(feats):
                fh.write(json.dumps({"image": stem, "span": k, **r}) + "\n")
        print(stem[-10:], len(spans), "spans", sum(r["tp"] for r in feats), "tp", flush=True)


RULES = {
    "ground>=0.5": lambda r: r["ground_share"] >= 0.5,
    "ground>=0.8": lambda r: r["ground_share"] >= 0.8,
    "loop_holes>=1": lambda r: r["loop_holes"] >= 1,
    "bulge_up>=3px&>=2%": lambda r: r["bulge_up_px"] >= 3 and r["bulge_up_px"] >= 0.02 * max(r["length_px"], 1),
    "bulge_up>=5px&>=4%": lambda r: r["bulge_up_px"] >= 5 and r["bulge_up_px"] >= 0.04 * max(r["length_px"], 1),
    "isolated>=40&below_net>=40": lambda r: r["isolation_px"] >= 40 and r["below_net_px"] >= 40,
    "isolated>=80&below_net>=80": lambda r: r["isolation_px"] >= 80 and r["below_net_px"] >= 80,
}


def analyse(a) -> None:
    from ml.experiments.harness import load
    rows = [json.loads(x) for x in open(a.analyse)]
    by = {}
    for r in rows:
        by.setdefault(r["image"], []).append(r)
    ex = make(FINAL)
    cache = {}
    for f, z in load(a.cache):
        probs = z["probs"].astype(np.float32)
        cache[f] = (ex(probs, probs.argmax(0)), z)

    def tol_map(drop):
        imgs = []
        for stem, (spans, z) in cache.items():
            feats = by.get(stem, [])
            keep = [s for k, s in enumerate(spans) if not (k < len(feats) and drop(feats[k]))]
            preds = [ev.Instance("Vegetation", widen(m), float(s)) for m, s in zip(z["veg_masks"], z["veg_scores"], strict=True)]
            preds += [ev.Instance(c, widen(m), s) for c, m, s in keep]
            gts = [ev.Instance(c, widen(m)) for c, m in zip(z["gt_classes"].tolist(), z["gt_masks"], strict=True)]
            imgs.append((preds, gts))
        r = ev.evaluate_segmentation(imgs, 0.2)
        return r.map50

    base = tol_map(lambda r: False)
    out = {"spans": len(rows), "true_positive_spans": sum(r["tp"] for r in rows), "baseline_tolerant_map50": base,
           "rules": {}}
    for name, rule in RULES.items():
        hit = [r for r in rows if rule(r)]
        res = {"removed": len(hit), "removed_true_positive": sum(r["tp"] for r in hit),
               "tolerant_map50": tol_map(rule)}
        out["rules"][name] = res
        print(f"{name:28s} removed {res['removed']:3d} (TP {res['removed_true_positive']}) tol mAP50 {res['tolerant_map50']:.4f}"
              f" (base {base:.4f})", flush=True)
    a.summary.write_text(json.dumps(out, indent=1))
    _ = CLASSES


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path("runs/cache/val-v28"))
    ap.add_argument("--out", type=Path)
    ap.add_argument("--analyse", type=Path)
    ap.add_argument("--summary", type=Path)
    a = ap.parse_args()
    if a.analyse:
        analyse(a)
    else:
        run(a)


if __name__ == "__main__":
    main()
