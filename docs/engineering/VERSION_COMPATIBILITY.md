# Version compatibility

## Version identifiers in the release (45873c3)

| Identifier | Value | Where | Meaning |
|---|---|---|---|
| Git commit | `45873c341e4d00aff02c4bc5b65c70d2da9f2c9e` | `main` | **The release identity.** Use it in every citation |
| Session label | S18 (with the follow-up that removed the technical view) | docs, register D-39 | Development session that produced the release |
| FastAPI `version` | `2.23` | `app/main.py` | Last numbered app version. Not bumped after S4 |
| `pyproject.toml` version | `2.0.0` | `pyproject.toml` | Never maintained; does not describe the release |
| Config file version | `1` | `config/wireframe.yaml: version` | Schema of the YAML file |
| Config fingerprint | `42bb4ddc7d89` | `AnalysisConfig.fingerprint`: first 12 hex chars of the SHA-256 of the typed config | Stored with every analysis (`config_fingerprint`). Identifies the exact parameters |
| Assessment basis | `S4-2026-11-18` | `app/services/basis.py: BASIS` | Stored in `metadata_json.assessment_basis`. Records without it get the historical display rules (D-21, D-23) |
| Power reference | `PWR-2026.11-S17` | `comparison_reference.version` | Returned with every comparison (`reference_version`) |
| Telecom reference | `TEL-2026.11-S17` | `telecom_reference.version` | 〃 |
| Risk rubric | `version: 1` (default) | `app/services/screening.py`, then `app_settings["risk_rubric"]` | Returned with every risk (`rubric_version`) |
| Span-fit algorithm | `catenary+kink-split/1` | `app/pipeline/span_fit.py: ALGORITHM` | Recorded with every measurement |
| Model set | v2.18 manifest | `app/core/weights.py: REQUIRED` and `model-weights@1de7082` | 12 files with exact size and SHA-256 |
| Ensemble recipe | v2.17 | `models/v217_ensemble.json` | Equal-weight R34 + MiT-B1 + ConvNeXt-T |
| Strand head | v217 | `strand_head_v217.pt` + `.json` + `.bounds.json` (bounds bound to the checkpoint SHA-256) | |
| Pole detector | v218 | `poles-yolo11n-v218.pt` | |
| Database schema | none (additive) | `app/core/database.py: migrate` | Compatibility is by column presence. See below |

**Finding (not fixed):** the three app version numbers (`2.0.0`, `2.23`, and the S-labels) disagree. Use the
commit hash and the config fingerprint to identify a build or a result.

## Compatibility matrix

| Component A | Component B | Compatible? | Basis |
|---|---|---|---|
| App 45873c3 | `model-weights@1de7082` (v2.18) | **Yes**: all 12 files verified by size and SHA-256 (audit, 19 Nov 2026) | `weights.py: REQUIRED` |
| App 45873c3 | `model-weights@bae543d` (v2.15) | **No**: it lacks 10 of the 12 required files | branch history |
| App 45873c3 | Locally retrained weights under the same names | Loaded (files present are never overwritten), but results are not comparable | `weights.py` docstring |
| `strand_head_v217.bounds.json` | `strand_head_v217.pt` | Only that checkpoint (SHA-256 `eac91d84…a6be9d`) | `bounds.json: checkpoint_sha256` |
| App 45873c3 | Databases from v2.9 … S17 | **Yes**: upgraded in place at startup after an automatic backup | `migrate` |
| Databases written by 45873c3 | Older app versions | **Not supported**: old code does not know the S18 tables or columns | no down-migration |
| Stored results from before S4 | Current display | Shown with withdrawn levels and an INCONCLUSIVE verdict; never reprocessed | D-21, D-23 |
| Stored results from S4 to S17 | Current references | Compared on read with the reference configured now | `comparison.py` |
| Frontend assets | Backend | Same commit only. The client renders server-computed fields (`r.screening`, `w.finding`) | `result-view.js` |

## Python and packages

`requirements.txt` declares the ranges. The release was validated with the versions below, in the development
environment: macOS 26.0.1, Apple M1, 8 GB, Node v24.11.1.

| Package | Declared | Validated |
|---|---|---|
| Python | ≥ 3.11 (README: 3.11–3.13) | 3.13.2 |
| fastapi | ≥ 0.115, < 1 | 0.162.4 |
| starlette | (via fastapi) | 1.10.0 |
| uvicorn[standard] | ≥ 0.32 | 0.54.0 |
| pydantic / pydantic-settings | ≥ 2.9, < 3 / ≥ 2.6, < 3 | 2.14.3 / 2.16.2 |
| sqlalchemy | ≥ 2.0.36, < 3 | 2.1.8 |
| numpy | ≥ 2.1, < 3 | 2.5.4 |
| scipy | ≥ 1.14 | 1.18.1 |
| opencv-python-headless | ≥ 4.10, < 6 | 5.0.0.93 |
| scikit-image | ≥ 0.24 | 0.26.0 |
| torch / torchvision | ≥ 2.4 / ≥ 0.19 | 2.15.0 / 0.29.2 |
| segmentation-models-pytorch | ≥ 0.3, < 1 | 0.6.0 |
| timm | (via smp) | 1.0.31 |
| transformers | ≥ 5.17, < 6 (D-12: CVE-2026-80047) | 5.17.0 |
| ultralytics | ≥ 8.3, < 9 | 8.4.210 |
| pillow | ≥ 11 | 13.0.0 |
| pi-heif | ≥ 1.0, < 2 | 1.4.0 |
| reportlab | ≥ 4.2 | 5.1.0 |
| pyyaml | ≥ 6 | 6.0.3 |
| httpx | ≥ 0.27 | 0.28.1 |
| python-multipart | ≥ 0.0.18 | 0.0.33 |
| certifi | ≥ 2024.2 | 2026.10.23 |
| anthropic | ≥ 1.17, < 2 (optional) | 1.17.0 |
| pytest / ruff / playwright / pypdfium2 (dev) | ≥ 8 / ≥ 0.6 / ≥ 1.45 / ≥ 4.30 | 9.2.0 / 0.17.4 / 1.65.0 / 5.15.2 |
| albumentations (ml) | ≥ 1.4.4, < 3 | 2.0.8 |

The ranges are not pinned, so a fresh install may resolve newer versions. To reproduce the validated environment
exactly, use the complete `pip freeze` of the validating environment (116 packages):
`pip install -r docs/engineering/validated_environment.txt`.

Known deprecation: Starlette warns that `HTTP_422_UNPROCESSABLE_ENTITY` is renamed (6 warnings in the test suite).
It has no functional effect with these versions.
