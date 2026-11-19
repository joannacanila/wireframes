"""ORM schema.

    poles 1 ── 0..1 pole_locations     (GPS kept apart from images: Section 3.14.4)
    poles 1 ── N analyses 1 ── N detections
                          1 ── 0..1 reports

Every child foreign key is ``ON DELETE CASCADE`` at the database level *and* mirrored by
``cascade="all, delete-orphan", passive_deletes=True`` on the relationship, so deletes
work identically from SQL, the ORM, or a manual sqlite3 session. Domains (status,
class, level, band) are enforced by CHECK constraints rather than trusted to callers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.core.config import WIRE_CLASSES  # Vegetation is stored on the analysis, not here
from app.core.database import Base

ANALYSIS_STATUSES = ("processing", "complete", "no_wires_detected", "fit_failed", "error")
DETECTION_STATUSES = ("scored", "too_short", "fit_failed", "service_drop", "not_assessed")  # v2.21: measured, no severity
LEVELS = ("low", "medium", "high")
VNI_BANDS = ("Low", "Moderate", "High", "Severe")


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes. SQLite stores no offset, so values are normalised to
    UTC on write and re-tagged as UTC on read (otherwise clients parse them as local time)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; use timezone-aware UTC values")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=UTC)


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return uuid.uuid4().hex


def _in(col: str, values: tuple[str, ...], nullable: bool = False) -> str:
    vals = ", ".join(f"'{v}'" for v in values)
    expr = f"{col} IN ({vals})"
    return f"({col} IS NULL OR {expr})" if nullable else expr


class Pole(Base):
    __tablename__ = "poles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pole_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    barangay: Mapped[str | None] = mapped_column(String(120))
    # v2.21: the readable label (SAL-001), stored once so other poles' changes never renumber it
    label: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)

    location: Mapped[PoleLocation | None] = relationship(
        back_populates="pole", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )
    analyses: Mapped[list[Analysis]] = relationship(
        back_populates="pole", cascade="all, delete-orphan", passive_deletes=True,
        order_by="Analysis.created_at",
    )

    __table_args__ = (CheckConstraint("length(trim(pole_code)) > 0", name="ck_pole_code_nonempty"),)


class PoleLocation(Base):
    """Coordinates linked by pole, never embedded in stored images (RA 10173)."""

    __tablename__ = "pole_locations"

    pole_id: Mapped[int] = mapped_column(ForeignKey("poles.id", ondelete="CASCADE"), primary_key=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # exif | manual
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now)

    pole: Mapped[Pole] = relationship(back_populates="location")

    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_lat"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_lon"),
        CheckConstraint(_in("source", ("exif", "manual")), name="ck_loc_source"),
    )


RECORD_KINDS = ("operational", "test")


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    pole_id: Mapped[int] = mapped_column(ForeignKey("poles.id", ondelete="CASCADE"), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # v2.15: set by the person who uploads; the original file name above is kept as file metadata
    inspection_name: Mapped[str | None] = mapped_column(String(120))
    city: Mapped[str | None] = mapped_column(String(120))
    stored_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    label_name: Mapped[str | None] = mapped_column(String(64))  # reference annotation file, if any
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="processing")
    error_code: Mapped[str | None] = mapped_column(String(48))
    error_message: Mapped[str | None] = mapped_column(Text)
    segmentation_source: Mapped[str | None] = mapped_column(String(16))
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)

    vni_raw: Mapped[float | None] = mapped_column(Float)
    vni: Mapped[float | None] = mapped_column(Float)
    vni_band: Mapped[str | None] = mapped_column(String(16))

    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    envelope_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    flags_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    timings_json: Mapped[dict[str, float]] = mapped_column(JSON, default=dict, nullable=False)
    # Vegetation regions: {confidence, bbox, polygons, touches: [wire_index]} (never scored)
    vegetation_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    config_fingerprint: Mapped[str] = mapped_column(String(16), nullable=False)
    submitted_via: Mapped[str] = mapped_column(String(8), nullable=False, default="admin")
    # v2.19: "operational" (a real inspection) or "test" (QA run, synthetic sample, stock photo...). NULL only
    # on rows written before v2.19 until the startup backfill classifies them (app/services/records.py).
    # Test records are kept, never deleted; operational views leave them out.
    record_kind: Mapped[str | None] = mapped_column(String(12))
    record_kind_reason: Mapped[str | None] = mapped_column(String(160))
    # S18: "Delete inspection" archives (soft delete). The photo, findings, reviews and audit stay; operational views,
    # counts, the map and exports leave the record out. Reversible (restore), audited both ways.
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    archived_by: Mapped[str | None] = mapped_column(String(120))
    archived_reason: Mapped[str | None] = mapped_column(String(300))

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    pole: Mapped[Pole] = relationship(back_populates="analyses")
    detections: Mapped[list[Detection]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan", passive_deletes=True,
        order_by="Detection.wire_index",
    )
    report: Mapped[Report | None] = relationship(
        back_populates="analysis", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )

    __table_args__ = (
        CheckConstraint(_in("status", ANALYSIS_STATUSES), name="ck_analysis_status"),
        CheckConstraint(_in("vni_band", VNI_BANDS, nullable=True), name="ck_analysis_band"),
        CheckConstraint("vni IS NULL OR (vni >= 0 AND vni <= 1)", name="ck_analysis_vni"),
        CheckConstraint(_in("segmentation_source", ("model", "annotation"), nullable=True), name="ck_seg_src"),
        CheckConstraint("width > 0 AND height > 0", name="ck_analysis_dims"),
        CheckConstraint(_in("submitted_via", ("field", "admin")), name="ck_analysis_channel"),
        CheckConstraint(_in("record_kind", RECORD_KINDS, nullable=True), name="ck_analysis_record_kind"),
        Index("ix_analyses_pole_created", "pole_id", "created_at"),
        Index("ix_analyses_status", "status"),
    )

    @property
    def valid_crs(self) -> list[float]:
        """CRS values usable for ranking. S4: values stored before the withdrawal of the provisional criteria
        (no current ``assessment_basis``) are not used; the stored rows are unchanged (app/services/basis.py)."""
        from app.services.basis import is_current
        if not is_current(self.metadata_json):
            return []
        return [d.crs for d in self.detections if d.crs is not None]


class Detection(Base):
    __tablename__ = "detections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False)
    wire_index: Mapped[int] = mapped_column(Integer, nullable=False)
    wire_class: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    fail_reason: Mapped[str | None] = mapped_column(String(32))
    bbox_json: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    polygons_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    centerline_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    curve_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    fit_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    chord_px: Mapped[float | None] = mapped_column(Float)
    sag_px: Mapped[float | None] = mapped_column(Float)
    sag_ratio: Mapped[float | None] = mapped_column(Float)
    delta_lim: Mapped[float | None] = mapped_column(Float)
    delta_lim0: Mapped[float | None] = mapped_column(Float)
    crs: Mapped[float | None] = mapped_column(Float)
    level: Mapped[str | None] = mapped_column(String(8))
    flags_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    analysis: Mapped[Analysis] = relationship(back_populates="detections")

    __table_args__ = (
        UniqueConstraint("analysis_id", "wire_index", name="uq_detection_index"),
        CheckConstraint(_in("wire_class", WIRE_CLASSES), name="ck_det_class"),
        CheckConstraint(_in("status", DETECTION_STATUSES), name="ck_det_status"),
        CheckConstraint(_in("level", LEVELS, nullable=True), name="ck_det_level"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_det_conf"),
        CheckConstraint("crs IS NULL OR (crs >= 0 AND crs <= 10)", name="ck_det_crs"),
        # A score exists exactly when the wire was scored, and then it has a level.
        CheckConstraint(
            "(status = 'scored' AND crs IS NOT NULL AND level IS NOT NULL) OR "
            "(status <> 'scored' AND crs IS NULL AND level IS NULL)",
            name="ck_det_scored_consistency",
        ),
        Index("ix_detections_analysis", "analysis_id"),
    )


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[str] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    file_name: Mapped[str] = mapped_column(String(64), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    config_fingerprint: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)

    analysis: Mapped[Analysis] = relationship(back_populates="report")


# ----------------------------------------------------------------------------- admin portal

# v2.19: "flagged" = the reviewer asks for a further (field) inspection; the model's finding is left as computed.
REVIEW_DECISIONS = ("confirmed", "flagged", "overridden", "dismissed")
# v2.19: "admin" may open the system area (models, queue, evaluation, settings, test records); "inspector" uses the
# operational portal only (dashboard, inspections, map, reviews, reports). Accounts before v2.19 are admins.
ROLES = ("admin", "inspector")


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(default=True, nullable=False)
    role: Mapped[str] = mapped_column(String(12), default="admin", nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (CheckConstraint("length(username) >= 3", name="ck_admin_username"),
                      CheckConstraint(_in("role", ROLES), name="ck_admin_role"))


class AdminSession(Base):
    """Only the SHA-256 of the cookie token is stored."""

    __tablename__ = "admin_sessions"

    token_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (Index("ix_admin_sessions_user", "user_id"),)


class Review(Base):
    """A professional's decision on an analysis (Section 3.7: a human reviewer remains
    responsible). The computed CRS and levels are never modified; an override is recorded
    beside them and shown in the report."""

    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False)
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    reviewer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    override_level: Mapped[str | None] = mapped_column(String(8))
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)

    analysis: Mapped[Analysis] = relationship(back_populates="reviews")

    __table_args__ = (
        CheckConstraint(_in("decision", REVIEW_DECISIONS), name="ck_review_decision"),
        CheckConstraint(_in("override_level", LEVELS, nullable=True), name="ck_review_level"),
        CheckConstraint("(decision = 'overridden') = (override_level IS NOT NULL)", name="ck_review_override"),
        Index("ix_reviews_analysis", "analysis_id", "created_at"),
    )


Analysis.reviews = relationship(Review, back_populates="analysis", cascade="all, delete-orphan",
                                passive_deletes=True, order_by=Review.created_at)


class AppSetting(Base):
    """Runtime switches an administrator can change without editing files."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(64))


