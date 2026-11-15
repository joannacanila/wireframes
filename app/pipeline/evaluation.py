"""Evaluation metrics and target checks (Sections 3.10 and 3.11, Equations 5 and 12-18).

Segmentation (test set, mask level)
    IoU(A, B)  = |A n B| / |A u B|                                    (5)
    Precision  = TP / (TP + FP)                                       (12)
    Recall     = TP / (TP + FN)                                       (13)
    F1         = 2 P R / (P + R)                                      (14)
    AP         area under the class precision-recall curve (101-point interpolation,
               the COCO convention); mAP@50 and mAP@50:95 over the four classes.

Agreement with reference assessments (Section 3.9)
    RMSE  = sqrt(mean((x - y)^2))                                     (15)  H3
    MAE   = mean(|x - y|)                                             (16)  H3
    r     = Pearson correlation                                       (17)  H1
    rho   = r(rank(x), rank(y))                                       (18)  H1, H4
    weighted Cohen's kappa + confusion matrix on Low/Medium/High      (Table 3.11, H3)
    95% CIs by percentile bootstrap, 10,000 resamples, at image/pole level (Section 3.11).

Supplementary (not in Chapter III, reported separately and labelled as such)
    Kendall's tau-b for H4, and geometric fit accuracy against known curves (curve RMSE in
    pixels, relative sag-ratio error), used for the synthetic benchmark of the geometric layer.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import stats

from app.core.config import CLASSES

LEVELS = ("low", "medium", "high")
IOU_THRESHOLDS = tuple(np.round(np.arange(0.50, 0.951, 0.05), 2))

# Table 3.10 (fixed before any experiment)
TARGETS: dict[str, dict[str, Any]] = {
    "map50": {"op": ">=", "value": 0.70, "applies_to": "YOLO11-seg masks, test set"},
    "precision_per_class": {"op": ">=", "value": 0.70, "applies_to": "each of the four classes"},
    "recall_per_class": {"op": ">=", "value": 0.65, "applies_to": "each of the four classes"},
    "f1_per_class": {"op": ">=", "value": 0.67, "applies_to": "each of the four classes"},
    "h1_pearson_r": {"op": ">=", "value": 0.70, "applies_to": "VNI vs reference clutter rating"},
    "h3_rmse": {"op": "<=", "value": 1.00, "applies_to": "CRS vs reference severity rating"},
    "h4_spearman_rho": {"op": ">=", "value": 0.70, "applies_to": "MSPI rank vs reference rank (20 poles)"},
}


def meets(metric: str, value: float | None) -> bool | None:
    if value is None or not np.isfinite(value):
        return None
    t = TARGETS[metric]
    return bool(value >= t["value"]) if t["op"] == ">=" else bool(value <= t["value"])


# --------------------------------------------------------------------------- segmentation


@dataclass(frozen=True)
class Instance:
    cls: str
    mask: np.ndarray  # bool H x W
    score: float = 1.0


def mask_iou(a: np.ndarray, b: np.ndarray) -> float:
    """Equation 5."""
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    return float(inter / union) if union else 0.0


def iou_matrix(preds: Sequence[Instance], gts: Sequence[Instance]) -> np.ndarray:
    if not preds or not gts:
        return np.zeros((len(preds), len(gts)))
    p = np.stack([x.mask.ravel() for x in preds]).astype(np.float32)
    g = np.stack([x.mask.ravel() for x in gts]).astype(np.float32)
    inter = p @ g.T
    union = p.sum(1)[:, None] + g.sum(1)[None, :] - inter
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(union > 0, inter / union, 0.0)


def _match(preds: Sequence[Instance], gts: Sequence[Instance], thr: float) -> tuple[list[bool], list[float]]:
    """Greedy matching in descending score order (COCO). Returns per-pred TP flags and matched IoUs."""
    order = np.argsort([-p.score for p in preds], kind="stable")
    ious = iou_matrix([preds[i] for i in order], gts)
    taken = np.zeros(len(gts), bool)
    tp, matched = [False] * len(preds), []
    for rank, i in enumerate(order):
        if not len(gts):
            break
        cand = np.where(taken, -1.0, ious[rank])
        j = int(np.argmax(cand))
        if cand[j] >= thr:
            taken[j] = True
            tp[i] = True
            matched.append(float(cand[j]))
    return tp, matched


def average_precision(scores: Sequence[float], tp: Sequence[bool], n_gt: int) -> float:
    """101-point interpolated AP (COCO). AP is NaN when the class has no ground truth."""
    if n_gt == 0:
        return float("nan")
    if not len(scores):
        return 0.0
    order = np.argsort(-np.asarray(scores), kind="stable")
    hits = np.asarray(tp, dtype=float)[order]
    ctp, cfp = np.cumsum(hits), np.cumsum(1 - hits)
    recall = ctp / n_gt
    precision = ctp / np.maximum(ctp + cfp, 1e-12)
    precision = np.maximum.accumulate(precision[::-1])[::-1]  # monotone envelope
    grid = np.linspace(0, 1, 101)
    idx = np.searchsorted(recall, grid, side="left")
    return float(np.mean([precision[i] if i < len(precision) else 0.0 for i in idx]))


@dataclass
class ClassResult:
    n_gt: int
    n_pred: int
    tp: int
    fp: int
    fn: int
    precision: float | None
    recall: float | None
    f1: float | None
    ap50: float | None
    ap50_95: float | None
    mean_mask_iou: float | None


@dataclass
class SegmentationReport:
    per_class: dict[str, ClassResult]
    map50: float | None
    map50_95: float | None
    conf_threshold: float
    n_images: int
    targets: dict[str, Any] = field(default_factory=dict)


def _prf(tp: int, fp: int, fn: int) -> tuple[float | None, float | None, float | None]:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    if p is None or r is None:
        return p, r, None
    return p, r, (2 * p * r / (p + r) if p + r > 0 else 0.0)


def evaluate_segmentation(
    images: Sequence[tuple[Sequence[Instance], Sequence[Instance]]],
    conf_threshold: float,
    classes: Sequence[str] = CLASSES,
) -> SegmentationReport:
    """images: per image (predictions, ground truth). P/R/F1 at ``conf_threshold`` and IoU 0.50
    (Equations 12-14); AP over all scores; matching is per class within each image."""
    per: dict[str, ClassResult] = {}
    for c in classes:
        n_gt = n_pred = tp_n = 0
        ious_at_50: list[float] = []
        scores_all: list[float] = []
        tp_by_thr = {t: [] for t in IOU_THRESHOLDS}
        for preds, gts in images:
            pc = [p for p in preds if p.cls == c]
            gc = [g for g in gts if g.cls == c]
            n_gt += len(gc)
            # AP: every prediction, all thresholds
            for t in IOU_THRESHOLDS:
                flags, _ = _match(pc, gc, t)
                tp_by_thr[t].extend(flags)
            scores_all.extend(p.score for p in pc)
            # P/R/F1 at the operating threshold
            op = [p for p in pc if p.score >= conf_threshold]
            n_pred += len(op)
            flags, matched = _match(op, gc, 0.50)
            tp_n += sum(flags)
            ious_at_50.extend(matched)
        fp, fn = n_pred - tp_n, n_gt - tp_n
        p, r, f = _prf(tp_n, fp, fn)
        aps = [average_precision(scores_all, tp_by_thr[t], n_gt) for t in IOU_THRESHOLDS]
        ap50 = aps[0] if n_gt else None
        per[c] = ClassResult(n_gt, n_pred, tp_n, fp, fn, p, r, f, ap50,
                             float(np.mean(aps)) if n_gt else None,
                             float(np.mean(ious_at_50)) if ious_at_50 else None)
    with_gt = [v for v in per.values() if v.n_gt]
    map50 = float(np.mean([v.ap50 for v in with_gt])) if with_gt else None
    map5095 = float(np.mean([v.ap50_95 for v in with_gt])) if with_gt else None
    rep = SegmentationReport(per, map50, map5095, conf_threshold, len(images))
    rep.targets = {
        "map50": meets("map50", map50),
        "precision_per_class": {c: meets("precision_per_class", v.precision) for c, v in per.items()},
        "recall_per_class": {c: meets("recall_per_class", v.recall) for c, v in per.items()},
        "f1_per_class": {c: meets("f1_per_class", v.f1) for c, v in per.items()},
    }
    return rep


# --------------------------------------------------------------------------- agreement


def rmse(x: Sequence[float], y: Sequence[float]) -> float:
    """Equation 15."""
    a, b = np.asarray(x, float), np.asarray(y, float)
    return float(np.sqrt(np.mean((a - b) ** 2)))


def mae(x: Sequence[float], y: Sequence[float]) -> float:
    """Equation 16."""
    return float(np.mean(np.abs(np.asarray(x, float) - np.asarray(y, float))))


def pearson_r(x: Sequence[float], y: Sequence[float]) -> float:
    """Equation 17, written out."""
    a, b = np.asarray(x, float), np.asarray(y, float)
    da, db = a - a.mean(), b - b.mean()
    den = np.sqrt((da**2).sum() * (db**2).sum())
    return float((da * db).sum() / den) if den > 0 else float("nan")


def spearman_rho(x: Sequence[float], y: Sequence[float]) -> float:
    """Equation 18: Pearson correlation of average ranks (ties share their mean rank)."""
    return pearson_r(stats.rankdata(x), stats.rankdata(y))


def kendall_tau(x: Sequence[float], y: Sequence[float]) -> float:
    """Supplementary: Kendall's tau-b (not specified in Chapter III)."""
    return float(stats.kendalltau(x, y, variant="b").statistic)


