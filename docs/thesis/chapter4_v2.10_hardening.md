# Chapter 4 (continued) — v2.10 Hardening: Glare, Verdict Stability, Latency

Branch `v2.10-hardening`, from `5989657` (v2.9). No model was retrained. The WireNet weights (`ab2353c7`), the YOLO
detector, the span constants and the corroborated-verdict rule are unchanged. Rules were derived on the 22
validation photos. The test split was used once, at the end (§4.12.5).

v2.9 ended with three open issues: a glare-masked hazard, dense-pole latency above 4.5 s, and an unscoped
linter. This chapter records what each turned out to be, what was tried, and what worked.

| Issue | Root cause found | Fix | Result |
|---|---|---|---|
| Glare-masked hazard | Two different failure modes: (a) glare erases spans while the model stays confident; (b) the pole verdict is unstable near the corroboration threshold even when spans survive | (a) A photometric glare gate, chosen from three candidates. (b) Quantified; a "borderline band" was tried and rejected | Validation: silent NOT AT RISK under glare 5 → 0. Test: 7 → 3; the 3 left are mode (b) |
| Dense-pole latency | Span assembly ran O(pieces × pixels) full-frame scans (1,235 `nonzero` calls); model inference is not the bottleneck | Bounding-box and single-pass rewrites, vectorised stitching; **outputs proven identical to v2.9** | Dense poles 35–46% faster (paired); 81-span pole 2.3 s on a quiet machine. Not guaranteed under sustained load on this laptop (§4.12.3) |
| Linting outside the project | `pyproject.toml` had no ruff exclusions, so `ruff check --fix .` walked into `.agents/` | `extend-exclude` plus `force-exclude` | `ruff check .` touches project files only (test) |

---

## 4.12.1 Glare: diagnosis (`ml/experiments/glare_diag.py`, `v210_glare_diag_val.jsonl`)

Every validation photo was run through the shipped pipeline under all 8 stress conditions (176 cases). Each
case was compared with the clean run of the same photo. Recorded per case:
- exposure (mean L*, contrast, clipped share);
- **near-white share** (L* > 90);
- **thin-line contrast**: the mean of the strongest 1% of black-top-hat responses (5 px ellipse) on L*, i.e.
  how much darker thin structures are than their surroundings;
- WireNet's wire-pixel count, entropy and flip disagreement, the reliability R and grade;
- span counts by CRS level, and the verdict.

**What glare does** (22 clean / glare pairs):

| Quantity | Clean (median) | Glare (median) | Glare range |
|---|---|---|---|
| Near-white share | 0.07 | 0.90 | 0.77–0.998 |
| Thin-line contrast (L*) | 25.9 | 8.5 | 2.4–20.8 |
| Wire pixels (×10⁴, model input) | 7.6 | 1.1 | 0.1–6.2 |
| Scored spans retained (glare / clean) | 1 | 0.32 | 0.04–2.2 |
| Reliability R | 0.78 | 0.68 | 0.53–0.81 |

- **Glare erases spans, and the model is confident about what is left.** WireNet sees *less* but is not
  more uncertain about it. R overlaps clean (0.53–0.81 against 0.66–0.82), so v2.9's grade stays A/B on
  photos that lost 70–95% of their spans. Entropy and flip disagreement measure uncertainty about the
  pixels the model labels; they cannot measure wires it never saw ("confident absence").
- **Outcome:** 5 of 22 validation photos went silently from AT RISK (clean) to NOT AT RISK (glare),
  graded A or B.
- **Exposure separates glare from every other condition perfectly** (near-white share ≥ 0.77 against
  ≤ 0.444 for every non-glare case, clean included).

### A control: bright sky without glare

A pure brightness rule would also reject photos of a bright overcast sky, where dark wires are still
*high*-contrast. To test that, a validation-only control condition `brightsky` was added
(`ml/stress.py`, `EXTRA_CONDITIONS`): the same exposure lift as glare, with local contrast kept. It is not
part of the 8-condition benchmark.

