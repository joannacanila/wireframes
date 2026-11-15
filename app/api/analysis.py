"""/api/analysis routes.

Public (field screener, no sign-in): upload, status, result, image of one analysis. An
analysis id is a 128-bit random capability, so a result is visible to whoever submitted it
(or was given its link) and is never enumerable. Admin only: the listing, retry and delete.
"""

from __future__ import annotations

import mimetypes
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.core.security import client_key, current_admin, require_admin
from app.core.state import AppState, get_state
from app.models.orm import Analysis, Detection, Pole
from app.pipeline.acquisition import ImageRejected
from app.services import analysis as svc
from app.services import sites

router = APIRouter(prefix="/api/analysis", tags=["analysis"])
State = Annotated[AppState, Depends(get_state)]
Admin = Annotated[object, Depends(require_admin)]


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


@router.get("")
def list_analyses(
    st: State, _: Admin,
    limit: Annotated[int, Query(ge=1, le=500)] = 50, offset: Annotated[int, Query(ge=0)] = 0,
    status_: Annotated[str | None, Query(alias="status")] = None,
    level: Annotated[str | None, Query(pattern="^(low|medium|high)$")] = None,
    q: Annotated[str | None, Query(max_length=64)] = None,
    unreviewed: bool = False,
) -> dict:
    with st.db.session() as s:
        stmt = select(Analysis).join(Pole)
        if status_:
            stmt = stmt.where(Analysis.status == status_)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(Pole.pole_code.ilike(like), Pole.barangay.ilike(like),
                                  Analysis.original_filename.ilike(like)))
        if level:
            stmt = stmt.where(Analysis.id.in_(select(Detection.analysis_id).where(Detection.level == level)))
        rows = s.scalars(stmt.options(selectinload(Analysis.pole), selectinload(Analysis.detections),
                                      selectinload(Analysis.reviews)).order_by(Analysis.created_at.desc())).all()
        items = [_summary(a) for a in rows]
        if level:  # the worst wire decides an analysis's level
            items = [i for i in items if i["level"] == level]
        if unreviewed:
            items = [i for i in items if i["review"] is None and i["status"] in ("complete", "fit_failed")]
        total = s.scalar(select(func.count()).select_from(Analysis))
    return {"total": total, "matched": len(items), "items": items[offset: offset + limit],
            "queue_depth": st.queue.depth}


def _load(st: AppState, analysis_id: str) -> Analysis:
    with st.db.session() as s:
        a = s.get(Analysis, analysis_id)
        if a is None:
            raise HTTPException(404, {"code": "not_found", "message": "Analysis not found."})
        s.expunge(a)
        return a


@router.get("/{analysis_id}")
def get_status(analysis_id: str, st: State) -> dict:
    a = _load(st, analysis_id)
    return {"id": a.id, "status": a.status, "error_code": a.error_code, "error_message": a.error_message}


@router.get("/{analysis_id}/result")
def get_result(analysis_id: str, st: State) -> dict:
    with st.db.session() as s:
        a = svc.load_analysis(s, analysis_id)
        if a is None:
            raise HTTPException(404, {"code": "not_found", "message": "Analysis not found."})
        if a.status == "processing":
            raise HTTPException(409, {"code": "processing", "message": "Analysis is still processing."})
        rank = sites.rank_of(s, st.config, a.pole.pole_code) if a.status != "error" else None
        return svc.serialize_result(a, st.config, rank, st.settings)


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
def retry(analysis_id: str, st: State, _: Admin) -> dict:
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
    st.queue.submit(analysis_id)
    return {"id": analysis_id, "status": "processing"}


@router.delete("/{analysis_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_analysis(analysis_id: str, st: State, _: Admin) -> None:
    """Deletes the analysis; detections, reviews and report go with it through ON DELETE CASCADE."""
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
        s.delete(a)
        s.flush()
        pole = s.get(Pole, pole_id)
        if pole is not None and not pole.analyses:
            s.delete(pole)  # a pole exists only through its photographs
    for f in files:
        f.unlink(missing_ok=True)
