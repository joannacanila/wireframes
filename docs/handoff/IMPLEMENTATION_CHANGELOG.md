# Implementation Changelog

Newest first. Each entry: task, original behaviour, issue, change, files, source, tests, results, risks, commit.

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
