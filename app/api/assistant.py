"""/api/assistant: the WIREFRAME Assistant (v2.19). Signed-in accounts only; read-only."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.assistant import live, service
from app.core.security import SlidingWindow, require_admin
from app.core.state import AppState, get_state
from app.models.orm import Analysis
from app.services import records, sites

router = APIRouter(prefix="/api/assistant", tags=["assistant"], dependencies=[Depends(require_admin)])
State = Annotated[AppState, Depends(get_state)]
User = Annotated[object, Depends(require_admin)]


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=4000)


class Ask(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    page: str | None = Field(default=None, max_length=200)
    analysis_id: str | None = Field(default=None, pattern="^[0-9a-f]{32}$")
    history: list[Turn] = Field(default_factory=list, max_length=12)


@router.get("/status")
def assistant_status(st: State) -> dict:
    return service.status(st.settings)


@router.post("/ask")
def ask(body: Ask, st: State, user: User) -> dict:
    lims = st.__dict__.setdefault("_assistant_limiters", {})  # per running app, per configured rate
    lim = lims.setdefault(st.settings.assistant_per_minute, SlidingWindow(st.settings.assistant_per_minute, 60))
    key = f"assistant|{user.id}"
    wait = lim.blocked(key)
    if wait:
        raise HTTPException(429, {"code": "too_many_questions",
                                  "message": f"Too many questions at once. Try again in {int(wait) + 1} s."},
                            headers={"Retry-After": str(int(wait) + 1)})
    lim.hit(key)
    with st.db.session() as s:
        a = None
        if body.analysis_id:
            a = s.get(Analysis, body.analysis_id)
            # the same visibility as the portal: test records only for administrators
            if a is not None and not records.is_operational(a) and getattr(user, "role", "admin") != "admin":
                a = None
        # v2.20 layers 1 and 3: live answers and navigation actions come from the database, not from a model
        ranking = sites.ranking_payload(s, st.config)["sites"]
        lv = live.handle(s, body.question, body.page, a, ranking)
        if lv is not None:
            return {"answer": lv.text, "sources": [], "mode": lv.kind, "inspection": None, "notice": None,
                    "poles": lv.poles, "action": lv.action, "nav": [], "suggest": [], "intent": "live"}
        out = service.answer(st.settings, body.question, body.page, [t.model_dump() for t in body.history], a, st.config)
        return {**out.as_dict(), "poles": [], "action": None}
