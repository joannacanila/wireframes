"""WIREFRAME FastAPI application.

    uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import admin, analysis, auth, dashboard, evaluation, reports
from app.core.config import get_analysis_config, get_settings
from app.core.state import AppState, build_state, get_state
from app.pipeline.runner import Segmenter

WEB_DIR = Path(__file__).parent / "web"
log = logging.getLogger("wireframe")


def create_app(state: AppState | None = None, segmenter: Segmenter | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st = state or build_state(get_settings(), get_analysis_config(), segmenter)
        app.state.wf = st
        loader = getattr(st.segmenter, "load", None)
        if loader and not loader():
            log.warning("segmentation model not loaded: %s", getattr(st.segmenter, "error", ""))
        if st.settings.start_worker:
            await st.queue.start()
        yield
        await st.queue.stop()

    app = FastAPI(title="WIREFRAME", version="2.0.0", lifespan=lifespan,
                  description="Vision-based diagnostic framework for urban overhead wire risk screening.")
    app.include_router(analysis.router)
    app.include_router(dashboard.router)
    app.include_router(reports.router)
    app.include_router(evaluation.router)
    app.include_router(auth.router)
    app.include_router(admin.router)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # v2.10: an unexpected error is logged with its traceback and answered as structured JSON
        # (the UI shows the message), never as an HTML page or a dropped connection.
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"error": {"code": "internal_error", "message":
                                       f"Internal error ({type(exc).__name__}); it has been logged."}}, status_code=500)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
        return JSONResponse({"error": detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.get("/api/health", tags=["system"])
    def health(request: Request) -> dict:
        st = get_state(request)
        seg = st.segmenter
        loader = getattr(seg, "load", None)
        # Re-check on every call so installing the weights switches to field mode without a
        # restart (YoloSegmenter.load is a no-op once loaded, one stat() while the file is absent).
        ready = bool(loader()) if loader else bool(getattr(seg, "ready", False))
        return {
            "status": "ok",
            # field: the model segments uploads; validation: reference polygons are required
            "mode": "field" if ready else "validation",
            "model": {"ready": ready, "error": getattr(seg, "error", None),
                      "weights": st.config.segmentation.weights},
            "config_fingerprint": st.config.fingerprint,
            "provisional": st.config.provisional_blocks(),
            "queue_depth": st.queue.depth,
        }

    @app.get("/api/config", tags=["system"])
    def config(request: Request) -> dict:
        st = get_state(request)
        return {"fingerprint": st.config.fingerprint, **st.config.model_dump(mode="json")}

    app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def landing() -> FileResponse:
        return FileResponse(WEB_DIR / "landing.html", headers={"Cache-Control": "no-cache"})

    @app.get("/workspace", include_in_schema=False)
    @app.get("/workspace/result/{_id}", include_in_schema=False)
    def field(_id: str | None = None) -> FileResponse:
        """Side A: the field screener. Open access, no sign-in."""
        return FileResponse(WEB_DIR / "field.html", headers={"Cache-Control": "no-cache"})

    @app.get("/admin", include_in_schema=False)
    @app.get("/admin/{_path:path}", include_in_schema=False)
    def admin_portal(_path: str | None = None) -> FileResponse:
        """Side B: the admin command centre. The page shows its sign-in form until /api/auth/me
        succeeds; every /api/admin and /api/dashboard route is guarded server-side."""
        return FileResponse(WEB_DIR / "admin.html", headers={"Cache-Control": "no-cache",
                                                             "X-Frame-Options": "DENY"})

    return app


app = create_app()
