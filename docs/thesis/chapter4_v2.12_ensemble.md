# Chapter 4 (continued) — v2.12: Complementary Backbones, Probability Fusion and Monocular Depth

Branch `v2.12-multi-model-ensemble`, from `eff526a` (v2.11). The shipped WireNet-R34 (`models/wirenet-r34.pt`,
`ab2353c7`) and every earlier commit are untouched; new checkpoints are saved separately as `models/v212_*.pt`.
Every decision was made on the 22 validation photos. The 21 test photos were used once, after v2.12 was frozen
(§4.14.7).

**Hardware.** All training, inference, evaluation and latency runs in this report, and in every earlier version
from v2.6 onwards, were executed locally on one **Apple Silicon M1 laptop with 8 GB of unified memory**
(`hw.memsize` 8,589,934,592; PyTorch MPS backend for the GPU, CPU otherwise). CPU and GPU share those 8 GB, so
model choice, batch size and concurrency below are bounded by memory, not only by accuracy. No cloud or external
GPU was used.

| Mission | What was found | Outcome |
|---|---|---|
| Complementary backbones | MiT-B1 (transformer) and ConvNeXt-Tiny (ConvNet) U-Nets trained with the WireNet recipe. Each alone beats R34 at span level on validation (tolerant mAP50 0.0429 and 0.0458 against 0.0397), but each loses telecom pixel F1. | Both kept as separate files (`models/v212_*.pt`); neither replaces R34 |
| Probability fusion | R34 0.25 + MiT-B1 0.5 + ConvNeXt 0.25: span tolerant mAP50 **0.0397 → 0.0537**, power / telecom tolerant F1 0.792 / 0.677 → 0.819 / 0.684. Beats R34 in 94.4% of paired bootstrap resamples. Passes the accuracy gate. | **Not shipped: 4.7 s per photo, 5.5 s on the dense pole**, over the 3.5 s limit on the 8 GB M1. Kept as an opt-in spec, `models/v212_ensemble.json` |
| Fusion without the mirror pass | Fits the time limit (about 2.6–2.7 s) but keeps less gain (0.045–0.050), and it changes the inputs of the reliability grade and the verdict's p0, both calibrated on R34. | Not adopted (re-calibration would be needed; selection after ~30 tries is optimistic) |
| Depth Anything V2 vegetation guard | The depth gate removes the night-sky false positive but also true trees (vegetation AP50 0.024 → 0.0025). Photometric gates are safe but miss it. | **Rejected**; limitation kept |
| Depth and sag | High-band share rises with depth recession, 3.2% → 10.4% (Fisher p = 0.0039). | **Finding**, no correction (no metric depth, no ground truth) |
| Shipped pipeline | v2.12 ships v2.11's pipeline. Whole-pipeline golden check 22/22 identical; R34 hash unchanged. | Test split run once (§4.14.7) |

---

## 4.14.1 Why complementary backbones

WireNet-R34 is a convolutional U-Net. Its errors (v2.7 §4.3, v2.8 §4.8.9) are mostly broken thin spans and
power/telecom confusion where conductors run in parallel bundles. Two kinds of backbone could err differently:

