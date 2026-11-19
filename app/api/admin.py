"""/api/admin: the admin portal.

v2.19: two routers on the same prefix. ``router`` serves the operational portal (dashboard, inspections, map,
reviews, reports) to every signed-in account; ``system`` serves the system area (models, queue, evaluation inputs,
technical exports, test records) to administrators only, enforced here, not only by hiding links. Operational
responses carry plain statuses (risk level, completeness, review), never scores or fit parameters, and leave test
records out (app/services/records.py)."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core import notice
from app.core.security import require_admin, require_system
from app.core.state import AppState, get_state, list_checkpoints
from app.models.orm import Analysis, Pole, Review
from app.services import audit, geo, records, sites
from app.services import barangay as barangay_svc
from app.services.labels import all_labels
from app.services.report_pdf import build_sites_report

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])
system = APIRouter(prefix="/api/admin", tags=["admin-system"], dependencies=[Depends(require_system)])
State = Annotated[AppState, Depends(get_state)]
User = Annotated[object, Depends(require_admin)]
SystemUser = Annotated[object, Depends(require_system)]


# S12 (D-28): why no reference applies, in the words the page uses
REF_WHY = {"no_reference_for_telecom": "Unavailable: no validated telecom / fibre reference",
           "no_reference_for_abandoned": "Unavailable: abandoned wire, type not established",
           "no_reference_for_class": "Unavailable: no validated reference for this type",
           "type_uncertain": "Unavailable: wire type uncertain", "drop_wire": "Not applicable: drop wire",
           "reference_disabled": "Unavailable: no reference configured"}


# ----------------------------------------------------------------------------- overview & queue


def queue_state(st: AppState) -> dict:
    with st.db.session() as s:
        processing = s.scalars(select(Analysis).where(Analysis.status == "processing")
                               .options(selectinload(Analysis.pole)).order_by(Analysis.created_at)).all()
        errors = s.scalars(select(Analysis).where(Analysis.status == "error").options(selectinload(Analysis.pole))
                           .order_by(Analysis.created_at.desc()).limit(20)).all()
        return {
            "depth": st.queue.depth, "processed_since_start": st.queue.processed,
            "processing": [{"id": a.id, "pole_code": a.pole.pole_code, "since": a.created_at.isoformat()} for a in processing],
            "errors": [{"id": a.id, "pole_code": a.pole.pole_code, "code": a.error_code, "message": a.error_message,
                        "at": a.created_at.isoformat()} for a in errors],
        }


@system.get("/queue")
def queue(st: State) -> dict:
    return queue_state(st)


# ----------------------------------------------------------------------------- models & settings


@system.get("/models")
def models(st: State) -> dict:
    seg = st.segmenter
    active = str(getattr(seg, "path", "")) or None
    return {
        "mode": "field" if st.model_ready() and not st.require_reference else "validation",
        "ready": st.model_ready(), "error": getattr(seg, "error", None),
        "active": active, "require_reference_masks": st.require_reference,
        "configured": st.config.segmentation.weights,
        "conf_min": st.config.segmentation.conf_min, "conf_provisional": st.config.segmentation.conf_provisional,
        "iou_nms": st.config.segmentation.iou_nms,
        "checkpoints": [{"name": p.name, "size_mb": round(p.stat().st_size / 2**20, 1),
                         "modified": datetime.fromtimestamp(p.stat().st_mtime, UTC).isoformat(),
                         "active": active is not None and p.resolve() == Path(active).resolve()}
                        for p in list_checkpoints()],
    }


class Activate(BaseModel):
    name: str = Field(min_length=1, max_length=128)


@system.post("/models/active")
def activate(body: Activate, st: State, user: SystemUser) -> dict:
    ok, err = st.activate_weights(body.name)
    if not ok:
        raise HTTPException(422, {"code": "checkpoint_rejected", "message": err or "Checkpoint could not be loaded."})
    st.put_setting("active_weights", body.name, user.username)
    with st.db.session() as s:
        audit.record(s, audit.actor_of(user), "model.activated", None, None, name=body.name)
    return models(st)


class SettingsBody(BaseModel):
    require_reference_masks: bool


@system.put("/settings")
def put_settings(body: SettingsBody, st: State, user: SystemUser) -> dict:
    st.put_setting("require_reference_masks", body.require_reference_masks, user.username)
    with st.db.session() as s:
        audit.record(s, audit.actor_of(user), "settings.changed", None, None,
                     require_reference_masks=body.require_reference_masks)
    return models(st)


# ----------------------------------------------------------------------------- analysis detail


class ReviewBody(BaseModel):
    decision: str = Field(pattern="^(confirmed|flagged|overridden|dismissed)$")
    override_level: str | None = Field(default=None, pattern="^(low|medium|high)$")
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _consistent(self) -> ReviewBody:
        if (self.decision == "overridden") != (self.override_level is not None):
            raise ValueError("an override needs a level, and only an override has one")
        if self.decision in ("overridden", "dismissed") and not self.note.strip():
            raise ValueError("explain a level change or a dismissal in the note")
        return self


def _reviews(analysis_id: str, st: AppState) -> list[dict]:
    with st.db.session() as s:
        rows = s.scalars(select(Review).where(Review.analysis_id == analysis_id).order_by(Review.created_at)).all()
        return [{"decision": r.decision, "override_level": r.override_level, "note": r.note,
                 "by": r.reviewer_name, "at": r.created_at.isoformat()} for r in rows]


@router.post("/analyses/{analysis_id}/reviews", status_code=201)
def add_review(analysis_id: str, body: ReviewBody, st: State, user: User) -> list[dict]:
    with st.db.session() as s:
        a = s.get(Analysis, analysis_id)
        if a is None:
            raise HTTPException(404, {"code": "not_found", "message": "Analysis not found."})
        if a.status not in ("complete", "fit_failed", "no_wires_detected"):
            raise HTTPException(409, {"code": a.status, "message": "Only finished analyses can be reviewed."})
        rv = Review(analysis_id=analysis_id, reviewer_id=user.id, reviewer_name=user.display_name or user.username,
                    decision=body.decision, override_level=body.override_level, note=body.note.strip())
        s.add(rv)
        audit.record(s, audit.actor_of(user), f"review.{body.decision}", a.pole.pole_code, analysis_id,
                     level=body.override_level)
        s.flush()
        _mirror_review(s, a, rv, user)
    return _reviews(analysis_id, st)


def _mirror_review(s, a: Analysis, rv: Review, user) -> None:
    """S18: a review recorded through the pre-S18 route (quick review, API clients) is also the administrator's
    decision, with its meaning preserved (review_flow.LEGACY) and linked by ``legacy_review_id`` (never duplicated)."""
    from app.models.orm import AdminDecision, ReviewProgress
    from app.services import review_flow
    code, label = review_flow.LEGACY[rv.decision]
    t = rv.created_at or review_flow.now()
    for d in a.decisions:
        if d.superseded_at is None:
            d.superseded_at = t
    a.decisions.append(AdminDecision(analysis_id=a.id, decision=code, decision_label=label, rationale=rv.note,
                                     reviewer_id=rv.reviewer_id, reviewer_name=rv.reviewer_name,
                                     legacy_review_id=rv.id, created_at=t))
    p = a.review_progress
    if p is None:
        a.review_progress = ReviewProgress(status="REVIEW_COMPLETE", started_at=t, started_by=rv.reviewer_name,
                                           completed_at=t, completed_by=rv.reviewer_name, reviewers=[rv.reviewer_name])
    else:
        p.status, p.completed_at, p.completed_by = "REVIEW_COMPLETE", t, rv.reviewer_name
        if rv.reviewer_name not in (p.reviewers or []):
            p.reviewers = [*(p.reviewers or []), rv.reviewer_name]


# ----------------------------------------------------------------------------- v2.19 operational portal


def _all_analyses(s, include_test: bool = False) -> list[Analysis]:
    rows = s.scalars(select(Analysis).options(
        selectinload(Analysis.pole).selectinload(Pole.location),
        selectinload(Analysis.detections).load_only(*records.list_columns()),
        selectinload(Analysis.reviews), selectinload(Analysis.assignment), selectinload(Analysis.decisions),
        selectinload(Analysis.review_progress)).order_by(Analysis.created_at.desc())).all()
    return [a for a in rows if records.listed(a, include_test)]


def _health(st: AppState) -> dict:
    """Only what an operator can act on; the detail lives in the system area."""
    from app.core import weights as wts
    alerts = []
    if not st.model_ready():
        downloading = any(v.get("state") == "downloading" for v in (wts.status() or {}).values()
                          if isinstance(v, dict))
        alerts.append({"code": "model_unavailable", "text": "Preparing the inspection models. New photos can be "
                       "screened when this finishes." if downloading else "Inspection processing is unavailable: "
                       "the detection model is not loaded. An administrator can check System."})
    elif st.require_reference:
        alerts.append({"code": "reference_only", "text": "Photo screening is switched off (reference masks are "
                       "required). An administrator can change this in System."})
    with st.db.session() as s:
        failed = s.scalar(select(func.count()).select_from(Analysis).where(Analysis.status == "error")) or 0
    q = queue_state(st)
    return {"ok": not alerts, "alerts": alerts, "processing": len(q["processing"]) + q["depth"], "failed": failed}


TRIAGE_ORDER = {"failed": 0, "processing": 1, "high": 2, "medium": 3, "unassessed": 4}


def _triage_key(r: dict) -> tuple:
    """Failed and unprocessed first, then flagged, then by risk; newest first within a group."""
    flagged = r["review"] == "flagged"
    return (0 if r["risk"] in ("failed", "processing") else 1 if flagged else 2,
            TRIAGE_ORDER.get(r["risk"], 9), -datetime.fromisoformat(r["created_at"]).timestamp())


@router.get("/dashboard")
def dashboard(st: State, user: User) -> dict:
    """v2.20 triage dashboard. Pole counts use each pole's latest finished operational inspection, so the buckets
    add up to the total. The triage queue holds each pole's latest inspection when it needs someone: processing
    failed or still running, flagged for further inspection, high or moderate risk without a review, or not
    assessed without a review. Activity is the audit trail (who did what), not a copy of the queue."""
    with st.db.session() as s:
        labels = all_labels(s)
        items = _all_analyses(s)
        by_pole: dict[int, list[Analysis]] = {}
        for a in items:
            by_pole.setdefault(a.pole_id, []).append(a)
        buckets = {"high": 0, "medium": 0, "low": 0, "unassessed": 0, "failed": 0}
        triage = []
        for analyses in by_pole.values():
            done = [a for a in analyses if a.status in records.FINISHED]
            latest_any = max(analyses, key=lambda a: a.created_at)
            if not done:
                buckets["failed"] += 1  # registered, but no inspection of it finished
                triage.append(records.row(latest_any, labels))
                continue
            latest = max(done, key=lambda a: a.created_at)
            rk = records.risk(latest)
            buckets[rk if rk in buckets else "unassessed"] += 1
            rv = records.review_status(latest)
            if latest_any.status in ("error", "processing") and latest_any is not latest:
                triage.append(records.row(latest_any, labels))
            elif rv in ("pending", "flagged") or (rk == "unassessed" and not latest.reviews):
                triage.append(records.row(latest, labels))
        triage.sort(key=_triage_key)
        events = audit.recent(s, 25)
        for e in events:  # v2.20: the readable label beside the pole code
            e["target_label"] = labels.get(e["target"]) if e["target"] else None
        feed = audit.activity(s, 30)  # S18: real inspection and administrative events, automated vs human
        for e in feed:
            e["target_label"] = labels.get(e["target"]) if e["target"] else None
        rows_all = [records.row(a, labels) for a in items]
        review_counts = {k: sum(r["review_status"] == k for r in rows_all) for k in REVIEW_TABS[1:]}
        risk_counts = {k: sum(SCREEN_FILTERS[k](r) for r in rows_all) for k in SCREEN_FILTERS}
    return {"poles": {"total": len(by_pole), **buckets}, "pending_reviews": sum(r["review"] == "pending" for r in triage),
            "triage": triage, "triage_total": len(triage), "activity": events, "system": _health(st),
            "feed": feed, "inspections": {"total": len(rows_all), "review": review_counts, "screen": risk_counts},
            "notice": notice.TEXT}


def _parse_day(v: str | None, end: bool = False) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        raise HTTPException(422, {"code": "date", "message": "Dates are YYYY-MM-DD."}) from None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.replace(hour=23, minute=59, second=59) if end and len(v) <= 10 else d


STATUS_TABS = {"all": {}, "needs_review": {"review": "pending,flagged"}, "failed": {"risk": "failed,processing"},
               "low": {"risk": "low"}}
SORTS = {"triage": None, "pole": lambda r: (r["label"] or r["pole_code"]), "location": lambda r: (r["barangay"] or "~"),
         "risk": lambda r: TRIAGE_ORDER.get(r["risk"], 8 if r["risk"] == "low" else 9),
         "inspected": lambda r: r["created_at"], "review": lambda r: r["review"],
         # S14: the operational status and the assignee (records.row, app/services/ops.py)
         "status": lambda r: (OP_ORDER.get(r.get("op_status"), 9), r["created_at"]),
         "assigned": lambda r: ((r.get("assignee") or {}).get("name") or "~").lower(),
         # S18
         "risk_level": lambda r: (RISK_ORDER.get((r.get("risk_level") or {}).get("level"), 3), -(r.get("problem_count") or 0)),
         "problem": lambda r: r.get("problem_count") or 0, "detected": lambda r: r.get("detected") or 0,
         "review_status": lambda r: REVIEW_ORDER.get(r.get("review_status"), 9),
         "filename": lambda r: (r.get("filename") or "").lower()}
# S18: review progress (administrative) and screening filters (machine), never mixed in one control
RISK_ORDER = {"high": 0, "moderate": 1, "normal": 2}
REVIEW_ORDER = {"NOT_YET_REVIEWED": 0, "PARTIALLY_REVIEWED": 1, "REVIEW_COMPLETE": 2}
REVIEW_TABS = ("all", "NOT_YET_REVIEWED", "PARTIALLY_REVIEWED", "REVIEW_COMPLETE")
SCREEN_FILTERS = {"high": lambda r: (r.get("risk_level") or {}).get("level") == "high",
                  "moderate": lambda r: (r.get("risk_level") or {}).get("level") == "moderate",
                  "normal": lambda r: (r.get("risk_level") or {}).get("level") == "normal",
                  "processing_issue": lambda r: r.get("machine_state") in ("failed", "processing"),
                  "missing_location": lambda r: not r.get("has_location")}
# S14 (D-30): status tabs on the operational vocabulary. "attention" is every latest-state that is not reviewed.
OP_ORDER = {"processing_failed": 0, "flagged": 1, "incomplete": 2, "processing": 3, "awaiting_review": 4, "reviewed": 5}
OP_TABS = {"all": None, "attention": {"processing_failed", "flagged", "incomplete", "awaiting_review", "processing"},
           "flagged": {"flagged"}, "incomplete": {"incomplete"}, "failed": {"processing_failed", "processing"},
           "reviewed": {"reviewed"}}


def _filtered(s, q, risk, review, barangay, date_from, date_to, completeness, latest_only, include_test,
              sort: str = "triage", desc: bool = False, status: str | None = None, assignee: str | None = None):
    labels = all_labels(s)
    rows = [records.row(a, labels) for a in _all_analyses(s, include_test)]
    seen_day: dict[tuple, int] = {}
    for r in rows:  # v2.20: the same pole inspected more than once on the same day
        k = (r["pole_code"], r["created_at"][:10])
        seen_day[k] = seen_day.get(k, 0) + 1
    for r in rows:
        r["same_day"] = seen_day[(r["pole_code"], r["created_at"][:10])]
    if latest_only:
        seen, keep = set(), []
        for r in rows:  # newest first: the first row of each pole is its latest inspection
            if r["pole_code"] not in seen:
                seen.add(r["pole_code"])
                keep.append(r)
        rows = keep
    if q:
        rows = [r for r in rows if records.matches(r, q)]
    if risk:
        rows = [r for r in rows if r["risk"] in risk.split(",")]
    if review:
        rows = [r for r in rows if r["review"] in review.split(",")]
    if barangay:
        rows = [r for r in rows if (r["barangay"] or "") == ("" if barangay == "__none__" else barangay)]
    if completeness:
        rows = [r for r in rows if r["completeness"] in completeness.split(",")]
    lo, hi = _parse_day(date_from), _parse_day(date_to, end=True)
    if lo or hi:
        rows = [r for r in rows if (not lo or datetime.fromisoformat(r["created_at"]) >= lo)
                and (not hi or datetime.fromisoformat(r["created_at"]) <= hi)]
    if status:
        rows = [r for r in rows if r.get("op_status") in status.split(",")]
    if assignee:
        rows = [r for r in rows if (not r.get("assignee") if assignee == "none"
                                    else str((r.get("assignee") or {}).get("id")) == assignee)]
    key = SORTS.get(sort)
    if key is None:  # default: records needing attention first, then newest
        rows.sort(key=lambda r: (_triage_key(r)[:2] if r["review"] in ("pending", "flagged") or r["risk"] in
                                 ("failed", "processing") else (3, 9), -datetime.fromisoformat(r["created_at"]).timestamp()))
    else:
        rows.sort(key=key, reverse=desc)
    return rows


Filt = Annotated[str | None, Query(max_length=120)]


@router.get("/inspections")
def inspections(st: State, user: User, q: Filt = None, risk: Filt = None, review: Filt = None, barangay: Filt = None,
                date_from: Filt = None, date_to: Filt = None, completeness: Filt = None, latest_only: bool = False,
                include_test: bool = False, tab: Annotated[str, Query(pattern="^(all|needs_review|failed|low)$")] = "all",
                sort: Annotated[str, Query(pattern="^(triage|pole|location|risk|inspected|review|status|assigned|risk_level|problem|detected|review_status|filename)$")] = "triage",
                status: Annotated[str | None, Query(pattern=r"^[a-z_]+(,[a-z_]+)*$", max_length=120)] = None,
                assignee: Annotated[str | None, Query(pattern=r"^(none|\d+)$")] = None,
                op_tab: Annotated[str | None, Query(pattern="^(all|attention|flagged|incomplete|failed|reviewed)$")] = None,
                mismatch: bool = False, desc: bool = False, page: Annotated[int, Query(ge=1)] = 1,
                review_status: Annotated[str | None, Query(pattern="^(all|NOT_YET_REVIEWED|PARTIALLY_REVIEWED|REVIEW_COMPLETE)$")] = None,
                screen: Annotated[str | None, Query(pattern="^(high|moderate|normal|processing_issue|missing_location)$")] = None,
                per_page: Annotated[int, Query(ge=5, le=500)] = 25, limit: Annotated[int | None, Query(ge=1, le=1000)] = None,
                offset: Annotated[int, Query(ge=0)] = 0) -> dict:
    """Inspections, newest first or by triage, filtered and paginated server-side. ``include_test`` is honoured for
    administrators only; everyone else always gets operational records."""
    include_test = include_test and getattr(user, "role", "admin") == "admin"
    t = STATUS_TABS[tab]
    risk, review = risk or t.get("risk"), review or t.get("review")
    with st.db.session() as s:
        labels = all_labels(s)
        everything = [records.row(a, labels) for a in _all_analyses(s, include_test)]
        if op_tab and OP_TABS[op_tab] and not status:
            status = ",".join(sorted(OP_TABS[op_tab]))
        rows = _filtered(s, q, risk, review, barangay, date_from, date_to, completeness, latest_only, include_test,
                         sort, desc, status, assignee)
    if mismatch:  # S16: barangay recorded for a different city than the inspection's (reported, never auto-corrected)
        rows = [r for r in rows if (r.get("location_check") or {}).get("consistent") is False]
    # S18: counts are computed on the rows the other filters left, so the review tabs always add up to "all"
    base_rs = list(rows)
    if screen:
        rows = [r for r in rows if SCREEN_FILTERS[screen](r)]
    review_tabs = {"all": len(rows), **{k: sum(r.get("review_status") == k for r in rows) for k in REVIEW_TABS[1:]}}
    screen_tabs = {k: sum(1 for r in (base_rs if not review_status or review_status == "all" else
                                      [x for x in base_rs if x.get("review_status") == review_status]) if f(r))
                   for k, f in SCREEN_FILTERS.items()}
    if review_status and review_status != "all":
        rows = [r for r in rows if r.get("review_status") == review_status]
    if limit is not None:  # v2.19 clients
        page_rows = rows[offset: offset + limit]
    else:
        page_rows = rows[(page - 1) * per_page: page * per_page]
    tabs = {k: sum(1 for r in everything if (not v.get("risk") or r["risk"] in v["risk"].split(","))
                   and (not v.get("review") or r["review"] in v["review"].split(","))) for k, v in STATUS_TABS.items()}
    brgys = sorted({r["barangay"] for r in everything if r["barangay"]})
    return {"total": len(everything), "matched": len(rows), "items": page_rows, "page": page, "per_page": per_page,
            "pages": max(1, -(-len(rows) // per_page)), "tabs": tabs,
            "op_tabs": {k: sum(1 for r in everything if v is None or r.get("op_status") in v) for k, v in OP_TABS.items()},
            "mismatch": sum(1 for r in everything if (r.get("location_check") or {}).get("consistent") is False),
            "review_tabs": review_tabs, "screen_tabs": screen_tabs,
            "facets": {"barangays": brgys, "no_barangay": sum(1 for r in everything if not r["barangay"])}}


class BulkBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)
    action: str = Field(pattern="^(assign_barangay|mark_test|mark_operational|assign|unassign|mark_reviewed)$")
    barangay: str | None = Field(default=None, max_length=120)
    assignee_id: int | None = None  # S14: for "assign"


@router.post("/inspections/bulk")
def bulk(body: BulkBody, st: State, user: User) -> dict:
    """Bulk operations on selected inspections, each recorded in the audit trail. Marking test / operational is
    for administrators; assigning a barangay sets it on the inspections' poles."""
    if body.action in ("mark_test", "mark_operational") and getattr(user, "role", "admin") != "admin":
        raise HTTPException(403, {"code": "admin_only", "message": "Only administrators can re-classify records."})
    if body.action == "assign_barangay":
        body.barangay = official_barangay(body.barangay)
    who = audit.actor_of(user)
    done = 0
    if body.action in ("assign", "unassign", "mark_reviewed"):
        return _bulk_ops(body, st, user, who)
    with st.db.session() as s:
        for a in s.scalars(select(Analysis).where(Analysis.id.in_(body.ids)).options(selectinload(Analysis.pole))).all():
            if body.action == "assign_barangay":
                old = a.pole.barangay
                a.pole.barangay = body.barangay
                audit.record(s, who, "barangay.set", a.pole.pole_code, a.id, old=old, new=a.pole.barangay, bulk=True)
            else:
                kind = "test" if body.action == "mark_test" else "operational"
                a.record_kind, a.record_kind_reason = kind, f"Set by {who}"
                audit.record(s, who, f"record.{kind}", a.pole.pole_code, a.id, bulk=True)
            done += 1
    return {"updated": done}


