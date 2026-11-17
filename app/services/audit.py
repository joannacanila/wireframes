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
