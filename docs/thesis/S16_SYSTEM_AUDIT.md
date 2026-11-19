# S16 system audit (18 Nov 2026)

Working copy: `/Users/joannacanila/WIREFRAME-s13`, branch `recovery/s13-master`, S15 uncommitted (80 paths).
Snapshot before S16: `refs/recovery/s16-start` (= `s15-end` content). Data used: the isolated copy
(`scratchpad/iso14/var`), never `WIREFRAME/var`.

## Architecture (as found)

| Layer | Pieces |
|---|---|
| Inference | `app/pipeline/*`: WireNet ensemble (1024 letterbox) -> strands -> strand-type head (S15 parity + clamp) -> centreline -> catenary / kink-split fit -> quality, guardrail, hazard |
| Result services | `comparison.py` (references, per-wire states), `outcome.py` (finding, overview, citizen summary), `methodology.py` (summary lines, topics), `diagnostics.py`, `report_pdf.py` |
| Operations | `ops.py` (operational status per inspection), `records.py` (legacy risk / review), `sites.py` (legacy MSPI ranking for the map), `contacts.py`, `barangay.py` (PSGC) |
| API | `analysis.py` (public upload / result), `operations.py` (contacts, operations), `admin.py` (dashboard, inspections, bulk, exports), `dashboard.py` (map priority) |
| Web | `field.js` (citizen), `admin.js` / `admin-map.js` / `admin-diagnostics.js`, `viewer.js`, `findings.js`, `notice.js` |

## Highest-impact problems (evidence from the isolated data)

1. **Three status vocabularies for one inspection.** The dashboard uses `records.py` risk / review (high, medium,
   low, unassessed); Inspections uses both that and `ops.py` operational status; the map uses `sites.py` MSPI ranking,
   which since the S4 threshold removal reports `n_wires 0, NO_VALID_WIRES` for inspections that measured 10 wires.
2. **Counts are of different entities without saying so.** 76 = registered poles, 77 = operational inspections (one
   pole has two), 75 = triage queue (latest per pole, minus 1 reviewed), 74 = map sites (latest per pole, minus 2
   failed). Flagged: 48 per inspection vs 47 per pole.
3. **Result summary contradicts the references.** `summary_lines` says "the 3% reference for power lines applies to
   115 wires" when all 115 are telecom compared with the telecom bands (single-reference code path left from S11).
   The operational reason still says "power line at or above the provisional sag reference" for telecom flags.
4. **Result page selection.** Picking a wire inserts it into "The wires to look at", so the heading says 2 and three
   cards show; ordering of other wires is client-side.
5. **Flags on fragments.** 48 of 77 inspections are flagged, all by the telecom >5% band on read. Many flagged
   wires are ~40 px traces in dense photos (wire 47: 14.1% +/-2.3 pp). The +/-1 px resolution model is not
   calibrated against labelled data for short traces.
6. **Contacts.** `city_of(None, "Dasmariñas City")` -> None (suffix form not normalised). With no resolved place
   the note says "The electric utility for this location is not in the directory", which is false: Meralco is.
   Seed notes ("S14 search found no official ...") are shown in admin cells.
7. **Location consistency.** A barangay of one city can be stored with another city (Salitran II + Imus) with no
   warning. (The three records found were the assistant's own S15 browser-check uploads; now marked test, audited.)
8. **Historical records are re-interpreted silently.** Comparisons are recomputed on read from the current
   configuration; older results carry no record of the references in force. Measurements are unchanged.
9. **HEIC/HEIF:** not decodable (Pillow 13 without a HEIF plugin; `pillow-heif` not installed).
10. **Model:** field typing gap (S15 D-31); dense / night detection losses not yet attributed to a stage.

## Working and worth keeping

Upload, queue and retry limits; S15 references and states; PDF generation; role checks (`require_admin`,
`require_system`); audit events; record kind (test / operational); PSGC barangay resolution; e2e script.
