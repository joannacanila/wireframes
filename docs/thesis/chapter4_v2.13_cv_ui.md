# Chapter 4 (continued) — v2.13: Ground-Structure Suppression, Field Guidance and the Field Screener Redesign

Branch `main`, from `4dbc6ca`. The shipped WireNet-R34 (`models/wirenet-r34.pt`, SHA-256 prefix `ab2353c7`) is
frozen: nothing in v2.13 retrains or modifies it, and YOLO11-seg (vegetation) and the Chapter III mathematics
(catenary fit, CRS, VNI) are unchanged. v2.13 works only before and after the model: pre-processing, a pretrained
scene gate and span post-processing. Every threshold was decided on the 22 validation photos, and a change was
adopted only if span-level tolerant mAP50 on validation stayed at or above the shipped **0.0397**. The 21 test
photos are run once, after v2.13 is frozen (§4.15.11).

**Hardware.** All official latency figures in this report were measured on the same **Apple Silicon M1 laptop
with 8 GB of unified memory** (PyTorch MPS) used since v2.6. The unit tests, the browser verification (§4.15.9) and
the documentation build for v2.13 were also run on a Windows 11 PC (Python 3.13.17, CPU only). Windows timings
are not comparable with the M1 and none appear in any latency table here.

| Mission | What was found | Outcome |
|---|---|---|
| Ground gate (pretrained scene parsing) | UperNet-ConvNeXt-T (ADE20K, MIT licence) labels road / sidewalk / floor / earth / … pixels; a span lying ≥ 50% on them is a kerb, manhole or pavement edge. Validation: removes 0 spans (tolerant mAP50 unchanged, 0.0397). Live problem photo: removes the road and manhole traces, including a false CRS 8.6 span; verdict AT RISK – HIGH → NOT AT RISK. | **Adopted** (`ground_gate: true`) |
| Field guidance | One `action` per unmeasured or edge-truncated span, and one photo-level `retake_guidance` when ≥ 40% of the spans could not be measured. Derived from photo size, span position and failure code only. | **Adopted** |
| Always-on CLAHE on L* | Tolerant mAP50 0.0397 → 0.0356 (strict 0.0068 → 0.0117). | Rejected |
| Oriented black-hat tubeness blend | β = 0.25 / 0.5 / 1 / 2: 0.0387 / 0.0367 / 0.0280 / 0.0336. | Rejected |
| Span bridging (gap / angle) | 12° variants 0.0357–0.0381; 60 px / 25° 0.0407 (within noise of the shipped 70 px / 25°). | Shipped 70 px / 25° kept |
| Closed-loop, isolated-below-network, upward-bulge rules | Loop and isolation rules remove no labelled wire on validation but remove real wires on unlabelled street photos (labels are incomplete); bulge rules remove labelled wires. | Rejected |
| Field screener redesign (`/workspace`) | Dark theme, Geist, gold accent, glass panels, hero verdict card, view toggles + opacity, collapsed diagnostics, 200–300 ms motion, shimmer loader. Browser-verified in 16 scenario × theme × viewport combinations; one phone-width bug found and fixed. | **Shipped** |
| Latency (M1) | Gate adds 0.26 s when it runs; a noisy first profile showed larger jumps on two photos. | **[PENDING QUIET M1 RUN]** |

---

## 4.15.1 The failure: WireNet traces the ground

WireNet-R34 segments thin, dark, elongated structures. In the training crops these are almost always conductors
against the sky. In a live street photo taken from pavement level, the lower half of the frame also contains thin
dark lines: kerb edges, manhole rims, pavement joints, road markings and railings. WireNet traces them, the span
extractor turns them into "spans", and the catenary fit scores them. Because such a trace is rarely a clean
hanging curve, it can come out as a strongly sagging span with a high CRS.

The v2.13 problem photo (`images (1).jpeg`, 452 × 678 px, a live upload without labels) showed exactly this: road
and manhole traces in the bottom of the frame, one of them scored at **CRS 8.6**, drove the verdict to
**AT RISK – HIGH** although no overhead conductor in the photo was sagging.

