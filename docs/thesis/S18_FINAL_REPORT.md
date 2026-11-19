# S18 Final Report: Admin Inspection Refinement, Wire-Level Navigation, Directory Scope, Pole Detection, Consistency

Session S18 (refinement pass), 19 Nov 2026. Working tree `/Users/joannacanila/WIREFRAME-s13`, branch
`recovery/s13-master`, HEAD `02bd470` (unchanged). Register entry: **D-39**. The first S18 pass (evidence-first
screening, review workflow, provider directory, D-38) is reported in `S18A_EVIDENCE_FIRST_REPORT.md`; that file is the
previous content of this report, preserved unchanged.

Nothing was committed, pushed, merged or deployed. All runtime checks ran against the isolated copy of the data
(`scratchpad/iso14/var`); the live database (`/Users/joannacanila/WIREFRAME/var`) was never opened. No model weights
were replaced. Recovery snapshot of this working tree: `refs/recovery/s18b-refinement` (`8dba10f`), created with a
temporary index; the branch, HEAD and the real index were not touched.

---

## 1. Executive summary

| Area | Status | Evidence |
|---|---|---|
| Admin inspection page cleanup (diagnostics, envelope, geometry, "not available" removed; VNI / CRS under "Other" with explanations) | **Complete** | browser 70/70, `test_inspection_page_sections`, `test_other_metrics_come_from_the_configuration` |
| Clickable wire references, photo wire search, list wire search, one synchronized selection, pan/zoom to the wire | **Complete** | browser checks (search, list, references, layers, resize), Node tests for `findWire` / `wireRefs` |
| Segmentation mask legend from the model's classes | **Complete** | `test_mask_legend_lists_only_classes_present`, browser legend checks |
| "Other documented decision" requires written text | **Complete** | 4 parametrized rejection tests, audit test, browser form check |
| Barangay assign / clear (×) / dismissible picker | **Complete** (a trapping bug in the picker was found and fixed) | `test_barangay_can_be_cleared_…`, browser Cancel / × / Escape checks |
| "Mark as test record" removed; separate Delete with confirmation as an audited, restorable archive | **Complete** | `test_delete_inspection_archives_…`, `test_inspector_cannot_archive`, browser archive / restore |
| Dashboard activity from real events only | **Complete** (already real; archive / restore / barangay-clear events added) | `audit.activity` test inside the archive test |
| Inspection table: asc → desc → clear cycle, explicit filter controls, tooltips | **Complete** | browser sort cycle and risk filter checks, source test |
| Pole naming convention audited and documented | **Complete** (documented; one inconsistency fixed: the PDF said "SP-003") | sections 10–11 |
| Admin upload "Use my current location" (shared with the Field Screener) | **Complete** | browser 10/10 (geolocation granted, lookup stubbed), API test |
| Citizen result: "How this result was produced" and barangay-hall line removed | **Complete** (the data stays in the API and the staff technical view) | e2e, browser, source tests |
| Loading facts: random, non-repeating, sourced | **Complete** | Node shuffle test (40 sessions), source test |
| Directory: Cavite corridor, verified info only, five columns, no warning / ownership paragraph, update label | **Complete**; **study-area list unconfirmed** (no sampling list exists in the thesis; the owner chose to keep the current list) | browser directory checks, `test_directory_*` |
| Thresholds per wire category, centralized, always visualized | **Complete** (no new threshold invented) | section 18, e2e telecom/power check |
| Pole detection improvement | **Blocked: needs field labels.** Baseline measured; no inference-side change improved it; checkpoint unchanged | section 17 |

Test totals on the final tree: **pytest 1102 passed, 2 xfailed** (25 new tests in `tests/test_s18_refinement.py`),
**ruff clean**, **e2e 103/103 PASS, 0 page errors**, **S18 refinement browser checks 70/70**, **upload-location browser
checks 10/10**, all 16 frontend modules pass `node --check`, SQLite `integrity_check` ok.

## 2. Files and components changed

Changed in this refinement pass (the rest of `git status` is the first S18 pass and S13–S17, all uncommitted):

