# Chapter 4 (continued) — v2.11: Dense and Night Wire Detection, and the Field Inspection Console

Branch `v2.11-field-ui`, from `7cdb244` (v2.10.1). No model was retrained: the WireNet weights (`ab2353c7`) and the
YOLO detector are unchanged, and no new checkpoint was produced. Rules were derived on the 22 validation photos.
The test split was used once, at the end (§4.13.5).

| Mission | What was found | Outcome |
|---|---|---|
| Dense-wire coverage | On the dense night pole P-D67F60 the bottleneck was **WireNet, not the span assembler**: the model saw ~10k wire pixels on a photo with hundreds of visible conductors, and the spans covered 74% of what it saw. The cause was **polarity**: at night, street-lit conductors are lighter than the sky, while every training photo shows dark wires on a bright sky. | **Dual-polarity inference** adopted. Synthetic night validation: tolerant mAP50 0.0051 → 0.0312. Daytime validation byte-identical. P-D67F60: 12 → 27 spans (8 → 18 measured). |
| Recall post-processing | Hysteresis thresholding of the wire probability raises pixel recall but merges neighbouring wires. | **Rejected**: tolerant mAP50 0.0397 → 0.018–0.028 at every setting. |
| External data | No open dataset of ground-level urban distribution poles with wire masks passes the licence and viewpoint gates. | **No fine-tuning**; `models/wirenet-r34.pt` kept. |
| Expose everything detected | — | The full WireNet class mask is served and drawn as a layer. Every span, measured or not, is listed with a reason code and a plain-language reason. Mask coverage statistics are reported. |
| Field console | — | `/workspace` rebuilt as a daylight inspection console (§4.13.6). Checked in headless Chrome on 5 photos and at phone width. |

---

## 4.13.1 Diagnosis on the dense night pole (P-D67F60, a user upload, 640 × 480)

| | v2.10 | v2.11 |
|---|---|---|
| WireNet wire pixels | 9,694 | **23,464** (2.4×) |
| Share of wire pixels inside extracted spans | 74% | 82% |
| Spans / measured spans | 12 / 8 | **27 / 18** |
| Unmeasured spans with a stated reason | – | 9 / 9 (5 chord < 100 px, 4 not one hanging curve) |

Rendering WireNet's full mask on the photo showed the issue directly. On the photo as uploaded, the model marked
short blobs on a handful of conductors. On the same photo with CIELAB L* inverted, so the wires become dark on a
light sky, it traced dozens of thin continuous conductors, including the upper-right group it had missed entirely.
No change to the span assembler can recover wires the model never marked. The fix had to be at the input.

## 4.13.2 Dual-polarity inference (`app/pipeline/wirenet.py`, `ml/experiments/polarity_eval.py`)

**Rule.**
- For a dark photo (mean L* of the photo content ≤ 40), WireNet also runs on the photo with L* inverted. Chroma is
  kept, and the letterbox padding stays at its training value.
- The inverted pass is used when it finds at least 1.2× the wire pixels of the normal pass.
- The choice is recorded as the flag `polarity_inverted`, and the uncertainty and reliability grade come from the
  pass that was used.
- Photos brighter than the gate pay nothing extra.

**Validation design.** Validation has no labelled night photos, so a synthetic **night** condition was added to the
validation-only control set (`ml/stress.py`): L* inverted, compressed to a dark range (×0.55) and given sensor noise
(σ = 4). This tests the mechanism with real labels, but **the night photos are synthetic**. The only real night
evidence is P-D67F60 above, which has no labels.

Per validation photo and condition, the normal and inverted passes were compared (`v211_polarity_val.jsonl`):

| Condition (22 photos each) | Inverted/normal wire pixels | Tolerant IoU, normal pass (power / telecom) | Tolerant IoU, inverted pass |
|---|---|---|---|
| clean | max 0.75 | 0.528 / 0.422 | 0.220 / 0.166 |
| lowlight | max 0.71 | 0.512 / 0.368 | 0.167 / 0.074 |
| glare, motion, defocus, jpeg, lowres, rainhaze | max 0.92 | – | always far lower |
| **night (synthetic)** | **min 1.53**, median 3.39 | 0.185 / 0.132 | **0.495 / 0.401** |

The two populations are separated by a wide gap: daytime ≤ 0.92 and night ≥ 1.53. The switch ratio 1.2 is
their geometric midpoint, 1.18, rounded. At that value it switches 22 of 22 night cases and 0 of 176 daytime
cases.

