# Repository guide

Repository: `https://github.com/joannacanila/wireframes` (GitHub). The canonical release is `main` at
`45873c341e4d00aff02c4bc5b65c70d2da9f2c9e`. The documentation commits that follow it change only `docs/engineering/`.

## Directory layout (tracked)

| Path | Contents |
|---|---|
| `app/main.py` | FastAPI factory `create_app`, health and config endpoints, page routes |
| `app/api/` | HTTP routers (see [API_REFERENCE.md](API_REFERENCE.md)) |
| `app/core/` | `config.py` (settings + typed analysis config), `database.py` (engine, pragmas, additive migration), `security.py`, `state.py` (AppState, model loading), `weights.py` (model download manifest), `notice.py` |
| `app/pipeline/` | 24 modules of image analysis (see [COMPUTER_VISION_PIPELINE.md](COMPUTER_VISION_PIPELINE.md)) |
| `app/services/` | 25 modules: ingestion, persistence, serialisation, comparison, screening, review flow, PDF reports, queue, audit, barangay lookup |
| `app/assistant/` | Assistant service, intent routing, knowledge base (`knowledge.md`, `citizen.md`) |
| `app/models/orm.py` | SQLAlchemy models (15 tables) |
| `app/data/barangays_cavite.json` | Official barangay names (location assignment) |
| `app/web/` | `landing.html`, `field.html`, `admin.html`, and `assets/` (ES modules, CSS, fonts) |
| `config/wireframe.yaml` | Every analysis parameter, threshold and reference (424 lines) |
| `models/` | Only small tracked files: `v217_ensemble.json`, `strand_head_v217.bounds.json`, `.gitkeep`. The weights themselves are downloaded |
| `ml/` | Training, curation, calibration and evaluation code. `ml/experiments/` holds research scripts (`python -m ml.experiments.<name>`). `ml/poles/` holds the pole detector |
| `scripts/` | Operator tools: `backup.py`, `create_admin.py`, `e2e_check.py`, `server_check.py`, `batch_inspect.py`, `live_upload_check.py`, `repeatability.py`, `fix_location_data.py`, `stress_test_all_conditions.py` |
| `tests/` | 64 pytest modules plus `fixtures/` |
| `docs/thesis/` | Thesis reference, decision register, changelog, session reports, `results/` evidence, `figures/` |
| `docs/handoff/` | Session-continuity notes |
| `docs/engineering/` | This documentation |
| `requirements.txt`, `-dev`, `-ml` | Runtime, development and training dependencies |
| `.env.example` | Deployment settings template (no secrets) |

## Not in Git (by design, `.gitignore`)

| Item | Where it comes from |
|---|---|
| `models/*.pt`, `models/hf/` | Downloaded from the `model-weights` branch on first start |
| `var/` (database, uploads, renders, reports, backups) | Created at runtime in `WIREFRAME_DATA_DIR` |
| `datasets/` | Training and evaluation data. Needed only for retraining and evaluation, never at runtime |
| `runs/` | Training runs, checkpoints, inference caches |
| `.venv/`, caches, `*.log`, `.env` | Local |

## Branches and refs (state after the 19 Nov 2026 audit)

| Ref | Commit | Where | Purpose |
|---|---|---|---|
| `main` | `45873c3` | local + GitHub | **Canonical application** |
| `model-weights` | `1de7082` | local + GitHub | Model files only, with no shared history with `main`. The app downloads from `raw.githubusercontent.com/joannacanila/wireframes/model-weights`. **Never delete, rewrite or merge** |
| `recovery/s9-ux` | `25bb9b5` | local + GitHub | Archive. Holds one commit not in `main` (the S9 result-page restructure, later superseded by the S11 restoration) |
| other `recovery/*` branches | see table below | local and/or GitHub | Archive of session states. Every commit is contained in `main` |
| tag `s13-baseline-s12` | `02bd470` | local only | S13 starting point |
| `refs/recovery/s14-start` … `s18b-refinement` (15 refs) | various | local only | Snapshots of intermediate session states, kept for recovery |

### Permanent branch → commit table

Historical documents (`docs/handoff/SESSION_LOG.md`, `IMPLEMENTATION_CHANGELOG.md`, the register) refer to these
branch names. If a branch is ever removed, `git show <commit>` still resolves the reference.

| Branch | Full commit | Subject |
|---|---|---|
| `recovery/pre-recovery-main` | `a727bb943c4e3da20bdead45550cac1a3a9b3b12` | fix: restore diagnostic output for measured, unassessed wires (S5) |
| `recovery/s6-restore` | `f71a86bdeb14f8f87a95c3cd35af68fe2bb93e8f` | docs: S6 recovery record, 3 % research update, assumption register |
| `recovery/s7-sag-meter` | `a02fb97a3720399923b5adee3dd9f9e45a143740` | feat: v2.23 result page with the three-zone sag meter (S7) |
| `recovery/s8-colour-meter` | `52baa3c84ddbbfa864f7a64c66f655a43bb5f54f` | fix: comparison pill and reference status no longer clip (S8) |
| `recovery/s9-ux` | `25bb9b5ad68da3af3d440f7d032cf30167091136` | feat: restructure the result page and admin sheet (S9, D-27) |
| `recovery/meter-reference-0916fd2` | `0916fd29fada7df27f5211e99670c9f9131eeafc` | docs: verify ERB 95-21 status; add handoff package |
| `recovery/original-colour-meter-engineering-corrections` | `0bf4299c3ca12551b405b5ce38286dad3001952c` | fix: restore the S8 colourful-meter page (D-27) |
| `recovery/handoff-0916fd2-engineering` | `2a8abc1bcc7ec49900a4f9bdc6ed0b4ddc3d3fcd` | fix: keep meter key labels inside the wire card (S11) |
| `recovery/s12-results-ui`, `recovery/s13-master` | `02bd4707d6f9679b79e10fe84b1b1b9b930e2081` | feat: approved results page and explicit meter boundaries (S12, D-28) |
| `recovery/baseline-v218` | `f74250e84584fd8381e1ba8546d517470e93906d` | v2.18: every model from this repository |
| `backup/before-trailer-removal` (deleted 19 Nov 2026, local only) | `79386e2ac4d6296ffd2ad84f6299476e6c93445e` | six commits patch-identical to commits in `main`. Kept in the audit bundle |

## Rules for this repository

* Never force-push and never rewrite published history.
* Commit only on a branch made for the work, and fast-forward `main`.
* Do not commit model weights to `main`. Publish them on `model-weights`, with their size and SHA-256 added to
  `app/core/weights.py: REQUIRED`.
* Do not commit `var/`, `.env`, datasets or caches.
* Thesis manuscripts (`docs/thesis/*.docx`) and figures belong to the authors and are not edited by development work.

## Local layout on the development machine (19 Nov 2026)

| Path | Role |
|---|---|
| `~/WIREFRAME` | Main checkout on `main`. Holds the live data directory `var/`, `datasets/`, `runs/`, `.venv/` and the shared `.git` |
| `~/WIREFRAME-s13` | Primary working copy (a worktree, detached at `45873c3`) |
| `~/WIREFRAME-backups` | Database and upload backups (`scripts/backup.py`). Keep them |
| `~/WIREFRAME-audit-20261119` | Release-audit bundle of all refs, checksums and inventories (see [REPRODUCIBILITY_AND_RECOVERY.md](REPRODUCIBILITY_AND_RECOVERY.md)) |
| `~/WIREFRAME-final-recovery-20261119` | Archive of the working tree from before the release commit |
