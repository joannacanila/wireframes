# WIREFRAME — Thesis Reference (single consolidated document)

**A Vision-Based Diagnostic Framework for Urban Overhead Wire Infrastructure Risk Assessment**

Authors: Canila, Tubianosa, Villamar. State described: v2.16 (the current code on `main`).

This one file replaces every earlier report: the per-version Chapter 4 reports (v2.7–v2.15), the master
manuscript, the synthesis, the v2.9 defence summary and the disclosure log. It is organised by topic rather than
by version, so each part maps onto a section of the thesis:

| Part | Use it for |
|---|---|
| 1. What WIREFRAME is | Chapter 1 scope, Chapter 3 system overview |
| 2. Data | Chapter 3 data and Chapter 4 data description |
| 3. Models | Chapter 3 model design, Chapter 4 training |
| 4. Method, stage by stage | Chapter 3 methodology (Algorithms 1–6, Eq. 1–18) |
| 5. Results | Chapter 4 results tables |
| 6. Version history | Chapter 4 development narrative |
| 7. Everything tried and rejected | Chapter 4 discussion, defence questions |
| 8. How the test split was used | Chapter 3 / 4 validity and integrity |
| 9. The web application | Chapter 3 system design, Chapter 4 software results |
| 10. Software verification | Chapter 4 testing |
| 11. Deviations from Chapter III | Chapter 3 corrections to make in the manuscript |
| 12. Limitations and open problems | Chapter 5 conclusions and recommendations |
| 13. Reproduction | Appendix |
| 14. Evidence files | Appendix (every number traces to a file) |

**What is claimed:** a working, explainable screening prototype, measured honestly on a 145-photo dataset.
**What is not claimed:** none of the Table 3.10 targets is met, and no result here validates the allowable sag
limit δ_lim, which is still a development placeholder (0.040) awaiting the consulting engineer's reference ratings
(hypothesis H3).

**Hardware.** Every training, evaluation and latency run was executed locally on one **Apple Silicon M1 laptop
with 8 GB of unified memory** (PyTorch MPS for the GPU; CPU and GPU share the 8 GB). No cloud or external GPU was
used. In v2.13 the unit tests, the browser check and the documentation build were also run on a Windows 11 PC
(CPU only); no Windows timing is reported as latency. The memory limit shaped the design: model size, batch size,
one heavy process at a time, and latency figures from a fanless laptop that throttles under sustained load.

---

## 1. What WIREFRAME is

A web application. A user uploads one ground-level photo of a utility pole. WIREFRAME finds every overhead wire,
measures how far each wire section sags, flags vegetation touching the wires, and returns a plain-language
screening result with a PDF report. It is a **screening** tool, not a safety inspection: every result must be
reviewed by a qualified professional before action.

**Website only.** It runs in laptop and desktop browsers (window ≥ 1024 px). There is no mobile app and no phone
layout; below 700 px the page asks for a computer.

### 1.1 The pipeline, end to end (current state)

1. **Ingestion.** Every readable image is accepted and normalised to 8-bit RGB (EXIF rotation applied; 16-bit,
   alpha, CMYK, palette and greyscale converted) and capped at 2048 px on the long side. GPS is removed at the JPEG
   segment level, so the analysed pixels are exactly the uploaded pixels. Upload limit 64 MB. Tiny (< 320 px) or
   blank photos are accepted but graded *Retake*.
2. **Pre-processing (Algorithm 1).** A 1024 px letterbox (grey padding 114) for segmentation, with measured,
   triggered enhancement: edge-preserving denoise (noisy or JPEG-blocked photos), CLAHE only for low contrast or
   glare (not for darkness), blur-adaptive unsharp masking. A separate 640 px grey path for the VNI, never
   enhanced.
3. **Segmentation** (three models; Part 3):
   - **WireNet-R34** labels every wire pixel (power, telecom/fiber, abandoned) at full resolution, averaging the
     photo and its mirror image (flip test-time augmentation). For dark photos it also reads the photo with
     lightness inverted and keeps whichever finds more wire (dual polarity).
   - **YOLO11s-seg** outlines vegetation.
   - **UperNet-ConvNeXt-Tiny** (pretrained scene parser) marks road and pavement; wire traces lying mostly on the
     ground are removed and listed as "ground structures ignored".
4. **Span assembly.** WireNet's class map is skeletonised, split at junctions, re-joined through crossings,
   joined across short collinear gaps, and turned into wire sections ("spans"). Gap bridges are recorded as
   *inferred* geometry, never presented as detections.
5. **Chapter III mathematics, unchanged:** Zhang–Suen centreline with spur pruning (Algorithm 4);
   Levenberg–Marquardt catenary fit y = a·cosh((x − b)/a) + c (Eq. 6); sag ratio δ/L (Eq. 8); allowable sag δ_lim
   (Eq. 9); CRS = min(10, 10·(δ/L)/δ_lim·w) (Eq. 10) with bands Low < 3 ≤ Medium ≤ 6 < High (Table 1.2); VNI from a
   Hann-windowed 2-D FFT (Eq. 1–4); MSPI ranking (Eq. 11). CRS is computed only for chords ≥ 100 px (precision
   gate).
6. **Interpretation layer** (reads CRS, changes none of it):
   - **Pole verdict:** AT RISK – HIGH / AT RISK – MODERATE / NOT AT RISK / INCONCLUSIVE – RETAKE PHOTO. A sag band
     counts only when more spans are in it than measurement noise explains (one-sided binomial test, p0 = 0.050 /
     0.090, α = 0.05). Vegetation contact rules are absolute.
   - **Reliability grade:** A / B / C / Retake, from R = 1 − (U + D)/2, with a plain-language cause and a
     photometric glare gate. A Retake photo never reads NOT AT RISK.
7. **Outputs:** the Field Screener results page (Part 9), a PDF report with a three-panel diagnostic figure, the
   admin command centre (rankings, map, reviews, exports), and a batch street-audit CLI.

### 1.2 What the user sees (v2.15)

Overall assessment in plain words (never a safety certification); High / Moderate / Low counts; how many sections
could be assessed; the photo with detected wires (estimated gaps dashed); a list of wire sections with plain
findings; *What to do next*; *Photo limitations*; technical diagnostics collapsed for administrators. Part 9 has
the full description.

---

## 2. Data

### 2.1 The dataset

| Item | Value |
|---|---|
| Dataset | Wireframes-Prelabelled-Datasets (team export, Roboflow Universe, workspace `ravens-workspace-g6fye`), export of 2026-11-14 12:02 GMT, "No pre-processing" |
| Licence | CC BY 4.0 |
| Photos | 145: train 102 / validation 22 / test 21, balanced by wire-density tertile (25.7 and 34 instances per photo) |
| Classes | `Vegetation`, `abandoned-wire`, `power-distribution-lines`, `telecom-cable` (fiber optic drops are labelled `telecom-cable`) |
| Why this source | The only one with span-level polygons: 11–12% of power and telecom polygons are fragments, against 72–86% in the 965-image v1/v2i exports |

**Curation audit** (`results/dataset_audit_v27.json`): 0 excluded for viewpoint, 0 duplicates, 0 corrupt label
lines, 0 unmapped labels. All photos letterboxed to 1024 × 1024 with grey (114) padding, as at inference. 145
distinct native sizes, aspect ratios 0.76–3.87.

| Class | Instances | Median extent | Fragment share | Median width at 1024 px |
|---|---|---|---|---|
| Vegetation | 220 | – | – | 7.1 px |
| abandoned-wire | 27 | 0.17 | 70% | 8.7 px |
| power-distribution-lines | 1,801 | 0.52 | 11.5% | 2.1 px |
| telecom-cable | 2,441 | 0.47 | 10.6% | 2.1 px |

Test split labelled instances: power 253, telecom 361, abandoned 7, vegetation 38.

### 2.2 Earlier exports (history)

- **v1 export (v2.4):** 965 photos resized to 432 × 432 "Stretch" (distorting catenaries); 69% / 84% of
  power / telecom polygons were fragments, 0 fiber-optic examples, 103 dead-wire instances. Unusable for sag
  measurement, which needs span-length masks.
- **2026-11-14 99-photo export (v2.5–v2.6):** span-level, split 69 / 15 / 15. Superseded by the 145-photo export.

### 2.3 External datasets screened and rejected

The search (Kaggle, Roboflow Universe, GitHub, Zenodo, Mendeley Data, IEEE DataPort; 2026-11-14, repeated
2026-11-15) required five gates: polygon or pixel masks; ground-level viewpoint; span-level labels; an allowed
licence; classes that map to the four WIREFRAME classes. **None passed** (register: `ml/sources.yaml`).

| Dataset | Reason rejected |
|---|---|
| Rural distribution power line vegetation (Zenodo 18392898) | Files restricted |
| SEwire "wire" (Roboflow) | Classes do not separate power from telecom; not inspectable without an API key |
| TTPLA | Aerial drone view of transmission towers |
| VEPL (Zenodo 7800234) | Top-down orthomosaic tiles |
| WireSegHR (CVPR 2023) | One generic wire class; no licence stated |
| Electric Wires Dataset (Kaggle) | Synthetic chroma-key images |
| OHL-UK (BMVC 2025) | Labels poles and signs only |
| lonlonago LabelMe sets | Commercial; no licence |
| Merged Public Power Cable Dataset (2025) | UAV / aerial |
| Railway OCS conductor segmentation (2025) | Railway catenary |
| Street-view power-line classification (arXiv 2105.03804) | Classification only; data not released |

### 2.4 v2.16 research inventory: poles and street-level infrastructure (searched 2026-11-16)

| Name | Source | Licence | Task / labels | Perspective | Decision |
|---|---|---|---|---|---|
| Annotated Google Street View dataset for pole and tree detection | Zenodo 20091961 | CC BY 4.0 as published; images are Google Street View captures | 3,103 images (640 × 640), YOLO boxes: `poste` 5,969 (utility, lamp and sign posts), `arvore` 3,047 | Street level, Curitiba (Brazil), concrete distribution poles similar to Philippine ones | **Used** (poles only), research use; neither images nor trained weights are redistributed by this repository |
| Utility Poles (Zac) | Roboflow Universe `zac-ogogt/utility-poles-kcumt` | CC BY 4.0 | ~380 images, boxes | Street level | Evaluate further: needs `ROBOFLOW_API_KEY` (not set on the development machine) |
| Utility Pole v7 (Yue-Hung) | Roboflow Universe `yue-hung/utility-pole-5j6dp` | CC0 (page metadata) | 511 images, boxes | Street level | Evaluate further: API key / possible subscription |
| OHL-UK wooden pole and sign corpus | arXiv 2608.04061 | CC BY 4.0 (stated) | 4,570 images, boxes, masks, lean angle | Street View (UK wooden poles) | Not yet released ("upon acceptance") |
| Mapillary Vistas | Mapillary research | Research licence agreement | 25,000 images, 66+ classes incl. `object--support--utility-pole` | Street level, worldwide | Evaluate further: licence terms and size (≈ 25 GB) |
| TTPLA | github.com/r3ab/ttpla_dataset | Unconfirmed (Apache 2.0 per a third-party catalogue) | 1,100 aerial images, towers + lines | UAV / aerial | Rejected: transmission towers seen from the air |
| ADE20K scene parser (UperNet-ConvNeXt-T) | Hugging Face `openmmlab/upernet-convnext-tiny` | MIT | 150 classes incl. pole, streetlight, bridge, tree | Street level | **Used** (already loaded for the ground gate): structure-edge and vegetation gates, pole regions |

