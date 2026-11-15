# Chapter 4 (continued) — v2.9 Refinements: Pole-Level Verdicts and Three Negative Results

Branch `v2.9-refinement`, from `dc1177b` (`v2.8-overnight-sprint`). No model was retrained. The WireNet
weights (`models/wirenet-r34.pt`, SHA-256 prefix `ab2353c7`) and the YOLO detector are byte-identical to v2.7
and v2.8. Every rule and constant below was decided on the 22 validation photos. The 21 test photos were
used only for the final tables (§4.11.6–4.11.8). §4.11.6 records why the test split was evaluated more
than once, with every superseded run kept.

This chapter follows the protocol of the v2.8 chapter (§4.7): cached model outputs where possible, coordinate
changes scored on validation, differences below about 0.002 tolerant mAP50 treated as ties, and the
anti-regression guard (validation clean tolerant mAP50 ≥ 0.0397, power ≥ 0.525 and telecom ≥ 0.418 tolerant
pixel IoU, 8-condition mean not lower than v2.8's 0.3872).

---

## 4.11 v2.9 at a glance

| Upgrade | Decision | Evidence (validation) |
|---|---|---|
| 1a. Motion-blur detector (structure-tensor anisotropy) | **Kept as a measurement** (it names "camera shake" in the reliability reason) | Separates 22/22 motion photos from 22/22 defocus and 3/3 soft clean photos at one threshold |
| 1b. Route motion-blurred photos away from unsharp masking | **Rejected** | Pixel IoU +0.021 power, but span tolerant mAP50 0.0177 → 0.0098–0.0143 in all four variants |
| 1c. JPEG deblocking (8 px boundary filter) | **Rejected** | Tolerant mAP50 tie (0.0319 → 0.0316); strict 0.0058 → 0.0043 |
| 2a. Service drop / guy wire split (chord > 35°) | **Rejected after a visual audit** (code kept, off) | Steep wires are *not* the ones that read high, and 24 of 24 audited "drops" were spans seen steeply in upward-looking photos (§4.11.2) |
| 2b. Corroborated pole-level aggregation (binomial test on test–retest p0) | **Adopted** | Verdicts high / moderate / not: 17 / 3 / 2 → 2 / 10 / 10 (test: 18 / 3 / 0 → 7 / 7 / 7) |
| 2c. Low-hanging service drop (framing proxy) | **Off** (depends on 2a) | Fired on 4 of 22 photos, on misclassified spans |
| 3. X-junction tangent-continuity matching | **Rejected** | 0.0353 (far tangents) or exactly 0.0397 (near tangents) against 0.0397 |
| 4. Diagnostic figure, grade reasons, web badge, defense summary | **Adopted** | §4.11.5, tests |

**Three of the proposed techniques produced negative results** (motion routing, the drop split,
X-junction matching). They are reported in full, because each answers a question a panel will ask: "why not
turn off sharpening for motion blur?", "why not separate drop wires?", "why not untangle crossings?".

---

## 4.11.1 Mission 1 — Motion blur, defocus and JPEG blocking

### Method: a content-normalised, direction-coherent blur measure (`app/pipeline/enhance.py`)

The variance of the Laplacian (v2.8's blur measure) falls under both motion blur and defocus. Only motion
blur removes detail **along one direction**. A plain gradient structure tensor cannot see this in street
photos, because the content is itself anisotropic: parallel wires and facades produce strongly oriented
gradients in a sharp photo (raw eigenvalue anisotropy: median 0.48 clean, 0.72 motion — overlapping).

The measure therefore normalises out the content:

1. Per 128 px tile, two structure tensors:
   - J_f, from the photo's gradients (Sobel);
   - J_c, from the gradients of a σ = 2 px Gaussian copy.
2. The whitened tensor M = J_c^{−1/2} J_f J_c^{−1/2}.
   - Content orientation appears in both tensors and cancels.
   - A blur kernel attenuates fine detail more than coarse detail. A *linear* kernel does so only along its
     direction, so M becomes anisotropic.
3. Tile anisotropy a = 1 − λ_min/λ_max, kept with its direction as a doubled-angle vector a·e^{2iθ}. Tiles in
   padding or with almost no coarse gradient energy (sky) are skipped.
4. **Motion coherence** = |mean vector|: high only when the lost direction agrees across the photo. A camera
   shake has one direction; content anisotropy does not.

A photo is classed as motion-blurred when it is soft (Laplacian variance ≤ 60, the existing unsharp trigger),
its coherence is ≥ 0.16, and it is **not overexposed** (clipped share < 0.25, the existing glare trigger).

**Why the overexposure guard.** In the first calibration, 3 of 16 glare photos were routed as motion. All had
58–83% clipped pixels: their low Laplacian variance comes from lost contrast, not blur, and their anisotropy
is the content's. The guard removed all three (0/16).

### Calibration (`v29_motion_calib_val.json`, figure `v29_motion_router_val.png`)

Photos routed as motion, out of photos soft enough to be sharpened, per stress condition:

| Coherence threshold | clean | glare | **motion** | defocus | jpeg | lowres | rainhaze |
|---|---|---|---|---|---|---|---|
| 0.10 | 1/3 | 0/16 | 22/22 | 3/22 | 1/2 | 9/17 | 6/9 |
| 0.14 | 1/3 | 0/16 | 22/22 | 0/22 | 1/2 | 7/17 | 1/9 |
| **0.16** | **0/3** | **0/16** | **22/22** | **0/22** | 1/2 | 4/17 | 1/9 |
| 0.20 | 0/3 | 0/16 | 18/22 | 0/22 | 0/2 | 3/17 | 1/9 |

0.16 is the smallest threshold that keeps every clean and defocused photo out of the motion route while
catching every motion photo. The low-resolution photos it catches are resampling-blurred. At pixel level they
score the same with or without sharpening (0.517 / 0.408 against 0.517 / 0.409), so misrouting them costs
nothing.

**Caveat.** The motion condition is synthetic: a linear kernel, 1.5% of the long side, at a random angle. Real
hand shake is curved, so coherence on real photos will be lower. The threshold has not been checked against
real motion-blurred field photos.

### Routing variants (`v29_stress_pixel_val*.jsonl`, `v29_stress_spans_val*.jsonl`)

Pixel level (tolerant IoU, power / telecom):

| Variant | motion | jpeg | glare | 8-condition mean |
|---|---|---|---|---|
| v2.8 | 0.357 / 0.277 | 0.490 / 0.400 | 0.079 / 0.185 | 0.3872 |
| + motion route, bilateral smoothing | 0.378 / 0.270 | 0.493 / 0.402 | 0.079 / 0.185 | 0.3886 |
| + motion route, skip unsharp only | **0.381 / 0.276** | 0.493 / 0.402 | 0.079 / 0.185 | **0.3892** |
| + deblocking | 0.357 / 0.277 | 0.491 / 0.401 | 0.079 / 0.185 | 0.3873 |
| + deblocking replacing edge denoise | 0.357 / 0.277 | 0.487 / 0.397 | 0.079 / 0.185 | 0.3868 |
| + motion route + deblocking | 0.378 / 0.270 | 0.494 / 0.403 | 0.079 / 0.185 | 0.3888 |

Clean, low-light and defocus are identical for every variant (0.528 / 0.422, 0.512 / 0.368, 0.403 / 0.314).

At pixel level, skipping the unsharp mask under motion blur is the best variant, as v2.8 predicted. **It does
not survive at span level**, which is what the screener uses.

Span level, the full screener pipeline (validation, motion condition; final code, `v29_stress_spans_val.jsonl`):

| Variant | Strict mAP50 | Tolerant mAP50 | Power tol. AP50 / R | Telecom tol. AP50 / R | s/img* |
|---|---|---|---|---|---|
| **v2.8 = v2.9** | 0.0051 | **0.0177** | 0.032 / 0.111 | 0.018 / 0.048 | 6.1 |
| route, smoothing | 0.0039 | 0.0143 | 0.036 / 0.111 | 0.005 / 0.012 | 4.7 |
| route, skip unsharp | **0.0064** | 0.0124 | 0.021 / 0.105 | 0.003 / 0.024 | 5.5 |
| route, smoothing; fallback sharpens | 0.0038 | 0.0143 | 0.037 / 0.111 | 0.005 / 0.012 | 5.0 |
| route, skip; fallback sharpens | 0.0039 | 0.0098 | 0.021 / 0.105 | 0.003 / 0.024 | 5.9 |

\* Including evaluation.

**What happened.**
1. **The first span-level run exposed a coupling** (`superseded/v29_stress_spans_val_run1*.jsonl`). v2.8's
   blur fallback (a second, stronger pass with a mirrored merge, `runner.finalize_detections`) is triggered
   by the *same* decision that requests unsharp masking. Routing a photo away from unsharp silently switched
   its fallback off: tolerant 0.0148 / 0.0093 at 3.5–3.8 s per photo. The coupling was fixed (motion photos
   stay eligible for the fallback, `runner._degraded`), and the table above is the re-run.
2. **With the fallback restored, every routed variant is still below v2.8 in tolerant mAP50**, by
   0.0034–0.0079, above the 0.002 noise band. Skipping unsharp gains strict mAP50 (+0.0013, inside the
   noise band). The loss is concentrated in telecom: AP50 falls from 0.018 to 0.003–0.005 and recall from
   0.048 to 0.012–0.024. A plausible mechanism, not verified here: sharpening across a motion streak keeps
   the thin telecom cables connected enough to skeletonise into spans. Without sharpening they fragment
   below the minimum span length.
3. **Pixel IoU and span AP disagree.** Pixel IoU rewards covering wire pixels; span AP needs a continuous,
   correctly separated instance. This is the second time the gap has decided a design (the first was
   YOLO-seg's mask ceiling, v2.7 §4.3.1).

**Decision.** The router is **not adopted** (`enhancement.motion_route: false`). The anti-regression rule
requires the degraded-condition score not to fall, and it falls. The detector stays in use for one purpose: a
low-reliability photo that is soft and direction-coherent is explained to the field worker as "Camera shake
(motion blur) smeared the conductors" rather than "out of focus" (§4.11.5).

**JPEG deblocking**, an H.264-style boundary filter, works as a filter: blockiness falls 3.17 → 1.57 at q20,
and real edges above the step threshold are untouched (tested). It is not adopted. At span level it ties in
tolerant mAP50 (0.0319 → 0.0316) and loses in strict mAP50 (0.0058 → 0.0043). Power recall rises
(0.172 → 0.201), but the masks are less precise. It runs at the original resolution before any resize,
because the 8 px grid is aligned only there. This matters for real 12 MP uploads, which v2.8's working-scale
blockiness measure could not see.

---

## 4.11.2 Mission 2 — Service drops and the per-photo aggregation rule

### 2a. Are steep wires the ones that read high? (`v29_span_geometry_summary_val.json`, figure `v29_inclination_val.png`)

The directive's premise was that steep service drops, scored with span sag equations, cause the "always high"
reading. The premise was **checked before it was acted on**. 657 scored spans on validation (single pass, no
TTA), by chord inclination from the image horizontal:

| Inclination | Spans | High band | Medium band | Median chord (px) |
|---|---|---|---|---|
| 0–10° | 196 | 12.8% | 13.3% | 242 |
| 10–20° | 147 | 14.3% | 7.5% | 217 |
| 20–35° | 145 | 6.9% | 10.3% | 241 |
| 35–50° | 91 | 2.2% | 5.5% | 335 |
| 50–90° | 78 | 1.3% | 5.1% | 321 |

**The premise is false on this data.** Steep wires (26% of scored spans) are 5–10 times *less* likely to read
high than flat ones.

The geometry explains why. Sag is measured perpendicular to the chord. For a span of horizontal length S
inclined at θ, with vertical mid-span sag D, the chord is L = S / cos θ and the perpendicular sag is about
D cos θ. So δ/L ≈ (D/S) cos²θ, which shrinks as θ grows.

**The split was implemented anyway, for physical correctness.**
- Chapter III's allowable sag ratio (Eq. 9) comes from the tension–span relation of a pole-to-pole
  distribution span. A service drop to a building, or a guy wire, is not such a span.
- A catenary y(x) is ill-conditioned for near-vertical wires.

Chords steeper than 35° (the directive's value) were given the status `service_drop`. Fragments below the
100 px precision gate were kept `too_short`.

**Then it was audited, and rejected** (`figures/v29_drop_audit_val.jpg`, `ml/experiments/drop_audit.py`).
- The diagnostic figure of a validation photo showed 18 cyan "drops". Most were long power-line runs that
  converge steeply towards the vanishing point because the photo looks up at the pole.
- A random sample of 24 of the 200 validation "drops" (seed 0) was then inspected. Essentially all are
  straight conductors running inside parallel bundles: pole-to-pole spans seen in perspective. At most two
  are ambiguous (a mask on a pole, a bundle entering a crossarm), and none is clearly a drop to a building.
- **Image-space inclination is a property of the camera, not of the wire.** Separating drops from spans
  needs 3-D cues: where the wire ends (a building wall rather than a crossarm), the vanishing-point
  geometry, or a labelled `service-drop` class.

**Decision.** The split is **off** (`crs.max_span_inclination_deg: 0`); the code stays as a configurable
option, with tests. Two coupling bugs it exposed were fixed and kept, because they matter for any future
status that is "measured but not scored":
1. Fragments must meet the precision gate before being classified.
2. The blur fallback must not treat a classified wire as a failed one (`runner._measured`).

Rejecting the split also restores the steep spans' CRS scoring. That is consistent with the geometric
argument above: δ/L shrinks with cos²θ, so steep spans are *under*-scored if anything, never over-scored.

### 2b. Corroborated aggregation

**Why the v2.8 rule saturates.** v2.8 sets a photo's verdict from its single worst span. A pole carries a
median of 29.5 measurable spans (validation). Even a small per-span false-alarm rate p therefore saturates
the photo verdict: 1 − (1 − p)^n. At p = 0.05 and n = 29.5 that is 0.78.

**Test–retest: measuring p0 instead of assuming it** (`ml/experiments/span_geometry.py`,
`v29_span_geometry_summary_all_val.json`).
- Each validation photo is measured twice: as is, and mirrored (with TTA off, so the two passes differ).
- Spans of the same class are matched by tolerant mask IoU ≥ 0.3: 492 matched pairs of scored spans.
- A span "high" in one measurement and not in the other is a level the measurement cannot reproduce. p0 is
  the per-measurement discordance.

| Quantity (all scored spans) | Value |
|---|---|
| p0 (high) | **0.050** |
| p0 (medium or above) | **0.090** |
| CRS = 10 (saturated) spans whose retest was also high | 13 of 35 (37%) |

The last row rules out the obvious shortcut, a "single extreme span" override: even saturated scores
reproduce in barely a third of cases.

*Provenance note.* p0 was first measured on spans up to 35° only (0.058 / 0.100, 356 pairs,
`v29_span_geometry_summary_val.json`), while the drop split was on. When the split was turned off
(§4.11.2a), the rule came to apply to all spans, so p0 was re-measured on the same retest data over all
spans. Only validation data was used. The verdict runs made with the earlier values are kept in
`results/superseded/`.

**The rule** (`hazard.assess`, `sag_rule: corroborated`). For a photo with n measurable spans:
- k*(n) = min { k : P(Bin(n, p0) ≥ k) ≤ α }, with α = 0.05 (the conventional level, not tuned).
- **HIGH** needs at least k*(n) spans in the high band, using p0 = 0.050.
- **MODERATE** needs at least k*(n) spans at medium or above, using p0 = 0.090.
- Spans in a band that do not reach k* are **listed as a NOTE**, with their numbers, the count found and
  the count needed, and "verify visually". They are never dropped silently.
- The vegetation rules (power contact → HIGH, ≤ 15 px → MODERATE, telecom contact → MODERATE) are
  **unchanged and absolute**.

| Measurable spans n | 1 | 2 | 5 | 10 | 22 | 30 (≈ median) | 49 |
|---|---|---|---|---|---|---|---|
| k* for HIGH (p0 = 0.050) | 1 | 2 | 2 | 3 | 4 | 5 | 6 |
| k* for MODERATE (p0 = 0.090) | 2 | 2 | 3 | 4 | 5 | 6 | 9 |

**Two edge cases a panel should hear plainly.**
- With exactly one measurable span, one high span is significant (P = p0 = 0.050 ≤ α). The boundary is
  that thin. One medium span is not (0.090 > α), and is reported as a NOTE.
- The trade-off between missed and false alarms is one explicit policy parameter, `hazard.alpha`. A team
  that wants more sensitivity lowers k* by raising α.

**What this rule is and is not.**
- It is a significance test: "more spans read high than measurement noise explains".
- It is *not* a claim about the true hazard rate, which needs the engineer's reference ratings (H3).
- p0 comes from single-pass measurements. The deployed pipeline averages a flip pass, which is less noisy,
  so p0 is if anything overestimated. That errs towards fewer alarms, and the NOTE lines keep every
  uncorroborated span visible to the reviewer.
- Spans of one photo share lighting and blur, so their errors are not independent. Correlated false
  highs make clusters more likely than the binomial predicts, so the rule is somewhat permissive in bad
  photos. The reliability grade is the safeguard there.
- The rule says nothing about whether the placeholder δ_lim is right; v2.8 §4.8.2 still applies.

### 2c. Low-hanging service drops

A proxy raising MODERATE when a service drop's lowest point lay in the bottom 15% of the photo content was
implemented and tested. It is **off**, because its input (the drop classification) failed the audit above.
It fired on 4 of 22 validation photos, all on misclassified spans.

### 2d. Verdicts (validation: `v29_verdicts_val.json` for the rule ablation, `v29_verdicts_pipeline_val.json` for the full pipeline)

Rule ablation on identical spans and vegetation:

| Rule | High | Moderate | Not at risk |
|---|---|---|---|
| v2.8 (any span) | 17 | 3 | 2 |
| + service-drop split (rejected) | 17 | 2 | 3 |
| **corroborated only = v2.9** | **2** | **10** | **10** |
| split + corroborated (rejected) | 3 | 8 | 11 |
| split + corroborated + low-drop proxy (rejected) | 3 | 10 | 9 |

(p0 = 0.050 / 0.090 for every corroborated row, `v29_verdicts_val.json`.)

**The corroboration rule does the work.** The split changes one photo on its own. Combined with
corroboration it moves one photo the *wrong* way (to HIGH), because removing steep spans lowers n and so k*.
This is consistent with §2a.

Drivers of the v2.9 verdicts (validation, full shipped pipeline, `v29_verdicts_pipeline_val.json`):

| Driver | Photos |
|---|---|
| Corroborated high sag | 2 |
| Corroborated medium-or-above sag | 5 |
| Telecom in contact with vegetation (sag not corroborated) | 5 |
| Not at risk | 10, of which 8 carry a NOTE listing uncorroborated spans |

The full pipeline gives the same distribution as the ablation (2 / 10 / 10). No validation photo is
INCONCLUSIVE: no photo was graded Retake.

---

## 4.11.3 Mission 3 — X-junction tangent continuity (`v29_xjunction_val.jsonl`)

At a junction with four (or six) piece ends, the pieces are paired by the **best perfect matching** of
tangent continuity. The matching is accepted only if:
- every pair is anti-parallel within the tolerance τ;
- the paired wires cross at more than 30°.

Otherwise v2.8's greedy rule applies. Tangents were first measured 4–20 px from the end, because the
skeleton bends inside a junction.

| Variant (all else v2.8) | Strict mAP50 | Tolerant mAP50 | Power tol. AP50 | Telecom tol. AP50 |
|---|---|---|---|---|
| **v2.8** | 0.0068 | **0.0397** | 0.108 | 0.022 |
| matching, far tangents, τ = 15° / 20° / 25° | 0.0063 | 0.0353 | 0.093 | 0.019 |
| matching, v2.8 near tangents, τ = 15° / 20° / 25° | 0.0068 | 0.0397 | 0.108 | 0.022 |

**Reading.**
- With v2.8's tangents, the optimal matching makes exactly the pairings the greedy rule already makes: at a
  clean X crossing, "straightest pair first" *is* the optimal matching.
- The far-tangent estimator is worse (−0.0044). At 4–20 px from a junction, the piece often already
  belongs to a neighbouring bundle wire.
- Neither variant beats 0.0397, so the option ships **off** (`xjunction_tol_deg: 0`).

The remaining fragmentation is not at crossings. It is at mask dropouts and in near-parallel bundles,
where no geometric pairing rule can tell the wires apart at 2–3 px width.

---

## 4.11.4 Anti-regression guard

v2.9 ships no change to the segmentation path: motion routing, deblocking, X-junction matching and the drop
split are all off. The full-pipeline span evaluation on validation is identical, digit for digit, to v2.8:

| Validation | v2.8 strict / tolerant | v2.9 strict / tolerant |
|---|---|---|
| clean | 0.0068 / 0.0394 | 0.0068 / 0.0394 |
| motion | 0.0051 / 0.0177 | 0.0051 / 0.0177 |
| jpeg | 0.0058 / 0.0319 | 0.0058 / 0.0319 |
| glare | 0.0064 / 0.0075 | 0.0064 / 0.0075 |

On the cached-span harness of v2.8 §4.8.1, tolerant mAP50 is 0.0397, the same span constants as before. The
full-pipeline evaluator gives 0.0394 because it includes the blur fallback. Power / telecom tolerant pixel
IoU is 0.528 / 0.422, and the 8-condition pixel mean is 0.3872. **Guard: pass.** The only new per-photo
computation is the coherence measurement, about 0.1 s.

---

## 4.11.5 Mission 4 — Demonstration and report output

**Diagnostic figure** (`app/services/diagnostic.py`; PDF and batch tool). Example:
`figures/v29_diagnostic_example.jpg`.

1. **Original + reliability badge:** "Grade A/B/C (R = 0.xx)" or "RETAKE RECOMMENDED" / "INCONCLUSIVE –
   RETAKE PHOTO", with the reason in plain English.
2. **Segmentation** with a class legend: power blue, telecom violet, abandoned orange, vegetation green.
3. **Spans vs drops:**
   - primary spans filled by CRS level and labelled S*n*;
   - service drops in cyan, labelled D*n* (shown only if the drop split is enabled; off in v2.9);
   - a level legend with the drop count;
   - vegetation clearance lines labelled "*px* ~*m* ± *u* m". The metre value is the midpoint of the
     ±1 px wire-width range; the uncertainty is half its width.
4. **A strip of verdict reasons** under the panels.

**Reliability reasons** (`quality.cause`). Every grade below A names the most likely cause, checked in the
order a field worker can act on:
1. Severe sky glare washed out the overhead conductors
2. Too dark
3. Almost no wire pixels
4. Camera shake (motion blur, from the §4.11.1 detector)
5. Out of focus
6. Bright-sky glare lowering contrast
7. Low light
8. Model uncertainty (clutter)

The wording never changes the grade.

**Web field screener.**
- A coloured grade badge, with the reason.
- Service drops, if enabled, are listed as "service drop", not "not measured".

**PDF.**
- Reason line under the grade.
- A per-wire note for drops.
- A caption that states that metre values are estimates, not survey measurements.

---

## 4.11.6 Test split: verdicts (21 photos, `v29_verdicts_pipeline_test.json`)

| | High | Moderate | Not at risk | Inconclusive |
|---|---|---|---|---|
| v2.8 | 18 | 3 | 0 | 0 |
| **v2.9** | **7** | **7** | **7** | 0 |

Drivers of the v2.9 test verdicts:

| Driver | Photos |
|---|---|
| Corroborated high sag (one also with telecom–vegetation contact) | 7 |
| Corroborated medium-or-above sag | 3 |
| Telecom in contact with vegetation only | 4 |
| Not at risk | 7, each with a NOTE listing uncorroborated spans |

On test, the corroboration rule keeps 7 poles at HIGH: more of their spans read high than noise explains.
On validation only 2 did. The rule removes the saturation without removing the high verdict. Whether these 7
poles are the right ones cannot be judged without reference ratings (H3).

Figure: `figures/v29_verdicts.png`.

**How many times the test split was evaluated, and why.** No threshold or rule was chosen from test. All
superseded runs are kept in `docs/thesis/results/superseded/`.
1. **First locked run.** It had the drop split on, and its tolerant 8-condition mean was 0.0355 against
   v2.8's 0.0360 (`superseded/v29_stress_spans_test_run1.jsonl`). Its verdicts were 7 / 9 / 5
   (`superseded/v29_verdicts_pipeline_test_run1.json`). The segmentation path should not have changed at
   all, so the difference was traced. It came from a coupling bug: the blur fallback counted service drops
   as failed wires. That was fixed, which is a correctness fix, not a tuning step.
2. **Validation then exposed the drop misclassification** (§4.11.2a), and the split was turned off.
3. **The final run** uses the configuration in this commit. Its segmentation results equal v2.8's on every
   condition (§4.11.7).
4. **The verdict layer only (not segmentation) was re-run once more on test**, after p0 was re-measured on
   validation over all scored spans (§4.11.2b provenance note). With the earlier subset value (0.058 / 0.100)
   the test verdicts were 6 / 7 / 8 (`superseded/v29_verdicts_pipeline_test_run2_p0subset.json`). With the
   final value they are 7 / 7 / 7.

---

## 4.11.7 Test split: 8-condition stress benchmark (span level, `v29_stress_spans_test.jsonl`)

| Condition | v2.8 strict / tol. | **v2.9 strict / tol.** | v2.9 power tol. AP50 / R | s/img* |
|---|---|---|---|---|
| clean | 0.0204 / 0.0469 | 0.0204 / 0.0469 | 0.105 / 0.225 | 4.6 |
| lowlight | 0.0053 / 0.0222 | 0.0053 / 0.0222 | 0.065 / 0.170 | 4.6 |
| glare | 0.0206 / 0.0257 | 0.0206 / 0.0257 | 0.019 / 0.043 | 3.5 |
| motion | 0.0166 / 0.0227 | 0.0166 / 0.0227 | 0.022 / 0.091 | 4.2 |
| defocus | 0.0255 / 0.0271 | 0.0255 / 0.0271 | 0.005 / 0.032 | 3.9 |
| jpeg | 0.0210 / 0.0410 | 0.0210 / 0.0410 | 0.078 / 0.186 | 4.3 |
| lowres | 0.0225 / 0.0503 | 0.0225 / 0.0503 | 0.105 / 0.209 | 4.6 |
| rainhaze | 0.0220 / 0.0520 | 0.0220 / 0.0520 | 0.117 / 0.237 | 5.6 |
| **mean** | 0.0192 / 0.0360 | **0.0192 / 0.0360** | | |

\* Including evaluation.

v2.9 reproduces v2.8 exactly on unseen data, as intended: v2.9's contribution is the verdict layer, not
segmentation. Segmentation itself has not improved since v2.8. The open problems are unchanged: glare,
motion blur and telecom recall (0.01–0.05).

---

## 4.11.8 End-to-end server verification (`server_check_v29.json`)

The real FastAPI app with the shipped configuration and installed models, on a throw-away data directory
(`scripts/server_check.py`, now a repeatable script). 5 test photos: 3 under clean, low-light, motion and
glare conditions, and 2 clean.

| Photo | Condition | Status | Upload → result (s) | Verdict | Grade | Grade reason | Spans / scored |
|---|---|---|---|---|---|---|---|
| 145516 | clean | complete | 2.67 | AT RISK – MODERATE | Retake | model uncertain (clutter / low contrast) | 2 / 2 |
| 145516 | lowlight | no wires | 1.34 | INCONCLUSIVE – RETAKE | Retake | almost no wire pixels | 0 / 0 |
| 145516 | motion | no wires | 3.39 | INCONCLUSIVE – RETAKE | Retake | almost no wire pixels | 0 / 0 |
| 145516 | glare | complete | 1.63 | AT RISK – MODERATE | Retake | bright-sky glare lowered contrast | 7 / 4 |
| 145557 | clean | complete | 2.45 | AT RISK – HIGH | B | model uncertain | 35 / 21 |
| 145557 | lowlight | complete | 2.97 | AT RISK – HIGH | B | low light | 34 / 25 |
| 145557 | motion | complete | 2.14 | AT RISK – HIGH | B | camera shake (motion blur) | 15 / 11 |
| 145557 | glare | complete | 2.55 | NOT AT RISK | B | bright-sky glare lowered contrast | 27 / 18 |
| 145603 | clean | complete | 2.80 | NOT AT RISK | A | – | 39 / 26 |
| 145603 | lowlight | complete | 2.82 | AT RISK – MODERATE | B | low light | 35 / 27 |
| 145603 | motion | complete | 2.35 | AT RISK – HIGH | B | camera shake (motion blur) | 22 / 20 |
| 145603 | glare | complete | 2.49 | NOT AT RISK | B | bright-sky glare lowered contrast | 31 / 19 |
| 145634 | clean | complete | 4.58 | AT RISK – MODERATE | B | model uncertain | 81 / 57 |
| 213707 | clean | complete | 3.17 | NOT AT RISK | B | model uncertain | 37 / 34 |

**Latency** was 1.34–4.58 s, median 2.67 s, on the Apple-silicon Mac with MPS. Thirteen of 14 uploads
finished under the 4.5 s target. The 81-span pole took 4.58 s (4.50 s on an earlier run of the same check):
per-span fitting dominates on very dense poles, so **the 4.5 s target is not met for the densest scenes**.
Motion coherence is now computed only for soft photos, the only ones it is used for.

**PDFs:** all 14 were valid and include the reliability reason and the v2 diagnostic figure.

**Read the verdicts with the grades.**
- Under glare, photo 145557 reads AT RISK – HIGH on the clean upload but NOT AT RISK at grade B. Glare
  removes spans (power recall 0.04 on test), and the B grade does not fully capture that loss.
- This is a limit of the reliability score (ρ = 0.64), not of the verdict rule. A low-contrast photo can
  look confident to the model.
- On photo 145516 the glare result is graded Retake. A hazard was still found (corroborated medium sag), so it
  stays AT RISK – MODERATE with a low-reliability note, as designed.

---

## 4.11.9 Limitations

- **δ_lim is still the placeholder 0.040.** The corroboration rule decides how many spans must agree. It
  does not decide whether "high" is high. Calibration needs the engineer's reference ratings (H3).
- **p0 comes from a single type of retest (mirroring).** A capture–recapture of the same pole from a few
  metres away would be a stronger test–retest and would include viewpoint noise.
- **Drop wires are still scored as spans.** Telling them apart needs 3-D cues or a labelled class
  (§4.11.2a). Image-space inclination failed the audit.
- **The motion detector is calibrated on synthetic linear blur.** The routing result is a negative result
  for *this* WireNet; a model trained with motion-blur augmentation might respond differently.
- **22 validation photos.** Every decision above is a decision on 22 photos, with the noise band stated
  where it applies.

## 4.11.10 Reproduction

```
python -m ml.experiments.motion_calib --out docs/thesis/results/v29_motion_calib_val.json
python -m ml.experiments.stress_bench --split val --out docs/thesis/results/v29_stress_pixel_val.jsonl --variants v2.8 v2.8+motion v2.8+deblock v2.8+deblock_only v2.8+motion+deblock
python -m ml.experiments.stress_bench --split val --out docs/thesis/results/v29_stress_pixel_val_r2.jsonl --variants v2.8+motion v2.8+motion_skip v2.8+motion+deblock
python -m scripts.stress_test_all_conditions --split val --out docs/thesis/results/v29_stress_spans_val.jsonl --configs v2.8 v2.9 --conditions clean motion jpeg glare
python -m scripts.stress_test_all_conditions --split val --out docs/thesis/results/v29_stress_spans_val.jsonl --conditions motion --configs v2.8+motion v2.8+motion_skip v2.8+motion_fbu v2.8+motion_skip_fbu
python -m scripts.stress_test_all_conditions --split val --out docs/thesis/results/v29_stress_spans_val.jsonl --conditions jpeg --configs v2.8+deblock
python -m ml.experiments.span_geometry --split val --out docs/thesis/results/v29_span_geometry_val.json
python -m ml.experiments.span_geometry_report docs/thesis/results/v29_span_geometry_val.json --out docs/thesis/results/v29_span_geometry_summary_val.json
python -m ml.experiments.aggregation_val --split val --out docs/thesis/results/v29_verdicts_val.json
python -m ml.experiments.xjunction_sweep --out docs/thesis/results/v29_xjunction_val.jsonl [--near-tangent]
python -m ml.experiments.verdict_dist --splits val --out docs/thesis/results/v29_verdicts_pipeline_val.json
python -m ml.experiments.verdict_dist --splits test --out docs/thesis/results/v29_verdicts_pipeline_test.json
python -m scripts.stress_test_all_conditions --split test --out docs/thesis/results/v29_stress_spans_test.jsonl --configs v2.9
python -m scripts.server_check --out docs/thesis/results/server_check_v29.json
python -m ml.experiments.drop_audit --out docs/thesis/figures/v29_drop_audit_val.jpg
python -m ml.experiments.v29_figures
```
