# Next Session Prompt (copy everything below into Claude Code)

---

You are continuing **WIREFRAME**, an existing, working thesis software project. Do not restart completed work.

**Repository:** https://github.com/joannacanila/wireframes.git, branch `main`, local clone at
`/Users/joannacanila/WIREFRAME` (macOS, Apple M1 8 GB; use `.venv/bin/python`, `.venv/bin/ruff`).
Expected HEAD: the S5 commit "fix: restore diagnostic output for measured, unassessed wires (S5)" (or a later
commit listed in `docs/handoff/SESSION_LOG.md`). S5 was committed locally; check whether it was pushed.

## Read first (in this order) and verify against the repository
1. `docs/handoff/CURRENT_PROJECT_STATE.md`
2. `docs/thesis/RESEARCH_AND_DECISION_REGISTER.md` (D-01..D-23, "S4 threshold resolution", "S5 research update")
3. `docs/thesis/IMPLEMENTATION_CHANGELOG.md`, `docs/handoff/SESSION_LOG.md`
4. `docs/thesis/WIREFRAME_THESIS_REFERENCE.md` sections 7.4-7.8, 8 (incl. 8.6 diagnostics), 10, 11, 13, 14.5, 15,
   16, 17, 18.3

## Commands to run first
```bash
cd /Users/joannacanila/WIREFRAME
git fetch origin && git status --short && git log -5 --oneline && git rev-parse HEAD origin/main
.venv/bin/python -m pytest -p no:cacheprovider -o addopts="" -q   # expect 817 passed on recovery/s9-ux (pyproject adds -q; with
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
- Documentation (owner's rule, S5): `docs/thesis/` is the authoritative, living technical record. Update the
  reference, `RESEARCH_AND_DECISION_REGISTER.md`, `IMPLEMENTATION_CHANGELOG.md` and `results/` whenever
  implementation, decisions, research, behaviour, validation or specifications change; update existing documents,
  no duplicates. Never edit the owner's manuscript chapters (`*.docx`), figures or academic references. Internal
  records are not published evidence. `docs/handoff/` is for session continuity only (CURRENT_PROJECT_STATE,
  SESSION_LOG, NEXT_SESSION_PROMPT). No em dashes in new text.
- Ask the owner only for things only they have.
- E2E on an isolated copy: `sqlite3 "file:var/wireframe.db?mode=ro" ".backup '<scratch>/wireframe.db'"`, copy
  `var/uploads`, `var/renders`, `var/reports`; create an admin with `scripts.create_admin.upsert_admin` against
  `WIREFRAME_DATA_DIR=<scratch>` (the CLI prompts for a password); run
  `WIREFRAME_DATA_DIR=<scratch> .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8011`, then
  `WIREFRAME_E2E_USER=... WIREFRAME_E2E_PASSWORD=... .venv/bin/python -m scripts.e2e_check --base
  http://127.0.0.1:8011 --photo <photo> --out <scratch>/e2e_out` (S5: 24 checks). Playwright uses the system
  Chrome (`channel="chrome"`); the admin login API needs the Origin header (CSRF), so use the script or a browser.

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
- **S5 (diagnostic restoration, register D-22, D-23):** S4 had hidden every measurement (renderers drew and selected
  only assessed wires: 0 wires drawn, PDF "No wire was flagged", retake banner on every analysis, withdrawn levels
  still counted in admin/assistant). Fixed with `app/services/diagnostics.py` (per-wire and per-photo diagnosis, no
  thresholds) used by page, viewer, admin, PDF and assistant; reference Section 8.6. Evidence
  `docs/thesis/results/s5_diagnostic_restoration.json`. Tests 776; E2E 24/24. Docs moved: register and changelog
  now in `docs/thesis/`. Research: DSOAR s.1.7.12 "Poles, Sag of Wires" lead (S16), text NOT read (TLS error).

## Outstanding tasks (priority order)
S8 status: port 8000 runs `recovery/s8-colour-meter` from `/Users/joannacanila/WIREFRAME-meter` on the live data
(started with WIREFRAME_DATA_DIR=/Users/joannacanila/WIREFRAME/var). It contains S6 and S7. Merge it (not the S6 or S7
branches) into `main` only with the owner's authorisation. Expected: 809 passed, E2E 49 checks. To change the
reference, edit `comparison_reference` in config/wireframe.yaml and restart.
S7 status: the owner's chosen page (v2.23 layout with the three-zone sag meter, D-25) is on branch
`recovery/s7-sag-meter` (worktree `/Users/joannacanila/WIREFRAME-meter`), pushed, NOT merged. It supersedes the S6
v2.18 page; merge `recovery/s7-sag-meter` (it contains all of S6) after the owner's review, not `recovery/s6-restore`.
Expected: 794 passed, E2E 29 checks.
S6 status: the recovery is on branch `recovery/s6-restore` (worktree `/Users/joannacanila/WIREFRAME-recovery`),
pushed to origin, NOT merged into `main`. First: ask the owner to review the restored page (port 8011 isolated or
after merge), then merge `recovery/s6-restore` into `main` with a normal merge (no force), restart port 8000, and
re-run the E2E against it. Expected: 788 passed, E2E 41 checks. Backup task 1 below is DONE (`scripts/backup.py`).
0. **Confirm with the owner:** restart of the port 8000 server so it serves the S5 API (`diagnosis` field), and push
   of the S5 commit. Then verify the live page at `http://127.0.0.1:8000/workspace` in a browser.
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
- Official DSOAR text (ERC Res. 01 s.2006 and amendments) to quote Section 1.7.12 "Poles, Sag of Wires" verbatim
  (D-04, S16); until read, the thesis must not call the 3 % rule obsolete.
- Optional: JMC No. 01 s.2021 / DILG MC 2021-135 PDF (official sites refused automated download).
- For any future telecom assessment: cable model and the manufacturer's sag-tension table, span lengths, and field
  sag measurements for the physical validation protocol (reference 7.8).
- An editable full manuscript (.docx).

## Known limitations (do not hide)
No sag severity and no pole-level screening verdict (by evidence, S4); image-derived ratio is not physical sag (no
camera pose, no field validation); telecom test centerline F1 0.710 (precision CI [0.665, 0.768], 21 photos); strict
mask mAP50 0.0199; abandoned wires not detected; SQLite single worker; backup covers the DB only (until task 1).

## Before ending your session
Update `docs/thesis/` (reference, register, changelog, results) and the three `docs/handoff/` files, commit only
intended files, push (no force), verify `git rev-parse HEAD
origin/main` match, restart the port 8000 server if you stopped it, and print a complete continuation prompt if work
remains. Start the handoff when about 25 % of context remains.

---
