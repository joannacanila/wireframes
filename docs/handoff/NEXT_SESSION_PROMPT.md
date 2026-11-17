# Next Session Prompt (copy everything below into Claude Code)

---

You are continuing **WIREFRAME**, an existing, working thesis software project. Do not restart completed work.

**Repository:** https://github.com/joannacanila/wireframes.git, branch `main`, local clone at
`/Users/joannacanila/WIREFRAME` (macOS, Apple M1 8 GB; use `.venv/bin/python`, `.venv/bin/ruff`).
Expected HEAD: the S4 commit "fix: remove unsupported sag, vegetation and conductor thresholds (S4)" (or a later
handoff commit listed in `docs/handoff/SESSION_LOG.md`).

## Read first (in this order) and verify against the repository
1. `docs/handoff/CURRENT_PROJECT_STATE.md`
2. `docs/handoff/RESEARCH_AND_DECISION_REGISTER.md` (D-01..D-21 and the "S4 threshold resolution" section)
3. `docs/handoff/IMPLEMENTATION_CHANGELOG.md`, `docs/handoff/SESSION_LOG.md`
4. `docs/thesis/WIREFRAME_THESIS_REFERENCE.md` sections 7.4-7.8, 8, 13, 14.5, 16, 17, 18.3

## Commands to run first
```bash
cd /Users/joannacanila/WIREFRAME
git fetch origin && git status --short && git log -5 --oneline && git rev-parse HEAD origin/main
.venv/bin/python -m pytest -p no:cacheprovider -o addopts="" -q   # expect 754 passed (pyproject adds -q; with
                                                                   # the default -q -q the count line is hidden)
.venv/bin/ruff check app tests scripts ml                          # expect clean
lsof -iTCP:8000 -sTCP:LISTEN                                       # owner's server; check before stopping it
```

## Owner's standing rules (must follow)
- Evidence before implementation: no threshold, formula or classification without a verifiable applicable source or
  documented empirical validation. No placeholders, invented values, fabricated results or citations. If a value
  cannot be supported, remove it from the decision logic (NOT ASSESSED / INCONCLUSIVE), do not just reword it.
- Chain: source -> requirement -> applicability -> interpretation -> decision -> code -> boundary tests -> reference
  doc -> thesis. Update the register for every decision.
- Models are frozen (no retraining, no weight or threshold changes to improve metrics). Never reprocess historical
  inspections or modify `var/wireframe.db` except through the app.
- Git: never force-push, `reset --hard` or `clean`; never commit `var/`, `.env`, `models/*.pt`, `models/hf/`,
  `datasets/`, `runs/`, `docs/thesis/*.docx`, or the owner's untracked figures in `docs/thesis/figures/`.
  **No Co-Authored-By or Claude/AI attribution lines in commits or PRs.**
- Documentation: the thesis reference file plus the five `docs/handoff/` files only. No em dashes in new text.
- Ask the owner only for things only they have.
- E2E on an isolated copy: `sqlite3 "file:var/wireframe.db?mode=ro" ".backup '<scratch>/wireframe.db'"`, copy
  `var/uploads`, `var/renders`, `var/reports`; create an admin with `scripts.create_admin.upsert_admin` against
  `WIREFRAME_DATA_DIR=<scratch>` (the CLI prompts for a password); run
  `WIREFRAME_DATA_DIR=<scratch> .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8011`, then
  `WIREFRAME_E2E_USER=... WIREFRAME_E2E_PASSWORD=... .venv/bin/python -m scripts.e2e_check --base
  http://127.0.0.1:8011 --photo <photo> --out <scratch>/e2e_out` (S4: 20 checks).