| Layer | File | Change |
|---|---|---|
| Data model | `app/models/orm.py` | `analyses.archived_at`, `archived_by`, `archived_reason` |
| Records | `app/services/records.py` | `listed()`: archived inspections are left out of every list, count, map and export; `is_operational` false when archived |
| Audit | `app/services/audit.py` | event text / kind for `inspection.archived`, `inspection.restored`, `barangay.unset`, `inspection.retried` |
| Admin API | `app/api/admin.py` | `POST …/archive`, `POST …/restore`, `DELETE /api/admin/poles/{code}/barangay`; context returns `archived` and `can.delete / restore` |
| Review | `app/services/review_flow.py`, `app/api/review.py` | `other_text` required and validated for "Other" |
| Upload | `app/api/analysis.py`, `app/services/analysis.py` | location capture fields (`location_method`, accuracy, address lookup) stored apart from typed text |
| Result payload | `app/services/metrics.py` (new), `app/services/analysis.py`, `app/services/outcome.py` | `metrics` (VNI / CRS with explanations, staff only), `mask_legend` (public), `wire_id` on every wire |
| Wording | `app/services/diagnostics.py`, `app/services/plain.py` | observations name their wires ("wires 3, 5 and 7"); PDF says "Wire 3" |
| Directory | `app/services/contacts.py`, `app/api/operations.py` | `STUDY_AREA` (unconfirmed), `DIRECTORY_UPDATED`, `area_label()` that never widens a source |
| Viewer | `app/web/assets/viewer.js` | `focus()` zooms to the wire (fit … 4× fit), keeps it framed on resize |
| Shared result view | `app/web/assets/result-view.js`, `result-view.css` | photo wire search, list search, wire-reference links, legend, info tips, metric meters, "Other" section |
| Admin | `app/web/assets/admin.js`, `admin-review.js`, `admin.css`, `admin.html` | removed diagnostics and three-dot menu; delete dialog / restore; barangay chip with ×; sort cycle and column filters; five-column directory; upload location |
| Removed | `app/web/assets/admin-diagnostics.js` | unused after the diagnostics block was removed (kept in the recovery history and HEAD); its dead CSS removed |
| Citizen | `app/web/assets/field.js`, `field.css`, `field.html`, `facts.js` (new), `location.js` (new) | methodology block and barangay-hall line removed; sourced fact pool; shared location module |
| Pole evaluation | `app/pipeline/poles.py`, `ml/poles/eval_detectors.py` | `augment` / `imgsz` options and COCO mAP50-95 in the evaluator (inference default unchanged) |
| Tests | `tests/test_s18_refinement.py` (new), `tests/test_v221.py`, `tests/test_s12_results_ui.py`, `scripts/e2e_check.py` | see section 20 |

## 3. Admin inspection page changes

Removed: the Technical Diagnosis dropdown (metric cards), the Acquisition envelope, Geometry, "Not available from one
photo", and the three-dot menu. Nothing was deleted from the data: the envelope, geometry and diagnostic values remain
in the result payload, the database and the technical exports.

Kept, in this order, inside "Technical details": **Photo limitations**, **Observed in the image**, **Recommended
steps**, then **Other**.

**Other** shows two meters, each with an ⓘ button that opens a panel with six answers: what it represents, its
inputs, how it is derived, what it means, its threshold / reference, and what it cannot establish. The panel opens on
click or keyboard, closes on Escape, an outside click or a second click, and Escape also hides a panel shown only by
hover (WCAG 1.4.13; this was found failing in the browser check and fixed).

| Metric | Scale | Bands / marks drawn | Source |
|---|---|---|---|
| Visual Noise Index (VNI) | 0–1 | Low ≤ 0.25, Moderate ≤ 0.50, High ≤ 0.75, Severe ≤ 1.0, labelled **provisional** | `config/wireframe.yaml` `vni.bands` (thesis Table 3.7) |
| Catenary Risk Score index (CRS) | 0–10, highest wire named and clickable | marks at 5.0 (= 2 % sag) and 7.5 (= 3 % reference); CRS = min(10, 250 × δ/L), power lines only | derived from the power comparison reference; it never decides a level |

Screenshot: `figures/s18/s18r_other_metrics.png`.

## 4. Wire navigation and search implementation

Every wire has one key, its **wire number** (`detections.wire_index`), and one display ID derived from it (section
11). All selection goes through `viewer.select(index)`, so the photo, the wire list, the detail panel and every
reference show the same wire.

- **Wire references are links.** Any "wire 3" / "wires 2, 5 and 7" in Photo limitations, Observed in the image, the
  findings and the CRS reading is a button (`wireRefs`, `SECTION` in `findings.js`). It links only wires that exist in
  this result. Clicking one selects the wire, switches away from the plain-photo layer if needed, frames it, and
  scrolls the photo into view.
- **Photo search** (box above the photo). It accepts `3`, `03`, `#3`, `w3`, `Wire 3`, the full wire ID
  (`WF-415C41C6-W03`) or a wire type ("telecom"). One match selects and frames the wire. Several matches list the
  choices as links and select nothing; the search never guesses. No match keeps the current selection and says so
  ("no wire 999").
