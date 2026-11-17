# Implementation Changelog

Newest first. Each entry: task, original behaviour, issue, change, files, source, tests, results, risks, commit.

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