The brightness gate (L* ≤ 40) lies between the night maximum (29.7) and the normal-daytime minimum (45.5).
Darkened daytime photos (lowlight, from 16.9) do run the check, and the ratio rule correctly keeps their normal
pass.

**Span level, the full screener pipeline** (validation, `v211_spans_val.jsonl`):

| Condition | v2.10 strict / tol. mAP50 | v2.11 strict / tol. mAP50 | v2.11 power tol. AP50 / R | v2.11 telecom tol. AP50 / R |
|---|---|---|---|---|
| clean | 0.0068 / 0.0394 | 0.0068 / 0.0394 (identical) | 0.107 / 0.213 | 0.022 / 0.088 |
| lowlight | 0.0006 / 0.0214 | 0.0006 / 0.0214 (identical) | 0.060 / 0.197 | 0.025 / 0.088 |
| **night** | 0.0051 / 0.0051 | **0.0060 / 0.0312** | 0.085 / 0.204 (was 0 / 0) | 0.020 / 0.106 (was 0 / 0) |

The whole-pipeline golden check (v2.10 §4.12.3) is still identical to v2.9 on all 22 clean validation photos: the
gate never fires on them. **The anti-regression rule holds by construction, and it was confirmed by measurement.**
The corroboration rule's p0 was measured on daytime spans and is unaffected. For a night photo, the spans come from
the inverted pass and the same rule is applied to them.

**Cost.** A dark photo pays one extra pair of WireNet passes (flip TTA). The rule decides the polarity for the
whole photo. A scene that mixes lit and unlit regions is handled by whichever polarity finds more wire overall;
per-region selection is future work.

**What is not fixed.** Vegetation comes from YOLO, which runs on the photo as uploaded and has never been
validated at night. On P-D67F60 it marks part of the black sky as vegetation, which produces telecom "vegetation
contact" lines. This is reported as a limitation, not corrected.

## 4.13.3 Recall post-processing: hysteresis (`ml/experiments/hysteresis_sweep.py`, `v211_hysteresis_val.jsonl`)

Pixels whose total wire probability is ≥ *low* and 8-connected to a pixel ≥ *high* were relabelled as wire. Scored
on the cached v2.8 validation probabilities with the v2.8 span constants:

| low / high | Tolerant mAP50 | Strict mAP50 | Pixel recall (2 px), all / 6 densest photos | Power R | Telecom R |
|---|---|---|---|---|---|
| argmax (v2.10) | **0.0397** | 0.0068 | 0.801 / 0.775 | 0.213 | 0.088 |
| 0.4 / 0.5 | 0.0284 | 0.0069 | 0.835 / 0.817 | 0.175 | 0.063 |
| 0.3 / 0.5 | 0.0241 | 0.0060 | 0.861 / 0.847 | 0.150 | 0.057 |
| 0.25 / 0.5 | 0.0232 | 0.0060 | 0.875 / 0.864 | 0.143 | 0.048 |
| 0.2 / 0.5 | 0.0184 | 0.0061 | 0.890 / 0.882 | 0.124 | 0.048 |
| 0.3 / 0.6 | 0.0241 | 0.0060 | 0.859 / 0.845 | 0.150 | 0.057 |
| 0.2 / 0.4 | 0.0181 | 0.0061 | 0.893 / 0.885 | 0.121 | 0.045 |

Every setting raises pixel recall, by up to 9 points, including on the densest photos. Every setting also lowers
span recall and mAP. The extra pixels fill the gaps *between* neighbouring wires, so the skeleton merges bundles
into fewer, wrong spans. This is the third time in this project that pixel accuracy and span accuracy pointed in
opposite directions: YOLO masks in v2.7, the motion router in v2.9, and now hysteresis. **Rejected.**

The other post-processing ideas in the directive were already settled on validation and were not re-run:
- **Multi-angle collinear gap bridging:** swept in v2.8 §4.8.1 (gap angle 10–35°, gap 0–150 px).
- **Recovering spans below 100 px for measurement:** the 100 px chord gate is a measurement-precision bound
  (σ/L ≤ ¼ δ_lim, v2.8 §4.6.2). Sub-gate spans are therefore not measured, but they are now **shown** with that
  reason (§4.13.4).

## 4.13.4 Exposing everything the model detected