The 145-photo team set has no pole labels, so pole detection is evaluated on the held-out street sequences of the
Zenodo set, not on WIREFRAME photos.

### 2.5 Privacy

GPS is stripped from every stored photo; coordinates live only in a separate table and only when the user adds
them. Pole coordinates, rankings and the multi-pole map are admin-only (§3.14.4). The curation privacy screen
fails closed (every image listed for manual review when no face detector is available).

---

## 3. Models

### 3.1 Models in the running system

| # | Model | File | Role |
|---|---|---|---|
| 1 | **WireNet-R34**: U-Net, ResNet-34 encoder (ImageNet-pretrained), 5 output classes, full-resolution output, 24.4 M parameters | `models/wirenet-r34.pt` (SHA-256 prefix `ab2353c7`, frozen since v2.7) | Wire pixels (power, telecom, abandoned). Everything measured comes from it |
| 2 | **YOLO11s-seg** fine-tuned at 1024 px (10.1 M parameters; run `yolo11s-seg-1024-mps-b1-cont`). The file keeps the name `wireframe-yolo11m-seg.pt` because that is the path the server loads; YOLO11m-seg ran out of memory on the 8 GB M1 | `models/wireframe-yolo11m-seg.pt` | Vegetation regions only (its wire masks are capped by a 1/4-resolution grid, §5.2) |
| 3 | **UperNet + ConvNeXt-Tiny**, ADE20K-pretrained, 60 M parameters, MIT licence; not trained by the team | Downloaded: `openmmlab/upernet-convnext-tiny` | Ground gate: road / sidewalk / floor / earth / path / grass / field / sand / dirt track / runway / rug |

### 3.2 Trained but not shipped

| Model | File | Status |
|---|---|---|
| U-Net, MiT-B1 encoder (16.4 M) | `models/v212_mitb1.pt` (`f595ef68`) | Part of the three-model fusion, not shipped (latency) |
| U-Net, ConvNeXt-Tiny encoder | `models/v212_convnext_t.pt` (`9633cee8`) | Same |
| Fusion recipe 0.25 R34 + 0.5 MiT-B1 + 0.25 ConvNeXt | `models/v212_ensemble.json` | Opt-in: set `segmentation.wirenet_weights` to this file |

Each v2.12 checkpoint has `-best-tolerant` and `-last` variants and an `.arch.json` sidecar so the app can load it.

**Why the fusion is off:** 4.71 s median per photo (5.47 s on the dense pole) against R34's 1.75 s and a 3.0–3.5 s
limit on the M1; its validation gain (0.0397 → 0.0537) is probable but not established (bootstrap interval
includes zero); the faster single-pass variant would require re-deriving p0 and the reliability grade. The team
chose "keep R34, document the fusion".

### 3.3 Training

**WireNet-R34 (v2.7).** 102 training photos, 1024 letterbox, 512² random crops (80% centred on a wire). Loss:
class-weighted cross-entropy + soft Dice + clDice. AdamW, one-cycle LR. 40 epochs, 2.63 h on MPS, best epoch 29.
Blur and contrast augmentations (Albumentations). Validation tolerant pixel IoU: power 0.525, telecom 0.418.

**YOLO detectors.**
- v2.6: YOLO11s-seg at 640, 30 epochs, 1.2 h (best epoch 12). Validation mask mAP50 0.086, all from Vegetation;
  every wire class AP 0.
- v2.7: YOLO11s-seg at 1024, batch 1 with gradient accumulation to 64; 16 epochs (early stop on a fluke epoch-1
  best), then 11 more; best at global epoch 22. This is the shipped vegetation model; it is under-trained.
- YOLO11m-seg: out of memory at batch 4 even at 640 px on the 8 GB M1.

**v2.12 backbones** (same recipe as R34, batch 2 because of memory; full validation after every epoch):

| Model | Epochs | Wall time | Best epoch | Validation tolerant IoU power / telecom |
|---|---|---|---|---|
| WireNet-R34 (reference) | 40 (batch 4) | 2.63 h | 29 | 0.525 / 0.418 |
| MiT-B1 U-Net | 40 | 4.66 h | 32 | 0.543 / 0.404 |
| ConvNeXt-Tiny U-Net | 20 (40 would take ~7 h) | 3.39 h | 16 | 0.540 / 0.415 |

Backbone probe on the M1 (one training step, batch 4 × 512²; one 1024² inference): R34 0.92 s / 0.27 s; MiT-B1
1.15 s / 0.46 s (2.4 GB); ConvNeXt-T 2.11 s / 0.60 s (3.6 GB); HRNet-W18 2.01 s / 0.55 s (not trained); MiT-B2 out of
memory; SegFormer-B2 2.31 s inference alone (rejected).

**Abandoned wire cannot be learned.** 27 instances in all (14 train in 7 photos, 6 validation, 7 test), 0.033% of
training pixels; a visual audit found only about a third are tight wire traces. Label tightening, 35% oversampling,
copy-paste, procedural hanging ends, focal loss and logit adjustment all raised recall only by labelling ordinary
wires as abandoned (precision ≤ 0.04 at every setting). Requirement: several hundred tightly traced instances from
a few hundred photos.

**Pretrained components:** YOLO11 (Ultralytics 8.4, AGPL-3.0), ResNet-34 (torchvision / smp, BSD-3 / MIT),
Albumentations 2.0.8 (MIT), UperNet-ConvNeXt-T (MIT). SAM 2.1 (Apache-2.0) was benchmarked only.

---

## 4. Method, stage by stage

### 4.1 Pre-processing and enhancement

| Step | Trigger | Notes |
|---|---|---|
| Edge-preserving denoise (bilateral, d = 5, σ_space = 3) | Immerkær noise σ ≥ 3, or JPEG blockiness B ≥ 1.3 | B = mean step across 8-px block boundaries ÷ mean step inside blocks |
| CLAHE on L* (clip 2.0, 8×8) | Low contrast or glare; **not** darkness | The v2.7 darkness trigger hurt low light (0.464 vs 0.504) and was removed in v2.8 |
| Blur-adaptive unsharp mask | Laplacian variance ≤ 60 | A fixed 1 px radius returned blurred photos unchanged (gain 1.0); adaptive radius gives 1.7–2.1 |
| VNI grey path | always 640 px, 5×5 Gaussian, never enhanced | Keeps VNI comparable across photos |

Motion coherence (v2.9) is measured on soft photos to name "camera shake" in the reliability reason: per 128 px tile
the whitened tensor M = J_c^{−1/2} J_f J_c^{−1/2} cancels content orientation; coherence = |mean doubled-angle
anisotropy vector|; motion when soft, coherence ≥ 0.16 and clipped share < 0.25. It separates 22/22 synthetic motion
photos from 22/22 defocus and 3/3 soft clean photos. It does not change processing (routing was rejected, Part 7).

### 4.2 Segmentation

- **WireNet + flip TTA:** plain and mirrored probability maps averaged (v2.8).
- **Dual polarity (v2.11):** for a photo with mean L* ≤ 40, WireNet also runs on the photo with L* inverted (chroma
  kept, padding at its training value); the inverted pass is used when it finds ≥ 1.2× the wire pixels. On
  validation the populations separate cleanly: daytime ratio ≤ 0.92, synthetic night ≥ 1.53; the rule switches
  22/22 night and 0/176 daytime cases. Flag `polarity_inverted`.
