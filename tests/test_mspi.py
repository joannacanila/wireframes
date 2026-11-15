"""Section 3.6.6, Equation 11, Algorithm 6."""

import pytest

from app.pipeline.mspi import SiteInput, mspi, rank_sites, sensitivity, weight_grid


def test_equation_11():
    assert mspi(0.6, [2.0, 8.0], 0.2, 0.3, 0.5) == pytest.approx(0.2 * 0.6 + 0.3 * 0.5 + 0.5 * 0.8)
    with pytest.raises(ValueError):
        mspi(0.5, [1.0], 0.5, 0.5, 0.5)
    with pytest.raises(ValueError):
        mspi(0.5, [], 1 / 3, 1 / 3, 1 / 3)


def test_ranking_descending_and_unranked_poles_flagged():
    sites = [SiteInput("A", 0.1, [1.0]), SiteInput("B", 0.9, [9.0, 7.0]), SiteInput("C", 0.5, []),
             SiteInput("D", 0.5, [4.0])]
    out = rank_sites(sites, 1 / 3, 1 / 3, 1 / 3)
    assert [r.site_id for r in out] == ["B", "D", "A", "C"]
    assert [r.rank for r in out] == [1, 2, 3, None]
    assert out[0].level == "high" and out[-1].flags == ("NO_VALID_WIRES",)
    assert out[0].mean_crs == 8.0 and out[0].max_crs == 9.0


def test_weight_grid_covers_simplex():
    g = weight_grid(0.1)
    assert len(g) == 66
    assert all(abs(a + b + c - 1) < 1e-12 and min(a, b, c) >= 0 for a, b, c in g)


def test_sensitivity_identical_rankings_give_rho_one():
    # Every term increases together, so every weight setting gives the same order.
    sites = [SiteInput(str(i), i / 10, [float(i)]) for i in range(1, 8)]
    rep = sensitivity(sites, (1 / 3, 1 / 3, 1 / 3))
    assert rep.min_rho == pytest.approx(1.0) and rep.mean_rho == pytest.approx(1.0)
    assert all(lo == hi for lo, hi in rep.rank_range.values())


def test_sensitivity_detects_weight_dependence():
    sites = [SiteInput("cluttered", 0.95, [1.0]), SiteInput("sagging", 0.05, [9.5])]
    rep = sensitivity(sites, (1 / 3, 1 / 3, 1 / 3))
    assert rep.min_rho == pytest.approx(-1.0)
    assert rep.rank_range["cluttered"] == (1, 2)
