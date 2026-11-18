# Current Project State

Last updated: 18 Nov 2026, end of session S8 (branch `recovery/s8-colour-meter` running on port 8000: v2.18 page, colour sag meter, provisional 3 % reference, D-26). Before: end of session S7 (branch `recovery/s7-sag-meter`: v2.23 page with the sag meter, D-25). Before: end of session S6 (recovery branch `recovery/s6-restore`; `main` unchanged since S5). Continuity notes only. The authoritative technical record is
`docs/thesis/` (`WIREFRAME_THESIS_REFERENCE.md`, `RESEARCH_AND_DECISION_REGISTER.md`, `IMPLEMENTATION_CHANGELOG.md`,
`results/`). Read with `SESSION_LOG.md` and `NEXT_SESSION_PROMPT.md`.

## Project
WIREFRAME: a web application (FastAPI + vanilla JS, SQLite) that screens overhead wires in one ground-level photo of a
utility pole: segmentation (3-member WireNet ensemble, YOLO11s-seg vegetation, UperNet scene gates, YOLO11n poles),
strand extraction and typing (DINOv2-small + MLP), catenary fit, image-derived sag ratio, pole verdict with abstention
(INCONCLUSIVE), PDF reports, admin portal. Undergraduate thesis project (Canila, Tubianosa, Villamar). Technical
description: `docs/thesis/WIREFRAME_THESIS_REFERENCE.md` (single thesis reference document by the owner's rule).

- Repository: https://github.com/joannacanila/wireframes.git, branch `main`
- Version: v2.23 + S4 assessment change + S5 diagnostic restoration. HEAD / remote: see SESSION_LOG.md (S5)
- Environment: macOS 26.0.1, Apple M1 8 GB, Python 3.13.2 in `.venv`, torch 2.15.0 (MPS), transformers 5.17.0
- Local-only (never commit): `var/`, `models/*.pt`, `models/hf/`, `datasets/`, `runs/`, `.env`, `docs/thesis/*.docx`,
  untracked figures in `docs/thesis/figures/` (owner's)
- Manuscript: full thesis only as PDFs `~/Downloads/[CANILA_VILLAMAR_TUBIANOSA] BCS45.pdf` (18 Nov 01:40) and
  `... BCS45 (1).pdf` (01:50, newer). Editable: `docs/thesis/Chapter4_5_v218.docx`, `docs/thesis/Chapter5.docx`.

## Assessment behaviour now (S4)
| Element | Behaviour | Basis |
|---|---|---|
| Every wire class | Measured (image-derived sag ratio + 1 px resolution + fitted curve), **not assessed** (`no_validated_reference`) | D-02..D-05: no applicable source for any sag limit |
| Endpoint off photo / uncertain type | Not assessed (`endpoint_unavailable`, `class_uncertain`), measurement kept | D-06, D-07 |
| Vegetation | Image observation (NOTE, pixel gap); never raises AT RISK | D-15 |
| Pole verdict | INCONCLUSIVE ("Assessment incomplete") for every analysis with measured wires; NOT AT RISK needs every span assessed and >= 1 assessed | D-08, D-19 |
| MSPI ranking | No CRS, so not ranked | D-20 |
| Stored pre-S4 results | Rows unchanged; shown without withdrawn levels/verdicts, with a notice | D-21, `app/services/basis.py` |
| Disclaimer | Owner's S4 wording, page + all PDFs + CSV | `app/core/notice.py`, `notice.js` |
| Admin label for INCONCLUSIVE | "Assessment incomplete" (key `unassessed`) | brief section 6 |
| Diagnostic output (S5) | Every measured wire drawn (curve, chord, sag point) and explained; per-photo "What the analysis found"; retake advised only when wires were not (fully) measured; completeness `measured` | D-22, reference 8.6 |
| Withdrawn levels (S5) | Never counted; withdrawn whenever the record lacks the current assessment basis | D-23 |

Release evaluation (S4): segmentation/strand metrics identical to v2.21; assessment coverage 0 % (val 0/677, test
0/721 measured spans); verdicts val 22/22 and test 21/21 INCONCLUSIVE (before: val 0/9/3/10, test 3/7/3/8).

## Status
**Done (S4):** threshold research and decisions D-02..D-21; implementation; tests 754 passed; ruff clean; E2E 20/20,
0 page errors (isolated copy, port 8011); release re-evaluation; reference 7.4, 7.5, 7.6, 7.7, 8.1, 8.2, 14.5, 15, 17,
18.3, 19, 20.3 and README updated; disclaimer; admin label.

**Done (S5):** diagnostic regression reproduced in a browser (isolated copies) and repaired; tests 776 passed; ruff
clean; E2E 24/24, 0 page errors; `docs/thesis/` made authoritative (register and changelog moved there); research
update S16-S18 (DSOAR s.1.7.12 lead, unverified). Production server on port 8000 NOT restarted (owner to confirm).

**Done (S6):** v2.18 result experience restored on branch `recovery/s6-restore` (D-24); full backup and restore
(D-13 resolved); 3 % research Outcome C; assumption register. 788 tests; E2E 41/41. Not merged into `main`.

**Pending (priority order, details in NEXT_SESSION_PROMPT.md):**
0. Owner confirmation: restart port 8000; push the S5 commit.
1. Full backup + restore (D-13).
2. Accessibility review (no compliance claim).
3. Benchmark under transformers 5.17 -> `docs/thesis/results/benchmark_v223b.json`.
4. Thesis revision copies (Ch. IV-V docx; full manuscript needs an editable source from the owner).

**Owner-supplied items needed:** official DSOAR text for Section 1.7.12 (D-04, S16); current PEC Part 2 sag article (D-04); optionally the JMC No. 01 s.2021 PDF (official
hosts refuse automated access); the photographed operators' cable model / sag-tension tables if a telecom assessment is
ever to be restored; an editable full manuscript (.docx).
