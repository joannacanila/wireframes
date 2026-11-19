# Implementation Changelog

Location: `docs/thesis/` (moved from `docs/handoff/` in S5). Newest first. Each entry: task, original behaviour, issue, change, files, source, tests, results, risks, commit.

## 2026-11-19, session S18 (refinement): admin inspection cleanup, wire navigation, directory scope (register D-39)

- Admin inspection: technical diagnostics, acquisition envelope, geometry, "not available" and the three-dot menu removed (data kept in payload and exports); VNI and CRS moved to "Other" with explained meters.
- Wire navigation: clickable wire references, photo and list wire search, one synchronized selection, focus zoom (fit to 4×); wire IDs `WF-xxxxxxxx-Wnn`; PDF says "Wire N".
- Segmentation legend from the classes present; "Other documented decision" requires text; barangay clear (×) with a dismissible picker; Delete = audited restorable archive.
- Sorting cycles ascending → descending → default with tooltips; Risk and Review filters in the header.
- Admin upload "Use my current location" (shared `location.js`), capture method stored and shown.
- Citizen: "How this result was produced" and the barangay-hall line removed; sourced, shuffled loading facts.
- Directory: five columns, verified contact details only, "Updated as of November 19, 2026", study area kept but UNCONFIRMED.
- Pole detector evaluated at 640 / 960 / 1280 px and with TTA; baseline kept; improvement blocked on field labels.
- Tests: 1102 passed, 2 xfailed; e2e 103/103; browser 70/70 + 10/10. Report: `S18_FINAL_REPORT.md`.

## 2026-11-19, session S18: evidence-first results, review workflow, problem-wire triage, provider directory (register D-38)

- **Three records.** Machine observations (unchanged), screening indicators (`screening.summary`: total wires, problem wires, triage, finding, photo tips, next steps) and administrative review (`review_progress`, append-only `admin_decisions`). New tables only; a backup precedes the idempotent legacy backfill; historical machine statuses and reviews are not rewritten.
- **Triage rubric.** 0 Normal, 1-9 Moderate, 10+ High problem wires; confirmed critical hazards override. Versioned (`settings.risk_rubric`, audited); operational, not statutory.
- **Shared result view** (`result-view.js/.css`) for the Field Screener, the inspection sheet and quick review: big wire count, finding, three timestamps, problem cards with the standard meter, personalised photo tips, six evidence layers with zoom / pan / pinch and list-photo sync. Staff-review and duplicate diagnosis sections removed from the citizen page; one 911 line.
- **Admin.** Review tabs (Not yet / Partially / Review complete) and screening filters (High, Moderate, Normal, Processing issue, Missing location) with reconciling counts; columns file / location / date / detected / problem / risk / review / admin result; no Assigned or Reason column; real event feed (automated vs people); decision panel with configurable options, rationale, follow-up, critical hazards, amend, reopen and history; failed inspections reviewable.
- **Providers.** Categories, aliases, previous names, coverage basis, source type and verification; matching by city and conductor type; "confirmed responsible" only from `pole_attachments`. FIBERBLAZE branches, Streamtech, Meralco and Converge contacts verified from official pages; no number was invented.
- **Upload, map, exports.** One upload rule set (HEIC by extension); map list as file / location label / coordinates with detected, problem, risk and review in the preview; search by file name, ID and coordinates everywhere; CSV adds the S18 columns; the PDF states problem wires and triage.

## 2026-11-19, session S17: engineering backend, sag classification, segmentation evaluation, UI/UX redesign (register D-37)

- **Reference engine.** One versioned configuration per reference with provenance (issuer, section, date, URL, applicability, interpretation, boundary basis). Power: <2 % "Within lower reference range", 2-3 % inclusive "Within reference threshold", >3 % "Reference threshold exceeded" (3 % regulatory, ERB Res. 95-21 s.19; 2 % descriptive). Telecom keeps 2 % / 5 %. Classification on the unrounded ratio; the calibrated range is reported and never moves a class; CRS = 250 x ratio for display.
- **Pole result.** `outcome.pole_assessment`: exceeded / no exceedance / incomplete / measurement unavailable / no applicable reference. An unavailable span never erases an exceedance. Professional sentences ("Estimated sag 3.8% exceeds the 3.0% power-line reference. Refer this span for professional inspection."); no "give or take".
- **One result everywhere.** The screening result is a field of its own beside the queue status on the results page, dashboard queue, Inspections, map popups and the inspections CSV ("Screening result" column, appended).
- **Important Notice.** The canonical text (`app/core/notice.py`, `notice.js`) once per page; PDF footers say "See the Important Notice".
- **HEIC preview.** `POST /api/analysis/preview` decodes with the upload's own decoder, re-encodes without metadata, stores nothing; used throughout upload and processing. `captured_at` (timestamp only) shown on the report.
- **Segmentation.** `ml/experiments/s17_seg_eval.py` (dump / score / compare by condition), baseline plus xjunction, turn25, xjunction_turn25, cluster12 measured on labelled val/test; none adopted. `tests/test_s17_segmentation.py` pins crossings, parallels, 1 px wires, gaps and determinism with the shipped parameters; crossings at or below ~20 deg are a strict xfail (known limitation). Hard-case pack and instance annotation spec.
- **Design system.** IBM Plex Sans / Mono self-hosted; neutral paper and ink; one steel accent; amber / green / red by meaning only; Geist, the gold glow, the skeleton shimmer and the processing sweep removed. PRODUCT.md and DESIGN.md.
- **Results page.** Report header (reference, status, result, counts, dates, actions), photo-dominant layout, wire cards with classification, estimated relative sag, estimated measurement range, reference and per-wire quality notes; Other wires toggle; card / list / photo selection synchronised (keyboard too); sag diagram; Copy message without link or safety claim; next steps beside contacts; methodology folded; notice at the foot.
- **Admin.** Dashboard status tabs with live counts replace the KPI cards and the status dropdown; Result column; notice as a footer toggle. Inspections: Result column (bulk actions verified functional server-side). "Map & Dispatch" renamed "Map & Locations", "Dispatch route" renamed "Visit route"; popups show the screening result. Technical view reads the server classification.
- **Tests.** New: `test_s17_reference.py`, `test_s17_consistency.py`, `test_s17_segmentation.py`, `test_s17_heic_preview.py`. Updated to new requirements, not loosened (see S17_FINAL_REPORT.md s.16).
- Unchanged: model weights, stored inspection records, roles and permissions; no dispatch capability added.

