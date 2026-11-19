# Reproducibility and recovery

## 1. Reproduce the released application

1. Install it from a fresh clone (see [CONFIGURATION_AND_DEPLOYMENT.md](CONFIGURATION_AND_DEPLOYMENT.md)):

   ```
   git clone https://github.com/joannacanila/wireframes.git && cd wireframes
   git checkout 45873c341e4d00aff02c4bc5b65c70d2da9f2c9e     # or a later docs-only commit on main
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r docs/engineering/validated_environment.txt  # exact validated versions (or requirements.txt)
   uvicorn app.main:app --host 127.0.0.1 --port 8000          # downloads and verifies the 12 model files
   ```

2. Verify the models. `GET /api/health`, under `model.downloads`, should report every file as `ready` or `present`.
   Each file was checked against its size and SHA-256 in `app/core/weights.py: REQUIRED` before it was installed.
3. Verify the parameters. `GET /api/config` → `fingerprint` must be `42bb4ddc7d89`. Every analysis stores the
   fingerprint it ran with (`analyses.config_fingerprint`).
4. Run the tests: `python -m pytest -q`. The expected result is 1,095 passed and 2 xfailed. The models are not
   needed, because the tests use synthetic scenes and reference annotations.

### Sources of non-determinism

* Device: MPS on Apple Silicon, CPU elsewhere. Floating-point results can differ slightly between devices.
* Unpinned dependency ranges, when `requirements.txt` is used instead of `validated_environment.txt`.
* `enhancement.mode: auto` runs a step only when its measured trigger fires, but that decision is deterministic for
  the same image.
* Test-retest on the same photo: `scripts/repeatability.py`. The mirrored-pass false-alarm rate is in
  `docs/thesis/results/v29_span_geometry_summary_all_val.json` (D-10).

## 2. Reproduce research results

The research code is in `ml/` and `ml/experiments/` (run with `python -m ml.experiments.<name>`). It needs the
datasets, which are not in Git (`ml/sources.yaml`, `ml/curate.py`), and `requirements-ml.txt`. Each evidence file
in `docs/thesis/results/` is named after the script that wrote it. Many scripts read inference caches under
`runs/cache/`, which `python -m ml.experiments.cache` regenerates in hours of inference. The models are frozen, and
retraining is not part of the release.

## 3. Verify a checkout against the release

```
git -C <checkout> diff --stat 45873c3 -- app config models tests scripts ml requirements*.txt pyproject.toml
```

An empty diff means the application is the release. The audit also keeps a per-file SHA-256 list of all 576
canonical files (`~/WIREFRAME-audit-20261119/canonical_45873c3.sha256`, made from the commit's blobs).

To check a working tree file by file:

```
cd <checkout> && shasum -a 256 -c ~/WIREFRAME-audit-20261119/canonical_45873c3.sha256 --quiet
```

## 4. Recover data

| Situation | Action |
|---|---|
| A migration went wrong at startup | Stop the server. The pre-migration copy is in `<data_dir>/backups/` (written by `backup_before_migration`). Copy it back over `wireframe.db` |
| Restore a full backup | `python -m scripts.backup verify <backup>`, then `python -m scripts.backup restore <backup> --to <new empty dir>`, then start with `WIREFRAME_DATA_DIR=<new dir>` |
| An inspection was "deleted" in the portal | It was archived. Restore it with `POST /api/admin/inspections/{id}/restore` (admin role) |
| A model file is corrupt or missing | Delete the file and restart. Only missing files are downloaded, and every one is verified |
| No internet | Copy `models/` from another machine, then set `WIREFRAME_AUTO_DOWNLOAD=0` |

## 5. Recover Git state

The release audit (19 Nov 2026) wrote a bundle of every ref in the shared repository before any cleanup:

* `~/WIREFRAME-audit-20261119/wireframes-all-refs.bundle` (656 MB). It holds 51 refs: all branches, the tag,
  the remote-tracking refs and the 15 `refs/recovery/*`. `git bundle verify` was OK.
* `git_refs_and_worktrees.txt` and `worktree_status.txt`: every ref, worktree and status before the cleanup.
* `canonical_45873c3.sha256`: SHA-256 of all 576 canonical files.
* `model_inventory_WIREFRAME.tsv`: 26 local model files with size and SHA-256.
* `pip_freeze_validated.txt`: the validated environment.

```
# restore one ref
git fetch ~/WIREFRAME-audit-20261119/wireframes-all-refs.bundle 'refs/heads/<name>:refs/heads/<name>'
# full copy of the repository with every ref
git clone ~/WIREFRAME-audit-20261119/wireframes-all-refs.bundle wireframes-restored
```

GitHub also holds `main`, `model-weights` and eight `recovery/*` branches. The historical branch names and their
commits are listed in [REPOSITORY_GUIDE.md](REPOSITORY_GUIDE.md#permanent-branch--commit-table).

## 6. Cleanup performed on 19 Nov 2026 (approved by the project owner)

| Action | Detail | Recovery |
|---|---|---|
| Primary worktree `~/WIREFRAME-s13` | HEAD moved from `recovery/s13-master` (`02bd470`) to `45873c3`, detached; no file changed | the bundle |
| Local `model-weights` | fast-forwarded from `bae543d` to `1de7082` (GitHub's) | the bundle |
| Branch `backup/before-trailer-removal` | deleted (local only; 6 commits patch-identical to `main`) | the bundle; `79386e2` |
| Nine obsolete worktrees | `WIREFRAME-github`, `-handoff`, `-meter`, `-recovery`, `-s12`, `-tidy`, `-ux`, plus two scratch worktrees, removed with `git worktree remove`. Their branches are kept. The only unique file, `WIREFRAME-github/var` (an empty test database), was copied to `~/WIREFRAME-audit-20261119/preserved/` first | branches and the copy |
| Regenerable files | 29 cache directories (`__pycache__`, `.pytest_cache`, `.ruff_cache`), 10 `.DS_Store`, and `runs/cache/_fused` and `_tube` (temporary directories of the ensemble and enhancement experiments): 2,219,809,675 bytes. The list is in `removed_tierA.txt` | regenerated automatically or by the scripts |

Datasets, model weights, training runs, inference caches, databases, backups and thesis files were not touched.
