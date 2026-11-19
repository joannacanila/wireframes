# API reference

This reference was generated from the router objects of 45873c3 (`app/api/*.py`), plus the 7 routes defined in
`app/main.py`, for 64 routes in all. The interactive OpenAPI pages are served by the running app at `/docs` and
`/redoc`, and the schema at `/openapi.json`.

## Conventions

* **Access levels** (`app/core/security.py`):

  | Level | Who | Guard |
  |---|---|---|
  | public | anyone | none |
  | staff | a signed-in `admin` or `inspector` | `require_admin` |
  | admin | the `admin` role only (inspectors get 403) | `require_system` |

* **Analysis ids** are 128-bit random capabilities. The public routes that take `{analysis_id}` work for whoever holds
  the id, and results cannot be enumerated.
* **Different views for staff.** `GET /api/analysis/{id}/result` returns `public_view(...)` to anonymous callers and
  the full technical result to staff. `GET /api/analysis/{id}` returns generic error text to the public.
* **CSRF.** Every state-changing staff or admin request must carry an `Origin` or `Referer` matching the host.
  Otherwise the response is 403 `bad_origin`.
* **Errors** are always JSON: `{"error": {"code": "...", "message": "..."}}`. Common codes:

  | Code | Status | Meaning |
  |---|---|---|
  | `not_found` | 404 | No such resource |
  | `processing` | 409 | Analysis still processing |
  | `too_many_attempts` | 429 | Login throttled |
  | `checkpoint_rejected` | 422 | Model activation failed |
  | `internal_error` | 500 | Unexpected error (logged) |

  Upload rejections return 413 or 422 with a reason such as `undecodable` or `unsupported_codec`. When no model is
  installed, an upload gets 503.
* **Session cookie:** `wf_admin`, set by `POST /api/auth/login` and cleared by `POST /api/auth/logout`.
  `GET /api/auth/me` returns `{"user": null}` when signed out.

## Routes in `app/main.py`

| Method | Path | Access | Purpose |
|---|---|---|---|
| GET | `/api/health` | public | Model readiness, mode (field / validation), queue state |
| GET | `/api/config` | public | The full analysis configuration (`config/wireframe.yaml`, typed) and its `fingerprint` |
| GET | `/` | public | `landing.html` |
| GET | `/workspace`, `/workspace/result/{id}` | public | `field.html` (Field Screener) |
| GET | `/admin`, `/admin/{path}` | public page; data needs sign-in | `admin.html` (the sign-in form shows until `/api/auth/me` succeeds) |

`/assets/*` is a static mount of `app/web/assets/` (`RevalidatedStatic` sends `Cache-Control: no-cache`, so browsers revalidate every asset after an update).

## Routers

### `/api/admin`

| Method | Path | Access | Handler | Notes |
|---|---|---|---|---|
| POST | `/api/admin/analyses/{analysis_id}/reviews` | staff | `admin.add_review` |  |
| GET | `/api/admin/audit` | staff | `admin.audit_log` |  |
| GET | `/api/admin/barangays` | staff | `admin.barangays` | Official barangay names matching `q` (assignment picker). |
| GET | `/api/admin/contacts` | staff | `operations.staff.list_contacts` |  |
| POST | `/api/admin/contacts` | admin | `operations.staff.create_contact` |  |
| DELETE | `/api/admin/contacts/{contact_id}` | admin | `operations.staff.deactivate_contact` | Contacts are deactivated, never deleted, so a report that cited one stays explainable. |
| PUT | `/api/admin/contacts/{contact_id}` | admin | `operations.staff.update_contact` |  |
| GET | `/api/admin/dashboard` | staff | `admin.dashboard` | v2.20 triage dashboard. Pole counts use each pole's latest finished operational inspection, so the buckets add up to the total. The triage queue … |
| GET | `/api/admin/exports/inspections.csv` | staff | `admin.inspections_csv` | The inspection list as filtered on screen (operational records only). S14: `ids` limits it to the rows selected on the dashboard. |
| GET | `/api/admin/exports/manifest.csv` | admin | `admin.system.manifest` | One row per pole: latest finished analysis, MSPI rank, review state, location. |
| GET | `/api/admin/exports/sites.pdf` | staff | `admin.sites_pdf` | The inspection priority report. `technical` (or custom weights) adds the scores, weights and weight sensitivity, for administrators only. |
| GET | `/api/admin/exports/wires.csv` | admin | `admin.system.wires` | One row per detected wire across all analyses, with the fit and score inputs. |
| GET | `/api/admin/inspections` | staff | `admin.inspections` | Inspections, newest first or by triage, filtered and paginated server-side. `include_test` is honoured for administrators only; everyone else … |
| POST | `/api/admin/inspections/bulk` | staff | `admin.bulk` | Bulk operations on selected inspections, each recorded in the audit trail. Marking test / operational is for administrators; assigning a barangay … |
| POST | `/api/admin/inspections/{analysis_id}/archive` | admin | `admin.system.archive_inspection` | S18 "Delete inspection": an audited soft delete. Nothing is removed (photo, findings, decisions, audit); the inspection leaves every operational … |
| GET | `/api/admin/inspections/{analysis_id}/context` | staff | `admin.inspection_context` | What the inspection sheet shows beside the result: plain status, the pole's other inspections, its place in the inspection order, and what this … |
| POST | `/api/admin/inspections/{analysis_id}/decision` | staff | `review.save_decision` |  |
| GET | `/api/admin/inspections/{analysis_id}/findings.csv` | staff | `admin.findings_csv` | One inspection's wire findings in plain terms (the technical per-wire export is in the system area). |
| PUT | `/api/admin/inspections/{analysis_id}/record-kind` | admin | `admin.system.set_record_kind` | Re-classify one record. Nothing is deleted; the record only moves in or out of the operational views. |
| POST | `/api/admin/inspections/{analysis_id}/restore` | admin | `admin.system.restore_inspection` |  |
| GET | `/api/admin/inspections/{analysis_id}/review` | staff | `review.get_review` |  |
| POST | `/api/admin/inspections/{analysis_id}/review/reopen` | staff | `review.reopen_review` |  |
| POST | `/api/admin/inspections/{analysis_id}/review/start` | staff | `review.start_review` |  |
| GET | `/api/admin/models` | admin | `admin.system.models` |  |
| POST | `/api/admin/models/active` | admin | `admin.system.activate` |  |
| GET | `/api/admin/operations` | staff | `operations.staff.operations` | The operational dashboard: distinct counts of each pole's latest inspection, the needs-attention queue (every latest inspection not yet reviewed, … |
| POST | `/api/admin/poles/{pole_code}/attachments` | staff | `operations.staff.confirm_attachment` | S18: record a CONFIRMED responsible provider for a pole attachment, from an authoritative record. Area coverage never does this; the record type … |
| DELETE | `/api/admin/poles/{pole_code}/barangay` | staff | `admin.clear_barangay` | S18: remove a barangay assignment (the X beside it). Coordinates, city and the inspections are untouched. |
| PUT | `/api/admin/poles/{pole_code}/barangay` | staff | `admin.set_barangay` |  |
| PUT | `/api/admin/poles/{pole_code}/location` | admin | `admin.system.set_location` | Drop a pin for a pole (administrators). Saved as a manual location; the old value is kept in the audit trail. |
| GET | `/api/admin/queue` | admin | `admin.system.queue` |  |
| GET | `/api/admin/routes/pdf` | staff | `admin.route_pdf` | v2.21: printable dispatch route for the stops picked on Map & Locations, in the given order (pole codes). Only poles with a usable recorded … |
| PUT | `/api/admin/settings` | admin | `admin.system.put_settings` |  |
| GET | `/api/admin/settings/decision-options` | staff | `review.get_options` |  |
| PUT | `/api/admin/settings/decision-options` | admin | `review.put_options` |  |
| GET | `/api/admin/settings/risk-rubric` | staff | `review.get_rubric` |  |
| PUT | `/api/admin/settings/risk-rubric` | admin | `review.put_rubric` |  |
| GET | `/api/admin/system/status` | admin | `admin.system.system_status` |  |
| GET | `/api/admin/users` | staff | `operations.staff.users` |  |

