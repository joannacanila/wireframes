# S18 Final Report: Evidence-First Screening, Admin Decision Workflow, Provider Directory

Session S18, 19 Nov 2026. Working tree `/Users/joannacanila/WIREFRAME-s13`, branch `recovery/s13-master`.
Recovery snapshot of the S17 tree before any S18 change: `refs/recovery/s18-start` (`19a792a9`).
Register entry: **D-38**. Nothing was committed, pushed, merged or deployed. All runtime checks used the isolated
copy of the data (`scratchpad/iso14/var`); the live database was never opened.

---

## 1. Executive summary

WIREFRAME now keeps three things apart everywhere:

| Record | What it is | Who writes it | Where it is stored |
|---|---|---|---|
| Machine observations | Detected wires, measurements, reference comparisons | The pipeline | `analyses`, `detections` (unchanged by S18) |
| Screening indicators | Total wires, **problem wires**, inspection triage, one plain finding, photo tips, next steps | Derived on the server (`app/services/screening.py`) | Computed on read; snapshotted when a decision is made |
| Administrative review | Review status, reviewers, decision, rationale, follow-up, confirmed critical hazards | An administrator | `review_progress`, `admin_decisions` (append-only) |

The citizen page follows **What we know → What you should do next → How did we know?** A partial result is still shown
as a result: the count of wires, the problem wires, and each one's meter. Generic "Incomplete / Inconclusive" headlines
are gone. The admin side gets the review workflow: Not yet reviewed → Partially reviewed (on open) → Review complete
(on a decision), plus amend and reopen, all audited. Providers are grouped by category and matched on location and
conductor type. The UI never says "confirmed responsible" unless an attachment or asset record exists.

Results: **1075 tests pass** (2 xfailed, unchanged), ruff clean, the ported end-to-end check passes **96 / 96** with no
page errors, and the S18 browser checks pass at 1440 px and 390 px (58 / 58 citizen, 29 / 29 admin).

## 2. New system philosophy

- **What we know.** A large count of detected wires, one concise finding written by the server, three counts (problem wires,
  spans measured against a reference, spans exceeding it), three timestamps (photo captured, photo received, screening
  completed), and a card for each problem wire with its sag meter.
- **What you should do next.** The server's next steps (each with one action), the providers for the location, and one
  emergency instruction (911).
- **How did we know?** The photo, larger, with six evidence layers, zoom / pan / pinch, and the wire list synced to the photo.
- The browser draws server decisions only. No threshold, band or rubric number exists in a page script; tests enforce this.
- No internal development notes reach citizens. `source_note` and `admin_notes` are staff-only.

## 3. Machine findings versus administrative decisions

- A processed inspection is **Not yet reviewed**, however clean its result. Processing alone never marks anything reviewed.
- Historical machine statuses (including INCONCLUSIVE / incomplete classifications) are left exactly as stored. They are not
  converted into review statuses.
- A decision stores `machine_json`: detected, problem count, risk level, result code, machine state and rubric version
  *as the reviewer saw them*. If the pipeline or rubric changes later, the record still shows what was decided against.
- The review panel says it in the UI: "The machine screening stays as computed. Your decision is stored separately…"

## 4. New review-status workflow

| Status | Entered by | Audit action |
|---|---|---|
| `NOT_YET_REVIEWED` | default (no `review_progress` row) | none |
| `PARTIALLY_REVIEWED` | opening the inspection (`POST …/review/start`); a second reviewer joins | `review.started`, `review.joined` |
| `REVIEW_COMPLETE` | finalizing a decision (`POST …/decision`) | `review.finalized`, `decision.amended` (previous / new values) |
| back to `PARTIALLY_REVIEWED` | `POST …/review/reopen` with a reason | `review.reopened` |

