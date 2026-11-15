"""Section 3.10-3.11: Equations 5 and 12-18, AP/mAP, weighted kappa, bootstrap."""

import numpy as np
import pytest
from scipy import stats

from app.pipeline import evaluation as ev


def box_mask(x0, y0, x1, y1, shape=(50, 50)):
    m = np.zeros(shape, bool)
    m[y0:y1, x0:x1] = True
    return m


def test_mask_iou_equation_5():
    a, b = box_mask(0, 0, 10, 10), box_mask(5, 0, 15, 10)
    assert ev.mask_iou(a, b) == pytest.approx(50 / 150)
    assert ev.mask_iou(a, a) == 1.0 and ev.mask_iou(a, box_mask(20, 20, 30, 30)) == 0.0


def test_prf_equations_12_to_14_at_operating_threshold():
    gt = [ev.Instance("telecom-cable", box_mask(0, 0, 10, 10)), ev.Instance("telecom-cable", box_mask(20, 20, 30, 30))]
    pred = [ev.Instance("telecom-cable", box_mask(0, 0, 10, 10), 0.9),      # TP
            ev.Instance("telecom-cable", box_mask(40, 40, 50, 50), 0.8),    # FP
            ev.Instance("telecom-cable", box_mask(20, 20, 30, 30), 0.1)]    # below threshold -> FN
    rep = ev.evaluate_segmentation([(pred, gt)], conf_threshold=0.5)
    c = rep.per_class["telecom-cable"]
    assert (c.tp, c.fp, c.fn) == (1, 1, 1)
    assert c.precision == pytest.approx(0.5) and c.recall == pytest.approx(0.5) and c.f1 == pytest.approx(0.5)
    assert rep.per_class["abandoned-wire"].ap50 is None  # no ground truth -> excluded from mAP


def test_perfect_predictions_give_map_one():
    gt = [ev.Instance(c, box_mask(i * 10, 0, i * 10 + 8, 8)) for i, c in enumerate(ev.CLASSES)]
    pred = [ev.Instance(g.cls, g.mask, 0.9) for g in gt]
    rep = ev.evaluate_segmentation([(pred, gt)], 0.5)
    assert rep.map50 == pytest.approx(1.0) and rep.map50_95 == pytest.approx(1.0)
    assert rep.targets["map50"] is True


def test_average_precision_known_curve():
    # ranked: TP, FP, TP with 2 GT -> precision envelope 1.0 up to R=0.5, 2/3 up to R=1.0
    ap = ev.average_precision([0.9, 0.8, 0.7], [True, False, True], 2)
    expected = (51 * 1.0 + 50 * (2 / 3)) / 101
    assert ap == pytest.approx(expected)


def test_crossing_wires_match_one_to_one():
    a, b = box_mask(0, 20, 50, 24), box_mask(23, 0, 27, 50)  # a horizontal and a vertical wire
    gt = [ev.Instance("telecom-cable", a), ev.Instance("telecom-cable", b)]
    pred = [ev.Instance("telecom-cable", a | b, 0.9)]        # one mask covering both
    c = ev.evaluate_segmentation([(pred, gt)], 0.5).per_class["telecom-cable"]
    assert (c.tp, c.fp, c.fn) == (1, 0, 1)


def test_agreement_equations_15_to_18_match_scipy():
    rng = np.random.default_rng(0)
    x = rng.random(40); y = x + rng.normal(0, 0.2, 40)
    assert ev.rmse(x, y) == pytest.approx(np.sqrt(np.mean((x - y) ** 2)))
    assert ev.mae(x, y) == pytest.approx(np.mean(np.abs(x - y)))
    assert ev.pearson_r(x, y) == pytest.approx(stats.pearsonr(x, y).statistic)
    ties = np.round(y, 1)
    assert ev.spearman_rho(x, ties) == pytest.approx(stats.spearmanr(x, ties).statistic)
    assert ev.kendall_tau(x, ties) == pytest.approx(stats.kendalltau(x, ties).statistic)


def test_weighted_kappa():
    ref = ["low"] * 5 + ["medium"] * 5 + ["high"] * 5
    assert ev.weighted_kappa(ref, ref) == pytest.approx(1.0)
    # hand-computed linear-weighted kappa for a single adjacent disagreement
    pred = ref[:]; pred[0] = "medium"
    o = ev.confusion(ref, pred).astype(float)
    w = np.abs(np.subtract.outer(np.arange(3), np.arange(3)))
    e = np.outer(o.sum(1), o.sum(0)) / o.sum()
    assert ev.weighted_kappa(ref, pred) == pytest.approx(1 - (w * o).sum() / (w * e).sum())


def test_bootstrap_ci_contains_estimate_and_narrows_with_n():
    rng = np.random.default_rng(1)
    x = rng.random(200); y = 0.8 * x + rng.normal(0, 0.1, 200)
    lo, hi = ev.bootstrap_ci(x, y, ev.pearson_r, n_resamples=2000)
    assert lo < ev.pearson_r(x, y) < hi
    lo20, hi20 = ev.bootstrap_ci(x[:20], y[:20], ev.pearson_r, n_resamples=2000)
    assert (hi20 - lo20) > (hi - lo)


def test_hypothesis_helpers_and_targets():
    h3 = ev.evaluate_h3([1, 4, 7, 9], [1.5, 4.5, 6.5, 8.0], n_resamples=500)
    assert h3["rmse"]["value"] == pytest.approx(ev.rmse([1, 4, 7, 9], [1.5, 4.5, 6.5, 8.0]))
    assert h3["meets_target"] is True and h3["confusion"]["rows_reference_cols_crs"][2][2] == 2
    h4 = ev.evaluate_h4([1, 2, 3, 4, 5], [1, 2, 3, 5, 4], n_resamples=500)
    assert h4["spearman_rho"]["value"] == pytest.approx(0.9) and h4["meets_target"] is True
    assert ev.meets("h1_pearson_r", 0.69) is False and ev.meets("h3_rmse", None) is None
