# Configuration and deployment

Configuration has two layers:

* **Deployment settings:** environment variables, or a `.env` file that is never committed. Template:
  `.env.example`.
* **Analysis parameters:** `config/wireframe.yaml`, typed and validated by `app/core/config.py`. Any change there
  changes results and is governed by the decision register.

## Environment variables

`Settings` in `app/core/config.py` (pydantic-settings, prefix `WIREFRAME_`):

| Variable | Type | Default | Effect |
|---|---|---|---|
| `WIREFRAME_DATA_DIR` | path | `<repo>/var` | Database, uploads, renders, reports, backups |
| `WIREFRAME_CONFIG_PATH` | path | `<repo>/config/wireframe.yaml` | Analysis configuration file |
| `WIREFRAME_DATABASE_URL` | str | unset (SQLite in the data dir) | SQLAlchemy URL. Migrations run only on SQLite |
| `WIREFRAME_MAX_UPLOAD_MB` | int | 64 | Upload size limit (413 above it) |
| `WIREFRAME_PUBLIC_UPLOADS_PER_WINDOW` | int | 20 | Anonymous uploads per client per 15 minutes |
| `WIREFRAME_COOKIE_SECURE` | bool | false | Set `true` behind HTTPS (Secure session cookie) |
| `WIREFRAME_LOG_LEVEL` | str | INFO | Level of the `wireframe` and `app.*` loggers |
| `WIREFRAME_START_WORKER` | bool | true | Start the analysis worker (tests set false) |
| `WIREFRAME_ASSISTANT_PROVIDER` | str | auto | `auto`: Claude when an API key is present; `local`: never calls an outside service |
| `WIREFRAME_ASSISTANT_MODEL` | str | `claude-opus-5-5` | Model used by the assistant |
| `WIREFRAME_ASSISTANT_SHARE_CONTEXT` | bool | true | Send the anonymised inspection summary with a question |
| `WIREFRAME_ASSISTANT_PER_MINUTE` | int | 12 | Assistant rate limit |

Read directly with `os.environ`:

| Variable | Where | Effect |
|---|---|---|
| `WIREFRAME_AUTO_DOWNLOAD` | `app/core/weights.py` | `0`, `false`, `no` or `off` disables model downloads (offline installs) |
| `WIREFRAME_WEIGHTS_BASE_URL` | `app/core/weights.py` | Mirror with the same file names |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` | `app/assistant/service.py` | Enables Claude answers in the assistant. **Never commit a value** |
| `WIREFRAME_E2E_USER`, `WIREFRAME_E2E_PASSWORD` | `scripts/e2e_check.py` | Test account for the browser check |
| `WIREFRAME_YUNET` | `ml/curate.py` | YuNet ONNX model for the privacy screen (curation only) |
| `ROBOFLOW_API_KEY` | `ml/curate.py` | Dataset download (research only) |
| `DINO_DEVICE` | `ml/strand_classifier.py` | Device for DINOv2 features (research only) |

## Analysis configuration (`config/wireframe.yaml`)

The file has `version: 1` and 424 lines. Every section cites its manuscript section or decision.
`GET /api/config` returns it with a `fingerprint`, which is stored with results as provenance.

| Section | Governs | Document |
|---|---|---|
| `acquisition` | Capture envelope (distance, elevation, daylight, minimum size) | [Pipeline](COMPUTER_VISION_PIPELINE.md) |
| `preprocessing`, `enhancement` | Letterbox, VNI smoothing, conditional enhancement | 〃 |
| `vni` | Equations 1–4 and calibration q01 / q99 | 〃 |
| `segmentation` | Model files, thresholds, span assembly, gates, strand head, poles | 〃 and [Models](MACHINE_LEARNING_MODELS.md) |
| `centerline`, `catenary` | Algorithms 4–5, kink split | [Sag](SAG_MEASUREMENT_AND_ASSESSMENT.md) |
| `crs`, `engineering` | CRS machinery: inactive (`delta_lim0: {}`, `conductors: {}`) | 〃 |
| `comparison_reference`, `telecom_reference` | Power 2 % / 3 % and telecom 2 % / 5 % classifications | 〃 |
| `measurement_uncertainty` | Calibrated 90 % band | 〃 |
| `vegetation`, `hazard`, `quality`, `guardrail` | Contact, pole verdict, reliability grade, retake prompt | 〃 |
| `mspi` | Multi-pole priority weights (1/3 each) | [Pipeline](COMPUTER_VISION_PIPELINE.md) |

Settings that administrators change at runtime live in the database (`app_settings`), not in this file: the risk
rubric, the decision options and the active model. Each change is audited.

## Install and start

These steps follow `README.md`, "Quick start".

1. You need Python 3.11–3.13, Git, about 4 GB of free disk, 8 GB of RAM, and internet access for the first start.
   Apple Silicon uses MPS automatically; other machines run on the CPU.
2. Install:

   ```
   git clone https://github.com/joannacanila/wireframes.git
   cd wireframes
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Start the server with `uvicorn app.main:app --host 127.0.0.1 --port 8000`.
4. On the first start the models download (about 650 MB) and are verified. Follow the progress at `/api/health`
   under `model.downloads`. No restart is needed.
5. Create accounts. There is no default account:

   ```
   python -m scripts.create_admin <username> --name "<Full name>"
   python -m scripts.create_admin <username> --name "<Full name>" --role inspector
   ```

6. Open `/workspace` (Field Screener) or `/admin` (portal).

Extras: `requirements-dev.txt` (pytest, ruff, playwright, pypdfium2) and `requirements-ml.txt` (training).

## Deployment constraints

* **Single process.** Run one uvicorn worker. The analysis queue, model inference and the login and upload throttles
  are per process (`app/services/queue.py`, `app/core/security.py`).
* **HTTPS:** terminate TLS at a reverse proxy and set `WIREFRAME_COOKIE_SECURE=true`. The proxy must preserve the
  `Host` header, because the Origin check compares the request's Origin or Referer with it.
* **Offline:** copy `models/` from a machine that has downloaded the files, then set `WIREFRAME_AUTO_DOWNLOAD=0`.
* **Memory:** one model instance at a time. The ensemble members run one after another (`_probs_lowmem`), which fits
  in 8 GB.
* **Backups:** `python -m scripts.backup create --dest <dir>` while the server runs. See
  [DATABASE_AND_DATA_ENGINEERING.md](DATABASE_AND_DATA_ENGINEERING.md).

## Operator scripts (`scripts/`)

| Script | Purpose |
|---|---|
| `create_admin.py` | Create an account, with the password prompted (minimum 12 characters) |
| `backup.py` | Create, verify or restore a backup |
| `server_check.py` | Health check of a running server |
| `e2e_check.py` | Browser end-to-end check with Playwright |
| `batch_inspect.py`, `live_upload_check.py` | Batch and live upload checks |
| `repeatability.py` | Test-retest of results on the same photo |
| `stress_test_all_conditions.py` | Runs across degradation conditions |
| `fix_location_data.py` | v2.21 barangay correction. Dry run by default; `--apply` backs up first |