- A refresh records nothing: `start` is idempotent (tested).
- Multiple reviewers are kept in `reviewers` and each action is audited under its own name.
- Every inspection is accessible, **including failed ones**. A failed inspection shows the failure card and the same review panel.
- The legacy route `POST /api/admin/analyses/{id}/reviews` and the bulk "mark reviewed" action both create the same
  decision record (`_mirror_review`), so there are not two representations of one review.

## 5. Administrative decision model

- `admin_decisions` is **append-only**. Amending creates a new row and sets `superseded_at` on the old one, which keeps
  its text unchanged (tested).
- Options are data (`AppSetting "decision_options"`): For field inspection, For utility coordination, For scheduled
  cleanup, For monitoring, No further administrative action at this time, Other documented decision. Options are retired,
  never deleted (tested). Changing them needs a system-administrator account and is audited.
- A rationale is required; "Other" needs a described rationale.
- Confirmed critical hazards: snapped / hanging wire, wire dangling into a path, sparking / arcing, conductor in an
  accessible area, low clearance confirmed by physical measurement. Low clearance needs a documented clearance reference
  from the rubric, a measured height and a method, and the height must be below the reference. **No clearance reference is
  configured**, so the low-clearance option is disabled with the reason shown. The 4.5 m figure is not used; see §6.
- Legacy decisions map as follows: flagged → For field inspection, dismissed → No further administrative action,
  confirmed → `legacy_confirmed`, overridden → `legacy_overridden`. Source is `legacy_review`, linked by a unique `legacy_review_id`.

## 6. Risk rubric and its supporting references

**Rubric (version 1, effective 2026-11-19).** It counts problem wires: 0 = Normal, 1–9 = Moderate, 10 or more = High.
A confirmed critical hazard sets High. It is an **operational triage tool, not a statutory classification**, and every
surface says so: the chip tooltip, the "Inspection triage" label, the PDF line and the CSV header. It is configurable
(`PUT /api/admin/settings/risk-rubric`, system administrators only, reason required, versioned and audited) and is
tested at 0 / 1 / 9 / 10 / 25, with many wires and zero problems staying Normal.

A **problem wire** needs a supported observation:
- sag beyond its applicable reference, or beyond its typical range;
- a possible loose or dangling end (an endpoint the model left unattached inside the frame);
- vegetation contact;
- an apparently abandoned cable, when its type is not uncertain.

Detected wires and problem wires are always reported separately.

**Two scales, labelled apart.** The per-wire sag meter grades one wire against its own reference. For power lines that
is green Low below 2 %, yellow Moderate 2–3 % inclusive, red High above 3 %; telecom keeps its 2 % / 5 % bands. The band
is the server's `finding.meter_level`. Inspection triage counts problem wires. One wire can be High while the inspection
is Moderate, so the UI labels the triage "Inspection triage".

**Legal and reference sources, by authority type**

| Authority | Source | Status | What it supports | What it does not establish |
|---|---|---|---|---|
| Statute | RA 11361, Anti-Obstruction of Power Lines Act (approved 8 Aug 2019) | **Verified** (elibrary.judiciary.gov.ph) | Sec. 4(t) defines tall plants (> 3 m); Sec. 5 says clearances are set by the Board of Electrical Engineering, approved by DOE per the PEC; Sec. 8 sets duties; Sec. 15 sets penalties | Any numeric clearance; any triage scale |
| Regulatory | ERB Resolution No. 95-21 s.19 (3 % sag, power lines) | Verified in S17 (D-37) | The 3 % power-line reference used by the meter | Telecom sag; inspection-level risk |
| Bill | HB 584 (20th Congress) | **Unverified**: the PDF exists but is a scanned image; title and status not read | Nothing yet | Nothing |
| Local ordinance | Manila Ordinance 9120 (approved 22 Aug 2025) on wires, cables and foreign attachments | **Verified** | That an LGU regulates installation and maintenance of attachments | Anything outside Manila; any Cavite rule |
| Local ordinance | Davao City 0354-23 "Anti-Spaghetti Wire Ordinance" (enforced from 4 Feb 2024); related 0898-22, 0152-17 | **Press only** (PIA, SunStar); not found on pages 1–2 of the city's ordinance site | Context only | Any rule we may cite as text |
| Code / standard | Philippine Electrical Code, NESC, IIEE guidance | **Not yet researched** | — | Clearance values (so no clearance reference is configured) |
| Literature | Bonina "ENTANGLED"; Wang et al. 2024 | **Not found / unread** (ISEAS PDFs could not be extracted) | — | — |