class AuditEvent(Base):
    """v2.20: who did what in the admin portal, and when (reviews, location changes, record re-classification,
    bulk operations, deletions, model and settings changes, failed processing). Append-only; never edited."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)  # display name, or "System"
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    target: Mapped[str | None] = mapped_column(String(64))           # pole code or analysis id
    analysis_id: Mapped[str | None] = mapped_column(String(32))      # no FK: the event outlives a deleted record
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    __table_args__ = (Index("ix_audit_at", "at"),)


# --------------------------------------------------------------------------- S14
CONTACT_TYPES = ("emergency", "electric_utility", "telecom_provider", "barangay", "city_government", "other")
VERIFICATION = ("verified", "secondary_source", "unverified")


class Contact(Base):
    """S14: a reporting contact in the provider directory, maintained by administrators (no redeployment).
    ``verification`` says how the contact was checked; only ``verified`` and ``secondary_source`` contacts are ever
    suggested to the public, and a secondary-source contact is labelled so. A suggestion is never a statement of who
    owns a wire: a model's wire type cannot establish the operator."""

    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization: Mapped[str] = mapped_column(String(160), nullable=False)
    org_type: Mapped[str] = mapped_column(String(24), nullable=False)
    service_area: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # matching: "national", or city / municipality names as in the PSGC list (e.g. "City of Dasmariñas")
    cities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    barangays: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(80))
    url: Mapped[str | None] = mapped_column(String(300))
    assistance: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_url: Mapped[str | None] = mapped_column(String(300))
    source_note: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    verification: Mapped[str] = mapped_column(String(20), nullable=False, default="unverified")
    last_verified: Mapped[str | None] = mapped_column(String(10))  # YYYY-MM-DD
    active: Mapped[bool] = mapped_column(default=True, nullable=False)
    seed_key: Mapped[str | None] = mapped_column(String(40), unique=True)  # built-in rows; never re-seeded once edited
    # S18: the provider identity and its evidence (app/services/contacts.py). ``category`` NULL on rows written before
    # S18 is read from ``org_type``.
    category: Mapped[str | None] = mapped_column(String(16))
    registered_name: Mapped[str | None] = mapped_column(String(200))
    previous_names: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    email: Mapped[str | None] = mapped_column(String(160))
    reporting_url: Mapped[str | None] = mapped_column(String(300))
    source_type: Mapped[str | None] = mapped_column(String(24))
    coverage_basis: Mapped[str | None] = mapped_column(String(24))  # documented_area | branch_office | national_license
    admin_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(120))

    __table_args__ = (CheckConstraint(_in("org_type", CONTACT_TYPES), name="ck_contact_type"),
                      CheckConstraint(_in("verification", VERIFICATION), name="ck_contact_verification"),
                      CheckConstraint("phone IS NOT NULL OR url IS NOT NULL OR verification = 'unverified'",
                                      name="ck_contact_channel"),
                      Index("ix_contacts_org", "organization"))


