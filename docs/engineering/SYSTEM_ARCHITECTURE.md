# System architecture

WIREFRAME is a single-process web application that screens photographs of overhead wires. It segments the wires,
fits a catenary to each span, and reports the image-derived sag ratio. Power-line and telecom spans are compared
with configured reference ranges, and vegetation contact and photo quality are reported. All of this goes to a
public Field Screener and a signed-in admin portal.

## Components

```
Browser ─┬─ /            landing.html   (public)
         ├─ /workspace   field.html     Field Screener (public, no sign-in)
         └─ /admin       admin.html     Admin portal (signed-in staff)
              │  vanilla ES modules from app/web/assets/ (no build step)
              ▼
FastAPI app (app/main.py: create_app)
  ├─ app/api/        11 routers: analysis, operations (public + staff), dashboard, reports, auth, admin,
  │                  admin.system, review, assistant, help
  ├─ app/services/   persistence, serialisation, sag comparison, screening, review flow, PDF, queue, audit
  ├─ app/pipeline/   image analysis (pure: arrays in, AnalysisOutcome out)
  ├─ app/assistant/  WIREFRAME Assistant (staff) and citizen help (public, no tools)
  └─ app/core/       settings, analysis config, database, security, model provisioning, AppState
              │
              ├─ SQLite (WAL) ─ <data_dir>/wireframe.db
              ├─ files ─────── <data_dir>/uploads, renders, reports, backups
              └─ models/ ───── weights, downloaded from the model-weights branch (app/core/weights.py)
```

## Process model

* **One process, one worker.** `app/services/queue.py` runs a single consumer coroutine over an `asyncio.Queue`.
  Each analysis runs in a worker thread, so the event loop keeps serving requests. A single consumer keeps model
  inference serial and memory bounded, because Ultralytics predictors are not thread-safe. The in-memory login and
  upload throttles in `app/core/security.py` also assume a single process.
* **Startup** (`create_app` → `lifespan`):
  1. `weights.ensure_in_background(MODELS_DIR)` starts fetching any missing model file.
  2. `build_state` opens the database, runs the additive migration (after a backup) and builds the segmenter.
  3. The model loader runs.
  4. The worker starts unless `WIREFRAME_START_WORKER=false`.

  Analyses left in `processing` by a crash are re-enqueued.
* **Validation mode.** Until the weights are present, an upload without reference polygons is refused with HTTP 503
  before anything is stored. There is no mock detector. `GET /api/health` reports model readiness.

## Analysis flow

1. `POST /api/analysis/upload` (`app/services/analysis.py: ingest`):
   * validates the file (type, size `WIREFRAME_MAX_UPLOAD_MB`, decodability, HEIC via pi-heif);
   * reads the EXIF capture envelope, then strips GPS from the stored copy;
   * creates or updates the `poles` / `pole_locations` rows and inserts an `analyses` row (`processing`);
   * enqueues the analysis.

   The response carries the analysis id, a 128-bit random capability. Whoever holds it can read the result, and
   results cannot be enumerated.
2. The worker calls `process` → `app/pipeline/runner.py: run_analysis`. The stages are described in
   [COMPUTER_VISION_PIPELINE.md](COMPUTER_VISION_PIPELINE.md).
3. `persist` writes `detections` and the analysis metadata (`metadata_json`: hazard, quality, guardrail, scene,
   timings, provenance) and saves the wire mask PNG.
4. The client polls `GET /api/analysis/{id}`, then reads `GET /api/analysis/{id}/result`.
   * `serialize_result` adds the reference comparison (`app/services/comparison.py`), the diagnostics and the
     screening summary at read time. They are not stored, so historical records are always compared with the
     reference configured now, and they say so.
   * Anonymous callers get `public_view(...)`. Technical fields are returned only to signed-in staff.
5. Staff then review the result in the admin portal. Review progress and append-only decisions are kept apart from
   the machine observations (D-38, `app/services/review_flow.py`).

## Three separate records (D-38)

| Record | Where | Changed by |
|---|---|---|
| A. Machine observations | `analyses`, `detections`, `metadata_json` | the pipeline only. Never edited; never reprocessed silently |
| B. Screening indicators: per-wire reference comparison, problem wires, triage risk | computed on read (`comparison.py`, `screening.py`) | configuration (`comparison_reference`, `telecom_reference`, the risk rubric in `app_settings`) |
| C. Administrative decisions | `review_progress`, `admin_decisions` (append-only), `audit_events` | authorised staff |

## Security model (`app/core/security.py`)

* **Roles** (`admin_users.role`): `admin` and `inspector`.
  * `require_admin` accepts either role. It guards `/api/admin/*`, `/api/dashboard/*`, `/api/assistant/*`, retry and
    delete.
  * `require_system` accepts `admin` only. It guards the `admin.system` router: settings, models, queue, location
    pins, record kind, archive / restore, system exports.
* **Passwords:** scrypt (N = 2^14, r = 8, p = 1, 16-byte salt), at least 12 characters.
* **Sessions:** a 256-bit token in the `wf_admin` cookie (HttpOnly, SameSite=Strict, Secure when
  `WIREFRAME_COOKIE_SECURE=true`). Only its SHA-256 is stored. Sessions expire after 8 hours and are revoked on logout.
* **CSRF defence in depth:** every state-changing admin request must carry an Origin or Referer matching the host.
  Otherwise it gets 403 `bad_origin`.
* **Throttling:**
  * login failures per (client, username) → 429 `too_many_attempts`;
  * anonymous uploads per client (`WIREFRAME_PUBLIC_UPLOADS_PER_WINDOW`).
* **Privacy:** GPS EXIF is removed from stored photos. Error messages shown to the public are generic
  (`public_error`), and file paths are scrubbed from staff messages.
* **Assistant:** it calls Claude only when `ANTHROPIC_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`) is set in the server
  environment. Otherwise it answers from the bundled documentation only.

## Error handling

All errors are JSON `{"error": {"code", "message"}}` (`app/main.py` exception handlers). An unexpected exception is
logged with its traceback and returned as `internal_error` (500), never as an HTML page.