- **The full WireNet class mask** at the photo's resolution is stored as an indexed PNG (transparent background)
  and served at `GET /api/analysis/{id}/wiremask.png`. It uses the same capability rule as the photo, and it is
  deleted with the analysis.
- **Coverage statistics** come with the result (`wire_mask`): pixels per class, wire pixels, wire pixels inside
  extracted spans, and `span_coverage`. Pixels outside every span are fragments too short or too broken to become
  one. They are now visible in the "Full WireNet mask" layer instead of being silently dropped.
- **Every span is listed.** Each unmeasured span carries a stable `reason_code` and a plain-language `reason`
  (`app/services/reasons.py`):

  | reason_code | Text shown |
  |---|---|
  | `chord_below_precision_gate` | Chord under 100 px: too short to measure sag reliably |
  | `residual_too_large` | Does not follow one hanging curve (likely crossing or merged wires) |
  | `non_positive_a` | Curves upward, not like a hanging wire |
  | `no_convergence` / `degenerate` / `too_short` / `empty_mask` | Fit did not converge / baseline too straight or short / too few centreline points / empty mask |

  Measured spans carry notes ("touches the photo edge", "vegetation touches this wire") and their per-span
  vegetation clearance (pixels, plus approximate metres ± uncertainty).
- **Telemetry** (`telemetry`): model, weights, inference time, resolution, config fingerprint. The parameters now
  also include the corroboration rule (p0, α) and the chord gate, so the UI can show the statistics instead of
  restating them.

The hazard dictionary itself is unchanged, so verdicts are unchanged (whole-pipeline golden: identical).

## 4.13.5 Test split, evaluated once (`v211_spans_test.jsonl`)

21 test photos, full screener pipeline, span level:

| Condition | v2.10 strict / tol. mAP50 | v2.11 strict / tol. mAP50 | v2.11 power tol. AP50 / R | v2.11 telecom tol. AP50 / R |
|---|---|---|---|---|
| clean | 0.0204 / 0.0469 | 0.0204 / 0.0469 (identical) | 0.105 / 0.225 | 0.004 / 0.050 |
| lowlight | 0.0053 / 0.0222 | 0.0053 / 0.0222 (identical) | 0.065 / 0.170 | 0.003 / 0.033 |
| **night (synthetic)** | 0.0089 / 0.0090 | **0.0091 / 0.0313** | 0.088 / 0.217 (was 0.000 / 0.000) | 0.001 / 0.025 (was 0.000 / 0.003) |

The test split confirms validation. Clean and low light are unchanged, and synthetic night recovers to roughly
the daytime clean level (0.0313 against 0.0469). The power-line gain carries over in full: recall goes from
0 to 0.217.

**Telecom is the weak point at night.** On test its recall rises only to 0.025, against 0.106 on validation.
Telecom cables are thinner and lower in contrast, and on these test photos inversion recovers them far less.

The run was interrupted once and finished with a second process, so some conditions were computed twice. The
duplicate rows were identical and were removed. Per-image times are not reported because the two processes
overlapped.

## 4.13.6 The field inspection console (`app/web/field.html`, `field.css`, `field.js`, `viewer.js`)