class Assignment(Base):
    """S14: who is following up an inspection. One current assignment per inspection; history is in the audit trail."""

    __tablename__ = "assignments"

    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), primary_key=True)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    assignee_name: Mapped[str] = mapped_column(String(120), nullable=False)
    assigned_by: Mapped[str] = mapped_column(String(120), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)


Analysis.assignment = relationship(Assignment, uselist=False, cascade="all, delete-orphan", passive_deletes=True)


# --------------------------------------------------------------------------- S18: administrative review workflow
# Machine findings (analyses / detections), the review PROGRESS and the administrator's DECISION are three different
# things and live in three different places. No machine state (INCONCLUSIVE, incomplete, failed) is ever stored here.
REVIEW_STATUSES = ("NOT_YET_REVIEWED", "PARTIALLY_REVIEWED", "REVIEW_COMPLETE")


class ReviewProgress(Base):
    """S18: where an inspection is in administrative review. No row means NOT_YET_REVIEWED. ``source`` is
    ``legacy_review`` for an inspection a person reviewed before S18 (backfilled from ``reviews``; nothing there is
    changed)."""

    __tablename__ = "review_progress"

    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    started_by: Mapped[str | None] = mapped_column(String(120))
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_by: Mapped[str | None] = mapped_column(String(120))
    reviewers: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)  # everyone who opened it
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="workflow")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, onupdate=_now, nullable=False)

    __table_args__ = (CheckConstraint(_in("status", REVIEW_STATUSES), name="ck_review_progress_status"),
                      CheckConstraint(_in("source", ("workflow", "legacy_review")), name="ck_review_progress_source"),
                      CheckConstraint("status <> 'REVIEW_COMPLETE' OR completed_at IS NOT NULL",
                                      name="ck_review_progress_complete"),
                      Index("ix_review_progress_status", "status"))