### `/api/analysis`

| Method | Path | Access | Handler | Notes |
|---|---|---|---|---|
| POST | `/api/analysis/preview` | public | `analysis.preview` | S17 (Part 4.1): a browser-displayable copy of a photo BEFORE it is submitted, so HEIC / HEIF (which most browsers cannot draw) previews like any … |
| POST | `/api/analysis/upload` | public | `analysis.upload` | Validate the image, strip GPS, store it, and enqueue the analysis. |
| DELETE | `/api/analysis/{analysis_id}` | admin | `analysis.delete_analysis` | Administrators only (v2.19). Deletes the analysis; detections, reviews and report go with it through ON DELETE CASCADE. |
| GET | `/api/analysis/{analysis_id}` | public | `analysis.get_status` |  |
| GET | `/api/analysis/{analysis_id}/contacts` | public | `operations.public.inspection_contacts` |  |
| POST | `/api/analysis/{analysis_id}/guardrail` | public | `analysis.guardrail_choice` | v2.17: record the answer to the retake prompt ("proceed" or "retake"). Same capability rule as the result: whoever holds the analysis id. The … |
| GET | `/api/analysis/{analysis_id}/image` | public | `analysis.get_image` |  |
| GET | `/api/analysis/{analysis_id}/result` | public | `analysis.get_result` |  |
| POST | `/api/analysis/{analysis_id}/retry` | staff | `analysis.retry` | Re-run a failed analysis on its stored image, e.g. after the model has been installed. |
| GET | `/api/analysis/{analysis_id}/wiremask.png` | public | `analysis.get_wire_mask` | v2.11: WireNet's full per-pixel class mask (indexed PNG, background transparent), the same size as the stored image. Same access rule as the image … |

### `/api/assistant`

| Method | Path | Access | Handler | Notes |
|---|---|---|---|---|
| POST | `/api/assistant/ask` | staff | `assistant.ask` |  |
| GET | `/api/assistant/status` | staff | `assistant.assistant_status` |  |

### `/api/auth`

| Method | Path | Access | Handler | Notes |
|---|---|---|---|---|
| POST | `/api/auth/login` | public | `auth.login` |  |
| POST | `/api/auth/logout` | public | `auth.logout` |  |
| GET | `/api/auth/me` | public | `auth.me` | `{"user": null}` when signed out: a normal state, not an error. |

### `/api/dashboard`

| Method | Path | Access | Handler | Notes |
|---|---|---|---|---|
| GET | `/api/dashboard/priority` | staff | `dashboard.priority` | The inspection order with the configured weights. Trying other weights is a system-area tool (v2.19). |

### `/api/help`

| Method | Path | Access | Handler | Notes |
|---|---|---|---|---|
| POST | `/api/help/ask` | public | `assistant.help.citizen_ask` |  |

### `/api/reports`

| Method | Path | Access | Handler | Notes |
|---|---|---|---|---|
| GET | `/api/reports/{analysis_id}/pdf` | public | `reports.report_pdf` | Generate (or regenerate) the report. The PDF is rebuilt on every request because the MSPI rank depends on the other poles; the latest file and its … |