## What is already done (do not repeat)
- S1-S3: release audit, transformers 5.17.0 (CERT/CC VU#456290), abandoned wires not assessed, ERB 95-21 3 % found
  superseded (DSOAR, 17 Feb 2006).
- **S4 (threshold resolution):** research recorded in the register. No applicable source gives a universal sag
  limit: ITU-T L.102 (11/2025) (full text read; tensile ratings LS = 1.5 W max 2700 N, LL = 30 % LS, cl. 7.2.2.1;
  sag is a design input, cl. 7.3.10, 8.2.6; no sag ratio); Europacable 2023 (sag typically 1-1.3 % of span as an
  installation target; 1.3 % -> 2.7 % under load A2 for one correct installation); FOA (< 2 %, no standard cited);
  Hengtong (2-4 % "normal" final sag); no Philippine instrument found with a telecom sag ratio. Implemented: no
  class has a reference (`crs.delta_lim0: {}`), bands removed, precision gate off (resolution reported), conductor
  data removed, vegetation is an image observation, NOT AT RISK needs every span assessed, every analysis with
  measured wires is INCONCLUSIVE ("Assessment incomplete"), pre-S4 results shown without withdrawn severities
  (`app/services/basis.py`), owner's disclaimer on page and every PDF, admin label "Assessment incomplete"
  (key `unassessed`). Evaluation variant `s4`: segmentation metrics identical; verdicts all INCONCLUSIVE.
  Tests 754; E2E 20/20.

## Outstanding tasks (priority order)
1. **Backup and restore (register D-13).** Implement a backup command (e.g. `python -m scripts.backup create|verify|
   restore`): SQLite online backup of `var/wireframe.db` plus `var/uploads`, `var/renders`, `var/reports`, a
   SHA-256 manifest, atomic creation (write to a temp dir, fsync, rename), integrity validation, explicit failure on
   any partial step, a documented retention policy only if an operational basis is given (otherwise keep all and
   say so), restore command. Isolated restore test (pytest, tmp dirs only): integrity_check, foreign_key_check, row
   counts, file counts, file hashes, app start on the restored dir, a historical inspection and its image load, PDF
   regenerates. No external scheduler. Document in reference section 16 and the register.
2. **Accessibility review** of the page and admin (keyboard, focus, labels, contrast, table semantics, error
   announcements, form validation, modals, responsive). Fix concrete defects with tests; never claim WCAG
   compliance.
3. **Benchmark** under transformers 5.17: stop the port 8000 server only for the run, then
   `.venv/bin/python -m ml.benchmark --images datasets/wireframe_seg/images/test/*.jpg <field photos from var/uploads>
   --reps 1 --field-reps 3 --out docs/thesis/results/benchmark_v223b.json`; compare with `benchmark_v223.json`
   (cold load, first analysis, dataset median/P95, field median/max, PDF, memory); restart the server
   (`.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000`). Report only measured differences.
4. **Thesis revision copies.** Inspect `docs/thesis/Chapter4_5_v218.docx`, `Chapter5.docx` and the newest PDF
   (`~/Downloads/[CANILA_VILLAMAR_TUBIANOSA] BCS45 (1).pdf`). Write revised copies only (e.g.
   `docs/thesis/revised/Chapter4_5_S4_revised.docx`, untracked) using reference section 18.3: remove the CRS
   severity bands, AT RISK / NOT AT RISK results, Eq. 9 conductor data and pixel vegetation rules as findings;
   describe the image-derived ratio as a measurement; report S4 coverage (0 % assessed by design) and verdicts;
   keep RRL/RRS and formatting. Ask the owner for an editable full manuscript; do not reconstruct it.
5. Keep the register current; if the owner supplies the current PEC Part 2 sag article, evaluate it under the same
   rules (applicability, measurement compatibility, physical validation) before any implementation.

## Owner-supplied items still needed
- Current Philippine Electrical Code Part 2 sag provision (edition, article, exact text) (D-04).
- Optional: JMC No. 01 s.2021 / DILG MC 2021-135 PDF (official sites refused automated download).
- For any future telecom assessment: cable model and the manufacturer's sag-tension table, span lengths, and field
  sag measurements for the physical validation protocol (reference 7.8).
- An editable full manuscript (.docx).

## Known limitations (do not hide)
No sag severity and no pole-level screening verdict (by evidence, S4); image-derived ratio is not physical sag (no
camera pose, no field validation); telecom test centerline F1 0.710 (precision CI [0.665, 0.768], 21 photos); strict
mask mAP50 0.0199; abandoned wires not detected; SQLite single worker; backup covers the DB only (until task 1).

## Before ending your session
Update all five `docs/handoff/` files, commit only intended files, push (no force), verify `git rev-parse HEAD
origin/main` match, restart the port 8000 server if you stopped it, and print a complete continuation prompt if work
remains. Start the handoff when about 25 % of context remains.

---
