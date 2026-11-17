# WIREFRAME

**A vision-based diagnostic framework for urban overhead wire infrastructure risk assessment.**

WIREFRAME screens one ground-level photograph of a utility pole. It finds the overhead wires, separates them into
individual strands, types each strand (power distribution line, telecom cable, abandoned wire), fits a catenary to
each strand and measures its sag ratio, checks vegetation contact and photo reliability, and returns a pole-level
verdict with a plain-language result page and a PDF report. Staff manage inspections, reviews, maps and dispatch
routes in an admin portal.

> WIREFRAME is a screening aid. Its results need field verification by qualified personnel before any maintenance or
> regulatory action. The sag reference values in `config/wireframe.yaml` are development placeholders until they are
> calibrated with a licensed engineer, and every result carries flags that say so.

**How sag is assessed.** Sag ratio = sag / chord length of the fitted catenary. With the configured 4.0 % telecom
reference: below 2.4 % *Within reference*, 2.4 % to under 4.0 % *Minor sag*, 4.0 % and above *Critical*. A wire is
*Not assessed* when an endpoint is outside the photo, its type is uncertain, or its class has no reference (power
distribution lines). A pole is INCONCLUSIVE rather than NOT AT RISK when fewer than half of its measured spans could
be assessed.

## Quick start: run WIREFRAME on a new computer (step by step)

Everything the system needs comes from this repository: the code from `main`, and **all models** (about 650 MB)
from its [`model-weights`](https://github.com/joannacanila/wireframes/tree/model-weights) branch, which the app
downloads by itself on first start. Nothing is fetched from Hugging Face or any other site.

**What you need:** macOS, Windows or Linux; Python **3.11–3.13** (`python3 --version`); Git; about **4 GB free
disk** (Python packages ≈ 2.5 GB, models ≈ 650 MB); 8 GB RAM or more; an internet connection for the first start.
Apple Silicon Macs use the GPU (MPS) automatically; other computers run on the CPU (slower, considerably slower).

**1. Get the code**

```bash
git clone https://github.com/joannacanila/wireframes.git
cd wireframes
```

**2. Create a Python environment and install the packages** (one time, 5–15 minutes)

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows (PowerShell): .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

**3. Start the system**

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**4. Wait for the models (first start only).** The terminal shows `downloading model weights …` and then
`… verified and installed` for each file. To check progress, open <http://127.0.0.1:8000/api/health> and look at
`model.downloads`: every file should say `ready` or `present`. This takes a few minutes, depending on the
connection. The server keeps running while it downloads, and switches from validation mode to screening by itself
when the files are in place (no restart). Each file's size and SHA-256 are checked, so a broken download is never
used; if one fails, stop the server (`Ctrl+C`) and start it again: it fetches only what is missing.

**5. Open the screener:** <http://127.0.0.1:8000/workspace> (landing page: <http://127.0.0.1:8000/>, admin:
`/admin`). Upload a ground-level photo of a utility pole. The first analysis is slower because the models load into
memory; later ones take about 6–9 s on an Apple M1 (Section 13 of the thesis reference).

**6. Optional: accounts for the admin portal** (password prompted; there is no default account):

```bash
python -m scripts.create_admin engr.yourname --name "Engr. Your Name"                 # administrator
python -m scripts.create_admin field.tech --name "J. Cruz" --role inspector          # inspector
```

Open `http://127.0.0.1:8000/admin` and sign in. **Inspectors** see the three working pages: **Dashboard** (what
needs attention), **Inspections** (every inspection, its findings, the professional review, PDF and CSV reports)
and **Map & Dispatch** (where the poles are and which to inspect first). **Administrators** use the same pages,
plus **Technical exports** on Inspections, **Technical diagnostics** on each inspection and record re-classification;
there is no separate System page. QA runs, synthetic samples and stock photos are kept as *test
records* and left out of the working pages; an administrator shows them with Inspections, Filters, Include test
records.

**Optional: the WIREFRAME Assistant with Claude.** The "Ask" button in the admin portal always answers from the
built-in WIREFRAME documentation (`app/assistant/knowledge.md`). To have Claude write the answers from that
documentation, set an Anthropic API key in the server's environment before step 3:

