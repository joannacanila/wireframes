"""S18: administrative review progress and decisions (record C; never mixed with machine findings).

  NOT_YET_REVIEWED   -> PARTIALLY_REVIEWED   an authorized administrator opens the inspection detail (start)
  PARTIALLY_REVIEWED -> REVIEW_COMPLETE      the administrator saves and finalizes a decision (finalize)
  REVIEW_COMPLETE    -> PARTIALLY_REVIEWED   reopened (reopen); the decision stays in the history
  REVIEW_COMPLETE    -> REVIEW_COMPLETE      an amended decision (finalize again): the previous one is superseded

Processing finishing never changes the review status, and opening a page never completes a review. Every transition is
an audit event with the previous and the new value; a page refresh by the same reviewer records nothing.

Decision options are data (``app_settings["decision_options"]``), not code: administrators can add or retire options;
a retired option stays readable on the decisions that used it. Reviews recorded before S18 (``reviews``) are mapped
once into decisions with ``source = legacy_review``; the original rows are kept unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from app.models.orm import AdminDecision, Analysis, AppSetting, ReviewProgress
from app.services import audit

STATUS_LABEL = {"NOT_YET_REVIEWED": "Not yet reviewed", "PARTIALLY_REVIEWED": "Partially reviewed",
                "REVIEW_COMPLETE": "Review complete"}
DEFAULT_OPTIONS: list[dict[str, Any]] = [
    {"code": "field_inspection", "label": "For field inspection", "active": True},
    {"code": "utility_coordination", "label": "For utility coordination", "active": True},
    {"code": "scheduled_cleanup", "label": "For scheduled cleanup", "active": True},
    {"code": "monitoring", "label": "For monitoring", "active": True},
    {"code": "no_further_action", "label": "No further administrative action at this time", "active": True},
    {"code": "other", "label": "Other documented decision", "active": True},
]
# pre-S18 review decisions, read as decisions (meaning preserved; "flagged" was "ask for a field inspection", v2.19)
LEGACY = {"flagged": ("field_inspection", "For field inspection"),
          "dismissed": ("no_further_action", "No further administrative action at this time"),
          "confirmed": ("legacy_confirmed", "Machine finding confirmed (review before S18)"),
          "overridden": ("legacy_overridden", "Risk level changed (review before S18)")}


class ReviewError(ValueError):
    pass


def now() -> datetime:
    return datetime.now(UTC)


def who(user) -> str:
    return user.display_name or user.username


def status_of(a: Analysis) -> str:
    p = a.__dict__.get("review_progress") if "review_progress" in a.__dict__ else a.review_progress
    return p.status if p else "NOT_YET_REVIEWED"


def current_decision(a: Analysis) -> AdminDecision | None:
    ds = [d for d in a.decisions if d.superseded_at is None]
    return ds[-1] if ds else None


def current_critical(a: Analysis) -> list[dict]:
    d = current_decision(a)
    return list(d.critical_json or []) if d else []


def decision_view(d: AdminDecision | None) -> dict[str, Any] | None:
    if d is None:
        return None
    return {"id": d.id, "code": d.decision, "label": d.decision_label, "rationale": d.rationale,
            "follow_up": d.follow_up, "observations": d.observations, "critical": d.critical_json or [],
            "by": d.reviewer_name, "at": d.created_at.isoformat(), "source": d.source,
            "superseded_at": d.superseded_at.isoformat() if d.superseded_at else None,
            "machine": d.machine_json or {}}


def view(a: Analysis) -> dict[str, Any]:
    p = a.review_progress
    st = status_of(a)
    return {"status": st, "label": STATUS_LABEL[st],
            "started_at": p.started_at.isoformat() if p and p.started_at else None,
            "started_by": p.started_by if p else None,
            "completed_at": p.completed_at.isoformat() if p and p.completed_at else None,
            "completed_by": p.completed_by if p else None, "reviewers": list(p.reviewers or []) if p else [],
            "source": p.source if p else None,
            "decision": decision_view(current_decision(a)),
            "history": [decision_view(d) for d in a.decisions]}


# --------------------------------------------------------------------------- options
def options(s) -> list[dict[str, Any]]:
    row = s.get(AppSetting, "decision_options")
    return list(row.value) if row and isinstance(row.value, list) else [dict(o) for o in DEFAULT_OPTIONS]


def set_options(s, items: list[dict], user) -> list[dict]:
    old = options(s)
    seen, out = set(), []
    for o in items:
        code = str(o.get("code") or "").strip().lower()
        label = str(o.get("label") or "").strip()
        if not code or not label or not code.replace("_", "").isalnum() or code in seen or code.startswith("legacy_"):
            raise ReviewError("Each option needs a unique code (letters, digits, _) and a label.")
        seen.add(code)
        out.append({"code": code, "label": label[:120], "active": bool(o.get("active", True))})
    for o in old:  # an option in use is never deleted, only retired
        if o["code"] not in seen:
            out.append({**o, "active": False})
    if not any(o["active"] for o in out):
        raise ReviewError("At least one decision option must stay active.")
    row = s.get(AppSetting, "decision_options")
    if row is None:
        s.add(AppSetting(key="decision_options", value=out, updated_by=who(user)))
    else:
        row.value, row.updated_by = out, who(user)
    audit.record(s, audit.actor_of(user), "settings.decision_options", None, None, previous=old, new=out)
    return out


# --------------------------------------------------------------------------- transitions
def start(s, a: Analysis, user) -> bool:
    """The administrator opened the inspection detail. Returns True when something was recorded."""
    name = who(user)
    p = a.review_progress
    if p is None:
        a.review_progress = ReviewProgress(status="PARTIALLY_REVIEWED", started_at=now(), started_by=name,
                                           reviewers=[name])
        audit.record(s, audit.actor_of(user), "review.started", a.pole.pole_code, a.id,
                     previous="NOT_YET_REVIEWED", new="PARTIALLY_REVIEWED")
        return True
    if name not in (p.reviewers or []):
        p.reviewers = [*(p.reviewers or []), name]  # another reviewer joins; their actions are recorded individually
        audit.record(s, audit.actor_of(user), "review.joined", a.pole.pole_code, a.id, status=p.status)
        return True
    return False


OTHER_MIN = 5  # S18: shortest accepted description of an "Other documented decision"

def finalize(s, a: Analysis, user, body: dict[str, Any], machine: dict[str, Any]) -> AdminDecision:
    from app.services import screening
    opts = {o["code"]: o for o in options(s)}
    code = body.get("decision")
    if code not in opts or not opts[code]["active"]:
        raise ReviewError("Choose one of the active decision options.")
    rationale = str(body.get("rationale") or "").strip()
    if not rationale:
        raise ReviewError("Add the rationale for the decision.")
    label = opts[code]["label"]
    if code == "other":  # S18: the decision itself must be written down, separately from the rationale
        other = " ".join(str(body.get("other_text") or "").split())
        if len(other) < OTHER_MIN or not any(ch.isalnum() for ch in other):
            raise ReviewError(f"Describe the documented decision (at least {OTHER_MIN} characters).")
        label = f"Other: {other[:110]}"  # decision_label is VARCHAR(120)
    try:
        critical = screening.validate_critical(body.get("critical") or [])
    except screening.RubricError as e:
        raise ReviewError(str(e)) from None
    start(s, a, user)
    p = a.review_progress
    prev = current_decision(a)
    t = now()
    if prev is not None:
        prev.superseded_at = t
    d = AdminDecision(analysis_id=a.id, decision=code, decision_label=label, rationale=rationale[:4000],
                      follow_up=str(body.get("follow_up") or "").strip()[:2000],
                      observations=str(body.get("observations") or "").strip()[:4000], critical_json=critical,
                      machine_json=machine, reviewer_id=user.id, reviewer_name=who(user), created_at=t)
    a.decisions.append(d)
    was = p.status
    p.status, p.completed_at, p.completed_by = "REVIEW_COMPLETE", t, who(user)
    audit.record(s, audit.actor_of(user), "decision.amended" if prev is not None else "review.finalized",
                 a.pole.pole_code, a.id, previous={"status": was, "decision": prev.decision if prev else None},
                 new={"status": "REVIEW_COMPLETE", "decision": code, "label": label},
                 critical=[c["type"] for c in critical] or None)
    return d


def reopen(s, a: Analysis, user, reason: str) -> None:
    p = a.review_progress
    if p is None or p.status != "REVIEW_COMPLETE":
        raise ReviewError("Only a completed review can be reopened.")
    if not reason.strip():
        raise ReviewError("Say why the review is reopened.")
    p.status = "PARTIALLY_REVIEWED"
    if who(user) not in (p.reviewers or []):
        p.reviewers = [*(p.reviewers or []), who(user)]
    audit.record(s, audit.actor_of(user), "review.reopened", a.pole.pole_code, a.id,
                 previous="REVIEW_COMPLETE", new="PARTIALLY_REVIEWED", reason=reason.strip()[:500])


# --------------------------------------------------------------------------- migration (pre-S18 reviews)
def backfill_legacy(conn) -> int:
    """Map pre-S18 reviews into decisions once (idempotent: keyed on ``legacy_review_id``). Inspections nobody reviewed
    get no row and so read NOT_YET_REVIEWED; a processed result is never treated as reviewed."""
    rows = conn.execute(text(
        "SELECT r.id, r.analysis_id, r.reviewer_id, r.reviewer_name, r.decision, r.note, r.created_at "
        "FROM reviews r WHERE NOT EXISTS (SELECT 1 FROM admin_decisions d WHERE d.legacy_review_id = r.id) "
        "ORDER BY r.analysis_id, r.created_at, r.id")).all()
    if not rows:
        return 0
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r.analysis_id, []).append(r)
    for aid, revs in by.items():
        has_workflow = conn.execute(text("SELECT 1 FROM admin_decisions WHERE analysis_id = :a AND source = "
                                         "'workflow'"), {"a": aid}).first()
        for i, r in enumerate(revs):
            code, label = LEGACY[r.decision]
            sup = revs[i + 1].created_at if i + 1 < len(revs) else (r.created_at if has_workflow else None)
            conn.execute(text(
                "INSERT INTO admin_decisions (analysis_id, decision, decision_label, rationale, follow_up, "
                "observations, critical_json, machine_json, reviewer_id, reviewer_name, source, legacy_review_id, "
                "created_at, superseded_at) VALUES (:a, :c, :l, :n, '', '', '[]', '{}', :rid, :rn, 'legacy_review', "
                ":id, :t, :sup)"), {"a": aid, "c": code, "l": label, "n": r.note or "", "rid": r.reviewer_id,
                                    "rn": r.reviewer_name, "id": r.id, "t": r.created_at, "sup": sup})
        last = revs[-1]
        if not conn.execute(text("SELECT 1 FROM review_progress WHERE analysis_id = :a"), {"a": aid}).first():
            conn.execute(text(
                "INSERT INTO review_progress (analysis_id, status, started_at, started_by, completed_at, completed_by, "
                "reviewers, source, updated_at) VALUES (:a, 'REVIEW_COMPLETE', :t0, :b0, :t, :b, :rs, "
                "'legacy_review', :t)"), {"a": aid, "t0": revs[0].created_at, "b0": revs[0].reviewer_name,
                                          "t": last.created_at, "b": last.reviewer_name,
                                          "rs": __import__("json").dumps(sorted({x.reviewer_name for x in revs}))})
    return len(rows)


def load_rubric(s) -> dict[str, Any]:
    from app.services import screening
    row = s.get(AppSetting, "risk_rubric")
    screening.CURRENT = {**screening.DEFAULT_RUBRIC, **(row.value if row and isinstance(row.value, dict) else {})}
    return screening.CURRENT


def set_rubric(s, body: dict[str, Any], user) -> dict[str, Any]:
    from app.services import screening
    reason = str(body.get("reason") or "").strip()
    if not reason:
        raise ReviewError("State the reason and source for a rubric change.")
    old = load_rubric(s)
    try:
        new = screening.validate_rubric(body, old)
    except screening.RubricError as e:
        raise ReviewError(str(e)) from None
    new["changed_by"], new["reason"] = who(user), reason[:500]
    row = s.get(AppSetting, "risk_rubric")
    if row is None:
        s.add(AppSetting(key="risk_rubric", value=new, updated_by=who(user)))
    else:
        row.value, row.updated_by = new, who(user)
    audit.record(s, audit.actor_of(user), "settings.risk_rubric", None, None,
                 previous={k: old.get(k) for k in ("version", "moderate_from", "high_from", "problem_types",
                                                   "critical_types", "effective_date")},
                 new={k: new.get(k) for k in ("version", "moderate_from", "high_from", "problem_types",
                                              "critical_types", "effective_date")}, reason=reason[:500])
    screening.CURRENT = new
    return new


def rubric_view() -> dict[str, Any]:
    from app.services import screening
    return {**screening.CURRENT, "problem_type_labels": screening.PROBLEM_TYPES,
            "critical_type_labels": screening.CRITICAL_TYPES}

