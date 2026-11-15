# Chapter 4 — Quantitative Results (WIREFRAME v2.7)

Development results on the team dataset. These figures come from a 145-image dataset and a
21-image test split. They describe the prototype's current state and the reasons for it. They are
not a validation of the Table 3.10 targets, and none of those targets is met. Every number below
can be traced to a file in `docs/thesis/results/`.

---

## 4.1 Data

### 4.1.1 Datasets used

| Dataset | Author / platform | URL | Licence | Images contributed | Class mapping | Selection rationale |
|---|---|---|---|---|---|---|
| Wireframes-Prelabelled-Datasets (export of 2026-11-14 12:02 GMT, "No pre-processing") | Research team; Roboflow Universe (workspace `ravens-workspace-g6fye`) | https://universe.roboflow.com/ravens-workspace-g6fye/wireframes-prelabelled-datasets | CC BY 4.0 | 145 (train 102 / val 22 / test 21) | Native: `Vegetation`, `abandoned-wire`, `power-distribution-lines`, `telecom-cable` (fiber drops are labelled `telecom-cable`) | The only source with span-level polygon labels: 11–12% of power and telecom polygons are fragments, compared with 72–86% in the 965-image v1/v2i exports |

**Pretrained weights and libraries** (no training data from these is used):

| Component | Source | Licence | Use |
|---|---|---|---|
| YOLO11s-seg, COCO-pretrained | Ultralytics 8.4.210 | AGPL-3.0 | Span detector initialisation |
| ResNet-34, ImageNet-pretrained | torchvision, via segmentation-models-pytorch 0.6.0 | BSD-3 / MIT | WireNet encoder |
| SAM 2.1-tiny | Meta, via Ultralytics | Apache-2.0 | Benchmarked only (§4.3.2); not used |
| Albumentations 2.0.8 | Albumentations | MIT | Training-time blur and contrast augmentation |

### 4.1.2 External datasets screened and rejected

The search covered Kaggle, Roboflow Universe, GitHub, Zenodo, Mendeley Data and IEEE DataPort, on
2026-11-14. A source had to pass five gates:
1. polygon instance masks
2. a ground-level viewpoint (§3.4.2 step 2)
3. span-level rather than fragment labels
4. an allowed licence
5. classes that map onto the four WIREFRAME classes

No external source passed. The full register is in `ml/sources.yaml` under `rejected:`.

| Dataset | Reason rejected |
|---|---|
| Rural distribution power line vegetation management (Zenodo 18392898, CC BY 4.0, 2,404 images) | Files restricted; access must be requested from the authors |
| SEwire "wire" (Roboflow, CC BY 4.0) | Not inspected: no Roboflow API key. Its classes (`Tangled-wires`, `Electrical-wires-across-trees`) do not distinguish power from telecom |
| TTPLA (GitHub) | Aerial drone viewpoint of transmission towers |
| VEPL (Zenodo 7800234) | Top-down orthomosaic tiles |
| WireSegHR (CVPR 2023) | A single generic wire class; no licence or download located |
| Electric Wires Dataset (Kaggle) | Synthetic chroma-key cable images |
| OHL-UK (BMVC 2025) | Labels poles and warning signs only |
| lonlonago LabelMe sets (GitHub) | Commercial listings; no licence |

The thesis manuscript cites no Kaggle dataset; every source in Table 3.4 is Roboflow Universe.

### 4.1.3 Curation audit (`docs/thesis/results/dataset_audit_v27.json`)

- **Steps:** 145 images in; 0 excluded for viewpoint; 0 exact or near duplicates; 0 corrupt label
  lines; 0 unmapped labels; 0 sent to the annotation queue.
- **Instances:**

  | Class | Instances | Median extent | Fragment share |
  |---|---|---|---|
  | Vegetation | 220 | – | – |
  | abandoned-wire | 27 | 0.17 | 70% |
  | power-distribution-lines | 1,801 | 0.52 | 11.5% |
  | telecom-cable | 2,441 | 0.47 | 10.6% |

- **Geometry:** 145 distinct native sizes, aspect ratios 0.76–3.87. All images were written as
  1024 × 1024 letterboxes ("Fit") with grey (114) padding, the padding the server uses at inference.
- **Split:** 102 / 22 / 21, balanced by wire-density tertile (25.7 and 34 instances per image).
- **Measured wire width at 1024 px:** median 2.1 px for power and telecom, 7.1 px for Vegetation,
  8.7 px for abandoned-wire.

---

## 4.2 Methods

### 4.2.1 Models evaluated

