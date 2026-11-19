# S17 final report: engineering backend, sag assessment, segmentation, UI/UX redesign

Working tree: `/Users/joannacanila/WIREFRAME-s13`, branch `recovery/s13-master`. Nothing has been committed, pushed,
merged or deleted. Recovery refs are `refs/recovery/s17-start` (= S16 end), `s17-engine` (after Phase 2) and `s17-end`
(this state). All S15/S16 refs are kept. Development used only the isolated data copy (`scratchpad/iso14/var`); the
live database was never opened. One model instance ran at a time. Register entry: **D-37**.

## 1. Outcome
- Every wire with a measurable span and an applicable reference now gets a decisive classification on the server.
- Each pole gets one screening result. It is shown identically on the results page, dashboard, Inspections list, map
  and CSV, and always separately from the queue status.
- The Important Notice is the canonical text, shown once per page.
- The interface was rebuilt as a technical inspection instrument: IBM Plex, paper and ink, colour by meaning only. The
  three detector findings left open by S16 (Geist, gold glow, shimmer) are fixed.
- Four segmentation variants were measured against a new labelled baseline. None improved without regressions, so none
  was adopted. The model is unchanged; there are no new labels to train on.

## 2. Validation summary
| Check | Result |
|---|---|
| pytest (full suite) | **1031 passed, 2 xfailed** (both strict xfails are the documented shallow-crossing limitation, s.8) |
| Regression baseline (`s17-start`, exported copy) | 963 passed, 4 skipped |
| ruff | clean |
| `node --check`, all JS | clean |
| `scripts/e2e_check.py` (real upload through the model, isolated server) | **85 / 85** |
| S17 results-page browser check: 3 outcomes × desktop 1440 / tablet 834 / phone 390 | **111 / 111** |
| S17 staff-pages browser check (dashboard, Inspections, map list-to-marker, directory) | **16 / 16** |
| Console errors / horizontal scroll | none / none at 1440, 834, 390 px |
| `impeccable detect` | 23 → 19; the remaining 19 are explained in s.14 |

## 3. Phase 1: baseline and recovery
- `refs/recovery/s17-start` was created with a temporary index (no commit on the branch).
- The baseline suite ran on a `git archive` export so that no edit in progress could affect it: 963 passed, 4 skipped.
- Before screenshots: `docs/thesis/figures/s17/before_*.png`.

## 4. Sag reference configuration (Phase 2)
`config/wireframe.yaml` holds one versioned block per reference, validated by `ComparisonReference`. Classification
cannot be enabled without a non-provisional status, a source and all three codes.

| | Power (`PWR-2026.11-S17`) | Telecom / fibre (`TEL-2026.11-S17`) |
|---|---|---|
| Source | ERB Res. No. 95-21 (1995-08-03), s.19 "Poles, Sag of Wires" | FOA typical installation ranges |
| Status | regulatory (3 %); 2 % descriptive | typical range |
| Bands | < 2 % · 2–3 % inclusive · > 3 % | < 2 % · 2–5 % inclusive · > 5 % |
| Codes | WITHIN_LOWER_REFERENCE_RANGE · WITHIN_REFERENCE_THRESHOLD · REFERENCE_THRESHOLD_EXCEEDED | BELOW_TYPICAL_RANGE · WITHIN_TYPICAL_RANGE · ABOVE_TYPICAL_RANGE |
| Raises attention | above | no (above is "notable", reviewed by the provider) |

The configuration records that ERC Res. No. 02 s.2010 superseded ERB 95-21 with the DSOAR. The image-derived
comparison is therefore stated everywhere as screening, never as compliance.

## 5. Classification engine
- Defined in `app/services/comparison.py`: `classify(r, lo, hi)` returns above when r > hi, below when r < lo, and
  otherwise at. It runs on the **unrounded** ratio.
- The calibrated 90 % range (else ±1 px resolution) is reported as `uncertainty_overlaps`. It **never moves the
  class**.
- CRS = min(10, 250 × ratio), so 3 % = 7.5. It applies to power lines only, is for display, and never decides a class.

