"""Multi-site priority ranking (Algorithm 6) over stored analyses, and GeoJSON output.

The thesis captures one standardized photograph per pole. When a pole has several
analyses, the most recent finished one (complete / fit_failed / no_wires_detected)
represents the pole.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import AnalysisConfig
from app.models.orm import Analysis, Pole
from app.pipeline.mspi import RankedSite, SiteInput, rank_sites, sensitivity

FINISHED = ("complete", "fit_failed", "no_wires_detected")


def latest_per_pole(s: Session, include_test: bool = False) -> list[tuple[Pole, Analysis]]:
    """v2.19: test records (app/services/records.py) are left out unless ``include_test``."""
    from app.services.records import is_operational, list_columns  # local: records imports this module
    poles = s.scalars(
        select(Pole).options(
            selectinload(Pole.location),
            selectinload(Pole.analyses).selectinload(Analysis.detections).load_only(*list_columns()),
            selectinload(Pole.analyses).selectinload(Analysis.reviews),
        )
    ).all()
    out = []
    for p in poles:
        done = [a for a in p.analyses if a.status in FINISHED and a.vni is not None
                and (include_test or is_operational(a))]
        if done:
            out.append((p, max(done, key=lambda a: a.created_at)))
    return out


def ranking(s: Session, cfg: AnalysisConfig, weights: tuple[float, float, float] | None = None,
            include_test: bool = False) -> tuple[list[RankedSite], dict[str, tuple[Pole, Analysis]]]:
    pairs = latest_per_pole(s, include_test)
    index = {p.pole_code: (p, a) for p, a in pairs}
    w = weights or (cfg.mspi.alpha, cfg.mspi.beta, cfg.mspi.gamma)
    sites = [SiteInput(p.pole_code, a.vni, a.valid_crs) for p, a in pairs]
    return rank_sites(sites, *w, levels=cfg.crs.levels), index


def ranking_payload(s: Session, cfg: AnalysisConfig, weights: tuple[float, float, float] | None = None,
                    include_test: bool = False) -> dict[str, Any]:
    from app.services import records
    ranked, index = ranking(s, cfg, weights, include_test)
    w = weights or (cfg.mspi.alpha, cfg.mspi.beta, cfg.mspi.gamma)
    rows = []
    for r in ranked:
        pole, a = index[r.site_id]
        loc = pole.location
        rows.append({
            **asdict(r), "flags": list(r.flags), "pole_code": r.site_id, "barangay": pole.barangay,
            "analysis_id": a.id, "analysis_status": a.status, "vni_band": a.vni_band,
            "has_location": loc is not None, "analyzed_at": a.created_at.isoformat(),
            # v2.19 plain fields for the dispatch list (the score fields above stay for the system area)
            "risk": records.risk(a), "review": records.review_status(a), "inspection_name": a.inspection_name,
            "city": a.city, "location": None if loc is None else {"lat": loc.latitude, "lon": loc.longitude,
                                                                  "source": loc.source},
        })
    from app.services.labels import all_labels  # v2.20 readable pole labels (SAL-001); the pole code stays the id
    labels = all_labels(s)
    for row in rows:
        row["label"] = labels.get(row["pole_code"])
    return {"weights": {"alpha": w[0], "beta": w[1], "gamma": w[2]},
            "provisional": cfg.mspi.provisional, "ranked_count": sum(r["rank"] is not None for r in rows),
            "sites": rows}


def rank_of(s: Session, cfg: AnalysisConfig, pole_code: str) -> dict[str, Any] | None:
    ranked, _ = ranking(s, cfg)
    n = sum(r.rank is not None for r in ranked)
    for r in ranked:
        if r.site_id == pole_code:
            return {"rank": r.rank, "of": n, "mspi": r.mspi, "level": r.level, "flags": list(r.flags)}
    return None


def sensitivity_payload(s: Session, cfg: AnalysisConfig, k: int = 5,
                        baseline: tuple[float, float, float] | None = None) -> dict[str, Any]:
    """Rankings over the weight grid compared with ``baseline`` (default: the configured weights)."""
    pairs = latest_per_pole(s)
    sites = [SiteInput(p.pole_code, a.vni, a.valid_crs) for p, a in pairs]
    rep = sensitivity(sites, baseline or (cfg.mspi.alpha, cfg.mspi.beta, cfg.mspi.gamma), cfg.mspi.sensitivity_step, k)
    return {
        "baseline": dict(zip(("alpha", "beta", "gamma"), rep.baseline, strict=True)),
        "n_sites": rep.n_sites, "k": rep.k, "min_rho": rep.min_rho, "mean_rho": rep.mean_rho,
        "rank_range": {sid: {"best": b, "worst": w} for sid, (b, w) in rep.rank_range.items()},
        "grid": [{"alpha": r.alpha, "beta": r.beta, "gamma": r.gamma, "rho": r.spearman_rho,
                  "top_k_overlap": r.top_k_overlap} for r in rep.rows],
    }
