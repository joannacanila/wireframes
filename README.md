# WIREFRAME

**A Vision-Based Diagnostic Framework for Urban Overhead Wire Infrastructure Risk Assessment.**

WIREFRAME screens a ground-level photograph of a utility pole. It segments the scene into four classes (Vegetation,
abandoned-wire, power-distribution-lines, telecom-cable; telecom-cable includes fiber optic drops), flags wires that vegetation touches,
fits a catenary to each wire's centerline, scores its sag against an allowable limit (CRS, 0–10), measures
scene clutter with a 2D-FFT Visual Noise Index (VNI), and ranks poles for inspection by a Multi-Site Priority
Index (MSPI). Every algorithm follows Chapter III of the thesis manuscript, and the equation numbers in the
code refer to it.

> Outputs are computational screening results. They need review by a qualified professional before any
> regulatory or maintenance action (Section 3.14.6). Until calibration (Table 3.1, second part), the
> parameters in `config/wireframe.yaml` are provisional, and every result carries flags that say so.

## Quick start

```bash
git clone https://github.com/joannacanila/wireframes.git && cd wireframes
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt                       # everything the app needs, models included
uvicorn app.main:app                                  # http://127.0.0.1:8000 : / landing · /workspace screener · /admin
python -m scripts.create_admin engr.yourname          # optional: an admin account (password prompted; no default)
```

**The models download themselves.** The trained weights are not in the code history. On first start the app
fetches them (about 113 MB: `wirenet-r34.pt` and `wireframe-yolo11m-seg.pt`) from this repository's
[`model-weights`](https://github.com/joannacanila/wireframes/tree/model-weights) branch into `models/`, verifies
each file's size and SHA-256, and switches from validation mode to screening as soon as they are in place, without a
restart (progress: `GET /api/health` → `model.downloads`). The ground filter's scene parser (about 240 MB) is
downloaded from the Hugging Face Hub the first time a photo needs it. Existing files are never overwritten. Offline
install: copy the two files into `models/` by hand and set `WIREFRAME_AUTO_DOWNLOAD=0`; another mirror:
`WIREFRAME_WEIGHTS_BASE_URL`.

Development (tests, lint, training, experiments):

```bash
pip install -r requirements-dev.txt -r requirements-ml.txt
pytest                                                # 525 tests
ruff check app ml scripts tests
python -m scripts.seed_synthetic --n 8                # optional: SYNTHETIC demo poles (codes SYN-…)
```

**Licence of the weights:** the vegetation model is fine-tuned from Ultralytics YOLO11 (AGPL-3.0); WireNet builds on
ResNet-34 (BSD-3) and segmentation-models-pytorch (MIT); the dataset is CC BY 4.0.

The trained segmentation model is not in the repository. Without `models/wireframe-yolo11m-seg.pt`, the
server runs in **reference-mask mode**. You upload a photo together with its YOLO-seg polygon labels, and the
geometric and risk layers run on those masks. An upload without labels ends in the `error` state with the code
`model_unavailable`. The pipeline never invents detections.

## Pipeline (Figure 3.4)

| Stage | Module | Thesis |
|---|---|---|
| EXIF reading, envelope check, GPS removal | `app/pipeline/acquisition.py` | §3.3.1, §3.14.4 |
| Letterboxed RGB [0,1] + grayscale 640², 5×5 Gaussian | `app/pipeline/preprocess.py` | Alg. 1 |
| Hann window → FFT → power → r > r_L share → q01/q99 → band | `app/pipeline/vni.py` | Alg. 2, Eq. 1–4, Tab. 3.7 |
| YOLO11-seg, per-class NMS IoU 0.45, mask → original | `app/pipeline/segment.py` | Alg. 3, Eq. 5 |
| Zhang-Suen, 8-graph, spur pruning (L_min 30), double BFS | `app/pipeline/skeleton.py` | Alg. 4 |
| LM catenary fit, chord L, perpendicular δ, δ/L, truncation | `app/pipeline/catenary.py` | Eq. 6–8 |
| Resultant load, change-of-state, δ_lim | `app/pipeline/sag_limits.py` | Eq. 9, Tab. 3.8 |
| CRS and Low / Medium / High | `app/pipeline/crs.py` | Eq. 10, Tab. 1.2 |
| MSPI ranking + weight-grid sensitivity | `app/pipeline/mspi.py` | Alg. 6, Eq. 11 |

## API

| Method | Path | |
|---|---|---|
| POST | `/api/analysis/upload` | multipart: `file`, optional `pole_code`, `barangay`, `span_m`, `temperature_c`, `wind_speed_mps`, `distance_m`, `elevation_deg`, `latitude`, `longitude`, `labels`. Returns **202** |
| GET | `/api/analysis/{id}` | status: `processing` · `complete` · `no_wires_detected` · `fit_failed` · `error` |
| GET | `/api/analysis/{id}/result` | masks (polygons), centerlines, catenary curves, fit parameters, VNI, CRS, flags, rank |
| GET | `/api/analysis/{id}/image` | stored photo (GPS removed) |
| DELETE | `/api/analysis/{id}` | cascades to detections and the report |
| GET | `/api/dashboard/map` | RFC 7946 GeoJSON `FeatureCollection` of poles |
| GET | `/api/dashboard/priority?alpha=&beta=&gamma=` | MSPI ranking |
| GET | `/api/dashboard/sensitivity` | Spearman ρ over the 66-triple weight grid |
| GET | `/api/reports/{id}/pdf` | printable screening report with the mandatory disclaimer and the mode badge |
| POST | `/api/analysis/{id}/retry` | re-run a failed analysis (e.g. once the model is installed) |
| GET | `/api/evaluation/metrics` | Table 3.10 targets and the latest `ml.evaluate` runs (Eq. 12–18) |
| GET | `/`, `/workspace`, `/admin` | landing page, open field screener, authenticated command centre |
| POST | `/api/auth/login` · `/api/auth/logout` | admin session (HttpOnly, SameSite=Strict cookie) |
| GET | `/api/admin/*` | overview, queue, models, poles and history, reviews, spectrum, CSV/PDF exports (admin only) |

Listing, retry, delete, `/api/dashboard/*` and `/api/admin/*` require an admin session. Upload, status, result, image and the per-analysis PDF are open to the field screener.

## Dataset and training (Phase 1, on this Apple Silicon Mac)

```bash
pip install -r requirements-ml.txt
# 1. fill workspace/project/version/licence for each source in ml/sources.yaml, download each into datasets/raw/<name>
export ROBOFLOW_API_KEY=...                                             # never stored in the repo
python -m ml.curate --download                                          # fetch -> audit -> dedupe -> 70:15:15
# 2. draw polygons for the images in annotation_queue.csv (Section 3.4.3), re-run curate
python -m ml.train --data datasets/wireframe_seg/data.yaml --epochs 30  # MPS: batch 4, no AMP; writes conf_min
                                                                        # (add --model yolo11s-seg.pt if memory runs out)
python -m ml.curate --raw datasets/raw --out datasets/wireframe_seg --imgsz 1024   # 1024 letterbox
python -m ml.train --data datasets/wireframe_seg/data.yaml --batch 1 --epochs 60    # span detector (MPS)
python -m ml.wirenet train --epochs 40           # full-resolution wire segmentation (models/wirenet-r34.pt)
python -m scripts.batch_inspect path/to/folder_or_video --out runs/audit/day1   # batch street audit + LabelMe review queue
python -m scripts.stress_test_all_conditions --split val --out runs/stress.jsonl  # 8-condition stress benchmark
python -m scripts.server_check --repeat 3 --out runs/server_check.json   # end-to-end API + PDF check, real models
python -m scripts.live_upload_check --base http://127.0.0.1:8000   # real-HTTP upload check of a RUNNING server
python -m scripts.ui_check --base http://127.0.0.1:8000            # headless-Chrome check of /workspace (Playwright)
python -m scripts.ui_check --mock --base http://127.0.0.1:8000     # v2.13: same check on mocked results, no model needed
ruff check .                                                            # project only (pyproject excludes .agents/, venvs, data)
python -m ml.calibrate_vni path/to/local_calibration_images --write    # q01, q99
python -m ml.evaluate segmentation --data datasets/wireframe_seg/data.yaml --split test   # once, final
python -m ml.evaluate geometry --scenes 40                              # synthetic benchmark of the geometric layer
```

Segmentation is a hybrid: YOLO11s-seg (1024 px; file `wireframe-yolo11m-seg.pt`) for Vegetation and WireNet, a full-resolution U-Net, for
the 2 px wires that YOLO's 1/4-resolution masks cannot represent. Each result carries an explainable hazard verdict (AT RISK - HIGH / MODERATE / NOT AT RISK, with
reasons) and a reliability grade (A / B / C / Retake recommended, with the cause in plain English) on top
of Chapter III's CRS. Since v2.9 the pole verdict counts a sag band only when more spans are in it than the
measured per-span test-retest false-alarm rate explains (a one-sided binomial test, `hazard.sag_rule`);
uncorroborated spans are listed as notes.