- **Vegetation:** YOLO11s-seg at 1024, `conf_min` 0.20 (chosen on validation for best strict mean F1).
- **Ground gate (v2.13):** run only when some span's bounding box reaches the lower half of the photo. The scene
  parser runs at 384 px (UperNet's pyramid pooling needs a feature map divisible by 6 on MPS: 384/32 = 12), 0.26 s,
  agreeing with 576 px on 98.8% of pixels. A span with ≥ 50% of its pixels on ground classes is not scored; it is
  returned in `suppressed_spans` with the reason "Lies on the road or pavement: a kerb, manhole or pavement edge, not
  an overhead wire". It fails open (`ground_gate_unavailable`) if the parser cannot load.

### 4.3 Span assembly (`app/pipeline/wire_instances.py`)

Per wire class:
1. Class mask, components < 30 px removed, Zhang–Suen skeleton.
2. Junction pixels (≥ 3 skeleton neighbours) removed; junction points within **5 px** count as one crossing.
3. Pieces meeting at a crossing re-joined when their end directions are collinear within **35°**, straightest pair
   first.
4. Every mask pixel assigned to its nearest skeleton piece **within its own connected mask blob** (v2.14; before,
   a detached blob of a neighbouring cable could be painted into the nearest span).
5. **Collinear gap bridging (v2.8):** spans of one class joined through endpoints a, b when ‖b − a‖ ≤ **70 px**,
   −t_a·t_b ≥ cos 25°, and the gap continues both tangents (t_a·û, t_b·(−û) ≥ cos 25°). Tangents from the mean of
   the 12 skeleton points nearest each end; nearest-first, union-find, each end used once. The bridge is drawn 2 px
   wide so the fit runs through it.
6. Spans whose stitched skeleton is shorter than **90 px** are dropped; minimum piece length 15 px. Confidence =
   area-weighted mean class probability.

**Provenance (v2.14).** Bridge pixels were never labelled wire by the model. For every span the API reports
`observed_px`, `inferred_px`, `inferred_segments`, `inferred_polygons` and `centerline_inferred_share`, computed from
the stored class mask (so older analyses get it too). The viewer cuts inferred pixels out of the span mask and draws
the gaps dashed ("Estimated, not detected"). CRS is unchanged: the bridge stays in the measurement.

### 4.4 Geometry and risk (Chapter III, unchanged)

- **Centreline (Algorithm 4):** Zhang–Suen thinning, 8-connected graph, iterative spur pruning < 30 px, double-BFS
  longest path, n_min check.
- **Catenary fit (Eq. 6–7):** parabola starting values; `curve_fit(method="lm")` with an analytic Jacobian in
  normalised coordinates. Fails on non-convergence, a ≤ 0, or RMS residual / L > 0.02. Truncation flag at the border.
- **Sag (Eq. 8):** δ = largest perpendicular distance of the fitted curve from its chord, closed form
  (x* = b + a·asinh(m)); δ_rel = δ/L.
- **Allowable sag (Eq. 9):** change-of-state cubic (elasticity, thermal expansion) with wind load ½ρV²C_d·d, solved
  with Brent's method; falls back to δ_lim,0 = 0.040 with flag `default_parameters`. All engineering values are
  provisional (`development_limits`).
- **CRS (Eq. 10):** min{10, 10·(δ_rel/δ_lim)·w_class}, class weights 1. Levels: Low < 3.0 ≤ Medium ≤ 6.0 < High.
- **Precision gate (v2.8):** σ/L ≤ f·δ_lim with σ = 1 px, f = 0.25 → L_min = 1/(0.25 × 0.040) = **100 px**. Shorter
  spans: status `too_short`, reason `chord_below_precision_gate`, no CRS. Derived from precision, not tuned.
- **VNI (Eq. 1–4):** periodic Hann window, power spectrum, share beyond r_L = 0.15·min(M,N)/2, clipped and scaled by
  q01 / q99 (calibrated on training photos; provisional), Table 3.7 bands. The image mean is removed before
  windowing (see Part 11).
- **MSPI (Eq. 11, Algorithm 6):** α·VNI + β·mean(CRS)/10 + γ·max(CRS)/10, α + β + γ = 1; 66-point sensitivity grid
  (Table 3.11) in the admin portal.
- **Vegetation:** never skeletonised or scored. Clearance by Euclidean distance transform of the vegetation union.
  Approximate metres use s = D / w̃ (conductor diameter 10.1 mm ÷ median power-wire width in px) with a ±1 px range,
  so metre values are always labelled approximate.

### 4.5 The pole verdict (`app/pipeline/hazard.py`)

| Factor | Rule | Verdict |
|---|---|---|
| Sag | ≥ k*(n) spans in the High band (p0 = 0.050) | AT RISK – HIGH |
| | ≥ k*(n) spans at Medium or above (p0 = 0.090) | AT RISK – MODERATE |
| Vegetation | Power span in contact | AT RISK – HIGH |
| | Power span within 15 px | AT RISK – MODERATE |
| | Telecom span in contact | AT RISK – MODERATE |
| Reliability | Grade Retake and no hazard found | INCONCLUSIVE – RETAKE PHOTO (a found hazard is kept, with a note) |

k*(n) = min{k : P(Bin(n, p0) ≥ k) ≤ α}, α = 0.05.

| Measurable spans n | 1 | 2 | 5 | 10 | 22 | 30 | 49 |
|---|---|---|---|---|---|---|---|
| k* HIGH (p0 0.050) | 1 | 2 | 2 | 3 | 4 | 5 | 6 |
| k* MODERATE (p0 0.090) | 2 | 2 | 3 | 4 | 5 | 6 | 9 |

**Why (v2.9).** The v2.8 rule (worst single span) saturates: with ~30 spans per pole and a per-span false-alarm rate
p, P(≥ 1 high) = 1 − (1 − p)^30 ≈ 0.78 at p = 0.05. p0 was **measured**, not assumed: each validation photo
measured twice (as is and mirrored, TTA off), 492 matched scored spans; discordance high 0.050, medium-or-above
0.090. Only 13 of 35 saturated CRS = 10 spans reproduced (37%), which rules out a "single extreme span" override.
Spans in a band that do not reach k* are listed as a NOTE ("verify visually"), never dropped. The rule is a
significance test against measurement noise, not a claim about the true hazard rate.

### 4.6 The reliability grade (`app/pipeline/quality.py`)

Over predicted wire pixels: U = mean normalised entropy; D = share whose class flips between the plain and mirrored
passes; **R = 1 − (U + D)/2**. Grades: **A ≥ 0.78, B ≥ 0.66, C ≥ 0.64**, else Retake. Also Retake when < 500 wire
pixels, mean L* < 8, > 85% clipped, too small or blank.

**Glare gate (v2.10):** near-white share (L* > 90) ≥ 0.6 **and** thin-line contrast (mean of the strongest 1% of
black-top-hat responses, 5 px ellipse, on L*) < 15 → Retake. Model-independent, so it catches "confident absence".

Reason shown for every grade below A, in the order a field worker can act on: blank; resolution too low; severe glare
washed out conductors; too dark; almost no wire pixels; camera shake; out of focus; bright-sky glare lowered contrast;
low light; model uncertainty (clutter or low contrast).

### 4.7 Metrics (Eq. 5, 12–18)

- **Strict (official, Eq. 5):** an instance is a true positive at mask IoU ≥ 0.5, matched per class; AP is COCO
  101-point; P / R / F1 at `conf_min` 0.20. Predictions under confidence 0.01 are excluded from AP.
- **Tolerant (supplementary):** both masks widened by 2 px (at 1024) before IoU. On a 2 px wire a 1 px offset alone
  caps strict IoU at about 0.33; hand labels are ±1–2 px.
- **Pixel level:** per-class IoU, precision, recall, F1 with the same 2 px tolerance.
- **Decision rule:** on 22 validation photos, differences below ~0.002 tolerant mAP50 are ties; the more conservative
  value is kept.
- Agreement statistics for H1–H3 (RMSE, MAE, Pearson, Spearman, weighted κ, bootstrap CIs) are implemented
  (`app/pipeline/evaluation.py`) but cannot run without engineer reference ratings.

---

## 5. Results

### 5.1 Headline numbers (test split, 21 photos, shipped configuration)

Strict / tolerant (2 px). Source: `results/v212_spans_test.jsonl` (the R34 configuration that shipped).

| Category | Metric | Class | Clean | Low light | Night (synthetic) |
|---|---|---|---|---|---|
| Overall | mAP@50 | all | 0.020 / 0.047 | 0.005 / 0.022 | 0.009 / 0.031 |
| | mAP@50–95 | all | 0.004 / 0.009 | 0.001 / 0.004 | 0.002 / 0.006 |
| Power (253) | Precision | | 0.022 / 0.175 | 0.016 / 0.167 | 0.011 / 0.194 |
| | Recall | | 0.028 / 0.225 | 0.016 / 0.170 | 0.012 / 0.217 |
| | **F1** | | **0.024 / 0.197** | 0.016 / 0.168 | 0.011 / 0.205 |
| | AP@50 | | 0.002 / 0.105 | 0.000 / 0.065 | 0.001 / 0.088 |
| Telecom (361) | Precision | | 0.004 / 0.032 | 0.002 / 0.022 | 0.000 / 0.021 |
| | Recall | | 0.006 / 0.050 | 0.003 / 0.033 | 0.000 / 0.025 |
| | **F1** | | **0.004 / 0.039** | 0.002 / 0.026 | 0.000 / 0.023 |
| | AP@50 | | 0.000 / 0.004 | 0.000 / 0.003 | 0.000 / 0.001 |
| Abandoned (7) | Recall / F1 / AP@50 | | 0 / – / 0 | 0 / – / 0 | 0 / 0 / 0 |
| Vegetation (38) | P / R / **F1** / AP@50 | | 0.364 / 0.105 / **0.163** / 0.079 | 0.080 / 0.053 / 0.063 / 0.021 | 0.036 / 0.053 / 0.043 / 0.036 |
| Pixel level (validation, 22) | **F1** power / telecom | | **0.792 / 0.677** (IoU 0.528 / 0.422) | – | – |
| Sag score and verdicts | accuracy | | not measurable: no engineer reference ratings; δ_lim placeholder | | |
| Table 3.10 targets | | | none met or validated | | |

**How to read it.** WireNet finds wire *pixels* reasonably well (F1 0.79 / 0.68); cutting them into the same
sections a person drew is hard, so section-level scores are low. Any split, merge or trim counts as both a miss and a
false alarm. Telecom in dense bundles is the weakest case. The v2.14 span-ownership change has not been scored on test
(on validation it is identical to four decimals: 0.035557 → 0.035554).

### 5.2 Why YOLO masks cannot represent wires (v2.7)

Ceiling IoU of a *perfect* mask on YOLO's 1/4-resolution grid, upsampled, against the full-resolution label (2,129
instances):

| Class | Median width | Ceiling IoU (median) | Can reach IoU ≥ 0.5 |
|---|---|---|---|
| power | 2.1 px | 0.40 | 17% |
| telecom | 2.1 px | 0.41 | 21% |
| Vegetation | 7.1 px | 0.72 | 76% |
| abandoned | 8.7 px | 0.78 | 67% |

YOLO's wire mask AP50 is therefore capped at ~0.2 however well it is trained; observed: 0.000. At the operating
threshold YOLO-1024 alone predicts 0 power and 4 telecom spans against 253 and 361. **SAM 2.1** prompted with the true
boxes (and skeleton points) reaches median wire IoU 0.04–0.08 and ~40 s per image on CPU.

| Test, overall | Strict mAP50 | Strict mAP50–95 | Tolerant mAP50 |
|---|---|---|---|
| v2.6 baseline (YOLO 640) | 0.0005 | 0.0001 | 0.0005 |
| YOLO-1024 | 0.0198 | 0.0042 | 0.0199 |
| Hybrid v2.7 (WireNet spans) | 0.0204 | 0.0043 | 0.0375 |
| Hybrid v2.8+ (shipped) | 0.0204 | 0.0043 | **0.0469** |

### 5.3 Eight-condition stress benchmark (test, span level)

Conditions (`ml/stress.py`): low light (γ 2.4 + noise σ 6), glare (×1.6 + 60, local contrast halved), motion (linear
kernel 1.5% of the long side), defocus (disk radius 0.5%), JPEG q20, low-res (480 px then upscaled), rain-haze;
synthetic night (v2.11: L* inverted, ×0.55, noise σ 4).

| Condition | v2.7 strict / tol. | **Shipped (v2.8 → v2.15) strict / tol.** | Power tol. AP50 / R | Telecom tol. AP50 / R |
|---|---|---|---|---|
| clean | 0.0204 / 0.0372 | 0.0204 / 0.0469 | 0.105 / 0.225 | 0.004 / 0.050 |
| lowlight | 0.0018 / 0.0123 | 0.0053 / 0.0222 | 0.065 / 0.170 | 0.003 / 0.033 |
| glare | 0.0225 / 0.0281 | 0.0206 / 0.0257 | 0.019 / 0.043 | 0.002 / 0.014 |
| motion | 0.0158 / 0.0164 | 0.0166 / 0.0227 | 0.022 / 0.091 | 0.004 / 0.022 |
| defocus | 0.0257 / 0.0264 | 0.0255 / 0.0271 | 0.005 / 0.032 | 0.001 / 0.014 |
| jpeg | 0.0250 / 0.0401 | 0.0210 / 0.0410 | 0.078 / 0.186 | 0.004 / 0.050 |
| lowres | 0.0236 / 0.0377 | 0.0225 / 0.0503 | 0.105 / 0.209 | 0.007 / 0.053 |
| rainhaze | 0.0220 / 0.0399 | 0.0220 / 0.0520 | 0.117 / 0.237 | 0.002 / 0.030 |
| **mean of 8** | 0.0196 / 0.0298 | **0.0192 / 0.0360** | | |
| night (synthetic) | v2.10: 0.0089 / 0.0090 | **0.0091 / 0.0313** | 0.088 / 0.217 | 0.001 / 0.025 |

Tolerant mAP50 +21% over v2.7 on average (clean +26%, low light +80%, low-res +33%, rain-haze +30%); strict ties.
Motion blur is the open condition: enhancement *off* beats every version there (tolerant 0.0272).

### 5.4 Validation results

**Span extraction ablation (v2.8; one constant varied, others at final; tolerant mAP50, final in bold):**

| Constant | Values |
|---|---|
| `max_gap` | 0: 0.0307, 35: 0.0339, 50: 0.0346, **70: 0.0356**, 100: 0.0330, 150: 0.0321 |
| `gap_angle_deg` | 10: 0.0336, 15: 0.0332, 20: 0.0328, **25: 0.0356**, 30: 0.0339, 35: 0.0366 |
| `draw_bridge` | False: 0.0350, **True: 0.0356** |
| `min_span_len` | 0: 0.0343, 45: 0.0345, 60: 0.0346, **90: 0.0356**, 120: 0.0354, 200: 0.0366 |
| `max_turn_deg` | 15: 0.0328, 25: 0.0350, **35: 0.0356**, 45: 0.0357 |
| `cluster_radius` | 2: 0.0262, **5: 0.0356**, 10: 0.0341, 15: 0.0289 |
| `min_len` | **15: 0.0356**, 30: 0.0340, 45: 0.0339 |

