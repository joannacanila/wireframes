# Machine learning models

The model set is frozen. No model was trained or changed for the 45873c3 release. Parameter counts, recipes and
licences come from `docs/thesis/WIREFRAME_THESIS_REFERENCE.md` §5 and the `model-weights` branch README. Metrics come
from the named files in `docs/thesis/results/`. No other figures are claimed.

## Models loaded by the released configuration

| Model | File (under `models/`) | Bytes | Params | Input | Role | Config key |
|---|---|---|---|---|---|---|
| WireNet-R34: U-Net, ResNet-34 encoder | `wirenet-r34.pt` | 97,918,915 | 24.4 M | 1024² | Ensemble member | `segmentation.wirenet_weights` → `v217_ensemble.json` |
| WireNet-MiT-B1: U-Net, Mix Transformer B1 | `v212_mitb1.pt` | 65,824,959 | 16.4 M | 1024² | Ensemble member | 〃 |
| WireNet-ConvNeXt-T: U-Net, ConvNeXt-Tiny | `v212_convnext_t.pt` (2 parts) | 127,821,839 | 31.9 M | 1024² | Ensemble member | 〃 |
| Ensemble recipe: equal-weight probability fusion | `v217_ensemble.json` (tracked) | — | — | — | Lists the three members, weight 1 each | 〃 |
| YOLO11s-seg, fine-tuned. The name `yolo11m` is historical | `wireframe-yolo11m-seg.pt` | 20,660,928 | 10.1 M | 1024 | Vegetation masks and coarse wire proposals | `segmentation.weights` |
| YOLO11n pole detector, fine-tuned | `poles-yolo11n-v218.pt` | 5,487,450 | 2.6 M | 640 | Pole boxes, context only | `segmentation.pole_weights` |
| DINOv2-small, frozen, unmodified copy of `facebook/dinov2-small` | `hf/dinov2-small/` | 88,249,960 (weights) | 22.1 M | 1008² | Features for the strand head | — |
| Strand type head: 2-layer MLP (56 → 32 → 1), P(telecom) | `strand_head_v217.pt` + `.json` statistics | 9,975 / 279,817 | — | 24 hand features + 32 PCA dims of DINOv2 | Power vs telecom per strand | `segmentation.strand_head` |
| Strand-head feature bounds | `strand_head_v217.bounds.json` (tracked) | — | — | — | Clip range per hand feature, bound to checkpoint SHA-256 `eac91d84…a6be9d`, from 4,515 fitting strands | `strand_head_clamp` |
| UperNet-ConvNeXt-T, ADE20K. Unmodified copy of `openmmlab/upernet-convnext-tiny`; not trained by the team | `hf/upernet-convnext-tiny/` (weights in 3 parts) | 241,053,050 (weights) | 60.2 M | 384 px | Scene gates | `ground_gate`, `structure_gate`, `vegetation_gate` |

The WireNet output classes are background, `abandoned-wire`, `power-distribution-lines`, `telecom-cable` and
`vegetation`.

## Provisioning (`app/core/weights.py`)

* `REQUIRED` lists 12 files, each with its exact size and SHA-256. Files over GitHub's 100 MB limit are published as
  `<name>.000`, `.001`, …, joined on download, and checked on the joined file.
* On startup each missing file is downloaded in a background thread from
  `https://raw.githubusercontent.com/joannacanila/wireframes/model-weights` (override:
  `WIREFRAME_WEIGHTS_BASE_URL`). It is written to `<name>.part`, verified, then renamed atomically.
  `WIREFRAME_AUTO_DOWNLOAD=0` disables downloading.
* Existing files are never overwritten, even when they differ, because a locally retrained model is the operator's
  choice. Until all are present the screener runs in validation mode.
* The release audit (19 Nov 2026) verified all 12 files byte-exactly on the GitHub branch (`1de7082`), in
  `~/WIREFRAME/models` and in `~/WIREFRAME-s13/models`.
* The admin page `GET /api/admin/models` lists the installed models, and `POST /api/admin/models/active` switches
  between installed models (admin role).

