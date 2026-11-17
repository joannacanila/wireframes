"""v2.17 centerline metrics for thin wires: precision / recall / F1 / IoU / Dice that measure what the screener claims.

Mask-IoU mAP@50 (Part 5) asks a 2-4 px wide predicted mask to overlap a hand-drawn polygon by 50 %, so a wire found
1-2 px off-centre counts as a miss; it stays in every table, unchanged, but it cannot answer "what share of the
wire the screener draws is really a wire, and what share of the real wires does it find". Centerline metrics
(the standard for thin structures: road and vessel extraction, clDice) answer exactly that:

    precision  = share of the predicted centerline lying within ``tol`` px of a labelled wire of that class
    recall     = share of the labelled centerline lying within ``tol`` px of a predicted wire of that class
    F1         = harmonic mean;  clDice = 2 Tprec Tsens / (Tprec + Tsens) with the same skeletons (Shit et al. 2021)

``tol`` is 3 px per 1024 px of the long side (the label polygons are hand-drawn at roughly that accuracy).

Rules that keep the numbers honest
    * "wire" (detection) uses every reported wire, uncertain ones included; a class metric uses only wires with a
      confident class, so an uncertain wire can never count as a correct Power or Telecom finding (it costs recall)
    * false positives are never dropped: every predicted pixel is in the precision denominator
    * the labels are known to omit wires (Part 12, item 13), so precision is a LOWER bound: a real but unlabelled
      wire the screener finds counts against it
    * counts are summed over images (micro average); 95 % intervals resample whole images (bootstrap)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from skimage.morphology import skeletonize

WIRE_CLASSES = ("power-distribution-lines", "telecom-cable", "abandoned-wire")


@dataclass
class Pred:
    wire_class: str
    mask: np.ndarray  # bool, image size
    confident: bool = True  # False = "type uncertain"


@dataclass
class Counts:
    """Pixel counts for one image and one target ("wire" or a class)."""
    p_hit: int = 0  # predicted skeleton px near a label
    p_all: int = 0
    g_hit: int = 0  # labelled skeleton px near a prediction
    g_all: int = 0
    inter: int = 0  # predicted pixels within tol of a label (tolerant overlap, prediction side)
    inter_g: int = 0  # labelled pixels within tol of a prediction (tolerant overlap, label side)
    p_area: int = 0
    g_area: int = 0

    def __iadd__(self, o: Counts) -> Counts:
        for k in self.__dataclass_fields__:
            setattr(self, k, getattr(self, k) + getattr(o, k))
        return self


def tolerance(shape) -> float:
    return 3.0 * max(shape) / 1024.0


def _skel(m: np.ndarray) -> np.ndarray:
    return skeletonize(m) if m.any() else m


def _near(m: np.ndarray, tol: float) -> np.ndarray:
    if not m.any():
        return np.zeros_like(m, bool)
    return cv2.distanceTransform((~m).astype(np.uint8), cv2.DIST_L2, 3) <= tol


def counts(pred: np.ndarray, gt: np.ndarray, tol: float) -> Counts:
    sp, sg = _skel(pred), _skel(gt)
    ng, np_ = _near(gt, tol), _near(pred, tol)
    return Counts(p_hit=int((sp & ng).sum()), p_all=int(sp.sum()), g_hit=int((sg & np_).sum()), g_all=int(sg.sum()),
                  inter=int((pred & ng).sum()), inter_g=int((gt & np_).sum()), p_area=int(pred.sum()), g_area=int(gt.sum()))


def prf(c: Counts) -> dict:
    p = c.p_hit / c.p_all if c.p_all else None
    r = c.g_hit / c.g_all if c.g_all else None
    f = 2 * p * r / (p + r) if p is not None and r is not None and p + r > 0 else (0.0 if p is not None and r is not None else None)
    # tolerant (relaxed) pixel Dice / IoU, symmetric: matched pixels on both sides over all pixels, so both stay in
    # [0, 1] (v2.17 fix: the first version counted only the prediction side against a label-sized union, > 1)
    tot = c.p_area + c.g_area
    dice = (c.inter + c.inter_g) / tot if tot else None
    return {"precision": p, "recall": r, "f1": f, "iou": dice / (2 - dice) if dice is not None else None,
            "dice": dice,
            "pred_px": c.p_all, "label_px": c.g_all}


def image_counts(preds: list[Pred], gts: list[tuple[str, np.ndarray]], shape) -> dict[str, Counts]:
    tol = tolerance(shape)
    z = np.zeros(shape, bool)
    out = {}
    pw = z.copy()
    gw = z.copy()
    for p in preds:
        pw |= p.mask
    for c, m in gts:
        if c in WIRE_CLASSES:
            gw |= m
    out["wire"] = counts(pw, gw, tol)
    for cls in WIRE_CLASSES:
        pc, gc = z.copy(), z.copy()
        for p in preds:
            if p.confident and p.wire_class == cls:
                pc |= p.mask
        for c, m in gts:
            if c == cls:
                gc |= m
        out[cls] = counts(pc, gc, tol)
    return out


@dataclass
class StrandTally:
    """Strand-level classification: each reported wire whose centerline lies mostly on labelled wire is a case."""
    confusion: dict = field(default_factory=dict)  # (true class, predicted class or "uncertain") -> n
    unmatched: int = 0  # reported wires not on any labelled wire (cannot be graded for class)

    def add(self, preds: list[Pred], gts: list[tuple[str, np.ndarray]], shape, min_on: float = 0.5) -> None:
        tol = tolerance(shape)
        near = {c: _near(np.any([m for k, m in gts if k == c], axis=0) if any(k == c for k, _ in gts)
                         else np.zeros(shape, bool), tol) for c in WIRE_CLASSES}
        for p in preds:
            s = _skel(p.mask)
            n = int(s.sum())
            if not n:
                continue
            on = {c: int((s & near[c]).sum()) for c in WIRE_CLASSES}
            truth = max(on, key=on.get)
            if sum(on.values()) == 0 or max(on.values()) < min_on * n:
                self.unmatched += 1
                continue
            key = (truth, p.wire_class if p.confident else "uncertain")
            self.confusion[key] = self.confusion.get(key, 0) + 1

    def report(self) -> dict:
        out = {"matched": sum(self.confusion.values()), "unmatched": self.unmatched,
               "confusion": {f"{t} -> {p}": n for (t, p), n in sorted(self.confusion.items())}}
        n_all = sum(self.confusion.values())
        n_unc = sum(n for (t, p), n in self.confusion.items() if p == "uncertain")
        n_ok = sum(n for (t, p), n in self.confusion.items() if t == p)
        out["accuracy"] = n_ok / n_all if n_all else None  # uncertain counts as not correct
        out["uncertain_rate"] = n_unc / n_all if n_all else None
        for c in WIRE_CLASSES:
            tp = self.confusion.get((c, c), 0)
            n_pred = sum(n for (t, p), n in self.confusion.items() if p == c)
            n_true = sum(n for (t, p), n in self.confusion.items() if t == c)
            pr = tp / n_pred if n_pred else None
            rc = tp / n_true if n_true else None
            f = 2 * pr * rc / (pr + rc) if pr and rc else (0.0 if pr is not None and rc is not None else None)
            out[c] = {"precision": pr, "recall": rc, "f1": f, "n_pred": n_pred, "n_true": n_true}
        return out


def summarize(per_image: list[dict[str, Counts]], n_boot: int = 2000, seed: int = 0) -> dict:
    """Micro-averaged metrics per target with 95 % image-bootstrap intervals for precision, recall and F1."""
    rng = np.random.default_rng(seed)
    targets = per_image[0].keys() if per_image else []
    out = {}
    for t in targets:
        tot = Counts()
        for im in per_image:
            tot += im[t]
        r = prf(tot)
        boots = {"precision": [], "recall": [], "f1": []}
        for _ in range(n_boot):
            b = Counts()
            for i in rng.integers(0, len(per_image), len(per_image)):
                b += per_image[i][t]
            x = prf(b)
            for k in boots:
                if x[k] is not None:
                    boots[k].append(x[k])
        for k, v in boots.items():
            r[f"{k}_ci95"] = [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] if v else None
        out[t] = r
    out["images"] = len(per_image)
    return out
