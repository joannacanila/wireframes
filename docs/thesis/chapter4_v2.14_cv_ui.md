# 4.16 v2.14: wire-geometry provenance, span ownership, inspection console redesign

WireNet-R34 (`models/wirenet-r34.pt`, `ab2353c7`) is unchanged and no model was trained. The CRS formula, the
corroborated hazard rule (p0 0.050 / 0.090, α = 0.05), the 100 px chord gate and the reliability grade are
unchanged. All runs were on the 8 GB Apple Silicon M1.

## 4.16.1 What was wrong

The field screener was reviewed on pole P-B1D137 (a dense night photo, 640 × 480, 27 spans, AT RISK – HIGH).
The complaint was "fragmented, disconnected purple line segments floating around the image". The pipeline was
traced end to end, and three separate causes were found.

1. **The purple fragments are WireNet's raw output, drawn honestly.** The "Full wire mask" layer draws WireNet's
   per-pixel class map, and telecom pixels are purple (156, 54, 181). On this photo the model's argmax really is
   broken into pieces: 23,464 wire pixels, of which 18 % lie outside every span. Drawing them as continuous lines
   would invent detections, so v2.14 keeps them as their own clearly labelled layer.
2. **Span masks contained pixels the model never detected.** The span assembler (v2.8 `stitch_spans`, FINAL:
   70 px gap, 25° tolerance) bridges a gap by drawing a straight 2-px line into the span mask. That line then
   appeared in the overlay as if it were segmentation. On P-B1D137, 1,299 span pixels are such bridges. Four of
   the five high-risk spans contain them (span 14: 20 % of its traced path; span 26: 17 %), and unmeasured spans
   contain up to 35 %.
3. **Pixels of a separate cable were assigned to the nearest span.** Step 5 of `spans_from_class_mask` hands every
   mask pixel to its nearest skeleton piece, including pixels in a different, disconnected mask blob whose own
   pieces were dropped. A neighbouring cable could therefore be painted into a span.

A further rendering defect: centerlines, fitted curves and end points are pixel indices but were drawn at pixel
corners, so they sat half a pixel up and to the left of the mask at every zoom.

## 4.16.2 What changed

**Provenance (API, additive).** Every wire in `/api/analysis/{id}/result` now carries `provenance`, computed on
read from the stored class mask (`app/services/provenance.py`), so analyses made before v2.14 get it too. It holds:
`observed_px`, `inferred_px`, `inferred_segments` (the bridged gaps as line segments), `inferred_polygons` and
`centerline_inferred_share`. The value is `null` for reference-polygon (validation-mode) analyses. Inferred pixels
stay in the measurement, so CRS is unchanged, but they are never drawn as detections. Wires also gained
`reason_short` (for example "Insufficient visible length", "Ambiguous wire path"); the full `reason` is unchanged.

**Span ownership (`span_own_component: true`).** A mask pixel may now join only a span in its own 8-connected mask
component. Results:

| Measure | Before | After |
|---|---|---|
| Validation span tolerant mAP50 | 0.035557 | 0.035554 |
| Validation power / telecom recall | — | identical |
| Verdicts changed (22 validation photos + P-B1D137) | — | 0 of 23 |
| Photos with changed per-span measurements | — | 10 of 23 |

The changed measurements come from spans being split or merged differently where a detached blob had been pulling
them together. On P-B1D137 every measurement is identical. The p0 noise floors were not re-derived, because no
verdict changed (`docs/thesis/results/v214_own_component_val.json`).

**Rendering.**
- Span masks are drawn as fill plus a 1-image-px stroke, which reproduces the pixel mask exactly; a 1–2 px wire's
  contour has almost no area, so fill alone vanished.
- Inferred pixels are cut out of each span mask and drawn as a dashed line.
- All line geometry is offset by +0.5 px onto pixel centres. Checked at 8× zoom on P-B1D137: centerlines and masks
  coincide.

## 4.16.3 Corrections tested and rejected

Both were measured on validation with the shipped extractor and the frozen R34. The cache used here gives a
shipped tolerant mAP50 of 0.0356 rather than the 0.0397 recorded for v2.8 (a different `runs/cache/val`); every row
uses the same cache, so the comparisons hold.