Confirmed on the v2.8 probability maps: v2.7 constants 0.0064 / 0.0296 → v2.8 **0.0068 / 0.0397**; power tolerant
AP50 / F1 / R 0.108 / 0.190 / 0.213; telecom F1 / R 0.075 / 0.088; power spans 564 → 390 for 314 labelled
(fragmentation 1.84 → 1.24 per labelled span). **0.0397 is the validation reference every later version is judged
against.**

**Pre-processing and TTA under eight conditions (validation, tolerant pixel IoU power / telecom):**

| Condition | Enhancement off | v2.7 | **v2.8 (shipped)** |
|---|---|---|---|
| clean | 0.525 / 0.418 | 0.525 / 0.417 | 0.528 / 0.422 |
| lowlight | 0.504 / 0.360 | 0.464 / 0.334 | 0.512 / 0.368 |
| glare | 0.069 / 0.161 | 0.080 / 0.191 | 0.079 / 0.185 |
| motion | 0.375 / 0.271 | 0.360 / 0.270 | 0.357 / 0.277 |
| defocus | 0.387 / 0.290 | 0.395 / 0.310 | 0.403 / 0.314 |
| jpeg | 0.479 / 0.392 | 0.477 / 0.388 | 0.490 / 0.400 |
| lowres | 0.515 / 0.407 | 0.515 / 0.404 | 0.517 / 0.408 |
| rainhaze | 0.517 / 0.415 | 0.520 / 0.410 | 0.523 / 0.412 |
| mean | 0.3802 | 0.3788 | **0.3872** |

Glare is catastrophic for every variant (power ≈ 0.08): with the sky clipped white, wire contrast is gone.

**Night (validation, v2.11):** synthetic night tolerant mAP50 0.0051 → **0.0312**; power AP50 / R 0 → 0.085 / 0.204;
telecom 0 → 0.020 / 0.106; clean and low light byte-identical. Real night pole P-D67F60 (unlabelled): wire pixels
9,694 → 23,464, spans 12 → 27 (measured 8 → 18).

**Reliability grade calibration (176 image × condition cases):** Spearman ρ with per-image tolerant IoU: R 0.640
(p = 1.1 × 10⁻²¹), −U 0.551, −D 0.594. Grade matches the measured band exactly in 63.1% and within one band in
93.8%. Medians: A 0.460 (n 41), B 0.391 (112), C 0.283 (7), Retake 0.093 (16). Retake fires on 9/22 glare and 5/22
motion photos, 0 clean.

**Failure audit (validation, 22):** no image property predicts accuracy significantly (wire density ρ = 0.04).
False positives outweigh misses (32–62% of predicted wire pixels unlabelled, 10–34% of labelled pixels missed); on the
hardest photo most "false positives" are real wires the labels omit in dense bundles. Accuracy is limited about as
much by label completeness as by the model.

### 5.5 Verdicts

| | Validation (22) high / moderate / not | Test (21) high / moderate / not |
|---|---|---|
| v2.7 behaviour | 19 / 1 / 2 (worst level) | 21 / 0 / 0 |
| v2.8 (worst single span) | 17 / 3 / 2 | 18 / 3 / 0 |
| **v2.9 onward (corroborated)** | **2 / 10 / 10** | **7 / 7 / 7** |

Test drivers: corroborated high sag 7; corroborated medium 3; telecom–vegetation contact only 4; not at risk 7 (each
with a NOTE). No validation photo is INCONCLUSIVE.

**Sensitivity to the placeholder δ_lim (validation spans; evidence for calibration, not a choice):**

| δ_lim | Spans high / medium / low | P(≥ 1 high among 30 spans) |
|---|---|---|
| 0.040 (placeholder) | 0.069 / 0.093 / 0.838 | 0.88 |
| 0.060 | 0.035 / 0.075 / 0.890 | 0.65 |
| 0.080 | 0.023 / 0.047 / 0.931 | 0.50 |

Image-space sag ratios: median 0.0017, p90 0.020, p95 0.028; uncorrelated with chord length (ρ = −0.03).

**Steep wires are not the problem (v2.9):** by chord inclination, high-band share is 12.8% (0–10°), 14.3% (10–20°),
6.9% (20–35°), 2.2% (35–50°), 1.3% (50–90°). Geometry: δ/L ≈ (D/S)·cos²θ shrinks with inclination.

**Perspective (v2.12):** high-band share rises with depth recession between a span's ends (Depth Anything V2):
3.2% → 7.2% → 10.4% by tercile (Fisher p = 0.0039). Receding wires read high ~3.3× more often. No correction adopted
(relative depth only; no ground truth).

**Verdict instability (v2.10):** among 111 mildly degraded validation cases that kept ≥ 80% of their spans, the pole
verdict changed in 46 (41%), 26 of them AT RISK → NOT. Per-span levels flip between measurements and the count sits
near k*. Quantified, not solved.

### 5.6 Glare (v2.10)

Glare (22 clean / glare pairs): near-white share 0.07 → 0.90; thin-line contrast 25.9 → 8.5 L*; wire pixels
7.6 → 1.1 ×10⁴; spans retained 0.32; reliability R barely moves (0.78 → 0.68): the model is confidently blind.

| Gate | Recall | Specificity | Leave-one-out acc. | Silent NOT AT RISK left (of 5) | Bright-sky photos made INCONCLUSIVE |
|---|---|---|---|---|---|
| Exposure only | 1.00 | 0.00 | 0.39 | 0 | 14 / 22 |
| **Thin-line contrast < 15 (adopted)** | **1.00** | **0.78** | **0.86** | **0** | **3 / 22** |
| Wire density < 0.016 | 0.76 | 1.00 | 0.80 | 2 | 0 / 22 |

Validation silent NOT AT RISK under glare 5 → **0**; test 7 → 3 (every span-dropout case caught, 2 → 0; the 3 left
are the instability mode). Clean verdicts unchanged by construction.

### 5.7 Fusion (v2.12, validation)

| R34 / MiT-B1 / ConvNeXt | Strict / tolerant span mAP50 | Power tol. F1 | Telecom tol. F1 | Gate |
|---|---|---|---|---|
| 1 / 0 / 0 (shipped) | 0.0068 / 0.0397 | 0.792 | 0.677 | baseline |
| 0 / 1 / 0 | 0.0065 / 0.0429 | 0.808 | 0.664 | fails (telecom F1) |
| 0 / 0 / 1 | 0.0064 / 0.0458 | 0.797 | 0.669 | fails (telecom F1) |
| 0.25 / 0.25 / 0.5 | 0.0065 / 0.0516 | 0.814 | 0.685 | passes |
| **0.25 / 0.5 / 0.25** | 0.0067 / **0.0537** | **0.819** | 0.684 | passes, best |
| best single pass (0 / 0.5 / 0.5, no flip) | – / 0.0503 | 0.814 | 0.677 | needs recalibration |

Paired bootstrap (2,000 resamples): the best fusion beats R34 in 94.4% of resamples; 95% interval of the gain
−0.0039 to +0.0315 (includes zero). Latency: R34 1.75 s median (dense 1.82 s); fusion 4.71 s (dense 5.47 s); one 1024²
pass 0.20 / 0.58 / 0.69 s, fused 1.49 s. Never run on test.

### 5.8 Latency (M1)

- v2.10 rewrite of span assembly (same outputs, proven by a golden check written by v2.9 itself, 22/22): dense poles
  35–46% faster (e.g. 5,715 → 3,078 ms on the 78-span pole); 81-span pole 4.58 s → 2.3 s end to end on a quiet
  machine.
- Server check: 1.33–3.46 s per upload across 14 cases on a quiet machine; 1.25–6.21 s under OS load (fanless
  throttling: CPU stages slow as a run continues; GPU memory flat at 1.19 GB).
- Dark photos pay two extra WireNet passes; the ground gate adds 0.26 s when it runs. A noisy v2.13 profile (load
  average 5.8) gave validation median 1.73 → 2.29 s and night pole 2.29 → 4.23 s; the quiet re-measurement against
  the 3.0 s target has not been run.

### 5.9 Results of v2.13–v2.14 (validation)

| Change | Tolerant mAP50 | Decision |
|---|---|---|
| Ground gate (share ≥ 0.5) | 0.0397 (0 spans removed; validation photos contain no ground) | adopted; on the live problem photo it removes the road/manhole traces incl. a false CRS 8.6, verdict AT RISK – HIGH → NOT AT RISK |
| CLAHE always on | 0.0356 (strict 0.0068 → 0.0117) | rejected |
| Black-hat tubeness β 0.25 / 0.5 / 1 / 2 | 0.0387 / 0.0367 / 0.0280 / 0.0336 | rejected |
| Bridging 60 px / 25° | 0.0407 (within noise) | 70 / 25 kept |
| Bridging at 12° (60–150 px) | 0.0357–0.0381 | rejected |
| Span ownership within blob (v2.14) | 0.035557 → 0.035554; 0 of 23 verdicts changed | adopted |
| Bridge needs model evidence τ 0.02–0.30 (v2.14) | 0.0356 → 0.0322–0.0356 | rejected |
| Lateral-offset gate 3–12 px (v2.14) | 0.0325–0.0351 | rejected |

(v2.14 runs used a cache whose shipped baseline is 0.0356, not v2.8's 0.0397; every row in a comparison uses the
same cache.)

On P-B1D137 (dense night, 27 spans), 1,299 span pixels were bridge pixels; 4 of 5 high-risk spans contain them
(span 14: 20% of its path, span 26: 17%).

### 5.10 Results of v2.16: one class per physical wire, scene checks, measurement validity

v2.16 started from three field photos of one Manila flyover junction (2048 × 1536, iPhone 14; `street1` taken with
the 0.5× ultra-wide lens at 14 mm equivalent, `street2` and `street3` at 26 mm). v2.15 reported 136–154 "wire
sections" per photo, sags of 9.6–12.6%, CRS 10 on 17–19 spans, AT RISK – HIGH on all three, a vegetation contact on
shadowed concrete, and single cables drawn in two colours. Each defect was traced to its stage before anything was
changed (error inventory below); every adopted change was decided on the 22 validation photos.

**Root causes found**

| Visible defect | Stage | Cause | Kind |
|---|---|---|---|
| One cable drawn half power, half telecom | span assembly | spans were built separately per class from WireNet's per-pixel argmax, so a cable whose pixels flip class became two spans, each with a hole where the other class won | post-processing |
| Spans along the flyover deck, canopy and sign edges (incl. three CRS 10) | segmentation + no context check | WireNet traces any long straight contrast edge; nothing checked what the trace lies on | model + post-processing |
| "Vegetation" on the shadowed flyover underside, raising a vegetation contact | YOLO vegetation | no check that the region is vegetation (scene parser: 0% vegetation, 70% bridge) | model |
| CRS 10 on short traces (chord 122–136 px) | chord precision gate | the gate L ≥ σ / (¼·δ_lim) assumes σ = 1 px, but masks are predicted at 1024 px and resampled to the photo, so on a 2048 px photo σ ≈ 2 px and the gate must be 200 px | measurement validity |
| HIGH verdict on the ultra-wide photo | verdict | the capture envelope (24–50 mm) was checked and recorded as *fail*, but never reached the sag verdict; barrel distortion bends straight wires | measurement validity |
| A strand zig-zagging onto a neighbouring cable | gap bridging | bridges were allowed up to 70 px × tan 25° ≈ 33 px sideways | post-processing |
| Thick ribbons, merged neighbouring wires | inference scale | the photo is shrunk to 1024 px; adjacent 1–2 px wires merge and the mask is scaled back up 2× | inference |
| Many wires in the dense bundle missed; ~3.7 pieces per labelled wire | model | WireNet's own probability map; no assembly setting changes it (sweep below) | model + data |

