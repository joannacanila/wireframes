"""Analysis lifecycle: ingest an upload, run the pipeline, persist the outcome."""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import AnalysisConfig, Settings
from app.core.database import Database
from app.models.orm import Analysis, Detection, Pole, PoleLocation
from app.pipeline.acquisition import acquire, load_rgb
from app.pipeline.runner import AnalysisOutcome, Segmenter, run_analysis
from app.pipeline.sag_limits import EngineeringContext
from app.pipeline.segment import SegmenterUnavailable, parse_yolo_seg_labels

log = logging.getLogger(__name__)
_POLE_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")


class UploadError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422, hint: str | None = None) -> None:
        super().__init__(message)
        self.code, self.status, self.hint = code, status, hint


def normalise_pole_code(code: str | None) -> str:
    if not code or not code.strip():
        return f"P-{uuid.uuid4().hex[:6].upper()}"
    code = code.strip()
    if not _POLE_CODE.match(code):
        raise UploadError("invalid_pole_code", "Pole code may contain letters, digits, '.', '_' and '-' (max 64).")
    return code


def ingest(
    db: Database, settings: Settings, cfg: AnalysisConfig, *, data: bytes, filename: str,
    pole_code: str | None, barangay: str | None, context: dict[str, float | None],
    distance_m: float | None, elevation_deg: float | None,
    latitude: float | None, longitude: float | None, labels: bytes | None,
    model_ready: bool = True, trusted: bool = True, inspection_name: str | None = None, city: str | None = None,
) -> str:
    """Validate, strip GPS, store, and create the analysis row (status ``processing``).

    Order matters: the content is validated first (so a bad file is reported as such), then
    the segmentation source is checked, and only then is anything written to disk.
    """
    acq = acquire(data, cfg.acquisition, distance_m=distance_m, elevation_deg=elevation_deg)
    h, w = acq.image_rgb.shape[:2]

    label_name = None
    if labels is not None:
        try:
            text = labels.decode("utf-8")
            parse_yolo_seg_labels(text, w, h)  # validate now, so a bad file fails the upload
        except (UnicodeDecodeError, ValueError) as exc:
            raise UploadError("invalid_labels", f"Annotation file rejected: {exc}") from exc

    code = normalise_pole_code(pole_code)
    _validate_location(latitude, longitude)
    if labels is None and not model_ready:
        raise UploadError(
            "model_unavailable", "No segmentation model is loaded, so this photo cannot be segmented.", 503,
            hint="Attach YOLO-seg reference polygons to run in validation mode, or install "
                 "models/wireframe-yolo11m-seg.pt (python -m ml.train) for field mode.")
    stored = f"{uuid.uuid4().hex}{acq.sanitized_ext}"
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    written = [settings.upload_dir / stored]
    written[0].write_bytes(acq.sanitized_bytes)
    if labels is not None:
        label_name = f"{Path(stored).stem}.txt"
        written.append(settings.upload_dir / label_name)
        written[1].write_bytes(labels)
    try:
        return _insert(db, cfg, acq, code, barangay, filename, stored, label_name, context,
                       latitude, longitude, w, h, trusted, _clean(inspection_name), _clean(city))
    except Exception:
        for p in written:  # no orphan files when the transaction fails
            p.unlink(missing_ok=True)
        raise


def _validate_location(lat: float | None, lon: float | None) -> None:
    if (lat is None) != (lon is None):
        raise UploadError("invalid_location", "Provide both latitude and longitude, or neither.")
    if lat is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise UploadError("invalid_location", "Latitude/longitude out of range.")


def _clean(v: str | None) -> str | None:
    """Trimmed free text with control characters removed; empty becomes None."""
    if v is None:
        return None
    v = re.sub(r"[\x00-\x1f\x7f]", " ", v).strip()
    return v[:120] or None


