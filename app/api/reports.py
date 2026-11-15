"""/api/reports routes: printable screening report (PDF)."""

from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.core.state import AppState, get_state
from app.models.orm import Report
from app.pipeline.acquisition import load_rgb
from app.services import analysis as svc
from app.services import sites
from app.services.report_pdf import build_report

router = APIRouter(prefix="/api/reports", tags=["reports"])
State = Annotated[AppState, Depends(get_state)]


@router.get("/{analysis_id}/pdf")
def report_pdf(analysis_id: str, st: State) -> Response:
    """Generate (or regenerate) the report. The PDF is rebuilt on every request because the
    MSPI rank depends on the other poles; the latest file and its hash are recorded."""
    with st.db.session() as s:
        a = svc.load_analysis(s, analysis_id)
        if a is None:
            raise HTTPException(404, {"code": "not_found", "message": "Analysis not found."})
        if a.status in ("processing", "error"):
            raise HTTPException(409, {"code": a.status, "message": f"No report for an analysis in state '{a.status}'."})
        result = svc.serialize_result(a, st.config, sites.rank_of(s, st.config, a.pole.pole_code))
        rgb = load_rgb((st.settings.upload_dir / a.stored_name).read_bytes())
        pdf = build_report(result, rgb)
        name = f"{a.id}.pdf"
        (st.settings.report_dir / name).write_bytes(pdf)
        digest = hashlib.sha256(pdf).hexdigest()
        if a.report is None:
            a.report = Report(file_name=name, sha256=digest, config_fingerprint=st.config.fingerprint)
        else:
            a.report.sha256, a.report.config_fingerprint = digest, st.config.fingerprint
        filename = f"wireframe-{a.pole.pole_code}-{a.id[:8]}.pdf"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{filename}"', "X-Report-SHA256": digest})