**Strand-level classification (adopted).** Geometry is now built once on the union of the wire classes (argmax is
any wire class), with the shipped junction, bridging, ownership and length rules, and each strand takes the class
with the largest summed probability over its *observed* pixels (bridge pixels carry no appearance evidence). A
strand whose top class holds less than 90% of that evidence is shown as **type uncertain** (grey; the class is kept
internally because δ_lim is identical for every class, and the evidence shares are in the API as `class_evidence`).
The 0.9 cut comes from validation: strands at ≥ 0.9 have the labelled class 97.9% of the time (669 strands), strands
below it 62% (86 strands, barely better than a guess). Crossings are unaffected: the junction rule still splits at
a crossing and joins only collinear pieces, so each wire of an X is classified from its own pixels (unit-tested).

| Validation, cached probabilities (`v216_strands_val.json`) | v2.15 per class | v2.16 strand |
|---|---|---|
| Labelled wires drawn in ≥ 2 classes | 5.86% | **3.41%** |
| Tolerant mAP50 | 0.0397 | 0.0432 |
| Power F1 / telecom F1 (tolerant) | 0.193 / 0.078 | 0.208 / 0.090 |
| Wire-pixel precision / recall (2 px) | 0.758 / 0.851 | 0.758 / 0.855 |
| Strand-τ variants (P(wire) ≥ 0.30–0.45) | – | 0.0262–0.0324: **rejected** (recall up, spans worse) |

**Joining re-tuned for strands** (`v216_join_sweep_val.jsonl`, 18 settings). Single changes moved fragments per
labelled wire by at most ±0.3 and tolerant mAP50 by ±0.003 (noise on 22 photos): **fragmentation is not an
assembly-parameter problem**, it is in WireNet's probability map. Two settings gave consistent small gains and act on
the observed wrong-join mechanism: bridge angle 25° → 35° and a 15 px lateral-offset limit (a dropout continues
along its wire; a parallel neighbour is offset sideways). Together: tolerant mAP50 0.0432 → **0.0471**, power F1
0.208 → 0.217, telecom 0.090 → 0.101, fragments 3.74 → 3.69, wire-pixel precision 0.758 → 0.760. (v2.14 rejected the
lateral limit with the per-class extractor on a different cache; the strand extractor changes the trade-off, and
both comparisons use one cache each.) Longer gaps (100–140 px), with or without model evidence in the gap, were
worse (0.0382–0.0428).

**Scene checks with the ADE20K parser already used by the ground gate** (`v216_context_val.json`):

