"""S18 /api/admin review workflow: review progress, decisions, the risk rubric and decision options.

Machine findings are read, never written, here. Every write is audited with previous and new values
(app/services/review_flow.py)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import selectinload

from app.core.security import require_admin, require_system
from app.core.state import AppState, get_state
from app.models.orm import Analysis, Pole
from app.services import review_flow

router = APIRouter(prefix="/api/admin", tags=["admin-review"], dependencies=[Depends(require_admin)])
State = Annotated[AppState, Depends(get_state)]
User = Annotated[object, Depends(require_admin)]
SystemUser = Annotated[object, Depends(require_system)]


def _get(s, analysis_id: str) -> Analysis:
    from sqlalchemy import select
    a = s.scalar(select(Analysis).where(Analysis.id == analysis_id).options(
        selectinload(Analysis.decisions), selectinload(Analysis.review_progress), selectinload(Analysis.detections),
        selectinload(Analysis.pole).selectinload(Pole.location)))
    if a is None:
        raise HTTPException(404, {"code": "not_found", "message": "Inspection not found."})
    return a


def _bad(e: Exception):
    raise HTTPException(422, {"code": "review", "message": str(e)})


def machine_snapshot(a: Analysis, st: AppState) -> dict[str, Any]:
    """What the reviewer saw: the machine screening at decision time (kept with the decision, never re-derived)."""
    from app.services import records
    row = records.row(a)
    return {"detected": row.get("detected"), "problem_count": row.get("problem_count"),
            "risk_level": (row.get("risk_level") or {}).get("level"), "result": (row.get("result") or {}).get("code"),
            "machine_state": row.get("machine_state"), "rubric_version": (row.get("risk_level") or {}).get(
                "rubric_version")}


@router.get("/inspections/{analysis_id}/review")
def get_review(analysis_id: str, st: State, user: User) -> dict:
    with st.db.session() as s:
        a = _get(s, analysis_id)
        return {**review_flow.view(a), "options": review_flow.options(s), "rubric": review_flow.rubric_view(),
                "can_decide": a.status != "processing"}


@router.post("/inspections/{analysis_id}/review/start")
def start_review(analysis_id: str, st: State, user: User) -> dict:
    with st.db.session() as s:
        a = _get(s, analysis_id)
        changed = review_flow.start(s, a, user)
        s.flush()
        return {**review_flow.view(a), "recorded": changed}


class DecisionBody(BaseModel):
    decision: str = Field(min_length=1, max_length=40)
    rationale: str = Field(default="", max_length=4000)
    follow_up: str = Field(default="", max_length=2000)
    observations: str = Field(default="", max_length=4000)
    critical: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    other_text: str = Field(default="", max_length=110)  # S18: required when decision == "other"


@router.post("/inspections/{analysis_id}/decision", status_code=201)
def save_decision(analysis_id: str, body: DecisionBody, st: State, user: User) -> dict:
    with st.db.session() as s:
        a = _get(s, analysis_id)
        if a.status == "processing":
            raise HTTPException(409, {"code": "processing", "message": "Wait until processing has finished."})
        try:
            review_flow.finalize(s, a, user, body.model_dump(), machine_snapshot(a, st))
        except review_flow.ReviewError as e:
            _bad(e)
        s.flush()
        return review_flow.view(a)


class ReopenBody(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


@router.post("/inspections/{analysis_id}/review/reopen")
def reopen_review(analysis_id: str, body: ReopenBody, st: State, user: User) -> dict:
    with st.db.session() as s:
        a = _get(s, analysis_id)
        try:
            review_flow.reopen(s, a, user, body.reason)
        except review_flow.ReviewError as e:
            _bad(e)
        s.flush()
        return review_flow.view(a)


@router.get("/settings/risk-rubric")
def get_rubric(st: State, user: User) -> dict:
    with st.db.session() as s:
        review_flow.load_rubric(s)
    return review_flow.rubric_view()


@router.put("/settings/risk-rubric")
def put_rubric(body: dict, st: State, user: SystemUser) -> dict:
    with st.db.session() as s:
        try:
            review_flow.set_rubric(s, body, user)
        except review_flow.ReviewError as e:
            _bad(e)
    return review_flow.rubric_view()


@router.get("/settings/decision-options")
def get_options(st: State, user: User) -> list[dict]:
    with st.db.session() as s:
        return review_flow.options(s)


class OptionsBody(BaseModel):
    options: list[dict[str, Any]] = Field(min_length=1, max_length=40)


@router.put("/settings/decision-options")
def put_options(body: OptionsBody, st: State, user: SystemUser) -> list[dict]:
    with st.db.session() as s:
        try:
            return review_flow.set_options(s, body.options, user)
        except review_flow.ReviewError as e:
            _bad(e)
