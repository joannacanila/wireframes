"""S15 (register D-32): measure a traced wire only where it behaves like one hanging span.

A wire hanging between two supports is a smooth catenary: one curve explains its whole trace. A trace that switches to
a neighbouring cable at a crossing, or runs through a pole attachment, is two near-straight pieces meeting at an angle.
Fitting one catenary to that V reads the bend as deep sag: on the held-out test split, traces with a relative residual
of 1-2% of the chord had a median error of about 2 percentage points and up to 12 pp against their labelled wires
(ml/experiments/s15_measure.py), i.e. straight wires reported as sagging 8-12%.

``fit_span`` keeps the catenary fit (app/pipeline/catenary.py, unchanged) and adds one test. When the catenary leaves
a noticeable residual, the trace is also fitted as two straight lines broken at one point. If two lines explain it
much better than the curve (``kink_ratio``), the trace is kinked, not sagging: it is split at the break and each piece
is fitted on its own. The longest piece that fits as one curve is the measurement, flagged ``kink_split`` with the
share of the trace it leaves out; a kinked trace with no piece that fits is ``kinked_trace`` (not measured), never a
sag. A smooth, sagging span fits the catenary far better than any two lines, so it is never split.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from app.pipeline.catenary import CatenaryFit, fit_catenary

ALGORITHM = "catenary+kink-split/1"  # recorded with every measurement (provenance)


def _line_sse(p: np.ndarray) -> float:
    """Sum of squared perpendicular distances to the total-least-squares line through ``p``."""
    if len(p) < 3:
        return 0.0
    q = p - p.mean(0)
    s = np.linalg.svd(q, compute_uv=False)
    return float(s[-1] ** 2)


def two_line(points: np.ndarray, min_piece: float = 0.15, steps: int = 48) -> tuple[float, int]:
    """(RMS perpendicular residual of the best two-line fit, index of its break point)."""
    p = np.asarray(points, float)
    n = len(p)
    lo, hi = max(3, int(min_piece * n)), min(n - 3, int((1 - min_piece) * n))
    if hi <= lo:
        return float("inf"), -1
    best, k_best = float("inf"), -1
    for k in np.unique(np.linspace(lo, hi, steps).astype(int)):
        sse = _line_sse(p[: k + 1]) + _line_sse(p[k:])
        if sse < best:
            best, k_best = sse, int(k)
    return float(np.sqrt(best / n)), k_best


def fit_span(points: np.ndarray, *, image_size: tuple[int, int], max_rel_residual: float = 0.02,
             border_margin_frac: float = 0.01, max_function_evals: int = 4000, kink_ratio: float = 0.5,
             kink_check_from: float = 0.004, n_min: int = 20) -> CatenaryFit:
    kw = dict(image_size=image_size, max_rel_residual=max_rel_residual, border_margin_frac=border_margin_frac,
              max_function_evals=max_function_evals)
    pts = np.asarray(points, float)
    f = fit_catenary(pts, **kw)
    if f.rel_residual is None or f.rel_residual < kink_check_from or not f.rms_residual_px:
        return f
    rms2, k = two_line(pts)
    if k < 0 or rms2 >= kink_ratio * f.rms_residual_px:
        return f  # one curve explains the trace about as well as two lines: a hanging span (or noise), kept as is
    pieces = [pts[: k + 1], pts[k:]]
    fits = [(fit_catenary(pc, **kw), len(pc)) for pc in pieces if len(pc) >= n_min]
    good = [(g, m) for g, m in fits if g.ok]
    if not good:
        return replace(f, ok=False, reason="kinked_trace", kink_split=True, kink_index=k)
    g, m = max(good, key=lambda t: t[0].chord_px or 0.0)
    return replace(g, kink_split=True, kink_index=k, excluded_frac=round(1 - m / len(pts), 3))