def _bulk_ops(body: BulkBody, st: AppState, user, who: str) -> dict:
    """S14: assign / unassign a follow-up person, or mark the selected inspections reviewed (a "confirmed" review)."""
    from app.models.orm import AdminUser, Assignment
    done = 0
    with st.db.session() as s:
        assignee = None
        if body.action == "assign":
            assignee = s.get(AdminUser, body.assignee_id) if body.assignee_id else None
            if assignee is None or not assignee.active:
                raise HTTPException(422, {"code": "assignee", "message": "Choose an active staff account to assign."})
        for a in s.scalars(select(Analysis).where(Analysis.id.in_(body.ids)).options(
                selectinload(Analysis.pole), selectinload(Analysis.assignment), selectinload(Analysis.reviews))).all():
            if body.action == "assign":
                name = assignee.display_name or assignee.username
                if a.assignment is None:
                    a.assignment = Assignment(assignee_id=assignee.id, assignee_name=name, assigned_by=who)
                else:
                    a.assignment.assignee_id, a.assignment.assignee_name = assignee.id, name
                    a.assignment.assigned_by, a.assignment.assigned_at = who, datetime.now(UTC)
                audit.record(s, who, "inspection.assigned", a.pole.pole_code, a.id, to=name, bulk=True)
            elif body.action == "unassign":
                if a.assignment is None:
                    continue
                old = a.assignment.assignee_name
                a.assignment = None
                audit.record(s, who, "inspection.unassigned", a.pole.pole_code, a.id, old=old, bulk=True)
            else:
                if a.status in ("processing", "error"):
                    continue  # nothing to review yet
                rv = Review(analysis_id=a.id, reviewer_id=getattr(user, "id", None), reviewer_name=who,
                            decision="confirmed", note="Marked reviewed (bulk action).")
                s.add(rv)
                audit.record(s, who, "review.confirmed", a.pole.pole_code, a.id, bulk=True)
                s.flush()
                _mirror_review(s, a, rv, user)  # S18: the same decision record as every other review
            done += 1
    return {"updated": done}


