# Chapter 4 (continued) — v2.8: Span Assembly, Hazard Interpretation, and Robustness

Branch `v2.8-overnight-sprint`, built from `v2.7` (`7867d56`). The v2.7 weights
(`models/wirenet-r34.pt`, SHA-256 prefix `ab2353c7`) and the YOLO detector were not retrained or
modified. Every change in this chapter is in pre-processing, test-time inference, span assembly or
interpretation. Every constant was chosen on the **validation split** (22 images). The **test
split** (21 images) was used once, for the final tables. Raw results are in
`docs/thesis/results/v28_*`; figures are in `docs/thesis/figures/`.

**Guard rule (set by the team).** A change is kept only if validation tolerant pixel IoU for
power-distribution-lines and telecom-cable stays ≥ 97% of v2.7's (0.525 and 0.418). Every accepted
change passed it (§4.8.4).

---

## 4.6 Methods

### 4.6.1 Span stitching (collinear gap bridging)

v2.7 split WireNet's class map into spans at skeleton junctions (§4.2.2). Spans still broke wherever
the mask had a gap — under occlusion, against low contrast, or across mask dropouts. On validation,
v2.7 produced 579 power spans for 314 labelled spans.

v2.8 joins spans *after* junction splitting. For each span, the skeleton is ordered along its
principal axis (first SVD component). Its two endpoints e₁ and e₂ get outward unit tangents t₁ and
t₂, each estimated from the mean of the 12 skeleton points nearest the end. Two spans of the same
class are joined through endpoints *a* and *b* when all three conditions hold:

    ‖b − a‖ ≤ g_max,    −t_a · t_b ≥ cos θ_max,    t_a · û ≥ cos θ_max  and  t_b · (−û) ≥ cos θ_max,
    with û = (b − a) / ‖b − a‖.

In words: the ends are close, the wires face each other, and the gap continues the wire's direction.
Candidate joins are taken nearest-first with union-find, and each endpoint is used at most once. The
bridge segment is drawn into the mask, 2 px wide. A span's confidence is the area-weighted mean of its
pieces' confidences. Finally, spans whose skeleton is shorter than L_stitch are dropped.

### 4.6.2 Measurement-precision gate for CRS

The sag ratio δ/L is measured in pixels. If the skeleton has noise σ (about 1 px), the error in δ/L
is about σ/L. A CRS is meaningful only when that error is small relative to the allowable sag ratio
δ_lim. Requiring σ/L ≤ f·δ_lim gives

    L_min = σ / (f · δ_lim) = 1 px / (0.25 × 0.040) = 100 px.

Spans with chord L < 100 px get Chapter III's existing status `too_short`, with reason
`chord_below_precision_gate`, and no CRS. The gate follows from measurement precision. It was **not**
tuned to change the verdict distribution. Equation 10 is unchanged.

### 4.6.3 Explainable hazard verdict (`app/pipeline/hazard.py`)

This is an interpretation layer on top of CRS; Equations 1–11 are unchanged. The verdict is the
highest severity raised by these rules (the vegetation rules were set by the team, not tuned):

| Factor | Rule | Verdict |
|---|---|---|
| Sag (Chapter III) | Any span with CRS level High (CRS > 6) | AT RISK – HIGH |
| | Any span with level Medium | AT RISK – MODERATE |
| Vegetation | A power span at distance 0 from a vegetation mask (contact) | AT RISK – HIGH |
| | A power span within 15 px | AT RISK – MODERATE |
| | A telecom span in contact | AT RISK – MODERATE |
| Congestion | Spans and junction crossings per megapixel | Informational only (no reference rating to calibrate against) |

Distances come from the Euclidean distance transform of the vegetation union, and the nearest wire
and vegetation points are recorded for the diagnostic figure.

**Approximate clearance.** With D the conductor diameter (config: 10.1 mm) and w̃ the median
measured power-wire width in pixels (area divided by skeleton length), the scale is
s = D / w̃ metres per pixel. The ±1 px width uncertainty is carried through to a range,
[D / (w̃ + 1), D / (w̃ − 1)]. At w̃ ≈ 2–3 px that range spans a factor of 2–3, so clearances are always
labelled approximate.

