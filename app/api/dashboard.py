"""/api/dashboard routes: GeoJSON map, MSPI ranking, weight sensitivity, summary."""

from __future__ import annotations

import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.core.security import require_admin
from app.core.state import AppState, get_state
from app.models.orm import Analysis, Detection
from app.services import sites

# Multi-pole views expose pole locations, which the thesis restricts to the research team
# (Section 3.14.4): admin only.
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"], dependencies=[Depends(require_admin)])
State = Annotated[AppState, Depends(get_state)]
Weight = Annotated[float | None, Query(ge=0, le=1)]


@router.get("/map")
def map_geojson(st: State) -> JSONResponse:
    with st.db.session() as s:
        return JSONResponse(sites.geojson(s, st.config), media_type="application/geo+json")


def _weights(alpha: float | None, beta: float | None, gamma: float | None) -> tuple[float, float, float] | None:
    given = [w for w in (alpha, beta, gamma) if w is not None]
    if not given:
        return None
    if len(given) != 3:
        raise HTTPException(422, {"code": "weights", "message": "Provide alpha, beta and gamma together."})
    if not math.isclose(alpha + beta + gamma, 1.0, abs_tol=1e-6):
        raise HTTPException(422, {"code": "weights", "message": "alpha + beta + gamma must equal 1."})
    return (alpha, beta, gamma)


@router.get("/priority")
def priority(st: State, alpha: Weight = None, beta: Weight = None, gamma: Weight = None) -> dict:
    with st.db.session() as s:
        return sites.ranking_payload(s, st.config, _weights(alpha, beta, gamma))


@router.get("/sensitivity")
def weight_sensitivity(st: State, k: Annotated[int, Query(ge=1, le=50)] = 5,
                       alpha: Weight = None, beta: Weight = None, gamma: Weight = None) -> dict:
    """Spearman rho of every grid ranking against the given weights (the live slider setting)."""
    with st.db.session() as s:
        return sites.sensitivity_payload(s, st.config, k, _weights(alpha, beta, gamma))


@router.get("/summary")
def summary(st: State) -> dict:
    with st.db.session() as s:
        by_status = dict(s.execute(select(Analysis.status, func.count()).group_by(Analysis.status)).all())
        by_level = dict(s.execute(
            select(Detection.level, func.count()).where(Detection.level.is_not(None)).group_by(Detection.level)
        ).all())
        by_class = dict(s.execute(select(Detection.wire_class, func.count()).group_by(Detection.wire_class)).all())
    return {"analyses_by_status": by_status, "wires_by_level": by_level, "wires_by_class": by_class,
            "queue_depth": st.queue.depth}