class LocationBody(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


@system.put("/poles/{pole_code}/location")
def set_location(pole_code: str, body: LocationBody, st: State, user: SystemUser) -> dict:
    """Drop a pin for a pole (administrators). Saved as a manual location; the old value is kept in the audit trail."""
    from app.models.orm import PoleLocation
    with st.db.session() as s:
        bad = geo.problem(body.latitude, body.longitude)
        if bad:
            raise HTTPException(422, {"code": "location", "message": bad})
        p = s.scalar(select(Pole).where(Pole.pole_code == pole_code))
        if p is None:
            raise HTTPException(404, {"code": "not_found", "message": "Pole not found."})
        loc = s.get(PoleLocation, p.id)
        old = None if loc is None else [loc.latitude, loc.longitude, loc.source]
        if loc is None:
            s.add(PoleLocation(pole_id=p.id, latitude=body.latitude, longitude=body.longitude, source="manual"))
        else:
            loc.latitude, loc.longitude, loc.source = body.latitude, body.longitude, "manual"
        audit.record(s, audit.actor_of(user), "location.set", pole_code, None, old=old,
                     new=[body.latitude, body.longitude, "manual"])
    return {"pole_code": pole_code, "location": {"lat": body.latitude, "lon": body.longitude, "source": "manual"}}


class BarangayBody(BaseModel):
    barangay: str = Field(min_length=1, max_length=120)


@router.put("/poles/{pole_code}/barangay")
def set_barangay(pole_code: str, body: BarangayBody, st: State, user: User) -> dict:
    with st.db.session() as s:
        p = s.scalar(select(Pole).where(Pole.pole_code == pole_code))
        if p is None:
            raise HTTPException(404, {"code": "not_found", "message": "Pole not found."})
        name = official_barangay(body.barangay)
        old, p.barangay = p.barangay, name
        audit.record(s, audit.actor_of(user), "barangay.set", pole_code, None, old=old, new=p.barangay)
    return {"pole_code": pole_code, "barangay": name}


@router.delete("/poles/{pole_code}/barangay")
def clear_barangay(pole_code: str, st: State, user: User) -> dict:
    """S18: remove a barangay assignment (the X beside it). Coordinates, city and the inspections are untouched."""
    with st.db.session() as s:
        p = s.scalar(select(Pole).where(Pole.pole_code == pole_code))
        if p is None:
            raise HTTPException(404, {"code": "not_found", "message": "Pole not found."})
        old, p.barangay = p.barangay, None
        if old is not None:
            audit.record(s, audit.actor_of(user), "barangay.unset", pole_code, None, old=old, new=None)
    return {"pole_code": pole_code, "barangay": None}


def official_barangay(text: str | None) -> str:
    """v2.21: the official name, or 422. A free-text box once stored a person's name as a barangay."""
    name = barangay_svc.resolve(text)
    if name is None:
        raise HTTPException(422, {"code": "barangay", "message": f"\"{(text or '').strip()[:60]}\" is not a barangay in "
                                  "the official list (PSGC, Cavite). Pick one from the suggestions."})
    return name


@router.get("/barangays")
def barangays(q: Annotated[str, Query(max_length=60)] = "") -> list[dict]:
    """Official barangay names matching ``q`` (assignment picker)."""
    return barangay_svc.suggest(q)


@router.get("/audit")
def audit_log(st: State, limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict]:
    with st.db.session() as s:
        return audit.recent(s, limit)


def _csv_notice(rows: list[list], header: list[str], name: str) -> StreamingResponse:
    """An operational CSV opens with the notice line, so a shared spreadsheet carries it."""
    buf = io.StringIO()
    buf.write("\ufeff")  # UTF-8 BOM: Excel opens the file with the right encoding
    w = csv.writer(buf)
    w.writerow([f"{notice.TITLE}: {notice.TEXT}"])
    w.writerow([])
    if header:
        w.writerow(header)
    w.writerows(rows)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/exports/inspections.csv")
def inspections_csv(st: State, user: User, q: Filt = None, risk: Filt = None, review: Filt = None,
                    barangay: Filt = None, date_from: Filt = None, date_to: Filt = None, completeness: Filt = None,
                    latest_only: bool = False,
                    ids: Annotated[str | None, Query(pattern=r"^[0-9a-f]{32}(,[0-9a-f]{32}){0,499}$")] = None,
                    review_status: Annotated[str | None, Query(pattern="^(all|NOT_YET_REVIEWED|PARTIALLY_REVIEWED|REVIEW_COMPLETE)$")] = None,
                    screen: Annotated[str | None, Query(pattern="^(high|moderate|normal|processing_issue|missing_location)$")] = None,
                    ) -> StreamingResponse:
    """The inspection list as filtered on screen (operational records only). S14: ``ids`` limits it to the rows
    selected on the dashboard."""
    with st.db.session() as s:
        rows = _filtered(s, q, risk, review, barangay, date_from, date_to, completeness, latest_only, False)
    if ids:
        keep = set(ids.split(","))
        rows = [r for r in rows if r["id"] in keep]
    if screen:  # S18: the same filters as the list on screen
        rows = [r for r in rows if SCREEN_FILTERS[screen](r)]
    if review_status and review_status != "all":
        rows = [r for r in rows if r.get("review_status") == review_status]
    out = [[r["pole_code"], r["inspection_name"] or "", r["barangay"] or "", r["city"] or "",
            records.RISK_LABEL[r["risk"]], records.COMPLETENESS_LABEL[r["completeness"]],
            r["wires"]["high"], r["wires"]["medium"], r["wires"]["low"], r["wires"]["unassessed"],
            records.REVIEW_LABEL[r["review"]], r["reviewed_by"] or "",
            f"{r['location']['lat']:.6f}" if r["location"] else "", f"{r['location']['lon']:.6f}" if r["location"] else "",
            r["created_at"][:19].replace("T", " "), r["id"],
            r.get("op_status_label", ""), r.get("op_reason", ""), (r.get("assignee") or {}).get("name", ""),
            (r.get("result") or {}).get("label", ""),
            # S18: machine screening, triage and the administrative decision, in separate columns
            r.get("filename") or "", "" if r.get("machine_state") != "processed" else r.get("detected"),
            "" if r.get("machine_state") != "processed" else r.get("problem_count"),
            (r.get("risk_level") or {}).get("label", "Processing failed" if r.get("machine_state") == "failed" else ""),
            (r.get("risk_level") or {}).get("rubric_version", ""), r.get("review_status_label", ""),
            (r.get("decision") or {}).get("label", ""), (r.get("decision") or {}).get("by", "")] for r in rows]
    return _csv_notice(out, ["Pole ID", "Inspection name", "Barangay", "City", "Risk level", "Assessment",
                             "High-risk wires", "Moderate-risk wires", "Low-risk wires", "Wires not assessed",
                             "Review", "Reviewed by", "Latitude", "Longitude", "Inspected (UTC)", "Inspection ID",
                             "Status", "Status reason", "Assigned to", "Screening result",
                             "File name", "Wires detected", "Problem wires", "Triage risk (operational rubric)",
                             "Rubric version", "Review status", "Administrative decision", "Decided by"],
                       f"wireframe-inspections-{_stamp()}.csv")


@router.get("/inspections/{analysis_id}/findings.csv")
def findings_csv(analysis_id: str, st: State) -> StreamingResponse:
    """One inspection's wire findings in plain terms (the technical per-wire export is in the system area)."""
    from app.services.reasons import wire_reason_short
    with st.db.session() as s:
        a = s.get(Analysis, analysis_id)
        if a is None:
            raise HTTPException(404, {"code": "not_found", "message": "Inspection not found."})
        cls = {"power-distribution-lines": "Power line", "telecom-cable": "Telecom cable",
               "abandoned-wire": "Abandoned wire"}
        cond = {"high": "High risk", "medium": "Moderate risk", "low": "Low risk"}
        out = []
        from app.services.basis import level_withdrawn
        from app.services.comparison import compare_detection, detection_states, position_text
        for d in a.detections:
            # S4: a level from a withdrawn criterion is not a finding (app/services/basis.py)
            withdrawn = level_withdrawn(d.level, d.wire_class, st.config, a.metadata_json or {})
            scored = d.status == "scored" and d.level and not withdrawn
            if d.status == "service_drop":
                condition, finding = "Not checked", "Drop wire to a building; not checked for sag"
            elif scored:
                condition = cond[d.level]
                finding = {"high": "Hangs low", "medium": "Some sag", "low": "Sag within the normal range"}[d.level]
            else:
                code = "withdrawn_reference" if withdrawn else (d.fail_reason or d.status)
                condition, finding = "Not assessed", wire_reason_short(code) or "Not measured"
                ratio = d.sag_ratio if d.sag_ratio is not None else (d.fit_json or {}).get("sag_ratio")
                res = (d.fit_json or {}).get("sag_ratio_resolution")
                if ratio is not None and d.status in ("scored", "not_assessed"):
                    finding += (f"; image-derived sag ratio {ratio * 100:.1f}%" + (f" +/- {res * 100:.1f}" if res else "")
                                + (" of the visible part" if d.fail_reason == "endpoint_unavailable" else "")
                                + " (measurement only)")
            if "vegetation_contact" in (d.flags_json or []):
                finding += "; vegetation overlaps the wire in the photo (check on site)"
            c = compare_detection(d, st.config)  # S8: the configured reference, compared (not an assessment)
            s9 = detection_states(d, st.config)  # S12: coverage / reference / assessment kept apart
            out.append([d.wire_index, cls.get(d.wire_class, d.wire_class), condition, finding,
                        {"full_span": "Full span", "edge_clipped": "Edge clipped"}.get(s9["coverage"], "Unmeasured" if s9["measurement"] == "unmeasured" else ""),
                        "Available" if s9["reference_available"] else REF_WHY.get(s9["reference_unavailable_reason"], "Unavailable"),
                        "Assessed" if s9["assessed"] else "Unassessed",
                        f"{c['reference'] * 100:.1f}% ({c['status']})" if c else "Not applicable",
                        position_text(c),
                        f"{c['difference_pp']:+.1f}" if c and c["position"] else ""])
        name = f"wireframe-{a.pole.pole_code}-{a.id[:8]}-findings.csv"
        head = [[f"Pole {a.pole.pole_code}", a.inspection_name or "", a.created_at.isoformat()[:19]]]
    return _csv_notice(head + [[]] + [["Wire", "Type", "Condition", "Finding", "Coverage", "Reference applicability", "Assessment",
                                           "Sag reference", "Compared with reference (image ratio)",
                                           "Difference (percentage points)"]] + out, [], name)


@router.get("/inspections/{analysis_id}/context")
def inspection_context(analysis_id: str, st: State, user: User) -> dict:
    """What the inspection sheet shows beside the result: plain status, the pole's other inspections, its place in
    the inspection order, and what this account may do."""
    with st.db.session() as s:
        a = s.get(Analysis, analysis_id)
        if a is None:
            raise HTTPException(404, {"code": "not_found", "message": "Inspection not found."})
        is_admin = getattr(user, "role", "admin") == "admin"
        hist = sorted(a.pole.analyses, key=lambda x: x.created_at, reverse=True)
        labels = all_labels(s)
        history = [records.row(x, labels) for x in hist if is_admin or records.is_operational(x)]
        rank = sites.rank_of(s, st.config, a.pole.pole_code)
        operational_rank = rank if rank and rank.get("rank") and records.is_operational(a) else None
        return {"row": records.row(a, labels), "record_kind": a.record_kind or records.classify_analysis(a)[0],
                "record_kind_reason": a.record_kind_reason,
                "history": history, "priority": None if not operational_rank else
                {"position": operational_rank["rank"], "of": operational_rank["of"]},
                # S18: a failed run can still be decided
                "archived": None if a.archived_at is None else
                {"at": a.archived_at.isoformat(), "by": a.archived_by, "reason": a.archived_reason},
                "can": {"review": a.status != "processing" and a.archived_at is None,
                        "delete": is_admin and a.status != "processing" and a.archived_at is None,
                        "restore": is_admin and a.archived_at is not None,
                        "retry": a.status == "error", "diagnostics": is_admin, "reclassify": is_admin},
                "notice": notice.TEXT}


# ----------------------------------------------------------------------------- v2.19 system area


class RecordKindBody(BaseModel):
    record_kind: str = Field(pattern="^(operational|test)$")
    reason: str = Field(default="", max_length=160)


@system.put("/inspections/{analysis_id}/record-kind")
def set_record_kind(analysis_id: str, body: RecordKindBody, st: State, user: SystemUser) -> dict:
    """Re-classify one record. Nothing is deleted; the record only moves in or out of the operational views."""
    with st.db.session() as s:
        a = s.get(Analysis, analysis_id)
        if a is None:
            raise HTTPException(404, {"code": "not_found", "message": "Inspection not found."})
        a.record_kind = body.record_kind
        a.record_kind_reason = (body.reason.strip() or f"Set by {user.display_name or user.username}")[:160]
        audit.record(s, audit.actor_of(user), f"record.{body.record_kind}", a.pole.pole_code, a.id)
        return {"id": a.id, "record_kind": a.record_kind, "record_kind_reason": a.record_kind_reason}


class ArchiveBody(BaseModel):
    reason: str = Field(min_length=1, max_length=300)


@system.post("/inspections/{analysis_id}/archive")
def archive_inspection(analysis_id: str, body: ArchiveBody, st: State, user: SystemUser) -> dict:
    """S18 "Delete inspection": an audited soft delete. Nothing is removed (photo, findings, decisions, audit); the
    inspection leaves every operational view and can be restored. Permanent deletion stays the separate
    DELETE /api/analysis/{id} (no portal control)."""
    reason = body.reason.strip()
    if len(reason) < 3:
        raise HTTPException(422, {"code": "reason", "message": "Say why the inspection is being deleted."})
    with st.db.session() as s:
        a = s.get(Analysis, analysis_id)
        if a is None:
            raise HTTPException(404, {"code": "not_found", "message": "Inspection not found."})
        if a.status == "processing":
            raise HTTPException(409, {"code": "processing", "message": "Wait until processing has finished."})
        if a.archived_at is not None:
            raise HTTPException(409, {"code": "archived", "message": "This inspection is already deleted (archived)."})
        a.archived_at, a.archived_by, a.archived_reason = datetime.now(UTC), audit.actor_of(user), reason[:300]
        audit.record(s, audit.actor_of(user), "inspection.archived", a.pole.pole_code, a.id, reason=reason[:300],
                     file=a.original_filename)
        return {"id": a.id, "archived": {"at": a.archived_at.isoformat(), "by": a.archived_by, "reason": a.archived_reason}}


@system.post("/inspections/{analysis_id}/restore")
def restore_inspection(analysis_id: str, st: State, user: SystemUser) -> dict:
    with st.db.session() as s:
        a = s.get(Analysis, analysis_id)
        if a is None:
            raise HTTPException(404, {"code": "not_found", "message": "Inspection not found."})
        if a.archived_at is None:
            raise HTTPException(409, {"code": "not_archived", "message": "This inspection is not archived."})
        old = {"at": a.archived_at.isoformat(), "by": a.archived_by, "reason": a.archived_reason}
        a.archived_at = a.archived_by = a.archived_reason = None
        audit.record(s, audit.actor_of(user), "inspection.restored", a.pole.pole_code, a.id, old=old)
        return {"id": a.id, "archived": None}


@system.get("/system/status")
def system_status(st: State) -> dict:
    from app.core import weights as wts
    return {"health": _health(st), "downloads": wts.status(), "config_fingerprint": st.config.fingerprint,
            "provisional": st.config.provisional_blocks(), "queue": queue_state(st)}


# ----------------------------------------------------------------------------- poles & temporal tracking


# ----------------------------------------------------------------------------- exports


def _csv(rows: list[list], header: list[str], name: str) -> StreamingResponse:
    buf = io.StringIO()
    buf.write("﻿")  # UTF-8 BOM: Excel opens the file with the right encoding
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M")


@system.get("/exports/manifest.csv")
def manifest(st: State) -> StreamingResponse:
    """One row per pole: latest finished analysis, MSPI rank, review state, location."""
    with st.db.session() as s:
        ranked, index = sites.ranking(s, st.config)
        rows = []
        for r in ranked:
            pole, a = index[r.site_id]
            rev = a.reviews[-1] if a.reviews else None
            loc = pole.location
            rows.append([r.rank or "", r.site_id, pole.barangay or "", f"{r.mspi:.4f}" if r.mspi is not None else "",
                         r.level or "", f"{r.max_crs:.2f}" if r.max_crs is not None else "",
                         f"{r.mean_crs:.2f}" if r.mean_crs is not None else "", f"{r.vni:.4f}", a.vni_band or "",
                         r.n_wires, a.segmentation_source or "", rev.decision if rev else "unreviewed",
                         rev.override_level if rev and rev.override_level else "",
                         f"{loc.latitude:.6f}" if loc else "", f"{loc.longitude:.6f}" if loc else "",
                         a.created_at.isoformat(), a.id, ";".join(r.flags)])
    return _csv(rows, ["rank", "pole_code", "barangay", "mspi", "level", "max_crs", "mean_crs", "vni", "vni_band",
                       "wires_scored", "segmentation_source", "review", "override_level", "latitude", "longitude",
                       "analyzed_at", "analysis_id", "flags"], f"wireframe-manifest-{_stamp()}.csv")


@system.get("/exports/wires.csv")
def wires(st: State) -> StreamingResponse:
    """One row per detected wire across all analyses, with the fit and score inputs."""
    from app.services.basis import level_withdrawn
    from app.services.comparison import compare_detection
    with st.db.session() as s:
        rows = []
        for a in s.scalars(select(Analysis).options(selectinload(Analysis.detections), selectinload(Analysis.pole))
                           .order_by(Analysis.created_at)).all():
            for d in a.detections:
                f = d.fit_json or {}
                rows.append([a.id, a.pole.pole_code, a.created_at.isoformat(), d.wire_index, d.wire_class,
                             f"{d.confidence:.4f}", d.status, d.fail_reason or "", f.get("a"), f.get("b"), f.get("c"),
                             f.get("rms_residual_px"), f.get("rel_residual"), d.chord_px, d.sag_px, d.sag_ratio,
                             d.delta_lim, d.crs, d.level or "", f.get("pruned_px"), f.get("n_points"),
                             ";".join(d.flags_json),
                             # S4/S8: stored levels from the withdrawn placeholder are marked; the configured reference
                             # comparison is computed now from the stored ratio and is not an assessment
                             level_withdrawn(d.level, d.wire_class, st.config, a.metadata_json or {}),
                             *((c["reference"], c["status"], c["position"] or "not_measured", c["difference_pp"])
                               if (c := compare_detection(d, st.config)) else ("", "", "not_applicable", ""))])
    return _csv(rows, ["analysis_id", "pole_code", "analyzed_at", "wire", "class", "confidence", "status",
                       "fail_reason", "a_px", "b_px", "c_px", "rms_residual_px", "rel_residual", "chord_px", "sag_px",
                       "sag_ratio", "delta_lim", "crs", "level", "pruned_px", "points", "flags", "level_withdrawn",
                       "sag_reference", "sag_reference_status", "sag_reference_position", "sag_reference_difference_pp"],
                f"wireframe-wires-{_stamp()}.csv")


@router.get("/exports/sites.pdf")
def sites_pdf(st: State, user: User, technical: bool = False,
              alpha: float | None = None, beta: float | None = None, gamma: float | None = None) -> Response:
    """The inspection priority report. ``technical`` (or custom weights) adds the scores, weights and weight
    sensitivity, for administrators only."""
    weights = (alpha, beta, gamma) if None not in (alpha, beta, gamma) else None
    technical = technical or weights is not None
    if technical and getattr(user, "role", "admin") != "admin":
        raise HTTPException(403, {"code": "admin_only", "message": "The technical report is for administrators."})
    if weights and abs(sum(weights) - 1) > 1e-6:
        raise HTTPException(422, {"code": "weights", "message": "alpha + beta + gamma must equal 1."})
    with st.db.session() as s:
        payload = sites.ranking_payload(s, st.config, weights)
        sens = sites.sensitivity_payload(s, st.config, baseline=weights)
    pdf = build_sites_report(payload, sens, prepared_by=user.display_name or user.username,
                             provisional=st.config.provisional_blocks(), technical=technical)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="wireframe-sites-{_stamp()}.pdf"'})


MAX_STOPS = 25


@router.get("/routes/pdf")
def route_pdf(request: Request, st: State, user: User,
              stops: Annotated[str, Query(min_length=1, max_length=2000, pattern=r"^[A-Za-z0-9_.\-]+(,[A-Za-z0-9_.\-]+)*$")]
              ) -> Response:
    """v2.21: printable dispatch route for the stops picked on Map & Locations, in the given order (pole codes).
    Only poles with a usable recorded location; test records are not included."""
    from app.services.route_pdf import build_route_pdf
    codes = list(dict.fromkeys(stops.split(",")))[:MAX_STOPS]
    with st.db.session() as s:
        labels = all_labels(s)
        latest = {a.pole.pole_code: a for a in _all_analyses(s)}
        out = []
        for c in codes:
            a = latest.get(c)
            loc = a.pole.location if a else None
            if a is None or loc is None or geo.problem(loc.latitude, loc.longitude):
                continue
            out.append({"label": labels.get(c, c), "pole_code": c, "risk": records.risk(a), "barangay": a.pole.barangay,
                        "lat": loc.latitude, "lon": loc.longitude})
    if len(out) < 1:
        raise HTTPException(422, {"code": "route", "message": "None of these poles has a usable location."})
    share = str(request.base_url).rstrip("/") + "/admin/map?route=" + ",".join(x["pole_code"] for x in out)
    pdf = build_route_pdf(out, audit.actor_of(user), share)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="wireframe-dispatch-route.pdf"'})