* *Structure-edge gate (adopted).* A real wire is seen against one background (sky on both sides, or a wall on both
  sides) except where it crosses an edge; a deck, canopy or sign outline has sky on one side and structure on the
  other along its whole length. *Side contrast* = share of the span's skeleton where the scene class at ±d along the
  normal differs (d = max(8 px, 1.2% of the long side), clearing the parser's 384 px resolution). At ≥ 0.6 it removes
  2 of 658 label-supported strands (0.3%) and 3 of 140 unsupported ones on validation, and on `street1` 9–10 traces
  along the canopy and flyover edges (including three CRS 10). Removed traces are listed with the reason in the
  diagnostics, never dropped silently. A simpler "share on structure classes" rule could not be validated (validation
  has no flyovers or signboards: 0.000 signboard, ≈ 0 bridge) and would remove real wires seen in front of a deck.
* *Vegetation gate (adopted).* A YOLO vegetation region with < 40% of its area on ADE20K tree / grass / plant / palm
  / flower / field is suppressed. All 3 label-supported validation regions have ≥ 0.75 and are kept (vegetation AP and
  F1 unchanged); the `street1` region (0.00 vegetation, 0.70 bridge, 0.19 building) is removed, and with it the false
  vegetation contact. The 8 "unsupported" validation regions lie 47–100% on ADE20K vegetation: mostly unlabelled real
  vegetation (a label-completeness limit, Part 12).
* *Trajectory residual gate (rejected).* Catenary RMS residual ÷ stroke width does not separate supported from
  unsupported strands (median 0.16 vs 0.22; p95 1.48 vs 1.12).

**Measurement validity (correctness fixes, no threshold changed).**

* *Chord gate in model pixels.* `crs.min_chord_px` (100) is applied as 100 × max(1, long side ÷ 1024) on model
  output, because one model pixel is that many photo pixels; reference polygons, drawn at photo resolution, keep
  100 px. The CRS formula and every band are unchanged. On the 2048 px photos it sends 36–43 short spans per photo
  to "too short to measure", including the 12.6%, 11.7%, 10.2% and 9.7% sags measured on chords of 122–136 px. The
  largest remaining sag on `street1` (9.7%, chord 941 px) is a strand joined through a dense crossing onto a
  neighbouring cable: a wrong association that no validated rule removes yet (Part 12).
* *Capture envelope applied to sag.* When the EXIF 35 mm-equivalent focal length is below the envelope minimum
  (§3.3.1: 24 mm), CRS is still computed and stored, every scored span is flagged `lens_outside_envelope`, and sag
  does not raise the verdict; vegetation rules still apply. NOT AT RISK becomes INCONCLUSIVE with a retake instruction
  ("retake with the main (1×) camera").

**Field photos, v2.15 → v2.16** (`runs/v216/regression/summary.json`; no ground truth exists for these photos, so this
is visual inspection, not a measurement):

| | street1 (14 mm) | street2 (26 mm) | street3 (26 mm) |
|---|---|---|---|
| Wire sections | 136 → 108 | 154 → 146 | 151 → 147 |
| Scored spans | 84 → 38 | 101 → 61 | 96 → 61 |
| Largest sag | 12.6% → 9.7% | 11.7% → 7.6% | 11.2% → 7.2% |
| Spans with CRS > 6 | 17 → 10 | 19 → 10 | 18 → 9 |
| Structure edges / non-vegetation removed | 9 / 1 | 3 / 0 | 2 / 0 |
| Vegetation contacts | 1 → 0 | 0 → 0 | 0 → 0 |
| Type uncertain | – → 23 | – → 14 | – → 10 |
| Verdict | HIGH → **INCONCLUSIVE** (lens) | HIGH → HIGH | HIGH → HIGH |

The remaining high-sag spans on `street2` / `street3` were inspected: they are real sagging cables on the left of the
frame. They score HIGH because δ_lim is the 0.040 development placeholder (Part 12, item 1), not because of a
segmentation error; that limit is the consulting engineer's to set, and it was not changed.

**Full pipeline on validation** (`v216_pipeline_val.json`; `run_analysis` with WireNet, YOLO vegetation, scene gates
and scoring, scored as in `ml.evaluate`):

| | v2.15 | v2.16 |
|---|---|---|
| Strict / tolerant mAP50 | 0.0068 / 0.0382 | 0.0067 / **0.0456** |
| Power F1 / P / R (tolerant) | 0.193 / 0.175 / 0.217 | **0.217 / 0.201 / 0.236** |
| Telecom F1 / P / R (tolerant) | 0.078 / 0.068 / 0.091 | **0.101 / 0.091 / 0.115** |
| Vegetation F1 / P / R | 0.088 / 0.273 / 0.053 | 0.088 / 0.273 / 0.053 |
| Labelled wires in ≥ 2 classes | 5.86% | **3.41%** |
| Fragments per covered labelled wire | 3.76 | 3.69 |
| Verdicts NOT / MODERATE / HIGH | 10 / 10 / 2 | 11 / 9 / 2 |

**Full pipeline on the test split, run once after every setting was frozen** (`v216_pipeline_test.json`, 21 photos;
same script and scoring as the validation table):

| | v2.15 | v2.16 |
|---|---|---|
| Strict / tolerant mAP50 | 0.0204 / 0.0468 | 0.0203 / **0.0496** |
| Power F1 / P / R (tolerant) | 0.198 / 0.176 / 0.225 | **0.207 / 0.191** / 0.225 |
| Telecom F1 / P / R (tolerant) | 0.039 / 0.033 / 0.050 | **0.042 / 0.036** / 0.050 |
| Vegetation F1 / P / R | 0.163 / 0.364 / 0.105 | 0.163 / 0.364 / 0.105 |
| Labelled wires in ≥ 2 classes | 7.24% | **4.50%** |
| Fragments per covered labelled wire | 3.98 | 3.89 |
| Wires reported / scored / type uncertain | 877 / 705 / 0 | 804 / 651 / 105 |
| Verdicts NOT / MODERATE / HIGH | 7 / 7 / 7 | 10 / 5 / 6 |

The held-out result confirms the direction seen on validation: tolerant mAP50 +0.0028, the mixed-class rate falls by
about 38%, and precision rises for both wire classes at unchanged recall. Strict mAP50 is flat, because strict
matching is dominated by 1–2 px boundary offsets that span assembly does not change. Fragmentation is essentially
unchanged (limitation 13). On 21 photos the differences are small, so they are evidence of no regression, not proof of
a significant gain. Verdicts move towards NOT AT RISK because short traces now fall below the scaled chord gate and
structure edges are removed before scoring; δ_lim and the CRS mapping are unchanged.

**Span assembly under the eight capture conditions** (`v216_stress_spans_val.json`; same probability maps, only the
extractor differs; v2.15 = per-class spans, 25°, no lateral limit):

| Condition | Tolerant mAP50 v2.15 → v2.16 | Wires in ≥ 2 classes | Fragments per wire |
|---|---|---|---|
| clean | 0.0397 → **0.0471** | 5.9% → 3.4% | 3.76 → 3.69 |
| lowlight | 0.0213 → **0.0258** | 6.0% → 3.0% | 3.45 → 3.41 |
| glare | 0.0075 → 0.0079 | 6.4% → 3.9% | 2.08 → 1.98 |
| motion | 0.0135 → **0.0211** | 8.1% → 5.2% | 2.98 → 2.93 |
| defocus | 0.0045 → 0.0045 | 5.2% → 4.6% | 2.85 → 2.82 |
| jpeg | 0.0320 → **0.0381** | 5.4% → 3.3% | 3.28 → 3.24 |
| lowres | 0.0294 → **0.0380** | 6.8% → 3.9% | 3.60 → 3.59 |
| rainhaze | 0.0341 → 0.0333 | 4.7% → 3.7% | 3.76 → 3.71 |
| **mean** | **0.0227 → 0.0270** | lower in 8 / 8 | lower in 8 / 8 |

Guard rules (Part 8): validation tolerant mAP50 ≥ 0.0397 holds (0.0456); WireNet, its input and its class masks are
unchanged, so pixel IoU (0.525 / 0.418) and the eight-condition pixel mean (0.3872) are unchanged by construction.

**Full-resolution tiled inference (experimental, not shipped)** (`ml/experiments/v216_tiled.py`). Overlapping 1024 px
tiles at the photo's own resolution, linear blend, mirrored pass: 3.4–4.1 s per 2048 px photo. Visually, strokes are
thinner and adjacent wires separate more often than with the 1024 px pass, but each finds wires the other misses
(pixel agreement 0.36–0.53), and both miss much of the dense bundle's dark core. It cannot be measured here: 20 of the
22 validation photos are smaller than 1024 px (they are enlarged into the letterbox), so there is no high-resolution
ground truth. It stays off until high-resolution labelled photos exist.

**Not attempted, with the reason**

* *WireNet re-training with hard negatives.* The loss already includes clDice (Part 3). Hard-negative mining needs
  pixels known to be *not* wire, but the validation labels omit many real wires (82% of the "unsupported" strands lie
  on open sky), so mining "false positives" from these labels would teach the model to suppress real, unlabelled
  wires. The flyover, canopy and signboard examples that motivate it exist only in the three unlabelled field photos.
  Required first: human-verified labels on street-level photos with overpasses, signs and canopies, and re-traced
  dense bundles (Part 12).
**Pole detector** (`app/pipeline/poles.py`, `ml/poles/`). YOLO11n (COCO-pretrained, 2.6 M parameters, AGPL-3.0)
fine-tuned on the Zenodo 20091961 street-level set (§2.4), poles only, split by street sequence (train 2,009 images /
23 sequences, val 767 / 5, test 327 / 5), 640 px, batch 8, MPS, AMP off. The run was stopped by the operating system
for low memory after 15 of 30 epochs (it was sharing the 8 GB with the stress caching and the app); best epoch 13:
**validation box mAP50 0.635, mAP50-95 0.278, P 0.614, R 0.596** (`runs/detect/runs/poles/yolo11n/results.csv`,
resumable from `last.pt`). The test sequences have not been evaluated. On the 22 WIREFRAME validation photos it finds
14 pole-like structures in 11 photos (no pole labels exist there, so this is not an accuracy figure).

*Bridging across a detected pole (rejected)* (`v216_pole_bridging_val.json`): allowing a gap of up to 100 / 140 /
180 px when it crosses a pole box gives tolerant mAP50 0.0489 / 0.0472 / 0.0459 against 0.0471, fragments 3.687–3.692
against 3.692: no measurable gain, so `pole_occluder_gap` stays 0. The detector is shipped as *context only*
(`pole_weights: null` by default, because the training images are Google Street View captures and the weights are not
redistributed): boxes in the result's `scene.poles_detected` and `fit.pole_ends` per span when enabled locally.

* *Pole-anchored span endpoints for sag.* Reported (`fit.pole_ends`) but not used to gate CRS: there is no labelled
  evidence about which spans end at poles.

---

## 6. Version history

| Version | Main change | Headline result |
|---|---|---|
| v2.0 | Chapter III mathematics implemented exactly (replacing the original `main` code) | Part 11 lists the differences |
| v2.1 | Landing page, evaluation suite (Eq. 5, 12–18), Roboflow download, self-healing console, no-mock-detector rule | Geometry benchmark (40 synthetic scenes, 200 wires): curve RMSE median 0.27 px; sag-ratio error median 1.1% |
| v2.2 | Two portals: open Field Screener, authenticated admin command centre; reviews; exports; security | 0 console errors in browser walkthrough |
| v2.3 | Measured, triggered enhancement; training recipe (no vertical flip or rotation: an upside-down catenary is an arch) | – |
| v2.4 | Team dataset v1 audit (unusable fragments); VNI made illumination-invariant and calibrated on training photos | – |
| v2.5 | Revised four-class taxonomy; span-level export; vegetation separated from scoring | – |
| v2.6 | YOLO11s-seg trained on the M1 | Test strict mask mAP50 0.0005; wire AP 0 |
| v2.7 | 1024 px; the mask-ceiling diagnosis; **WireNet**; spans from the skeleton | Test 0.0204 / 0.0375 |
| v2.8 | Span stitching, chord gate, hazard verdict, reliability grade, edge denoise + flip TTA, stress suite | Validation 0.0283 → 0.0397; test 8-condition 0.0298 → 0.0360; ρ = 0.64 |
| v2.9 | Corroborated pole verdict; grade reasons; diagnostic figure | Verdicts test 18/3/0 → 7/7/7 |
| v2.10 | Glare gate; output-identical latency rewrite; scoped lint | Silent glare NOT AT RISK val 5 → 0, test 7 → 3; dense poles 35–46% faster |
| v2.10.1 | Live HTTP 500 fix (schema migration); universal ingestion; lossless GPS stripping | 8/8 live uploads, 0 HTTP 500 |
| v2.11 | Dual-polarity (night); full mask and reasons in the API; inspection console | Night test 0.0090 → 0.0313; daytime identical |
| v2.12 | MiT-B1 and ConvNeXt-Tiny backbones; fusion; Depth Anything V2 studies | Fusion 0.0397 → 0.0537 but 4.7 s: not shipped |
| v2.13 | Ground gate; per-span actions and retake guidance; screener redesign | Problem photo HIGH (false road trace) → NOT AT RISK |
| v2.14 | Provenance of span pixels (observed vs inferred); span ownership within blob; console redesign; website only | 0 verdict changes; honest rendering of bridged gaps |
| v2.15 | Single-screen landing page; centred upload card with inspection name and city; plain-language results page; diagnostics grouped and collapsed | 16/16 mocked and 3/3 real-photo browser checks |
| v2.16 | One class per physical strand (union geometry, evidence-weighted class, "type uncertain"); structure-edge and vegetation scene gates; chord gate in model pixels; capture envelope applied to sag; bridge angle 35° + 15 px lateral limit; YOLO11n pole detector (context) | Validation tolerant mAP50 0.0382 → 0.0456; wires in two colours 5.9% → 3.4%; ultra-wide field photo HIGH → INCONCLUSIVE; false vegetation contact removed |

---

## 7. Everything tried and rejected (all measured)

| Version | Idea | Evidence | Decision |
|---|---|---|---|
| v2.1 | High-contrast edge "fallback" detector | Edges cannot be classified; building edges would get CRS | Not built |
| v2.7 | SAM 2.1 with true boxes | Wire IoU 0.04–0.08 | Not used |
| v2.7 | YOLO-seg masks for wires | 1/4-grid ceiling median IoU 0.40 | Replaced by WireNet |
| v2.7 | Abandoned-wire Stage 2 (rebalancing, focal, oversampling, copy-paste) | Precision ≤ 0.04; floods or collapses | Not shipped |
| v2.8 | Multi-scale Retinex | Lower low-light and glare accuracy | Rejected |
| v2.8 | Multi-scale TTA | ≤ +0.006 at 2–3.5 s per image | Rejected |
| v2.8 | Topological loose-end (abandoned) detector | ~10 candidates per photo, precision < 0.03 | Shipped off |
| v2.8 | Pseudo-label self-training | No candidate set reached 0.75 precision | Not attempted |
| v2.9 | Motion-blur router (skip sharpening) | Pixel IoU +0.021, but span tolerant 0.0177 → 0.0098–0.0143 | Rejected |
| v2.9 | JPEG deblocking | Tolerant tie; strict 0.0058 → 0.0043 | Rejected |
| v2.9 | Service-drop split by image inclination | 24/24 audited "drops" were spans in perspective | Rejected |
| v2.9 | X-junction perfect matching | Identical (near tangents) or 0.0353 (far) | Rejected |
| v2.9 | Single-extreme-span override | CRS = 10 reproduces in 37% of retests | Rejected |
| v2.10 | Exposure-only glare gate | Rejects 14/22 bright-sky controls | Rejected (policy option) |
| v2.10 | Wire-density glare gate | Misses 2 of 5 silent cases | Rejected |
| v2.10 | Borderline band around k* | Instability 41% → 47–50% | Rejected |
| v2.11 | Hysteresis thresholding | Pixel recall 0.80 → 0.89, span 0.0397 → 0.018–0.028 | Rejected |
| v2.12 | MiT-B2, SegFormer-B2 | Out of memory / 2.31 s inference | Not trained |
| v2.12 | Three-model fusion | +0.014 tolerant, F1 up; 4.7 s | Not shipped (opt-in) |
| v2.12 | Single-pass fusion | 0.0503; needs p0 and grade recalibration | Not adopted |
| v2.12 | Depth gate on YOLO vegetation | Removes the night-sky false positive and 2 true trees (AP 0.024 → 0.0025) | Rejected |
| v2.12 | Photometric / darkness vegetation gates | Miss the night case or remove a true tree | Rejected |
| v2.13 | SegFormer-B0 scene parser | Research-only licence | Not used |
| v2.13 | Always-on CLAHE; black-hat tubeness | Lower tolerant mAP50 | Rejected |
| v2.13 | 12° bridging | 0.0357–0.0381 | Rejected |
| v2.13 | Closed-loop and isolated-below rules | Remove real wires on unlabelled street photos | Rejected |
| v2.13 | Upward-bulge rules | Remove labelled wires | Rejected |
| v2.14 | Bridge only with model evidence | No gain; losses from τ 0.15 | Rejected |
| v2.14 | Lateral-offset bridge gate | −0.0005 to −0.0031 | Rejected (re-tested and adopted with the v2.16 strand extractor) |
| v2.16 | Wire mask from P(wire) ≥ τ (0.30–0.45) | Pixel recall 0.855 → 0.87–0.90, tolerant mAP50 0.0432 → 0.0262–0.0324 | Rejected |
| v2.16 | Longer bridges 100–140 px, with or without model evidence | 0.0382–0.0428 | Rejected |
| v2.16 | Catenary residual ÷ stroke width as a trajectory gate | Does not separate supported from unsupported strands | Rejected |
| v2.16 | "Share on structure classes" wire gate | Not measurable on validation (no flyovers); removes wires in front of decks | Not adopted (side contrast used) |
| v2.16 | Tiled full-resolution inference | Visually thinner, separated strokes; no high-resolution labels to measure it | Experimental, off |
| v2.16 | Hard-negative WireNet re-training | Validation labels omit many real wires: mining would suppress them | Not attempted (annotation gap) |

**A recurring finding.** Pixel accuracy and span accuracy disagreed four times (YOLO masks, the motion router,
hysteresis, single v2.12 backbones). The screener needs continuous, separated spans, so span-level validation
decides, with pixel F1 as a guard. The three-model fusion is the only change that raised both.

---

## 8. How the test split was used (methodological integrity)

- **No rule, threshold or constant was chosen on test.** Every choice was made on the 22 validation photos.
- **v2.8:** test run once for the final tables.
- **v2.9:** test evaluated more than once, all superseded runs kept (`results/superseded/`). A locked run differed
  from v2.8 and the difference traced to a coupling bug (the blur fallback counted service drops as failed wires),
  fixed as a correctness fix. The drop split was then rejected on validation evidence. The verdict layer was re-run
  once more after p0 was re-measured on validation over all spans (subset p0 gave 6/7/8; final 7/7/7).
- **v2.10:** gate chosen on validation; test glare evaluation run once. The exposure-only alternative that would have
  caught the motivating test photo was **not** adopted, because choosing it after a test failure leaks test
  information.
- **v2.11:** one interrupted run completed by a second process; identical duplicate rows removed.
- **v2.12:** backbones and fusion weights chosen on validation (15 grid points with flip, 15 without); winner's score
  called optimistic; fusion never run on test. The frozen v2.12 equals v2.11 and was run once on test.
- **v2.13–v2.15:** all candidates decided on validation; benefits of the ground gate shown on unlabelled live photos
  (not part of any split). The v2.13+ test run on the frozen configuration has not been performed.
- **v2.16:** every setting (strand class, the 0.9 evidence share, 35° / 15 px joining, side contrast 0.6, vegetation
  share 0.4, pole bridging off) decided on validation; the field photos only illustrate. The single test run
  (`python -m ml.experiments.v216_pipeline_eval --split test`) was performed once, after freezing, on 16 Nov 2026
  (`v216_pipeline_test.json`, §5.10); no setting was changed after it.
- **Guard rules.** v2.8: tolerant pixel IoU must stay ≥ 97% of v2.7's. v2.9 onward: validation tolerant mAP50 ≥ 0.0397,
  pixel IoU ≥ 0.525 / 0.418, 8-condition mean ≥ 0.3872. Output-identical changes are proven with golden checks.

---

## 9. The web application

### 9.1 Pages (v2.15)

- **Landing (`/`).** One screen, no scrolling at desktop sizes. A drafting-style drawing of two poles and their
  wires; one gold wire shows the chord L and sag δ, with a live δ/L readout that follows the pointer. The wordmark
  "WIREFRAME" set large and centred, one sentence, the button *Open the Field Screener*, the authors' names (CANILA ·
  TUBIANOSA · VILLAMAR), and the screening disclaimer. Respects reduced motion.
