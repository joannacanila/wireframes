"""Section 3.6.5: Equations 6-10, Algorithm 5."""

import math

import cv2
import numpy as np
import pytest

from app.pipeline import catenary as cat
from app.pipeline.crs import catenary_risk_score, risk_level
from app.pipeline.sag_limits import (
    EngineeringContext,
    allowable_sag,
    catenary_sag_ratio,
    change_of_state,
    wind_load,
)
from app.pipeline.skeleton import extract_centerline


def hanging_points(a, b, x0, x1, y_attach=100.0, tilt=0.0, n=400, noise=0.0, seed=0):
    xs = np.linspace(x0, x1, n)
    yu = a * np.cosh((xs - b) / a) + tilt * xs
    y = y_attach + (yu[0] - yu)
    y += np.random.default_rng(seed).normal(0, noise, n)
    return np.stack([xs, y], 1)


def truth_ratio(a, b, x0, x1):
    length, delta, _, _ = cat._chord_sag(a, b, 0.0, x0, x1)
    return delta / length


def test_parabola_start_recovers_catenary_vertex():
    x = np.linspace(-0.5, 0.5, 200)
    a, b, c = 3.0, 0.1, -2.0
    a0, b0, c0 = cat.parabola_start(x, cat.catenary(x, a, b, c))
    assert a0 == pytest.approx(a, rel=0.02) and b0 == pytest.approx(b, abs=0.01)


@pytest.mark.parametrize("a,b", [(900, 820), (2500, 700), (6000, 900), (400, 500)])
def test_lm_fit_recovers_parameters_and_sag_ratio(a, b):
    pts = hanging_points(a, b, 100, 1500, y_attach=300, noise=0.3)
    f = cat.fit_catenary(pts, image_size=(1600, 2000))
    assert f.ok, f.reason
    assert f.a == pytest.approx(a, rel=0.02)
    assert f.b == pytest.approx(b, abs=5)
    assert f.sag_ratio == pytest.approx(truth_ratio(a, b, 100, 1500), rel=0.01)


def test_closed_form_sag_matches_dense_search():
    a, b, c = 700.0, 600.0, 0.0
    xa, xb = 100.0, 1400.0
    length, delta, xs, _ = cat._chord_sag(a, b, c, xa, xb)
    xx = np.linspace(xa, xb, 400_001)
    yy = cat.catenary(xx, a, b, c)
    ya, yb = cat.catenary(xa, a, b, c), cat.catenary(xb, a, b, c)
    d = np.abs((xb - xa) * (ya - yy) - (xa - xx) * (yb - ya)) / length
    assert delta == pytest.approx(d.max(), rel=1e-7)
    assert xs == pytest.approx(xx[d.argmax()], abs=0.01)


def test_sag_ratio_is_scale_invariant():
    pts = hanging_points(1200, 800, 100, 1500)
    f1 = cat.fit_catenary(pts, image_size=(4000, 4000))
    f2 = cat.fit_catenary(pts * 2.5, image_size=(10000, 10000))
    assert f1.sag_ratio == pytest.approx(f2.sag_ratio, rel=1e-6)


def test_vertical_flip_arching_curve_is_not_a_hanging_wire():
    pts = hanging_points(900, 800, 100, 1500)
    pts[:, 1] = 1000 - pts[:, 1]  # arch
    f = cat.fit_catenary(pts, image_size=(1600, 2000))
    assert not f.ok and f.reason in {"non_positive_a", "residual_too_large", "no_convergence"}


def test_residual_limit_rejects_non_catenary_shape():
    xs = np.linspace(0, 1000, 300)
    pts = np.stack([xs, 400 + 60 * np.sin(xs / 80)], 1)
    f = cat.fit_catenary(pts, image_size=(1200, 1200), max_rel_residual=0.01)
    assert not f.ok


def test_truncation_flag_when_endpoint_touches_border():
    pts = hanging_points(900, 800, 0, 1500, y_attach=300)
    assert cat.fit_catenary(pts, image_size=(1600, 2000)).truncated
    pts = hanging_points(900, 800, 100, 1500, y_attach=300)
    assert not cat.fit_catenary(pts, image_size=(1600, 2000)).truncated