**Boundary tables** (from `tests/test_s17_reference.py`; all pass):

| Ratio | Power | Telecom |
|---|---|---|
| 1.20 % | WITHIN_LOWER_REFERENCE_RANGE | BELOW_TYPICAL_RANGE |
| 2.00 % | WITHIN_REFERENCE_THRESHOLD | WITHIN_TYPICAL_RANGE |
| 2.95 % | WITHIN_REFERENCE_THRESHOLD | WITHIN_TYPICAL_RANGE |
| 3.00 % | WITHIN_REFERENCE_THRESHOLD | WITHIN_TYPICAL_RANGE |
| 3.01 % | REFERENCE_THRESHOLD_EXCEEDED | WITHIN_TYPICAL_RANGE |
| 4.50 % | REFERENCE_THRESHOLD_EXCEEDED | WITHIN_TYPICAL_RANGE |
| 5.00 % | REFERENCE_THRESHOLD_EXCEEDED | WITHIN_TYPICAL_RANGE |
| 5.01 % | — | ABOVE_TYPICAL_RANGE |
| 3.004 % (shows "3.0%") | EXCEEDED | — |
| 2.9996 % (shows "3.0%") | WITHIN | — |

| Ratio | CRS |
|---|---|
| 1.2 % | 3.0 |
| 2 % | 5.0 |
| 3 % | 7.5 |
| 4.5 % | 10.0 (capped) |
| 5 % | 10.0 (capped) |

## 6. Pole-level aggregation
`outcome.pole_assessment` applies these rules in order:

| Pole state | Result |
|---|---|
| any span exceeds its reference | **Exceeded** |
| some spans unavailable, none exceeding | **Incomplete** |
| every applicable span assessed, none exceeding | **No exceedance** |
| nothing measurable | **Measurement unavailable** |
| only wires without a reference | **No applicable reference** |

Tested cases, including the spec's two examples:
- 1.5 / 2.2 / 3.8 % with one span unavailable → **Exceeded** (exceeded = [3], unavailable = [4]).
- 1.5 / 2.2 / 2.8 % with one span unavailable → **Incomplete**, "no exceedance in measured spans".
- Telecom above 5 % → recorded as above typical, not an exceedance.
- A wire of uncertain type → unavailable with reason `type_uncertain`.
- An abandoned wire → no reference.

**Observation on the isolated data:**
- Of 137 stored results, 101 aggregate to Incomplete, 24 to No exceedance and 12 to Exceeded.
- The Incomplete results are almost all repeated QA uploads of a few dense dataset images (128–136 traces each). In
  those, 10–30 % of fragments fail the fit (`residual_too_large`: 1159) or fall below the precision gate (431).
- Under the specified rule this is correct, and the headline still states that no measured span exceeds.
- Whether short fragments below the precision gate should count as "applicable spans" is an owner decision (s.19).

## 7. Segmentation (Phase 3)
**Method.** `ml/experiments/s17_seg_eval.py`:
- `dump` runs the full pipeline on the labelled val (22) and test (21) images, one model at a time.
- `score` computes pixel P / R / F1 / IoU / Dice, boundary-2px F1, FP area and instances, missed rate, fragmentation
  (loose, and strict = identity switches), duplicates, merges, coverage, continuity and endpoint error. Each is broken
  down by condition: dark, bright, low sky, dense, vegetation, crossings, thin, low-res. Conditions without labels are
  listed as such.
- `compare` writes the baseline against each candidate. Results are in `docs/thesis/results/s17_seg_*.json`.

**Diagnosis (baseline).**
- 759 fragmented wires, 2886 ownership transitions; 79 % of transitions have zero gap.
- The dominant failure is **identity switching** at crossings and between close parallels, not broken masks.