No retraining was allowed, so the remedy had to come from outside the model: (a) image pre-processing that makes
real wires easier to separate (§4.15.4), (b) a pretrained model that knows where the ground is (§4.15.2), or
(c) geometric rules on the extracted spans (§4.15.5–4.15.6).

## 4.15.2 Ground gate (`app/pipeline/scene.py`, `HybridSegmenter._ground_gate`)

**Model choice.** A semantic scene parser trained on ADE20K (150 classes, including *road*, *sidewalk*, *floor*,
*earth*, *path*, *grass*, *field*, *sand*, *dirt track*, *runway*, *rug*) labels every pixel. Two pretrained parsers
were considered:

| Candidate | Licence | Decision |
|---|---|---|
| SegFormer-B0 (ADE20K) | NVIDIA Source Code Licence: non-commercial, research only | Rejected: incompatible with a deployable screener |
| UperNet + ConvNeXt-Tiny (`openmmlab/upernet-convnext-tiny`, 60 M parameters) | MIT | **Adopted** |

**Rule.** For every extracted wire span, the share of its mask pixels that lie on ground classes is computed
(`ground_share`). A span with `ground_share ≥ ground_gate_share` (**0.5**) is not scored. It is returned in the
API as an entry of `suppressed_spans` with its class, confidence, bbox, polygons, ground share, `reason_code:
"ground_structure"` and the plain-language reason *"Lies on the road or pavement: a kerb, manhole or pavement
edge, not an overhead wire"*. The result also carries the flag `ground_structures_suppressed`. Overhead
conductors are seen against sky, buildings or trees, so a span lying mostly on the road surface is not an
overhead conductor.

**Resolution and cost.** UperNet's pyramid pooling (bins 1, 2, 3, 6) needs a feature map divisible by 6 on MPS;
at 384 px the stride-32 map is 12 × 12. On the M1 the parser runs in **0.26 s at 384 px** (0.54 s at 576 px), and
its ground mask agrees with the 576 px mask on **98.8%** of the problem photo's pixels.

**Skip rule.** Ground lies below the wires in a pole photo. When no span's bounding box reaches the lower half of
the frame, the parser is not run at all (output identical, 0.26 s saved).

**Fail-open.** If the parser cannot load (no network on first run, missing `transformers`), every span is kept and
the result is flagged `ground_gate_unavailable`. The gate never blocks a screening.

**Configuration.** `config/wireframe.yaml`: `ground_gate: true`, `ground_gate_share: 0.5` (defaults in
`app/core/config.py` keep the gate off, so older configurations reproduce exactly).

## 4.15.3 Ground gate: validation and the problem photo

**Validation (`v213_filters_val.json`, 828 spans, 96 true-positive spans).** The validation photos are crops of
the wire region and contain no ground, so the gate removes nothing:

| Rule | Spans removed | True-positive spans removed | Tolerant mAP50 |
|---|---|---|---|
| none (shipped v2.12) | — | — | 0.0397 |
| ground share ≥ 0.5 (**adopted**) | 0 | 0 | 0.0397 |
| ground share ≥ 0.8 | 0 | 0 | 0.0397 |

The adoption rule (validation tolerant mAP50 ≥ 0.0397) is met with equality. The whole-pipeline golden check that
confirms the validation outputs are byte-identical with the gate on is **[PENDING QUIET M1 RUN]** (§4.15.11).

**Problem photo (unlabelled, M1 session).** With the gate on, the road and manhole traces are suppressed,
including the false CRS 8.6 span, and the verdict changes from **AT RISK – HIGH** to **NOT AT RISK**. The traces stay
visible in the diagnostics drawer under "Ground structures ignored", so a reviewer can see what was removed and why.
Because the photo has no labels, this is a visual check, not a metric. The figure-generation script is
`ml/experiments/v213_unlabelled.py`.

## 4.15.4 Pre-processing candidates (`ml/experiments/v213_enhance.py`, `v213_enhance_val.jsonl`)

