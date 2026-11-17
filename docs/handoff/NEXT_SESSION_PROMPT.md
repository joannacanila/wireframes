# Next Session Prompt (copy everything below into Claude Code)

---

You are continuing **WIREFRAME**, an existing, working project. Do not restart completed work.

**Repository:** https://github.com/joannacanila/wireframes.git, branch `main`, local clone at
`/Users/joannacanila/WIREFRAME` (macOS, Apple M1 8 GB, use `.venv`: `.venv/bin/python`, `.venv/bin/ruff`).
Expected HEAD: the last commit listed in `docs/handoff/SESSION_LOG.md` (session S3). Version v2.23.

## Read first (in this order)
1. `docs/handoff/CURRENT_PROJECT_STATE.md`
2. `docs/handoff/RESEARCH_AND_DECISION_REGISTER.md` (decisions D-01..D-18 and their source status)
3. `docs/handoff/IMPLEMENTATION_CHANGELOG.md`, `docs/handoff/SESSION_LOG.md`
4. `docs/thesis/WIREFRAME_THESIS_REFERENCE.md` sections 7.5, 7.8, 8, 14, 16, 17, 18.3

## Commands to run first
```bash
cd /Users/joannacanila/WIREFRAME
git fetch origin && git status --short && git log -5 --oneline && git rev-parse HEAD origin/main
.venv/bin/python -m pytest -q -p no:cacheprovider        # expect 719 passed
.venv/bin/ruff check app tests scripts ml                 # expect clean
```

## Owner's standing rules (must follow)
- No unsupported implementation: every threshold, formula and rule needs a verifiable source (primary preferred) or
  reproducible empirical validation. No placeholders, arbitrary defaults, invented values or fabricated results.
  If research cannot support a value, implement an honest state (Not assessed / INCONCLUSIVE / remove the feature).
- Research -> decision -> code -> test -> technical reference -> thesis. Update the register for every decision.
- Do not ask the owner for decisions research can settle. Ask only for things only they have (e.g., a paid code book,
  an editable manuscript, field measurements).
- Models are frozen: no retraining, no weight changes, no threshold changes to improve metrics.
- Git: never force-push, never `reset --hard` / `clean`, never discard user work. Never commit `var/`, `.env`,
  `models/*.pt`, `models/hf/`, `datasets/`, `runs/`, `docs/thesis/*.docx` or the owner's untracked figures.
  **No "Co-Authored-By" or Claude attribution lines in commits or PRs.**
- Thesis documentation lives in one file (`docs/thesis/WIREFRAME_THESIS_REFERENCE.md`); the `docs/handoff/` files are
  the only other docs (owner-requested). No em dashes in new system-facing text.
- Do not reprocess historical inspections; do not modify `var/wireframe.db` except through the app. Use the
  verification copy approach (copy `var/` to the session scratchpad, run a server on another port) for E2E.
- E2E: `WIREFRAME_E2E_USER=... WIREFRAME_E2E_PASSWORD=... .venv/bin/python -m scripts.e2e_check --base
  http://127.0.0.1:<port> --photo <photo>` against a test data directory with its own admin
  (`python -m scripts.create_admin`).
- Restart the owner's server on port 8000 at the end: `.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000`.