| Model | Architecture | Input | Training |
|---|---|---|---|
| **v2.6 baseline** | YOLO11s-seg | 640 | 30 epochs on the earlier 99-image export (git tag `v2.6-baseline`) |
| **YOLO-1024** | YOLO11s-seg | 1024 | MPS, batch 1 with gradient accumulation to 64. 16 epochs, stopped early by patience 15 (best epoch 1, which is likely a fluke), then 11 more epochs from epoch 16; best is global epoch 22. Blur and contrast augmentations included |
| **WireNet** | U-Net with ResNet-34 encoder, 5 classes, full-resolution output | 1024 (512 tiles in training) | 40 epochs, 2.63 h on MPS; best epoch 29. Loss: class-weighted cross-entropy + soft Dice + clDice. Same blur and contrast augmentations |
| **Hybrid (deployed)** | YOLO-1024 supplies Vegetation; WireNet supplies wire pixels; spans are extracted from WireNet's class map (§4.2.2) | 1024 | – |

YOLO11m-seg could not be trained on this Mac. It ran out of unified memory (8 GB) at batch 4 and
640 px.

### 4.2.2 Span extraction from WireNet (`app/pipeline/wire_instances.py`)

1. For each wire class: take the class mask, drop components under 30 px, then thin it with
   Zhang-Suen.
2. Cut the skeleton at junction pixels. Junction points within 10 px count as one crossing, since a
   shallow crossing thins into two junction points joined by a short bridge.
3. Re-join pieces that meet at a crossing when their end directions are collinear within 25°. A wire
   continues straight through a crossing.
4. Assign each mask pixel to its nearest piece using a Euclidean distance transform.
5. A span's confidence is the mean class probability over its pixels. Spans shorter than 30 px are
   dropped.

### 4.2.3 Metrics

- **Strict (Equations 5 and 12–14):** an instance counts as a true positive at mask IoU ≥ 0.5,
  matched per class. AP is the 101-point COCO interpolation. P, R and F1 are reported at the operating
  threshold, `conf_min` = 0.20, which was chosen on the validation split by sweeping 0.05–0.75 for the
  best strict mean F1.
- **Tolerant (supplementary; not Equation 5):** both masks are widened by 2 px (at 1024 px) before
  computing IoU. On a 2 px wire, a one-pixel offset alone caps strict IoU at about 0.33.
- **Ultralytics validation:** box and mask metrics computed on the 1/4-resolution mask grid, with
  NMS IoU 0.45 and retina masks.
- **Degraded test:** every test image receives one blur, chosen deterministically from its file name.
  The blur is motion (1.2% of the long side), defocus (radius 0.4%) or Gaussian (σ 0.3%). On top of
  that, contrast is scaled by 0.50–0.65 and brightness shifted by ±20 (`ml/degrade.py`).
- **AP floor:** predictions under confidence 0.01 are excluded from AP. This is recorded in every
  result.

---

## 4.3 Results

### 4.3.1 Why YOLO-seg masks cannot represent wires

The question was what IoU a *perfect* mask on YOLO's 1/4-resolution grid would reach, once
upsampled and compared with the full-resolution label. Measured on 2,129 labelled instances:

| Class | Median width at 1024 | Ceiling IoU (median) | Instances able to reach IoU ≥ 0.5 |
|---|---|---|---|
| power-distribution-lines | 2.1 px | 0.40 | 17% |
| telecom-cable | 2.1 px | 0.41 | 21% |
| Vegetation | 7.1 px | 0.72 | 76% |
| abandoned-wire | 8.7 px | 0.78 | 67% |

So for the two main wire classes, YOLO-seg's mask AP50 is capped at roughly 0.2, however well the
model is trained. This matches the observed result: Ultralytics mask AP50 for power and telecom is
0.000 for both YOLO models (§4.3.4).

### 4.3.2 SAM 2.1 prompted with the true boxes (upper bound for a "detector → SAM" pipeline)

Tested on 6 validation images. "Box + points" adds 3 points sampled along each labelled skeleton.

| Prompt | Class | n | Median IoU | IoU ≥ 0.5 |
|---|---|---|---|---|
| box | power-distribution-lines | 58 | 0.08 | 2% |
| box | telecom-cable | 62 | 0.04 | 0% |
| box + points | power-distribution-lines | 58 | 0.07 | 2% |
| box + points | telecom-cable | 63 | 0.04 | 0% |
| box + points | Vegetation | 26 | 0.52 | 58% |
| box + points | abandoned-wire | 3 | 0.65 | 100% |

Even with perfect prompts, SAM 2.1 does not segment 2 px wires. It also took about 40 s per image
on the CPU.

### 4.3.3 WireNet pixel-level segmentation (validation, 22 images, best epoch 29)