- **List search** (above the wire list). It filters the list as you type; Enter selects when exactly one wire is left;
  "No wire matches" is shown when nothing does.
- **Synchronization.** Selecting in the list, the photo, a reference or a search marks the same row, the same
  reference and the same outline. Changing layers keeps the selection. **Reset** clears it and refits.
- **Pan / zoom.** `viewer.focus()` frames the wire's box at 80 % of the view, between the fitted scale and 4× the
  fitted scale; the 4× cap was lowered from 6× after screenshots showed short wires losing all context. A resize
  re-frames the selected wire, verified in the browser: it stays centred after the viewport changes.

## 5. Segmentation mask legend

`metrics.mask_legend(result)` lists **only the classes present in this photo**, in the segmentation model's class
order (`app/core/config.CLASSES`), each with the colour the viewer actually draws (`viewer.classVar`) and a one-line
meaning. "Uncertain" is added only as a display state. The legend shows on the Segmentation mask and Combined layers
and hides on layers without a mask. The legend is public, so citizens and staff see the same one.
Screenshot: `figures/s18/s18r_mask_legend.png`.

## 6. Administrative review validation

Choosing **Other documented decision** shows a required field, "Describe the documented decision" (up to 110
characters). The server, which owns this rule, rejects text that is empty, whitespace only, shorter than 5 characters
after whitespace is normalised, or has no letter or digit. The browser checks the same rule first, so the message
appears without a round trip. A saved decision is labelled `Other: <text>`, and the `review.finalized` audit row
records that label. Tests: 4 rejection cases plus the saved-and-audited case; browser: a whitespace-only description is
refused with a message and the review stays open.

## 7. Deletion workflow

"Mark as Test Record" and the three-dot menu are gone. Each inspection now has its own **Delete inspection** block. It
opens a dialog that names the pole, the inspection reference and the photo, explains the consequence, and needs a
reason (at least 3 characters).

Delete is an **audited soft delete (archive)**:

- The inspection disappears from the dashboard, the inspection list, the map, counts and exports. This applies even
  when test records are included.
- The photo, findings, decisions and audit history stay, and the inspection's own page still opens, with a "Deleted
  inspection" banner and a **Restore** button.
- `inspection.archived` and `inspection.restored` are audit events, with the actor, the time and the reason.
- Admin only: an inspector gets 403. Archiving twice gets 409.
- Review and further deletion are disabled while an inspection is archived.

The older hard delete (`DELETE /api/analysis/{id}`) still exists in the API for completeness, but no screen offers it.
No historical record was deleted in this session.

## 8. Dashboard activity data source and event implementation

The activity feed was already built from real data, and that was confirmed. `audit.activity()` merges:

1. **Audit rows** (`audit_events`): every human or system action, such as a review started, a decision, a barangay
   set or cleared, an archive or restore, or a retry. Each row carries its actor and is marked automated or human.
2. **Inspection timestamps**: "new inspection received" (`created_at`, actor Field Screener or Admin upload) and
   "image processing completed" (`completed_at`).

Archived inspections are left out of the timestamp events. Nothing is synthesized or sampled. This pass added the
`inspection.archived`, `inspection.restored` and `barangay.unset` texts and kinds; the test checks that an archive
appears as a human "archive" event by the signed-in reviewer.

## 9. Inspection table sorting and filtering logic

**In plain language.** Each column header has two separate controls.

- **The column name with an arrow sorts.**
  - Click once: ↑, the first direction.
  - Click again: ↓, the opposite direction.
  - Click a third time: the column goes back to ↕ and the table returns to its default order.

  Hovering the header always says what the next click will do, for example "Sort: Highest risk first", "Sort: Lowest
  risk first", "Clear sorting (default order)". Sorting never hides a row; it only reorders.
- **The funnel icon (Risk and Review columns only) filters.** It opens a short list, for example "All risk levels /
  High / Moderate / Normal". Choosing one hides every row that does not match, and the count above the table changes
  ("2 of 80"). The funnel is highlighted while a filter is active. "Clear filters" resets all of them.

**What ↑ means per column.** The arrow means "first direction" as written in the tooltip, not "smallest number":

| Column | ↑ (first click) | ↓ (second click) |
|---|---|---|
| File, Location | A to Z | Z to A |
| Date | Oldest first | Newest first |
| Detected, Problem | Fewest first | Most first |
| Risk | **Highest risk first**; ties broken by more problem wires | Lowest risk first |
| Review | Not yet reviewed first | Review complete first |