## What is already done (do not repeat)
- S1: release audit (endpoints, dead code, docs rewrite, benchmark `docs/thesis/results/benchmark_v223.json`).
- S2: transformers 5.17.0 (CERT/CC VU#456290; evaluation identical; pip-audit clean); abandoned wires not assessed;
  INCONCLUSIVE shown as "Assessment incomplete" with reason; viewer zero-size fix; isolated DB restore test passed and
  automated; coverage verified (val 256/629 = 40.70 %, test 320/658 = 48.63 %); metric definitions; validation
  score file re-scored (stale copy in `results/superseded/`).
- S3: primary-source research on the 3 % power-line rule (register D-04). Result: ERB Resolution No. 95-21 s.19
  (3 % of the distance between poles; scope electric power utilities, s.2) was **superseded by the DSOAR effective
  17 Feb 2006** (ERC Resolution No. 02 s.2010 recital,
  https://elibrary.judiciary.gov.ph/thebookshelf/showdocs/10/49331). No current in-force 3 % source verified (PEC
  Part 2 not openly published; DSOAR annex and Philippine Distribution Code text not obtained). Decision: power lines
  stay Not assessed. Do not re-research this unless you can obtain the current PEC Part 2 article or another in-force
  instrument; if the owner provides it, implement per the plan in "Task 2b".

## Outstanding tasks (priority order)
1. **Telecom sag reference (register D-02, D-03).** The telecom 4.0 % reference and the 60 % Minor band (2.4 %) have
   no source. Research applicable sources: NTC memorandum circulars on aerial/outside-plant cable installation and
   pole attachment; ITU-T L-series recommendations on aerial optical cables (installation, sag/tension); Telcordia
   outside-plant generic requirements; Philippine utility pole-attachment standards (e.g., distribution utility joint-use
   rules); US NESC (only as the fallback named by the superseded ERB 95-21 s.10, so weigh carefully); peer-reviewed
   work on aerial telecom cable sag. Record each source in the register (title, issuer, date, URL/DOI, section,
   access date, finding, applicability). Decide:
   - If a verifiable, applicable source gives a sag criterion compatible with an image-derived ratio: implement it for
     telecom with boundary tests (below / at / above), update reasons, page, PDF, assistant knowledge, reference 7.5,
     register, then re-run the release evaluation (`python -m ml.experiments.v217_eval dump/score --variant v221`
     writes into `runs/v217/dump` and `docs/thesis/results`: keep the old files in `results/superseded/` first) and
     report coverage and verdicts before/after.
   - If none: telecom severity is unsupported. Replace Critical / Minor / Within-reference for telecom with an honest
     state (e.g., report the measured image-derived sag ratio without a severity, status Not assessed with a new reason
     such as `no_validated_reference`), and make the pole verdict rely only on supported factors, INCONCLUSIVE
     otherwise. This is a major behaviour change: implement carefully across `config/wireframe.yaml`,
     `app/pipeline/runner.py`, `crs.py`, `hazard.py`, `app/services/plain.py`, `reasons.py`, `report_pdf.py`,
     `app/web/assets/field.js`, `findings.js`, `viewer.js`, `admin.js`, assistant `knowledge.md`, tests and docs, and
     run the full suite + E2E (the E2E checks severity counts; update it to the new semantics, not to pass blindly).
   2b. (only if a current power-line source is obtained) implement power lines analogously; note the regulatory
   wording "not greater than 3 %" means exactly 3 % is compliant (exclusive bound) and CRS is capped at 10, so compare
   the sag ratio directly, not CRS.
2. **Other unsourced values:** D-14 change-of-state conductor data (`config/wireframe.yaml: engineering`, marked
   ILLUSTRATIVE / "DEVELOPMENT PLACEHOLDERS" at line ~185): source from conductor data sheets or disable the
   adjustment; D-15 vegetation margins and 15 px rule; D-08 abstention 0.5; D-09 f = 0.25; D-10 re-measure p0 with
   the current bands (photo vs mirrored photo on validation, as in `ml/experiments/span_geometry*.py`).
3. **Backup:** implement a DB + files backup (SQLite online backup of `var/wireframe.db` plus `var/uploads`,
   `var/renders`, `var/reports`, with a manifest of SHA-256 hashes, atomic write, retention), a restore command, and an
   isolated restore test (integrity_check, foreign_key_check, row counts, file counts and hashes, app start, historical
   inspection and PDF). No external scheduler without an operational basis; document how to run it.
4. **Admin label:** INCONCLUSIVE poles show "Not assessed" in the admin header, dashboard filters and map legend
   (`app/web/assets/admin.js: RISK`, `admin-map.js`). Make it "Assessment incomplete" consistently (same filter key),
   with tests and E2E.
5. **Accessibility** (keyboard, focus, labels, contrast, table semantics, error announcements) and one consistent
   decision-support disclaimer on page and PDF (owner's suggested wording is in their S3 brief; verify against
   `app/core/notice.py`, which already holds the notice). Do not claim full compliance.
6. **Benchmark** re-run under transformers 5.17: `python -m ml.benchmark --images
   datasets/wireframe_seg/images/test/*.jpg <field photos from var/uploads> --reps 1 --field-reps 3 --out
   docs/thesis/results/benchmark_v223b.json`; stop the port 8000 server first (GPU memory); compare with
   `benchmark_v223.json`.
7. **Thesis revision:** inspect `docs/thesis/Chapter4_5_v218.docx`, `Chapter5.docx` and the PDFs in `~/Downloads`
   (`[CANILA_VILLAMAR_TUBIANOSA] BCS45.pdf`, `... (1).pdf`). Never overwrite originals; write revised copies (e.g.
   `docs/thesis/revised/Chapter4_5_v223_revised.docx`, untracked, owner decides). Use the alignment table in reference
   18.3. If no editable full manuscript exists, ask the owner for it (only they have it).

## Known limitations (do not hide)
Image-derived sag is not physical sag (no camera pose, no field validation); telecom test centerline F1 0.710,
precision CI [0.665, 0.768] on 21 photos; strict mask mAP50 0.0199; abandoned wires not detected; power lines not
assessed; many poles INCONCLUSIVE; SQLite single worker; backup covers DB only.

## Before ending your session
Update all five `docs/handoff/` files, commit only intended files, push (no force), verify `git rev-parse HEAD
origin/main` match, and print a complete new continuation prompt if work remains. Start the handoff when about 25 %
of context remains.

---