**Reliability override.** When the reliability grade (§4.6.6) is *Retake recommended*, NOT AT RISK becomes
**INCONCLUSIVE – RETAKE PHOTO**: an unreadable photo is never reported as safe.

### 4.6.4 Topological loose-end detector (`app/pipeline/loose_ends.py`)

This detector targets abandoned wire using geometry alone, with no labels needed to run it. A span
endpoint is a candidate when all of these hold:
1. it is ≥ 12 px from the border and from letterbox padding;
2. no other wire lies within 8 px;
3. fewer than 2 other span endpoints lie within 25 px (an attachment proxy; the model has no pole
   class).

Score = 0.5 · downward + 0.5 · isolation.

### 4.6.5 Pre-processing candidates and test-time augmentation

- **Multi-scale Retinex** on CIELAB L\* (Jobson et al., 1997). Scales are 1, 5 and 15% of the long
  side; the output is stretched at the 1st–99th percentiles and blended 0.6 with the original.
  Triggered when mean L\* ≤ 40 or when ≥ 15% of pixels are clipped high.
- **Edge-preserving denoise:** a bilateral filter (d = 5, σ_space = 3), triggered by moderate noise
  (Immerkær σ ≥ 3) or by JPEG blocking.
- **JPEG blockiness B:** the mean absolute step across 8-px block boundaries divided by the mean
  step inside blocks. B ≈ 1.0 for natural images and 1.4–1.7 at JPEG quality 20; the trigger is
  B ≥ 1.3.
- **Darkness trigger for CLAHE:** `dark_l_max`, tested on and off.
- **WireNet test-time augmentation:** `flip` averages the plain and mirrored probability maps.
  `flip_ms` adds 0.85× and 1.15× scales, each also mirrored.

### 4.6.6 Reliability grade (`app/pipeline/quality.py`)

Two uncertainty measures are taken over pixels predicted as wire:
- **U:** the mean normalised entropy H(p) / log C.
- **D:** the share of those pixels whose class changes between the plain and mirrored passes.

Reliability is **R = 1 − (U + D) / 2**. The grade is A / B / C / *Retake recommended*, using
thresholds on R calibrated on validation (§4.8.5). A photo with fewer than 500 predicted wire pixels,
mean L\* < 8 or more than 85% clipped pixels is graded *Retake recommended* regardless of R.

### 4.6.7 Stress conditions (`ml/stress.py`)

Each condition is deterministic per image name and scaled to the image size:

| Condition | Model |
|---|---|
| clean | unchanged |
| lowlight | γ = 2.4, plus Gaussian sensor noise σ = 6 |
| glare | ×1.6 + 60, with local contrast halved (Gaussian base, σ = 2% of the long side) |
| motion | linear kernel of 1.5% of the long side, random angle |
| defocus | disk kernel, radius 0.5% of the long side |
| jpeg | JPEG quality 20 |
| lowres | downscaled to 480 px on the long side, then upscaled back |
| rainhaze | haze (airlight 220, α = 0.35) plus slanted rain streaks |

Figure: `docs/thesis/figures/stress_conditions.jpg`.

---

## 4.7 Experimental protocol

1. WireNet probabilities and YOLO vegetation detections were cached once per image (`ml/experiments/cache.py`).
   Span experiments re-use the cache, so every variant is scored on identical model outputs.
2. **Span constants** were chosen by coordinate descent on the validation tolerant mAP50, with two
   rounds and seven parameters, starting from v2.7's values. Parameters that ended at the edge of
   their grid were then extended (`v28_span_sweep.jsonl`, 64 runs).
3. **Pre-processing and TTA** were chosen on the validation stress set: 8 conditions × 22 images,
   scored by tolerant pixel IoU for power and telecom (`v28_stress_pixel_val.jsonl`). Combined
   candidates were then confirmed at span level.
4. **Decision rule:** differences below about 0.002 tolerant mAP50 were treated as ties. On 22 images
   the response is visibly noisy (§4.8.1), and among tied values the more conservative one was kept.