**Design.**
- Slate canvas (#F4F6F8), white panels with 1 px #CBD5E1 borders and 4 px corners.
- Solid status banners instead of glowing dots, and IBM Plex Sans with tabular IBM Plex Mono numerals.
- Scoped to the field page (`html.fs-root`); the admin portal is unchanged.

**Layout.**
- A 58 / 42 master–detail split. The viewer stays in view (sticky) while the right panel scrolls.
- On screens ≤ 1100 px the order becomes action first: retake → hazards → verdict → viewer → table.

**Viewer.**
- Layer toggles: Extracted spans (numbered), Full WireNet mask (with its pixel count), Vegetation encroachment,
  Original photo. Zoom −/+/Fit; wheel and drag still work.
- A telemetry bar replaces "detected by model": model, inference time, resolution, reliability grade, and a legend
  that changes with the active layers.

**Right panel, in priority order.**
1. **Retake banner** whenever the grade is below A, giving the reason in field language, with a
   "Retake / upload new photo" button.
2. **Active physical hazards**, one line per kind (corroborated critical sag, vegetation contact on power or
   telecom, vegetation near power), with clickable span numbers.
3. **Executive verdict.** One sentence per driver, no repetition. Three KPI tiles: worst CRS, detected / measured
   spans, scene clutter (VNI).
4. **Inspected spans table**, grouped High / Moderate / Low / Unmeasured, with filters and counts. Columns: ID,
   conductor, sag δ/L with CRS, vegetation clearance, status. Unmeasured rows always show their reason.
5. **Statistical and algorithmic diagnostics** (collapsed): the corroboration arithmetic ("5 of 18 measurable spans;
   at least 4 needed above the 5.0% noise floor, α = 0.05"), the NOTE lines, mask coverage, image quality (R, U, D,
   L*, contrast, near-white share, thin-line contrast, sharpness, noise, motion coherence), polarity, stage timings,
   weights and config hash.

**Two-way linking.**
- Clicking a table row selects the wire and zooms the canvas to it; hovering a row highlights it.
- Clicking a wire on the canvas selects and scrolls to its row.
- Span numbers in the hazard list jump to the wire.

**Verification in a real browser** (`scripts/ui_check.py`; Playwright driving Chrome 157 headless;
`ui_check_v211.json`, screenshots in `runs/ui_shots/`).

| Photo | Verdict shown | Spans (measured) | Console / page errors | Toggles | Table→canvas | Canvas→table | PDF |
|---|---|---|---|---|---|---|---|
| P-D67F60 (night, dense) | AT RISK – HIGH | 27 (18) | 0 | 4 / 4 | ✓ | ✓ (clicked wire 1, selected 1) | ✓ |
| 1_clean_not_at_risk | NOT AT RISK | 38 (34) | 0 | 3 / 3 (no vegetation) | ✓ | ✓ | ✓ |
| 2_at_risk_high | AT RISK – HIGH | 56 (49) | 0 | 3 / 3 | ✓ | ✓ | ✓ |
| 3_low_light | NOT AT RISK | 34 (21) | 0 | 4 / 4 | ✓ | ✓ | ✓ |
| 4_glare_degraded | INCONCLUSIVE – RETAKE PHOTO | 6 (5) | 0 | 3 / 3 | ✓ | ✓ | ✓ |
| Phone width (390 px) | – | – | 0 | – | – | – | no horizontal overflow |

The canvas→table check clicks the screen position of a wire's own centreline pixel, computed from the viewer
transform, and checks that the same wire's row is selected.

## 4.13.7 External datasets (searched 2026-11-15; `ml/sources.yaml`)

| Candidate | Why it does not qualify |
|---|---|
| WireSegHR (CVPR 2023) | Only 420 of 500 test images are released. **No licence is stated** anywhere: the repository has no LICENSE, and the paper says only "copyright-free". One generic binary wire class (no power / telecom split). |
| Merged Public Power Cable Dataset (2025) | UAV / aerial viewpoint of transmission cables |
| Railway OCS conductor segmentation (2025) | Railway catenary from train video |
| Street-view power-line classification (arXiv 2105.03804) | Classification only; the data was not released |

No source passes the gates (polygon or pixel wire masks, ground level, distribution poles, an allowed licence),
so no candidate checkpoint was trained. `models/wirenet-r34.pt` (`ab2353c7`) remains the model. Labelled night
photos of the team's own poles would be the most valuable addition: they would replace the synthetic night
control with real evidence.

## 4.13.8 Limitations

- **The night improvement is measured on synthetic night photos.** On the one real night photo the qualitative gain
  is large, but it has no labels.
- **YOLO vegetation at night is unvalidated** and produces false vegetation in dark sky (§4.13.2).
- **The polarity is chosen per photo, not per region.**
- **Latency on dark photos** includes two extra WireNet passes.
- **Coverage is not accuracy.** `span_coverage` says how much of what the model saw became spans, not how much of
  what is really there.

## 4.13.9 Reproduction

```
python -m ml.experiments.polarity_eval --out docs/thesis/results/v211_polarity_val.jsonl
python -m ml.experiments.hysteresis_sweep --out docs/thesis/results/v211_hysteresis_val.jsonl
python -m scripts.stress_test_all_conditions --split val --out docs/thesis/results/v211_spans_val.jsonl --conditions clean lowlight night --configs v2.10 v2.11
python -m ml.experiments.pipeline_golden --check docs/thesis/results/v210_pipeline_golden_v29_val.json
python -m scripts.stress_test_all_conditions --split test --out docs/thesis/results/v211_spans_test.jsonl --conditions clean lowlight night --configs v2.10 v2.11
python -m scripts.ui_check --base http://127.0.0.1:8000 --out docs/thesis/results/ui_check_v211.json [photos]
```