| Variant (val / test) | Strict frag ↓ | Loose frag ↓ | Missed ↓ | Merges/img ↓ | FP inst ↓ | Continuity ↑ | Endpoint rel ↓ |
|---|---|---|---|---|---|---|---|
| baseline (shipped) | 0.139 / 0.151 | 0.585 / 0.662 | 0.037 / 0.052 | 4.82 / 4.33 | 48 / 54 | 0.619 / 0.573 | 0.163 / 0.211 |
| xjunction | 0.155 / 0.138 | 0.583 / 0.635 | 0.041 / 0.055 | 5.05 / 4.43 | 50 / 60 | 0.621 / 0.578 | 0.163 / 0.204 |
| turn25 | 0.145 / 0.149 | 0.584 / 0.657 | 0.037 / 0.052 | 4.64 / 4.24 | 49 / 55 | 0.615 / 0.572 | 0.163 / 0.212 |
| xjunction_turn25 | 0.159 / 0.138 | 0.579 / 0.632 | 0.041 / 0.055 | 5.18 / 4.24 | 50 / 59 | 0.617 / 0.573 | 0.164 / 0.205 |
| cluster12 | **0.113 / 0.107** | 0.652 / 0.704 | 0.038 / 0.048 | 5.45 / 4.33 | **43 / 50** | 0.599 / 0.547 | 0.178 / 0.217 |

**Decision: none adopted.** The rule is "improves without regressions on both splits", and every variant regresses
somewhere.
- `cluster12` is the best candidate: it removes about a quarter of identity switches and some false positives.
- But it shortens traces and moves endpoints, and endpoints feed the sag measurement directly.
- Figures: `figures/s17/seg_cluster12_vs_baseline_{1,2,3}.jpg`. The labels in those images count *loose* fragmentation.

Pixel F1 is unchanged by all variants: they change only span extraction.

## 8. Controlled segmentation tests
`tests/test_s17_segmentation.py` uses synthetic masks with the shipped parameters.

**Pass:**
- crossings at 60°, 45° and 30°, and a sagging crossing: one span per wire across the full width, with < 2 % of
  pixels on the wrong wire
- parallel wires 6 and 10 px apart
- 1 px wires
- an occlusion under `span_max_gap` (70 px) is bridged; a larger gap is not
- deterministic output

**Strict xfail (known limitation):**
- Crossings at or below about 20° swap wires about half the time. The two Y junctions are joined greedily.
- The X-junction option does not fix this. `cluster_radius` 12 fixes 20° but not 16° at 2 px, and costs continuity
  (s.7).

No model was retrained and no accuracy figure was produced without labels.

## 9. Annotation workflow
- **Hard-case pack:** `docs/thesis/results/s17_hardcase_pack.json` lists 20 images ranked by baseline failures. It is
  generated by `ml/experiments/s17_hardcase_pack.py` and is deterministic.
- **Instance annotation spec:** `FIELD_LABELLING_GUIDE.md` §"S17 instance annotation" covers instance IDs through
  crossings, centrelines, endpoints, crossing / occlusion flags, conductor type, QA by double-labelling 20 %, and
  export through `ml/field_dataset.py`.
- **Remaining requirement:** instance-level labels on the pack before any extractor or model change can be validated.

## 10. HEIC preview (Phase 4)
- `POST /api/analysis/preview` runs the upload's own `acquire()`, so the decoder, orientation and size cap are the
  same as for the analysis.
- It returns a JPEG data URL with no EXIF (re-encoded), stores nothing, and refuses damaged or unsupported files with
  the upload's codes.
- The page uses it for HEIC and as a fallback whenever the browser cannot decode a file. The processing screen shows
  the same decoded copy.
- The report shows `captured_at`, the photo's timestamp only (never the device or position).
- Tests: `test_s17_heic_preview.py` (10).

## 11. Backend consistency (Phase 5)
- The pole result travels as `result {code, label}` on records rows (dashboard queue, Inspections) and map sites. It
  is also the CSV's appended "Screening result" column.
- `tests/test_s17_consistency.py`:
  - Two different scenes give identical results on the page, queue, Inspections, map and CSV.
  - A review changes the queue status, never the result.
  - The frontends render the server's result and compute none.
