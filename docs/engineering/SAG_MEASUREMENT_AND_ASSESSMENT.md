# Sag measurement and assessment

This document describes what the released system (45873c3) measures, how a measurement is compared with a reference,
and what is never claimed. The behaviour comes from `app/pipeline/catenary.py`, `app/pipeline/runner.py`,
`app/services/comparison.py`, `app/services/screening.py`, `app/services/analysis.py` and `config/wireframe.yaml`.
The decisions behind each rule are cited (D-xx = `docs/thesis/RESEARCH_AND_DECISION_REGISTER.md`).

> **Scope statement.** Every ratio is **image-derived**: sag over the chord between the fitted curve's end points in
> one perspective photograph. It is not a surveyed pole-to-pole distance or a physical sag (D-18). Every
> classification is a **screening result**, never a compliance, safety or engineering finding.

## 1. Measurement (per span)

1. **Catenary fit** (Equations 6–8): `y = a·cosh((x − b)/a) + c`, fitted to the centerline (`catenary.py`).
   * The fit is rejected when the RMS residual over the chord exceeds `catenary.max_rel_residual` (0.02), or when it
     does not converge, gives a non-positive `a`, or is degenerate.
   * S15 (D-32) kink-aware fit (`app/pipeline/span_fit.py`, `kink_split: true`): from a relative residual of 0.004,
     a two-line fit is tried. When its RMS is below 0.5 × the catenary RMS, the trace is kinked rather than sagging.
     It is split at the break, and the longest piece that fits as one curve becomes the measurement, flagged
     `kink_split` with the share it leaves out. If no piece fits, the span is `kinked_trace` (not measured).
2. **Chord and sag** (thesis reference §7.2):
   * `L = √((x₁ − x₀)² + (y(x₁) − y(x₀))²)` and `m = (y(x₁) − y(x₀)) / (x₁ − x₀)`.
   * The maximum sag is at `x* = b + a·asinh(m)`, where `δ = |y_chord(x*) − y(x*)| / √(1 + m²)`.
   * The **sag ratio** is `δ / L`. When `x*` falls outside `[x₀, x₁]`, the sag is 0.
3. **Resolution** (S4, D-09): one model pixel over the chord, `max(scale, 1) / L`. Here `scale = max(width,
   height) / segmentation.imgsz`, and it is 1 for reference annotations (`analysis.py: _resolution`). It is stored
   with every measured ratio.
4. **Calibrated 90 % band** (S16, D-34), from `analysis.py: uncertainty90`:
   * `u = a_px × resolution + b + c × rel_residual`, with `a_px` 0.05804, `b` 0, `c` 3.30469 and quantile 0.9
     (`config: measurement_uncertainty`).
   * The fit is in `ml/experiments/s16_uncertainty.py` and `results/s16_uncertainty_residual.json`. Held-out test
     coverage is 0.889; ± 1 px alone gives 0.689 (`results/s16_uncertainty_calibration.json`).
   * The band is shown beside the classification. It never moves a wire between classes.

## 2. Per-wire decision (`runner.py: _score_wire`)

The first condition that applies decides the outcome.

| # | Condition | Status | Reason code | What is kept |
|---|---|---|---|---|
| 1 | Centerline < 20 points | `too_short` | `too_short` | — |
| 2 | Catenary fit fails | `fit_failed` | `no_convergence`, `non_positive_a`, `residual_too_large`, `degenerate` | — |
| 3 | An endpoint is at the photo border (`require_both_endpoints`) | `not_assessed` | `endpoint_unavailable` | sag ratio and curve (`measured_partial`) |
| 4 | Strand type uncertain | `not_assessed` | `class_uncertain` | sag ratio; no comparison (D-06, D-27) |
| 5 | No CRS reference for the class (`crs.delta_lim0: {}`, so every class) | `not_assessed` | `no_validated_reference` | sag ratio, resolution, curve |

No wire receives a CRS severity level: `crs.delta_lim0`, `crs.levels` and `crs.assessed_classes` are empty (S4, D-02
to D-04). Both the former 4.0 % telecom placeholder and the 2.4 % minor band are withdrawn.

## 3. Reference comparison (computed on read; `comparison.py: compare_wire`)

Each measured span (`measured` or `measured_partial`) is compared with the one reference that covers its class.
Power and telecom never share a reference (S15, D-31). No comparison is made for a service drop, a class without a
reference (`abandoned-wire`), or an uncertain type.

The classification uses the **unrounded point estimate** (S17, D-37), via `classify(ratio, lower, upper)`:

* `above` when `ratio > upper`;
* `below` when `ratio < lower`;
* `at` otherwise (both boundaries of the middle band are inclusive).

