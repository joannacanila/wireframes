"""/api/analysis routes.

Public (field screener, no sign-in): upload, status, result, image of one analysis. An
analysis id is a 128-bit random capability, so a result is visible to whoever submitted it
(or was given its link) and is never enumerable. Admin only: the listing, retry and delete.
"""

from __future__ import annotations

import mimetypes
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse

from app.core.security import client_key, current_admin, require_admin, require_system
from app.core.state import AppState, get_state
from app.models.orm import Analysis, Pole
from app.pipeline.acquisition import ImageRejected
from app.services import analysis as svc
from app.services import sites

router = APIRouter(prefix="/api/analysis", tags=["analysis"])
State = Annotated[AppState, Depends(get_state)]
Admin = Annotated[object, Depends(require_admin)]
System = Annotated[object, Depends(require_system)]


async def _read_limited(f: UploadFile, limit: int) -> bytes:
    data = await f.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            {"code": "too_large", "message": f"File exceeds {limit // 2**20} MB."})
    return data


def _segmentable(st: AppState, has_labels: bool) -> bool:
    if has_labels:
        return True
    return not st.require_reference and st.model_ready()


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload(
    request: Request,
    st: State,
    file: Annotated[UploadFile, File(description="Pole photograph (JPEG, PNG, WebP, TIFF)")],
    pole_code: Annotated[str | None, Form(max_length=64)] = None,
    inspection_name: Annotated[str | None, Form(max_length=120)] = None,
    city: Annotated[str | None, Form(max_length=120)] = None,
    barangay: Annotated[str | None, Form(max_length=120)] = None,
    span_m: Annotated[float | None, Form(gt=0, le=500)] = None,
    temperature_c: Annotated[float | None, Form(ge=-10, le=70)] = None,
    wind_speed_mps: Annotated[float | None, Form(ge=0, le=80)] = None,
    distance_m: Annotated[float | None, Form(gt=0, le=200)] = None,
    elevation_deg: Annotated[float | None, Form(ge=0, le=90)] = None,
    latitude: Annotated[float | None, Form()] = None,
    longitude: Annotated[float | None, Form()] = None,
    labels: Annotated[UploadFile | None, File(description="Optional YOLO-seg reference polygons")] = None,
    retake_of: Annotated[str | None, Form(max_length=64, description="v2.17: id of the analysis this photo replaces")] = None,
) -> dict:
    """Validate the image, strip GPS, store it, and enqueue the analysis."""
    admin = current_admin(request)
    if admin is None:  # anonymous field upload: rate-limited, cannot rewrite pole metadata
        wait = st.upload_limiter.blocked(client_key(request))
        if wait:
            raise HTTPException(429, {"code": "rate_limited", "message":
                                      f"Upload limit reached. Try again in {int(wait // 60) + 1} min."},
                                headers={"Retry-After": str(int(wait))})
    limit = st.settings.max_upload_mb * 2**20
    data = await _read_limited(file, limit)
    label_bytes = await _read_limited(labels, 2**20) if labels is not None and labels.filename else None
    try:
        aid = svc.ingest(
            st.db, st.settings, st.config, data=data, filename=file.filename or "upload",
            pole_code=pole_code, barangay=barangay, inspection_name=inspection_name, city=city,
            context={"span_m": span_m, "temperature_c": temperature_c, "wind_speed_mps": wind_speed_mps},
            distance_m=distance_m, elevation_deg=elevation_deg,
            latitude=latitude, longitude=longitude, labels=label_bytes,
            model_ready=_segmentable(st, label_bytes is not None), trusted=admin is not None,
        )
    except ImageRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"code": exc.code, "message": str(exc)}) from exc
    except svc.UploadError as exc:
        detail = {"code": exc.code, "message": str(exc)} | ({"hint": exc.hint} if exc.hint else {})
        raise HTTPException(exc.status, detail) from exc
    if admin is None:
        st.upload_limiter.hit(client_key(request))
    if retake_of:  # v2.17 guardrail: the user retook the photo; both analyses are kept and linked
        svc.link_retake(st.db, aid, retake_of)
    st.queue.submit(aid)
    return {"id": aid, "status": "processing", "status_url": f"/api/analysis/{aid}",
            "result_url": f"/api/analysis/{aid}/result"}


def _summary(a: Analysis) -> dict:
    crs = a.valid_crs
    worst = max((d for d in a.detections if d.crs is not None), key=lambda d: d.crs, default=None)
    review = a.reviews[-1] if a.reviews else None
    return {
        "id": a.id, "status": a.status, "pole_code": a.pole.pole_code, "barangay": a.pole.barangay,
        "inspection_name": a.inspection_name, "city": a.city,
        "created_at": a.created_at.isoformat(), "filename": a.original_filename,
        "segmentation_source": a.segmentation_source, "submitted_via": a.submitted_via,
        "error_code": a.error_code, "vni": a.vni, "vni_band": a.vni_band, "wire_count": len(a.detections),
        "max_crs": max(crs) if crs else None, "level": worst.level if worst else None,
        "max_sag_ratio": max((d.sag_ratio for d in a.detections if d.sag_ratio is not None), default=None),
        "review": None if review is None else {"decision": review.decision, "override_level": review.override_level,
                                                "by": review.reviewer_name, "at": review.created_at.isoformat()},
        "thumbnail_url": f"/api/analysis/{a.id}/image",
    }