def _insert(db, cfg, acq, code, barangay, filename, stored, label_name, context, latitude, longitude, w, h,
            trusted: bool = True, inspection_name: str | None = None, city: str | None = None) -> str:
    """``trusted`` (an admin upload) may change an existing pole's barangay and location; an
    anonymous field upload may only fill them in where nothing is recorded yet."""
    from app.services.barangay import resolve as resolve_barangay
    entered = _clean(barangay) or None
    # v2.21: only an official barangay name is stored; other text is kept as typed in the metadata, unassigned
    official = resolve_barangay(entered, city)
    with db.session() as s:
        pole = s.scalar(select(Pole).where(Pole.pole_code == code))
        if pole is None:
            pole = Pole(pole_code=code, barangay=official)
            s.add(pole)
            s.flush()
        elif official and (trusted or not pole.barangay):
            pole.barangay = official
        if trusted or s.get(PoleLocation, pole.id) is None:
            _update_location(s, pole, acq.gps, latitude, longitude)
        a = Analysis(
            pole_id=pole.id, original_filename=Path(filename or "upload").name[:255],
            inspection_name=inspection_name, city=city,
            stored_name=stored, label_name=label_name, status="processing", width=w, height=h,
            metadata_json=_jsonable(acq.metadata), envelope_json=_jsonable([c.as_dict() for c in acq.envelope]),
            context_json={k: v for k, v in context.items() if v is not None},
            flags_json=list(acq.flags), config_fingerprint=cfg.fingerprint,
            submitted_via="admin" if trusted else "field",
        )
        from app.services.records import classify
        if entered and entered != official:
            a.metadata_json = {**(a.metadata_json or {}), "barangay_entered": entered[:120]}
        a.record_kind, a.record_kind_reason = classify(code, entered or pole.barangay, city, inspection_name,
                                                       a.original_filename)
        s.add(a)
        s.flush()
        return a.id


def _update_location(s: Session, pole: Pole, gps: tuple[float, float] | None,
                     lat: float | None, lon: float | None) -> None:
    if lat is not None:
        coords, source = (lat, lon), "manual"
    elif gps is not None:
        coords, source = gps, "exif"
    else:
        return
    from app.services.geo import problem
    if problem(*coords):  # v2.21: an unusable fix (0,0, outside the Philippines) is not stored as the location
        return
    loc = s.get(PoleLocation, pole.id)
    if loc is None:
        s.add(PoleLocation(pole_id=pole.id, latitude=coords[0], longitude=coords[1], source=source))
    elif source == "manual" or loc.source == "exif":
        loc.latitude, loc.longitude, loc.source = coords[0], coords[1], source


def process(analysis_id: str, db: Database, settings: Settings, cfg: AnalysisConfig, segmenter: Segmenter) -> str:
    """Run the pipeline for one queued analysis and persist the result. Returns the status."""
    with db.session() as s:
        a = s.get(Analysis, analysis_id)
        if a is None:
            return "missing"
        if a.status != "processing":
            return a.status
        stored, label_name, ctx_raw = a.stored_name, a.label_name, dict(a.context_json)
        envelope = list(a.envelope_json or [])

    try:
        rgb = load_rgb((settings.upload_dir / stored).read_bytes())
        ctx = EngineeringContext(
            span_m=ctx_raw.get("span_m"), temperature_c=ctx_raw.get("temperature_c"),
            wind_speed_mps=ctx_raw.get("wind_speed_mps"),
        )
        dets = None
        if label_name:
            text = (settings.upload_dir / label_name).read_text("utf-8")
            dets = parse_yolo_seg_labels(text, rgb.shape[1], rgb.shape[0])
        outcome = run_analysis(rgb, cfg, segmenter, ctx, detections=dets, envelope=envelope)
    except SegmenterUnavailable as exc:
        _fail(db, analysis_id, "model_unavailable", str(exc))
        return "error"
    except Exception as exc:  # recorded, never swallowed silently
        log.exception("analysis %s failed", analysis_id)
        _fail(db, analysis_id, "pipeline_exception", f"{type(exc).__name__}: {exc}")
        return "error"

    if outcome.wire_mask is not None:
        save_wire_mask(settings, analysis_id, outcome.wire_mask)
    persist(db, analysis_id, outcome)
    return outcome.status