No legal source establishes the triage rubric. It is documented as derived (D-38).

## 7. Provider research and verified coverage

Read from the providers' official pages on 19 Nov 2026. No number or URL was invented. Where a page could not be read,
the row stays unverified with no phone or URL.

| Provider | Category | Coverage basis | Contact recorded | Verification |
|---|---|---|---|---|
| Meralco | Electricity | Documented franchise area | (02) 16211; customercare@meralco.com.ph; contact and brownout-reporting pages | Verified (official site) |
| Converge ICT | Fiber / broadband | National operator | +63 2 8667 0850; customer-support page | Verified (official site) |
| FIBERBLAZE (Dasmariñas "Dasca Cable Services, Inc.", Imus (formerly Meridian Cable TV, Inc.), Silang, GMA, Gen. Trias, Trece Martires, Tanza, Naic) | Cable TV | Branch office for the city | Branch numbers from fiberblaze.ph | Verified (official site) |
| Streamtech (formerly Planet Cable) | Fiber / broadband | Province listing (Cavite) | 0919-057-8888; support page | Verified (official site) |
| PLDT, Globe | Telecom | National operator | Unchanged S14 secondary-source entries | Secondary source (pages returned empty / TLS error) |
| DITO, Smart, SKY, Eastern Communications, Innove | Telecom / CATV | — | **none** | Unverified (DITO 404, SKY 403; hidden from the public) |

Seed upgrades apply only to rows still marked as system seed (`System (initial directory)` or `System (S14 seed)`),
and each is audited as `provider.verified`. Rows a person edited are never overwritten.

## 8. Provider database architecture

- `contacts` gains `category`, `registered_name`, `previous_names` (JSON), `email`, `reporting_url`, `source_type`,
  `coverage_basis` and `admin_notes`. New columns are added by `add_missing_columns`; nothing is dropped.
- New table `pole_attachments`: pole, contact, wire class, `record_type` (asset record, attachment agreement, utility
  confirmation, permit; CHECK), a non-empty `record_reference`, `verified_by` and `verified_at`. This is the **only** source of
  "Confirmed responsible provider" (`POST /api/admin/poles/{code}/attachments`, audited as `attachment.confirmed`).
- Public views omit `source_note` and `admin_notes` (tested). `reporting_url` must be https.

## 9. Location matching implementation

- `contacts.providers(s, barangay, city, wire_classes, pole_id)` resolves the place first. City aliases include
  "Dasmariñas", "Dasmarinas City" and "City of Dasmariñas"; barangay forms include "Anabu I-A". The province is Cavite.
- **Relations**, in order of strength:
  - `confirmed_responsible`: an attachment record;
  - `serves_area`: a documented area or branch office for the city;
  - `province_listed`: the provider lists the province;
  - `nationwide`: a national operator, with "local availability not verified".
- **Relevance by conductor.** Power lines make electricity relevant; telecom / fibre makes telecom, broadband and CATV
  relevant. When a wire's type is uncertain, every category is shown with that reason stated.
- An unresolved location says so and shows only nationwide providers with the gap text. It never guesses.
- Verified end to end on the isolated server: an inspection uploaded with city "Dasmariñas", barangay "Salitran I"
  resolved to *Brgy. Salitran I, City of Dasmariñas, Cavite*. Meralco was listed as relevant (only power lines detected);
  FIBERBLAZE Dasmariñas and Streamtech appeared under "View all"; Converge, Globe and PLDT appeared as nationwide.

