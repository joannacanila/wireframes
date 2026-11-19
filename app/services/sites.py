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
    from app.services.records import list_columns, listed  # local: records imports this module
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
                and listed(a, include_test)]
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
    """S16: one row per registered pole, from ``records.latest_rows`` (the dashboard's rows), so the map and the
    dashboard show the same status for a pole. The MSPI rank fields are merged in where the pole is ranked (system
    area); a pole whose latest inspection failed or is still processing is listed too, with that status."""
    from app.services import records
    from app.services.contacts import location_check
    from app.services.labels import all_labels
    ranked, _ = ranking(s, cfg, weights, include_test)
    by_code = {r.site_id: r for r in ranked}
    w = weights or (cfg.mspi.alpha, cfg.mspi.beta, cfg.mspi.gamma)
    labels = all_labels(s)
    rows = []
    for x in records.latest_rows(s, labels, include_test):
        r = by_code.get(x["pole_code"])
        rank = asdict(r) if r else {"site_id": x["pole_code"], "rank": None, "mspi": None, "level": None,
                                    "vni": None, "mean_crs": None, "max_crs": None, "n_wires": None, "flags": []}
        rows.append({**rank, "flags": list(rank["flags"]), "pole_code": x["pole_code"], "barangay": x["barangay"],
                     "analysis_id": x["id"], "analysis_status": x["status"], "has_location": x["has_location"],
                     "analyzed_at": x["created_at"], "risk": x["risk"], "review": x["review"],
                     "inspection_name": x["inspection_name"], "city": x["city"], "location": x["location"],
                     "label": x["label"], "op_status": x.get("op_status"), "op_label": x.get("op_status_label"),
                     "op_reason": x.get("op_reason"), "assignee": x.get("assignee"), "result": x.get("result"),
                     "location_check": location_check(x["barangay"], x["city"]),
                     # S18: what the map list and preview show (the same server values as every other surface)
                     **{k: x.get(k) for k in ("filename", "detected", "problem_count", "risk_level", "review_status",
                                              "review_status_label", "machine_state", "decision")},
                     "place_label": (x.get("place") or {}).get("label")})
    return {"weights": {"alpha": w[0], "beta": w[1], "gamma": w[2]},
            "provisional": cfg.mspi.provisional, "ranked_count": sum(r["rank"] is not None for r in rows),
            "counts_of": "poles", "sites": rows}


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