- No stored record was rewritten. Results are computed on read from the stored measurements and the versioned
  configuration.

## 12. Design system (Phase 6)
`PRODUCT.md` and `DESIGN.md` (repository root) were written from the S17 brief.
- **Type:** IBM Plex Sans (variable) and Mono, self-hosted (`assets/fonts/`, latin, OFL). All Google Fonts requests are
  removed.
- **Colour:**
  - Primary actions are ink; a steel accent is used only for focus, selection and links.
  - Semantic colours: amber `--warn` #965800 (5.2:1 on the page), green `--ok`, red `--crit` (emergencies only).
  - The text tiers measure 4.5–6.1:1.
- **Removed:**
  - the gold accent and the `--glow` token
  - the submit-button glow
  - the skeleton shimmer
  - the processing "scan" sweep, which moved whether or not anything progressed
- Spacing, radii and type scale are now tokens.

## 13. Pages (Phase 6)
**Results** (`after_result_*`):
- **Header:** report reference, status dot, server headline, and counts from the pole aggregation (detected /
  assessed / exceeded / unavailable). Also when the photo was taken (if known), when it was screened, the review
  state, and the actions (Download report, Copy message, Share link, New photo).
- **Photo:** the dominant element, sticky on wide screens.
- **Wire cards:** type, classification label, estimated relative sag (mono), estimated measurement range (90 %),
  reference, the server's sentence, and per-wire quality notes.
- **Other wires:** a toggle whose state is remembered. Selection is synchronised across cards, list and photo, and the
  cards are keyboard-operable.
- **Sag diagram:** δ / L in plain words.
- **Next steps:** shown beside the contacts. "Call 911" is red.
- **Methodology:** folded into "How this result was produced".
- **Notice:** the canonical text once, at the foot. The shell's footer chip is hidden on results.
- **Copy message:** report ID, place and time, the result, each listed wire with its sag and class, and the
  recommended action. No link, no safety claim.
- "Give or take" is gone.

**Dashboard:**
- Status tabs with live counts replace the five KPI cards and the status dropdown. The tests check the counts against
  the operations API.
- The Result column is separate from Status.
- The notice is a footer toggle, not a banner.
- Activity (People / Automated) and Recently processed stay below the full-width queue.

**Inspections:** a Result column. All six bulk actions (assign, unassign, mark reviewed, set barangay, mark test /
operational) were verified as implemented server-side, so none was removed.

**Map & Locations** (renamed from "Map & Dispatch"; "Dispatch route" is now "Visit route"):
- Popups show the screening result.
- List-to-marker navigation is scripted: clicking a card selects the pole, highlights its marker and opens the popup.
- Poles that share a GPS point stay in their cluster, as designed.

**Directory:** unchanged apart from the shared tokens.

**Upload / processing:** the shared tokens. Primary action is ink; no glow; real stages only.

