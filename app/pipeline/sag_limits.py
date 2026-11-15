"""Allowable sag ratio with engineering factors (Section 3.6.5, Equation 9, Table 3.8).

    delta_lim = delta_lim,0 * (delta_exp / delta_exp,0)                        (9)

delta_exp is the expected sag ratio under the conditions at capture and delta_exp,0
the expected sag ratio under reference conditions, both for the same span. Each comes
from the catenary relation with

    resultant load       w_r = sqrt(w^2 + w_w^2)
    wind load            w_w = 1/2 rho V^2 C_d d           (per unit length)
    horizontal tension   from the change-of-state equation (Kiessling et al., 2003):

        H2^2 [H2 - H1 + E A w1^2 L^2 / (24 H1^2) + E A alpha (T2 - T1)] = E A w2^2 L^2 / 24

    midspan sag          D = (H / w_r) [cosh(w_r L / (2 H)) - 1],   delta_exp = D / L

State 1 is the reference state (reference temperature, no wind, reference tension of
the conductor). When the span is not available, Equation 9 cannot be evaluated:
delta_lim = delta_lim,0 and the record is flagged "default_parameters".
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.optimize import brentq

from app.core.config import Conductor, CRSConfig, EngineeringConfig


@dataclass(frozen=True)
class EngineeringContext:
    span_m: float | None = None
    temperature_c: float | None = None
    wind_speed_mps: float | None = None
    weight_n_per_m: float | None = None  # overrides the class conductor value
    elastic_modulus_pa: float | None = None

    @property
    def available(self) -> bool:
        return self.span_m is not None and self.span_m > 0


@dataclass(frozen=True)
class SagLimit:
    delta_lim: float
    delta_lim0: float
    delta_exp: float | None
    delta_exp0: float | None
    horizontal_tension_n: float | None
    used_context: bool
    flags: tuple[str, ...]


def wind_load(speed_mps: float, diameter_m: float, rho: float, cd: float) -> float:
    return 0.5 * rho * speed_mps**2 * cd * diameter_m


def change_of_state(
    h1: float, w1: float, t1: float, w2: float, t2: float, span: float, e: float, area: float, alpha: float
) -> float:
    """Positive real root H2 of the cubic change-of-state equation."""
    ea = e * area
    k = h1 - ea * w1**2 * span**2 / (24.0 * h1**2) - ea * alpha * (t2 - t1)
    rhs = ea * w2**2 * span**2 / 24.0

    def f(h: float) -> float:
        return h * h * (h - k) - rhs

    # f(0) = -rhs < 0 and f is increasing for h > max(k, 0) * 2/3; bracket the unique root.
    lo = max(k, 0.0)
    hi = max(lo, 1.0)
    while f(hi) < 0:
        hi *= 2.0
        if hi > 1e12:
            raise ValueError("change-of-state root not bracketed")
    return brentq(f, lo if f(lo) < 0 else 1e-9, hi, xtol=1e-9, rtol=1e-12)


def catenary_sag_ratio(h: float, w: float, span: float) -> float:
    return (h / w) * (math.cosh(w * span / (2.0 * h)) - 1.0) / span


def expected_sag_ratio(
    cond: Conductor, eng: EngineeringConfig, span: float, temp_c: float, wind_mps: float,
    weight: float | None = None, modulus: float | None = None,
) -> tuple[float, float]:
    """(delta_exp, H2) for the given state, relative to the conductor's reference state."""
    w = weight if weight is not None else cond.weight_n_per_m
    e = modulus if modulus is not None else cond.elastic_modulus_pa
    ww = wind_load(wind_mps, cond.diameter_m, eng.air_density, eng.drag_coefficient)
    wr = math.hypot(w, ww)
    h2 = change_of_state(
        h1=cond.reference_tension_n, w1=w, t1=eng.reference_temperature_c,
        w2=wr, t2=temp_c, span=span, e=e, area=cond.area_m2, alpha=cond.thermal_expansion_per_c,
    )
    return catenary_sag_ratio(h2, wr, span), h2


def allowable_sag(
    wire_class: str, ctx: EngineeringContext, crs_cfg: CRSConfig, eng: EngineeringConfig
) -> SagLimit:
    """ALLOWABLE_SAG(class, L, context) of Algorithm 5, line 8."""
    d0 = crs_cfg.delta_lim0[wire_class]
    flags: list[str] = []
    if crs_cfg.provisional:
        flags.append("development_limits")
    if not ctx.available:
        flags.append("default_parameters")
        return SagLimit(d0, d0, None, None, None, False, tuple(flags))

    cond = eng.conductors[wire_class]
    t_ref = eng.reference_temperature_c
    temp = ctx.temperature_c if ctx.temperature_c is not None else t_ref
    wind = ctx.wind_speed_mps if ctx.wind_speed_mps is not None else 0.0
    if ctx.temperature_c is None:
        flags.append("default_temperature")
    if ctx.wind_speed_mps is None:
        flags.append("default_wind")
    if eng.provisional:
        flags.append("illustrative_conductor_data")
    span = float(ctx.span_m)
    d_exp0, _ = expected_sag_ratio(cond, eng, span, t_ref, 0.0, ctx.weight_n_per_m, ctx.elastic_modulus_pa)
    d_exp, h2 = expected_sag_ratio(cond, eng, span, temp, wind, ctx.weight_n_per_m, ctx.elastic_modulus_pa)
    return SagLimit(d0 * d_exp / d_exp0, d0, d_exp, d_exp0, h2, True, tuple(flags))