Both candidates aim to raise the contrast of thin dark structures before WireNet sees the photo. Both were applied
to every validation photo; neither is adopted.

| Variant | Extra time | Strict mAP50 | Tolerant mAP50 | Spans | Power recall | Telecom recall |
|---|---|---|---|---|---|---|
| shipped | — | 0.0068 | **0.0397** | 828 | 0.213 | 0.088 |
| CLAHE on L*, always on | 21.6 ms | 0.0117 | 0.0356 | 841 | 0.178 | 0.091 |
| black-hat blend β = 0.25 | 48.9 ms | 0.0068 | 0.0387 | 826 | 0.197 | 0.094 |
| black-hat blend β = 0.5 | 48.9 ms | 0.0062 | 0.0367 | 836 | 0.204 | 0.091 |
| black-hat blend β = 1 | 48.9 ms | 0.0062 | 0.0280 | 853 | 0.159 | 0.079 |
| black-hat blend β = 2 | 48.9 ms | 0.0064 | 0.0336 | 888 | 0.201 | 0.073 |

CLAHE raises strict mAP50 (exact-boundary matches) but lowers the tolerant metric that the adoption rule uses, and
lowers power recall. WireNet was trained on unequalised photos, so equalisation moves the input away from its
training distribution. The oriented black-hat (tubeness) blend loses at every strength. The v2.10 conditional
enhancement routing is unchanged.

## 4.15.5 Span bridging (`v213_bridging_val.json`)

Fragmented spans could be healed by a looser collinear gap bridge. The shipped extractor bridges gaps up to
`span_max_gap` = 70 px within `span_gap_angle_deg` = 25° (v2.8).

| Max gap | Angle | Tolerant mAP50 | Strict mAP50 | Spans | Power recall | Telecom recall |
|---|---|---|---|---|---|---|
| **70 px** | **25°** (shipped) | **0.0397** | 0.0068 | 828 | 0.213 | 0.088 |
| 60 px | 25° | 0.0407 | 0.0067 | 831 | 0.213 | 0.094 |
| 100 px | 25° | 0.0378 | 0.0068 | 795 | 0.210 | 0.079 |
| 60 px | 12° | 0.0367 | 0.0067 | 859 | 0.197 | 0.097 |
| 70 px | 12° | 0.0379 | 0.0068 | 862 | 0.204 | 0.094 |
| 100 px | 12° | 0.0381 | 0.0068 | 855 | 0.210 | 0.094 |
| 150 px | 12° | 0.0357 | 0.0069 | 845 | 0.197 | 0.094 |

A stricter 12° angle loses at every gap. 60 px / 25° is +0.001, three spans different from the shipped setting,
which is within photo-to-photo noise on 22 photos (v2.12 §4.14.4). Changing a shipped parameter for a gain of that
size after this many tries would be selection on noise, so **70 px / 25° is kept**.

## 4.15.6 Geometric span rules (`ml/experiments/v213_filters.py`, `v213_filters_val.json`)

Per-span features were logged for all 828 validation spans (`v213_span_features_val.jsonl`): ground share, number
of enclosed holes in the span mask, upward bulge, quadratic-fit residual, length, isolation from other wires and
distance below the wire network. Each candidate rule was then applied:

| Rule | Removed | True-positive removed | Tolerant mAP50 |
|---|---|---|---|
| closed loop (≥ 1 enclosed hole) | 16 | 0 | 0.0398 |
| isolated ≥ 40 px and ≥ 40 px below the network | 2 | 0 | 0.0397 |
| isolated ≥ 80 px and ≥ 80 px below the network | 2 | 0 | 0.0397 |
| upward bulge ≥ 3 px and ≥ 2% | 82 | 2 | 0.0388 |
| upward bulge ≥ 5 px and ≥ 4% | 37 | 1 | 0.0393 |

The bulge rules remove labelled wires and are rejected on validation. The loop and isolation rules remove no
labelled wire and do not lower the metric. On the unlabelled street photos (the problem photo, the night pole and
the demo samples, `v213_unlabelled.py`), however, they **removed real wires**: service drops and isolated low
conductors that the validation labels do not include. The validation labels are incomplete for exactly the kind
of wire these rules target, so "0 true positives removed" is not evidence that the rules are safe. Both are
**rejected**.