| | Power lines (`comparison_reference`) | Telecom / fibre (`telecom_reference`) |
|---|---|---|
| Version | `PWR-2026.11-S17` | `TEL-2026.11-S17` |
| Classes | `power-distribution-lines` | `telecom-cable` |
| Status | `regulatory`: ERB Res. 95-21 (1995) s.19, "3 % of the distance between poles". ERC Res. 02 s.2010 recites that the DSOAR superseded it, and the DSOAR text was not obtained. Used by owner decision (D-26, D-27, D-37) | `guideline`: FOA aerial workmanship (< 2 %), JEA OH-VI-1 ADSS design sags, ZTO Cable (2–5 %), GL Fiber (D-31) |
| Lower boundary | 0.02, **descriptive** subdivision with no regulatory meaning | 0.02 (FOA guideline) |
| Upper boundary | 0.03 (ERB 95-21 s.19). Exactly 3.00 % is within | 0.05 (manufacturer typical maximum) |
| below | `WITHIN_LOWER_REFERENCE_RANGE`, "Within lower reference range" | `BELOW_TYPICAL_RANGE`, "Below typical range" |
| at | `WITHIN_REFERENCE_THRESHOLD`, "Within reference threshold" | `WITHIN_TYPICAL_RANGE`, "Within typical range" |
| above | `REFERENCE_THRESHOLD_EXCEEDED`, "Reference threshold exceeded" | `ABOVE_TYPICAL_RANGE`, "Above typical range" |
| Attention zones | `[above]`, which counts as a problem wire `sag_exceeded` | `[]`: never "needs attention" (D-34) |
| Notable zones | `[]` | `[above]`, which counts as a problem wire `sag_beyond_typical` |
| Meter scale | 0–10 % | 0–10 % |

For power lines only, the comparison also reports `crs_index = min(10, 250 × ratio)` (`CRS_SCALE`), plus
`difference_pp` (ratio minus reference, in percentage points). `uncertainty_overlaps` is true when the band
`ratio ± u` contains a boundary.

The comparison is **not stored**. Historical records are compared on read with the reference configured now, and the
views say so.

## 4. Pole verdict (`app/pipeline/hazard.py`)

| Factor | Rule (released configuration) |
|---|---|
| Sag | No span carries a CRS level, so sag gives no AT RISK verdict (`sag_rule: corroborated` and `p0_*` are dormant, D-10) |
| Vegetation | Overlap or proximity in the image is a NOTE for on-site review, never AT RISK (`vegetation_verdict: false`, D-15) |
| Coverage | NOT AT RISK requires every measured span to be assessed (`min_assessed_share: 1.0`, D-08). A span that was not assessed is unknown, not safe |
| Reliability | Grade Retake with no hazard → INCONCLUSIVE |
| Lens | An ultra-wide lens (35 mm-eq. below the envelope) turns NOT AT RISK into INCONCLUSIVE, with a retake note |

The stored verdict is therefore INCONCLUSIVE ("Assessment incomplete") for new analyses (D-19). The pole-level
*screening* result shown to users comes from the reference comparison of its spans (`comparison.py: pole_comparison`)
and the problem-wire count below.

## 5. Problem wires and triage risk (`app/services/screening.py`, S18, D-38)

A wire is a problem wire only for a supported, wire-specific observation:

* `sag_exceeded`: power above 3 %;
* `sag_beyond_typical`: telecom above 5 %;
* `possible_dangling_end`: a loose-end candidate; off in the released configuration;
* `vegetation_contact`;
* `apparently_abandoned`: a visual class, never "dead".

Tangling, unbundling and low clearance are **not detected** and are never inferred. An administrator can record them
in a decision.

The risk is an **operational triage rubric, not a statutory classification**:

* 0 problem wires → normal;
* 1–9 → moderate (`moderate_from` 1);
* 10 or more → high (`high_from` 10).

A critical hazard confirmed by an administrator in the current decision overrides the count. A low-clearance hazard
needs a physical measurement and a documented reference. The rubric is stored in `app_settings["risk_rubric"]`,
versioned, validated (`validate_rubric`) and audited.

## 6. Historical records (D-21, D-23)

Results stored before S4 are never reprocessed or modified. On display, a stored level is withdrawn
(`withdrawn_reference`, with the values kept under `historical`) when its class has no verified reference or the
record lacks the current `assessment_basis` (`S4-2026-11-18`, `app/services/basis.py`). A stored verdict other than
INCONCLUSIVE is shown as INCONCLUSIVE with a notice. Stored CRS values are not used for ranking.

## 7. What WIREFRAME does not claim

* Physical sag, tension, clearance to ground or other conductors, or conductor temperature. The change-of-state
  relation (Equation 9) is unavailable because `engineering.conductors: {}` (D-14).
* Compliance with any regulation, or the safety of a span.
* Vegetation contact in 3-D: a 2-D overlap is not contact (D-15).
* A type for an abandoned wire, or a reference for one (D-05).