**Default order** (no column sorted): triage. Failed or still-processing inspections come first, then flagged ones,
then the rest by risk, newest first within each group. The dashboard's table defaults to Risk ↑.

Sorting and filtering are done by the server (`/api/admin/inspections?sort=…&desc=…&screen=…&review_status=…`), so
paging, counts and exports agree with what is on screen. The browser check confirmed the cycle reads
`ascending → descending → none` on the header, and that the High filter returned only High rows.

## 10. Canonical pole naming convention

A pole has two names, and they do different jobs.

- **Pole code, for example `P-9743F7`.** The permanent identifier. It is fixed when the pole is first recorded and
  never changes. Database links, page addresses, exports and audit rows use it.
  - It is generated by `analysis.normalise_pole_code`: `P-` followed by six hexadecimal characters.
  - In a batch upload with "Use each file name as the pole ID" ticked, the file name (without its extension) becomes
    the code instead.
  - Because it never changes, a link or a printed report made today still finds the same pole next year.
- **Pole label, for example `SAL-001`.** The human-friendly name shown on screens. It is made of a three-letter prefix
  for the barangay (`SAL` for Salitran) and a running number within that prefix.
  - Poles that have a location but no barangay use the prefix `LOC-`.
  - Poles without a location use `UNP-` (unpinned).
  - The label changes only when the prefix changes, for example when a barangay is assigned to a `LOC-` pole. That is
    why nothing stores or links by the label.

Both names are shown together wherever a pole is identified: the label first, the code beside it in grey.

## 11. Full naming-convention reference table and rationale

| Thing | Format | Example | Who / what creates it | Changes? | Why this format |
|---|---|---|---|---|---|
| Pole code | `P-` + 6 hex, or the file stem in a batch | `P-9743F7` | `analysis.normalise_pole_code` | Never | Stable key for links, exports, audit |
| Pole label | barangay prefix + number | `SAL-001`, `LOC-004`, `UNP-091` | `app/services/labels.py` | Only if the prefix changes | Readable on a map or in a field conversation |
| Inspection (DB key) | UUID | `415c41c6…` | database | Never | Unique without coordination |
| Inspection reference | `WF-` + first 8 hex of the UUID, upper case | `WF-415C41C6` | `outcome.report_id` | Never | Short enough to read aloud or print on a PDF, traceable to the record |
| Wire number | integer from 1 | `Wire 3` | `detections.wire_index` (pipeline order) | Never within an inspection | What people say and type in the search |
| Wire ID | inspection reference + `-W` + 2 digits | `WF-415C41C6-W03` | `outcome.wire_id` | Never | Globally unique: names one wire in one inspection in exports, messages and searches |

Inconsistency fixed: the PDF used to call wire 3 "SP-003" (span). It now says "Wire 3", like every screen.

## 12. Admin upload location functionality

The batch upload form now has **Use my current location**. It uses the same module as the Field Screener
(`assets/location.js`: `locate`, `reverseLookup`, `fixLabel`, `appendLocation`), so the two cannot drift apart.

1. The browser asks for permission only when the button is pressed. It tries a precise fix first, then a quicker
   network fix.
   - If permission is denied, the request times out or the page is not secure, the message says exactly that.
   - No other location is ever substituted.
2. The position is shown with its accuracy, for example "14.32941, 120.93672 (±14 m)".
3. The address lookup (OpenStreetMap Nominatim) fills **only empty** City / Barangay boxes. Typed text is never
   overwritten, and both boxes stay editable. If the lookup fails, the coordinates are still kept.