def wire_mask_path(settings: Settings, analysis_id: str) -> Path:
    return settings.render_dir / f"{analysis_id}.wiremask.png"


def save_wire_mask(settings: Settings, analysis_id: str, label: np.ndarray) -> None:
    """WireNet's class map as an indexed PNG (index 0 transparent), colours as in the UI legend."""
    from app.core.config import CLASSES
    rgb = {"Vegetation": (47, 158, 68), "abandoned-wire": (232, 89, 12), "power-distribution-lines": (25, 113, 194),
           "telecom-cable": (156, 54, 181)}
    palette = [0, 0, 0] + [v for c in CLASSES for v in rgb.get(c, (128, 128, 128))]
    img = Image.fromarray(label.astype(np.uint8), "P")
    img.putpalette(palette + [0] * (768 - len(palette)))
    settings.render_dir.mkdir(parents=True, exist_ok=True)
    img.save(wire_mask_path(settings, analysis_id), "PNG", optimize=True, transparency=0)


def _fail(db: Database, analysis_id: str, code: str, message: str) -> None:
    with db.session() as s:
        a = s.get(Analysis, analysis_id)
        if a is not None:
            a.status, a.error_code, a.error_message = "error", code, message[:2000]
            from app.services import audit
            audit.record(s, "System", "inspection.failed", a.pole.pole_code if a.pole else None, a.id, code=code)
            a.completed_at = datetime.now(UTC)


def persist(db: Database, analysis_id: str, o: AnalysisOutcome) -> None:
    with db.session() as s:
        a = s.get(Analysis, analysis_id)
        if a is None:
            return
        a.detections.clear()
        s.flush()
        a.vni_raw, a.vni, a.vni_band = _jsonable(o.vni_raw), _jsonable(o.vni), o.vni_band
        a.segmentation_source = o.segmentation_source
        a.flags_json = sorted(set(a.flags_json) | set(o.flags))
        a.timings_json = _jsonable(o.timings_ms)
        a.vegetation_json = [_jsonable(asdict(r)) for r in o.vegetation]
        if o.hazard is not None:
            from app.services.basis import BASIS
            a.metadata_json = {**a.metadata_json, "hazard": _jsonable(o.hazard), "assessment_basis": BASIS}
        if o.quality is not None:
            a.metadata_json = {**a.metadata_json, "quality": _jsonable(o.quality)}
        if o.mask_stats is not None:
            a.metadata_json = {**a.metadata_json, "wire_mask": _jsonable(o.mask_stats)}
        if o.clearance is not None:
            a.metadata_json = {**a.metadata_json, "clearance": _jsonable({str(k): v for k, v in o.clearance.items()})}
        a.metadata_json = {**a.metadata_json, "suppressed_spans": _jsonable(o.suppressed),  # always: re-analysis
                           "scene": _jsonable(o.scene), "guardrail": _jsonable(o.guardrail)}
        if o.input_quality is not None or o.enhancement:
            a.metadata_json = {**a.metadata_json, "segmentation_input": _jsonable({
                "quality": o.input_quality, "enhancement": o.enhancement or []})}
        for w in o.wires:
            a.detections.append(Detection(
                wire_index=int(w.index), wire_class=w.wire_class, confidence=_jsonable(w.confidence),
                status=w.status, fail_reason=w.fail_reason, bbox_json=_jsonable(list(w.bbox)),
                polygons_json=_jsonable(w.polygons), centerline_json=_jsonable(w.centerline),
                curve_json=_jsonable(w.curve), fit_json=_jsonable(w.fit),
                chord_px=_jsonable(w.fit.get("chord_px")), sag_px=_jsonable(w.fit.get("sag_px")),
                # S4: every fitted span keeps its measured ratio (scored, or measured and not assessed)
                sag_ratio=_jsonable(w.fit.get("sag_ratio")) if w.status in ("scored", "not_assessed") else None,
                delta_lim=_jsonable(w.delta_lim), delta_lim0=_jsonable(w.delta_lim0), crs=_jsonable(w.crs),
                level=w.level, flags_json=_jsonable(w.flags),
            ))
        a.status = o.status
        a.completed_at = datetime.now(UTC)