**Thesis documentation:** everything (system, data, models, method, every result table, version history, rejected
ideas, test-split integrity, limitations, reproduction and the evidence files) is in one document,
[`docs/thesis/WIREFRAME_THESIS_REFERENCE.md`](docs/thesis/WIREFRAME_THESIS_REFERENCE.md). Raw results are in
`docs/thesis/results/` and figures in `docs/thesis/figures/`.

All training, evaluation and latency figures were measured on one Apple Silicon M1 laptop with 8 GB of unified
memory (MPS). The v2.12 three-model fusion is available as an opt-in, slower configuration:
`segmentation.wirenet_weights: models/v212_ensemble.json` (needs `models/v212_mitb1.pt` and
`models/v212_convnext_t.pt`, which are not committed; about 4.7 s per photo on the M1).

Since v2.13 a pretrained ADE20K scene parser (UperNet-ConvNeXt-T, MIT licence, downloaded from the Hugging Face
Hub on first use) suppresses wire traces lying on the road or pavement (kerbs, manholes); they are returned as
`suppressed_spans` with the reason and never scored (`segmentation.ground_gate`). Unmeasured spans carry an
`action` and the result a photo-level `retake_guidance`. If the parser cannot load, every span is kept and the
result is flagged `ground_gate_unavailable`.

What each version changed, what was verified and what is still open is in Parts 6, 8, 10 and 12 of the thesis
reference.