| Gate on a bridge | Bridges | Tolerant mAP50 | Decision |
|---|---|---|---|
| None (shipped) | 507 | 0.0356 | kept |
| WireNet probability along the gap ≥ 0.02 / 0.05 / 0.10 | 507 / 507 / 504 | 0.0356 | no effect |
| Probability ≥ 0.15 / 0.20 | 479 / 456 | 0.0328 / 0.0322 | rejected |
| Probability ≥ 0.30 | 374 | 0.0353 | rejected (no gain) |
| Lateral offset ≤ 3 / 5 / 8 / 12 px | 213 / 306 / 382 / 432 | 0.0325 / 0.0339 / 0.0351 / 0.0344 | rejected |
| Ownership + lateral ≤ 5 px | 308 | 0.0339 | rejected |

Almost every bridge already crosses a gap where WireNet's probability is at least 0.10, just below its argmax
threshold, so the bridges are weakly supported rather than invented. Requiring a straighter continuation (the
lateral gate) removes bridges that the labels confirm. Files: `v214_bridge_evidence_val.json`,
`v214_bridge_lateral_val.json`.

**Limitation that remains.** On P-B1D137, span 14 (CRS 10.0) still includes a fragment of the cable about 20 px
below it. In WireNet's mask the two cables are connected through the crossing tangle, so component ownership does
not separate them, and the gates that would cut the connection lose accuracy on validation. Span 14's fit follows
the upper cable; the lower fragment affects only its drawn mask. Separating crossing cables in a tangle this dense
needs topology-aware segmentation or labelled crossings, which is not attempted without retraining.

## 4.16.4 Inspection console (`/workspace`)

The interface was redesigned around the inspection workflow: warm-white light theme by default, charcoal type,
grey hairlines, flat panels (no glass, gradients or glows), Geist with tabular numerals, and colour used only for
status (red high, amber moderate, green low, grey not measured). It contains, in reading order:

- **Inspection header**: pole code, "Analysis complete", date and time, file name, PDF report (secondary) and New
  inspection (primary). The top bar's server state reads "Model ready" only when `/api/health` reports the model
  loaded, and is re-checked every 20 s.
- **Outcome strip**: the verdict, the physical hazards with links to their spans, and the next step. Counts per risk
  level and the worst span's CRS are clickable to filter or select, and CRS has a definition on hover.
- **Viewer**, the largest element:
  - presets Original / Spans / Mask / Overlay, plus a Layers menu with independent toggles: segmentation pixels,
    span masks, inferred gaps, centerlines, fitted catenary, chord and sag, span ends, span numbers;
  - opacity, vegetation, zoom with a percentage readout, Fit, and Reset (fit, clear the selection, default layers).
  - "Span ends" are the traced ends, labelled as not pole attachment points, which are not detected.
- **Beside the viewer**:
  - the selected span in full: CRS, sag ratio against its limit, sag, chord, fit residual, inclination, vegetation
    clearance, model probability, and the inferred share, plus the full reason and next step;
  - the span table: span, cable, CRS, sag, vegetation, status with the short reason. It has filters, keyboard
    navigation, and selection kept in sync with the photo.
- **Inspection quality**: photo grade, image reliability R (labelled as not a detection confidence), measurement
  completeness, processing status, and the limitations that apply.
- **Technical diagnostics** (collapsed): sag corroboration, image quality metrics, segmentation statistics
  (including fragments outside spans and inferred pixels), ground structures ignored, model, weights, timings and
  config.

**Website only.** WIREFRAME is a desktop web application for windows at least 1024 px wide. The v2.13 phone layout,
the phone-width UI checks and the phone screenshots and figure were removed. Below 700 px the page shows only a
notice asking for a computer.

## 4.16.5 Verification

| Check | Result |
|---|---|
| `pytest` | 524 passed (8 new in `tests/test_v214_cv_ui.py`; the console test rewritten for v2.14) |
| `ruff check app ml scripts tests` | clean |
| `scripts/ui_check.py --mock` | 16 of 16 pass: four verdict states × light / dark × 1440 × 900 and 1024 × 768 |
| `scripts/ui_check.py`, real photos (P-B1D137, the v2.13 handrail photo, the night pole) | 3 of 3 pass |
| Table and page overflow at 1920, 1440, 1366, 1280, 1100 and 1024 px | none |

The mocked check covers every preset, the layers menu, opacity, zoom, Fit and Reset, the diagnostics drawer,
filters, the span detail reason and retake steps, and table↔photo selection both ways. The real-photo check also
covers the PDF download and the new-inspection upload, and found 0 console or page errors.

Results: `docs/thesis/results/v214_ui_check_mock.json` and `v214_ui_check_real.json`. Screenshots:
`runs/ui_shots_v214/`.