def _jsonable(v: Any) -> Any:
    """Recursively make a value strict-JSON safe (v2.10): numpy scalars and arrays become Python
    numbers / lists, NaN and +-Inf become None, Fractions and Decimals become floats, dict keys
    become strings, bytes and unknown objects become strings. Used for every JSON column and every
    API payload built from pipeline output."""
    from decimal import Decimal
    from fractions import Fraction
    if v is None or isinstance(v, (str, bool)):
        return v
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_jsonable(x) for x in v]
    if isinstance(v, np.ndarray):
        return _jsonable(v.tolist())
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, (np.integer, int)):
        return int(v)
    if isinstance(v, (np.floating, float, Fraction, Decimal)):
        f = float(v)
        return f if np.isfinite(f) else None
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def load_analysis(s: Session, analysis_id: str) -> Analysis | None:
    return s.scalar(
        select(Analysis)
        .where(Analysis.id == analysis_id)
        .options(selectinload(Analysis.detections), selectinload(Analysis.reviews),
                 selectinload(Analysis.pole).selectinload(Pole.location))
    )


def serialize_result(a: Analysis, cfg: AnalysisConfig, ranking: dict[str, Any] | None = None,
                     settings: Settings | None = None) -> dict[str, Any]:
    """The API result, passed through ``_jsonable`` so it is always strict JSON (v2.10). With ``settings``,
    every wire also carries ``provenance``: observed vs inferred (gap-bridge) pixels (v2.14)."""
    from app.services.reasons import retake_summary
    r = _serialize_result(a, cfg, ranking)
    r["retake_guidance"] = retake_summary(r.get("wires") or [], a.width, a.height)  # v2.13
    if settings is not None:
        from app.services.provenance import attach
        attach(r, wire_mask_path(settings, a.id))
    # S5: what was observed and measured, per wire and per photo (app/services/diagnostics.py)
    from app.services.diagnostics import pole_diagnosis, wire_diagnosis
    for w in r.get("wires") or []:
        w["diagnosis"] = wire_diagnosis(w, cfg)
    r["diagnosis"] = pole_diagnosis(r, cfg)
    # S8: the configured sag reference, compared on read (never stored, never a level)
    from app.services.comparison import compare_wire, pole_comparison
    for w in r.get("wires") or []:
        w["comparison"] = compare_wire(w, cfg)
    r["comparison"] = pole_comparison(r, cfg)
    return _jsonable(r)


def _wire_view(d, cfg: AnalysisConfig, md: dict | None = None) -> dict[str, Any]:
    """The stored wire as shown. S4 (app/services/basis.py): a stored level from a withdrawn criterion is shown as not
    assessed (reason withdrawn_reference); the stored values stay under ``historical``."""
    from app.services.basis import level_withdrawn
    if level_withdrawn(d.level, d.wire_class, cfg, md):
        return {"status": "not_assessed", "fail_reason": "withdrawn_reference", "level": None, "crs": None,
                "delta_lim": None, "delta_lim0": None,
                "historical": {"status": d.status, "level": d.level, "crs": d.crs, "delta_lim": d.delta_lim}}
    return {"status": d.status, "fail_reason": d.fail_reason, "level": d.level, "crs": d.crs,
            "delta_lim": d.delta_lim, "delta_lim0": d.delta_lim0, "historical": None}


