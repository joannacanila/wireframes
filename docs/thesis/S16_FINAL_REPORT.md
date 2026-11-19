# S16 final report: system-wide harmonisation

Working tree: `/Users/joannacanila/WIREFRAME-s13`, nothing committed. Recovery refs: `refs/recovery/s16-start` (= S15
end), `s16-backend`, `s16-results`, `s16-end`. Development used the isolated data copy only.

## 1. Outcome
The results page, dashboard, Inspections list and map now read one server-side plan and one status vocabulary. HEIC
photos work end to end. The measurement now carries a calibrated uncertainty. A field-data labelling workflow exists.
The model itself is unchanged: there are no new labels to train on.

## 2. Audit (Phase 1)
`docs/thesis/S16_SYSTEM_AUDIT.md` lists the 10 highest-impact problems. The main ones:
- three status vocabularies
- counts of different entities presented side by side
- summary lines contradicting the references
- the picked wire being inserted into the attention list
- contacts suffix handling
- location mismatch
- silent reinterpretation of historical records
- HEIC
- the model gap

## 3. Data and backend integrity (Phase 2) — done
- `outcome.display_plan`: each wire appears in exactly one list.
- Headline counts are the lengths of those lists.
- Per-reference attention and notable zones.
- Per-reference summary lines.
- `interpretation` note on every result.
- One contacts resolver (`for_analysis`), plus `city_key`, `location_check`, and wire-type relevance.
- `records.latest_rows` is shared by the dashboard and the map.
- A single `report_id`.
- Directory edits are audited field by field, including the verification value before and after.

## 4. Model and measurement (Phase 3) — evaluated; no retraining
- **Calibrated 90 % band.** Error ≈ 0.05804 / chord px + 3.30469 × relative residual. Coverage: val 0.898, test 0.881. Median band 1.08 pp.
- **Flags against labels (1 px rule).**

  | Rule | Val | Test |
  |---|---|---|
  | Telecom > 5 % | 0 / 3 correct | 1 / 4 correct |
  | Power > 3 % | 1 / 4 correct | 2 / 2 correct |

  Gating flags by the band removed the true power flags, so the band is displayed, not used for flagging.
- **Continuation path through crossings.**
  - Val: unchanged.
  - Test: MAE 0.497 unchanged; p90 1.288 → 1.371.
  - Synthetic crossing fixed (3.84 % → 7.01 %).
  - Field wire 47: no change, because its skeleton has no junction.
  - Not adopted.
- **Curvature test:** rejected; it removed as many good measurements as bad.
- **Where wires are lost (test set).**
  - Detection 0.948, weakest in dark photos (0.894) and photos with little sky (0.899).
  - Typing given detection 0.919; power wires are typed correctly only 0.859 of the time.
  - Measurement given detection 0.993.
  - Abandoned wires are never typed correctly.
- **S15 baseline metrics are unchanged.** Accuracy 93.2 %; centreline F1: wire 0.812, power 0.822, telecom 0.715, abandoned 0.

## 5. Field-data workflow — tooling done, no labels yet
- `ml/field_dataset.py`:
  - reads the data read-only
  - groups exact duplicates and dHash near-duplicates
  - keeps each pole in a single leakage group
  - assigns splits deterministically
  - writes versioned output folders and never overwrites one
- `docs/thesis/FIELD_LABELLING_GUIDE.md`: classes, ambiguity rules, the fibre-evidence attribute, QA by double labelling, and per-photo conditions.
- Trial run: 15 photos in 12 groups.

## 6. Results page (Phase 4) — done
- Cards for the attention and notable wires, in server order, plus one list for the rest.
- Picking a wire opens a separate panel; the lists and counts never change.
- A larger, sticky photo with pinch zoom; the page still scrolls over the photo until it is zoomed.
- An inline sag diagram.
- "Give or take" ranges.
- A report card with the reference ID and the date and time.
- "Copy message", which confirms only when the copy succeeded.
- One disclaimer; no eyebrow label.
- Amber for attention; red only for emergencies.
- Accordions reduced to one ("About this result").

## 7. Contacts — backend done; page layout unchanged
- The utility relevant to the photo's wire types is listed first.
- The Meralco / Dasmariñas contradiction is fixed.
- No development notes are shown.
- The visual layout of the contact list on the results page is the S15 one.

## 8. Dashboard (Phase 5) — done
- A fleet line where every count names its entity: poles, inspections, on the map, need a location, mismatched barangays, awaiting review.
- Status tiles filter the queue.
- The review queue takes the full width.
- Activity is split into People and Automated.
- "Recently processed" replaces "Recently completed".

## 9. Inspections — mostly done
- New Incomplete tab and a barangay/city mismatch filter.
- One Export menu (technical exports grouped inside it).
- Location and coordinates stacked; dates don't wrap.
- The Action column is always visible, including on phones.
- Quick-review drawer: J/K to move, Space to open, Esc to close; records the same audited review.
- Bulk actions reuse the S14 bar and its server checks; they were not redesigned.

## 10. Map and dispatch — done, with existing parts reused
- Marker and cluster colours follow the operational status.
- Each cluster's ring shows its status mix.
- One search box.
- `?pin=1` opens Needs pinning (32 poles in the isolated copy).
- The pinning flow and the straight-line route tool are the existing ones.
- No new dispatch capability; `docs/thesis/DISPATCH_DESIGN.md` documents what one would need.

## 11. Directory — done
- `tel:` links, website names, and coverage truncated with "+N more".
- Search and filters by type and verification.
- No notes in table cells.
- Audited edits.
- Nothing becomes public until a person sets a verified status and a source.

## 12. HEIC / HEIF and the processing page (Phase 6) — done
- **Dependency:** `pi-heif` (decode-only; libheif and libde265, both LGPL; no encoder).
- **Detection:** by file content, not by name or MIME type.
- **Orientation:** no double rotation.
- **Errors:** clear messages for damaged files and for unsupported codecs.
- **Preview:** the page shows the server's decoded copy, since most browsers cannot display HEIC.
- **Processing screen:** fills the viewport, keeps the photo in view, shows real stages only, and rotates a "Did you know?" fact (physics and safety only).

## 13. Validation (Phase 7)
- pytest: 967 passed. New suites: `test_s16_integrity` 16, `test_s16_heic` 11, `test_s16_admin` 3.
- ruff: clean.
- `node --check`: all JavaScript files.
- `scripts/e2e_check.py` (HEIC photo): 81 / 81.
- S16 browser checks at 1440 and 390 px: 24 / 24.
- No console errors and no horizontal scroll.

## 14. Partial or not done
- Model retraining: not possible without field labels.
- The technical view does not yet display `interpretation.note`.
- The results-page contact layout and the bulk-action bar were not redesigned.
- Map–list fly-to was not re-tested by a script.
- Older design-detector findings remain: the Geist font, a gold glow, the skeleton shimmer.

## 15. Decisions for the owner
- Keep `pi-heif` as a runtime dependency.
- Whether telecom beyond 5 % should ever raise attention. The evidence says no for now.
- Whether to adopt the continuation path once field labels exist.
- Any dispatch scope, which needs new data models and services.
- Committing this work.

## 16. Tests changed
Updated to new requirements, not loosened:
- `test_s14_ux.py`: telecom is now notable; the Incomplete tab is checked against the filtered list.
- `scripts/e2e_check.py`: the single plan, stable lists when a wire is picked, the report ID, and the fleet line.
- The assistant guide now matches the new labels.
