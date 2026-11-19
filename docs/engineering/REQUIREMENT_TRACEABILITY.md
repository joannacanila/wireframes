# Requirement traceability

There is no separate requirements specification in the repository. The requirements traced here are the ones the
code and configuration themselves cite:

* the Chapter III methodology: Sections 3.3–3.6, Algorithms 1–6, Equations 1–11, Figures 3.4, 3.6 and 3.7;
* the engineering decisions D-01 … D-39;
* the security and data-handling rules stated in the module docstrings.

The test columns list the test modules that import, or call through the API, the implementing module. They were
found by searching the 45873c3 test suite.

## Methodology (Chapter III)

| Req. | Requirement (source) | Implementation | Configuration | Tests | Evidence |
|---|---|---|---|---|---|
| M1 | Acquisition envelope and objective quality check (§3.3.1, Fig. 3.6) | `app/pipeline/acquisition.py`, `services/analysis.py: ingest` | `acquisition` | `test_acquisition_preprocess`, `test_api`, `test_v210_ingestion`, `test_s16_heic`, `test_s17_heic_preview` | — |
| M2 | Pre-processing: letterbox, fixed Gaussian (§3.6.1, Algorithm 1) | `preprocess.py` | `preprocessing` | `test_acquisition_preprocess`, `test_wirenet`, `test_yolo_integration` | — |
| M3 | Enhancement for segmentation only, never VNI (§3.6.2) | `enhance.py` | `enhancement` | `test_enhance`, `test_blur_resilience`, `test_v28_robustness`, `test_v210_hardening` | `results/v213_*` |
| M4 | Vegetation Noise Index (§3.6.2, Eq. 1–4, Table 3.7, Algorithm 2) | `vni.py`, `ml/calibrate_vni.py` | `vni` (q01 / q99, D-17) | `test_vni`, `test_enhance` | calibration set named in config |
| M5 | Wire segmentation (§3.6.3, Algorithm 3) | `wirenet.py`, `segment.py`, `wire_instances.py` | `segmentation` | `test_wirenet`, `test_v212_ensemble`, `test_s17_segmentation`, `test_wire_instances`, `test_v216_strands` | `results/v217_*`, `s15_model_metrics_test.json` |
| M6 | One type per strand (power / telecom); uncertain types not assumed (D-06, D-31) | `strand_head.py` | `strand_head*`, `span_class_min_share` | `test_v217_strand_head`, `test_s15_model` | `s15_model_metrics_test.json` |
| M7 | Non-wire structures suppressed: ground, structure edges, non-vegetation | `scene.py` | `ground_gate`, `structure_gate`, `vegetation_gate` | `test_v213_ground_gate`, `test_v216_strands` | `results/v216_context_val.json` |
| M8 | Centerline extraction (§3.6.4, Algorithm 4) | `skeleton.py` | `centerline` | `test_skeleton`, `test_catenary_crs`, `test_s16_integrity` | — |
| M9 | Catenary fit, chord, sag ratio (§3.6.5, Eq. 6–8, Algorithm 5; D-01, D-32) | `catenary.py`, `span_fit.py` | `catenary` | `test_catenary_crs`, `test_s15_model`, `test_s16_integrity` | `results/s15_measure_*` |
| M10 | Allowable sag / CRS (Eq. 9–10) only with a validated reference (D-02 to D-04, D-14) | `crs.py`, `sag_limits.py`, `runner.py: _score_wire` | `crs` (empty), `engineering` (empty) | `test_catenary_crs`, `test_s4_thresholds`, `test_v221_ml` | register S4 |
| M11 | Both endpoints required for an assessment (D-07) | `runner.py: _score_wire` | `crs.require_both_endpoints` | `test_s4_thresholds`, `test_v221` | — |
| M12 | Vegetation proximity as an image observation, not contact (D-15) | `runner.py: vegetation_contacts`, `hazard.py` | `vegetation`, `hazard.vegetation_verdict` | `test_hazard_quality_batch`, `test_s4_thresholds` | — |
| M13 | Pole verdict; INCONCLUSIVE unless every measured span is assessed (D-08, D-19) | `hazard.py` | `hazard` | `test_hazard_quality_batch`, `test_s4_thresholds`, `test_v29_refinements` | — |
| M14 | Reliability grade and glare gate (D-16) | `quality.py` | `quality` | `test_hazard_quality_batch`, `test_v210_hardening` | `results/v210_glare_*` |
| M15 | Retake guardrail never changes results | `guardrail.py` | `guardrail` | `test_v217_guardrail` | — |
| M16 | Multi-pole priority index (§3.6.6, Eq. 11, Algorithm 6; D-20) | `mspi.py`, `services/sites.py` | `mspi` | `test_mspi`, `test_api`, `test_portals` | — |
| M17 | Asynchronous processing; re-enqueue after a restart (Fig. 3.6) | `services/queue.py` | `WIREFRAME_START_WORKER` | `test_api`, `test_s14_operations`, `test_s17_consistency` | — |
| M18 | Terminal statuses `complete`, `no_wires_detected`, `fit_failed` (Fig. 3.6 / 3.7) | `runner.py: run_analysis` | — | `test_taxonomy`, `test_hazard_quality_batch` | — |
| M19 | Evaluation metrics: centerline P/R/F1 at 3 px / 1024 (D-11) | `evaluation.py`, `centerline_eval.py`, `ml/evaluate.py` | — | `test_evaluation`, `test_v217_guardrail` | `results/` |