```bash
export ANTHROPIC_API_KEY=...            # Windows: $env:ANTHROPIC_API_KEY="..."
```

With a key, each question sends the question, the matching documentation and, on an inspection page, an
anonymised summary of that inspection (counts and statuses; no photo, pole ID, name, place or coordinates) to the
Anthropic API (model `claude-opus-5-5`). `WIREFRAME_ASSISTANT_SHARE_CONTEXT=0` keeps the inspection summary on the
server; `WIREFRAME_ASSISTANT_PROVIDER=local` never calls an outside service. If the service is unreachable the
assistant falls back to the documentation. It cannot change any record.

**Next time:** `cd wireframes`, `source .venv/bin/activate` (Windows: `.venv\Scripts\Activate.ps1`), then step 3.
**Update to the latest version:** `git pull`, `pip install -r requirements.txt`, then step 3 (new models download
by themselves).

**Models it uses** (all listed with size and SHA-256 in `app/core/weights.py`, downloaded into `models/`):

| Model | File(s) | Used for |
|---|---|---|
| Three WireNets (U-Net with ResNet-34, MiT-B1, ConvNeXt-T encoders), averaged | `wirenet-r34.pt`, `v212_mitb1.pt`, `v212_convnext_t.pt` + `models/v217_ensemble.json` | Wire pixels and wire type |
| Strand type head (DINOv2 features + wire geometry) | `strand_head_v217.pt`, `strand_head_v217.json`, `hf/dinov2-small/` | One type (power / telecom) per wire |
| YOLO11s-seg | `wireframe-yolo11m-seg.pt` | Vegetation |
| UperNet-ConvNeXt-T (ADE20K) | `hf/upernet-convnext-tiny/` | Ground / structure / vegetation scene checks |
| YOLO11n pole detector | `poles-yolo11n-v218.pt` | Pole boxes, information only (does not change wires or risk scores) |

**Troubleshooting**

* `uvicorn: command not found`: the environment is not active; run the `source .venv/bin/activate` line again.
* Port already in use: start with `--port 8001` and open `http://127.0.0.1:8001/workspace`.
* `CERTIFICATE_VERIFY_FAILED` while downloading (python.org Python on macOS): run
  `/Applications/Python\ 3.13/Install\ Certificates.command` once, then restart.
* No internet on the inspection computer: on a computer that has it, copy the whole `models/` folder after the
  first start; on the offline one, put it in `wireframes/models/` and start with `WIREFRAME_AUTO_DOWNLOAD=0`.
* A mirror instead of GitHub: `WIREFRAME_WEIGHTS_BASE_URL=<url with the same file names>`.
* Pole detection off: set `pole_weights: null` in `config/wireframe.yaml` and restart.

## Documentation

Everything about the system (architecture, data, models, equations, assessment rules, API, complexity, runtime,
evaluation, testing, security, limitations, reproduction and references) is in one document:
[`docs/thesis/WIREFRAME_THESIS_REFERENCE.md`](docs/thesis/WIREFRAME_THESIS_REFERENCE.md). Raw results are in
`docs/thesis/results/`, figures in `docs/thesis/figures/`.

**Models it uses** (all listed with size and SHA-256 in `app/core/weights.py`, downloaded into `models/`):

| Model | File(s) | Used for |
|---|---|---|
| Three WireNets (U-Net with ResNet-34, MiT-B1, ConvNeXt-T encoders), averaged | `wirenet-r34.pt`, `v212_mitb1.pt`, `v212_convnext_t.pt` + `models/v217_ensemble.json` | Wire pixels |
| Strand type head (DINOv2-small features + strand geometry) | `strand_head_v217.pt`, `strand_head_v217.json`, `hf/dinov2-small/` | One type (power or telecom) per strand |
| YOLO11s-seg | `wireframe-yolo11m-seg.pt` (file name kept for compatibility) | Vegetation |
| UperNet-ConvNeXt-T (ADE20K) | `hf/upernet-convnext-tiny/` | Ground, structure-edge and vegetation scene checks |
| YOLO11n pole detector | `poles-yolo11n-v218.pt` | Pole boxes, context only (never changes wires or scores) |

