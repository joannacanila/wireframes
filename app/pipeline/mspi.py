"""Multi-Site Priority Comparison (Section 3.6.6, Equation 11, Algorithm 6).

    MSPI_s = alpha * VNI_s + beta * mean_i(CRS_i) / 10 + gamma * max_i(CRS_i) / 10,
             alpha + beta + gamma = 1                                           (11)

A pole with no valid CRS is not ranked and is flagged NO_VALID_WIRES. Each ranked pole
carries the risk level of its highest CRS; the MSPI only orders poles.

Sensitivity (Section 3.6.6, Table 3.11): the ranking is recomputed over every weight
triple on a simplex grid with step 0.1, and each ranking is compared with the
baseline ranking by Spearman's rho and by top-k overlap.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy.stats import spearmanr

from app.core.config import Levels
from app.pipeline.crs import risk_level


@dataclass(frozen=True)
class SiteInput:
    site_id: str
    vni: float
    crs: Sequence[float]


@dataclass(frozen=True)
class RankedSite:
    site_id: str
    rank: int | None
    mspi: float | None
    level: str | None
    vni: float
    mean_crs: float | None
    max_crs: float | None
    n_wires: int
    flags: tuple[str, ...] = field(default_factory=tuple)


def mspi(vni: float, crs: Sequence[float], alpha: float, beta: float, gamma: float) -> float:
    if not crs:
        raise ValueError("MSPI needs at least one valid CRS")
    if not math.isclose(alpha + beta + gamma, 1.0, abs_tol=1e-9):
        raise ValueError("alpha + beta + gamma must equal 1")
    arr = np.asarray(crs, dtype=np.float64)
    return float(alpha * vni + beta * arr.mean() / 10.0 + gamma * arr.max() / 10.0)


def rank_sites(
    sites: Sequence[SiteInput], alpha: float, beta: float, gamma: float, levels: Levels | None = None
) -> list[RankedSite]:
    """Algorithm 6. Ranked poles first (descending MSPI, ties broken by max CRS then id),
    then unranked poles."""
    ranked: list[RankedSite] = []
    unranked: list[RankedSite] = []
    for s in sites:
        if not s.crs:
            unranked.append(RankedSite(s.site_id, None, None, None, s.vni, None, None, 0, ("NO_VALID_WIRES",)))
            continue
        arr = np.asarray(s.crs, dtype=np.float64)
        ranked.append(
            RankedSite(
                s.site_id, None, mspi(s.vni, s.crs, alpha, beta, gamma),
                risk_level(float(arr.max()), levels), s.vni, float(arr.mean()), float(arr.max()), len(arr),
            )
        )
    ranked.sort(key=lambda r: (-r.mspi, -r.max_crs, r.site_id))
    out = [RankedSite(**{**r.__dict__, "rank": i + 1}) for i, r in enumerate(ranked)]
    return out + sorted(unranked, key=lambda r: r.site_id)


def weight_grid(step: float = 0.1) -> list[tuple[float, float, float]]:
    """All (alpha, beta, gamma) on the simplex with the given step (66 triples for 0.1)."""
    k = round(1.0 / step)
    if not math.isclose(k * step, 1.0):
        raise ValueError("step must divide 1")
    return [(i / k, j / k, (k - i - j) / k) for i in range(k + 1) for j in range(k + 1 - i)]


@dataclass(frozen=True)
class SensitivityRow:
    alpha: float
    beta: float
    gamma: float
    spearman_rho: float | None
    top_k_overlap: float | None
    order: tuple[str, ...]


@dataclass(frozen=True)
class SensitivityReport:
    baseline: tuple[float, float, float]
    n_sites: int
    k: int
    rows: tuple[SensitivityRow, ...]
    min_rho: float | None
    mean_rho: float | None
    rank_range: dict[str, tuple[int, int]]  # site -> (best rank, worst rank) over the grid


def sensitivity(
    sites: Sequence[SiteInput], baseline: tuple[float, float, float], step: float = 0.1, k: int = 5
) -> SensitivityReport:
    valid = [s for s in sites if s.crs]
    base = [r.site_id for r in rank_sites(valid, *baseline) if r.rank is not None]
    k = min(k, len(base))
    base_pos = {sid: i for i, sid in enumerate(base)}
    rows = []
    ranks: dict[str, list[int]] = {sid: [] for sid in base}
    for a, b, g in weight_grid(step):
        order = [r.site_id for r in rank_sites(valid, a, b, g) if r.rank is not None]
        for i, sid in enumerate(order):
            ranks[sid].append(i + 1)
        rho = overlap = None
        if len(order) >= 2:
            x = [base_pos[s] for s in order]
            res = spearmanr(x, list(range(len(order))))
            rho = float(res.statistic) if np.isfinite(res.statistic) else None
        if k:
            overlap = len(set(order[:k]) & set(base[:k])) / k
        rows.append(SensitivityRow(a, b, g, rho, overlap, tuple(order)))
    rhos = [r.spearman_rho for r in rows if r.spearman_rho is not None]
    return SensitivityReport(
        baseline=baseline, n_sites=len(base), k=k, rows=tuple(rows),
        min_rho=min(rhos) if rhos else None,
        mean_rho=float(np.mean(rhos)) if rhos else None,
        rank_range={sid: (min(v), max(v)) for sid, v in ranks.items() if v},
    )