- **Upload (`/workspace`).** One centred card: drop or choose a photo (preview, file name, pixel size, file size,
  Replace, Remove; type and 64 MB checks in the browser, re-checked by the server); *Inspection name*, *City* and
  *Barangay* (all optional); *Use my location* (asked only on click; a refusal never blocks the upload; no reverse
  geocoding, so nothing is guessed or sent elsewhere); *Screen this photo*. While processing, real steps are shown
  (uploading, location data removed, analysing); no invented percentage.
- **Results (`/workspace/result/{id}`).**
  1. Header: inspection name (or file name), place, date and time, original file name, PDF report, New inspection.
  2. Overall assessment, mirroring the server verdict: "High risk found" / "Moderate risk found" / "No high risk
     found" (with "This is a screening from one photo, not a safety inspection.") / "Photo not clear enough".
     Sections that read high on their own but were not corroborated are named, not hidden. A coverage line: "18 of
     27 wire sections could be assessed; 9 could not".
  3. Risk summary: High / Moderate / Low counts only; unassessed sections are never counted as low.
  4. Viewer: Original / Wires / Mask / Both; Layers (model mask, wire sections, estimated gaps, centre lines, fitted
     curve, sag measurement, section ends, numbers); overlay strength; vegetation; zoom; Fit; Reset. Masks drawn
     pixel-exact; line geometry on pixel centres.
  5. Wire sections list: Wire, Type, Condition, Finding ("Hangs low (4.5%)", "Too small or far to measure",
     "Overlaps other wires"); selecting a row highlights the wire and opens its detail (sag vs allowance, a sag score
     explained in words, vegetation, measurement details folded).
  6. What to do next: built from the findings (who to call, which sections first, vegetation to report, what photo
     would help) and always "Do not touch, climb toward or try to move any overhead wire. Treat every wire as live."
  7. Photo limitations: only those found (too small or far; overlapping; past the photo edge; estimated gaps; photo
     quality cause; drop wires not checked), each with the affected sections and what would help.
  8. Technical diagnostics (collapsed, for administrators and researchers): model information, image quality,
     segmentation metrics (incl. fragments outside sections and inferred pixels, ground structures ignored),
     statistical analysis (binomial corroboration, p0, α, server verdict and reasons), processing performance,
     configuration and execution.
- **Admin command centre (`/admin`).** Overview, analyses and detail (per-wire a, b, c, residual, L, δ, δ/L, δ_lim,
  CRS), sites map and MSPI ranking with live α/β sliders and the 66-cell sensitivity grid, pole history, reviews
  (confirm / override / dismiss with a required note, stored beside the unchanged scores), model and queue
  management, CSV and PDF exports.

### 9.2 API (selected)

| Endpoint | Access |
|---|---|
| `POST /api/analysis/upload` (file, inspection_name, city, barangay, latitude, longitude, labels) | public, rate-limited (20 per 15 min) |
| `GET /api/analysis/{id}`, `/result`, `/image`, `/wiremask.png`; `GET /api/reports/{id}/pdf` | public with the 128-bit analysis id (capability) |
| `GET /api/health`, `/api/config`, `/api/evaluation/metrics` | public |
| Listing, retry, delete, `/api/dashboard/*`, `/api/admin/*` | admin session |

The result carries `inspection {name, city, barangay}`, `hazard`, `quality`, `wires` (with `reason_code`, `reason`,
`reason_short`, `action`, `provenance`, `veg_clearance`), `suppressed_spans`, `retake_guidance`, `wire_mask`
statistics, `telemetry`, `parameters` and `flags`.

### 9.3 Security and data handling

scrypt password hashing (min 12 characters), **no default account** (`python -m scripts.create_admin <name>`, password
prompted); 256-bit session token in an HttpOnly, SameSite=Strict cookie, only its SHA-256 stored, 8 h expiry,
server-side logout; Origin/Referer CSRF check; login throttling (5 failures per 15 min); anonymous uploads cannot change
an existing pole's data; admin pages `X-Frame-Options: DENY`. GPS stripped losslessly; coordinates stored separately.
SQLite with foreign keys enforced, CHECK constraints, automatic add-missing-columns migration, a config fingerprint
stored with every analysis and report. **No mock detector:** with no model and no reference polygons the upload is
refused (503) before anything is stored.

### 9.4 Installation and model provisioning

The repository is self-contained: `git clone`, `pip install -r requirements.txt`, `uvicorn app.main:app`. The trained
weights are not in the code history; they are published on the repository's `model-weights` branch. On first start
`app/core/weights.py` downloads any missing file (`wirenet-r34.pt`, 97,918,915 bytes, SHA-256 `ab2353c7…`;
`wireframe-yolo11m-seg.pt`, 20,660,928 bytes, `1532d7ae…`) in a background thread, verifies size and SHA-256, installs
it atomically, retries on network errors, and never overwrites an existing file. Until the files arrive the screener
reports validation mode; the health check switches to screening as soon as they are in place (progress in
`GET /api/health` → `model.downloads`). Verified on a fresh copy with an empty `models/`: both files downloaded,
verified and loaded with no restart. `WIREFRAME_AUTO_DOWNLOAD=0` disables it (offline installs);
`WIREFRAME_WEIGHTS_BASE_URL` points at another mirror. The scene parser is fetched from the Hugging Face Hub on first
use. Licences: the vegetation weights derive from Ultralytics YOLO11 (AGPL-3.0); WireNet from ResNet-34 (BSD-3) and
segmentation-models-pytorch (MIT); dataset CC BY 4.0.

### 9.5 Outputs beyond the page

- **PDF report:** disclaimer on page 1 and every footer; inspection name, place, pole code, file name; verdict and
  reasons; reliability and its reason; per-wire table; three-panel diagnostic figure (photo + grade badge;
  segmentation by class; spans by CRS level with clearance lines).
- **Batch street audit** (`scripts/batch_inspect.py`): a folder or a survey video (frames every N s, near-duplicates
  removed by pHash) → `inspection.csv`, `summary.json/.md`, diagnostic figures, `rejected.csv`, and a LabelMe review
  queue of high-risk and low-reliability photos for correcting into new training labels.

---

## 10. Software verification

| Check | Result |
|---|---|
| Unit and integration tests (`pytest`) | 551 passed (v2.16; `tests/test_v216_strands.py` covers the strand class, crossings, parallel wires, side contrast, the lens rule, the chord gate and pole bridging) |
| Lint (`ruff check app ml scripts tests`) | clean |
| Mocked browser check (`scripts/ui_check.py --mock`): 4 verdict states × light/dark × 1440 and 1024 px | 16 / 16 pass; 0 console errors |
| Real-photo browser check (P-B1D137, a handrail street photo, the night pole) | 3 / 3 pass: name/city/barangay saved and shown, PDF valid, every view and control, table ↔ photo selection |
| Upload behaviours | no location request on load; refusal does not block; wrong type and > 64 MB rejected; Remove; drag and drop |
| Whole-pipeline golden check | v2.10 = v2.9 on 22/22 validation photos; v2.11 and v2.12 identical on clean photos |
| Live upload check (v2.10.1) | every format, 64×64 to 4000×3000 RGBA (37.7 MB), 0 HTTP 500 |

Notable bugs found by verification and fixed: untrained smoke-run weights installed into `models/` (v2.1); unsharp mask
returning blurred photos unchanged (v2.7); silent NOT AT RISK for unreadable photos (v2.8); blur-fallback coupling with
service drops (v2.9); a stale database schema causing HTTP 500 on every live upload (v2.10.1); GPS stripping that
re-encoded JPEGs and changed pixels (v2.10.1); phone-width table clipping (v2.13); browsers reusing an old landing
stylesheet with new markup (v2.15: assets now revalidated on every load).

---

## 11. Deviations from Chapter III and corrections for the manuscript

| Item | What the code does | Why / action for the manuscript |
|---|---|---|
| **Eq. 3 (VNI)** | The image mean is removed before the Hann window (`vni.remove_mean: true`) | As written, the window leaks the mean into low-frequency bins, so the same texture scored 0.47 dark and 0.036 bright. **State in Eq. 3 that the mean is removed** |
| Sag measurement precision | CRS only when chord ≥ 100 px (σ/L ≤ ¼·δ_lim), with σ = 1 *model* pixel: on model output the gate is 100 × max(1, long side ÷ 1024) photo pixels (v2.16) | Add as a measurement-validity condition |
| Capture envelope | Checked since v2.0; from v2.16 a focal length below 24 mm (35 mm eq.) also withholds sag from the verdict | State in §3.3.1 that out-of-envelope lenses invalidate image-space sag |
| Wire classification | One class per strand from the summed class probability of its observed pixels; "type uncertain" below 90% of the evidence (v2.16) | Chapter III classifies per detection; add the strand rule and the uncertain category |
| Pole verdict | Corroborated binomial rule (p0 measured by test–retest) | New interpretation layer; Eq. 1–11 unchanged |
| Reliability grade, glare gate | New | Not in Chapter III |
| Segmentation architecture | WireNet (U-Net R34) for wires, YOLO11s-seg for vegetation, scene parser for ground | Chapter III specifies YOLO11-seg; its 1/4-resolution masks cannot represent 2 px wires (Part 5.2) |
| Evaluation sections | Equations are in §3.10–3.11 (§3.7 is the Operational Framework) | Correct cross-references |
| H3 | RMSE between CRS and the engineer's 0–10 ratings, not a curve-fit error | Curve-fit accuracy reported separately (synthetic benchmark) |
| Risk labels | Low / Medium / High (Table 1.2) in the API; the field page says Low / Moderate / High risk | Keep Table 1.2 in the manuscript |
| Chord endpoints | Fitted curve at the first and last centreline x | §3.6.5 "two ends of the fitted centreline" |
| Residual limit τ | RMS residual ÷ L ≤ 0.02 | Scale-invariant |
| Spur pruning | Shortest spur per junction per pass | A Y-fork at a wire end otherwise erases both arms |
| Hann window | Periodic (DFT-even) | Chapter III does not specify |
| Multiple photos per pole | Latest analysis represents the pole | Thesis assumes one standardised photo |
| Quality rejections | Only objective (undecodable, blank); small photos accepted and graded Retake | The original blur/darkness cut-offs had no source |
| Bounding boxes | Never converted to polygons; sent to an annotation queue | A box around a diagonal wire is a filled rectangle |
| Fiber optic drops | Labelled and scored as `telecom-cable`, using telecom conductor values in Eq. 9 | Revisit with the consulting engineer |
| Removed from the original code | Detection-confidence "severity", `detection_risk`, `barangay_risk`, 50/50 score, metric clearance from an assumed pole height | Not in Chapter III; uncalibrated |