**Technical view:** reads the server classification and span state ("Measurement unavailable", "No applicable
reference") instead of "Unassessed (…)".

**Language:** the assistant guides (`knowledge.md`, `citizen.md`) were rewritten to the S17 classification. Legacy
High / Moderate / Low risk labels are explained as withdrawn, so they are never presented as findings.

## 14. Design detector
| Run | Findings |
|---|---|
| S16 | Geist, gold glow, shimmer (plus older findings) |
| S17 | 23, then 19 after fixes |

**Fixed in S17:** the remaining `--glow` token (dark-glow on all three pages) and landing tertiary text at 4.1:1 (now
#8a929c).

**Remaining 19, all reviewed:**
- **side-tab (13):** stripes in the pre-S17 technical view and inspection sheet, deferred (DESIGN.md "Known
  deviations").
- **pulsing-dot (3):** genuine loading indicators.
- **broken-image (2):** `<img>` tags whose `src` the script fills, so false positives.
- **repeating-stripes-gradient (1):** the dashed-line swatch in the map legend.

## 15. Screenshots
- `docs/thesis/figures/s17/before_*.png`: S16 desktop and phone for results, dashboard, Inspections, map, directory
  and processing.
- `after_result_{exceeded,clear,incomplete}_{desktop,mobile}.png`, `after_result_exceeded_tablet.png`,
  `after_result_exceeded_desktop_dark.png`
- `after_dashboard_{desktop,tablet,mobile}.png`, `after_inspections_desktop.png`, `after_map_desktop.png`,
  `after_directory_desktop.png`
- `seg_cluster12_vs_baseline_{1,2,3}.jpg`

## 16. Tests added and changed
**New:**
- `test_s17_reference.py` (33)
- `test_s17_heic_preview.py` (10)
- `test_s17_consistency.py` (3)
- `test_s17_segmentation.py` (10 + 2 strict xfail)

**Kept, not loosened:** the S16 machinery tests still run, against an explicit `s16_cfg` fixture that reproduces the
provisional non-classifying mode. They still verify that mode.

**Updated to new requirements at equal strictness:**
- page reading order (`test_s12_results_ui`)
- notice placement and count (once in source as `NOTICE_FOOTER`, rendered once per view, never inside the folded
  methodology)
- self-hosted fonts and no shimmer or gold (`test_v211_field_ui`)
- dashboard notice as a footer toggle and not a banner (`test_v219_portal`)
- the canonical notice and the PDF footer (`test_api`, `test_v219_portal`)
- the Map & Locations name (`test_v221`)
- `scripts/e2e_check.py`: S17 header and counts, flagged = the pole's exceedances, one report action, status tabs
  against the API, the Result column, and the canonical notice in the PDF

## 17. Files
**Backend:**
- `config/wireframe.yaml`, `app/core/config.py`, `app/core/notice.py` (new)
- `app/services/{comparison,outcome,ops,records,sites,methodology,report_pdf,route_pdf,analysis}.py`
- `app/api/{analysis,admin}.py`
- `app/pipeline/wirenet.py` (options only; defaults unchanged)
- `app/assistant/{knowledge.md,citizen.md,intent.py,live.py,service.py}`

**Frontend:**
- `app/web/{field,admin,landing}.html`
- `app/web/assets/{styles,field,admin,landing}.css`, `fonts.css` and `fonts/` (new)
- `app/web/assets/{field,admin,admin-map,admin-diagnostics,findings,notice,assistant}.js`

**ML:** `ml/experiments/s17_seg_eval.py`, `s17_seg_figure.py`, `s17_hardcase_pack.py`

**Docs:** `PRODUCT.md`, `DESIGN.md`, `FIELD_LABELLING_GUIDE.md`, the register (D-37) and the changelog.

## 18. Not done or partial
- **Model retraining:** not possible without instance labels (s.9).
- **Shallow crossings (≤ 20°):** still swap identity; this is the known limitation in s.8.
- **Not redesigned:**
  - the staff technical view and the inspection sheet keep S14 layouts (they read S17 data)
  - the Directory received tokens only, no structural refinement
- **Not re-photographed:**
  - the upload and processing screens (token changes only)
  - the Inspections list at phone width

## 19. Decisions for the owner
1. The D-37 wording for publication, in particular "Reference threshold exceeded" for an image-derived estimate.
2. Whether fragments below the precision gate should count as applicable spans. This drives most Incomplete results
   on dense images (s.6).
3. Whether to annotate the hard-case pack, as the prerequisite for testing `cluster12` or a two-junction pairing rule.
4. Keep the self-hosted IBM Plex files (OFL), about 72 KB.
5. Committing this work. Nothing is committed; `refs/recovery/s17-end` holds a snapshot.

## 20. Git state
- Branch `recovery/s13-master`, HEAD `02bd470` (unchanged).
- Working tree: 64 modified and 67 untracked paths (S13–S17 work), plus `server8000.log` from before this session.
- Snapshot: `refs/recovery/s17-end` = `4abcb48` (169 files differ from HEAD).
- No commits, pushes, merges, branch deletions, resets or cleans.
- The isolated server is still running on 127.0.0.1:8014 against the isolated copy.