def _serialize_result(a: Analysis, cfg: AnalysisConfig, ranking: dict[str, Any] | None = None) -> dict[str, Any]:
    views = {id(d): _wire_view(d, cfg, a.metadata_json or {}) for d in a.detections}
    crs = [views[id(d)]["crs"] for d in a.detections if views[id(d)]["crs"] is not None]
    counts: dict[str, int] = {}
    for d in a.detections:
        counts[d.wire_class] = counts.get(d.wire_class, 0) + 1
    worst = max((d for d in a.detections if views[id(d)]["crs"] is not None), key=lambda d: d.crs, default=None)
    from app.services.basis import WITHDRAWN_NOTICE, hazard_view, is_current
    from app.services.reasons import wire_action, wire_notes, wire_reason, wire_reason_short
    md = a.metadata_json or {}
    clearance = md.get("clearance") or {}
    gate = cfg.crs.min_chord_px or 0.0
    total_ms = sum(v for v in (a.timings_json or {}).values() if isinstance(v, (int, float)))
    return {
        "id": a.id,
        "status": a.status,
        "error": {"code": a.error_code, "message": scrub_paths(a.error_message),
                  "summary": public_error(a.error_code, a.error_message)} if a.status == "error" else None,
        "created_at": a.created_at.isoformat(),
        "completed_at": a.completed_at.isoformat() if a.completed_at else None,
        "inspection": {"name": a.inspection_name, "city": a.city, "barangay": a.pole.barangay},
        "pole": {"code": a.pole.pole_code, "barangay": a.pole.barangay,
                 "has_location": a.pole.location is not None,
                 # the submitter's own pole only (capability URL); the multi-pole map is admin-only
                 "location": None if a.pole.location is None else {
                     "lat": a.pole.location.latitude, "lon": a.pole.location.longitude,
                     "source": a.pole.location.source}},
        "submitted_via": a.submitted_via,
        "reviews": [{"decision": r.decision, "override_level": r.override_level, "note": r.note,
                     "by": r.reviewer_name, "at": r.created_at.isoformat()} for r in a.reviews],
        "image": {"url": f"/api/analysis/{a.id}/image", "width": a.width, "height": a.height,
                  "original_filename": a.original_filename},
        "segmentation_source": a.segmentation_source,
        "vni": None if a.vni is None else {"raw": a.vni_raw, "value": a.vni, "band": a.vni_band,
                                            "radius_factor": cfg.vni.radius_factor},
        "summary": {
            "wire_count": len(a.detections),
            "scored_count": len(crs),
            "counts_by_class": counts,
            "mean_crs": round(sum(crs) / len(crs), 3) if crs else None,
            "max_crs": max(crs) if crs else None,
            "level": worst.level if worst else None,
            "vegetation_count": len(a.vegetation_json or []),
            "vegetation_contact_wires": sorted({i for r in a.vegetation_json or [] for i in r.get("touches", [])}),
        },
        "vegetation": [{"confidence": r["confidence"], "bbox": r["bbox"], "mask_polygons": r["polygons"],
                        "touches": r["touches"]} for r in a.vegetation_json or []],
        "ranking": ranking,
        "wire_mask": None if not md.get("wire_mask") else {
            "url": f"/api/analysis/{a.id}/wiremask.png", **md["wire_mask"]},
        "telemetry": {
            "model": ("WireNet-R34 full-res + span assembler" if md.get("wire_mask") is not None
                      else ("reference polygons" if a.segmentation_source == "annotation" else a.segmentation_source)),
            "wirenet_weights": Path(cfg.segmentation.wirenet_weights).name if cfg.segmentation.wirenet_weights else None,
            "detector_weights": Path(cfg.segmentation.weights).name,
            "inference_s": round(total_ms / 1000, 2) if total_ms else None,
            "resolution": [a.width, a.height], "config": a.config_fingerprint},
        "hazard": hazard_view(md),
        # S4: results analysed with the withdrawn provisional criteria carry a notice (never reprocessed)
        "assessment_basis": {"current": is_current(md) or not md.get("hazard"),
                             "notice": None if is_current(md) or not md.get("hazard") else WITHDRAWN_NOTICE},
        "quality": (a.metadata_json or {}).get("quality"),
        "wires": [
            {
                "index": d.wire_index, "class": d.wire_class, "confidence": d.confidence,
                "status": v["status"], "fail_reason": v["fail_reason"], "bbox": d.bbox_json,
                "mask_polygons": d.polygons_json, "centerline": d.centerline_json,
                "catenary_curve": d.curve_json, "fit": d.fit_json,
                "chord_px": d.chord_px, "sag_px": d.sag_px,
                "sag_ratio": d.sag_ratio if d.sag_ratio is not None or d.status != "not_assessed"
                else (d.fit_json or {}).get("sag_ratio"),
                # S4: resolution of the image-derived ratio (1 px / chord); measurement only, never a limit
                "sag_ratio_resolution": (d.fit_json or {}).get("sag_ratio_resolution"),
                "delta_lim": v["delta_lim"], "delta_lim0": v["delta_lim0"], "crs": v["crs"], "level": v["level"],
                "historical": v["historical"],
                "sag_bands": sag_bands(cfg, d.wire_class, v["delta_lim"]),
                "flags": d.flags_json,
                # v2.16: one class per strand; "uncertain" when the class evidence is split (no forced type)
                "class_status": "uncertain" if "class_uncertain" in (d.flags_json or []) else "confident",
                "class_evidence": (d.fit_json or {}).get("class_evidence"),
                "reason_code": rc[0], "reason": rc[1], "reason_short": wire_reason_short(rc[0]),
                "notes": wire_notes(d.flags_json),
                "action": wire_action(v["status"], v["fail_reason"], d.flags_json, d.bbox_json, a.width, a.height),
                "veg_clearance": clearance.get(str(d.wire_index)),
            }
            for d in a.detections
            for v in (views[id(d)],)
            for rc in (wire_reason(v["status"], v["fail_reason"], d.flags_json,
                                   (d.fit_json or {}).get("chord_gate_px") or gate),)
        ],
        "acquisition": {"metadata": a.metadata_json, "envelope": a.envelope_json},
        "engineering_context": a.context_json,
        "flags": a.flags_json,
        "timings_ms": a.timings_json,
        "parameters": {"fingerprint": a.config_fingerprint, "provisional": cfg.provisional_blocks(),
                       "current_fingerprint": cfg.fingerprint,
                       # v2.20: the readable version is known only while the parameters are unchanged; older runs show
                       # "Outdated configuration" with the exact hash, never a guessed version
                       "config_version": cfg.version if a.config_fingerprint == cfg.fingerprint else None,
                       "current_config_version": cfg.version,
                       "hazard_rule": {"sag_rule": cfg.hazard.sag_rule, "p0_high": cfg.hazard.p0_high,
                                       "p0_medium": cfg.hazard.p0_medium, "alpha": cfg.hazard.alpha},
                       "min_chord_px": cfg.crs.min_chord_px},
        "segmentation_input": (a.metadata_json or {}).get("segmentation_input"),
        "suppressed_spans": (a.metadata_json or {}).get("suppressed_spans") or [],
        "scene": (a.metadata_json or {}).get("scene"),
        "guardrail": guardrail_view(a.metadata_json or {}),
    }