## 4.15.7 Known limitation: fence and railing traces

One railing trace on the problem photo (span #18) is not removed. ADE20K has a *fence* / *railing* class, but at
the parser's resolution the railing in front of the facade is labelled *building*. A rule on *fence* would therefore
not fire here, and a rule on *building* would remove real cables seen against facades. No cue was found that
separates the railing from a cable without also removing real cables. The trace is **unmeasured** (it fails the
catenary checks), so it appears in the span list with its reason but does not affect the verdict. Removing such
traces needs a model that sees them as non-wires, which is retraining and out of scope for v2.13.

## 4.15.8 Field guidance (`app/services/reasons.py`)

v2.11 gave every unmeasured span a reason. v2.13 adds what the operator can do about it:

- **Per span (`wire_action`).** For an unmeasured span with a short chord (`chord_below_precision_gate`,
  `too_short`) on a photo under 1000 px on its short side, the action is to upload the full-resolution photo, naming
  its size (e.g. *"Upload the full-resolution photo (this one is 452 px on its short side), or move closer so the
  span looks longer"*). On a full-resolution photo it is "move back or centre the span" if the span touches the
  frame edge, otherwise "move closer or zoom in". A span that does not follow one curve (`residual_too_large`,
  `non_positive_a`) gets "shoot from a few metres along the street, where this wire does not cross others"; a fit
  that did not converge or is degenerate gets "shoot the span from the side". A scored span touching the frame
  edge gets "step back so both poles of this span are in the frame". Service drops get no action.
- **Per photo (`retake_summary`).** When ≥ 40% of the non-drop spans could not be measured, the result carries one
  `retake_guidance` sentence: *"N of M spans could not be measured. Best next photo: …"*, using the most common
  span action. The field screener shows it as the single next action.

The guidance is derived from photo size, span position and failure code only. It never changes a measurement or a
verdict.

## 4.15.9 Field screener redesign and browser verification

**Design.** `/workspace` (`app/web/field.html`, `assets/field.css`, `assets/field.js`) was rebuilt on the landing
page's design tokens: dark by default (light one tap away, for direct sunlight), Geist / Geist Mono, gold accent,
glass panels. The result screen is ordered for a two-second read:

1. a **hero verdict card**: status pill (**HAZARD DETECTED**, **REVIEW REQUIRED**, **NOT AT RISK**,
   **RETAKE PHOTO**, display labels for the unchanged API verdicts), worst CRS, spans measured out of total, and
   **one next action**;
2. the photo with an **Original / Detected spans / Full wire mask** segmented control and an **overlay opacity
   slider** (`OverlayViewer.setOpacity`: overlays fade toward the photo as photo · (1 − o) + overlaid · o);
3. every span grouped by urgency, each unmeasured one with its reason and action, linked both ways to the canvas;
4. a **collapsed diagnostics drawer** for engineers and reviewers: sag corroboration, photo reliability (entropy,
   flip disagreement, L*, contrast, noise), WireNet mask coverage, ground structures ignored, timings, weights and
   configuration fingerprint.

State changes animate in 200–300 ms, and a shimmer skeleton of the result layout is shown while the analysis runs,
so nothing jumps when it arrives. `prefers-reduced-motion` disables the motion.

**Verification method (`scripts/ui_check.py --mock`).** The detection models were not on the verification PC.
The check therefore uploads the bundled synthetic sample with its reference polygons once, to obtain a genuine
`/result` payload from the running server. It then derives four scenarios from it: HAZARD DETECTED (two spans in
the high band), REVIEW REQUIRED (the sample's own verdict), NOT AT RISK with two `suppressed_spans`, and RETAKE
PHOTO (three spans with `chord_below_precision_gate`, their actions and the photo-level `retake_guidance`).
Playwright intercepts `/api/health`, the upload, the status poll (held at *processing* for two polls so the loader
is observed), `/result`, the wire-mask PNG and the PDF. Each scenario was checked in Chrome in the dark and the
light theme, at 1440 × 900 and at 390 × 844 (phone, touch).

**Result (`results/ui_check_v213.json`): 16 / 16 combinations pass**, plus the check that the two themes resolve to
different token sets (body background `rgb(12, 13, 15)` dark, `rgb(243, 242, 239)` light). In every combination:

| Check | Result |
|---|---|
| Console errors and uncaught page errors | 0 |
| Shimmer skeleton running (`fx-shimmer`) and `aria-busy` while processing | yes |
| Status pill text matches the scenario | 16 / 16 |
| Worst CRS and spans measured / total KPIs; one next action | yes; RETAKE: next action = `retake_guidance` |
| `[data-view]` Original / Detected spans / Full wire mask switch the viewer layers; full mask image loads | yes |
| `#opacity` sets the viewer opacity (40 → 0.40, label "40%"); marked off in Original | yes |
| `#diag` collapsed by default; opens (`aria-expanded="true"`, height > 40 px) and closes | yes |
| Every unmeasured span shows a reason | yes |
| Suppressed traces listed in diagnostics ("94% on road / pavement"); KPI "2 ground traces ignored" | yes |
| Transition / animation durations: drawer 280 ms, view thumb 260 ms, opacity control 200 ms, stage 280 ms | within 200–300 ms |
| Table → canvas and canvas → table selection (desktop) | yes |
| Horizontal page scroll; span list fits its panel; filters hide rows | none; fits; yes |

**Bug found and fixed.** At 390 px the spans table was wider than its panel and scrolled sideways inside it,
clipping each span's reason and next step (the text a field operator most needs). Below 520 px each span is now a
card: id, conductor and CRS on one line, then status, reason and action at full width. The checker gained two
checks (`spans_table_fits`, `filter_hides_rows`) that would have caught it.

Figures: `figures/v213_ui_hazard_dark_desktop.jpg`, `v213_ui_not_at_risk_light_desktop.jpg`,
`v213_ui_retake_dark_phone.jpg`, `v213_ui_loader_dark_desktop.jpg`.

The mocked check verifies the interface, not the models. The real-photo mode of the same script
(`python -m scripts.ui_check --base … photos…`) is to be re-run on the M1 with the models installed.

## 4.15.10 Latency on the 8 GB M1 (`ml/experiments/latency_profile.py`)

Target for v2.13: under 3.0 s per photo on the M1. A first profile, gate off → on, was taken while the machine was
under load (load average 5.8):

| Photo set | Gate off | Gate on |
|---|---|---|
| Validation, median | 1.73 s | 2.29 s |
| Validation, p90 | 1.91 s | 2.53 s |
| Validation, max | 2.21 s | 2.72 s |
| Dense pole | 1.97 s | 2.53 s |
| Problem photo | 1.69 s | 2.13 s |
| Night pole | 2.29 s | 4.23 s |
| Demo photo 2 | 1.98 s | 3.24 s |

The parser itself costs 0.26 s when it runs. The validation median rose by 0.56 s and the night pole by 1.94 s,
much more than that, which points to contention from the concurrent load rather than to the gate. These numbers
are therefore **not** used to claim that the target is met or missed. The quiet re-measurement (no other process
running, same photos, gate off and on) is **[PENDING QUIET M1 RUN]**, and its raw output goes to
`results/v213_latency_val.json`. Until then the night pole (4.23 s) and demo photo 2 (3.24 s) remain open against the
3.0 s target.

## 4.15.11 Golden check and the test split

- **Whole-pipeline golden check, validation, gate on**:
  `python -m ml.experiments.pipeline_golden --check docs/thesis/results/v210_pipeline_golden_v29_val.json`.
  Expected: 22 / 22 identical (the gate removes no validation span). **[PENDING QUIET M1 RUN]**
- **Test split, once, frozen v2.13** (`v2.13` config in `scripts/stress_test_all_conditions.py`: the shipped
  configuration, i.e. v2.12 plus the ground gate):
  `python -m scripts.stress_test_all_conditions --split test --conditions clean lowlight night --configs v2.13 --out docs/thesis/results/v213_spans_test.jsonl`.
  **[PENDING QUIET M1 RUN]**

Neither run was possible on the verification PC: the dataset (`datasets/wireframe_seg/`) and the WireNet checkpoint
are not stored there, and the test split must be run exactly once on the frozen configuration.

## 4.15.12 Software verification

On Windows 11, Python 3.13.17, CPU: **508 passed, 4 skipped** (the four skips need `ultralytics` /
`albumentations` from `requirements-ml.txt`), and `ruff check app ml scripts tests` is clean. New tests:

- `tests/test_v213_ground_gate.py` (11 tests): a fake scene parser (offline, no weight download); suppression and the
  recorded fields; inclusive 0.5 threshold; gate off; lower-half skip; fail-open when the parser is unavailable;
  load failure reported; shipped configuration.
- `tests/test_v213_reasons.py` (25 tests): `touches_edge`, every `wire_action` branch, `retake_summary` (40%
  threshold, service drops, most common action), and through the API: `suppressed_spans`, the
  `ground_structures_suppressed` / `ground_gate_unavailable` flags, that suppressed traces are never listed as wires,
  per-span `action` and `retake_guidance`.
- `tests/test_v213_viewer_opacity.py` (8 tests): Node evaluates `viewer.js`; `setOpacity` clamps to [0, 1] and
  redraws; the photo is repainted with alpha 1 − o only below full opacity, with overlays on and a photo loaded.
- `tests/test_v211_field_ui.py`: the console asset check now asserts the v2.13 redesign.

## 4.15.13 Limitations

- The ground gate is verified on validation only in the sense that it changes nothing there; its benefit was shown
  on unlabelled live photos. A labelled set of full street-level photos, with ground in the frame, would be needed
  to measure how many false spans it removes and whether it ever removes a real low conductor (for example a
  service drop crossing in front of the road).
- Fence and railing traces are not removed (§4.15.7).
- The parser adds a 60 M-parameter model and 0.26 s when a span reaches the lower half of the frame. The latency
  target is open until the quiet M1 run (§4.15.10).
- The rejected loop and isolation rules show that validation labels are incomplete for low, isolated wires, so
  "no true positive removed" on validation is weak evidence for any suppression rule.
- The browser verification used mocked results derived from one real payload; it verifies the interface, not the
  detections.

## 4.15.14 Reproduction

```bash
pip install -r requirements-dev.txt playwright           # tests, ruff, UI check
python -m pytest
ruff check app ml scripts tests
uvicorn app.main:app --port 8765 &                        # any server; models not required for --mock
python -m scripts.ui_check --mock --base http://127.0.0.1:8765 --shots runs/ui_shots_v213 \
    --out docs/thesis/results/ui_check_v213.json
# On the M1 with the dataset and models (requirements-ml.txt):
python -m ml.experiments.v213_enhance --out docs/thesis/results/v213_enhance_val.jsonl
python -m ml.experiments.v213_filters --out docs/thesis/results/v213_span_features_val.jsonl
python -m ml.experiments.v213_filters --analyse docs/thesis/results/v213_span_features_val.jsonl \
    --summary docs/thesis/results/v213_filters_val.json
python -m ml.experiments.v213_unlabelled --images runs/v213_inputs/*.jpg runs/demo_samples/* \
    --out docs/thesis/results/v213_unlabelled.json --figures docs/thesis/figures
# quiet machine, nothing else running; gate off, then on
python -m ml.experiments.latency_profile --images <stems or paths> --reps 5 --no-ground-gate --out runs/lat_off.json
python -m ml.experiments.latency_profile --images <stems or paths> --reps 5 --out runs/lat_on.json
python -m ml.experiments.pipeline_golden --check docs/thesis/results/v210_pipeline_golden_v29_val.json
python -m scripts.stress_test_all_conditions --split test --conditions clean lowlight night --configs v2.13 \
    --out docs/thesis/results/v213_spans_test.jsonl      # once
```
