# WIREFRAME — Thesis Defense Summary (v2.9, updated for v2.10, v2.11 and v2.12)

A one-file reference for the defense. Every number traces to a file in `docs/thesis/results/` and to a
section of a Chapter 4 report:
- [v2.7](chapter4_v2.7_results.md)
- [v2.8](chapter4_v2.8_autonomous_upgrades.md)
- [v2.9](chapter4_v2.9_refinements.md)
- [v2.10](chapter4_v2.10_hardening.md)
- [v2.11](chapter4_v2.11_field_ui.md)
- [v2.12](chapter4_v2.12_ensemble.md)

All figures were measured on one Apple Silicon M1 laptop with 8 GB of unified memory; no cloud GPU was used.

**What is claimed:** a working, explainable screening prototype and an honest account of its accuracy.

**What is not claimed:** that any Table 3.10 target is met. None is.

---

## 1. The system in one paragraph

- **Input:** a street photo of a pole.
- **Segmentation:**
  - **Vegetation** comes from YOLO11s-seg.
  - **Wires** come from **WireNet**, a full-resolution U-Net (ResNet-34) trained with CE + Dice +
    clDice. YOLO's 1/4-resolution mask grid cannot represent a 2–3 px wire.
- **Span extraction:** spans are separated geometrically (skeleton, junction splitting, collinear gap
  bridging).
- **Scoring, unchanged from Chapter III:**
  - Zhang–Suen centreline;
  - Levenberg–Marquardt catenary fit;
  - sag ratio δ/L, CRS = min(10, 10·(δ/L)/δ_lim·w), banded Low < 3 ≤ Medium ≤ 6 < High;
  - VNI from a Hann-windowed FFT; MSPI ranking.
- **Interpretation layer (v2.8–v2.9), on top of the CRS:**
  - the pole verdict (AT RISK – HIGH / MODERATE / NOT AT RISK / INCONCLUSIVE – RETAKE PHOTO) with
    plain-English reasons;
  - a reliability grade (A / B / C / Retake), also with a reason.

## 2. Evolution, with the exact numbers

| Version | What changed | Key result (split) |
|---|---|---|
| v2.6 | YOLO11s-seg, 640 px, MPS-trained | Test strict mask mAP50 **0.0005** |
| v2.7 | 1024 px; diagnosed the mask ceiling (2.1 px wire → median ceiling IoU 0.40); WireNet; spans from the skeleton | Test strict / tolerant mAP50 **0.0204 / 0.0375**; WireNet val tolerant pixel IoU power 0.525, telecom 0.418 |
| v2.8 | Span stitching, chord precision gate (100 px), hazard verdict, reliability grade, edge denoise + flip TTA, 8-condition stress suite | Val tolerant mAP50 0.0283 → **0.0397**; test 8-condition mean tolerant 0.0298 → **0.0360**; reliability ρ = 0.64; server 1.2–3.45 s |
| **v2.9** | **Corroborated pole-level aggregation**; grade reasons; diagnostic figure v2; three negative results (motion router, drop split, X-junction) | Val verdicts high/mod/not **17/3/2 → 2/10/10**; test **18/3/0 → 7/7/7**; segmentation unchanged (guard pass) |
| **v2.10** | Photometric glare gate; output-identical latency rewrite; scoped lint; verdict instability quantified | Val silent NOT AT RISK under glare **5 → 0**, test **7 → 3** (all 3 are instability, not dropout); dense poles **35–46% faster**, outputs identical to v2.9 on 22/22 photos |
| **v2.11** | Dual-polarity (night) WireNet inference; full-mask + per-span reasons in the API; field inspection console | Synthetic night val tolerant mAP50 **0.0051 → 0.0312**, daytime unchanged (golden 22/22); dense night pole 12 → 27 spans |
| **v2.12** | MiT-B1 and ConvNeXt-Tiny backbones; three-model probability fusion; Depth Anything V2 | Fusion val tolerant mAP50 **0.0397 → 0.0537**, but 4.7 s per photo on the M1 (limit 3.5 s): **not shipped**, opt-in only. Depth gate rejected; perspective inflates high-sag readings (3.2% → 10.4%) |

