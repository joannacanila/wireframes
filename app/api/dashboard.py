"""/api/dashboard routes: GeoJSON map, MSPI ranking, weight sensitivity, summary."""

from __future__ import annotations

import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.security import require_admin
from app.core.state import AppState, get_state
from app.services import sites

# Multi-pole views expose pole locations, which the thesis restricts to the research team
# (Section 3.14.4): admin only.
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"], dependencies=[Depends(require_admin)])
State = Annotated[AppState, Depends(get_state)]
User = Annotated[object, Depends(require_admin)]
Weight = Annotated[float | None, Query(ge=0, le=1)]


def _admin(user) -> bool:
    return getattr(user, "role", "admin") == "admin"


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
def priority(st: State, user: User, alpha: Weight = None, beta: Weight = None, gamma: Weight = None,
             include_test: bool = False) -> dict:
    """The inspection order with the configured weights. Trying other weights is a system-area tool (v2.19)."""
    w = _weights(alpha, beta, gamma)
    if w is not None and not _admin(user):
        raise HTTPException(403, {"code": "admin_only", "message": "Prioritization settings are for administrators."})
    with st.db.session() as s:
        return sites.ranking_payload(s, st.config, w, include_test and _admin(user))

