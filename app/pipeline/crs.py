"""Catenary Risk Score and risk level (Section 3.6.5, Equation 10, Table 1.2).

    CRS = min{10, 10 * (delta_rel / delta_lim) * w_class}                     (10)

    level = Low (green)     if CRS < 3.0
            Medium (yellow) if 3.0 <= CRS <= 6.0
            High (red)      if CRS > 6.0
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
    lv = levels or Levels()
    if crs < lv.low_below:
        return LOW
    if crs <= lv.high_above:
        return MEDIUM
    return HIGH
