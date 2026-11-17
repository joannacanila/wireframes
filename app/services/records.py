"""v2.19: operational vs test records, and the plain inspection status the admin portal shows.

Test records (QA runs, synthetic samples, UI checks, timing runs, stock photos) stay in the database: they are
never deleted, and an administrator can list them in the system area and re-classify any record by hand. Every
operational view (dashboard, inspection list, map, dispatch list, operational CSV/PDF exports, assistant) applies
the same rule through :func:`is_operational`, so counts, filters and exports reconcile.

Risk per inspection follows the pole-level screening verdict (``metadata_json["hazard"]["verdict"]``), the
same assessment the field screener and the PDF report show. An inconclusive screening is "unassessed", never
"low"; a failed one is "failed".
"""

from __future__ import annotations

import re
from typing import Any

from app.models.orm import Analysis

# --------------------------------------------------------------------------- test-record rules
# Each rule: (field, compiled pattern, reason). Fields are read from the analysis and its pole. The rules name
# the fixtures this project actually produced (scripts/e2e_check.py, the QA runs, timing runs, the synthetic
# sample) and stock-photo downloads; a real inspection never matches them by accident.
_RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("pole_code", re.compile(r"^(QA|TEST|DEMO)[-_]|^SYN-SAMPLE$", re.I), "Quality-assurance or synthetic pole code"),
    ("pole_code", re.compile(r"(^|[-_])TEST$", re.I), "Pole code marks a test"),
    ("barangay", re.compile(r"^(test barangay|synthetic sample)$", re.I), "Test or synthetic barangay"),
    ("city", re.compile(r"^test city$", re.I), "Test city"),
    ("inspection_name", re.compile(r"^(ui check|timing run|validation photo)\b", re.I), "UI, timing or validation run"),
    ("inspection_name", re.compile(r"\(unlabelled\)$", re.I), "Validation photo"),
    ("pole_code", re.compile(r"^P-V\d{3}$", re.I), "Release check (version-numbered pole code)"),
    ("original_filename", re.compile(r"^synthetic-pole\.", re.I), "Synthetic sample image"),
    ("original_filename", re.compile(r"^test[ _-]?\d*\.[a-z]+$", re.I), "File name marks a test"),
    ("original_filename", re.compile(r"^[1-4]_(clean_not_at_risk|at_risk_high|low_light|glare_degraded)\.", re.I),
     "QA suite image"),
    ("original_filename", re.compile(r"^(tiny_64x64|huge_4000x3000_rgba|grayscale|blank)\.(png|jpe?g)$", re.I),
     "QA suite image"),
    ("original_filename", re.compile(r"(dreamstime|shutterstock|istock|depositphotos|alamy|adobestock)", re.I),
     "Stock photograph, not a field inspection"),
    # Dreamstime-style download names: a long hyphenated caption ending in the stock id
    ("original_filename", re.compile(r"^[a-z]+(-[a-z]+){5,}-\d{7,10}\.(jpe?g|png|webp)$", re.I),
     "Stock photograph, not a field inspection"),
]


def classify(pole_code: str | None, barangay: str | None = None, city: str | None = None,
             inspection_name: str | None = None, original_filename: str | None = None) -> tuple[str, str | None]:
    """("test", reason) when a known fixture rule matches, else ("operational", None)."""
    fields = {"pole_code": pole_code, "barangay": barangay, "city": city, "inspection_name": inspection_name,
              "original_filename": original_filename}
    for field, rx, reason in _RULES:
        v = fields.get(field)
        if v and rx.search(v.strip()):
            return "test", reason
    return "operational", None


def classify_analysis(a: Analysis) -> tuple[str, str | None]:
    return classify(a.pole.pole_code if a.pole else None, a.pole.barangay if a.pole else None, a.city,
                    a.inspection_name, a.original_filename)


def is_operational(a: Analysis) -> bool:
    """NULL (not yet classified) is judged by the rules, so a missed backfill can never leak a fixture."""
    kind = a.record_kind or classify_analysis(a)[0]
    return kind == "operational"


def backfill(conn) -> int:
    """Classify rows written before v2.19 (record_kind IS NULL). Rows already classified, including any an
    administrator re-classified by hand, are left alone. Returns the number of rows classified."""
    from sqlalchemy import text
    rows = conn.execute(text(
        "SELECT a.id, p.pole_code, p.barangay, a.city, a.inspection_name, a.original_filename "
        "FROM analyses a JOIN poles p ON p.id = a.pole_id WHERE a.record_kind IS NULL")).all()
    for aid, code, brgy, city, name, fname in rows:
        kind, reason = classify(code, brgy, city, name, fname)
        conn.execute(text("UPDATE analyses SET record_kind = :k, record_kind_reason = :r WHERE id = :i"),
                     {"k": kind, "r": reason, "i": aid})
    return len(rows)