## Screening, records and display decisions

| Req. | Requirement (decision) | Implementation | Configuration | Tests |
|---|---|---|---|---|
| S1 | Power spans compared with the 3 % reference only (D-26, D-27) | `services/comparison.py` | `comparison_reference.classes` | `test_d27_reference_scope`, `test_s8_sag_reference` |
| S2 | One classification rule on the unrounded ratio, middle band inclusive (D-37) | `comparison.py: classify` | `lower_boundary`, `upper_boundary` | `test_s17_reference` |
| S3 | Separate telecom / fibre reference; never "needs attention" (D-31, D-34) | `comparison.py`, `outcome.py: needs_attention` | `telecom_reference` | `test_s15_model`, `test_s17_reference`, `test_s16_integrity` |
| S4 | Calibrated 90 % band, shown beside the class and never moving it (D-34) | `services/analysis.py: uncertainty90` | `measurement_uncertainty` | `test_s16_integrity`, `test_s17_reference` |
| S5 | Machine observations, screening indicators and decisions kept separate (D-38) | `screening.py`, `review_flow.py` | `app_settings` rubric and options | `test_s18_workflow` |
| S6 | Historical records never reprocessed; withdrawn levels on display (D-21, D-23) | `services/basis.py` | `BASIS` | `test_s5_diagnostics`, `test_s4_thresholds` |
| S7 | Diagnostic output for measured but unassessed wires (D-22) | `services/diagnostics.py` | — | `test_s5_diagnostics`, `test_s18_refinement` |
| S8 | Archive-based deletion; contacts deactivated, not deleted (D-39) | `api/admin.py` archive / restore, `api/operations.py` | — | `test_s18_refinement` |
| S9 | One result view for every route; the client computes no classification (S18) | `web/assets/result-view.js` | — | `test_s12_results_ui`, `test_s18_refinement`, `scripts/e2e_check.py` |
| S10 | HEIC / HEIF uploads (D-35) | `services/analysis.py`, `api/analysis.py: preview` | — | `test_s16_heic`, `test_s17_heic_preview` |

## Security, data handling and operations

| Req. | Requirement (source) | Implementation | Tests |
|---|---|---|---|
| X1 | Staff-only and admin-only routes; inspectors get 403 on system routes | `core/security.py: require_admin`, `require_system` | `test_v219_portal`, `test_s14_operations`, `test_s14_citizen`, `test_s18_refinement` |
| X2 | scrypt passwords, hashed session tokens, login throttling | `core/security.py`, `api/auth.py` | `test_portals`, `test_v219_portal`, `conftest` |
| X3 | Origin check on state-changing requests | `security.py: same_origin` | `test_portals` |
| X4 | GPS EXIF stripped from stored photos | `services/analysis.py: ingest` | `test_acquisition_preprocess`, `test_api`, `test_v210_ingestion`, `test_v221` |
| X5 | Public responses have no technical fields or internal error text | `public_view`, `public_error`, `scrub_paths` | `test_v219_portal`, `test_api` |
| X6 | Models downloaded with size and SHA-256 checks, never overwritten | `core/weights.py` | `test_v215_weights`, `test_s13_stability` |
| X7 | Additive migration with a backup first | `core/database.py: migrate` | `test_taxonomy` |
| X8 | Verified backup and restore (D-13) | `scripts/backup.py` | `test_s6_backup` |
| X9 | PDF report rebuilt on request with current ranking | `services/report_pdf.py`, `api/reports.py` | `test_api`, `test_portals`, `test_s8_sag_reference`, `test_v217_guardrail` and others |
| X10 | Every staff change audited | `services/audit.py` | `test_s18_workflow` |

## Gaps (recorded, not fixed)

* There is no formal requirements document, so the "requirements" above are the ones the methodology and the
  decisions state.
* `services/queue.py` and `services/report_pdf.py` have no dedicated unit test module. They are exercised through
  the API tests.
* Pole detection has no ground truth on WIREFRAME photos. Its evidence is the LVIS test only.
* Physical sag, clearance, tangling and unbundling are out of scope, so they have no requirement and no test (see
  [SAG_MEASUREMENT_AND_ASSESSMENT.md](SAG_MEASUREMENT_AND_ASSESSMENT.md) §7).