## 2026-11-19, session S16: system-wide harmonisation (register D-34, D-35, D-36)

- **One plan per result.** `overview.display` places every wire in exactly one list (attention, notable, others, not checked), ordered on the server; the headline counts are those lists. Telecom beyond the typical range is "worth a closer look", never "may need attention"; power keeps the S14 rule. Summary lines count each reference's own wires. `report_id` comes from one server function (page, copied message, PDF).
- **Measurement.** Calibrated 90 % band per wire (`sag_ratio_uncertainty`), shown as "give or take". The continuation path through crossings and a curvature test were evaluated; neither is adopted (D-34 has the numbers). Per-stage, per-condition loss analysis in `results/s16_conditions.json`. **No model weights changed.**
- **Field data.** `ml/field_dataset.py` (versioned, read-only, exact and near-duplicate grouping, pole-level leakage groups, deterministic splits) and `docs/thesis/FIELD_LABELLING_GUIDE.md` (classes, ambiguity rules, QA by double labelling). Trial on the isolated copy: 15 photos in 12 groups. No labels yet, so no retraining.
- **Contacts and location.** One resolver (`contacts.for_analysis`) for page, PDF and assistant; city spellings normalised ("Dasmariñas City", "City of Dasmariñas", "dasmarinas"); utilities relevant to the photo's wire types first; a missing location is no longer reported as a missing utility; barangay / city mismatch reported, never corrected. Seed notes no longer mention development sessions; stored rows untouched.
- **Results page.** Two levels: cards for attention and notable wires, one list for the rest; picking a wire opens it in a separate panel, so no list or count changes; larger sticky photo with pinch zoom, and scroll-friendly wheel and touch handling; sag diagram (inline SVG) instead of an accordion; report card with reference and date-time; "Copy message" (confirms only on success); amber for attention, red only for emergencies; no eyebrow label.
- **Admin.** Fleet line with entity labels; full-width review queue; People / Automated activity; Recently processed. Inspections: Incomplete tab, mismatch filter, single Export menu, quick-review drawer, action always visible. Map: status colours, status-mix clusters, one search, `?pin=1`. Directory: readable cells, search, filters, field-level audit.
- **HEIC / HEIF.** `pi-heif` decoding by content, orientation-safe, clear errors for damaged files and unsupported codecs; the page accepts .heic and shows the server's copy while processing.
- **Processing screen.** Fills the viewport, keeps the photo in view, real stages only, and a rotating "Did you know?" with general physics and safety facts (no model statistics).
- **Tests.** New: `test_s16_integrity.py` (16), `test_s16_heic.py` (11), `test_s16_admin.py` (3). Updated to new requirements, not loosened: `test_s14_ux.py` (telecom notable; Incomplete tab, now checked against the filtered list), `scripts/e2e_check.py` (the single plan, stable lists on pick, report ID, fleet line). Full suite 967 passed; e2e 81 / 81.
- Unchanged: model weights, the power 3 % provisional reference, telecom bands, stored inspection records, roles and permissions.

## 2026-11-18, session S15: model repair, telecom / fibre ranges, targeted UX fixes (register D-31, D-32, D-33)