| Quantity | Glare (median) | Bright sky (median) |
|---|---|---|
| Near-white share | 0.90 | 0.84 |
| Thin-line contrast (L*) | 8.5 | 20.6 |
| Wire density (wire px / content px) | 0.011 | 0.066 |
| Scored spans retained | 0.32 | 1.00 |
| R | 0.68 | 0.78 |

Bright sky is as bright as glare, but the wires survive. **Brightness alone cannot be the gate.**

## 4.12.2 Glare: three candidate gates (`ml/experiments/glare_gate_eval.py`, `v210_glare_gates_val.json`)

Every candidate fires only on bright photos (near-white share ≥ 0.6, between the non-glare maximum of
0.444 and the bright-sky minimum of 0.684). A fired gate grades the photo *Retake recommended*. Under the
existing rule (`hazard.apply_reliability`), NOT AT RISK then becomes INCONCLUSIVE – RETAKE PHOTO, and a
hazard that was found is kept with a low-reliability note.

**Labels.**
- Positives (17): glare cases whose clean verdict was downgraded, or that lost more than half of their
  scored spans.
- Negatives (27): all 22 bright-sky cases and the 5 glare cases that kept their spans.

τ for M2 and M3 maximises balanced accuracy. Leave-one-photo-out (both conditions of a photo held out
together) shows how much of that is fitting.

| Mechanism | τ | Recall | Specificity | Leave-one-out acc. | Silent NOT AT RISK left (of 5) | Bright-sky photos made INCONCLUSIVE |
|---|---|---|---|---|---|---|
| M1 exposure only | – | 1.00 | 0.00 | 0.39 | 0 | 14 / 22 |
| **M2 photometric: thin-line contrast < τ** | **15.4 → 15** | **1.00** | **0.78** | **0.86** | **0** | **3 / 22** |
| M3 model evidence: wire density < τ | 0.016 | 0.76 | 1.00 | 0.80 | 2 | 0 / 22 |
| M2 or M3 | (15.4, 0.016) | 1.00 | 0.78 | 0.86 | 0 | 3 / 22 |
| M2 and M3 | (15.4, 0.016) | 0.76 | 1.00 | 0.80 | 2 | 0 / 22 |

**Decision: M2** (`quality.glare_gate`: near-white share ≥ 0.6 and thin-line contrast < 15 L* units).
1. **It is the only single mechanism with full recall.** Missing a washed-out hazard is the costlier error,
   because the result reads "safe".
2. **It is model-independent.** It measures the photo, so it cannot be fooled by the confident absence
   that defeated R. M3 depends on the model seeing wires, which is the very thing glare breaks.
3. **It has a physical meaning.** Conductors that stand out from the sky by less than 15 L* units — less
   than the L* difference between mid-grey and light grey — are not resolvable as dark lines.
4. Its leave-one-out accuracy (0.86) shows the threshold is not memorised. τ was rounded from 15.35 to 15.

### End-to-end on validation (`v210_glare_after_val.jsonl`)

| Condition | v2.9 verdicts | v2.10 verdicts | Silent NOT AT RISK (clean was AT RISK) |
|---|---|---|---|
| clean | 10 not / 10 mod / 2 high | **unchanged** | 0 → 0 |
| glare | 9 not / 8 inconclusive / 2 mod / 3 high | 2 not / **15 inconclusive** / 2 mod / 3 high | **5 → 0** |
| bright sky | 14 not / 4 mod / 4 high | 11 not / 3 inconclusive / 4 mod / 4 high | (5 → 5; see §4.12.4) |

The gate cannot fire on a non-bright photo, so clean verdicts and grades are unchanged by construction.
This was confirmed: the whole-pipeline golden check (§4.12.3) is identical on all 22 clean validation
photos.

The reliability reason for a gated photo is "Severe sky glare washed out the overhead conductors". The two
new measurements (`bright_share`, `line_contrast`) are stored with the photo-quality record.