## Training (historical record; not repeated)

| Model | Recipe (thesis reference §5.2) | Code |
|---|---|---|
| WireNet members | 102 training photos at 1024 letterbox, 512² random crops (80 % centred on a wire). Loss: class-weighted CE + soft Dice + clDice. AdamW, one-cycle schedule, Albumentations blur and contrast. No vertical flip or rotation. R34: 40 epochs (best 29), 2.63 h on MPS. MiT-B1: 40 (best 32), 4.66 h. ConvNeXt-T: 20 (best 16), 3.39 h | `ml/wirenet.py`, `ml/wirenet_stage2.py`, `ml/wirenet_v217.py` |
| YOLO11s-seg | 1024 px, batch 1 with gradient accumulation to 64, best at epoch 22. YOLO11m-seg ran out of memory on the 8 GB M1 | `ml/train.py` |
| Strand type head | Stacked on validation plus train strands. A strand is a training case only when ≥ 50 % of its skeleton lies on a labelled power or telecom wire. Validation figures are leave-one-photo-out | `ml/strand_classifier.py` |
| Pole detector | YOLO11n from the Street View (Zenodo 20091961) checkpoint, then LVIS `telephone_pole`. 50 epochs, 5° rotation augmentation, threshold 0.25 chosen on validation | `ml/poles/` |

Training runs and checkpoints are kept outside Git under `~/WIREFRAME/runs/` (`runs/wireframe`, `runs/wirenet`,
`runs/detect`, `runs/v21*`). They are preserved as provenance.

**Abandoned wire** cannot be learned from 27 instances. Every attempt (oversampling, copy-paste, focal loss, logit
adjustment) raised recall only by labelling ordinary wires as abandoned, at a precision of at most 0.04.

## Reported performance (evidence files)

| Measure | Value | Source |
|---|---|---|
| Strand type accuracy, held-out test (square) | 0.9358; power F1 0.9165, telecom F1 0.9479, macro F1 0.9322 (639 strands matched) | `results/s15_model_metrics_test.json`, "shipped (v2.17 head), square test" |
| Strand type accuracy, held-out test (field-shaped) | 0.919 | same file, "shipped, field-shaped test" |
| Centerline detection F1, test (square) | any wire 0.807; power 0.8277; telecom 0.7098; abandoned 0 recall | same file |
| Fusion gain, validation centerline F1 | telecom 0.719 → 0.753, power 0.844 → 0.875, any wire 0.825 → 0.849 | `config/wireframe.yaml` comment citing `results/v217_strand_head_stack_val_f3t04_tw1.0.json` |
| Pole detector, held-out LVIS test (all poles, threshold 0.2) | AP50 0.386, P 0.505, R 0.386, F1 0.437 (197 images, 516 boxes) | `results/s18_poles_poles_v218_test_baseline.json` |
| Pole detector, held-out LVIS test (medium + large poles) | P 0.620, F1 0.564 | `config/wireframe.yaml` comment (`segmentation.pole_weights`) |
| Sag-ratio 90 % band coverage, held-out test | 0.889 (± 1 px alone: 0.689) | `results/s16_uncertainty_calibration.json` |

There are no pole labels on WIREFRAME photos. The pole detector is context only and never creates or joins a wire
(`pole_occluder_gap: 0`).

## Licences

* **WireNets and the strand head:** trained on the team's Wireframes-Prelabelled-Datasets (CC BY 4.0). ResNet-34
  comes from torchvision (BSD-3); the MiT and ConvNeXt encoders come through segmentation-models-pytorch (MIT) and
  timm (Apache-2.0).
* **Vegetation and pole detectors:** fine-tuned from Ultralytics YOLO11, so they are AGPL-3.0.
  * Pole training data: LVIS v1 (CC BY 4.0) on COCO 2017 images (individual Creative Commons licences, several
    non-commercial), and Zenodo 20091961 (CC BY 4.0, Google Street View captures).
  * **Academic, non-commercial use only.**
* **DINOv2-small:** Apache-2.0. **UperNet-ConvNeXt-T:** MIT.