**Accuracy** (held-out test, 21 photos; centerline F1, thesis reference Section 14): all wires 0.807, power
distribution lines 0.828, telecom 0.710; wire-type accuracy 0.936. Abandoned wire is not detected. The pole detector
reaches F1 0.564 on public LVIS photos and has not been measured on the team's photos; treat pole boxes as hints.

**Speed** (Apple M1, 8 GB, `docs/thesis/results/benchmark_v223.json`): median 6.4 s per 1024 px photo, 8.8 s on
field photos, up to 17 s on dense or dark photos; PDF about 0.06 s.

If the model files are missing and cannot be downloaded, the server runs in **reference-mask mode**: an upload must
include its YOLO-seg polygon labels, and the geometric and risk layers run on those masks. An upload without labels
is refused with `model_unavailable` before anything is stored. The pipeline never invents detections.

**Licences of the models:** WireNets and the strand head are trained on the team's dataset (CC BY 4.0) with
BSD-3 / MIT / Apache-2.0 components; the vegetation and pole detectors are fine-tuned from Ultralytics YOLO11
(AGPL-3.0); DINOv2-small (Apache-2.0) and UperNet-ConvNeXt-T (MIT) are unmodified copies. The pole detector was
trained on LVIS / COCO photos (several non-commercial Flickr licences) starting from a model trained on Google
Street View captures (Zenodo 20091961): academic, non-commercial use only. Details: the `model-weights` branch
README.

## Configuration

| Setting | Where |
|---|---|
| Analysis parameters (thresholds, references, gates, weights) | `config/wireframe.yaml` |
| Deployment (data directory, upload limit, cookies, logging, downloads, assistant) | environment variables `WIREFRAME_*`, see `.env.example` |

Copy `.env.example` to `.env` to change deployment settings. Never commit `.env`. Behind HTTPS set
`WIREFRAME_COOKIE_SECURE=1`.

## Development

```bash
pip install -r requirements-dev.txt                # tests and lint
python -m pytest                                   # 717 tests, no model weights needed
ruff check app tests scripts ml
```

End-to-end browser check of a running **test** server (use a separate data directory, not production data; the
uploaded photo is marked as a test record):

```bash
pip install playwright && python -m playwright install chrome
WIREFRAME_DATA_DIR=/tmp/wf-test uvicorn app.main:app --port 8001 &
python -m scripts.create_admin qa.admin            # on the same WIREFRAME_DATA_DIR
WIREFRAME_E2E_USER=qa.admin WIREFRAME_E2E_PASSWORD=... python -m scripts.e2e_check --base http://127.0.0.1:8001 --photo path/to/pole.jpg
```

Other tools:

| Command | Purpose |
|---|---|
| `python -m scripts.create_admin <username> [--role inspector]` | Create an account (password prompted) |
| `python -m scripts.batch_inspect <folder or video> --out runs/audit/<day>` | Batch street audit |
| `python -m scripts.server_check --out runs/server_check.json` | API and PDF check with the real models |
| `python -m scripts.live_upload_check --base http://127.0.0.1:8000` | Upload check against a running server |
| `python -m ml.benchmark --images ... --out docs/thesis/results/benchmark_v223.json` | Runtime benchmark |
| `python -m ml.evaluate geometry` | Geometry recovery on synthetic catenaries |

## Training and evaluation (research)

```bash
pip install -r requirements-ml.txt
export ROBOFLOW_API_KEY=...                              # never stored in the repository
python -m ml.curate --download                           # fetch, audit, deduplicate, split, letterbox
python -m ml.wirenet train --data datasets/wireframe_seg/data.yaml --epochs 40
python -m ml.train --data datasets/wireframe_seg/data.yaml --batch 1 --epochs 60
python -m ml.strand_classifier fit
python -m ml.experiments.v217_eval dump --split test --variant v221 && python -m ml.experiments.v217_eval score --split test --variant v221
```

The released models are frozen; retraining is not needed to run or reproduce the reported results. `ml/experiments/`
holds the scripts behind each measured design decision; their outputs are in `docs/thesis/results/`.