---

## 12. Limitations and open problems (in order of expected impact)

1. **δ_lim calibration and reference ratings (H3).** Every sag band depends on the placeholder δ_lim = 0.040, and no
   result validates CRS against an engineer.
2. **Verdict instability.** The pole verdict changes in 41% of mildly degraded photos whose wires survive. A steadier
   per-span sag estimate is needed (e.g. averaged over variants, or from the probability map).
3. **Perspective.** Spans receding in depth read high 3.3× more often; image-space sag is perspective-sensitive.
4. **Telecom recall** is 0.05 on test (0.025 at night); dense bundles are not separated.
5. **Merged neighbouring cables** in very dense tangles (e.g. P-B1D137 span 14 carries a fragment of the cable below
   it, connected through the tangle in the model's own mask).
6. **Small dataset:** 145 photos, 21 test photos, 22 validation photos; differences below ~0.002 are noise; the test
   split has 7 abandoned-wire instances.
7. **Real night data:** night results use synthetic night; YOLO vegetation is unvalidated at night and marks dark sky
   as vegetation on the real night pole.
8. **Abandoned wire** cannot be learned from 27 labels.
9. **Street-level false wires:** road traces are removed; fence and railing traces in front of facades remain, and the
   ground gate has no labelled street-level measurement.
10. **Glare and motion blur** are turned into "retake", not results; the glare gate and motion threshold are calibrated
    on synthetic conditions.
11. **Service drops** are scored as spans; separating them needs 3-D cues or a labelled class.
12. **Compute.** The more accurate fusion is blocked only by latency on an 8 GB M1; the v2.13+ quiet latency run is
    outstanding (the v2.16 test run was performed).
13. **Label completeness:** validation labels omit many wires in dense bundles and low isolated wires, so "no true
    positive removed" is weak evidence for any suppression rule. In v2.16, 82% of the "unsupported" strands lie on open
    sky and 8 of 11 "unsupported" vegetation regions lie on scene-parser vegetation: most are unlabelled real objects.
14. **Fragmentation (v2.16).** About 3.7 predicted pieces cover each labelled wire, and no span-assembly setting moves
    this by more than ±0.3: it is in WireNet's probability map. Needs better pixel recall on thin wires (more and
    re-traced training data; high-resolution labelled photos to evaluate tiled inference).
15. **Wrong association in dense crossings (v2.16).** A strand can still be joined through a crossing onto a
    neighbouring cable (`street1`: a 941 px strand at 9.7% sag). The joins are collinear within 35°; a per-pixel
    instance embedding or labelled crossings would be needed to resolve dense bundles.
16. **Scene gates without street-level labels (v2.16).** The structure-edge and vegetation gates are checked on
    validation for *not removing* labelled wires and vegetation, and shown to remove the false traces on the three
    unlabelled field photos; their precision on overpass scenes has no labelled measurement.
17. **Pole detector domain (v2.16).** Trained on Brazilian sidewalk Street View frames where the camera looks along
    the street; WIREFRAME photos look up at the wires. Its "pole" class includes lamp and sign posts. Context only.

**Recommended next steps:** engineer reference ratings and δ_lim calibration; several hundred tightly traced
abandoned-wire instances and re-traced dense bundles; labelled street-level and night photos of the team's own poles;
retrain on the larger set and report on a test split of ≥ 100 photos; agree a tolerance-aware instance metric for thin
structures with the panel; test the fusion on faster hardware after re-checking p0 and the grade on validation.

---

## 13. Reproduction

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt -r requirements-ml.txt
python -m scripts.create_admin <username>                 # password prompted; no default account
uvicorn app.main:app --port 8000                          # / landing · /workspace screener · /admin
python -m pytest && ruff check app ml scripts tests

# training
python -m ml.wirenet train --out models/wirenet-r34.pt                       # WireNet-R34 (v2.7 recipe)
python -m ml.wirenet train --encoder mit_b1 --batch 2 --epochs 40 --out models/v212_mitb1.pt
python -m ml.train --data datasets/wireframe_seg/data.yaml                   # YOLO detector

# cached probabilities and span experiments (validation)
python -m ml.experiments.cache --split val
python -m ml.experiments.sweep_spans --out docs/thesis/results/v28_span_sweep.jsonl
python -m ml.experiments.ablation_final --out docs/thesis/results/v28_span_ablation_final.jsonl
python -m ml.experiments.v214_bridge_evidence --out docs/thesis/results/v214_bridge_evidence_val.json

# v2.16: strands, joining, scene gates, full pipeline, stress, field photos, poles
python -m ml.experiments.cache --split val --tag=-v216                       # + --condition <c> for each stress condition
python -m ml.experiments.v216_strands --out docs/thesis/results/v216_strands_val.json
python -m ml.experiments.v216_join_sweep --out docs/thesis/results/v216_join_sweep_val.jsonl
python -m ml.experiments.v216_context                                        # scene-parser study -> v216_context_val.json
python -m ml.experiments.v216_stress_spans --out docs/thesis/results/v216_stress_spans_val.json
python -m ml.experiments.v216_pipeline_eval --split val                      # v2.15 vs v2.16, full pipeline
python -m ml.experiments.v216_regression                                     # field photos in runs/v216/regression/
python -m ml.experiments.v216_tiled street1 street2                          # experimental tiled inference (visual)
python -m ml.poles.prepare && python -m ml.poles.train                       # pole dataset (Zenodo 20091961) + YOLO11n

# stress benchmark, verdicts, golden checks
python -m scripts.stress_test_all_conditions --split test --configs v2.12 --conditions clean lowlight night \
    --out docs/thesis/results/v212_spans_test.jsonl
python -m ml.experiments.verdict_dist --splits test --out docs/thesis/results/v29_verdicts_pipeline_test.json
python -m ml.experiments.pipeline_golden --check docs/thesis/results/v210_pipeline_golden_v29_val.json

# glare, polarity, fusion, depth
python -m ml.experiments.glare_gate_eval --diag docs/thesis/results/v210_glare_diag_val.jsonl \
    --out docs/thesis/results/v210_glare_gates_val.json
python -m ml.experiments.polarity_eval --out docs/thesis/results/v211_polarity_val.jsonl
python -m ml.experiments.ensemble_eval --caches r34=runs/cache/val-v28 mitb1=runs/cache/val-mitb1 \
    cnx=runs/cache/val-cnx --out docs/thesis/results/v212_ensemble_val.jsonl
python -m ml.experiments.depth_sag --out docs/thesis/results/v212_depth_sag_val.json

# browser checks
python -m scripts.ui_check --mock --base http://127.0.0.1:8000 --out docs/thesis/results/v215_ui_check_mock.json
python -m scripts.ui_check --base http://127.0.0.1:8000 photo1.jpg photo2.jpg \
    --out docs/thesis/results/v215_ui_check_real.json
```

---

## 14. Evidence files

Every number above traces to one of these (`docs/thesis/results/` unless stated).

| Topic | Files |
|---|---|
| Dataset | `dataset_audit_v27.json` |
| v2.7 models | `v27_comparison.json`, `v27_val_span_modes.json`, `wirenet_stage1_training_log.json`, `wirenet_stage2_attempt4_log.json`, `yolo1024_run1_results.csv`, `yolo1024_run2_results.csv`, `server_check_v27.json` |
| Span assembly | `v28_span_sweep.jsonl`, `v28_span_ablation_final.jsonl`, `v28_span_confirm_val.json` |
| Stress benchmark | `v28_stress_pixel_val.jsonl`, `v28_stress_spans_test.jsonl`, `v29_stress_pixel_val.jsonl`, `v29_stress_pixel_val_r2.jsonl`, `v29_stress_spans_val.jsonl`, `v29_stress_spans_test.jsonl`, `v211_spans_val.jsonl`, `v211_spans_test.jsonl`, `v212_spans_test.jsonl` |
| Verdicts and p0 | `v28_verdicts.json`, `v28_sag_ratio_distribution_val.json`, `v29_span_geometry_val.json`, `v29_span_geometry_summary_val.json`, `v29_span_geometry_summary_all_val.json`, `v29_verdicts_val.json`, `v29_verdicts_pipeline_val.json`, `v29_verdicts_pipeline_test.json`, `superseded/*` |
| Reliability, failure audit, loose ends | `v28_quality_val.jsonl`, `v28_failure_audit_val.json`, `v28_loose_end_sweep_val.json` |
| Motion, crossings | `v29_motion_calib_val.json`, `v29_xjunction_val.jsonl` |
| Glare and latency | `v210_glare_diag_val.jsonl`, `v210_glare_gates_val.json`, `v210_glare_after_val.jsonl`, `v210_glare_test_gate_off.jsonl`, `v210_glare_test_gate_config.jsonl`, `v210_latency_paired.json`, `v210_span_golden_val.json`, `v210_pipeline_golden_v29_val.json`, `server_check_v28.json`, `server_check_v29.json`, `server_check_v210.json`, `live_upload_check_v210.json` |
| Night, hysteresis | `v211_polarity_val.jsonl`, `v211_hysteresis_val.jsonl`, `ui_check_v211.json` |
| Fusion, depth | `v212_ensemble_val.jsonl`, `v212_ensemble2_val.jsonl`, `v212_ensemble3_val.jsonl`, `v212_ensemble3_notta_val.jsonl`, `v212_ensemble_bootstrap_val.json`, `v212_latency_val.json`, `v212_veg_guard_val.json`, `v212_veg_darkness_val.json`, `v212_depth_sag_val.json`; training logs `models/v212_*.log.json` |
| Ground gate, pre-processing, bridging | `v213_filters_val.json`, `v213_span_features_val.jsonl`, `v213_enhance_val.jsonl`, `v213_bridging_val.json`, `ui_check_v213.json` |
| Provenance, ownership | `v214_bridge_evidence_val.json`, `v214_bridge_lateral_val.json`, `v214_own_component_val.json`, `v214_ui_check_mock.json`, `v214_ui_check_real.json` |
| Website | `v215_ui_check_mock.json`, `v215_ui_check_real.json` |
| v2.16 strands, joining, scene gates, pipeline | `v216_strands_val.json`, `v216_join_sweep_val.jsonl`, `v216_context_val.json`, `v216_stress_spans_val.json`, `v216_pipeline_val.json`, `v216_pipeline_test.json`; field photos (local only, contain people and plates): `runs/v216/regression/summary.json` |
| v2.16 poles | `v216_poles_val.json`, `v216_poles_test.json`, `v216_pole_bridging_val.json`; dataset split `datasets/external/poles_yolo/split.json` (local) |

**Figures** (`docs/thesis/figures/`): `stress_conditions.jpg` (the eight capture conditions); `high_crs_spans_val.jpg`
(visual audit of high-CRS spans); `loose_end_candidates_val.jpg`; `failure_hard_*.jpg` / `failure_easy_*.jpg` (green
matched, red predicted only, blue labelled only); `v29_diagnostic_example.jpg` (the PDF diagnostic figure);
`v29_drop_audit_val.jpg`; `v29_inclination_val.png`; `v29_motion_router_val.png`; `v29_verdicts.png`;
`v212_night_veg_guard.jpg`; `v213_ui_hazard_dark_desktop.jpg`, `v213_ui_not_at_risk_light_desktop.jpg`,
`v213_ui_loader_dark_desktop.jpg` (v2.13 interface).
