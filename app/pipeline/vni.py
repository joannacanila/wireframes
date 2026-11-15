"""Visual Noise Index by 2D FFT (Section 3.6.2, Algorithm 2, Equations 1-4).

    I_w(x, y)  = G(x, y) * hann(x) * hann(y)                      (separable 2D Hann window)
    F(u, v)    = sum_x sum_y I_w(x, y) exp[-j 2 pi (ux/M + vy/N)]  (1)
    P(u, v)    = |F(u, v)|^2, after the zero frequency is shifted  (2)
    VNI_raw    = sum_{r > r_L} P / sum_{r > 0} P,                  (3)
                 r_L = radius_factor * min(M, N) / 2
    VNI        = clip((VNI_raw - q01) / (q99 - q01), 0, 1)        (4)

The index describes the complexity of the whole scene. It says nothing about the
condition of any wire.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from scipy.signal.windows import hann

from app.core.config import VNIConfig


@dataclass(frozen=True)
class VNIResult:
    vni_raw: float
    vni: float
    band: str
    radius_factor: float
    clipped: bool  # True when the raw value fell outside [q01, q99]


@lru_cache(maxsize=8)
def hann_2d(m: int, n: int) -> np.ndarray:
    """Outer product of periodic Hann windows (the DFT-even form used for spectral analysis)."""
    w = np.outer(hann(m, sym=False), hann(n, sym=False))
    w.setflags(write=False)
    return w


@lru_cache(maxsize=8)
def radial_distance(m: int, n: int) -> np.ndarray:
    """Distance of every (u, v) from the centre of an fftshift-ed m x n spectrum."""
    u = np.arange(m) - m // 2
    v = np.arange(n) - n // 2
    r = np.hypot(u[:, None], v[None, :])
    r.setflags(write=False)
    return r


def power_spectrum(gray: np.ndarray, remove_mean: bool = False) -> np.ndarray:
    """Centred power spectrum of the Hann-windowed image (Equations 1 and 2).

    ``remove_mean`` subtracts the image mean before windowing (see ``VNIConfig``); without
    it the window leaks DC power into the lowest non-zero frequencies.
    """
    if gray.ndim != 2:
        raise ValueError("power_spectrum expects a 2D grayscale array")
    g = gray.astype(np.float64, copy=False)
    if remove_mean:
        g = g - g.mean()
    m, n = g.shape
    f = np.fft.fftshift(np.fft.fft2(g * hann_2d(m, n)))
    return f.real**2 + f.imag**2


def vni_raw(gray: np.ndarray, radius_factor: float = 0.15, remove_mean: bool = False) -> float:
    """Equation 3: share of non-zero-frequency power beyond r_L."""
    p = power_spectrum(gray, remove_mean)
    m, n = p.shape
    r = radial_distance(m, n)
    r_l = radius_factor * min(m, n) / 2.0
    denom = float(p[r > 0].sum())
    if denom <= 0.0 or not np.isfinite(denom):
        return 0.0  # a constant image has no non-zero-frequency power
    return float(p[r > r_l].sum() / denom)


def normalize(raw: float, q01: float, q99: float) -> tuple[float, bool]:
    """Equation 4. Returns the value and whether it was clipped."""
    z = (raw - q01) / (q99 - q01)
    return float(min(1.0, max(0.0, z))), bool(z < 0.0 or z > 1.0)


def band_of(vni: float, cfg: VNIConfig) -> str:
    """Table 3.7: each band's upper bound is inclusive (0-0.25, >0.25-0.50, ...)."""
    for b in cfg.bands:
        if vni <= b.max:
            return b.label
    return cfg.bands[-1].label


def compute_vni(gray: np.ndarray, cfg: VNIConfig) -> VNIResult:
    raw = vni_raw(gray, cfg.radius_factor, cfg.remove_mean)
    v, clipped = normalize(raw, cfg.q01, cfg.q99)
    return VNIResult(raw, v, band_of(v, cfg), cfg.radius_factor, clipped)


def spectrum_image(gray: np.ndarray) -> np.ndarray:
    """log(1 + P) scaled to uint8, with the low-frequency radius drawn by the caller."""
    p = np.log1p(power_spectrum(gray))
    p -= p.min()
    peak = p.max()
    return (255 * p / peak).astype(np.uint8) if peak > 0 else np.zeros_like(p, dtype=np.uint8)


def calibrate_quantiles(raw_values: Iterable[float]) -> tuple[float, float]:
    """q01 and q99 of VNI_raw over a calibration set (Equation 4)."""
    arr = np.asarray(list(raw_values), dtype=np.float64)
    if arr.size < 2:
        raise ValueError("calibration needs at least two images")
    q01, q99 = np.quantile(arr, [0.01, 0.99])
    if q99 <= q01:
        raise ValueError("calibration set has no spread in VNI_raw")
    return float(q01), float(q99)
