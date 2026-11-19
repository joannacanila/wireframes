"""v2.20 audit trail: one append-only row per administrative action (app/models/orm.py AuditEvent)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.orm import AuditEvent

TEXT = {
    "review.confirmed": "confirmed the finding", "review.flagged": "flagged for further inspection",
    "review.overridden": "changed the risk level", "review.dismissed": "dismissed the finding",
    "location.set": "set the location", "barangay.set": "assigned a barangay",
    "barangay.cleared": "cleared an invalid barangay",
    "record.test": "marked as a test record", "record.operational": "marked as an operational record",
    "inspection.deleted": "deleted an inspection", "inspection.failed": "could not process",
    "inspection.retried": "re-ran processing", "model.activated": "activated a model",
    "settings.changed": "changed a setting",
    "inspection.assigned": "assigned for follow-up", "inspection.unassigned": "removed the assignment",
    "contact.created": "added a directory contact", "contact.updated": "updated a directory contact",
    "contact.deactivated": "deactivated a directory contact",
    # S18: review workflow, decisions, rubric and provider evidence
    "review.started": "started the review", "review.joined": "joined the review",
    "review.finalized": "finalized the decision", "decision.amended": "amended the decision",
    "review.reopened": "reopened the review", "settings.risk_rubric": "changed the risk rubric",
    "settings.decision_options": "changed the decision options", "attachment.confirmed": "confirmed an attachment owner",
    "inspection.exported": "exported inspections", "location.corrected": "corrected a location",
    "provider.verified": "verified a provider record",
    "inspection.archived": "deleted (archived) an inspection", "inspection.restored": "restored an archived inspection",
    "barangay.unset": "removed the barangay",
}


def actor_of(user) -> str:
    return getattr(user, "display_name", None) or getattr(user, "username", None) or "System"


def record(s: Session, actor: str, action: str, target: str | None = None, analysis_id: str | None = None,
           **detail: Any) -> None:
    s.add(AuditEvent(actor=actor, action=action, target=target, analysis_id=analysis_id, detail=detail))


def recent(s: Session, limit: int = 30) -> list[dict]:
    rows = s.scalars(select(AuditEvent).order_by(AuditEvent.at.desc(), AuditEvent.id.desc()).limit(limit)).all()
    return [{"at": e.at.isoformat(), "actor": e.actor, "action": e.action, "text": TEXT.get(e.action, e.action),
             "target": e.target, "analysis_id": e.analysis_id, "detail": e.detail} for e in rows]


# S14: what people did (reviews, assignments, locations, directory) versus what the system did (processing failures,
# migrations, data repairs, test-record classification). The audit trail itself is unchanged; this only filters it.
SYSTEM_ACTIONS = {"inspection.failed", "record.test", "record.operational", "barangay.cleared", "model.activated",
                  "settings.changed"}


def is_system(e: dict) -> bool:
    return e["actor"] == "System" or e["actor"].startswith("System ") or e["action"] in SYSTEM_ACTIONS


def split(s: Session, limit: int = 20) -> dict[str, list[dict]]:
    rows = recent(s, 300)
    team = [e for e in rows if not is_system(e)][:limit]
    system = [e for e in rows if is_system(e)][:limit]
    return {"team": team, "system": system, "all": rows[:limit]}


# S18: the dashboard activity feed. Human and automated events from real records only: audit rows (people, and the
# system's own recorded actions) plus each inspection's own timestamps (received, processing finished). Reading a page
# never writes an event.
KIND = {"review.started": "review", "review.joined": "review", "review.finalized": "decision",
        "decision.amended": "decision", "review.reopened": "review", "review.confirmed": "decision",
        "review.flagged": "decision", "review.overridden": "decision", "review.dismissed": "decision",
        "location.set": "location", "barangay.set": "location", "location.corrected": "location",
        "contact.created": "provider", "contact.updated": "provider", "provider.verified": "provider",
        "attachment.confirmed": "provider", "inspection.failed": "failure", "inspection.exported": "export",
        "barangay.unset": "location", "inspection.archived": "archive", "inspection.restored": "archive",
        "inspection.deleted": "archive", "inspection.retried": "processing"}


def activity(s: Session, limit: int = 25, include_test: bool = False) -> list[dict]:
    from app.models.orm import Analysis
    from app.services.records import listed
    out = []
    for e in recent(s, 200):
        out.append({**e, "kind": KIND.get(e["action"], "admin"), "automated": is_system(e)})
    rows = s.scalars(select(Analysis).order_by(Analysis.created_at.desc()).limit(limit * 2)).all()
    for a in rows:
        if not listed(a, include_test):
            continue
        name = a.original_filename
        out.append({"at": a.created_at.isoformat(), "actor": "Field Screener" if a.submitted_via == "field"
                    else "Admin upload", "action": "inspection.received", "text": "new inspection received",
                    "target": a.pole.pole_code, "analysis_id": a.id, "detail": {"file": name},
                    "kind": "received", "automated": True})
        if a.completed_at and a.status not in ("processing", "error"):  # a failure is already an audit row
            out.append({"at": a.completed_at.isoformat(), "actor": "System", "action": "inspection.processed",
                        "text": "image processing completed", "target": a.pole.pole_code, "analysis_id": a.id,
                        "detail": {"file": name}, "kind": "processed", "automated": True})
    out.sort(key=lambda e: e["at"], reverse=True)
    return out[:limit]