- **Diagnosis.** Field results were 92.3 % telecom. On held-out test images the strand head is accurate (0.936 square, 0.919 field-shaped). Two pipeline faults were found and fixed: the head saw original-resolution features instead of the letterbox frame it was fitted in, and out-of-range features (field strand counts up to 136, fitted maximum 81) were extrapolated. The rest is a domain gap (dense, night and against-sky scenes) that cannot be fixed without labelled field photos. **No weights retrained or changed.**
- **Metrics (field-shaped test, 642 matched strands).** Accuracy 0.919 to 0.932. Macro F1 0.914 to 0.927. Power P / R 0.932 / 0.859 to 0.961 / 0.863. Telecom P / R 0.912 / 0.959 to 0.915 / 0.977. Centerline F1 (unchanged): wire 0.812, power 0.822, telecom 0.715, abandoned 0. Files: `docs/thesis/results/s15_model_metrics_test.json`.
- **Measurement.** Kink-aware span fit (`app/pipeline/span_fit.py`): test MAE 0.665 to 0.497 pp against the labelled wire; measured share 96.5 % to 99.2 %.
- **Telecom / fibre reference.** Server-side `telecom_reference` with three bands (below 2 %, 2-5 %, above 5 %), sources in D-31. Separate from the power 3 %; unknown types get neither.
- **States.** Six-stage `wire_states.stages`, `inconclusive_cause`, one bridged rule, `methods` provenance.
- **UX.** Landing notice as a small indicator (hover / focus preview, click for the full notice, Esc); larger upload action. Workspace: the optional fields fold under "Add location & label", a location chip with Edit / Remove, a dropzone tip, no warning block above the button, Recent shows 5 plus View all with server statuses and a "Pole check · Barangay" name fallback, enabled / disabled button states. Location: secure-context check, high-accuracy then low-accuracy fallback, distinct denied / timeout messages, a reverse lookup (OpenStreetMap Nominatim) that fills only empty City / Barangay fields, coordinates kept on lookup failure. Processing: real stages only (photo received, queue position, checking, preparing), a slow notice at 90 s, and a stall state with Check again / Screen another photo. Results: one disclaimer, telecom bands on the meter, the selected wire no longer repeated in "Other wires", the viewer uses the server label, no research notes.
- **Tests.** `tests/test_s15_model.py` (23). Updated, never removed, to the new requirements: telecom now compared (stricter: never 3 %); the processing markup; the notice moved to the footer; one disclaimer.
- Unchanged: model weights, the power 3 % reference, verdict strings, stored records, roles.

## 2026-11-18, session S14 (continued): citizen-first results page and assistant role separation (register D-30)