class AdminDecision(Base):
    """S18: the administrator's inspection decision. Append-only: an amendment adds a row and stamps the previous
    one ``superseded_at``; nothing is overwritten. The machine findings at decision time are kept in ``machine_json``
    so the record shows what the reviewer saw."""

    __tablename__ = "admin_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False)
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    decision_label: Mapped[str] = mapped_column(String(120), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    follow_up: Mapped[str] = mapped_column(Text, nullable=False, default="")
    observations: Mapped[str] = mapped_column(Text, nullable=False, default="")
    critical_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    machine_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    reviewer_name: Mapped[str] = mapped_column(String(120), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="workflow")
    legacy_review_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (CheckConstraint("length(trim(decision)) > 0", name="ck_decision_code"),
                      CheckConstraint(_in("source", ("workflow", "legacy_review")), name="ck_decision_source"),
                      UniqueConstraint("legacy_review_id", name="uq_decision_legacy_review"),
                      Index("ix_admin_decisions_analysis", "analysis_id", "created_at"))


Analysis.review_progress = relationship(ReviewProgress, uselist=False, cascade="all, delete-orphan",
                                        passive_deletes=True)
Analysis.decisions = relationship(AdminDecision, cascade="all, delete-orphan", passive_deletes=True,
                                  order_by=AdminDecision.created_at)


# --------------------------------------------------------------------------- S18: provider directory, ownership
PROVIDER_CATEGORIES = ("electricity", "telecom", "broadband", "catv", "other_infra", "emergency", "government")


class PoleAttachment(Base):
    """S18: a CONFIRMED relationship between a pole and the provider responsible for one of its attachments, entered by
    an administrator from an authoritative record (asset record, attachment agreement, utility confirmation). Area
    coverage never creates one: GPS shows who operates in an area, not who owns a wire."""

    __tablename__ = "pole_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pole_id: Mapped[int] = mapped_column(ForeignKey("poles.id", ondelete="CASCADE"), nullable=False)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    wire_class: Mapped[str | None] = mapped_column(String(32))
    record_type: Mapped[str] = mapped_column(String(24), nullable=False)
    record_reference: Mapped[str] = mapped_column(String(200), nullable=False)
    verified_by: Mapped[str] = mapped_column(String(120), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_now, nullable=False)

    __table_args__ = (CheckConstraint(_in("record_type", ("asset_record", "attachment_agreement",
                                                          "utility_confirmation", "permit")), name="ck_attach_record"),
                      CheckConstraint("length(trim(record_reference)) > 0", name="ck_attach_reference"),
                      Index("ix_pole_attachments_pole", "pole_id"))