# --------------------------------------------------------------------------- v2.20 public view and safe errors
ERROR_TEXT = {
    "model_unavailable": "Model weights unavailable. Contact the system administrator.",
    "undecodable": "The file could not be read as a photo.",
    "too_small": "The photo is too small to screen.",
}
_PATH = re.compile(r"(?:[A-Za-z]:\\|/)(?:[^\s/\\:]+[/\\])+([^\s/\\:]+)")


def scrub_paths(text: str | None) -> str | None:
    """Replace absolute file-system paths with the bare file name (no user names, folders or drive letters)."""
    return None if text is None else _PATH.sub(lambda m: m.group(1), text)


def sag_bands(cfg, wire_class: str, delta_lim: float | None) -> dict[str, float] | None:
    """The sag ratios where this wire's level changes, from the configured CRS cut-offs (Equation 10:
    CRS = 10 x sag / delta_lim x w_class). v2.21: ``minor_from`` and ``critical_from`` are the lower bounds of Minor
    sag and Critical (inclusive when the configuration says so). The level itself is the one stored with the wire;
    these numbers only draw the meter and the PDF footnote, so no client re-implements the rule."""
    if not delta_lim:
        return None
    w = cfg.crs.class_weights.get(wire_class, 1.0) or 1.0
    minor, critical, inclusive = cfg.crs.levels.bounds()
    return {"minor_from": round(minor / 10 * delta_lim / w, 6), "critical_from": round(critical / 10 * delta_lim / w, 6),
            "critical_inclusive": inclusive}