- **Citizen results page rebuilt around four questions** (what was found, does anything need attention, what to do, who to contact). One plain sentence of result from the server (`overview.citizen`: tone, title, text), the photo beside the wire(s) to look at, then What you can do next (safety, 911), Who to contact (Call / Website buttons; secondary-source numbers say so; the ownership caveat), Your report (one Download / Share action), and About this result (folded: notice and methodology). No metric tiles, wire table, meter boundaries, layers, opacity, mask, coverage, chord, pixels or map on the citizen page.
- **Measurement for citizens.** Each wire card shows "Estimated sag x%" on a small bar. Power lines the server compared get one "Comparison point" tick (no zones, no boundaries); every other measured wire gets the plain bar. Each wire has one server-written sentence (`finding.plain`) and a label (`finding.citizen_label`: May need attention / Checked / Measured, not rated / Partly checked / Could not be checked). A wire with no comparison point is never labelled "Checked" or coloured as fine.
- **Technical view kept for staff.** The previous detailed page is `renderTechnical`, opened with `?view=technical` by a signed-in staff member (the server sends a public result to anyone else, so the parameter alone unlocks nothing).
- **Citizen assistant** (`app/assistant/citizen.py`, `citizen.md`, `POST /api/help/ask`): public, rate-limited per address, reads no session, loads only the public view of the one result whose id the caller holds, has no tools, no record lookup and no navigation, and refuses staff requests (dispatch, assign, review, export, delete, other records, directory, dashboards, model internals, "act as admin"). Emergency words get "Call 911 now" first. Optional Claude wording uses a citizen prompt over the same sources. **Staff assistant** (`/api/assistant`) still requires a staff sign-in; its inspection facts lead with the portal status and reason, and "what needs review" now counts the dashboard's Needs review status (8 = 8), linking to the matching Inspections tab.
- **Tests.** `tests/test_s14_citizen.py` (17): module and route separation (imports, no session, public view only), staff requests refused (10 phrasings, anonymous and with an administrator's session), staff route 401, plain answers without jargon or "is safe", 911 first, citizen summary per state, unrated wires never "Checked". Updated for the new structure: the results reading-order test (now also asserts the engineering controls are absent from the citizen page and present in the technical view), the staff-assistant answer prefix, a chat shadow within the S12 style rule. `scripts/e2e_check.py` checks the citizen page, the help panel, the refusal and 401, and the meter kinds in the staff technical view.
- Unchanged: models, weights, thresholds, the 3 % reference and its boundaries, the flag rule, stored data, the PDF content.

## 2026-11-18, session S14 (continued): user experience overhaul and interface simplification (register D-30)

- **Sag meter for every measured wire, decided by the server.** `outcome.meter_state` sets `finding.meter.kind`: `reference` (the stored comparison applied a configured reference: three zones from its boundaries, the reference tick, and a note that it is a comparison point, not a legal safety limit), `neutral` (measured, no applicable reference: telecom / fiber, abandoned, uncertain type; a plain scale with the value, no zones, no reference tick, no rating colour, "No verified comparison" and the reason), `none` (not measured). The results page, the admin wire panel and the PDF read that kind; none of them computes a threshold. A reference configured later for another class switches the meter without page changes. The 3 % reference is still applied to power lines only.
- **Plain words.** Wires identified, Measured, Need review, Not fully evaluated; Estimated sag; Partly outside the photo; No verified comparison; Reference unavailable. Same labels on the page, the PDF summary, the PDF findings table (new Estimated sag column with the matching mini meter) and the admin sheet.
- **Citizen results.** Summary band with four counts; one primary action (Download Inspection Report, with Share); wire panel shows type, estimated sag on its meter, the explanation and the report toggle, with coverage, chord, reference arithmetic and the trace under Technical details; wire list columns Wire / Type / Estimated sag / Result; What to do next beside Who to contact. Photo label reads "Wire n · estimated sag x%". Mobile overflow fixed (grid `minmax(0, 1fr)`).
- **Admin.** Inspections list on the operational statuses (tabs All / Needs attention / Needs review / Processing failed / Reviewed; status and assignee filters; columns Pole / Location / Status / Reason / Date / Assigned to; server-side `status`, `assignee`, `op_tab`, sorts `status` and `assigned`; the dashboard's bulk bar plus Set barangay and test / operational record actions). Dashboard: Assigned to me metric and Recently completed list (`/api/admin/operations` returns `me`, `mine`, `recent`). Inspection sheet: overview with status, reason, assignment and review first; photo with the wire panel and meter; wire table; review status; details; one Technical details fold (coverage, limitations, diagnosis, next steps, full notice) and the administrators' diagnostics. "Flagged" is now "Needs review" everywhere.
- **Tests.** `tests/test_s14_ux.py` (7). Updated to the owner's new directive: the no-reference meter test (neutral replaces S12's wire-coloured fill; the no-zones, no-tick, no-3 % assertions are kept and tightened) and wording pins. `scripts/e2e_check.py` checks the meter kind for every wire class present, the dashboard's Assigned to me metric and the sheet's overview.
- Unchanged: models, weights, thresholds, the 3 % reference and its boundaries, the flag rule, stored data.

## 2026-11-18, session S14: master consolidation (register D-30)

- **Baseline:** worktree `~/WIREFRAME-s13`, branch `recovery/s13-master` (base `02bd470` + the S13 work). Recovery snapshot of the starting tree: `refs/recovery/s14-start` (6c96d1a). All runs against an isolated data copy (scratchpad `iso14/var`, port 8014); the live `WIREFRAME/var` was never opened for writing.
- **Kept from S12:** the 3 % reference for power lines only; no 3 % marker or zones for telecom, fibre, abandoned or uncertain wires (the S13 muted-context meter was removed); server-generated summary and methodology; web and PDF from the same data.
- **Kept from S13:** `model_identity`, the `strand_head_unavailable` flag, `scripts/repeatability.py` (S14 rerun: 3 runs bit-identical on P-1958C4).
- **Lifecycle:** `outcome.py` separates processing state from assessment state; failures record stage / category / attempts / retryability (staff only); at most 3 attempts; retry refuses non-retryable failures; queue dedupes and reports position.
- **Results page:** light first; summary "Photo analyzed" with Wires detected / Measured / Flagged for review / Unable to fully assess; one Download / Share Report action whose PDF and link carry the chosen wires (remembered per device); simple viewer controls with model layers under Advanced; photo frame follows the image's aspect ratio; selected wire isolated (others dimmed); inventory tabs All / Measured / Unmeasured, one secondary filter, search, sort, paging, per-wire report checkbox; three-zone meter only for compared power lines; What to do next (with 911); suggested contacts; one folded "How this inspection works". The desktop-only block is gone: phones and tablets are supported.
- **Landing:** only the disclaimer line (owner's wording) and "Read the notice" as a modal dialog; spacing so the longer line never overlaps the wordmark (360 to 1920 px). The full Important Notice is unchanged.
- **Admin:** dashboard on `/api/admin/operations`: one status per pole (processing failed, flagged, incomplete, awaiting review, reviewed), needs-attention queue with reason, readable location (coordinates secondary, identical-GPS warning), assignee; filters, search, sort; bulk assign / unassign / mark reviewed / export selected / mark test (server-checked); team vs system activity; Directory page (administrators edit, inspectors read). Tablet and phone layouts without sideways scrolling.
- **PDF:** 1.0 summary (page's sentences), 2.0 what to do next, 3.0 photograph of the included wires, 4.0 findings included, 5.0 contacts, 6.0 limitations and methodology, 7.0 review and sign-off, Appendix A technical measurements.
- **Results:** pytest 875 passed, ruff clean; E2E 62/62, 0 page errors, one real model inspection (P-1958C4: 10 wires, 10 measured, 1 power line flagged) with its PDF.
- **Not changed:** model weights, thresholds, preprocessing, classification, the comparison rule.

## 2026-11-18, session S13: model stability investigation, traceability, meter context and landing notice (register D-29)

- **Baseline:** branch `recovery/s13-master` from `02bd470` (S12; tag `s13-baseline-s12`). Data backup before any change: `~/WIREFRAME-backups/s13-start-20261118/wireframe-backup-20261118-171805` (verified, integrity ok). All browser checks on a restored copy of that backup, never on `WIREFRAME/var`.
- **Fluctuation, root cause (evidence `results/s13_repeatability.json`):** the same photo gives bit-identical output (wire count, classes, statuses, chord, sag, ratio, resolution, endpoints, wire-mask hash) three times in one process and in two fresh processes (MPS, flip TTA, 3 photos, 18 to 135 wires). Byte-identical photos uploaded up to 12 times agree exactly within every config fingerprint and differ only between fingerprints. The observed variation is version drift: configuration and code changed across S4 to S12, and three servers ran three branches at once (8000 = S11 on the live data, 8011 = S11, 8012 = S12). No randomness, no nondeterministic GPU effect found; checkpoints match the pinned SHA-256 manifest. No weights, thresholds or model outputs were changed.
- **Traceability gaps fixed:** (1) the config fingerprint does not change when a checkpoint file or the admin's active checkpoint changes: `model_identity()` (`app/core/state.py`) hashes every configured checkpoint (YOLO, the three ensemble members, pole detector, strand head); stored per analysis in `metadata_json.model_identity` and shown on `/api/health`. (2) A strand head that fails to load (e.g. memory pressure on an 8 GB machine) left segmentation-only wire types with only a log line, and the next request retried the load, so two runs could differ silently: the result now carries the flag `strand_head_unavailable`.
- **Sag meter:** a measured wire without an applicable reference (telecom, abandoned, uncertain type) no longer shows a single-colour bar; it shows the three zones muted, with a dashed tick labelled "3.0% power-line ref." and the note that the reference is shown for context only and does not apply. No zone, position or comparison is assigned to that wire. Covered power lines are unchanged (D-28).
- **Results page:** the "no wire is covered" sentence is no longer repeated under the KPI bar when the server summary already states it.
- **Assistant:** knowledge base said every measured wire is compared with 3 % (stale since D-27) and had no answer to "what 3 % limit applies to telecom cables?" (it answered about detection accuracy). Sag and not-assessed sections corrected; new section "Which wires the 3% reference applies to". Verified live on a stored inspection: answers use the record's data and make no safety claim.
- **Landing page:** only the one-line notice text (`notice.js` `LEAD.landing`) changed; screenshot diff before/after at 1440 and 390 px shows no other change apart from the hero's animated measurement label. The full Important Notice (PDF, CSV, assistant, "Read the notice") is unchanged.
- **Files:** `app/core/state.py`, `app/main.py`, `app/services/analysis.py`, `app/pipeline/wirenet.py`, `app/pipeline/runner.py`, `app/web/assets/field.js`, `app/web/assets/field.css`, `app/web/assets/notice.js`, `scripts/repeatability.py` (new), `app/assistant/knowledge.md`, `scripts/e2e_check.py` (no-reference meter check and the meter-follows check updated: two wires past the 10 % scale both sit at 100 %, correctly), `tests/test_s13_stability.py` (new), `tests/test_s12_results_ui.py` (no-reference meter assertion replaced by the new requirement).
- **Results:** pytest 848 passed, ruff clean; E2E 64/64 and 63/63 on two photos, 0 page errors (isolated copy, port 8013); new records carry `model_identity` 9ac07427bd56.
- **Not changed:** model weights, thresholds, preprocessing, the comparison rule, the landing layout.

## 2026-11-18, session S12: approved results page and explicit meter boundaries (register D-28)

- Results page: header actions on the right; one dismissible banner (tip and off-frame warning); dynamic summary from the server; KPI bar Measured / Visible Part Only / Not Measured / Assessed; three-step action bar (Keep Distance, Download / Share Report, Retake Photo); viewer toolbar with Measured / All wires / Original / Mask / Both, Layers, zoom readout, Fit, Reset and the legend; sticky inspector with one status, hidden empty rows and Measurement details; wire inventory with tabs, quick filters (Notable Sag > 2% is a browse filter only), search, clear, Coverage and Status columns and a collapsed "no resolvable sag" group; synchronised selection with controlled scrolling; one Methodology & Limitations section.
- Meter: green / amber / red from configured boundaries (both 3 %, no validated tolerance), uncertainty widens amber, overlap and overflow shown; no-reference wires keep the coloured meter with "No applicable comparison".
- PDF follows the page (summary, actions, evidence, measured spans with mini meters, Methodology and Limitations, review). Admin sheet and findings CSV use the same coverage and reference words. The public result keeps the new fields.

## 2026-11-18, session S11: handoff interface (0916fd2) restored with the engineering corrections (register D-27)
- **Baseline:** `0916fd2` ("docs: verify ERB 95-21 status; add handoff package", 18 Nov 2026 02:39 +0800), HEAD
  when this account's first session began (transcript 17 Nov 18:51 UTC; working tree clean apart from the owner's
  untracked thesis files). Its result page is the v2.23 page with the v2.21 meter (`7c5a716`).
- **Branch:** `recovery/handoff-0916fd2-engineering` (worktree `/Users/joannacanila/WIREFRAME-handoff`) from S10
  `0bf4299` (backend); page files `field.js` / `field.css` taken from S7 `a02fb97` (= handoff page plus the S4/S5
  data changes; `field.html`, `findings.js`, `viewer.js` already identical). Admin portal unchanged from S10, which is
  within about 50 lines of the handoff (S4/S5 fixes and the comparison column).
- **Meter:** the original markup and colours; lit from the power-line comparison (zones from the 3 % reference and
  the wire's resolution, legend Below / At / Above). Non-power, abandoned and uncertain-type wires: the same meter in
  the wire-type colour, no zones, no tick, "Comparison unavailable". The S7 grey meter is removed.
- **Page changes to the handoff page:** the S4/S5 measured-wire states (kept from S7), the comparison sentence in the
  summary, the card rows Reference / Difference, "What the analysis found" placed after "What to do" instead of above
  the photo. Nothing else.
- **Tests:** pytest 815 passed; ruff clean; E2E 29/29 on P-B1D137 (telecom only) and 29/29 on a power + telecom
  photo, 0 page errors; PDF, admin sheet and findings CSV agree with the page.
- S10 (`0bf4299`, S8 page) and S9 (`25bb9b5`) are kept on their branches and not carried forward.

## 2026-11-18, session S10: S8 colourful-meter version restored; 3 % reference for power lines only (register D-27)
- **Baseline:** `52baa3c` (`recovery/s8-colour-meter`, 18 Nov 2026 11:10 +0800): v2.18 result page with the v2.21
  green / amber / red meter in the selected-wire panel, the version the owner asked to have back. The S9 overhaul
  (`25bb9b5`, branch `recovery/s9-ux`) is kept in history and not carried forward.
- **Branch:** `recovery/original-colour-meter-engineering-corrections` (worktree `/Users/joannacanila/WIREFRAME-tidy`).
- **Engineering:** reference applies to power lines only; wires of uncertain type are never compared; telecom /
  abandoned / uncertain wires keep their measurement on the meter in their type colour, marked "Comparison
  unavailable"; panel, table, admin sheet, PDF and CSV say not applicable; counts sentence states when no wire is covered.
- **Presentation (targeted):** notice once (short line and footer removed on the result), duplicate screening line
  removed, table note removed, table shows the ratio, per-wire reasoning / section-number lists / "What the analysis
  found" folded; nothing removed.
- **Not changed:** models, measurement, thresholds, severity logic, stored records, routes, API, roles.
- **Tests:** pytest 815 passed; ruff clean; E2E 45/45 (telecom-only photo) and 46/46 (power and telecom), 0 page errors.

## 2026-11-18, session S8: full-colour sag meter, configured provisional 3 % reference (register D-26)
- **Branch:** `recovery/s8-colour-meter` (worktree `/Users/joannacanila/WIREFRAME-meter`) from S7 `a02fb97`; S7 and S6
  branches unchanged. Port 8000 runs this branch on the live `var/` (backup `wireframe-backup-20261118-105442` first).
- **Root cause of the grey meter (S7):** with no validated reference the server sent no `sag_bands`, so S7 drew the
  meter unlit by design; the owner asked for the coloured meter with a 3 % reference.
- **Change:** `comparison_reference` in the configuration and `app/services/comparison.py` (one rule, every view);
  result page = S6 v2.18 page + v2.21 meter in the selected-wire panel, comparison column and counts; PDF, admin sheet,
  findings CSV, wires CSV (adds `level_withdrawn`), assistant; resolution derived on read for pre-S4 records; diagnosis
  and assistant wording updated (the assistant text no longer cites ITU-T L.102 content that could not be read).
- **Not changed:** models, measurement code, assessment (no wire is scored, no verdict changes), stored records
  (detection fingerprint before and after deployment identical, excluding a new inspection uploaded during the session).
- **Tests / results:** pytest 809 passed; ruff clean; E2E 49/49, 0 page errors; live port 8000: four P-B1D137
  inspections and the newest upload show the coloured meter for above / at / below / not-measured wires, 0 console
  errors (`results/s8_colour_meter.json`).
- **Limits:** the admin sheet was checked on the isolated copy only (the live admin needs the owner's sign-in); the live
  PDF was built from a snapshot of the live database, because requesting it on the server rewrites the stored report.

## 2026-11-18, session S7: the v2.23 result page with the three-zone sag meter (register D-25)
- **Branch:** `recovery/s7-sag-meter` (worktree `/Users/joannacanila/WIREFRAME-meter`), from `recovery/s6-restore`;
  bookmark `recovery/meter-reference-0916fd2` = `0916fd2` (last commit that displayed the meter).
- **Finding:** the only sag meter in the history is `rx-meter` (`7c5a716`, v2.21). Its three zones were Within
  reference / Minor sag / Critical at 2.4 % and 4.0 % image sag (withdrawn placeholder). After S4 it never rendered.
  The S6 v2.18 restore removed it with the v2.23 page.
- **Change:** owner's choice: the v2.23 page (as S5 left it, `a727bb9`, with the S4/S5 corrections) replaces the S6
  v2.18 page; `meter()` kept verbatim for a wire assessed against a configured reference; new `meterUnlit()` and
  `meterEmpty()` for every other wire; card rows "Image sag ratio", "Reference limit: None validated", "Difference:
  Not computed". S6 fixes outside the page kept: table finding wording (`findings.js`), PDF wording and colours, admin
  sheet, backup.
- **Not changed:** thresholds (none reintroduced), models, configuration, stored inspections.
- **Tests / results:** pytest 794 passed; ruff clean; E2E 29/29, 0 page errors; three historical records render the
  meter unlit with no page error (`results/s7_sag_meter.json`).
- **Risk:** the S6 v2.18 controls (table, Both view, opacity slider, Layers menu, Reset, Measurement details,
  technical drawer on the result page) are not on this page; they remain on branch `recovery/s6-restore`.

## 2026-11-18, session S6: recovery of the v2.18 result experience, full backup (register D-13, D-24)
- **Branch:** `recovery/s6-restore` (worktree `../WIREFRAME-recovery`), from `a727bb9`; `main` untouched. Bookmarks:
  `recovery/baseline-v218` (`f74250e`), `recovery/pre-recovery-main` (`a727bb9`).
- **Baseline:** v2.18 `f74250e`, run in an isolated worktree on a fresh data directory with the thesis photo
  P-B1D137; it reproduced the owner's thesis figure (2 / 2 / 14 counts, 26 sections, wire 24 selected).
- **Root cause chain:** (1) `7c5a716` (v2.19-v2.23 "plain result page"): the v2.18 result page and its styles were
  removed (table, Both, opacity, Vegetation, Layers, zoom readout, Reset, measurement details, specific limitations
  and next steps, technical drawer); (2) `5313c52` (S4): renderers showed only assessed wires, so every measurement
  was hidden; (3) `a727bb9` (S5): measurements restored in a different layout, with two defects found now: the table's
  short finding said "Could not be measured" for measured wires, and the PDF summary said a retake could not help
  while section 3.2 asked for one. Tests missed (1) because the v2.20 test asserted the removed features were absent.
- **Change:** v2.18 result page restored on top of S4/S5 (`field.js`, `field.css`); measurement states instead of
  severities when nothing is assessed; type colours; staff-only drawer; `findings.js` ordering fix; PDF figure
  coloured by type (`render.py`), caption updated; `plain.py` retake wording follows `diagnosis.retake_helps`;
  admin sheet uses the same states, legend and coverage (`admin.js`, `admin.css`); `scripts/backup.py` (D-13).
- **Tests / results:** pytest 788 passed; ruff clean; E2E 41/41, 0 page errors (isolated copy); backup of live data
  verified (241 files) and restored copy served 105/105 results and photos (`results/s6_recovery.json`).
- **Not changed:** model weights, configuration values, stored inspections, the engineering decisions of S4.
- **Audits (limited, S6):** accessibility, automated basics on the restored page (every visible control has an
  accessible name, no image without alt text, table headers present, arrow keys move through table rows and the view
  radio group; no WCAG conformance is claimed, no screen-reader test was run); dependencies, `pip-audit`: no known
  vulnerabilities; code review of the new backup tool found and fixed a path-traversal risk from a tampered manifest
  (`_safe`, tested). Runtime benchmark not run: it requires stopping the owner's port 8000 server.
- **Risk:** the production server on port 8000 runs `main`, not this branch, until the owner merges and restarts.

## 2026-11-18, session S5: diagnostic output restored (register D-22, D-23)
- **Original behaviour (S4 build, 5313c52):** the pipeline measured every wire, but the result page, overlay viewer,
  PDF figure and admin sheet showed, drew and selected only *assessed* wires. With no validated reference: 0 wires
  drawn by default, no wire selected, no fitted curve, chord or sag point on any wire, every measurement in a
  collapsed list labelled "These wires were not measured", a retake banner on every analysis, PDF Figure 1 "No wire
  was flagged, so none is drawn", and a PDF that both said a retake would not help and recommended one. Admin list
  counts and assistant facts still counted withdrawn pre-S4 levels ("10 high risk"). Reproduced in headless Chrome on
  isolated copies of `var/`, compared with the pre-S4 code (0916fd2) on the same photo
  (`docs/thesis/results/s5_diagnostic_restoration.json`).
- **Why the checks missed it:** the S4 tests and E2E checks asserted only that severity was absent (754 passed, E2E
  20/20 on the broken build).
- **Change:** new `app/services/diagnostics.py` (per-wire and per-photo diagnosis from stored fields only; no new
  threshold; Section 8.6 of the reference); attached to the staff and public result. Viewer and PDF overlay draw the
  curve, chord and sag point of every measured wire; the result page opens on measured wires (coloured by type, the
  largest ratio selected), lists Measured / Not measured, shows "What the analysis found", and advises a retake only
  when wires were not measured or only partly measured; partial-span and uncertain-type wires show their measurement;
  ratios below their 1 px resolution read "within resolution"; the admin sheet shows the diagnosis and opens on a
  measured wire; PDF Figure 1 draws measured wires, section 4.0 adds per-wire observations, new section 4.1; findings
  CSV gives ratio +/- resolution; admin completeness `measured` ("Measured, not assessed"); assistant facts report
  measured wires and "overlaps in the image". `level_withdrawn` also withdraws any level of a record without the
  current assessment basis; `wire_counts` never counts withdrawn levels.
- **Files:** app/services/{diagnostics (new),analysis,basis,records,render,report_pdf}.py, app/api/admin.py,
  app/assistant/service.py, app/web/assets/{field,findings,viewer,admin}.js, field.css, scripts/e2e_check.py,
  tests/test_s5_diagnostics.py (new, 20 tests), tests/test_v219_portal.py (completeness `measured`).
- **Not changed:** model weights, configuration values, pipeline outputs, stored inspections (no reprocessing).
- **Tests / results:** pytest 776 passed; ruff clean; E2E 24/24, 0 page errors (isolated copy, port 8011).
- **Documentation:** `docs/thesis/` made the authoritative technical record; this changelog and the register moved
  there from `docs/handoff/` (git history kept). Research update S16-S18 (DSOAR s.1.7.12 lead, unverified).
- **Risk:** the production server (port 8000) serves the new page scripts from disk at once, but the `diagnosis`
  field only after a restart; until then the page falls back to its own measured-wire logic.

## 2026-11-18, session S4: unsupported thresholds removed (register D-02..D-21)
- **Original behaviour:** telecom spans scored against a 4.0 % reference with a Minor band from 2.4 %; pole verdicts
  from corroborated sag bands and pixel vegetation rules (contact -> HIGH, power within 15 px -> MODERATE);
  INCONCLUSIVE below 50 % assessed; 100 px precision gate (f = 0.25); illustrative conductor data (change of state,
  10.1 mm diameter for metre gaps); MSPI ranking from CRS.
- **Issue:** research (register "S4 threshold resolution": ITU-T L.102 (11/2025) full text, Europacable 2023, FOA,
  Hengtong, Philippine JMC No. 01 s.2021 / NTC / ERB 95-21 / PEC searches) found no applicable source for any of
  these values; 4.0 % was a development placeholder from the initial commit.
- **Change:** `config/wireframe.yaml`: `crs.delta_lim0: {}`, `crs.levels: {}`, `crs.min_chord_px: 0`,
  `engineering.conductors: {}`, `hazard.vegetation_verdict: false`, `hazard.min_assessed_share: 1.0`.
  Code: a class is assessed only with a verified reference (`CRSConfig.has_reference`); new reason
  `no_validated_reference`; every fitted span keeps its sag ratio, curve and `sag_ratio_resolution` (1 px / chord);
  photo status `complete` when a span was measured; NOT AT RISK needs at least one assessed span; vegetation is an
  image observation (NOTE). Stored pre-S4 results are shown without their withdrawn severities and verdicts
  (`app/services/basis.py`; stored rows unchanged; CRS not used for ranking). Page, PDF ("Measured Spans (not
  assessed)" table, severity counts only when assessed), admin CSV, assistant knowledge and E2E updated. Owner's
  decision-support disclaimer adopted in `app/core/notice.py` / `notice.js`. Admin label for INCONCLUSIVE poles is
  "Assessment incomplete" everywhere (key `unassessed` unchanged).
- **Files:** app/core/config.py, app/pipeline/{runner,hazard}.py, app/services/{analysis,basis,plain,reasons,records,
  report_pdf}.py, app/models/orm.py, app/api/{admin,analysis}.py, app/web/assets/{field,findings,viewer,admin,
  admin-map,notice}.js, app/assistant/knowledge.md, app/core/notice.py, config/wireframe.yaml, ml/evaluate.py,
  ml/experiments/v217_eval.py (variant `s4`), scripts/e2e_check.py, tests (new `tests/test_s4_thresholds.py`;
  mechanics tests use a TEST-ONLY reference in `tests/conftest.py`), docs.
- **Tests / results:** see SESSION_LOG.md (S4).
- **Risk:** the system no longer produces a pole-level screening verdict; every analysis with measured wires is
  INCONCLUSIVE until a verified reference and physical validation exist.

## 2026-11-18, session S3: 3 % power-line reference investigated (not implemented)
- **Original behaviour:** power lines measured, not assessed (`no_reference_for_class`).
- **Issue:** requested implementation of the 3 % sag rule of ERB Resolution No. 95-21 s.19 for power lines.
- **Finding:** primary texts read: 95-21 applies to electric power utilities (s.2); ERC Resolution No. 02 s.2010 states
  95-21 was superseded by the DSOAR (effective 17 Feb 2006). No current in-force source for 3 % could be verified.
- **Change:** documentation only (technical reference 7.5, reference 39; register D-04). Behaviour unchanged.
- **Files:** `docs/thesis/WIREFRAME_THESIS_REFERENCE.md`, `docs/handoff/*` (new).
- **Tests:** full suite re-run (see SESSION_LOG.md).
- **Risk:** power lines stay unassessed until a current source is obtained.
- **Commit:** see SESSION_LOG.md (S3 end commit).

## 2026-11-18, session S2: research alignment (commits 0fbfd89, 16626ae, fb0f7d4)
- Transformers 4.57.6 -> 5.17.0 (+ huggingface-hub 1.33.0, tokenizers 0.23.3) for CERT/CC VU#456290. Evidence:
  DINOv2 bit-identical; scene parser <= 0.009 % pixels differ; release evaluation identical in every field
  (val + test); 719 tests; E2E 16/16; model hashes unchanged; pip-audit clean. Rollback: 4.57.6 / 0.36.2 / 0.22.2.
- Abandoned wires no longer scored against the telecom limit (`crs.assessed_classes: [telecom-cable]`); reason texts
  generalised. No validation/test metric changes; 6 historical scored abandoned records kept.
- INCONCLUSIVE labelled "Assessment incomplete" with the system's reason (`field.js`, `findings.js`).
- Viewer: no fit on a zero-size container (fixed intermittent negative-radius canvas error) (`viewer.js`).
- Tests added: backup restore cycle; abandoned-wire rule. Two tests corrected to check their intent.
- `v217_eval_v221_val.json` re-scored from its dump (verdicts only); stale file in `results/superseded/`.
- Technical reference: sag definitions, physical validation protocol, metric definitions, coverage, backup scope,
  restore test, security analysis, manuscript alignment table.

## 2026-11-18, session S1: release audit (commits ca08acb..c4fd63e)
- Removed 11 unused endpoints, dead code, unused CSS; list views without geometry; log level applied; dependency
  declarations fixed; thesis reference rewritten (20 sections); README rewritten; benchmark `benchmark_v223.json`.
