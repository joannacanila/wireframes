# WIREFRAME engineering documentation

These documents describe the system as released at commit `45873c341e4d00aff02c4bc5b65c70d2da9f2c9e` on `main`
(19 Nov 2026, "chore: finalize and clean repository"). The documentation was written after the release, and it
changes no application file, model, configuration or threshold. Every statement was checked against the code and
configuration of that commit, or against an evidence file in `docs/thesis/results/`. The source is named where a
number appears.

## How these documents relate to the existing records

| Record | Role | Status |
|---|---|---|
| `docs/engineering/` (this folder) | How the released system is built, run, verified and recovered | written for 45873c3 |
| `docs/thesis/WIREFRAME_THESIS_REFERENCE.md` | Detailed technical reference for the manuscript: equations, models, evaluation | **describes v2.23 + S4/S5 (18 Nov)**. Its sections on the sag comparison, review workflow and admin (S8–S18) are out of date. They are not rewritten here (see [Findings](#findings-recorded-not-fixed)) |
| `docs/thesis/RESEARCH_AND_DECISION_REGISTER.md` | Every engineering decision D-01 … D-39, with its sources | current |
| `docs/thesis/IMPLEMENTATION_CHANGELOG.md` | What changed in each session, why, and how it was verified | current |
| `docs/thesis/S16/S17/S18_*REPORT.md` | Session reports | historical, preserved |
| `docs/thesis/results/` | 184 evidence files (metrics, sweeps, evaluations) | historical, preserved |
| `docs/handoff/` | Session-continuity notes | historical |

Where the two disagree, the code and `config/wireframe.yaml` at 45873c3 are authoritative, then this folder, then
the register and changelog.

## Contents

| Document | Covers |
|---|---|
| [SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md) | Components, request and processing flow, process model, security model |
| [REPOSITORY_GUIDE.md](REPOSITORY_GUIDE.md) | Directory layout, branches and refs (with a permanent branch → commit table), worktrees, what is not in Git |
| [COMPUTER_VISION_PIPELINE.md](COMPUTER_VISION_PIPELINE.md) | Stage-by-stage image pipeline (`app/pipeline/`), flags and statuses |
| [MACHINE_LEARNING_MODELS.md](MACHINE_LEARNING_MODELS.md) | The nine model files, provenance, licences, reported metrics, provisioning |
| [SAG_MEASUREMENT_AND_ASSESSMENT.md](SAG_MEASUREMENT_AND_ASSESSMENT.md) | Sag ratio, uncertainty band, the power and telecom reference classifications, pole verdict, problem wires and risk |
| [DATABASE_AND_DATA_ENGINEERING.md](DATABASE_AND_DATA_ENGINEERING.md) | 15 tables (generated from the ORM), migrations, files on disk, backups, datasets |
| [API_REFERENCE.md](API_REFERENCE.md) | All 64 HTTP routes with their access level |
| [FRONTEND_ENGINEERING.md](FRONTEND_ENGINEERING.md) | Pages, ES modules, cache-busting, accessibility conventions |
| [CONFIGURATION_AND_DEPLOYMENT.md](CONFIGURATION_AND_DEPLOYMENT.md) | Environment variables, `config/wireframe.yaml`, install, start, operations |
| [TESTING_AND_VALIDATION.md](TESTING_AND_VALIDATION.md) | Test suite, lint, end-to-end check, release validation results |
| [ENGINEERING_DECISIONS.md](ENGINEERING_DECISIONS.md) | Index of decisions D-01 … D-39, grouped by subject |
| [VERSION_COMPATIBILITY.md](VERSION_COMPATIBILITY.md) | Version identifiers and compatibility matrix (app, models, config, schema, Python packages) |
| [REPRODUCIBILITY_AND_RECOVERY.md](REPRODUCIBILITY_AND_RECOVERY.md) | Fresh install, checksum verification, data and Git recovery |
| [REQUIREMENT_TRACEABILITY.md](REQUIREMENT_TRACEABILITY.md) | Chapter III sections and algorithms → modules → configuration → tests → evidence |

## Findings recorded, not fixed

The release audit (19 Nov 2026) recorded these items. None was changed, because the release is frozen.

1. `WIREFRAME_THESIS_REFERENCE.md` describes the system as of S5 (18 Nov). For example, §8.2 says every new analysis
   is INCONCLUSIVE with no sag comparison. Since S17 (D-37), however, power-line spans are classified against the 3 %
   reference, and telecom spans against the 2 % / 5 % range. [SAG_MEASUREMENT_AND_ASSESSMENT.md](SAG_MEASUREMENT_AND_ASSESSMENT.md)
   describes the current behaviour.
2. Version identifiers disagree: `pyproject.toml` says `2.0.0`, FastAPI's `version` in `app/main.py` says `2.23`, and
   the release is identified by session (S18) and commit. See [VERSION_COMPATIBILITY.md](VERSION_COMPATIBILITY.md).
3. The vegetation model file is called `wireframe-yolo11m-seg.pt`, but it holds a fine-tuned **YOLO11s-seg**. The
   model-weights README and the thesis reference both say the name is historical.
4. `config/wireframe.yaml` keeps the hazard / CRS machinery (`crs.levels`, `hazard.sag_rule`, `p0_*`) inactive while no
   validated reference exists (D-10). It is dormant code, not dead code.
5. `ml/experiments/` contains about 36 research scripts that nothing in the application imports. They produced the
   files in `docs/thesis/results/` and are kept as provenance.
6. The test suite emits 6 `StarletteDeprecationWarning`s (`HTTP_422_UNPROCESSABLE_ENTITY` → `…_CONTENT`). This is
   harmless with the pinned versions.
7. The local data directory of the main checkout has not yet received the S18 additive columns. They are added, with
   an automatic backup first, on the next server start from 45873c3.