5. **Test split:** evaluated once, with the final configuration (§4.8.6–4.8.7).

---

## 4.8 Results

### 4.8.1 Span-extraction ablation (validation; v2.7 probability maps)

Each row varies one constant from the final setting.

| Varied constant (others at final) | Tolerant mAP50 at each value (final value in bold) |
|---|---|
| `max_gap` | 0: 0.0307, 35: 0.0339, 50: 0.0346, **70: 0.0356**, 100: 0.0330, 150: 0.0321 |
| `gap_angle_deg` | 10: 0.0336, 15: 0.0332, 20: 0.0328, **25: 0.0356**, 30: 0.0339, 35: 0.0366 |
| `draw_bridge` | False: 0.0350, **True: 0.0356** |
| `min_span_len` | 0: 0.0343, 45: 0.0345, 60: 0.0346, **90: 0.0356**, 120: 0.0354, 200: 0.0366 |
| `max_turn_deg` | 15: 0.0328, 25: 0.0350, **35: 0.0356**, 45: 0.0357 |
| `cluster_radius` | 2: 0.0262, **5: 0.0356**, 10: 0.0341, 15: 0.0289 |
| `min_len` | **15: 0.0356**, 30: 0.0340, 45: 0.0339 |

Final setting: gap bridging 70 px at 25°, bridges drawn, minimum stitched span 90 px, junction tolerance 35°, junction
cluster radius 5 px, minimum piece length 15 px. Full log: `v28_span_ablation_final.jsonl`; the descent log is in
`v28_span_sweep.jsonl`, 64 runs.

**Reading the table.**
- **Two effects are clear:** a sharp optimum in the junction cluster radius (2 px: 0.0262; 15 px: 0.0289) and a single
  peak in the bridging distance at 70 px (shorter bridges leave spans broken; longer ones join unrelated wires).
- **The angle and length effects are non-monotonic and within about ±0.002 (noise on 22 images).** Among those values,
  the stricter angle (25°) and the least restrictive length (90 px) were kept.

**Confirmation on the v2.8 probability maps** (edge denoise + flip TTA), validation:

| Span constants | Strict mAP50 | Tolerant mAP50 | Power tol. AP50 / F1 / R | Power spans (314 labelled) | Telecom tol. F1 / R |
|---|---|---|---|---|---|
| v2.7 | 0.0064 | 0.0296 | 0.068 / 0.125 / 0.175 | 564 | 0.043 / 0.066 |
| **v2.8** | **0.0068** | **0.0397** | **0.108 / 0.190 / 0.213** | **390** | **0.075 / 0.088** |

Together, v2.8's span assembly and pre-processing raise validation tolerant mAP50 from 0.0283 to
0.0397 (+40%). Fragmentation drops, from 1.84 to 1.24 predicted power spans per labelled span.

### 4.8.2 The "always high" diagnosis

Worst CRS level per photo:

| | Validation (22): high / medium / low | Test (21): high / medium / low | High-band spans (val / test) |
|---|---|---|---|
| v2.7 behaviour (no stitching, no chord gate) | 19 / 1 / 2 | 21 / 0 / 0 | 90 / 120 |
| v2.8 | 17 / 3 / 2 | 18 / 2 / 1 | 46 / 75 |

What drives the v2.8 verdicts (validation / test):

| Driver | Validation | Test |
|---|---|---|
| Sag (high band) only | 13 | 14 |
| Sag (high) + telecom in contact with vegetation | 4 | 4 |
| Sag (medium), with or without telecom vegetation contact | 3 | 2 |
| Telecom vegetation contact only | 0 | 1 |
| Nothing (NOT AT RISK) | 2 | 0 |

Stitching and the chord gate halved the high-band spans, but most photos still read high. Three
causes, measured:

1. **The per-photo rule saturates.** On validation, 6.9% of 666 scored spans fall in the high band.
   A pole carries about 30 measurable spans, so the chance that at least one is high is
   1 − 0.931³⁰ ≈ 0.88.
