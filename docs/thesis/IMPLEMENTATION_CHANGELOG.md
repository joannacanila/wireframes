# Implementation Changelog

Location: `docs/thesis/` (moved from `docs/handoff/` in S5). Newest first. Each entry: task, original behaviour, issue, change, files, source, tests, results, risks, commit.

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
