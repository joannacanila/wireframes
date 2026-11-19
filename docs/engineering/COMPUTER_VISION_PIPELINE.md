# Computer vision pipeline

Entry point: `app/pipeline/runner.py: run_analysis(rgb, cfg, segmenter, ctx, detections, envelope)`. The pipeline
does no storage: it takes a decoded image and returns an `AnalysisOutcome`. Persistence, queueing and multi-pole
ranking live in `app/services/`. All parameters come from `config/wireframe.yaml` (typed by `app/core/config.py`).
The "Section" and "Algorithm" numbers refer to Chapter III of the manuscript.

`docs/thesis/WIREFRAME_THESIS_REFERENCE.md` §6 describes every stage in more detail. It still matches the frozen
models and these stage parameters. The S15 additions are noted below.

## Stage order (as executed)

| # | Stage | Module | Key parameters (`config/wireframe.yaml`) | Timing key |
|---|---|---|---|---|
| 0 | Decode, EXIF orientation, 8-bit RGB, long side ≤ 2048 px; acquisition envelope from EXIF (Section 3.3.1) | `app/services/analysis.py: ingest`, `app/pipeline/acquisition.py` | `acquisition.*` (distance 5–15 m, elevation 30–60°, daylight 06–18 h, `min_side_px` 320) | — |
| 1 | Pre-processing (Algorithm 1): letterbox for segmentation; separate grey path for VNI | `preprocess.py`, `enhance.py` | `preprocessing.size` 640, `letterbox_fill` 114, `gaussian_kernel` 5; `segmentation.imgsz` 1024 | `preprocess` |
| 1a | Conditional enhancement (segmentation input only, never VNI) | `enhance.py` | `enhancement.mode: auto`. CLAHE on contrast / overexposure triggers (dark trigger off); NLM / bilateral denoise; blur-adaptive unsharp. Fallback re-run when ≥ 50 % of spans are unscored | — |
| 2 | Vegetation Noise Index (Algorithm 2, Equations 1–4) | `vni.py` | `vni.radius_factor` 0.15, `remove_mean: true`, `q01` 0.034001, `q99` 0.243397, bands 0.25 / 0.50 / 0.75 / 1.00 | `vni` |
| 3 | Segmentation (Algorithm 3): WireNet ensemble + YOLO vegetation + pole boxes + scene parser | `wirenet.py`, `segment.py`, `poles.py`, `scene.py` | `segmentation.wirenet_weights: models/v217_ensemble.json`, `wirenet_tta: flip`, `dual_polarity` (mean L* ≤ 40, ratio 1.2), `conf_min` 0.2, `iou_nms` 0.45, `pole_conf` 0.25 | `segmentation` |
| 3a | Span (strand) extraction from the WireNet class map | `wire_instances.py`, `continuation.py` | `span_wire_tau` 0.4, `span_min_px` 30, `span_max_turn_deg` 35, `span_cluster_radius` 5, `span_max_gap` 70, `span_max_lateral_px` 15, `span_min_stitched_len` 90 | — |
| 3b | Strand type: power vs telecom per strand | `strand_head.py` | `strand_head: models/strand_head_v217.pt`, `strand_head_min_conf` 0.5, `strand_head_clamp: true` (features clipped to the fitting range, S15 / D-31), `span_class_min_share` 0.9 | — |
| 3c | Scene gates: ground, structure edge, vegetation check (fail open with a flag) | `scene.py` | `ground_gate_share` 0.5, `structure_gate_contrast` 0.6, `vegetation_gate_min_share` 0.4 | — |
| 4 | Centerline (Algorithm 4): skeleton, spur pruning, longest path | `skeleton.py` | `centerline.l_min_px` 30, `n_min_points` 20 | `geometry_and_risk` |
| 5 | Catenary fit, chord, sag ratio, resolution (Algorithm 5, Equations 6–8) | `catenary.py`, `span_fit.py` | `catenary.max_rel_residual` 0.02, `border_margin_frac` 0.01, `kink_split: true` (S15 / D-32) | 〃 |
| 5a | Per-wire decision (status, reason code) | `runner.py: _score_wire` | `crs.require_both_endpoints: true`; `crs.delta_lim0: {}` (no CRS severity) | 〃 |
| 6 | Vegetation contact (image observation) | `runner.py: vegetation_contacts` | `vegetation.contact_margin_frac` 0.01, `min_contact_share` 0.02 | 〃 |
| 7 | Pole hazard verdict and reasons; loose-end candidates (off) | `hazard.py`, `loose_ends.py` | `hazard.vegetation_verdict: false`, `min_assessed_share` 1.0, `loose_ends: false` | 〃 |
| 8 | Reliability grade A / B / C / Retake; glare gate | `quality.py` | `quality.grade_a/b/c` 0.78 / 0.66 / 0.64, `min_wire_pixels` 500, `glare_gate` | — |
| 9 | Retake guardrail (reads the finished outcome; never changes it) | `guardrail.py` | `guardrail.*` | — |

Multi-pole ranking (Algorithm 6, MSPI, Equation 11) runs across stored analyses in `app/services/sites.py` with
`app/pipeline/mspi.py`. Its weights are α = β = γ = 1/3.

## Terminal statuses (`AnalysisOutcome.status`)

| Status | Meaning |
|---|---|
| `complete` | At least one span was measured (catenary fitted), whether or not it could be compared or assessed |
| `no_wires_detected` | No wire above the confidence threshold |
| `fit_failed` | Wires were detected, but none could be measured |

An `error` status (stored by `app/services/analysis.py: _fail`) records a processing failure with a category and stage.

## Per-wire status (`WireOutcome.status`)

`scored` · `not_assessed` · `too_short` · `fit_failed` · `service_drop` (disabled: `max_span_inclination_deg: 0`). The
reason codes are listed in [SAG_MEASUREMENT_AND_ASSESSMENT.md](SAG_MEASUREMENT_AND_ASSESSMENT.md).

## Image-level flags (`AnalysisOutcome.flags`)

These are set in `run_analysis`:

* `vni_uncalibrated`, `vni_outside_calibration_range`, `confidence_threshold_provisional`, `enhanced:<step>`;
* `polarity_inverted`, `strand_head_unavailable`, `ground_gate_unavailable`, `ground_structures_suppressed`,
  `structure_edges_suppressed`, `non_vegetation_suppressed`;
* `enhancement_mismatch`, `vegetation_contact`, `lens_outside_envelope`, `loose_end_candidate`;
* fallback flags from `finalize_detections`.

When the 35 mm-equivalent focal length is below the capture envelope (`wide_lens`), every scored wire is flagged
`lens_outside_envelope`, and a NOT AT RISK verdict becomes INCONCLUSIVE with a retake note. CRS values are still
stored for audit.

## Reference-annotation mode

When `detections` (reference polygons) are passed, the segmenter, enhancement, scene gates, pole boxes and guardrail
are bypassed, and `segmentation_source = "annotation"`. This is the validation mode used before the weights are
installed, and it is used by the evaluation scripts.

## Output provenance

* `timings_ms` per stage.
* `segmentation_source`.
* `mask_stats` (WireNet class-map coverage by spans).
* The suppressed spans, with reason codes.
* Inferred (bridged) pixels per span, drawn dashed in the viewer as "Estimated, not detected".
* The full WireNet class mask, served as an indexed PNG (`GET /api/analysis/{id}/wiremask.png`).