### Test split, v2.9 locked configuration (21 photos, span level)

| Condition | Strict / tolerant mAP50 (identical to v2.8) |
|---|---|
| clean | 0.0204 / 0.0469 |
| lowlight | 0.0053 / 0.0222 |
| glare | 0.0206 / 0.0257 |
| motion | 0.0166 / 0.0227 |
| defocus | 0.0255 / 0.0271 |
| jpeg | 0.0210 / 0.0410 |
| lowres | 0.0225 / 0.0503 |
| rainhaze | 0.0220 / 0.0520 |
| **mean** | **0.0192 / 0.0360** |

Per-photo verdicts on test (high / moderate / not at risk): v2.8 18 / 3 / 0 → **v2.9 7 / 7 / 7**. The 7 not-at-risk
photos each carry a NOTE listing their uncorroborated spans.

## 3. Five takeaways

1. **Wires are a resolution problem before they are a learning problem.** YOLO-seg's prototype masks are
   1/4 resolution. A 2 px wire therefore has a median best-achievable IoU of 0.40, below the 0.5 match
   threshold. WireNet, at full resolution, made wire spans detectable at all.
2. **Pixel accuracy is not span accuracy.** Twice, a change that raised pixel IoU lowered span AP:
   YOLO-seg masks, and the v2.9 motion-blur router (+0.021 pixel IoU, −0.003 to −0.008 span tolerant
   mAP50). The screener needs continuous, separated spans, so span-level validation decides.
3. **"Always high" was an aggregation problem, not a sag problem.**
   - About 30 measurable spans per pole, with a test–retest false-alarm rate of 0.050 per span, give
     P(at least one high) ≈ 0.78 for a pole that is fine.
   - v2.9 requires the count of high spans to beat that noise (one-sided binomial test, α = 0.05).
   - Steep drop wires were *not* the cause: they read high 5–10× *less* often. And "steep in the image"
     turned out to mean "seen in perspective", not "drop wire".
4. **Every rule is either physical, measured on validation or conventional, and the files say which.**
   - The chord gate comes from σ/L ≤ ¼ δ_lim.
   - p0 is measured on validation.
   - α = 0.05 is the convention.
   - Nothing was tuned on test.
5. **The system says when it cannot see.** An unreadable photo never reads "NOT AT RISK". It reads
   "INCONCLUSIVE – RETAKE PHOTO", with the cause: glare, darkness, camera shake, focus or too few wire
   pixels.

## 4. Expected panel questions

**Q: Your mAP50 is around 0.02–0.05. Isn't the model useless?**
These are instance-level scores at IoU 0.5 for objects 2–3 px wide and hundreds of pixels long.
- Strict IoU punishes a 1 px offset along the whole wire, which is why the 2 px tolerant metric is reported
  next to it.
- At pixel level, with 2 px tolerance, WireNet's overlap with the labelled power and telecom wires is
  IoU 0.528 / 0.422 (validation).
- The main limit is the data: 145 photos and 102 for training.
The thesis reports these as development results, not as a validation of Table 3.10.

**Q: Why did almost every photo read HIGH in v2.8, and how do you know v2.9 isn't just hiding hazards?**
- **Cause:** the worst of ~30 spans decided the photo, with a measured 5.0% per-span chance that a "high"
  reading does not reproduce.
- **Rule:** v2.9 asks for more high spans than that noise rate explains.
- **Nothing is hidden:**
  - uncorroborated spans appear as NOTE lines ("verify visually");
  - vegetation contact still triggers HIGH on its own;
  - the trade-off is one explicit parameter (α).
- **What we cannot say:** whether the remaining HIGH and MODERATE verdicts are correct. That needs
  the engineer's reference ratings (H3), and δ_lim is still a placeholder.

**Q: Service drops aren't pole-to-pole spans. Why do you still score them for sag?**
Because we could not yet tell them apart reliably, and we checked.
- A 35° image-inclination rule was built and tested.
- A visual audit of 24 random validation "drops" found pole-to-pole spans converging steeply in
  upward-looking photos, not drops.
- Steep spans read high 5–10× *less* often than flat ones (δ/L shrinks with cos²θ), so scoring them does
  not cause false alarms.