def test_full_geometric_path_from_rasterised_mask():
    a, b = 3000.0, 800.0
    pts = hanging_points(a, b, 120, 1480, y_attach=200, n=3000)
    m = np.zeros((900, 1600), np.uint8)
    cv2.polylines(m, [np.round(pts * 16).astype(np.int32).reshape(-1, 1, 2)], False, 1, 9, cv2.LINE_8, shift=4)
    cl = extract_centerline(m, 30, 20)
    f = cat.fit_catenary(cl.points, image_size=(1600, 900))
    assert f.ok
    assert f.sag_ratio == pytest.approx(truth_ratio(a, b, cl.points[0, 0], cl.points[-1, 0]), rel=0.03)


# ---------------------------------------------------------------- Equation 10 and levels

def test_crs_equation_10():
    assert catenary_risk_score(0.02, 0.04) == pytest.approx(5.0)
    assert catenary_risk_score(0.04, 0.04) == pytest.approx(10.0)  # sag at limit -> 10
    assert catenary_risk_score(0.10, 0.04) == 10.0                 # capped
    assert catenary_risk_score(0.02, 0.04, class_weight=1.5) == pytest.approx(7.5)
    assert catenary_risk_score(0.0, 0.04) == 0.0
    with pytest.raises(ValueError):
        catenary_risk_score(0.01, 0.0)


@pytest.mark.parametrize("crs,level", [(0.0, "low"), (2.999, "low"), (3.0, "medium"), (6.0, "medium"),
                                       (6.0001, "high"), (10.0, "high")])
def test_risk_level_cutoffs_table_1_2(crs, level):
    assert risk_level(crs) == level


# ---------------------------------------------------------------- Equation 9

def test_wind_load_and_catenary_sag_formula():
    assert wind_load(10, 0.01, 1.225, 1.0) == pytest.approx(0.6125)
    h, w, span = 2000.0, 2.0, 40.0
    d = catenary_sag_ratio(h, w, span) * span
    assert d == pytest.approx(w * span**2 / (8 * h), rel=1e-3)  # parabolic limit for shallow sag


def test_change_of_state_identity_and_monotonicity():
    kw = dict(h1=2900.0, w1=2.12, t1=25.0, span=40.0, e=7.6e10, area=6.24e-5, alpha=1.91e-5)
    assert change_of_state(**kw, w2=2.12, t2=25.0) == pytest.approx(2900.0, rel=1e-9)
    hot = change_of_state(**kw, w2=2.12, t2=45.0)
    windy = change_of_state(**kw, w2=math.hypot(2.12, 3.0), t2=25.0)
    assert hot < 2900.0 < windy  # heat slackens, extra load tightens
    # the root satisfies the cubic
    ea = kw["e"] * kw["area"]
    k = kw["h1"] - ea * kw["w1"] ** 2 * kw["span"] ** 2 / (24 * kw["h1"] ** 2) - ea * kw["alpha"] * 20.0
    assert hot**2 * (hot - k) == pytest.approx(ea * 2.12**2 * 40.0**2 / 24, rel=1e-6)


def test_allowable_sag_without_context_is_reference_value(cfg):
    lim = allowable_sag("telecom-cable", EngineeringContext(), cfg.crs, cfg.engineering)
    assert lim.delta_lim == cfg.crs.delta_lim0["telecom-cable"]
    assert "default_parameters" in lim.flags and not lim.used_context


def test_allowable_sag_at_reference_conditions_equals_reference(cfg):
    ctx = EngineeringContext(span_m=40, temperature_c=cfg.engineering.reference_temperature_c, wind_speed_mps=0)
    lim = allowable_sag("power-distribution-lines", ctx, cfg.crs, cfg.engineering)
    assert lim.delta_lim == pytest.approx(lim.delta_lim0)
    hot = allowable_sag("power-distribution-lines", EngineeringContext(40, 45, 0), cfg.crs, cfg.engineering)
    assert hot.delta_lim > lim.delta_lim  # more sag is expected on a hot day, so the limit relaxes