## 10. Citizen results redesign

- One shared component, `result-view.js` / `result-view.css`, is used by the Field Screener (`mode: "citizen"`), the
  inspection sheet (`"admin"`) and quick review (`"compact"`).
- **Removed from the citizen page:** the redundant diagnosis section, the staff-review rows, and the S17 card / bar
  helpers (49 lines of dead code).
- **Kept:** report PDF (with the wire selection), Copy message, Share link, New photo, Retake, the guard banner, the
  help assistant, the folded methodology, the technical view for signed-in staff, and the Important Notice once at the foot.
- **Photo tips** ("Want a more complete result?"), at most two, come only from measured conditions: dark, blur, glare,
  small, cut at the top / side / bottom, vegetation.
- **Emergency line:** one 911 instruction (tested: one `tel:911` and one `rv-emerg` on the page).
- Phone numbers: the Call button now parses "(02) 16211" (area code kept) and "171 (PLDT/Smart); …" (label dropped).

## 11. Evidence viewer and interactions

- Six layers: Original, Pole detection, Segmentation mask (each traced wire in its own colour, golden-angle HSL),
  Drawn wires, Problem wires (problem wires plus the selected one), Combined. Unavailable layers are disabled with a reason.
- The view opens on Problem wires when there are any, otherwise Combined.
- Zoom buttons, the scroll wheel, drag, pinch and Reset view.
- Hover sync between the photo, the wire list and the problem cards (`viewer.highlight`). Selection sync, where a second
  click clears. Arrow keys move through the list; Escape clears.
- Selecting a problem card in A selects the wire and scrolls to C. The detail panel shows the meter and the plain sentence.

## 12. Admin redesign

- **Inspection sheet:**
  - header with PDF, findings CSV and the more menu;
  - the shared view, with the **Administrative review** panel as section B: status chip, reviewers, options, rationale,
    follow-up, observations, critical hazards, finalize / amend / reopen, history;
  - "All wire measurements" table (folded; selecting a row selects the wire);
  - inspection details (file name, pole, barangay and assign, coordinates to 6 decimals, source, IDs) and the pole's history;
  - technical details and diagnostics, both kept.
  - The Assigned control was removed from the overview.
- **Quick review drawer:** the compact view plus the same decision panel, at 640 px wide. J / K / Space / Esc are kept.
- **Inspections:**
  - review tabs and separate screening chips, with server counts computed after the other filters, so they reconcile;
  - columns: file, location, date, detected, problem, risk, review, admin result, action;
  - sorts for each of these;
  - search by file name, inspection or report ID, pole, place and coordinates (`records.matches`).
  - The old status / risk / review filters remain as removable chips. The assignee filter and bulk actions are kept.