- Separating drops needs 3-D cues (where the wire ends) or a labelled `service-drop` class. That is
  future work, and the code is ready for it (`crs.max_span_inclination_deg`).

**Q: Isn't the binomial independence assumption wrong? Spans in one photo share lighting and blur.**
Partly, yes. Correlated errors make clusters of false highs more likely than the binomial predicts, so the
rule is somewhat permissive in bad photos. The reliability grade covers exactly that case: a bad photo is
graded C or Retake and says so. A capture–recapture study would measure the correlation directly
(Chapter 4 v2.9 §4.11.9).

**Q: Why did you not keep the motion-blur fix? You said motion blur was the open problem.**
- The detector works: 22/22 motion photos are separated from 22/22 defocused photos.
- Turning sharpening off for those photos raised pixel IoU but lowered span-level accuracy, mainly
  telecom recall.
- The anti-regression rule says revert, so it was reverted.
- The detector is still used to tell the field worker "camera shake" when the grade is low.

**Q: Are the metre clearances real?**
- They are estimates. The scale comes from an assumed conductor diameter divided by the measured wire
  width in pixels.
- The ±1 px width error is propagated, and the range is shown as "~m ± u".
- The PDF states that they are not survey measurements.

**Q: Why can't it find abandoned wires?**
- There are 27 labelled instances in total, 14 in training, and about a third are tight wire traces.
- Rebalancing, focal loss and a topological loose-end detector were all tried. The detector's precision
  was below 0.03.
- Recognising an abandoned wire needs context — a free end not at hardware — and so more labels or
  pole and hardware classes.

**Q: Is the verdict stable if I take the same photo twice?**
Not stable enough, and this is the most important open result.
- On validation, mild degradations that keep the wires (JPEG, haze, low light) change the pole verdict in
  41% of cases.
- The reliability grade and the NOTE lines make borderline poles visible ("2 of 18 high, 4 needed").
- The decision is still binary at k*.
- The fix is upstream, in a steadier per-span sag estimate.

**Q: Was the test set used to choose anything?**
No threshold, rule or constant was chosen on test. Two disclosures, both recorded:
1. **The first locked test run differed slightly from v2.8** (tolerant mean 0.0355 against 0.0360),
   although segmentation should have been identical. The difference was traced to a bug: the blur fallback
   counted service drops as failed wires. The bug was fixed.
2. **Validation then showed the drop split itself was wrong** (perspective), and it was turned off.
3. **p0 was then re-measured on validation over all spans**, because the rule now scores all of them. The
   verdict layer was re-run on test: 6 / 7 / 8 with the earlier subset value, 7 / 7 / 7 with the final one.

The final test run reproduces v2.8's segmentation exactly. Superseded runs are kept in
`docs/thesis/results/superseded/`.

**Q: Under glare a pole went from HIGH to NOT AT RISK. Isn't that dangerous?**
It was two different problems, and v2.10 separated them.
1. **Glare that erases wires.** The model stays confident about the little it still sees, so its own
   uncertainty cannot flag this. v2.10 measures the photo instead: a frame that is mostly near-white
   (≥ 60% at L* > 90) *and* whose thin dark lines stand out by less than 15 L* units is graded Retake, and
   NOT AT RISK becomes INCONCLUSIVE. On validation this caught every case (silent NOT AT RISK 5 → 0). It
   left 19 of 22 bright-but-clear sky photos alone; a brightness-only rule would have rejected all 22.
   On test: 7 → 3.
2. **Verdict instability.** The 3 test cases left, the motivating photo among them, kept 76–92% of their
   spans. Their verdicts moved because the count of high spans sits near the corroboration threshold.
   - On validation the pole verdict changes in 41% of mildly perturbed photos whose wires survive
     (JPEG, haze, low light).
   - A "borderline band" did not help and was rejected.
   - The fix needs a more stable per-span sag measurement. It is the main open problem, and it is
     quantified (Chapter 4 v2.10 §4.12.4).

**Q: Why not just reject every bright photo?**
That is option M1, and its cost is measured: 14 of 22 bright-sky validation photos would read
INCONCLUSIVE. It would also catch the instability case above. It is documented as a policy choice for the
team, not adopted, because we would have been choosing it after seeing a test-photo failure.