## 4.12.3 Latency: profile and output-identical optimisation

### Where the time went (cProfile, densest validation pole, 64 spans, v2.9 code)

| Stage | Time | Note |
|---|---|---|
| Span assembly (`skeleton_instances`) | **2.64 s of 4.92 s** | 1,235 `np.nonzero` / `argwhere` calls on full 1024² masks: **1.99 s** |
| WireNet (2 passes, flip TTA) + YOLO | ~1.4 s | GPU (MPS) |
| Geometry and risk (catenary fits, hazard) | 0.60 s | Algorithm 4 skeletons; full-frame skeleton per power wire in `clearance_scale` |
| Diagnostic figure / PDF | 0 s in the upload path | rendered only when the PDF is requested |

**The bottleneck was algorithmic, not the model.** Span assembly scanned the whole image once per skeleton
piece (`argwhere(plab == i + 1)`), once per span (`span_lab == g`), and once per span for its bounding box:
O(pieces × pixels). A dense pole has hundreds of pieces before stitching.

### Changes (`app/pipeline/wire_instances.py`, `hazard.py`, `segment.py`)

- **Piece coordinates** come from one pass: label-sorted flat indices, split per label. A stable sort keeps
  the row-major order of `argwhere`.
- **Bridge seeds** are removed with one `isin`.
- **Span masks and confidences** are built inside each span's `find_objects` box: same pixels, same order,
  same float32 mean.
- **Bounding boxes** (`_crop_skeleton`, `segment._bbox`, `hazard._pixels`) come from row / column
  projections, not a full `nonzero`.
- **`mean_width_px`** skeletonises the mask's bounding box. Thinning is local, so this equals the
  full-frame skeleton (tested, image-border case included).
- **Stitching candidates** are generated for all endpoint pairs at once with numpy (`_stitch_candidates`),
  in exactly v2.8's loop order (i, j, end of i, end of j), so ties in the gap sort resolve identically. The
  v2.8 loop is kept verbatim in the tests as the specification and compared on randomised inputs with
  many ties.

### Proof of identical outputs

- **Span golden** (`ml/experiments/span_golden.py`): 828 spans on 22 validation photos; masks (SHA-256)
  and confidences identical. Extraction time 27.8 s → 7.0 s.
- **Whole-pipeline golden** (`ml/experiments/pipeline_golden.py`): the reference was written by **v2.9
  itself**, run from a separate git worktree of `5989657`. v2.10 reproduces every wire's index, class,
  status, CRS, level, polygons, centreline and fit, plus the vegetation, hazard verdict, reasons and
  factors, and the grade, on **22/22** validation photos. Total run time 85.8 s → 41.3 s.

  An earlier attempt at the reference silently ran the *new* code: `python -m` puts the working directory
  ahead of `PYTHONPATH`. It was caught by printing `ROOT_DIR` and discarded.

### Latency, paired v2.9 / v2.10 (`v210_latency_paired.json`)

In-process `run_analysis`, alternating v2.9 and v2.10 twice, 3 repetitions each, medians. Both versions saw
the same machine state.

| Pole (spans) | v2.9 | v2.10 | Change | Segmentation + spans | Geometry + risk |
|---|---|---|---|---|---|
| 213741 (61) | 4,566 ms | 2,963 ms | −35% | 3,905 → 2,258 | 617 → 561 |
| 192859 (63) | 4,207 ms | 2,602 ms | −38% | 3,701 → 2,004 | 388 → 461 |
| 224827 (64) | 4,660 ms | 2,860 ms | −39% | 4,034 → 2,143 | 569 → 625 |
| 145634, test (78) | 5,715 ms | 3,078 ms | −46% | 5,233 → 2,325 | 347 → 602 |

The geometry differences are within run-to-run noise. That stage was not the bottleneck and is dominated by
Chapter III's Algorithm 4, which was deliberately left untouched.

### Server latency and the machine