4. **Remove location** discards it before upload.
5. The server stores how the position was captured (`metadata_json.location_capture`: method `device`, accuracy, and
   the lookup's city / barangay) apart from the typed text. An unknown method is rejected (422).

Bug fixed: device GPS used to appear on the inspection as "pinned by an administrator". The Location row now reads
"(device location, ±14 m) · address lookup: Salitran I, Dasmariñas", or "photo GPS", or "entered or pinned".

Browser check (Chrome, geolocation granted, lookup stubbed so no coordinates left the machine): 10/10, including the
uploaded request's fields and the stored record. Screenshot: `figures/s18/s18r_upload_location.png`.

## 13. Citizen result changes

- **Removed** "How this result was produced". The 11 methodology topics remain in the API (`methodology`) and in the
  staff "Technical view" link, so no data was lost. The e2e check asserts the section is absent while the API still
  carries all 11 topics.
- **Removed** the "Your barangay hall" recommendation.
- If the provider list fails to load, the page now says "The provider list could not be loaded. In an emergency
  call 911."
- **Added** the same photo wire search and mask legend as staff. The test checks that the citizen and admin views
  show the same wire IDs, meter levels and problem counts.
- Kept: the ownership caveat on the citizen contacts ("Area coverage shows which companies operate here, not who owns
  a particular wire"). The brief removed that paragraph from the admin Directory only.

**Follow-up (owner request, 19 Nov 2026): Field Screener technical view removed.** The staff-only "Technical view"
button and its page (`?view=technical`, layers / opacity / wire tabs / span detail / methodology) are gone. Everyone,
signed in or not, now gets the same result page, and the old address shows that page. Staff engineering details are on
the admin inspection page.

- `field.js` went from 1361 to 676 lines; every helper used only by that page was removed.
- The 11 methodology topics stay in the API and in the PDF.
- 7 tests that exercised only the removed page's functions were deleted (5 in `test_s12_results_ui.py`, 2 in
  `test_s7_sag_meter.py`). The shared meter that citizens still see keeps its own tests (`test_s14_ux.py`,
  `test_s8_sag_reference.py`).
- New assertions check that the technical view is absent (`test_s12`, `test_v211`, e2e).
- After the change: pytest 1095 passed, 2 xfailed; e2e 103/103, 0 page errors.
- Screenshot: `figures/s18/s18r_field_report_no_technical.png`.
- Some `rx-*` styles in `field.css` that only that page used remain; they are inert and can be pruned later.

## 14. Loading facts and their sources

`assets/facts.js` holds a controlled pool of 10 facts, each tied to a source. They contain no statistics, no claim
about the user's photo and no claim about the system's accuracy.

- Each processing session gets a fresh Fisher–Yates shuffle, so no fact repeats until all have been shown and there
  is no fixed loop.
- The id of the last fact shown is kept on the device only (`localStorage` `wf-fact-last`), so the next session
  doesn't open with it.
- The source of each fact is shown under it.

| Fact (short) | Source |
|---|---|
| A freely hanging cable forms a catenary | Irvine, H. M. (1981). *Cable Structures*. MIT Press |
| "Catenary" from Latin *catena*, chain | Merriam-Webster, "catenary", etymology |
| Sag depends on tension and weight per metre | Kampik et al. (2024). *Energies* 17(12), 2967 |
| ERB set utility-pole wire sag at ≤ 3 % of the span (1995) | ERB Resolution No. 95-21, s. 19, 3 Aug 1995 |
| No single sag limit for aerial fiber cable | ITU-T L.102 (11/2025), cl. 7.2.2.1, 7.3.10, 8.2.6 |
| Power-line sag measured from smartphone photos + accelerometer | Safdarinezhad et al. (2022). *Measurement* 199, 111493 |
| Wire pixels thinned to a centre line (1984 algorithm) | Zhang & Suen (1984). *CACM* 27(3), 236–239 |
| Low-contrast photos evened out with CLAHE | Zuiderveld (1994). *Graphics Gems IV* |
| Barangay names from the PSGC | Philippine Statistics Authority, PSGC |
| 911 is the national emergency hotline | Executive Order No. 56, s. 2018 |

Test: across 40 simulated sessions every session shows each fact exactly once, more than 30 orders are distinct, and no
session opens with the previous session's last fact.

## 15. Provider directory geographic scope and verified organizations

**Scope.** `contacts.STUDY_AREA` lists eight Cavite municipalities: City of Dasmariñas, City of Imus, Silang, Gen.
Mariano Alvarez, City of General Trias, City of Trece Martires, Tanza and Naic.

**This list is UNCONFIRMED.** The thesis has no barangay or municipality sampling list. The owner chose "Keep current
Cavite list" (19 Nov 2026), and the code comment and this report record it as unconfirmed.

**Display rules:**

- Exactly five columns: Organization, Category, Area, Phone, Website.
- The subtitle reads "Updated as of November 19, 2026".
- The warning banner and the ownership paragraph are removed.
- Phone and website appear **only for `verified` entries**. Unverified entries are hidden unless an administrator
  picks them in the verification filter.
- **Area never widens a source:**
  - specific cities are listed as published;
  - a province-only source reads "Cavite: province listed; towns not published";
  - a source with no coverage statement reads "Coverage in the study area not published";
  - 911 reads "Nationwide emergency hotline".

Verified organizations on the isolated copy:

| Organization | Category | Area shown |
|---|---|---|
| Meralco | Electricity distributor | Bacoor, Carmona, Cavite City, Dasmariñas, General Trias, Imus, Tagaytay, Trece Martires |
| Emergency 911 National Hotline | Emergency | Nationwide emergency hotline |
| Converge ICT | Fiber and broadband | Coverage in the study area not published |
| FIBERBLAZE (Dasmariñas, GMA, General Trias, Imus, Naic, Silang, Tanza, Trece Martires) | Cable TV / local cable | one town each, as published |
| Streamtech (formerly Planet Cable) | Fiber and broadband | Cavite: province listed; towns not published |

Secondary-source entries (Globe, PLDT) are listed without a phone or website. Unverified entries (City Government
of Dasmariñas, DITO, Eastern Communications, Innove, SKY, Smart) are hidden by default.

## 16. Directory data sources and verification dates

| Organization | Verification | Last verified | Source type | Source |
|---|---|---|---|---|
| Meralco | verified | 2026-11-19 | law | RA 12146 (2025), lawphil.net |
| Emergency 911 | verified | 2026-11-18 | law | EO 56 s. 2018, elibrary.judiciary.gov.ph |
| Converge ICT | verified | 2026-11-19 | official site | convergeict.com/contact-us |
| FIBERBLAZE (8 branches) | verified | 2026-11-19 | official site | fiberblaze.ph (Imus: fiberblaze.ph/FiberblazeImus) |
| Streamtech | verified | 2026-11-19 | official site | streamtech.com.ph |
| Globe Telecom | secondary source | 2026-11-18 | third party | expatfocus.com |
| PLDT | secondary source | 2026-11-18 | press | pldthome.com news, 13 Jun 2024 |
| City Gov. of Dasmariñas, DITO, Eastern Communications, Innove, SKY, Smart | unverified | — | — | none recorded |

No phone number or URL was added or changed in this pass.

## 17. Pole detection baseline and final evaluation

Detector: YOLO11n `models/poles-yolo11n-v218.pt`, trained on LVIS-derived pole boxes. It is context only: it never
creates or joins a wire. Data: `datasets/external/poles_v218` (validation 166 images / 408 boxes; held-out test 197
images / 516 boxes). The threshold was chosen on validation and reused on test. Results are in
`docs/thesis/results/s18_poles_poles_v218_*.json`.

| Run | Split | Protocol | Thr. | Precision | Recall | F1 (95 % CI) | AP50 | mAP50-95 | Latency |
|---|---|---|---|---|---|---|---|---|---|
| **Baseline**, 640 px | val | all boxes | 0.20 | 0.559 | 0.439 | 0.492 (0.447–0.532) | 0.413 | 0.186 | 33 ms |
| Baseline, 640 px | val | medium + large | 0.25 | 0.616 | 0.599 | 0.607 (0.551–0.658) | 0.593 | 0.299 | |
| 960 px | val | all | 0.20 | 0.500 | 0.451 | 0.474 | 0.419 | 0.200 | 55 ms |
| 960 px | val | medium + large | 0.30 | 0.615 | 0.497 | 0.550 | 0.553 | 0.295 | |
| 1280 px | val | all | 0.25 | 0.506 | 0.434 | 0.467 | 0.400 | 0.180 | 75 ms |
| 1280 px | val | medium + large | 0.30 | 0.540 | 0.497 | 0.518 | 0.478 | 0.240 | |
| Test-time augmentation, 640 px | val | all | 0.25 | 0.556 | 0.426 | 0.483 | 0.407 | 0.181 | 88 ms |
| TTA, 640 px | val | medium + large | 0.25 | 0.539 | 0.661 | 0.594 | 0.569 | 0.296 | |
| **Final (= baseline)**, 640 px | **test** | all | 0.20 | 0.505 | 0.386 | 0.437 (0.384–0.491) | 0.386 | 0.174 | 30 ms |
| Final (= baseline), 640 px | **test** | medium + large | 0.25 | 0.620 | 0.518 | 0.564 (0.507–0.619) | 0.545 | 0.268 | |

**Decision.**
- Larger input sizes and test-time augmentation did not beat the 640 px baseline on F1 or mAP (the CIs overlap), and
  they cost 1.7–2.7× the latency.
- Production stays at 640 px, conf 0.25, no augmentation. The checkpoint is unchanged.
- The `imgsz` / `augment` options remain in the evaluator and the pipeline factory for future runs, defaulting to the
  production values.

**Why improvement is blocked.**
- Domain shift: LVIS / COCO poles are not Philippine distribution poles carrying dense wire bundles.
- Heavy occlusion by wires.
- No pole labels exist on WIREFRAME's own field photos.

Real improvement needs field-labelled boxes. `ml/poles/annotation_pack.py` prepares the annotation pack, but labelling
it is a data task for the owner. No metric was estimated or extrapolated.

## 18. Thresholds, visualizations, sources, and applicability

Single source: `config/wireframe.yaml`, read by `app/services/comparison.py`. All classification happens on the
server; the browser only draws what it is given.

| Wire category | Reference | Bands | Status of the source | Where shown |
|---|---|---|---|---|
| Power distribution line | sag / span vs **3 %** | Low < 2 %, Moderate 2–3 % inclusive, High > 3 % | ERB Resolution 95-21 s. 19 (regulatory) | sag meter (three zones, 2 % and 3 % marks), CRS marks 5.0 / 7.5 |
| Telecom / fiber cable | typical range **2 % / 5 %** | below 2 % / 2–5 % / above 5 % | ITU-T L.102 guideline; **not a regulation** | sag meter labelled "typical sag range for telecom and fiber cable (2 % / 5 %)" |
| Other / unclassified wire | none | none | no applicable source | neutral meter, no reference |
| Vegetation, clearance, conductor condition | none | none | the earlier S4 unsupported thresholds stay removed | observation text only |
| VNI | 0.25 / 0.50 / 0.75 / 1.0 | Low / Moderate / High / Severe | thesis Table 3.7, **provisional** | "Other" meter, labelled provisional |
| CRS | marks at 5.0 / 7.5 | derived from the power 2 % / 3 % | derived (250 × δ/L) | "Other" meter; it decides nothing |

The e2e check asserts that telecom meters reference only 2 % / 5 % and never 3 %, and that other classes get no
reference. No new threshold was introduced.

## 19. Database and API changes

**Migration.** Three nullable columns were added to `analyses` (`archived_at`, `archived_by`, `archived_reason`) through
the existing `add_missing_columns`, which writes an automatic backup first: `wireframe-pre-migration-20261119-025603.db`
on the isolated copy. The change is additive only, and existing rows are untouched. On the isolated copy after all
checks, `PRAGMA integrity_check` returned `ok` and `foreign_key_check` returned nothing.

**API.**

| Method and path | Who | Purpose |
|---|---|---|
| `POST /api/admin/inspections/{id}/archive` `{reason}` | admin | Delete (archive); 422 without a reason, 409 if already archived |
| `POST /api/admin/inspections/{id}/restore` | admin | Undo it |
| `DELETE /api/admin/poles/{code}/barangay` | staff | Clear the barangay; coordinates untouched; audited `barangay.unset` |
| `POST /api/admin/inspections/{id}/decision` | staff | Now takes `other_text` (required for "other") |
| `POST /api/analysis/upload` | any | Now takes `location_method` (`device` only), `location_accuracy_m`, `geocoded_city`, `geocoded_barangay` |
| `GET /api/admin/inspections/{id}/context` | staff | Now returns `archived` and `can.delete` / `can.restore` |
| `GET /api/admin/contacts` | staff | Now returns `updated`, `study_area`, and `area_label` per entry |
| result payload | — | Every wire carries `wire_id`; `mask_legend` (public); `metrics` (staff only); `pole.location.capture` |

## 20. Test results and actual execution evidence

| Check | Command / tool | Result |
|---|---|---|
| Backend + API + frontend source tests | `python -m pytest -o addopts="" -p no:cacheprovider -q` | **1102 passed, 2 xfailed** (the 2 xfails are pre-existing and expected) |
| New tests | `tests/test_s18_refinement.py` | 25 passed (incl. 3 Node-executed JS behaviour tests) |
| Lint | `ruff check app tests scripts ml` | All checks passed |
| JS syntax / "build" (no bundler) | `node --check` on all 16 modules | 0 failures |
| Python compile | `python -m compileall app scripts ml` | ok |
| Type checking | none configured in the project (no mypy / pyright config) | not run; nothing to run |
| Migration | server start on the isolated copy | columns added, backup written |
| DB integrity | `PRAGMA integrity_check`, `foreign_key_check` | ok / none |
| End-to-end (upload → processing → citizen → admin → map → exports) | `scripts/e2e_check.py` on 127.0.0.1:8014 | **103 PASS, 0 FAIL, 0 page errors** |
| Refinement browser checks (desktop 1440 px + mobile 390 px, Chrome) | scratchpad `s18r_browser.py` | **70/70** |
| Upload location (Chrome, geolocation granted) | scratchpad `loc_check.py` | **10/10** |
| Pole detection evaluation | `ml/poles/eval_detectors.py` | section 17 |

Changes made to existing tests; no assertion was weakened:

- `test_v221.py` now asserts that the diagnostics module is **not** served and that "Other" is.
- `test_s12_results_ui.py` now asserts that the methodology section is **absent** from the citizen page.
- `e2e_check.py`:
  - checks the methodology section is absent while the API still holds all 11 topics;
  - replaces the diagnostics check with checks for Other, delete and wire search;
  - replaces the "telecom never compared" check, which predated the telecom guideline (D-31), with a stricter check:
    telecom references only 2 % / 5 %, never 3 %, and other classes get none.

Problems found by the checks and fixed:

1. The barangay picker could not be cancelled while its box was empty: a `required` input blocked the Cancel
   submit.
2. Device GPS was displayed as "pinned by an administrator".
3. The PDF said "SP-003".
4. `viewer.focus()` never zoomed.
5. Escape did not hide a hover-shown info panel.
6. The photo toolbar overflowed by 26 px at 390 px and 360 px after the search box was added.
7. The CRS meter's end dot and label were clipped.
8. The delete dialog's reason box used the monospace font.
9. `_insert` referenced `capture` without receiving it (caught by ruff F821 before any test ran).

The upload checks added test inspections (file `loc-check.jpg`) to the **isolated copy only**. Archive / restore and
barangay set / clear were exercised there and left restored.

## 21. Screenshots of major redesigned interactions

All in `docs/thesis/figures/s18/`, taken on the isolated server with Chrome:

| File | Shows |
|---|---|
| `s18r_other_metrics.png` | Photo limitations / Observed / Recommended steps with linked wire numbers; "Other" with VNI and CRS meters |
| `s18r_wire_search.png` | Photo framed on a searched wire |
| `s18r_mask_legend.png` | Segmentation mask with its legend (classes in this photo only) |
| `s18r_citizen_search.png` | The same search on the citizen result |
| `s18r_mobile_search_legend.png` | Search and legend at 390 px |
| `s18r_other_decision.png` | "Other documented decision" refused when blank |
| `s18r_delete_dialog.png` | Delete confirmation with required reason |
| `s18r_barangay_chip.png` | Barangay chip with × and Change |
| `s18r_table_head.png` | Sort arrows and filter funnels |
| `s18r_directory.png` | Five-column directory with the update label |
| `s18r_upload_location.png` | Admin upload with device location |

## 22. Remaining limitations and unresolved research decisions

1. **Study area unconfirmed.** The eight municipalities are the current list kept by the owner's choice, not a
   documented sampling frame.
2. **Pole detection** cannot improve without field-labelled pole boxes. The detector stays context only.
3. **VNI bands are provisional** (Table 3.7), and the CRS is a derived display index with no external validation.
4. **Telecom 2 % / 5 %** is a guideline range, not a regulation; all screens label it that way.
5. **Directory:** six organizations are unverified and two rely on secondary sources; their phones and websites are
   not shown until verified.
6. **The hard delete endpoint** still exists in the API with no UI. Removing it is an owner decision.
7. **Address lookup** depends on OpenStreetMap coverage of barangay names. A failed lookup leaves the boxes for typing;
   nothing is guessed.
8. **Pre-existing mixed asset versions** remain for `notice.js` (2.23 / 2.28) and `styles.css` (2.28 / 2.29). They are
   harmless (different pages) but untidy.

## 23. Exact Git branch, commit, working-tree status, and remote status

- Worktree: `/Users/joannacanila/WIREFRAME-s13`, branch **`recovery/s13-master`**, HEAD **`02bd470`**
  ("feat: approved results page and explicit three-zone meter boundaries (S12, D-28)"). No commit was made.
- Working tree: about 155 entries in `git status --short`, all uncommitted, covering sessions S13–S18. This pass adds
  `app/services/metrics.py`, `app/web/assets/facts.js`, `app/web/assets/location.js`,
  `tests/test_s18_refinement.py`, `docs/thesis/S18A_EVIDENCE_FIRST_REPORT.md` and `docs/thesis/figures/s18/s18r_*.png`,
  and deletes `app/web/assets/admin-diagnostics.js`. The deletion is unstaged and the file is still in HEAD.
- Recovery refs (local only): `refs/recovery/s18-start` (state before S18) and **`refs/recovery/s18b-refinement`**
  (`8dba10f`, this tree).
- Remote: nothing pushed. `origin` still has only the earlier `recovery/s6…s12` branches.
- Main checkout `/Users/joannacanila/WIREFRAME` (branch `main`, `a727bb9`) was not modified by this session.
