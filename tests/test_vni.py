"""Section 3.6.2: Equations 1-4 and Table 3.7."""

import numpy as np
import pytest
from scipy.signal.windows import hann

from app.pipeline import vni


def test_hann_window_is_separable_periodic_outer_product():
    w = vni.hann_2d(64, 48)
    np.testing.assert_allclose(w, np.outer(hann(64, sym=False), hann(48, sym=False)))
    assert w[0, :].max() == 0 and w[:, 0].max() == 0  # tapers to zero at the border


def test_power_spectrum_matches_equations_1_and_2():
    rng = np.random.default_rng(0)
    g = rng.random((32, 32))
    p = vni.power_spectrum(g)
    gw = g * vni.hann_2d(32, 32)
    # Equation 1 by direct summation for one frequency, after fftshift (u, v) -> (u - 16, v - 16)
    u, v = 3, 5
    x = np.arange(32)[:, None]
    y = np.arange(32)[None, :]
    f = np.sum(gw * np.exp(-2j * np.pi * (u * x / 32 + v * y / 32)))
    assert p[u + 16, v + 16] == pytest.approx(abs(f) ** 2, rel=1e-9)


def test_raw_vni_matches_equation_3_by_hand():
    rng = np.random.default_rng(1)
    g = rng.random((64, 64))
    p = vni.power_spectrum(g)
    r = vni.radial_distance(64, 64)
    r_l = 0.15 * 64 / 2
    expected = p[r > r_l].sum() / p[r > 0].sum()
    assert vni.vni_raw(g, 0.15) == pytest.approx(expected)


def test_constant_image_has_zero_raw_vni():
    assert vni.vni_raw(np.full((64, 64), 77.0)) == pytest.approx(0.0, abs=1e-12)


def _stripes(offset=128.0, amp=127.0):
    return (np.sin(np.arange(640) * 2 * np.pi / 4)[None, :] * amp + offset) * np.ones((640, 1))


def test_fine_texture_scores_higher_than_smooth_gradient():
    x = np.linspace(0, 1, 640)
    smooth = np.outer(x, x) * 255
    assert vni.vni_raw(_stripes()) > vni.vni_raw(smooth)
    assert vni.vni_raw(_stripes(), remove_mean=True) > 0.99 > 0.01 > vni.vni_raw(smooth, remove_mean=True)


def test_equation_3_as_written_depends_on_brightness_mean_removal_does_not():
    """Documents the Hann/DC leakage finding (config vni.remove_mean)."""
    dark, bright = _stripes(0, 38), _stripes(150, 38)
    assert vni.vni_raw(dark) > 5 * vni.vni_raw(bright)
    assert vni.vni_raw(dark, remove_mean=True) == pytest.approx(vni.vni_raw(bright, remove_mean=True))


def test_raw_vni_is_invariant_to_intensity_scaling():
    g = np.random.default_rng(2).random((128, 128))
    assert vni.vni_raw(g) == pytest.approx(vni.vni_raw(g * 255.0))


def test_larger_radius_never_increases_raw_vni():
    g = np.random.default_rng(3).random((128, 128))
    vals = [vni.vni_raw(g, f) for f in (0.10, 0.15, 0.20)]
    assert vals[0] >= vals[1] >= vals[2]


@pytest.mark.parametrize("raw,expected,clipped", [(0.0, 0.0, True), (0.5, 0.5, False), (0.9, 1.0, False), (0.95, 1.0, True)])
def test_quantile_normalisation_equation_4(raw, expected, clipped):
    v, c = vni.normalize(raw, 0.1, 0.9)
    assert v == pytest.approx((min(max(raw, 0.1), 0.9) - 0.1) / 0.8)
    assert v == pytest.approx(expected) and c is clipped


@pytest.mark.parametrize("v,band", [(0.0, "Low"), (0.25, "Low"), (0.2501, "Moderate"), (0.5, "Moderate"),
                                    (0.75, "High"), (0.7501, "Severe"), (1.0, "Severe")])
def test_band_upper_bounds_are_inclusive_table_3_7(cfg, v, band):
    assert vni.band_of(v, cfg.vni) == band


def test_calibration_quantiles():
    q01, q99 = vni.calibrate_quantiles(np.linspace(0, 1, 101))
    assert q01 == pytest.approx(0.01) and q99 == pytest.approx(0.99)
    with pytest.raises(ValueError):
        vni.calibrate_quantiles([0.3, 0.3, 0.3])


def test_configured_vni_is_illumination_invariant(cfg):
    """With vni.remove_mean (the shipped setting) VNI_raw is unchanged by I -> a*I + b."""
    assert cfg.vni.remove_mean is True
    rng = np.random.default_rng(7)
    g = rng.random((128, 128)) * 60 + 40
    base = vni.compute_vni(g, cfg.vni).vni_raw
    for a, b in [(0.3, 0.0), (1.0, 120.0), (2.5, -60.0), (0.5, 90.0)]:
        assert vni.compute_vni(a * g + b, cfg.vni).vni_raw == pytest.approx(base, rel=1e-9)