- a **hierarchical transformer** (SegFormer's Mix Transformer, MiT), whose self-attention relates distant parts of a
  long thin structure; and
- a **modern ConvNet** (ConvNeXt), with larger kernels and a different inductive bias from ResNet.

If their errors are not shared, averaging class probabilities could recover spans that each model alone breaks.

## 4.14.2 Backbone probe under the 8 GB memory limit

Before any training, each candidate was timed for one training step (batch 4 × 512², the R34 recipe) and one
1024² inference, on the M1 GPU (MPS):

| Backbone (decoder) | Parameters | Train step | 1024² inference | Peak GPU memory | Outcome |
|---|---|---|---|---|---|
| ResNet-34 (U-Net), shipped | 24.4 M | 0.92 s | 0.27 s | — | baseline |
| MiT-B1 (U-Net) | 16.4 M | 1.15 s | 0.46 s | 2.4 GB | **trained** |
| ConvNeXt-Tiny (U-Net) | — | 2.11 s | 0.60 s | 3.6 GB | **trained** |
| HRNet-W18 (U-Net) | — | 2.01 s | 0.55 s | 2.5 GB | not trained: same ConvNet family as ConvNeXt, slower; one ConvNet was budgeted |
| MiT-B2 (U-Net) | — | out of memory at batch 4 | — | — | rejected |
| SegFormer-B2 (native all-MLP head) | — | 2.63 s | **2.31 s** | — | rejected: inference alone would dominate the 3.5 s budget |

A probe that loaded several models into one process at the same time pushed the machine into swap and had to be
killed; the live inspection server was stopped for the rest of the sprint to free memory. Both trained models
therefore use **batch 2** (instead of 4); the training resolution, crops, loss and validation are otherwise the R34
recipe.

## 4.14.3 Training (`ml/wirenet.py --encoder`, logs `models/v212_*.log.json`)

The trainer is the v2.7 WireNet trainer, now taking `--encoder` and `--arch`. Recipe, identical for both:

- data: the 102 training photos, 1024² letterbox, random 512² crops (80% centred on a wire);
- loss: cross-entropy + Dice + clDice; AdamW, one-cycle learning rate, peak 3·10⁻⁴;
- ImageNet-pretrained encoders (smp `mit_b1`; timm `tu-convnext_tiny`);
- **a full validation pass on all 22 photos after every epoch**; checkpoints kept for the best strict wire mIoU
  (`v212_*.pt`), the best tolerant wire IoU (`*-best-tolerant.pt`) and the last epoch (`*-last.pt`).

Each checkpoint writes an `.arch.json` sidecar (`{"encoder", "arch", "classes"}`). The application reads it, so
any of them can be loaded without code changes, and the shipped R34 file (no sidecar) loads as before.

| Model | Epochs | Batch | Wall time | Best epoch (strict) | Validation tolerant IoU power / telecom (training metric) |
|---|---|---|---|---|---|
| WireNet-R34 (v2.7, for reference) | 40 | 4 | — | — | 0.525 / 0.418 |
| MiT-B1 U-Net (`f595ef68`) | 40 | 2 | 4.66 h (286–779 s per epoch) | 32 | 0.543 / 0.404 |
| ConvNeXt-Tiny U-Net (`9633cee8`) | 20 | 2 | 3.39 h (515–773 s per epoch) | 16 | 0.540 / 0.415 |

The per-epoch times vary because the laptop throttles under sustained load. The best-tolerant checkpoints are
epoch 30 for MiT-B1 (0.546 / 0.413) and epoch 16 for ConvNeXt (the same checkpoint). The strict-best checkpoints
(`v212_*.pt`) are the ones evaluated below, as for R34. The weight files are not committed (66 MB and 128 MB); their
SHA-256 prefixes are given above and the logs and sidecars are committed.

**Data loading.** As in every WireNet run, the trainer decodes all training and validation photos once at start-up
and keeps the images and label masks in RAM as `uint8` arrays; only the random crops and augmentations are computed
per step, so there is no disk I/O during training.

ConvNeXt-Tiny got 20 epochs instead of 40 to finish within the session; 40 epochs would have taken about 7 hours
at the measured pace. Its full one-cycle schedule over those 20 epochs, end-to-end fine-tuning from epoch 1 and
validation on all 22 photos after every epoch were kept, so checkpoint selection is the same as for the other
models. Its validation curve had flattened (epochs 16–20: tolerant power 0.534–0.540, telecom 0.407–0.415).

**A schedule that was planned and not run.** A shorter 14-epoch schedule for ConvNeXt (encoder frozen for epochs
1–3, then cosine decay, validation from epoch 4) was briefly queued to save time. It was cancelled before it ran, in
favour of the standard recipe above, so the comparison between backbones is not confounded by different schedules.
The code for it was removed; no result in this report comes from it.

## 4.14.4 Probability fusion (`ml/experiments/ensemble_eval.py`, `v212_ensemble_val.jsonl`)

Each model's flip-averaged class probabilities on the 22 validation photos are cached
(`ml.experiments.cache --wirenet`). The fused map is a weighted mean, p = Σ wᵢ pᵢ, with weights on a 0.25 simplex
grid. Every candidate is scored at two levels:

- **pixel**: 2 px tolerant IoU, precision, recall and F1 for power and telecom, plus the power↔telecom confusion
  rate (the share of a class's labelled pixels predicted as the other class);
- **span**: strict and tolerant span mAP50 from the shipped span assembler and constants (`FINAL`), the v2.8
  metric every version is judged on.

**Adoption gate, fixed before any result was seen:** a fusion is adopted only if its span tolerant mAP50 is above
v2.11's 0.0397 **and** neither class's pixel tolerant F1 falls, **and** the added inference fits the memory and
latency limits of §4.14.2.

Baseline (WireNet-R34 alone, the v2.11 configuration):

| | Strict / tolerant span mAP50 | Power tol. IoU / F1 | Telecom tol. IoU / F1 | Power→telecom | Telecom→power |
|---|---|---|---|---|---|
| R34 | 0.0068 / **0.0397** | 0.528 / 0.792 | 0.422 / 0.677 | 0.018 | 0.045 |

### Single models and every fusion on the 0.25 grid (`v212_ensemble3_val.jsonl`)

Every weight vector on the simplex, step 0.25 (15 vectors; R34 / MiT-B1 / ConvNeXt):

| R34 | MiT-B1 | ConvNeXt | Strict / tolerant span mAP50 | Power tol. F1 | Telecom tol. F1 | Telecom→power | Gate |
|---|---|---|---|---|---|---|---|
| 1 | 0 | 0 | 0.0068 / 0.0397 | 0.792 | 0.677 | 0.045 | baseline |
| 0 | 1 | 0 | 0.0065 / 0.0429 | 0.808 | 0.664 | 0.049 | fails (telecom F1) |
| 0 | 0 | 1 | 0.0064 / 0.0458 | 0.797 | 0.669 | 0.041 | fails (telecom F1) |
| 0.75 | 0.25 | 0 | 0.0064 / 0.0432 | 0.804 | 0.684 | 0.047 | passes |
| 0.5 | 0.5 | 0 | 0.0067 / 0.0392 | 0.815 | 0.685 | 0.046 | fails (span) |
| 0.25 | 0.75 | 0 | 0.0071 / 0.0497 | 0.814 | 0.674 | 0.048 | fails (telecom F1) |
| 0.75 | 0 | 0.25 | 0.0064 / 0.0447 | 0.802 | 0.681 | 0.046 | passes |
| 0.5 | 0 | 0.5 | 0.0063 / 0.0487 | 0.807 | 0.681 | 0.045 | passes |
| 0.25 | 0 | 0.75 | 0.0063 / 0.0462 | 0.804 | 0.676 | 0.043 | fails (telecom F1) |
| 0 | 0.75 | 0.25 | 0.0064 / 0.0468 | 0.813 | 0.671 | 0.047 | fails (telecom F1) |
| 0 | 0.5 | 0.5 | 0.0061 / 0.0487 | 0.815 | 0.680 | 0.042 | passes |
| 0 | 0.25 | 0.75 | 0.0062 / 0.0467 | 0.808 | 0.678 | 0.040 | passes |
| 0.5 | 0.25 | 0.25 | 0.0065 / 0.0455 | 0.813 | 0.687 | 0.046 | passes |
| 0.25 | 0.25 | 0.5 | 0.0065 / 0.0516 | 0.814 | 0.685 | 0.042 | passes |
| **0.25** | **0.5** | **0.25** | 0.0067 / **0.0537** | **0.819** | 0.684 | 0.045 | **passes, best** |

- **Every backbone alone beats R34 at span level** but loses telecom pixel F1. The fusion gets both.
- **Three models beat any pair**; R34 still contributes (its weight is non-zero in the best mix).
- **Strict mAP50 does not move** (0.0061–0.0071): the gain is in spans that are found within 2 px, not in exact
  masks, as in v2.8.
- **Power/telecom separation improves only slightly.** Power→telecom falls from 0.018 to 0.013 at best; telecom→
  power stays at 0.040–0.049. Fusion does not fix the class confusion between parallel bundles.
- The span score is not smooth on the grid (0.0392 at 0.5 / 0.5 / 0 between 0.0432 and 0.0497): with 22 photos,
  a few spans move it by several thousandths.

### Is the gain larger than photo-to-photo noise? (`ensemble_bootstrap.py`, `v212_ensemble_bootstrap_val.json`)

The 22 photos were resampled with replacement 2,000 times (seed 0); in each resample every candidate and R34 are
scored on the same photos (paired), and tolerant mAP50 is recomputed exactly as the evaluator does (the full-sample
values are reproduced to four decimals).

| Candidate (R34 / MiT-B1 / ConvNeXt) | Tolerant mAP50 | 95% interval of the gain over R34 | Resamples where it beats R34 |
|---|---|---|---|
| 0.75 / 0.25 / 0 | 0.0432 | −0.0065 to +0.0126 | 76.9% |
| 0 / 0.5 / 0.5 | 0.0487 | −0.0057 to +0.0228 | 90.2% |
| 0.25 / 0.25 / 0.5 | 0.0516 | −0.0021 to +0.0276 | 95.0% |
| **0.25 / 0.5 / 0.25** | **0.0537** | −0.0039 to +0.0315 | **94.4%** |

The three-model gain is probable but not established: every interval still includes zero. Choosing the best of 15
grid points on the same 22 photos also makes the winner's score optimistic.

### The fusion in the application (`app/pipeline/wirenet.py`, `models/v212_ensemble.json`)

`segmentation.wirenet_weights` may now name a JSON spec instead of a checkpoint:
`{"members": [{"weights": "models/wirenet-r34.pt", "weight": 0.25}, …]}`. `load_wirenet` then builds one module
that returns log(Σ wᵢ softmaxᵢ). The softmax every caller already applies gives back exactly the fused
probabilities, so flip TTA, dual polarity, the uncertainty summary and every calibration script work unchanged.

- **Equivalence.** Caching the 22 validation photos through the in-app fusion reproduces the offline fusion: the
  largest probability difference is 0.0005 (the float16 cache), the class maps agree on 99.998% of pixels, and span
  tolerant mAP50 is 0.0536 (offline 0.0537).
- **Latency** (`v212_latency_val.json`; full `run_analysis`, median of 2 after a warm-up, all 22 validation photos
  plus the dense test pole 145634; load average 2.5–3.4):

| | R34 (shipped) | Fusion (R34 + MiT-B1 + ConvNeXt, flip TTA) |
|---|---|---|
| Median per photo | 1.75 s | **4.71 s** |
| 90th percentile / slowest | 1.93 s / 1.99 s | 6.16 s / 6.54 s |
| Segmentation stage (median) | 1.37 s | 4.00 s |
| Dense pole (78 / 74 spans) | 1.82 s | **5.47 s** |
| Peak resident memory | 1.56 GB | 1.49 GB |

  One 1024² forward pass costs 0.20 s (R34), 0.58 s (MiT-B1), 0.69 s (ConvNeXt) and 1.49 s fused. Batching the
  plain and mirrored passes gives identical probabilities but no speed-up (0.42 / 1.16 / 1.65 s for batch 2): the
  M1 GPU is already saturated. A fused batch of 2 takes 11.7 s, because it no longer fits comfortably in GPU memory.
  Dark photos would pay double again (dual polarity).

### Fusion without the mirror pass (`v212_ensemble3_notta_val.jsonl`)

Dropping flip TTA would bring the fusion back to roughly 2.6–2.7 s. Every model and every grid point was re-cached
and re-scored without the mirrored pass:

| R34 / MiT-B1 / ConvNeXt, single pass | Tolerant span mAP50 | Power tol. F1 | Telecom tol. F1 |
|---|---|---|---|
| 1 / 0 / 0 | 0.0356 | 0.791 | 0.671 |
| 0 / 0.5 / 0.5 | 0.0503 | 0.814 | 0.677 |
| 0.25 / 0.25 / 0.5 | 0.0460 | 0.816 | 0.682 |
| 0.5 / 0.25 / 0.25 | 0.0453 | 0.812 | 0.684 |
| 0.25 / 0.5 / 0.25 | 0.0437 | 0.818 | 0.679 |

Single-pass fusions still beat the shipped R34 + flip, but by less (best 0.0503, whose telecom F1 only ties). They
also remove the mirrored pass that the reliability grade's disagreement term D comes from, and change the span
population from which the verdict's per-span false-alarm rate p0 was measured. Both would have to be re-calibrated
on validation, after some 30 configurations had already been compared on the same 22 photos.

### Decision

**The fusion is not shipped.** It passes the accuracy gate, but not the latency limit of §4.14.2 (the v2.10 target
of 3.5 s for the densest pole), on the only hardware available, an 8 GB M1. The single-pass variant fits the time
limit but would need two re-calibrations on a validation set that has already been used for this selection.
v2.12 therefore ships **v2.11's pipeline unchanged**, and the fusion is kept as an opt-in configuration
(`wirenet_weights: models/v212_ensemble.json`) for faster hardware. On a machine where 4.7 s per photo is
acceptable, or with a faster GPU, it is the configuration to try first; its p0 and grade calibration should then be
re-checked on validation.

## 4.14.5 Depth Anything V2 as a night-sky guard for YOLO vegetation (`depth_veg_guard.py`)

On the user's night pole P-D67F60, YOLO marks part of the black sky as Vegetation (score 0.27, 28,063 px). That
false instance creates "vegetation contact" hazards on telecom cables. **Depth Anything V2 Small** (Apache-2.0,
24.8 M parameters, run on the CPU so it does not compete with training) gives a relative depth map, and three
per-instance guards were tested:

- **G1 depth:** drop the instance if its median relative inverse depth is in the far (sky) range, below the photo's
  10th percentile + 0.05.
- **G2 photometric:** drop it if it is near-black and textureless, with mean L* < 15 and std L* < 4.
- **G3:** both G1 and G2.

| Guard | Validation instances removed | of which true vegetation | Vegetation AP50 (validation) | Night false instance removed? |
|---|---|---|---|---|
| none | 0 | 0 | 0.024 | no |
| G1 depth | 16 | **2** | **0.0025** | yes |
| G2 photometric | 0 | 0 | 0.024 | no (std L* 5.17) |
| G3 both | 0 | 0 | 0.024 | no |

G1 removes the night false positive but also real trees: on daytime photos, foliage against the sky is often
"far" in relative depth, and relative depth has no absolute scale to separate a tree 20 m away from the sky. G2
and G3 are safe on validation but miss the night case, whose sky is lit unevenly by street lamps.

A simpler rule, dropping any vegetation instance with mean L* < 15, was checked for safety on validation under
three conditions (`v212_veg_darkness_val.json`):

| Condition | Vegetation instances | Mean L* < 15 | of which true vegetation |
|---|---|---|---|
| clean | 127 | 7 | 0 |
| low light | 31 | 22 | **1** |
| synthetic night | 66 | 13 | 0 |

It removes a true tree in low light, and 22 of 31 low-light instances fall below the cut, so it is a darkness
detector, not a sky detector. **No guard is adopted.** The night false positive stays a documented limitation; the
real fix is labelled night vegetation (Figure `v212_night_veg_guard.jpg`).

## 4.14.6 Depth and perspective in the sag measurement (`depth_sag.py`, `v212_depth_sag_val.json`)

The 666 validation spans scored with Chapter III's CRS (cached v2.8 probabilities, shipped constants) were paired
with Depth Anything V2's relative depth at the two span ends; **recession** = |depth(end A) − depth(end B)|.

- Recession and sag ratio are barely correlated overall: Spearman ρ = 0.063, p = 0.10.
- But the share of spans in the **high** CRS band rises with recession:

| Recession tercile (222 spans each) | High-band share | Median δ/L |
|---|---|---|
| low | 3.2% (7) | 0.0015 |
| middle | 7.2% (16) | 0.0017 |
| high | **10.4% (23)** | 0.0023 |

Low vs high tercile: Fisher's exact p = 0.0039; χ² across the three terciles p = 0.011.

**Reading.** Spans that run away from the camera read as high sag about 3.3× more often. This is consistent with
perspective: a straight wire receding in depth projects as a curve, which the image-space catenary reads as sag.
This matches the v2.9 audit, in which all 24 "service drops" were pole-to-pole spans seen in perspective (v2.9
§4.11.2a). **No correction is adopted.** Relative depth has no metric scale, there is no ground-truth sag to
calibrate against, and any re-weighting chosen here would be a new, unvalidated constant on top of the placeholder
δ_lim. The finding goes to the open problems: a perspective-aware sag needs metric depth, or two views, plus field
ground truth.

## 4.14.7 Test split, evaluated once (`v212_spans_test.jsonl`)

v2.12 was frozen before the test split was touched: it ships v2.11's pipeline (WireNet-R34 + flip TTA + dual
polarity, YOLO vegetation, unchanged constants). The 21 test photos were then run once, under the same three
conditions as v2.11, with the full screener pipeline:

| Condition | Strict / tolerant mAP50 | Power tol. AP50 / R | Telecom tol. AP50 / R | vs v2.11 |
|---|---|---|---|---|
| clean | 0.0204 / 0.0469 | 0.105 / 0.225 | 0.004 / 0.050 | identical |
| low light | 0.0053 / 0.0222 | 0.065 / 0.170 | 0.003 / 0.033 | identical |
| night (synthetic) | 0.0091 / 0.0313 | 0.088 / 0.217 | 0.001 / 0.025 | identical |

Every metric field of every row equals v2.11's `v211_spans_test.jsonl`; only the run time differs. The fusion, the
single backbones and the vegetation guards were **never run on the test split**, so the test split carries no
information about them and remains usable for a future v2.13 that ships one of them.


## 4.14.8 Memory and stability on the 8 GB M1

- Two training processes were never run at the same time. ConvNeXt-Tiny started only after MiT-B1 had exited (a
  waiter on its PID).
- Depth Anything ran on the CPU, during training.
- Validation probability caching for each new model ran after its training, one model at a time.
- The live server was stopped during training and restarted cleanly at the end.

Measured: peak resident memory 1.31 GB while caching through the three-model fusion and 1.49 GB in the full
pipeline, with 68–70% of system memory free and no swapping during these runs. Only the batch-2 fused forward pass
(11.7 s) showed memory pressure; nothing in the shipped path uses it.

## 4.14.9 Limitations

- Three backbones, one small dataset (102 training photos): the comparison says which backbone helps **here**, not
  in general.
- Batch 2 for the new backbones vs batch 4 for R34 is a confound imposed by memory.
- ConvNeXt-Tiny was trained for 20 epochs, not 40.
- Depth Anything gives relative, not metric, depth.

## 4.14.10 Reproduction

```bash
python -m ml.wirenet train --encoder mit_b1 --batch 2 --epochs 40 --out models/v212_mitb1.pt
python -m ml.wirenet train --encoder tu-convnext_tiny --batch 2 --epochs 20 --out models/v212_convnext_t.pt
python -m ml.experiments.cache --split val --wirenet models/v212_mitb1.pt --tag=-mitb1
python -m ml.experiments.cache --split val --wirenet models/v212_convnext_t.pt --tag=-cnx
python -m ml.experiments.ensemble_eval --caches r34=runs/cache/val-v28 mitb1=runs/cache/val-mitb1 cnx=runs/cache/val-cnx --out docs/thesis/results/v212_ensemble_val.jsonl
python -m ml.experiments.depth_veg_guard --out docs/thesis/results/v212_veg_guard_val.json --night runs/ui_inputs/P-D67F60_night.jpg
python -m ml.experiments.depth_sag --out docs/thesis/results/v212_depth_sag_val.json
python -m ml.experiments.cache --split val --wirenet models/v212_ensemble.json --tag=-ens     # in-app equivalence
python -m ml.experiments.ensemble_bootstrap --caches r34=runs/cache/val-v28 mitb1=runs/cache/val-mitb1 cnx=runs/cache/val-cnx --out docs/thesis/results/v212_ensemble_bootstrap_val.json
for m in r34:models/wirenet-r34.pt mitb1:models/v212_mitb1.pt cnx:models/v212_convnext_t.pt; do
  python -m ml.experiments.cache --split val --wirenet ${m#*:} --tta none --tag=-${m%%:*}-notta; done
python -m ml.experiments.ensemble_eval --caches r34=runs/cache/val-r34-notta mitb1=runs/cache/val-mitb1-notta cnx=runs/cache/val-cnx-notta --out docs/thesis/results/v212_ensemble3_notta_val.jsonl
python -m ml.experiments.latency_profile --reps 2 --wirenet models/wirenet-r34.pt --images <22 val stems> 145634
python -m ml.experiments.latency_profile --reps 2 --wirenet models/v212_ensemble.json --images <22 val stems> 145634
python -m ml.experiments.pipeline_golden --check docs/thesis/results/v210_pipeline_golden_v29_val.json
python -m scripts.stress_test_all_conditions --split test --out docs/thesis/results/v212_spans_test.jsonl --conditions clean lowlight night --configs v2.12
```

The two-model fusion results are in `v212_ensemble2_val.jsonl` (R34 + MiT-B1, run before ConvNeXt finished;
identical to the corresponding rows of `v212_ensemble3_val.jsonl`). `v212_ensemble_val.jsonl` holds the R34
baseline row written while the evaluator was being set up.