The end-to-end check (`scripts/server_check.py`, now with `--repeat`) was run three times:

| Run | Machine state | 81-span pole | All 14 cases |
|---|---|---|---|
| A, single pass | quiet | **2.28 s** | 1.33–3.46 s |
| B, single pass | OS daemons at 25–45% CPU each, load average 4.2 | 4.16 s | 1.77–6.29 s |
| C, median of 3 per case (`server_check_v210.json`) | load average 3.5–3.9 | 4.31 s | 1.25–6.21 s |

Run C shows spreads of 2.55 → 6.23 s **on identical input**. An in-process repeat of one photo, 12 times,
separated the causes:
- **GPU memory is flat** (1.19 GB): no leak.
- **CPU-only stages slow down as the run continues** (preprocess 48 → 105 ms, geometry 255 → 700 ms), and
  the run time drifts from 2.0 to 3.1 s.
- That is the fanless laptop throttling under sustained load, together with OS background activity, not the
  pipeline.

**Verdict on the target.**
- On a quiet machine the densest pole now takes 2.3 s end to end, against 4.58 s in v2.9. In-process
  steady state is about 2.0 s.
- Under sustained load on this laptop, a dense pole can still exceed 3.5 s.
- **The < 3.5 s target is met for single uploads, not guaranteed under sustained load on this hardware.**
  The remaining time is GPU inference (YOLO at 1024 plus two WireNet passes) and Chapter III's own
  geometry. Further cuts would change outputs or the method, for example dropping the flip TTA, which v2.8
  adopted for accuracy.

## 4.12.4 The other half of the "glare" problem: verdict instability

The motivating case from v2.9 (test photo 145557: HIGH when clean, NOT AT RISK at grade B under glare)
**is not caught by the gate, and should not be**.
- Under glare it is bright (0.87), but its thin-line contrast stays at 17.8, because its clean contrast is
  unusually high (44 against a validation median of 26).
- It kept 18 of its 21 scored spans: glare did not wash its wires out.
- Its verdict changed because its count of high spans went from 5 to 2, against a corroboration threshold
  k* = 4.

The validation data shows this is common and not specific to glare. Cases that kept at least 80% of their
clean scored spans:

| Condition | Spans retained | Verdict changed | AT RISK → NOT |
|---|---|---|---|
| jpeg | 16 | 7 | 6 |
| rainhaze | 21 | 9 | 7 |
| lowlight | 18 | 9 | 4 |
| lowres | 19 | 7 | 4 |
| motion | 9 | 6 | 1 |
| bright sky | 21 | 7 | 4 |
| defocus / glare | 5 / 2 | 0 / 1 | 0 / 0 |
| **all** | **111** | **46 (41%)** | **26** |

**The pole verdict changes in 41% of mildly perturbed photos** whose wires all survive. Per-span CRS levels
flip between measurements: v2.9 measured a 5% per-span test–retest discordance for the high band. With
about 30 spans and k* = 4–5, a pole's count sits within one or two flips of the threshold.

**A candidate fix, tested and rejected.** A *borderline band*: a NOT AT RISK pole whose high (or
medium-or-above) count is within m of k* reads AT RISK – MODERATE (borderline). The expected count change
between two measurements is √(n · 2p0) ≈ 1.4.

| Band m | Clean verdicts (not / mod / high) | Verdict changed (111 retained cases) | AT RISK → NOT |
|---|---|---|---|
| 0 (v2.9) | 10 / 10 / 2 | 46 (41%) | 26 |
| 1 | 9 / 11 / 2 | 56 (50%) | 28 |
| 2 | 5 / 15 / 2 | 52 (47%) | 22 |

It does not stabilise the verdict, and at m = 2 it gives back half of v2.9's gain. The instability lives in
the per-span measurement, so no count rule on top of it can remove it. **The fix belongs upstream:** a more
stable per-span sag estimate (e.g. averaged over flip and scale variants, or a sag measurement from the
probability map rather than the binary mask). That changes the measurement and is left as the main open
problem for the team.

