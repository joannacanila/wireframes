"""v2.17: where is labelled telecom centerline lost, and does a lower wire threshold recover it? (validation caches)

For every labelled telecom skeleton pixel: is a telecom prediction within tol (hit), a power prediction only
(class swap), any wire probability >= 0.2 nearby but below argmax (weak), or nothing (missed)? Then a val-only
sweep of the detection rule "wire if P(any wire) >= t, class = argmax over wire classes" (argmax = t ~ 0.5).

    python -m ml.experiments.v217_telecom_loss --cache runs/cache/val-v216
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from app.core.config import CLASSES, ROOT_DIR, load_analysis_config
from app.pipeline import centerline_eval as ce
from app.pipeline.wire_instances import strand_spans
from app.pipeline.wirenet import span_params
from ml.experiments.v216_strands import WIRE_IDS

TEL = CLASSES.index("telecom-cable") + 1
POW = CLASSES.index("power-distribution-lines") + 1


def thresholded(p: np.ndarray, t: float) -> np.ndarray:
    """class map: 0 bg/veg unless P(any wire) >= t, then the most likely wire class."""
    wire = p[list(WIRE_IDS)].sum(0)
    cls = np.asarray(WIRE_IDS)[p[list(WIRE_IDS)].argmax(0)]
    out = np.where(wire >= t, cls, 0)
    return np.where((out > 0) & (p[1] > wire), 0, out)  # vegetation stays vegetation


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path("runs/cache/val-v216"))
    ap.add_argument("--min-share", type=float, default=0.6)
    a = ap.parse_args()
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml").segmentation
    params = span_params(cfg)
    loss = {"hit": 0, "as_power": 0, "weak": 0, "missed": 0}
    ts = [0.5, 0.4, 0.3, 0.2]  # 0.5 = shipped argmax rule
    per = {t: {"pixel": [], "strands": []} for t in ts}
    for f in sorted(a.cache.glob("*.npz")):
        z = np.load(f)
        p = z["probs"].astype(np.float32)
        gts = [(c, m) for c, m in zip(z["gt_classes"].tolist(), z["gt_masks"], strict=True) if c in ce.WIRE_CLASSES]
        shape, tol = p.shape[1:], ce.tolerance(p.shape[1:])
        am = p.argmax(0)
        gt_tel = np.any([m for c, m in gts if c == "telecom-cable"], axis=0) if any(c == "telecom-cable" for c, _ in gts) else None
        if gt_tel is not None:
            sk = ce._skel(gt_tel)
            near_t, near_p = ce._near(am == TEL, tol), ce._near(am == POW, tol)
            near_w = ce._near(p[list(WIRE_IDS)].sum(0) >= 0.2, tol)
            hit = sk & near_t
            loss["hit"] += int(hit.sum())
            loss["as_power"] += int((sk & ~near_t & near_p).sum())
            loss["weak"] += int((sk & ~near_t & ~near_p & near_w).sum())
            loss["missed"] += int((sk & ~near_t & ~near_p & ~near_w).sum())
        for t in ts:
            cm = thresholded(p, t)
            per[t]["pixel"].append(ce.image_counts([ce.Pred(CLASSES[i - 1], cm == i) for i in WIRE_IDS], gts, shape))
            preds = [ce.Pred(s.wire_class, s.mask, s.class_status != "uncertain")
                     for s in strand_spans(p, WIRE_IDS, params, tau=None if t == 0.5 else t,
                                                   min_class_share=a.min_share)]
            per[t]["strands"].append(ce.image_counts(preds, gts, shape))
        print(f.stem[:40], flush=True)
    n = sum(loss.values())
    out = {"telecom_label_centerline": {k: v / n for k, v in loss.items()}, "sweep": {}}
    print("telecom label centerline:", {k: round(v / n, 3) for k, v in loss.items()})
    fm = lambda m: f"P {m['precision']:.3f} R {m['recall']:.3f} F1 {m['f1']:.3f}"  # noqa: E731
    for t in ts:
        px, st = ce.summarize(per[t]["pixel"], 300), ce.summarize(per[t]["strands"], 300)
        out["sweep"][t] = {"pixel": px, "strands": st}
        print(f"t {t}: pixel tel {fm(px['telecom-cable'])} pow {fm(px['power-distribution-lines'])} | strands tel "
              f"{fm(st['telecom-cable'])} pow {fm(st['power-distribution-lines'])} wire {fm(st['wire'])}", flush=True)
    Path("docs/thesis/results/v217_telecom_loss_val.json").write_text(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