def _load(st: AppState, analysis_id: str) -> Analysis:
    with st.db.session() as s:
        a = s.get(Analysis, analysis_id)
        if a is None:
            raise HTTPException(404, {"code": "not_found", "message": "Analysis not found."})
        s.expunge(a)
        return a


@router.get("/{analysis_id}")
def get_status(analysis_id: str, st: State, request: Request) -> dict:
    a = _load(st, analysis_id)
    staff = current_admin(request) is not None
    msg = svc.scrub_paths(a.error_message) if staff else (svc.public_error(a.error_code, a.error_message) if a.error_code else None)
    return {"id": a.id, "status": a.status, "error_code": a.error_code, "error_message": msg}


@router.get("/{analysis_id}/result")
def get_result(analysis_id: str, st: State, request: Request) -> dict:
    with st.db.session() as s:
        a = svc.load_analysis(s, analysis_id)
        if a is None:
            raise HTTPException(404, {"code": "not_found", "message": "Analysis not found."})
        if a.status == "processing":
            raise HTTPException(409, {"code": "processing", "message": "Analysis is still processing."})
        staff = current_admin(request) is not None
        rank = sites.rank_of(s, st.config, a.pole.pole_code) if a.status != "error" and staff else None
        r = svc.serialize_result(a, st.config, rank, st.settings)
        return r if staff else svc.public_view(r)  # v2.20: technical fields only for signed-in staff


@router.post("/{analysis_id}/guardrail")
def guardrail_choice(analysis_id: str, st: State, body: dict) -> dict:
    """v2.17: record the answer to the retake prompt ("proceed" or "retake"). Same capability rule as the result:
    whoever holds the analysis id. The choice is recorded; it never changes the analysis."""
    action = (body or {}).get("action")
    if action not in svc.GUARDRAIL_ACTIONS:
        raise HTTPException(422, {"code": "bad_action", "message": "action must be 'proceed' or 'retake'."})
    choice = svc.record_guardrail_choice(st.db, analysis_id, action)
    if choice is None:
        raise HTTPException(404, {"code": "not_found", "message": "Analysis not found."})
    return {"id": analysis_id, "choice": choice}


@router.get("/{analysis_id}/image")
def get_image(analysis_id: str, st: State) -> FileResponse:
    a = _load(st, analysis_id)
    path = st.settings.upload_dir / a.stored_name
    if not path.exists():
        raise HTTPException(410, {"code": "file_missing", "message": "Stored image is missing."})
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                        headers={"Cache-Control": "private, max-age=86400"})


@router.get("/{analysis_id}/wiremask.png")
def get_wire_mask(analysis_id: str, st: State) -> FileResponse:
    """v2.11: WireNet's full per-pixel class mask (indexed PNG, background transparent), the same size as
    the stored image. Same access rule as the image (the analysis id is the capability)."""
    a = _load(st, analysis_id)
    path = svc.wire_mask_path(st.settings, a.id)
    if not path.exists():
        raise HTTPException(404, {"code": "no_wire_mask", "message": "No WireNet mask for this analysis."})
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@router.post("/{analysis_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry(analysis_id: str, st: State, user: Admin) -> dict:
    """Re-run a failed analysis on its stored image, e.g. after the model has been installed."""
    with st.db.session() as s:
        a = s.get(Analysis, analysis_id)
        if a is None:
            raise HTTPException(404, {"code": "not_found", "message": "Analysis not found."})
        if a.status != "error":
            raise HTTPException(409, {"code": a.status, "message": "Only failed analyses can be retried."})
        if not _segmentable(st, a.label_name is not None):
            raise HTTPException(503, {"code": "model_unavailable",
                                      "message": "The segmentation model is still not available."})
        a.status, a.error_code, a.error_message, a.completed_at = "processing", None, None, None
        from app.services import audit
        audit.record(s, audit.actor_of(user), "inspection.retried", a.pole.pole_code, a.id)
    st.queue.submit(analysis_id)
    return {"id": analysis_id, "status": "processing"}


@router.delete("/{analysis_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_analysis(analysis_id: str, st: State, user: System) -> None:
    """Administrators only (v2.19). Deletes the analysis; detections, reviews and report go with it through ON DELETE CASCADE."""
    files = []
    with st.db.session() as s:
        a = s.get(Analysis, analysis_id)
        if a is None:
            raise HTTPException(404, {"code": "not_found", "message": "Analysis not found."})
        if a.status == "processing":
            raise HTTPException(409, {"code": "processing", "message": "Cannot delete while processing."})
        files = [st.settings.upload_dir / a.stored_name]
        if a.label_name:
            files.append(st.settings.upload_dir / a.label_name)
        if a.report:
            files.append(st.settings.report_dir / a.report.file_name)
        files.append(svc.wire_mask_path(st.settings, a.id))  # v2.11
        pole_id = a.pole_id
        from app.services import audit
        audit.record(s, audit.actor_of(user), "inspection.deleted", a.pole.pole_code, a.id,
                     file=a.original_filename, status=a.status)
        s.delete(a)
        s.flush()
        pole = s.get(Pole, pole_id)
        if pole is not None and not pole.analyses:
            s.delete(pole)  # a pole exists only through its photographs
    for f in files:
        f.unlink(missing_ok=True)
