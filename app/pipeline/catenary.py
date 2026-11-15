"""Catenary fitting and the sag ratio (Section 3.6.5, Equations 6-8, Algorithm 5 lines 1-7).

    y = a cosh((x - b) / a) + c                                       (6)
    S(a, b, c) = sum_i [y_i - a cosh((x_i - b)/a) - c]^2             (7)
    delta_rel = delta / L                                             (8)

Implementation notes
--------------------
* Image rows grow downward, so y is flipped (y_up = -y) before fitting; a hanging
  wire then has a minimum and a > 0.
* The fit runs in normalised coordinates (origin at the chord midpoint, unit = chord
  length). The catenary family is closed under translation and uniform scaling
  (a, b, c scale with the coordinates), so this changes only the conditioning of the
  problem, not the solution. Results are mapped back to pixels.
* Starting values come from a least-squares parabola, using the expansion
  a cosh(t/a) ~ a + t^2 / (2a):  a0 = 1/(2 p2),  b0 = -p1/(2 p2),  c0 = vertex - a0.
  A parabola that is flat or concave (p2 <= 0) is started as a very shallow catenary
  tangent to the linear trend; LM then decides whether a positive a exists.
* Levenberg-Marquardt: ``scipy.optimize.curve_fit(method="lm")`` (MINPACK lmdif).
  Failure conditions (Algorithm 5 line 4): no convergence, a <= 0, or the RMS
  residual divided by L above tau.
* Chord: the two ends of the fitted centerline, i.e. the fitted curve evaluated at the
  first and last centerline abscissae. delta is the largest perpendicular distance
  from the fitted curve to that chord. For a convex curve this distance is maximal
  where the curve's slope equals the chord's slope:
        sinh((x* - b)/a) = m   =>   x* = b + a asinh(m),
  which gives delta in closed form instead of by sampling.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass

import numpy as np
from scipy.optimize import OptimizeWarning, curve_fit


def catenary(x: np.ndarray | float, a: float, b: float, c: float) -> np.ndarray | float:
    return a * np.cosh((np.asarray(x) - b) / a) + c


def _jacobian(x: np.ndarray, a: float, b: float, c: float) -> np.ndarray:
    t = (x - b) / a
    sh, ch = np.sinh(t), np.cosh(t)
    return np.column_stack([ch - t * sh, -sh, np.ones_like(x)])


@dataclass(frozen=True)
class CatenaryFit:
    ok: bool
    reason: str | None  # no_convergence | non_positive_a | residual_too_large | degenerate
    a: float | None = None  # pixel units, y-up frame
    b: float | None = None
    c: float | None = None
    rms_residual_px: float | None = None
    rel_residual: float | None = None
    chord_px: float | None = None  # L
    sag_px: float | None = None  # delta
    sag_ratio: float | None = None  # delta_rel
    sag_point: tuple[float, float] | None = None  # (x*, y*) image coords (y down)
    chord_foot: tuple[float, float] | None = None  # foot of the perpendicular on the chord
    endpoints: tuple[tuple[float, float], tuple[float, float]] | None = None  # image coords
    truncated: bool = False
    n_points: int = 0
    iterations: int | None = None

    def curve(self, n: int = 128) -> list[tuple[float, float]]:
        """Fitted curve sampled between the chord ends, in image coordinates (y down)."""
        if not self.ok or self.endpoints is None:
            return []
        x0, x1 = self.endpoints[0][0], self.endpoints[1][0]
        xs = np.linspace(x0, x1, n)
        ys = -catenary(xs, self.a, self.b, self.c)
        return [(float(x), float(y)) for x, y in zip(xs, ys, strict=True)]


def parabola_start(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    """Starting values (a0, b0, c0) from a least-squares parabola (normalised units)."""
    p2, p1, p0 = np.polyfit(x, y, 2)
    if p2 > 1e-6:
        a0 = 1.0 / (2.0 * p2)
        b0 = -p1 / (2.0 * p2)
        vertex = p0 - p1 * p1 / (4.0 * p2)
        return a0, b0, vertex - a0
    # Nearly straight or concave: start from a catenary of very large a whose slope at the
    # data centre matches the linear trend.
    m, k = np.polyfit(x, y, 1)
    a0 = 50.0
    xm = float(np.mean(x))
    b0 = xm - a0 * math.asinh(m)
    c0 = (m * xm + k) - a0 * math.cosh((xm - b0) / a0)
    return a0, b0, c0


def _chord_sag(a: float, b: float, c: float, xa: float, xb: float) -> tuple[float, float, float, float]:
    """(L, delta, x*, foot parameter) for the chord between x=xa and x=xb on the curve."""
    ya, yb = catenary(xa, a, b, c), catenary(xb, a, b, c)
    dx, dy = xb - xa, yb - ya
    length = math.hypot(dx, dy)
    m = dy / dx
    xs = b + a * math.asinh(m)
    if not (xa < xs < xb):
        return length, 0.0, xa, 0.0
    ys = catenary(xs, a, b, c)
    # signed perpendicular distance; for a > 0 the curve lies below the chord
    dist = abs(dx * (ya - ys) - (xa - xs) * dy) / length
    t = ((xs - xa) * dx + (ys - ya) * dy) / (length * length)
    return length, dist, xs, t


def fit_catenary(
    points: np.ndarray,
    *,
    image_size: tuple[int, int],
    max_rel_residual: float = 0.02,
    border_margin_frac: float = 0.01,
    max_function_evals: int = 4000,
) -> CatenaryFit:
    """Fit Equation 6 to ordered centerline points given in image coordinates (x, y-down).

    ``image_size`` is (width, height), used for the truncation check.
    """
    pts = np.asarray(points, dtype=np.float64)
    n = len(pts)
    if n < 4:
        return CatenaryFit(False, "degenerate", n_points=n)
    x_img, y_img = pts[:, 0], pts[:, 1]
    y_up = -y_img  # FLIP_VERTICAL_AXIS

    w, h = image_size
    margin = border_margin_frac * min(w, h)
    def near_border(px: float, py: float) -> bool:
        return px <= margin or py <= margin or px >= w - 1 - margin or py >= h - 1 - margin
    truncated = near_border(*pts[0]) or near_border(*pts[-1])

    # normalise: chord midpoint at origin, chord length = 1
    x_first, x_last = x_img.min(), x_img.max()
    if x_last - x_first < 2.0:
        return CatenaryFit(False, "degenerate", n_points=n, truncated=truncated)
    ox, oy = 0.5 * (pts[0, 0] + pts[-1, 0]), 0.5 * (y_up[0] + y_up[-1])
    scale = math.hypot(pts[-1, 0] - pts[0, 0], y_up[-1] - y_up[0]) or (x_last - x_first)
    xn, yn = (x_img - ox) / scale, (y_up - oy) / scale

    p0 = parabola_start(xn, yn)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", OptimizeWarning)
            with np.errstate(over="raise", invalid="raise"):
                popt, _, info, _, ier = curve_fit(
                    catenary, xn, yn, p0=p0, method="lm", jac=_jacobian,
                    maxfev=max_function_evals, full_output=True,
                )
    except (RuntimeError, OptimizeWarning, FloatingPointError, ValueError):
        return CatenaryFit(False, "no_convergence", n_points=n, truncated=truncated)
    if ier not in (1, 2, 3, 4) or not np.all(np.isfinite(popt)):
        return CatenaryFit(False, "no_convergence", n_points=n, truncated=truncated)

    an, bn, cn = (float(v) for v in popt)
    if an <= 0:
        return CatenaryFit(False, "non_positive_a", a=an * scale, n_points=n, truncated=truncated)

    # back to pixels (y-up frame): x = xn*s + ox, y = yn*s + oy
    a, b, c = an * scale, bn * scale + ox, cn * scale + oy
    resid = y_up - catenary(x_img, a, b, c)
    rms = float(np.sqrt(np.mean(resid**2)))

    xa, xb = float(x_first), float(x_last)
    length, delta, xs, t = _chord_sag(a, b, c, xa, xb)
    rel_res = rms / length if length > 0 else math.inf
    ya_up, yb_up = float(catenary(xa, a, b, c)), float(catenary(xb, a, b, c))
    endpoints = ((xa, -ya_up), (xb, -yb_up))
    fit = dict(
        a=a, b=b, c=c, rms_residual_px=rms, rel_residual=rel_res,
        chord_px=length, sag_px=delta, sag_ratio=delta / length if length > 0 else None,
        endpoints=endpoints, truncated=truncated, n_points=n, iterations=int(info["nfev"]),
    )
    if delta > 0:
        ys_up = float(catenary(xs, a, b, c))
        fit["sag_point"] = (xs, -ys_up)
        fx = xa + t * (xb - xa)
        fy = ya_up + t * (yb_up - ya_up)
        fit["chord_foot"] = (fx, -fy)
    if rel_res > max_rel_residual:
        return CatenaryFit(False, "residual_too_large", **fit)
    return CatenaryFit(True, None, **fit)
