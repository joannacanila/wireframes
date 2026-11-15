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


def latest_per_pole(s: Session) -> list[tuple[Pole, Analysis]]:
    poles = s.scalars(
        select(Pole).options(
            selectinload(Pole.location),
            selectinload(Pole.analyses).selectinload(Analysis.detections),
        )
    ).all()
    out = []
    for p in poles:
        done = [a for a in p.analyses if a.status in FINISHED and a.vni is not None]
        if done:
            out.append((p, max(done, key=lambda a: a.created_at)))
    return out


def ranking(s: Session, cfg: AnalysisConfig, weights: tuple[float, float, float] | None = None
            ) -> tuple[list[RankedSite], dict[str, tuple[Pole, Analysis]]]:
    pairs = latest_per_pole(s)
    index = {p.pole_code: (p, a) for p, a in pairs}
    w = weights or (cfg.mspi.alpha, cfg.mspi.beta, cfg.mspi.gamma)
    sites = [SiteInput(p.pole_code, a.vni, a.valid_crs) for p, a in pairs]
    return rank_sites(sites, *w, levels=cfg.crs.levels), index


def ranking_payload(s: Session, cfg: AnalysisConfig, weights: tuple[float, float, float] | None = None
                    ) -> dict[str, Any]:
    ranked, index = ranking(s, cfg, weights)
    w = weights or (cfg.mspi.alpha, cfg.mspi.beta, cfg.mspi.gamma)
    rows = []
    for r in ranked:
        pole, a = index[r.site_id]
        loc = pole.location
        rows.append({
            **asdict(r), "flags": list(r.flags), "pole_code": r.site_id, "barangay": pole.barangay,
            "analysis_id": a.id, "analysis_status": a.status, "vni_band": a.vni_band,
            "has_location": loc is not None, "analyzed_at": a.created_at.isoformat(),
        })
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


def geojson(s: Session, cfg: AnalysisConfig) -> dict[str, Any]:
    """RFC 7946 FeatureCollection; coordinates are [longitude, latitude]."""
    payload = ranking_payload(s, cfg)
    features, unlocated = [], []
    _, index = ranking(s, cfg)
    for row in payload["sites"]:
        pole, _ = index[row["pole_code"]]
        if pole.location is None:
            unlocated.append(row["pole_code"])
            continue
        features.append({
            "type": "Feature",
            "id": row["pole_code"],
            "geometry": {"type": "Point",
                         "coordinates": [round(pole.location.longitude, 7), round(pole.location.latitude, 7)]},
            "properties": {k: row[k] for k in (
                "pole_code", "barangay", "rank", "mspi", "level", "vni", "vni_band", "mean_crs",
                "max_crs", "n_wires", "flags", "analysis_id", "analysis_status", "analyzed_at")}
            | {"location_source": pole.location.source},
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {"weights": payload["weights"], "provisional": payload["provisional"],
                     "unlocated_poles": unlocated,
                     "notice": "Computational screening results. Professional review required."},
    }


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
