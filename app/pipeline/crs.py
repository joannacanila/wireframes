"""Catenary Risk Score and risk level (Section 3.6.5, Equation 10, Table 1.2).

    CRS = min{10, 10 * (delta_rel / delta_lim) * w_class}                     (10)

    level = Low (green)     if CRS < 3.0
            Medium (yellow) if 3.0 <= CRS <= 6.0
            High (red)      if CRS > 6.0

v2.21: config ``crs.levels.minor_from`` / ``critical_from`` (inclusive lower bounds) replace these cut-offs when
set; see app/core/config.py Levels.
"""

from __future__ import annotations

from app.core.config import Levels

LOW, MEDIUM, HIGH = "low", "medium", "high"


def catenary_risk_score(delta_rel: float, delta_lim: float, class_weight: float = 1.0) -> float:
    if delta_lim <= 0:
        raise ValueError("delta_lim must be positive")
    if delta_rel < 0:
        raise ValueError("delta_rel must be non-negative")
    return min(10.0, 10.0 * (delta_rel / delta_lim) * class_weight)


def risk_level(crs: float, levels: Levels | None = None) -> str:
    """Level for a CRS. Compared after rounding to 9 decimals, so a sag of exactly 2.4% (CRS 6.000000000000001 in
    floating point) sits on its boundary as written in the configuration."""
    lv = levels or Levels()
    minor, critical, inclusive = lv.bounds()
    c = round(crs, 9)
    if c < minor:
        return LOW
    if (c < critical) if inclusive else (c <= critical):
        return MEDIUM
    return HIGH