| Class | Pixel IoU | Tolerant pixel IoU (2 px) |
|---|---|---|
| power-distribution-lines | 0.265 | 0.525 |
| telecom-cable | 0.232 | 0.418 |
| Vegetation | 0.204 | 0.203 |
| abandoned-wire | 0.036 | 0.045 |

### 4.3.4 Instance segmentation on the held-out test split (21 images)

**Overall:**

| Model | Strict mask mAP50 | Strict mAP50-95 | Tolerant mAP50 |
|---|---|---|---|
| v2.6 baseline (640) | 0.0005 | 0.0001 | 0.0005 |
| YOLO-1024 | 0.0198 | 0.0042 | 0.0199 |
| **Hybrid (deployed)** | **0.0204** | **0.0043** | **0.0375** |

**Per class, hybrid** (P, R and F1 at `conf_min` 0.20):

| Class | Labelled | Predicted | Strict P / R / F1 / AP50 | Tolerant P / R / F1 / AP50 |
|---|---|---|---|---|
| power-distribution-lines | 253 | 466 | 0.017 / 0.032 / 0.022 / 0.002 | 0.101 / 0.186 / 0.131 / 0.067 |
| telecom-cable | 361 | 910 | 0.004 / 0.011 / 0.006 / 0.000 | 0.020 / 0.050 / 0.028 / 0.004 |
| Vegetation | 38 | 11 | 0.364 / 0.105 / 0.163 / 0.079 | same |
| abandoned-wire | 7 | 0 | – / 0 / – / 0 | – |

At the operating threshold, YOLO-1024 alone predicts **0 power spans and 4 telecom spans** against
253 and 361 labelled. Its wire confidences sit almost entirely below 0.20. On validation, taking
spans from WireNet raised tolerant power-line matches from 0 to 52, and telecom from 0 to 25
(`v27_val_span_modes.json`).

**Ultralytics validation (box and mask, 1/4-resolution grid), standard test:**

| Model | Box mAP50 | Mask mAP50 | Power box AP50 | Telecom box AP50 | Vegetation mask AP50 |
|---|---|---|---|---|---|
| v2.6 (640) | 0.102 | 0.003 | 0.202 | 0.177 | 0.014 |
| YOLO-1024 | 0.081 | 0.014 | 0.132 | 0.173 | 0.055 |

Wire mask AP50 is 0.000 for both models, as the §4.3.1 ceiling predicts.

### 4.3.5 Blurred and low-contrast photos

| Model | Standard test | Degraded test | Change |
|---|---|---|---|
| v2.6 (Ultralytics box mAP50) | 0.102 | 0.074 | **−28%** |
| YOLO-1024, trained with blur augmentations (box mAP50) | 0.081 | 0.090 | **+10% (no loss)** |
| v2.6 (Ultralytics mask mAP50) | 0.0034 | 0.0006 | −82% |
| YOLO-1024 (mask mAP50) | 0.0136 | 0.0132 | −3% |
| Hybrid, tolerant mAP50 | 0.0375 | 0.0124 | −67% |

**Effect of the inference-time enhancement on degraded photos** (hybrid; CLAHE on L* plus
blur-adaptive unsharp masking):

| Hybrid, degraded test | Strict mAP50 | Strict mAP50-95 | Tolerant mAP50 | Tolerant power F1 |
|---|---|---|---|---|
| Enhancement off | 0.0051 | 0.0008 | 0.0104 | 0.076 |
| Enhancement on | **0.0079** | **0.0025** | **0.0124** | 0.057 |

Enhancement raises strict mAP50 by 55% and tolerant mAP50 by 19% on degraded photos. Tolerant
power-line F1 at the fixed threshold falls, because enhancement produces more candidate spans
(523 against 460), which lowers precision.

The blur fallback (a second pass with stronger enhancement and a mirrored copy) fired on 20 of 21
degraded photos with the YOLO-only models, and its result was kept 8 times (v2.6) and 14 times
(YOLO-1024). It never fired with the hybrid: WireNet still returns enough scored spans on degraded
photos, so the trigger condition (no spans, or half or more unscored) is not met.

**A bug found by this work:** with a fixed 1 px radius and a detail threshold of 4 grey levels,
unsharp masking returned a strongly blurred photo unchanged (sharpness gain 1.0 at σ 5). Blur-adaptive
radius and threshold restored the gain to 1.7–2.1 (`tests/test_blur_resilience.py`).

### 4.3.6 Abandoned-wire

The model cannot learn abandoned-wire from the available labels.

**1. Too few examples, and some of them wrong.**
- **Counts:** 27 instances in all — 14 in training (7 photos), 6 in validation (3 photos) and 7 in
  test (2 photos). That is 0.033% of training pixels.