**A documented option, not adopted.** M1 (exposure only) would also catch photo 145557, because every bright
photo gets "NOT AT RISK cannot be asserted". Its validation cost is 14 of 22 bright-sky NOT AT RISK verdicts
becoming INCONCLUSIVE, and 0 of 22 real clean photos (none is that bright). Switching to it *because* a test
photo failed would break the test-once rule, so it is recorded here as a policy choice for the team
(`quality.glare_line_contrast_max: 100` turns M2 into M1).

## 4.12.5 Test split, evaluated once (`v210_glare_test_gate_{off,config}.jsonl`, `server_check_v210.json`)

21 test photos, clean and glare, with the gate off (v2.9 behaviour) and on:

| | Clean verdicts | Glare verdicts | Silent NOT AT RISK under glare | … of which span dropout |
|---|---|---|---|---|
| gate off (v2.9) | 7 not / 7 mod / 7 high | 13 not / 6 inconclusive / 2 mod | 7 | 2 |
| **gate on (v2.10)** | **unchanged** | 4 not / **15 inconclusive** / 2 mod | **3** | **0** |

The test result matches the validation diagnosis. **Every span-dropout case is caught** (2 → 0). The 3
silent cases left all kept their spans: they are the instability mode of §4.12.4, the motivating photo 145557
among them. v2.9's clean verdicts are unchanged.

Segmentation (and so the 8-condition span benchmark) is unchanged by construction. The whole-pipeline golden
check proves v2.10 = v2.9 output on clean photos, so the 8-condition test benchmark was not re-run.

## 4.12.6 Scoped linting

`ruff check --fix .` in v2.9 rewrote two untracked files under `.agents/` because `pyproject.toml` had no
exclusions. `[tool.ruff]` now has `extend-exclude` (`.agents`, `.claude`, `.venv`, `venv`, `datasets`,
`runs`, `var`, `models`) and `force-exclude = true`, so the exclusion holds even when an editor or pre-commit
hook passes a path explicitly. Verified: `ruff check .agents/...` reports "No Python files found", and a
test pins the configuration.

## 4.12.7 Limitations

- **Verdict instability is the largest remaining reliability problem** (§4.12.4). It is quantified, not
  solved.
- **The glare gate is calibrated on a synthetic glare model** (exposure ×1.6 + 60, local contrast halved),
  and its control is synthetic too. Real sun-in-frame glare (flare, haloes) has not been tested.
- **Latency depends on the machine state.** The numbers are from one fanless laptop.
- **22 validation photos.** The leave-one-out accuracy (0.86) is reported for exactly this reason.

## 4.12.8 Reproduction

```
python -m ml.experiments.glare_diag --out docs/thesis/results/v210_glare_diag_val.jsonl
python -m ml.experiments.glare_diag --out docs/thesis/results/v210_glare_diag_val.jsonl --conditions brightsky
python -m ml.experiments.glare_gate_eval --diag docs/thesis/results/v210_glare_diag_val.jsonl --out docs/thesis/results/v210_glare_gates_val.json
python -m ml.experiments.glare_diag --out docs/thesis/results/v210_glare_after_val.jsonl --conditions clean glare brightsky
python -m ml.experiments.span_golden --write runs/span_golden_val.json      # before; --check after
python -m ml.experiments.pipeline_golden --write ref.json                   # run inside a worktree of 5989657
python -m ml.experiments.pipeline_golden --check ref.json
python -m ml.experiments.latency_profile --images <stems> --reps 3 [--cprofile]
python -m ml.experiments.glare_diag --split test --conditions clean glare --glare-gate off --out docs/thesis/results/v210_glare_test_gate_off.jsonl
python -m ml.experiments.glare_diag --split test --conditions clean glare --out docs/thesis/results/v210_glare_test_gate_config.jsonl
python -m scripts.server_check --repeat 3 --out docs/thesis/results/server_check_v210.json
```