2. **The placeholder δ_lim.** The high band (CRS > 6) means δ/L > 0.6 δ_lim = 0.024 at the
   placeholder δ_lim = 0.040. Measured image-space sag ratios: median 0.0017, 90th percentile 0.020,
   95th 0.028. Sag ratio is uncorrelated with chord length (Spearman ρ = −0.03), so the remaining
   tail is not short-span noise.

   Sensitivity (validation spans; this is evidence for calibration, not a choice):

   | δ_lim | Spans high / medium / low | P(≥ 1 high among 30 spans) |
   |---|---|---|
   | 0.040 (placeholder) | 0.069 / 0.093 / 0.838 | 0.88 |
   | 0.060 | 0.035 / 0.075 / 0.890 | 0.65 |
   | 0.080 | 0.023 / 0.047 / 0.931 | 0.50 |

3. **What the high spans are.** A visual audit (`figures/high_crs_spans_val.jpg`) found a mix. Some
   are genuinely strongly curved wires — mostly service drops and slack leads curving into a pole,
   which are correct fits but not span sag. Others are merge artefacts, where a span mask picked up a
   neighbouring bundle wire and the fit bent to follow it.

**Conclusion.** v2.8 removes the fragment-noise share of the problem. The remaining "high" rate
reflects three things: the placeholder δ_lim (whose calibration needs the engineer's reference
ratings, H3), a per-photo *worst-of-30* rule, and Chapter III's CRS not separating drop and
service wires from spans. None of these thresholds was tuned here. The per-photo aggregation rule —
for example, requiring k high spans, or scoring only pole-to-pole spans — is a methodology decision
for the team.

### 4.8.3 Pre-processing and TTA under the eight conditions (validation, tolerant pixel IoU, power / telecom)

| Condition | Enhancement off | v2.7 | Edge denoise | v2.7 + flip | **v2.8** |
|---|---|---|---|---|---|
| clean | 0.525 / 0.418 | 0.525 / 0.417 | 0.525 / 0.417 | 0.528 / 0.422 | **0.528 / 0.422** |
| lowlight | 0.504 / 0.360 | 0.464 / 0.334 | 0.494 / 0.345 | 0.472 / 0.345 | **0.512 / 0.368** |
| glare | 0.069 / 0.161 | 0.080 / 0.191 | 0.080 / 0.191 | 0.079 / 0.185 | 0.079 / 0.185 |
| motion | **0.375** / 0.271 | 0.360 / 0.270 | 0.360 / 0.270 | 0.357 / 0.277 | 0.357 / 0.277 |
| defocus | 0.387 / 0.290 | 0.395 / 0.310 | 0.395 / 0.310 | 0.403 / 0.314 | **0.403 / 0.314** |
| jpeg | 0.479 / 0.392 | 0.477 / 0.388 | 0.486 / 0.395 | 0.482 / 0.394 | **0.490 / 0.400** |
| lowres | 0.515 / 0.407 | 0.515 / 0.404 | 0.515 / 0.404 | 0.517 / 0.408 | **0.517 / 0.408** |
| rainhaze | 0.517 / 0.415 | 0.520 / 0.410 | 0.520 / 0.410 | 0.523 / 0.412 | **0.523 / 0.412** |
| **mean** | 0.3802 | 0.3788 | 0.3824 | 0.3824 | **0.3872** |

v2.8 = edge denoise + CLAHE darkness trigger off + flip TTA.