- **Label quality:** a visual audit of all 27 polygons found some that trace a door edge on a wall,
  a blob on a blurred pole, or a whole hanging bundle or the sky around it, rather than the wire. Only
  about a third are tight wire traces. Automatic tightening (below) set 9 of the 23 connected label
  components to *ignore*.

**2. What was tried.** Each attempt branched from the WireNet best checkpoint, with a guard keeping
power and telecom at ≥ 95% of their Stage 1 IoU.
- **Label tightening** to wire-like pixels, using the Stage 1 wire map plus a dark-ridge filter.
- **Oversampling:** 35% of tiles centred on abandoned-wire. The abandoned-wire pixel share rose from
  0.03% to 0.23%.
- **Copy-paste** of the tightened real instances, and **procedural hanging-end wires**.
- **Loss:** focal cross-entropy, Dice and clDice. One attempt also used logit adjustment (Menon et
  al., 2021).

**3. Outcome.** Abandoned-wire coverage on validation, where an instance counts as hit when
predictions run along ≥ 50% of its length, and precision is the share of predicted pixels within
6 px of a label:

| Model | Instances hit | Precision | Pixels predicted abandoned |
|---|---|---|---|
| WireNet Stage 1 | 1 / 6 | 0.25 | 3,360 (all 22 images) |
| Stage 2, weight 15 + logit adjustment, epoch 3 | 6 / 6 | 0.01 | 1,387,708 |
| Same, bias −4 at inference | 3 / 6 | 0.02 | 315,688 |
| Same, bias −5 | 0 / 6 | 0.01 | 49,191 |
| Stage 2, weight 6, epoch 1 | 4 / 6 | 0.04 | 100,215 |

Every rebalancing raised recall only by labelling ordinary wires as abandoned. At no inference bias
does precision exceed 0.02, so the model has learned no localised cue for abandoned-wire. That is
expected: an abandoned wire looks like any other wire. It differs by context — a free end, slack,
coiling, no termination — and 14 examples, about a third of them mislabelled, do not contain enough
of that context to learn. Oversampling, copy-paste and synthesis repeat the same 14 examples; they
cannot add the missing variety.

**Requirement:** several hundred tightly traced abandoned-wire instances, from a few hundred
different photos, before this class can be learned. Until then, the deployed model uses the Stage 1
weights, and any abandoned-wire output should be treated as unreliable.

### 4.3.7 Downstream: catenary risk scores

On the end-to-end server check (8 uploads: 4 test photos, standard and degraded; 1.4–3.9 s each; PDF
reports valid), the hybrid produced 411 spans, of which 306 were scored:

| Risk level | Spans |
|---|---|
| Low (CRS < 3) | 216 |
| Medium (3–6) | 40 |
| High (> 6) | 50 (19 saturated at 10) |

The mean CRS was 2.34. Every photo's worst span was high, driven by short span fragments with chords
of 22–48 px, where a pixel of noise is a large share of the sag. Every score carries
`development_limits`, because δ_lim,0 = 0.040 is a placeholder.

---

## 4.4 Limitations

1. **The dataset is small** (145 photos) and has a single source. Every figure has wide uncertainty:
   the test set is 21 images with 7 abandoned-wire instances.
2. **The strict instance metric is mismatched with the data.** Equation 5 at IoU 0.5, applied to
   2 px wires drawn by hand to ±1–2 px, rejects most correct spans. The tolerant metric is reported
   alongside it, and its definition is given in §4.2.3.
3. **Spans are fragmented.** WireNet spans break wherever the class mask has gaps: 466 predicted
   against 253 labelled for power, and 910 against 361 for telecom. This lowers precision and makes
   the per-photo maximum CRS noisy.
4. **The YOLO-1024 detector is under-trained.** Its best checkpoint, global epoch 22, came from a run
   cut short by early stopping on a fluke epoch-1 best.
5. **CRS, VNI and the engineering parameters are provisional** (Table 3.10 targets not evaluated: no
   engineer reference ratings yet).

## 4.5 Recommended next steps, in order of expected impact

1. **Labels:** tightly trace several hundred abandoned-wire instances, and re-trace a sample of
   power and telecom spans, to measure the annotator noise floor.
2. **Span assembly:** bridge gaps along the skeleton direction when joining pieces, and require a
   minimum chord length before a span is scored.
3. **Retrain** WireNet and the detector on the larger set, and report strict and tolerant metrics on a
   test split of at least 100 photos.
4. **Agree** a tolerance-aware instance metric for thin structures with the panel, alongside
   Equation 5.
