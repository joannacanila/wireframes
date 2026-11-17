# Implementation Changelog

Location: `docs/thesis/` (moved from `docs/handoff/` in S5). Newest first. Each entry: task, original behaviour, issue, change, files, source, tests, results, risks, commit.

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