Further findings:
- **Retinex was rejected.** It lowered low-light power accuracy (0.453 against v2.7's 0.464) and
  glare telecom (0.151 against 0.191).
- **Multi-scale TTA was rejected.** It gained ≤ 0.006 over flip at 2–3.5 s per image, against
  0.5–0.9 s.
- **v2.7's darkness-triggered CLAHE was harmful in low light:** 0.464 against 0.504 with enhancement
  off. Removing that trigger alone gives 0.512 against 0.497 (v2.8 against v2.8-keepdark).
- **Edge denoise** fired only on JPEG and noisy low-light photos.
- **Glare stays catastrophic for every variant** (power ≈ 0.08): with the sky clipped to white,
  dark-wire contrast is gone. No preprocessing fix exists; recapture is needed, and the reliability
  grade flags 9 of 22 glare photos as *Retake recommended* (§4.8.5).
- **Motion blur:** the unsharp mask slightly hurts motion blur (0.360 against 0.375 off) while
  helping defocus. Telling motion blur from defocus is left as future work.

### 4.8.4 Guard check

On clean validation, v2.8 scores tolerant pixel IoU of 0.528 for power (v2.7: 0.525, threshold
0.509) and 0.422 for telecom (v2.7: 0.418, threshold 0.405). **Pass.** The WireNet weights are
byte-identical to v2.7's.

### 4.8.5 Reliability grade calibration (validation, 176 image × condition cases)

| Score | Spearman ρ with per-image tolerant IoU | p |
|---|---|---|
| R = 1 − (U + D) / 2 | **0.640** | 1.1 × 10⁻²¹ |
| −U (entropy only) | 0.551 | 2.4 × 10⁻¹⁵ |
| −D (flip disagreement only) | 0.594 | 3.9 × 10⁻¹⁸ |

The thresholds were chosen to maximise agreement with the measured accuracy bands (A ≥ 0.45,
B 0.30–0.45, C 0.15–0.30, Retake < 0.15): **A ≥ 0.78, B ≥ 0.66, C ≥ 0.64**. The grade matches the
measured band exactly in 63.1% of cases and within one band in 93.8%.

| Grade | n | Measured tolerant IoU (median) |
|---|---|---|
| A | 41 | 0.460 |
| B | 112 | 0.391 |
| C | 7 | 0.283 |
| Retake recommended | 16 | 0.093 |

Retake fires on 9 of 22 glare photos, 5 of 22 motion-blur photos and none of the clean photos.

### 4.8.6 Test split: eight-condition stress benchmark (span level, full screener pipeline)

| Condition | Enhancement off (strict / tol.) | v2.7 (strict / tol.) | **v2.8** (strict / tol.) | v2.8 power tol. AP50 / R | v2.8 s/img* |
|---|---|---|---|---|---|
| clean | 0.0204 / 0.0376 | 0.0204 / 0.0372 | **0.0204 / 0.0469** | 0.105 / 0.225 | 5.47 |
| lowlight | 0.0011 / 0.0135 | 0.0018 / 0.0123 | **0.0053 / 0.0222** | 0.065 / 0.170 | 5.37 |
| glare | 0.0131 / 0.0156 | 0.0225 / 0.0281 | **0.0206 / 0.0257** | 0.019 / 0.043 | 4.73 |
| motion | 0.0215 / 0.0272 | 0.0158 / 0.0164 | **0.0166 / 0.0227** | 0.022 / 0.091 | 5.47 |
| defocus | 0.0220 / 0.0226 | 0.0257 / 0.0264 | **0.0255 / 0.0271** | 0.005 / 0.032 | 4.92 |
| jpeg | 0.0250 / 0.0405 | 0.0250 / 0.0401 | **0.0210 / 0.0410** | 0.078 / 0.186 | 4.34 |
| lowres | 0.0210 / 0.0374 | 0.0236 / 0.0377 | **0.0225 / 0.0503** | 0.105 / 0.209 | 4.4 |
| rainhaze | 0.0229 / 0.0400 | 0.0220 / 0.0399 | **0.0220 / 0.0520** | 0.117 / 0.237 | 5.0 |
| **mean** | 0.0184 / 0.0293 | 0.0196 / 0.0298 | **0.0192 / 0.0360** | | |

\* Seconds per image include the evaluation (mask matching). Server latency is in §4.8.8.

**Findings on the unseen test split.**
- **Tolerant mAP50 rises 21% over v2.7 on average** (0.0298 → 0.0360). v2.8 is best on 6 of 8 conditions: clean +26%,
  low light +80%, low-res +33%, rain-haze +30%.
- **Strict mAP50 is a tie** (0.0196 against 0.0192).
- **Small regressions on JPEG and glare (strict).** The validation gain from edge denoise on JPEG did not carry over at
  span level, and glare is slightly worse than v2.7 because CLAHE's darkness trigger is off.
- **Motion blur is the open problem.** Enhancement *off* beats both v2.7 and v2.8 (tolerant 0.0272), because unsharp
  masking sharpens the motion streak. A directional-blur detector that skips sharpening under motion is the clearest
  next step; it was not tuned here on test evidence.
- **Latency:** v2.8 is faster than v2.7 on degraded photos (4.3–5.5 s against 7–12 s including evaluation), because
  stitched spans rarely trigger the blur fallback.

### 4.8.7 Abandoned wire: the topological detector

Swept over 108 settings on validation (`v28_loose_end_sweep_val.json`). A candidate counts as a hit
when it lies within 15 px of a labelled abandoned instance.

| Setting | Instances hit | Candidates | Precision (lower bound) |
|---|---|---|---|
| Best F | 1 / 6 | 38 | 0.026 |
| Best recall | 3 / 6 | 180 | 0.017 |

With the final v2.8 span constants, the detector still raises 213 candidates on the 22 validation
photos, about 10 per photo. A visual audit of 24 candidates (`figures/loose_end_candidates_val.jpg`)
found mostly two kinds:
- **Mid-wire mask gaps** that stitching did not bridge. The wire visibly continues through the
  candidate.
- **Real terminations at insulators, crossarms and poles.**

The few dangling shapes were jumper loops at insulators: normal installations, not abandoned wire.

Pseudo-label mining needs candidates with precision > 0.75, so it was **not attempted**. Self-training
on these candidates would teach WireNet its own mask gaps. The detector is shipped **disabled**
(`hazard.loose_ends: false`) as a documented research module.

This confirms §4.3.6 from a second direction. An abandoned wire differs from a live one by context
— a free end that is *not* at hardware, no termination — and recognising that context needs either
pole and hardware classes or many more labelled abandoned instances.

### 4.8.8 End-to-end server verification

The real FastAPI app was run with the shipped configuration (YOLO + WireNet span mode, flip TTA, stitching, chord
gate, hazard and quality on) on a throw-away data directory. Uploads went through the public field-screener endpoint
(`docs/thesis/results/server_check_v28.json`): 3 test photos × {clean, glare, lowlight, motion}, plus 4 photos that
v2.8 rated low-risk.

| Photo | Condition | Status | Upload → result (s) | Verdict | Grade | Spans / scored | PDF |
|---|---|---|---|---|---|---|---|
| 026-11-03 1455 | clean | complete | 2.83 | AT RISK - MODERATE | Retake recommended | 2 / 2 | yes |
| 026-11-03 1455 | glare | complete | 1.79 | AT RISK - HIGH | Retake recommended | 7 / 4 | yes |
| 026-11-03 1455 | lowlight | no_wires_detected | 1.20 | INCONCLUSIVE - RETAKE PHOTO | Retake recommended | 0 / 0 | yes |
| 026-11-03 1455 | motion | no_wires_detected | 3.26 | INCONCLUSIVE - RETAKE PHOTO | Retake recommended | 0 / 0 | yes |
| 026-11-03 1455 | clean | complete | 2.58 | AT RISK - HIGH | B | 35 / 21 | yes |
| 026-11-03 1455 | glare | complete | 2.88 | AT RISK - HIGH | B | 27 / 18 | yes |
| 026-11-03 1455 | lowlight | complete | 2.97 | AT RISK - HIGH | B | 34 / 25 | yes |
| 026-11-03 1455 | motion | complete | 2.24 | AT RISK - HIGH | B | 15 / 11 | yes |
| 026-11-03 1456 | clean | complete | 2.90 | AT RISK - HIGH | A | 39 / 26 | yes |
| 026-11-03 1456 | glare | complete | 2.66 | AT RISK - HIGH | B | 31 / 19 | yes |
| 026-11-03 1456 | lowlight | complete | 2.85 | AT RISK - HIGH | B | 35 / 27 | yes |
| 026-11-03 1456 | motion | complete | 2.27 | AT RISK - HIGH | B | 22 / 20 | yes |
| 026-11-03 2214 | clean | complete | 3.45 | NOT AT RISK | B | 37 / 19 | yes |
| 026-11-03 2137 | clean | complete | 3.19 | AT RISK - HIGH | B | 34 / 26 | yes |
| jpg.rf.7qcdqnj | clean | complete | 1.74 | NOT AT RISK | A | 5 / 5 | yes |
| jpg.rf.ZfhiIln | clean | complete | 1.48 | NOT AT RISK | A | 5 / 4 | yes |

Latency from upload to result was 1.20–3.45 s (median 2.83 s) on the M1 with MPS,
under the 4.5 s target. All 16 PDFs were valid and include the hazard section and the diagnostic figure.

**A failure mode found here and fixed.** In the first run, a low-light and a motion-blurred photo with no detectable
wires were reported as NOT AT RISK — a silent failure. The verdict now becomes **INCONCLUSIVE – RETAKE PHOTO** whenever
the reliability grade is *Retake recommended* and no hazard was found (`hazard.apply_reliability`). A hazard that was
found is kept, with a low-reliability note. Both runs are in the record; the table shows the run after the fix.

### 4.8.9 Failure-mode audit (validation, v2.8, 22 images)

Spearman correlation of per-image tolerant pixel IoU with each image property. None is significant
at α = 0.05.

| Property | ρ | p |
|---|---|---|
| Labelled span count | 0.041 | 0.86 |
| Spans per megapixel of photo content | 0.040 | 0.86 |
| Median labelled wire width | −0.328 | 0.14 |
| Contrast (std of L\*) | −0.286 | 0.20 |
| Sharpness (variance of the Laplacian) | −0.309 | 0.16 |
| Mean L\* (brightness) | 0.405 | 0.06 |

Per image, **false positives outweigh misses**: 32–62% of predicted wire pixels have no label, while
10–34% of labelled wire pixels are missed. The hardest image (`figures/failure_hard_*.jpg`; tolerant
IoU 0.42, 35 labelled spans) shows where these come from:
- **False positives (red):** mostly in a dense bundle where many visible wires were never labelled,
  so these are *label incompleteness*, not model errors. A minority are true false positives on
  insulators and crossarm edges.
- **Misses (blue):** labelled wires crossing in front of the pole, against pole texture.

Accuracy in clean conditions is therefore limited about as much by label completeness in dense
bundles as by the model. Wire density alone does not predict accuracy (ρ = 0.04).

---

## 4.9 Engineering additions

- **Diagnostic figure** (`app/services/diagnostic.py`): three panels — the photo; segmentation by
  class; and spans by CRS level with clearance lines, loose-end markers and the verdict and grade
  badges. It is embedded in every PDF report together with the hazard reasons.
- **Batch street audit** (`scripts/batch_inspect.py`): takes a folder or a survey video (frames every
  N s) and removes near-duplicate frames by pHash. It writes `inspection.csv`, `summary.json` /
  `summary.md`, the diagnostic figures, `rejected.csv` (corrupt, too small, constant images), and
  `review_queue/` — LabelMe 5 JSON with the model's polygons for high-risk and low-reliability
  photos, ready to be corrected into new training labels.
- **Robustness:** the API and the batch tool handle corrupt, tiny, constant, duplicate and non-image
  inputs, extreme aspect ratios (3000 × 320, 400 × 1600), photos with no wires, and vegetation-only
  photos. Each case is tested.
- **Tests:** 294 (v2.7) → 351, all passing; `ruff` clean.

## 4.10 Limitations and next steps

1. **The risk verdict is not calibrated.** The δ_lim placeholder and the per-photo worst-of-30 rule
   (§4.8.2) need the engineer's reference ratings (H3) and a team decision on aggregation. The
   evidence above is offered for that decision.
2. **CRS treats every curve as span sag.** Service drops and slack leads need either a class of their
   own or a pole-to-pole span criterion.
3. **Glare is unsolved.** It is detected — the grade asks for a retake — but not corrected.
4. **Abandoned wire** still needs labels (§4.3.6), or pole and hardware classes for the topological
   approach.
5. **Small validation set.** With 22 images, differences of about 0.002 in tolerant mAP50 are noise.
   All constants should be re-validated when the labelled set grows.