# --------------------------------------------------------------------------- plain status
FINISHED = ("complete", "fit_failed", "no_wires_detected")


def list_columns():
    """v2.23: the detection columns list views read (status, level, score inputs). Loading only these avoids decoding
    every wire's polygons, centreline and fitted curve (about 22,000 JSON columns for 33 inspections, 1.2 s per
    request). Use as ``selectinload(Analysis.detections).load_only(*records.list_columns())``."""
    from app.models.orm import Detection
    return (Detection.id, Detection.analysis_id, Detection.wire_index, Detection.wire_class, Detection.status,
            Detection.level, Detection.crs, Detection.sag_ratio, Detection.fail_reason, Detection.flags_json)
VERDICT_RISK = {"AT RISK - HIGH": "high", "AT RISK - MODERATE": "medium", "NOT AT RISK": "low",
                "INCONCLUSIVE - RETAKE PHOTO": "unassessed"}
RISK_LABEL = {"high": "High risk", "medium": "Moderate risk", "low": "Low risk", "unassessed": "Assessment incomplete",
              "failed": "Processing failed", "processing": "Processing"}
REVIEW_LABEL = {"pending": "Needs review", "not_reviewed": "Not reviewed", "confirmed": "Confirmed",
                "flagged": "Flagged for inspection", "overridden": "Level changed", "dismissed": "Dismissed"}
COMPLETENESS_LABEL = {"complete": "Complete", "partial": "Partial", "not_assessed": "Not assessed"}


def risk(a: Analysis) -> str:
    """high | medium | low | unassessed | failed | processing (the screening verdict, never a guess)."""
    if a.status == "processing":
        return "processing"
    if a.status == "error":
        return "failed"
    from app.services.basis import verdict_of
    verdict = verdict_of(a.metadata_json)  # S4: a pre-S4 verdict from withdrawn criteria reads INCONCLUSIVE
    if verdict in VERDICT_RISK:
        return VERDICT_RISK[verdict]
    # records from before the pole-level verdict (v2.8): the worst measured wire
    scored = [d for d in a.detections if d.crs is not None and d.level] if a.valid_crs else []
    if not scored:
        return "unassessed"
    return max(scored, key=lambda d: d.crs).level


def completeness(a: Analysis) -> str:
    """complete: every detected wire section was assessed; partial: some could not be; not_assessed: none."""
    if a.status not in FINISHED or risk(a) == "unassessed":
        return "not_assessed"
    ds = [d for d in a.detections if d.status != "service_drop"]
    scored = [d for d in ds if d.status == "scored"]
    if not scored:
        return "not_assessed"
    return "complete" if len(scored) == len(ds) else "partial"


def review_status(a: Analysis) -> str:
    if a.reviews:
        return a.reviews[-1].decision
    return "pending" if risk(a) in ("high", "medium") else "not_reviewed"


def wire_counts(a: Analysis) -> dict[str, int]:
    out = {"high": 0, "medium": 0, "low": 0, "unassessed": 0}
    for d in a.detections:
        if d.status == "service_drop":
            continue
        out[d.level if d.status == "scored" and d.level else "unassessed"] += 1
    return out


def row(a: Analysis, labels: dict[str, str] | None = None) -> dict[str, Any]:
    """One inspection as the operational portal lists it (no scores, no fit parameters). ``labels``: the readable
    pole labels (app/services/labels.py); the pole code stays the identifier."""
    rev = a.reviews[-1] if a.reviews else None
    loc = a.pole.location
    return {
        "id": a.id, "pole_code": a.pole.pole_code, "label": (labels or {}).get(a.pole.pole_code),
        "inspection_name": a.inspection_name, "wires_assessed": sum(1 for d in a.detections if d.status == "scored"),
        "wires_total": sum(1 for d in a.detections if d.status != "service_drop"),
        "barangay": a.pole.barangay, "city": a.city, "created_at": a.created_at.isoformat(),
        "status": a.status, "risk": risk(a), "completeness": completeness(a), "review": review_status(a),
        "reviewed_by": rev.reviewer_name if rev else None, "reviewed_at": rev.created_at.isoformat() if rev else None,
        "override_level": rev.override_level if rev else None,
        "wires": wire_counts(a), "has_location": loc is not None,
        "location": None if loc is None else {"lat": loc.latitude, "lon": loc.longitude, "source": loc.source},
        "thumbnail_url": f"/api/analysis/{a.id}/image", "submitted_via": a.submitted_via,
        "record_kind": a.record_kind or classify_analysis(a)[0],
    }
