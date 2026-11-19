"""S14 routes: the admin overview (operational dashboard), staff list, provider directory and per-inspection contacts.

Permissions are enforced here, not in the page: reading the directory and the overview needs a staff sign-in;
changing the directory needs an administrator; the public gets only verified / secondary-source suggestions for an
inspection whose id it holds (the id is the capability, as for the result itself).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.security import require_admin, require_system
from app.core.state import AppState, get_state
from app.models.orm import AdminUser, Analysis, Contact
from app.services import audit, contacts, ops, records
from app.services.labels import all_labels

State = Annotated[AppState, Depends(get_state)]
User = Annotated[object, Depends(require_admin)]
SystemUser = Annotated[object, Depends(require_system)]

public = APIRouter(prefix="/api/analysis", tags=["contacts"])
staff = APIRouter(prefix="/api/admin", tags=["operations"], dependencies=[Depends(require_admin)])


@public.get("/{analysis_id}/contacts")
def inspection_contacts(analysis_id: str, st: State) -> dict:
    with st.db.session() as s:
        a = s.get(Analysis, analysis_id)
        if a is None:
            raise HTTPException(404, {"code": "not_found", "message": "Analysis not found."})
        return contacts.for_analysis(s, a)


@staff.get("/operations")
def operations(st: State, user: User) -> dict:
    """The operational dashboard: distinct counts of each pole's latest inspection, the needs-attention queue (every
    latest inspection not yet reviewed, most urgent first) and activity split into team and system."""
    from app.api.admin import _health
    with st.db.session() as s:
        labels = all_labels(s)
        rows = records.latest_rows(s, labels)  # S16: the same pole rows the map uses
        coords: dict[tuple, int] = {}
        for r in rows:
            if r["place"]["coords"]:
                k = tuple(r["place"]["coords"])
                coords[k] = coords.get(k, 0) + 1
        for r in rows:  # identical coordinates on several poles: shown for checking, never merged
            r["shared_coords"] = coords.get(tuple(r["place"]["coords"]), 1) - 1 if r["place"]["coords"] else 0
        counts = {k: sum(r["op_status"] == k for r in rows) for k in ops.STATUS_LABEL}
        queue = sorted((r for r in rows if r["op_status"] != "reviewed"),
                       key=lambda r: (ops.STATUS_ORDER[r["op_status"]], -datetime.fromisoformat(r["created_at"]).timestamp()))
        n_inspections = s.info.pop("wf_inspections_counted", None)
        activity = audit.split(s, 20)
        for group in activity.values():
            for e in group:
                e["target_label"] = labels.get(e["target"]) if e["target"] else None
        feed = audit.activity(s, 40)  # S18: real inspection and administrative events
        for e in feed:
            e["target_label"] = labels.get(e["target"]) if e["target"] else None
    me = getattr(user, "id", None)
    recent = sorted((r for r in rows if r["op_status"] not in ("processing", "processing_failed")),
                    key=lambda r: r["created_at"], reverse=True)[:6]
    return {"me": me, "recent": recent,
            "mine": sum(1 for r in queue if (r.get("assignee") or {}).get("id") == me),
            # S16: every count names its entity. Poles: one row per pole (its latest inspection), the same rows as the
            # map; inspections: every operational upload (a pole can have several); the queue is poles not reviewed
            "fleet": {"poles": len(rows), "inspections": n_inspections, "queue": len(queue),
                      "located": sum(1 for r in rows if r["place"]["coords"]),
                      "needs_pinning": sum(1 for r in rows if not r["place"]["coords"]),
                      "location_mismatch": sum(1 for r in rows if (r.get("location_check") or {}).get("consistent") is False)},
            "metrics": {"registered_poles": len(rows), "flagged": counts["flagged"],
                        "processing_failed": counts["processing_failed"], "incomplete": counts["incomplete"],
                        "awaiting_review": counts["awaiting_review"], "reviewed": counts["reviewed"],
                        "processing": counts["processing"]},
            "queue": queue, "activity": activity, "system": _health(st),
            # S18: every pole's latest inspection (any review status), the review-status counts and the event feed
            "poles": rows, "feed": feed,
            "review_counts": {k: sum(r["review_status"] == k for r in rows)
                              for k in ("NOT_YET_REVIEWED", "PARTIALLY_REVIEWED", "REVIEW_COMPLETE")},
            "status_labels": ops.STATUS_LABEL}


@staff.get("/users")
def users(st: State, user: User) -> list[dict]:
    with st.db.session() as s:
        return [{"id": u.id, "name": u.display_name or u.username, "role": u.role}
                for u in s.scalars(select(AdminUser).where(AdminUser.active.is_(True)).order_by(AdminUser.username))]


@staff.get("/contacts")
def list_contacts(st: State, user: User, include_inactive: bool = False) -> dict:
    with st.db.session() as s:
        q = select(Contact).order_by(Contact.org_type, Contact.organization)
        rows = [contacts.view(c, staff=True) for c in s.scalars(q) if include_inactive or c.active]
    stale = [r["id"] for r in rows if r["verification"] != "unverified" and r["last_verified"]
             and (datetime.now() - datetime.fromisoformat(r["last_verified"])).days > 365]
    return {"items": rows, "types": list(contacts.CONTACT_TYPES), "verification": list(contacts.VERIFICATION),
            "stale": stale, "unverified": [r["id"] for r in rows if r["verification"] == "unverified"],
            "updated": contacts.DIRECTORY_UPDATED, "study_area": list(contacts.STUDY_AREA)}


class ContactBody(BaseModel):
    organization: str = Field(min_length=2, max_length=160)
    org_type: str
    service_area: str = Field(default="", max_length=200)
    cities: list[str] = Field(default_factory=list, max_length=60)
    barangays: list[str] = Field(default_factory=list, max_length=200)
    phone: str | None = Field(default=None, max_length=80)
    url: str | None = Field(default=None, max_length=300)
    assistance: str = Field(default="", max_length=300)
    instructions: str = Field(default="", max_length=2000)
    source_url: str | None = Field(default=None, max_length=300)
    source_note: str = Field(default="", max_length=300)
    verification: str = "unverified"
    last_verified: str | None = None
    active: bool = True
    # S18 provider identity and evidence
    category: str | None = Field(default=None, pattern="^(electricity|telecom|broadband|catv|other_infra|emergency|government)$")
    registered_name: str | None = Field(default=None, max_length=200)
    previous_names: list[str] = Field(default_factory=list, max_length=10)
    email: str | None = Field(default=None, max_length=160, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    reporting_url: str | None = Field(default=None, max_length=300)
    source_type: str | None = Field(default=None, pattern="^(official_site|government_record|law|press|third_party)$")
    coverage_basis: str | None = Field(default=None, pattern="^(documented_area|branch_office|provincial_listing|national_license)$")
    admin_notes: str = Field(default="", max_length=2000)


def _clean(body: ContactBody) -> dict:
    try:
        return contacts.validate(body.model_dump())
    except contacts.ContactError as exc:
        raise HTTPException(422, {"code": "contact", "message": str(exc)}) from exc


@staff.post("/contacts", status_code=201)
def create_contact(body: ContactBody, st: State, user: SystemUser) -> dict:
    d = _clean(body)
    who = audit.actor_of(user)
    with st.db.session() as s:
        if (dup := contacts.duplicate(s, d)) is not None:
            raise HTTPException(409, {"code": "duplicate", "message": f"“{dup.organization}” already covers this area. "
                                      "Edit that entry instead."})
        c = Contact(**d, updated_by=who)
        s.add(c)
        s.flush()
        audit.record(s, who, "contact.created", None, None, contact=c.organization)
        return contacts.view(c, staff=True)


@staff.put("/contacts/{contact_id}")
def update_contact(contact_id: int, body: ContactBody, st: State, user: SystemUser) -> dict:
    d = _clean(body)
    who = audit.actor_of(user)
    with st.db.session() as s:
        c = s.get(Contact, contact_id)
        if c is None:
            raise HTTPException(404, {"code": "not_found", "message": "Contact not found."})
        if (dup := contacts.duplicate(s, d, exclude_id=c.id)) is not None:
            raise HTTPException(409, {"code": "duplicate", "message": f"“{dup.organization}” already covers this area."})
        changed = {k: [getattr(c, k), v] for k, v in d.items() if getattr(c, k) != v}
        for k, v in d.items():
            setattr(c, k, v)
        c.updated_by = who
        # S16: the trail says what changed; a verification change is recorded with its before and after
        audit.record(s, who, "contact.updated", None, None, contact=c.organization, fields=sorted(changed),
                     **({"verification": changed["verification"]} if "verification" in changed else {}))
        s.flush()
        return contacts.view(c, staff=True)


class AttachmentBody(BaseModel):
    contact_id: int
    wire_class: str | None = Field(default=None, max_length=32)
    record_type: str = Field(pattern="^(asset_record|attachment_agreement|utility_confirmation|permit)$")
    record_reference: str = Field(min_length=3, max_length=200)


@staff.post("/poles/{pole_code}/attachments", status_code=201)
def confirm_attachment(pole_code: str, body: AttachmentBody, st: State, user: User) -> dict:
    """S18: record a CONFIRMED responsible provider for a pole attachment, from an authoritative record. Area coverage
    never does this; the record type and reference are required and audited."""
    from app.models.orm import Pole, PoleAttachment
    with st.db.session() as s:
        p = s.scalar(select(Pole).where(Pole.pole_code == pole_code))
        c = s.get(Contact, body.contact_id)
        if p is None or c is None:
            raise HTTPException(404, {"code": "not_found", "message": "Pole or provider not found."})
        pa = PoleAttachment(pole_id=p.id, contact_id=c.id, wire_class=body.wire_class, record_type=body.record_type,
                            record_reference=body.record_reference.strip(), verified_by=audit.actor_of(user))
        s.add(pa)
        audit.record(s, audit.actor_of(user), "attachment.confirmed", pole_code, None, contact=c.organization,
                     record_type=body.record_type, record_reference=pa.record_reference)
        s.flush()
        return {"id": pa.id, "pole": pole_code, "contact": c.organization, "record_type": pa.record_type}


@staff.delete("/contacts/{contact_id}", status_code=204)
def deactivate_contact(contact_id: int, st: State, user: SystemUser) -> None:
    """Contacts are deactivated, never deleted, so a report that cited one stays explainable."""
    with st.db.session() as s:
        c = s.get(Contact, contact_id)
        if c is None:
            raise HTTPException(404, {"code": "not_found", "message": "Contact not found."})
        c.active = False
        audit.record(s, audit.actor_of(user), "contact.deactivated", None, None, contact=c.organization)
