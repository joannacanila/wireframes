# Current Project State

Last updated: 18 Nov 2026, end of session S3. Read with `RESEARCH_AND_DECISION_REGISTER.md`,
`IMPLEMENTATION_CHANGELOG.md`, `SESSION_LOG.md` and `NEXT_SESSION_PROMPT.md`.

## Project
WIREFRAME: a web application (FastAPI + vanilla JS, SQLite) that screens overhead wires in one ground-level photo of a
utility pole: segmentation (3-member WireNet ensemble, YOLO11s-seg vegetation, UperNet scene gates, YOLO11n poles),
strand extraction and typing (DINOv2-small + MLP), catenary fit, image-derived sag ratio, conductor-specific
assessment, pole verdict with abstention (INCONCLUSIVE), PDF reports, admin portal (dashboard, inspections, map and
dispatch). Undergraduate thesis project (Canila, Tubianosa, Villamar). Full technical description:
`docs/thesis/WIREFRAME_THESIS_REFERENCE.md` (20 sections; single thesis reference document by the owner's rule).

- Repository: https://github.com/joannacanila/wireframes.git, branch `main`
- Version: v2.23. HEAD and remote HEAD at the end of S3: see SESSION_LOG.md
- Environment: macOS 26.0.1, Apple M1 8 GB, Python 3.13.2 in `.venv`, torch 2.15.0 (MPS), transformers 5.17.0
- Local-only (never commit): `var/` (production DB, uploads, reports, backups), `models/*.pt`, `models/hf/`,
  `datasets/`, `runs/`, `.env`, `docs/thesis/*.docx` (owner's manuscripts, untracked), untracked figures in
  `docs/thesis/figures/` (owner's)
- Manuscript: full thesis exists only as PDFs: `~/Downloads/[CANILA_VILLAMAR_TUBIANOSA] BCS45.pdf` (18 Nov 01:40) and
  `... BCS45 (1).pdf` (01:50). Editable sources in repo: `docs/thesis/Chapter4_5_v218.docx` (Ch. IV-V as of v2.18),
  `docs/thesis/Chapter5.docx` (older Ch. V). No editable full-manuscript source found.

## Assessment behaviour now
| Class | Behaviour | Basis |
|---|---|---|
| Telecom cable | Scored vs 0.040: < 2.4 % Within reference, 2.4 to < 4.0 % Minor sag, >= 4.0 % Critical | **Unsourced** provisional values (register D-02, D-03) |
| Power distribution line | Measured, Not assessed | 3 % of ERB 95-21 superseded in 2006; no current source verified (D-04) |
| Abandoned wire | Measured, Not assessed | No criteria exist (D-05) |
| Uncertain type | Not assessed | D-06 |
| Endpoint off photo | Not assessed (visible sag kept as review cue) | D-07 |
| Pole | INCONCLUSIVE if < 50 % of candidates scored or a partial span sags into a band or photo grade Retake | D-08 policy (unsourced threshold) |

Coverage (release dumps): validation 256/629 = 40.70 %, test 320/658 = 48.63 %; INCONCLUSIVE 10/22 and 8/21 poles.

## Status
**Completed and tested:** S1 release audit; S2 research alignment (transformers 5.17, abandoned wires, INCONCLUSIVE
labels, viewer fix, backup restore test, documentation); S3 primary-source research on the 3 % rule.

**Tested results (S3, 18 Nov 2026):** pytest 719 passed; ruff clean. (S2: E2E 16/16 x3 with 0 page errors; release
evaluation identical under transformers 5.17; model hashes unchanged; isolated DB restore passed.)

**Pending (priority order, details in NEXT_SESSION_PROMPT.md):**
1. Telecom threshold research and decision (D-02, D-03); if unsupported, replace severity with an honest state.
2. Remaining unsourced values: change-of-state conductor data (D-14), vegetation margins (D-15), abstention 0.5
   (D-08), precision-gate f (D-09); re-measure p0 under current bands (D-10).
3. Full backup (DB + uploads + renders + reports), restore procedure and test (D-13).
4. Admin "Not assessed" label for INCONCLUSIVE poles (shared by dashboard filters and map legend).
5. Accessibility review; consistent decision-support disclaimer on web and PDF.
6. Re-run benchmark under transformers 5.17 (`ml/benchmark.py`).
7. Thesis revision: create revised copies (never overwrite originals); needs an editable full manuscript.

**Blocked:** power-line 3 % (needs current PEC Part 2 article text or another in-force instrument + engineer
confirmation); physical sag validation (needs field measurements).

## Next action
Task 1 of NEXT_SESSION_PROMPT.md: telecom sag-reference research.
