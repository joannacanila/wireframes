"""/api/admin: the command centre. Every route requires a signed-in administrator."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import cv2
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.security import require_admin
from app.core.state import AppState, get_state, list_checkpoints
from app.models.orm import Analysis, Pole, Review
from app.pipeline.acquisition import load_rgb
from app.pipeline.preprocess import grayscale_path
from app.pipeline.vni import spectrum_image
from app.services import sites
from app.services.report_pdf import build_sites_report

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])
State = Annotated[AppState, Depends(get_state)]
User = Annotated[object, Depends(require_admin)]


# ----------------------------------------------------------------------------- overview & queue


@router.get("/overview")
def overview(st: State) -> dict:
    with st.db.session() as s:
        by_status = dict(s.execute(select(Analysis.status, func.count()).group_by(Analysis.status)).all())
        levels = {"low": 0, "medium": 0, "high": 0}
        needs_review = []
        # A pole is counted once, by its latest finished inspection (as the MSPI ranking does).
        for _, a in sites.latest_per_pole(s):
            crs = [d for d in a.detections if d.crs is not None]
            if not crs:
                continue
            worst = max(crs, key=lambda d: d.crs)
            levels[worst.level] += 1
            if worst.level == "high" and not a.reviews:
                needs_review.append({"id": a.id, "pole_code": a.pole.pole_code, "max_crs": worst.crs,
                                     "created_at": a.created_at.isoformat()})
        poles = s.scalar(select(func.count()).select_from(Pole))
    needs_review.sort(key=lambda r: -r["max_crs"])
    return {"analyses_by_status": by_status, "poles": poles, "poles_by_level": levels,
            "needs_review": needs_review[:12], "queue": queue_state(st)}


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


@router.get("/queue")
def queue(st: State) -> dict:
    return queue_state(st)


# ----------------------------------------------------------------------------- models & settings


@router.get("/models")
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


@router.post("/models/active")
def activate(body: Activate, st: State, user: User) -> dict:
    ok, err = st.activate_weights(body.name)
    if not ok:
        raise HTTPException(422, {"code": "checkpoint_rejected", "message": err or "Checkpoint could not be loaded."})
    st.put_setting("active_weights", body.name, user.username)
    return models(st)


class SettingsBody(BaseModel):
    require_reference_masks: bool


@router.put("/settings")
def put_settings(body: SettingsBody, st: State, user: User) -> dict:
    st.put_setting("require_reference_masks", body.require_reference_masks, user.username)
    return models(st)


# ----------------------------------------------------------------------------- analysis detail


@router.get("/analyses/{analysis_id}/spectrum.png")
def spectrum(analysis_id: str, st: State) -> Response:
    """log(1 + P) of the Hann-windowed grayscale path with the r_L circle (Equations 1-3)."""
    with st.db.session() as s:
        a = s.get(Analysis, analysis_id)
        if a is None:
            raise HTTPException(404, {"code": "not_found", "message": "Analysis not found."})
        name = a.stored_name
    rgb = load_rgb((st.settings.upload_dir / name).read_bytes())
    gray = grayscale_path(rgb, st.config.preprocessing)
    spec = spectrum_image(gray if not st.config.vni.remove_mean else gray - gray.mean())
    colored = cv2.applyColorMap(spec, cv2.COLORMAP_INFERNO)
    m, n = spec.shape
    r = int(round(st.config.vni.radius_factor * min(m, n) / 2))
    cv2.circle(colored, (n // 2, m // 2), r, (255, 255, 255), 1, cv2.LINE_AA)
    ok, png = cv2.imencode(".png", colored)
    return Response(png.tobytes(), media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


class ReviewBody(BaseModel):
    decision: str = Field(pattern="^(confirmed|overridden|dismissed)$")
    override_level: str | None = Field(default=None, pattern="^(low|medium|high)$")
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _consistent(self) -> ReviewBody:
        if (self.decision == "overridden") != (self.override_level is not None):
            raise ValueError("an override needs a level, and only an override has one")
        if self.decision in ("overridden", "dismissed") and not self.note.strip():
            raise ValueError("explain an override or a dismissal in the note")
        return self


@router.get("/analyses/{analysis_id}/reviews")
def list_reviews(analysis_id: str, st: State) -> list[dict]:
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
        s.add(Review(analysis_id=analysis_id, reviewer_id=user.id, reviewer_name=user.display_name or user.username,
                     decision=body.decision, override_level=body.override_level, note=body.note.strip()))
    return list_reviews(analysis_id, st)


# ----------------------------------------------------------------------------- poles & temporal tracking


def _pole_point(a: Analysis) -> dict:
    crs = [d.crs for d in a.detections if d.crs is not None]
    ratios = [d.sag_ratio for d in a.detections if d.sag_ratio is not None]
    worst = max((d for d in a.detections if d.crs is not None), key=lambda d: d.crs, default=None)
    rev = a.reviews[-1] if a.reviews else None
    return {"analysis_id": a.id, "at": a.created_at.isoformat(), "status": a.status, "vni": a.vni,
            "max_crs": max(crs) if crs else None, "mean_crs": sum(crs) / len(crs) if crs else None,
            "max_sag_ratio": max(ratios) if ratios else None, "n_wires": len(a.detections),
            "level": worst.level if worst else None, "source": a.segmentation_source,
            "review": None if rev is None else {"decision": rev.decision, "override_level": rev.override_level}}


@router.get("/poles")
def poles(st: State, q: Annotated[str | None, Query(max_length=64)] = None) -> list[dict]:
    with st.db.session() as s:
        stmt = select(Pole).options(selectinload(Pole.location), selectinload(Pole.analyses)
                                    .selectinload(Analysis.detections), selectinload(Pole.analyses).selectinload(Analysis.reviews))
        if q:
            stmt = stmt.where(Pole.pole_code.ilike(f"%{q}%") | Pole.barangay.ilike(f"%{q}%"))
        out = []
        for p in s.scalars(stmt).all():
            pts = [_pole_point(a) for a in p.analyses if a.status in sites.FINISHED]
            latest = pts[-1] if pts else None
            first = next((x for x in pts if x["max_crs"] is not None), None)
            trend = None
            if latest and first and latest is not first and latest["max_crs"] is not None:
                trend = latest["max_crs"] - first["max_crs"]
            out.append({"pole_code": p.pole_code, "barangay": p.barangay, "inspections": len(pts),
                        "has_location": p.location is not None, "latest": latest, "trend_max_crs": trend})
        def worst_first(r: dict) -> tuple:
            crs = (r["latest"] or {}).get("max_crs")
            return (crs is None, -(crs or 0.0), r["pole_code"])

        return sorted(out, key=worst_first)


@router.get("/poles/{pole_code}")
def pole_history(pole_code: str, st: State) -> dict:
    with st.db.session() as s:
        p = s.scalar(select(Pole).where(Pole.pole_code == pole_code).options(
            selectinload(Pole.location), selectinload(Pole.analyses).selectinload(Analysis.detections),
            selectinload(Pole.analyses).selectinload(Analysis.reviews)))
        if p is None:
            raise HTTPException(404, {"code": "not_found", "message": "Pole not found."})
        return {"pole_code": p.pole_code, "barangay": p.barangay,
                "location": None if p.location is None else {"lat": p.location.latitude, "lon": p.location.longitude,
                                                              "source": p.location.source},
                "history": [_pole_point(a) for a in p.analyses],
                "note": "Pole-level aggregates per inspection. Wires are not matched one-to-one across photographs, "
                        "so a change reflects the pole's worst and average wire, not a single tracked wire."}


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


@router.get("/exports/manifest.csv")
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


@router.get("/exports/wires.csv")
def wires(st: State) -> StreamingResponse:
    """One row per detected wire across all analyses, with the fit and score inputs."""
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
                             ";".join(d.flags_json)])
    return _csv(rows, ["analysis_id", "pole_code", "analyzed_at", "wire", "class", "confidence", "status",
                       "fail_reason", "a_px", "b_px", "c_px", "rms_residual_px", "rel_residual", "chord_px", "sag_px",
                       "sag_ratio", "delta_lim", "crs", "level", "pruned_px", "points", "flags"],
                f"wireframe-wires-{_stamp()}.csv")


@router.get("/exports/sites.pdf")
def sites_pdf(st: State, user: User,
              alpha: float | None = None, beta: float | None = None, gamma: float | None = None) -> Response:
    weights = (alpha, beta, gamma) if None not in (alpha, beta, gamma) else None
    if weights and abs(sum(weights) - 1) > 1e-6:
        raise HTTPException(422, {"code": "weights", "message": "alpha + beta + gamma must equal 1."})
    with st.db.session() as s:
        payload = sites.ranking_payload(s, st.config, weights)
        sens = sites.sensitivity_payload(s, st.config, baseline=weights)
    pdf = build_sites_report(payload, sens, prepared_by=user.display_name or user.username,
                             provisional=st.config.provisional_blocks())
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="wireframe-sites-{_stamp()}.pdf"'})