def public_error(code: str | None, message: str | None) -> str:
    return ERROR_TEXT.get(code or "", "The photo could not be processed. Try again, or contact the administrator.")


_PUBLIC_WIRE = ("index", "class", "status", "bbox", "mask_polygons", "centerline", "catenary_curve", "sag_ratio",
                "sag_ratio_resolution",
                "delta_lim", "sag_bands", "level", "flags", "class_status", "reason_code", "reason_short", "notes", "action",
                "veg_clearance", "provenance", "diagnosis", "comparison")


def public_view(r: dict[str, Any]) -> dict[str, Any]:
    """The result as the public result page needs it (v2.20). Scores, fit parameters, model names, configuration
    hashes, timings, EXIF metadata and the priority ranking are removed server-side; signed-in staff get the full
    record. Drawing geometry and the measured sag / reference limit stay (the page shows them)."""
    out = {k: r.get(k) for k in ("id", "status", "created_at", "completed_at", "inspection", "pole", "submitted_via",
                                 "image", "vegetation", "wire_mask", "guardrail", "flags", "diagnosis", "comparison")}
    out["reviews"] = [{"decision": x["decision"], "by": x["by"], "at": x["at"]} for x in r.get("reviews") or []]
    if r.get("error"):
        out["error"] = {"code": r["error"]["code"], "message": public_error(r["error"]["code"], r["error"]["message"])}
    hz = r.get("hazard") or {}
    out["hazard"] = {"verdict": hz.get("verdict"), "factors": {"vegetation": (hz.get("factors") or {}).get("vegetation") or []},
                     # v2.21: why a result is incomplete (plain words, no scores), so the page never implies safety
                     "reasons": [x for x in hz.get("reasons") or [] if x.startswith("INCONCLUSIVE")]} if hz else None
    q = r.get("quality") or {}
    out["quality"] = {"grade": q.get("grade"), "reason": q.get("reason")} if q else None
    out["scene"] = {"poles_detected": (r.get("scene") or {}).get("poles_detected") or []}
    out["wires"] = []
    for w in r.get("wires") or []:
        x = {k: w.get(k) for k in _PUBLIC_WIRE}
        f = w.get("fit") or {}
        x["fit"] = {k: f[k] for k in ("endpoints", "sag_point", "chord_foot") if k in f}  # drawing only
        out["wires"].append(x)
    out["public"] = True
    return out


GUARDRAIL_ACTIONS = ("proceed", "retake")


def guardrail_view(md: dict) -> dict | None:
    """v2.17: the stored guardrail assessment plus what the user chose (proceed / retake) and retake links."""
    g = md.get("guardrail")
    if g is None and not md.get("retake_of") and not md.get("guardrail_choice"):
        return None
    return {**(g or {}), "choice": md.get("guardrail_choice"), "retake_of": md.get("retake_of")}


def record_guardrail_choice(db: Database, analysis_id: str, action: str, replaced_by: str | None = None) -> dict | None:
    """Record the user's answer to the retake prompt. A choice never changes the result; "retake" may be followed
    by a new upload carrying ``retake_of`` (linked both ways)."""
    if action not in GUARDRAIL_ACTIONS:
        raise ValueError(action)
    with db.session() as s:
        a = s.get(Analysis, analysis_id)
        if a is None:
            return None
        md = dict(a.metadata_json or {})
        md["guardrail_choice"] = {"action": action, "at": datetime.now(UTC).isoformat(),
                                  **({"replaced_by": replaced_by} if replaced_by else {})}
        a.metadata_json = md
        return md["guardrail_choice"]


def link_retake(db: Database, new_id: str, retake_of: str) -> bool:
    with db.session() as s:
        old = s.get(Analysis, retake_of)
        new = s.get(Analysis, new_id)
        if old is None or new is None:
            return False
        new.metadata_json = {**(new.metadata_json or {}), "retake_of": retake_of}
    record_guardrail_choice(db, retake_of, "retake", replaced_by=new_id)
    return True