def confusion(ref: Sequence[str], pred: Sequence[str], labels: Sequence[str] = LEVELS) -> np.ndarray:
    idx = {k: i for i, k in enumerate(labels)}
    m = np.zeros((len(labels), len(labels)), int)
    for a, b in zip(ref, pred, strict=True):
        m[idx[a], idx[b]] += 1
    return m


def weighted_kappa(ref: Sequence[str], pred: Sequence[str], labels: Sequence[str] = LEVELS,
                   weights: str = "linear") -> float:
    """Cohen's weighted kappa: 1 - sum(w O) / sum(w E), w_ij = |i-j| (linear) or (i-j)^2."""
    o = confusion(ref, pred, labels).astype(float)
    n = o.sum()
    if n == 0:
        return float("nan")
    k = len(labels)
    i, j = np.meshgrid(np.arange(k), np.arange(k), indexing="ij")
    w = np.abs(i - j) if weights == "linear" else (i - j) ** 2
    e = np.outer(o.sum(1), o.sum(0)) / n
    den = (w * e).sum()
    return float(1 - (w * o).sum() / den) if den > 0 else float("nan")


def bootstrap_ci(x: Sequence[float], y: Sequence[float], fn: Callable[[np.ndarray, np.ndarray], float],
                 n_resamples: int = 10_000, seed: int = 0, level: float = 0.95) -> tuple[float, float]:
    """Percentile CI, resampling (x_i, y_i) pairs (images or poles) with replacement."""
    a, b = np.asarray(x, float), np.asarray(y, float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(n_resamples, len(a)))
    vals = np.array([fn(a[k], b[k]) for k in idx])
    vals = vals[np.isfinite(vals)]
    lo, hi = np.quantile(vals, [(1 - level) / 2, 1 - (1 - level) / 2])
    return float(lo), float(hi)