**Q: How fast is it?**
- **v2.10 is 35–46% faster on dense poles than v2.9, with outputs proven identical.** v2.9's own run
  (from a git worktree) was the reference, and every wire, verdict and grade matched on 22/22 validation
  photos.
- **The slowdown was algorithmic.** Span assembly scanned the whole image once per skeleton fragment.
- On a quiet machine the 81-span pole takes 2.3 s from upload to result (4.58 s in v2.9).
- Under sustained load on this fanless laptop the CPU stages slow by up to 2–3×, and a dense pole can
  again exceed 3.5 s (`server_check_v210.json`, median of 3, load average about 3.8).
- **What's left:** mostly GPU inference and Chapter III's own geometry.

**Q: On a dense night pole the screener showed only 12 wires. Why, and what changed?**
- The model, not the geometry, was missing them. At night, street-lit conductors are *lighter* than the sky,
  and every training photo shows dark wires against a bright sky.
- v2.11 also runs WireNet on the photo with luminance inverted, and keeps that pass only when it finds clearly
  more wire (≥ 1.2×).
- **Daytime:** it never switched on 176 validation cases, and the output stays identical.
- **Night:** on synthetic night photos, tolerant mAP50 rose from 0.0051 to 0.0312. On the real pole, 12 → 27 spans.
- **Caveat:** the night numbers are synthetic, and real labelled night photos are the next data to collect.

**Q: Why not just lower the threshold to catch more wire?**
That was measured (hysteresis). Pixel recall rose by up to 9 points, but span accuracy fell from 0.0397 to as
low as 0.018, because the extra pixels glue neighbouring wires together. Everything the model sees is now shown
in the "Full WireNet mask" layer instead.

**Q: Why didn't you ship the more accurate ensemble?**
- Fusing R34 with a MiT-B1 transformer and a ConvNeXt-Tiny U-Net raised validation span tolerant mAP50 from
  0.0397 to 0.0537, and both classes' pixel F1. It beat R34 in 94% of bootstrap resamples, but the 95% interval
  still includes zero, with 22 photos.
- It costs 4.7 s per photo and 5.5 s on a dense pole on the 8 GB M1, over the 3.5 s field limit. Dropping the
  mirror pass fits the limit, but it would have meant re-calibrating the reliability grade and p0 on an
  already-used validation set.
- So v2.12 ships v2.11's pipeline unchanged (proven identical), and the fusion is one config line away on faster
  hardware.

**Q: Can monocular depth fix the night vegetation or the perspective problem?**
- Depth Anything V2 removes the night-sky "vegetation" false positive, but it also removes real trees
  (vegetation AP50 0.024 → 0.0025), so it was rejected.
- It did show that **spans receding in depth read as high sag 3.3× more often** (Fisher p = 0.0039). A correction
  needs metric depth and ground-truth sag, which we don't have; it is reported as a limitation.

**Q: Did Chapter III's mathematics change?**
No. Equations 1–11, the CRS bands and the VNI are unchanged. Every v2.8–v2.9 addition reads the CRS
output and adds an interpretation on top. Pinned by tests.

## 5. Live demo script (about 4 minutes)

1. **Start:** `uvicorn app.main:app` and open the field screener.
2. **A clean pole photo from the test split.**
   - Show the verdict and its reasons.
   - Show the diagnostic: grade badge with its reason; spans S*n* coloured by CRS; legend; reasons strip.
   - Download the PDF and show the hazard page.
3. **The same photo with stress glare** (`ml.stress.apply_condition(rgb, "glare", name)`). It reads
   "Retake recommended", reason "Severe sky glare washed out the overhead conductors".
4. **A motion-blurred copy.** The reason names camera shake.
5. **Batch audit:** `python -m scripts.batch_inspect <folder> --out <dir>` produces a CSV, a summary and a LabelMe
   `review_queue/`.

Fallback if the network or GPU misbehaves: the PDFs and diagnostics from `scripts/server_check.py` are
reproducible offline. WireNet runs on CPU, more slowly.
