# Frontend engineering

The frontend is plain HTML, CSS and native ES modules in `app/web/`, with no framework, bundler or build step. The
FastAPI app serves the pages, and `/assets/*` is served with `Cache-Control: no-cache`, so browsers revalidate after
an update. A `?v=` query string on each import also changes when a module changes.

## Pages

| Route | Page | Audience | Entry module |
|---|---|---|---|
| `/` | `landing.html` | public | `landing.js` |
| `/workspace`, `/workspace/result/{id}` | `field.html`: Field Screener (upload a photo, read the screening result) | public, no sign-in | `field.js` |
| `/admin`, `/admin/{path}` | `admin.html`: admin portal (Dashboard, Inspections, Map & Locations, inspection sheet, review) | signed-in staff | `admin.js` |

Every page loads `fonts.css`, `styles.css` (shared tokens and base styles) and `notice.js` (the Important Notice,
D-37) before its own CSS and module.

## Module graph (45873c3)

```
field.js ── lib, viewer, result-view, upload-rules, status, toast, facts, location, findings
admin.js ── lib, viewer, findings, status, toast, admin-map, assistant, result-view, admin-review,
            upload-rules, location
result-view.js ── lib, viewer          admin-review.js ── lib, toast, result-view
admin-map.js ── toast, result-view     findings.js ── lib        status.js ── toast
leaf modules: lib, viewer, toast, facts, location, upload-rules, assistant, notice, landing
```

| Module | Responsibility |
|---|---|
| `lib.js` | `api()` fetch wrapper (JSON errors → exceptions), `esc`, `ago`, `levelChip`, `waitForAnalysis` polling |
| `viewer.js` | Interactive overlay viewer in original-image coordinates: mask polygons (even-odd fill), centerlines, fitted curves, chord and sag point, inferred gaps dashed, zoom and pan (focus zoom capped at 4× fit) |
| `result-view.js` | **The one result view (S18)** for the Field Screener, the admin inspection sheet and the quick-review drawer. It renders what the server decided (`r.screening`, `r.overview`, `w.finding`) and computes no threshold, count or classification |
| `findings.js` | Plain-language reading of a result, shared by both portals. Never presents a screening as a safety certification |
| `field.js` | Field Screener flow: upload, preview (HEIC through `POST /api/analysis/preview`), capture location, guardrail prompt, result. The technical view was removed in S18 |
| `admin.js` | Admin portal shell and pages. Presentation only: every API it calls is guarded server-side |
| `admin-review.js` | Review workflow: start, decision, reopen (S18, D-38) |
| `admin-map.js` | Map & Locations, priority list (`/api/dashboard/priority`), dispatch route |
| `assistant.js` | WIREFRAME Assistant panel (staff) |
| `location.js` | Device geolocation, reverse lookup, accuracy label |
| `upload-rules.js` | Client-side file checks that mirror the server limits |
| `status.js`, `toast.js` | Server-status banner and notifications |
| `facts.js` | Static explanatory facts for the Field Screener |
| `notice.js` | Important Notice banner |

CSS: `styles.css` (shared), `field.css`, `admin.css`, `landing.css`, `result-view.css` (shared result view), and
`fonts.css` with the self-hosted `fonts/`.

## Rules the frontend follows

* **No client-side assessment.** Classifications, zones, counts, problem wires and risk come from the server
  (`comparison.py`, `screening.py`). The client only renders them, so every view says the same thing.
* **Role checks are server-side.** The client hides controls the role cannot use, but access is enforced by
  `require_admin` / `require_system`.
* **Public vs staff payloads.** The Field Screener receives `public_view(...)`. Technical fields appear only in staff
  responses.
* **Accessibility conventions (S18):**
  * an info panel opened by hover is also closed by Escape (WCAG 1.4.13), via `data-quiet`;
  * the toolbars wrap at ≤ 720 px, so there is no horizontal scroll at 390 px;
  * icon-only controls carry an `aria-label`.
* **Cache-busting.** When a module changes, bump its `?v=` in every importer and in the HTML.

## Known inconsistencies (recorded, not fixed)

Asset versions are not uniform across pages. For example, `styles.css?v=2.29` in `admin.html` vs `?v=2.28` in
`field.html` and `landing.html`, and `notice.js?v=2.23` vs `?v=2.28`. This is harmless, because the files are
revalidated (`no-cache`) and each page imports one copy, but a future change should align them.

## Tests that cover the frontend

* Tests that run the ES modules under Node: `tests/test_s18_refinement.py`, `test_s8_sag_reference.py`,
  `test_v213_viewer_opacity.py` and `test_v221.py`. They are skipped when `node` is not installed.
* Asset and markup assertions in `test_portals.py`, `test_v211_field_ui.py`, `test_v219_portal.py` and others.
* The browser end-to-end check `scripts/e2e_check.py` (Playwright, Chrome). See
  [TESTING_AND_VALIDATION.md](TESTING_AND_VALIDATION.md).