def _with_ci(name: str, x, y, fn, n_resamples: int, seed: int) -> dict[str, Any]:
    v = fn(np.asarray(x, float), np.asarray(y, float))
    lo, hi = bootstrap_ci(x, y, fn, n_resamples, seed) if len(x) >= 3 else (None, None)
    return {"metric": name, "value": v, "ci95": [lo, hi], "n": len(x)}


def evaluate_h1(vni: Sequence[float], clutter: Sequence[float], n_resamples: int = 10_000, seed: int = 0) -> dict:
    r = _with_ci("pearson_r", vni, clutter, pearson_r, n_resamples, seed)
    rho = _with_ci("spearman_rho", vni, clutter, spearman_rho, n_resamples, seed)
    return {"pearson_r": r, "spearman_rho": rho, "meets_target": meets("h1_pearson_r", r["value"])}


def evaluate_h3(crs: Sequence[float], severity: Sequence[float], levels_cut=(3.0, 6.0),
                n_resamples: int = 10_000, seed: int = 0) -> dict:
    """CRS vs reference severity (0-10). Reference levels use the same cut-offs as the CRS."""
    from app.core.config import Levels
    from app.pipeline.crs import risk_level

    lv = Levels(low_below=levels_cut[0], high_above=levels_cut[1])
    ref_lv = [risk_level(float(s), lv) for s in severity]
    pred_lv = [risk_level(float(c), lv) for c in crs]
    e = _with_ci("rmse", crs, severity, rmse, n_resamples, seed)
    return {
        "rmse": e, "mae": _with_ci("mae", crs, severity, mae, n_resamples, seed),
        "weighted_kappa_linear": weighted_kappa(ref_lv, pred_lv, weights="linear"),
        "confusion": {"labels": list(LEVELS), "rows_reference_cols_crs": confusion(ref_lv, pred_lv).tolist()},
        "meets_target": meets("h3_rmse", e["value"]),
    }


def evaluate_h4(mspi_rank: Sequence[float], ref_rank: Sequence[float], n_resamples: int = 10_000, seed: int = 0) -> dict:
    rho = _with_ci("spearman_rho", mspi_rank, ref_rank, spearman_rho, n_resamples, seed)
    tau = _with_ci("kendall_tau_b", mspi_rank, ref_rank, kendall_tau, n_resamples, seed)
    return {"spearman_rho": rho, "kendall_tau_b_supplementary": tau,
            "meets_target": meets("h4_spearman_rho", rho["value"]),
            "note": "n = 20 gives a wide interval; reported as indicative (Section 3.9)."}


# --------------------------------------------------------------------------- geometry (supplementary)


def curve_rmse_px(fit_xy: np.ndarray, true_fn: Callable[[np.ndarray], np.ndarray]) -> float:
    """Vertical RMSE between a fitted curve's samples and the known curve at the same x."""
    xy = np.asarray(fit_xy, float)
    return float(np.sqrt(np.mean((xy[:, 1] - true_fn(xy[:, 0])) ** 2)))


def relative_error(est: float, truth: float) -> float:
    return abs(est - truth) / abs(truth) if truth else float("nan")


def summarize(values: Sequence[float]) -> dict[str, float | None]:
    a = np.asarray([v for v in values if v is not None and np.isfinite(v)], float)
    if not a.size:
        return {"n": 0, "mean": None, "median": None, "p95": None, "max": None}
    return {"n": int(a.size), "mean": float(a.mean()), "median": float(np.median(a)),
            "p95": float(np.quantile(a, 0.95)), "max": float(a.max())}