- **Upload:** the admin batch upload now uses the Field Screener's rules (`upload-rules.js`): HEIC by extension even
  with an empty MIME type, the 64 MB limit (the server's `max_upload_mb`), plus city, barangay and inspection name.
  The same server endpoint validates both.
- **Directory:**
  - category filter;
  - registered and previous names, coverage basis, source type, report page;
  - staff-only internal notes;
  - the page text states that coverage is not ownership.
- **Exports:**
  - The CSV appends file name, wires detected, problem wires, triage (operational rubric), rubric version, review status,
    administrative decision and decided by. Existing columns are unchanged, and it honours the review-status and screening filters.
  - The PDF states "Problem wires: n of N detected. Inspection triage: … not a statutory classification."
- **Assistant:** the knowledge base sections for review, history, triage, dashboard, inspections and the evidence viewer
  were rewritten. The system prompt uses the S18 words, and the live pole facts include review status, triage and file name.

## 13. Dashboard and activity changes

- "Inspections by pole" shows each pole's latest inspection. Default tab: Not yet reviewed. Screening chips: High,
  Moderate, Normal, Processing issue, Missing location.
- Columns: inspection (file name, label and code), location, wires, **risk ("Moderate · 3 / 12 problem wires")**,
  review (with the decision label), received, action. **No Assigned and no Reason column.**
- **Activity** (`audit.activity`) lists real events only: audit rows plus inspection received / processed events
  derived from the analyses (errors are already audited).
  - Automated events have a muted icon; people's actions carry their name.
  - Filters: All / People / Automated.
  - Consecutive identical untargeted events collapse ("×6").
  - Reading the feed writes nothing (tested).
- Review progress bars for the three statuses. The fleet line is kept.

## 14. Map changes

- Markers, clusters, animations, colours, hover and list↔map sync are unchanged (the approved visual direction).
- List item: **original file name / location label / exact coordinates** (6 decimals), plus "n detected · m problem".
  The placeholder "Coordinates only" is no longer shown as a label.
- Preview: file name, location label, pole code, coordinates, detected, problem, risk chip, review chip; directions and Street View kept.
- Search covers file name, inspection or report ID, pole, place, inspection name and coordinates, in the list and in the Find box.

## 15. Database migrations

- New tables only:
  - `review_progress`: PK is a cascading FK to the analysis; CHECKs on status and source; complete ⇒ `completed_at`;
  - `admin_decisions`: append-only, unique `legacy_review_id`;
  - `pole_attachments`: CHECK on `record_type`, non-empty reference.
- New `contacts` columns are added by `add_missing_columns` with defaults.
- `database.migrate()` counts pending legacy reviews, **takes a backup first**, then runs the idempotent
  `backfill_legacy`. On the isolated copy, 2 legacy reviews became 2 `REVIEW_COMPLETE` / `legacy_review` rows. Rerunning
  adds nothing. No existing row was modified.
- Seed upgrades touch only unedited system rows and are audited.
- Model weights were not touched.

## 16. API changes

| Method | Path | Notes |
|---|---|---|
| GET | `/api/admin/inspections/{id}/review` | view, history, options, rubric, `can_decide` |
| POST | `/api/admin/inspections/{id}/review/start` | idempotent; `recorded` tells whether anything was written |
| POST | `/api/admin/inspections/{id}/decision` | 201; 409 while processing; 422 on validation |
| POST | `/api/admin/inspections/{id}/review/reopen` | reason required |
| GET / PUT | `/api/admin/settings/risk-rubric` | PUT: system administrators only |
| GET / PUT | `/api/admin/settings/decision-options` | PUT: system administrators only; retire, never delete |
| POST | `/api/admin/poles/{code}/attachments` | the only route to "confirmed responsible" |
| GET | `/api/admin/inspections` | adds `review_status`, `screen` and new sorts; returns `review_tabs` and `screen_tabs`; rows add `filename`, `machine_state`, `detected`, `problem_count`, `risk_level`, `review_status(_label)`, `decision` |
| GET | `/api/admin/operations` | adds `poles`, `feed`, `review_counts` |
| GET | `/api/dashboard/priority` | map rows add the S18 fields and `place_label` |
| GET | `/api/analysis/{id}/result` | adds `screening`, `timeline`; wire findings add `meter_level(_label)` |
| GET | `/api/analysis/{id}/contacts` | adds `providers` (groups, nationwide, place, relations, ownership note) |
| GET | `/api/admin/exports/inspections.csv` | S18 columns appended; `review_status` and `screen` filters |

Both bulk "mark reviewed" and the legacy review route now also write an `admin_decisions` row.

## 17. Test results

- `pytest`: **1075 passed, 2 xfailed**. The full suite was 1034 before S18.
- `ruff check app tests scripts`: clean.
- New tests (`tests/test_s18_workflow.py`, 40). They cover:
  - transitions, refresh idempotency, amend / reopen history and audit;
  - validation and multiple reviewers;
  - failed inspections being decidable;
  - tab reconciliation, legacy mirroring and the migration backfill;
  - rubric boundaries, the critical override and low-clearance rules;
  - problem-wire rules, rubric versioning and options retirement;
  - result and row consistency, the read-only feed and photo tips;
  - provider aliases, categories and unresolved places, attachment-only ownership, no internal notes in public responses;
  - DB CHECK constraints, append-only decisions, bulk-review mirroring, search by file name / ID / coordinates;
  - CSV columns and filters, dashboard count reconciliation;
  - one shared result view across three surfaces;
  - no Assigned / Reason column, and no rubric numbers in page scripts.
- Updated tests, each pointed at the S18 equivalent with its intent kept and its assertions no weaker:
  - `test_s12_results_ui` reading order: A → B → C, one emergency line, no staff review text;
  - `test_s14_ux` meter: the shared meter, server band, no threshold literals;
  - `test_s16_heic`: one rule set used by both upload paths;
  - `test_s17_consistency`: server risk and review chips;
  - `test_s6_recovery`: type colours and the server band, no severity legend;
  - `test_s14_operations`: "DITO not in names" replaces "Converge not in names", because Converge is now verified.

## 18. Browser and responsive validation

All on the isolated server at port 8014, using headless Chrome.

- `scripts/e2e_check.py`, ported to S18 (selectors and assertions for the new structure; the PDF problem-wire and triage
  line added): **96 PASS, 0 FAIL, 0 page errors**. It checks:
  - widths 1280 / 1024 / 820 / 390 / 360;
  - dark theme;
  - the PDF section order and the assistant;
  - dashboard, Inspections, the sheet, diagnostics, the technical view and the map.
- S18 citizen check (exceeded, problems, clean and failed samples × 1440 and 390): **58 / 58**. It checks:
  - A, B and C present, the notice once;
  - no staff text, no "Incomplete assessment" headline;
  - no horizontal scroll, no console errors.
- S18 admin check (1440 and 390): **29 / 29**. It checks:
  - no Assigned / Reason column;
  - tab counts equal matched rows;
  - filename search;
  - quick review panel present;
  - opening starts the review, finalizing completes it;
  - a failed inspection is reviewable;
  - no horizontal scroll, no console errors.
- Design detector (`impeccable detect`): the side-stripe flags on the new 911 line and the decision history were fixed.
  The dead `.sheet-ov` rules were removed. **0 findings** on the changed CSS / JS. That detector run used regex only, so it
  is an undercount, not a clean bill.

## 19. Before-and-after screenshots

`docs/thesis/figures/s18/`. The "before" set is the S17 frontend from `refs/recovery/s18-start`, served over the same
isolated data. Each is captured at desktop 1440 and phone 390.

| Page | Before (S17) | After (S18) |
|---|---|---|
| Citizen result | `before_s17_citizen_*.png` | `after_s18_citizen_*.png` |
| Dashboard | `before_s17_dashboard_*.png` | `after_s18_dashboard_*.png` |
| Inspections | `before_s17_inspections_*.png` | `after_s18_inspections_*.png` |
| Inspection sheet | `before_s17_sheet_*.png` | `after_s18_sheet_*.png` |
| Map | `before_s17_map_*.png` | `after_s18_map_*.png` |

## 20. Known limitations

- **Clearance.** No documented clearance reference is configured. RA 11361 defers to the PEC, which was not researched,
  so a measured low-clearance hazard cannot yet be recorded. This is by design until a source is added.
- **Problem-wire rules are image-based.** "Possible loose end" and "apparently abandoned" are screening indicators from
  one photo, not field facts. They are labelled that way.
- **Map colours.** Marker colours and the map status chips still use the S17 operational status vocabulary (Needs review,
  Incomplete), kept under the "preserve the approved visual direction" constraint. Review status and triage appear in the
  list and the preview.
- **Unread provider pages.** PLDT, Globe, Smart, DITO and SKY pages could not be read, so those rows stay secondary or
  unverified. No `pole_attachments` record exists yet, so no provider is shown as confirmed responsible anywhere.
- **Partial seed coverage.** Location matching covers the seeded Cavite cities and their aliases. Other places resolve
  as "not matched" and show nationwide providers only.
- **Test data in the isolated copy.** The isolated database now holds S18 QA activity: reviews started, one finalized
  decision, an uploaded Dasmariñas check, and e2e uploads marked as test records.

## 21. Outstanding work

1. Research the PEC clearance tables (and NESC / IIEE guidance), then add documented `clearance_references` to the rubric.
2. Verify the HB 584 title and status, and the Davao ordinance texts, from primary sources.
3. Record pole-attachment agreements as utilities supply them, to enable "confirmed responsible".
4. Decide with the owner whether the map's marker colours move from operational status to triage.
5. A settings UI for the rubric and the decision options. Today they are API-only and limited to system administrators.
6. A full-page design scan against the running server (`impeccable detect http://…`) for contrast and tiny-text rules.
7. Commit S16–S18 when the owner approves. Nothing is committed yet.

## 22. Exact Git status

Captured after all S18 edits, in `/Users/joannacanila/WIREFRAME-s13`. Nothing is staged or committed on any branch. The finished
tree is also saved as `refs/recovery/s18-end` (`dfb4aeba`), a snapshot ref built from a temporary index, like `s18-start`.
All S16, S17 and S18 changes are uncommitted working-tree changes on `recovery/s13-master`.

```text
$ git rev-parse --abbrev-ref HEAD && git rev-parse --short HEAD
recovery/s13-master
02bd470
$ git show-ref refs/recovery/s18-start
19a792a9eb45c6ed2b7014cb484028c2da469d70 refs/recovery/s18-start
$ git status --short
 M app/api/admin.py
 M app/api/analysis.py
 M app/api/assistant.py
 M app/api/reports.py
 M app/assistant/intent.py
 M app/assistant/kb.py
 M app/assistant/knowledge.md
 M app/assistant/live.py
 M app/assistant/service.py
 M app/core/config.py
 M app/core/database.py
 M app/core/notice.py
 M app/core/state.py
 M app/main.py
 M app/models/orm.py
 M app/pipeline/acquisition.py
 M app/pipeline/catenary.py
 M app/pipeline/hazard.py
 M app/pipeline/runner.py
 M app/pipeline/skeleton.py
 M app/pipeline/strand_head.py
 M app/pipeline/wirenet.py
 M app/services/analysis.py
 M app/services/audit.py
 M app/services/comparison.py
 M app/services/diagnostics.py
 M app/services/methodology.py
 M app/services/plain.py
 M app/services/queue.py
 M app/services/reasons.py
 M app/services/records.py
 M app/services/report_pdf.py
 M app/services/route_pdf.py
 M app/services/sites.py
 M app/web/admin.html
 M app/web/assets/admin-diagnostics.js
 M app/web/assets/admin-map.js
 M app/web/assets/admin.css
 M app/web/assets/admin.js
 M app/web/assets/assistant.js
 M app/web/assets/field.css
 M app/web/assets/field.js
 M app/web/assets/findings.js
 M app/web/assets/landing.css
 M app/web/assets/notice.js
 M app/web/assets/styles.css
 M app/web/assets/viewer.js
 M app/web/field.html
 M app/web/landing.html
 M config/wireframe.yaml
 M docs/handoff/SESSION_LOG.md
 M docs/thesis/IMPLEMENTATION_CHANGELOG.md
 M docs/thesis/RESEARCH_AND_DECISION_REGISTER.md
 M docs/thesis/WIREFRAME_THESIS_REFERENCE.md
 M requirements.txt
 M scripts/e2e_check.py
 M tests/conftest.py
 M tests/test_api.py
 M tests/test_d27_reference_scope.py
 M tests/test_s12_results_ui.py
 M tests/test_s5_diagnostics.py
 M tests/test_s6_recovery.py
 M tests/test_s8_sag_reference.py
 M tests/test_v211_field_ui.py
 M tests/test_v219_portal.py
 M tests/test_v221.py
?? .impeccable/
?? DESIGN.md
?? PRODUCT.md
?? app/api/operations.py
?? app/api/review.py
?? app/assistant/citizen.md
?? app/assistant/citizen.py
?? app/pipeline/continuation.py
?? app/pipeline/span_fit.py
?? app/services/contacts.py
?? app/services/ops.py
?? app/services/outcome.py
?? app/services/review_flow.py
?? app/services/screening.py
?? app/web/assets/admin-review.js
?? app/web/assets/fonts.css
?? app/web/assets/fonts/
?? app/web/assets/result-view.css
?? app/web/assets/result-view.js
?? app/web/assets/upload-rules.js
?? datasets
?? docs/thesis/DISPATCH_DESIGN.md
?? docs/thesis/FIELD_LABELLING_GUIDE.md
?? docs/thesis/S16_FINAL_REPORT.md
?? docs/thesis/S16_SYSTEM_AUDIT.md
?? docs/thesis/S17_FINAL_REPORT.md
?? docs/thesis/S18_FINAL_REPORT.md
?? docs/thesis/figures/s17/
?? docs/thesis/figures/s18/
?? docs/thesis/results/s13_repeatability.json
?? docs/thesis/results/s15_aspect_clamp_field_test.json
?? docs/thesis/results/s15_aspect_letterbox_field_test.json
?? docs/thesis/results/s15_aspect_shipped_field_test.json
?? docs/thesis/results/s15_aspect_shipped_square_test.json
?? docs/thesis/results/s15_measure_catenary_field_test.json
?? docs/thesis/results/s15_measure_span_field_test.json
?? docs/thesis/results/s15_model_metrics_test.json
?? docs/thesis/results/s16_conditions.json
?? docs/thesis/results/s16_continuation.json
?? docs/thesis/results/s16_uncertainty_calibration.json
?? docs/thesis/results/s16_uncertainty_residual.json
?? docs/thesis/results/s17_hardcase_pack.json
?? docs/thesis/results/s17_seg_baseline.json
?? docs/thesis/results/s17_seg_cluster12.json
?? docs/thesis/results/s17_seg_compare_baseline_vs_cluster12.json
?? docs/thesis/results/s17_seg_compare_baseline_vs_turn25.json
?? docs/thesis/results/s17_seg_compare_baseline_vs_xjunction.json
?? docs/thesis/results/s17_seg_compare_baseline_vs_xjunction_turn25.json
?? docs/thesis/results/s17_seg_turn25.json
?? docs/thesis/results/s17_seg_xjunction.json
?? docs/thesis/results/s17_seg_xjunction_turn25.json
?? ml/experiments/s15_aspect.py
?? ml/experiments/s15_measure.py
?? ml/experiments/s16_conditions.py
?? ml/experiments/s16_continuation.py
?? ml/experiments/s16_uncertainty.py
?? ml/experiments/s17_hardcase_pack.py
?? ml/experiments/s17_seg_eval.py
?? ml/experiments/s17_seg_figure.py
?? ml/field_dataset.py
?? models/hf
?? models/strand_head_v217.bounds.json
?? scripts/repeatability.py
?? tests/fixtures/
?? tests/test_s13_stability.py
?? tests/test_s14_citizen.py
?? tests/test_s14_lifecycle.py
?? tests/test_s14_operations.py
?? tests/test_s14_ux.py
?? tests/test_s15_model.py
?? tests/test_s16_admin.py
?? tests/test_s16_heic.py
?? tests/test_s16_integrity.py
?? tests/test_s17_consistency.py
?? tests/test_s17_heic_preview.py
?? tests/test_s17_reference.py
?? tests/test_s17_segmentation.py
?? tests/test_s18_workflow.py
$ git diff --stat | tail -1
 66 files changed, 4739 insertions(+), 1617 deletions(-)
```
