# 4.17 v2.15: landing page, upload page and results page redesigned for non-specialist users

v2.15 changes nothing in any model, threshold, calculation or verdict rule:
- WireNet-R34 (`ab2353c7`) and the YOLO11 vegetation model are unchanged.
- CRS, the corroborated hazard rule, the 100 px chord gate, the reliability grade, and v2.14's span ownership and
  provenance are unchanged.

The audit separated the issues into three groups:
- **Interface:** generic copy, a server-status label, a demo button, and scores shown without explanation.
- **Data:** no way to name an inspection, and only a pole code to identify it.
- **Computer vision:** fragmented wire masks, which v2.14 traced to its sources (§4.16). No new CV fault was found;
  the fragment of a neighbouring cable merged into span 14 of P-B1D137 remains, as documented there.

## 4.17.1 Data: inspection name and city

`analyses` gained `inspection_name` and `city` (nullable, at most 120 characters). The database's existing
add-missing-columns migration creates them; no data is changed.

- **Upload:** `POST /api/analysis/upload` accepts both as optional form fields. Input is trimmed, control characters
  are removed, and blank becomes null.
- **Result:** `/result` returns `inspection: {name, city, barangay}`, and the analysis listing includes both fields.
- **File name:** the original file name is kept unchanged in `image.original_filename`.
- **Pole code:** still generated internally. The admin portal, MSPI ranking and exports depend on it. It is no
  longer asked for on the upload page; it appears in the diagnostics and on the PDF.
- **PDF:** the title is the inspection name, falling back to the pole code. The line under it gives the
  barangay and city, pole code and original file name.

## 4.17.2 Landing page (`/`)

The previous page was a scrolling marketing page: a headline and screenshot side by side, invented-looking metric
counters, feature cards and method demos. It is replaced by a single screen that does not scroll at desktop
sizes, checked at 1024 × 768, 1280 × 720, 1366 × 640, 1440 × 900 and 1920 × 1080. It keeps the same identity:
near-black ground, the logo's gold, Geist.

- **Drawing:** a drafting sheet of two hairline supports and the wires between them, drawn in once on load.
- **Measured wire:** one wire carries the measurement WIREFRAME makes, its chord L and mid-span sag δ, with a δ/L
  readout computed from the drawn curve. Moving the pointer changes that wire's sag. Nothing else moves, and
  `prefers-reduced-motion` turns the motion off.
- **Wordmark:** set large at the foot of the sheet.
- **Copy and action:** one factual sentence and one action, "Open the Field Screener", plus an admin sign-in link.
- **Removed:** "Overhead wire screening · for LGU engineers", the metric counters, feature cards and method demos.
  No statistics, claims or partnerships are shown.

## 4.17.3 Upload page (`/workspace`)

A single centred card:
- **Photo area:** drop or choose a photo. It shows a preview with the file name, pixel size and file size, and
  offers Replace and Remove.
- **Validation in the browser**, before any upload: type and the 64 MB limit; the server re-checks everything.
- **Fields:** inspection name (optional), city (optional) and barangay (optional).
- **Use my location:** the browser is asked only when the button is pressed. The coordinates are shown and can be
  removed. A failure or refusal shows a plain message and never blocks the upload. No reverse geocoding is done,
  so no place name is guessed and no location is sent to an outside service.
- **Removed:** "New inspection", "One photo of the pole. A field verdict in seconds.", the framing/GPS paragraph,
  "Load the sample", the pole code field, and the "Model ready" label. The server state is still polled, but only
  to switch the form into validation mode when no model is installed.
- **While processing:** the loading state lists real steps (uploading, location data removed, analysing). It shows
  no invented percentage.

## 4.17.4 Results page

Written for someone with no technical background, in this order:

1. **Header:** the inspection name (or the file name), the place, the date and time, the original file name, the
   PDF report and New inspection.
2. **Overall assessment:** mirrors the server verdict in plain words and never presents a screening as a
   clearance.

   | Server verdict | Shown as |
   |---|---|
   | AT RISK – HIGH | "High risk found" |
   | AT RISK – MODERATE | "Moderate risk found" |
   | NOT AT RISK | "No high risk found", with "This is a screening from one photo, not a safety inspection." |
   | INCONCLUSIVE | "Photo not clear enough" |

   - When individual sections read high or moderate but the pole was not flagged (the corroboration rule needs
     several), the page says so and names them instead of hiding them.
   - A coverage line gives how many sections could be assessed and how many could not.
3. **Risk summary:** only High, Moderate and Low counts. Sections that could not be assessed are never counted as
   low, and are explained under Photo limitations.
4. **Photo and wire sections:**
   - The viewer from v2.14 with plain labels: Original / Wires / Mask / Both, plus layers, overlay strength,
     vegetation, zoom, Fit and Reset.
   - A list of wire sections: wire, type, condition, and a short plain finding such as "Hangs low (4.5%)",
     "Too small or far to measure" or "Overlaps other wires".
   - Selecting a section highlights it on the photo and opens a detail panel. The panel shows the full sentence,
     its sag against the allowance, and a sag score (the CRS) explained in words. The measurement figures
     (chord, δ, residual, probability, inferred share, status code) sit under "Measurement details".
5. **What to do next:** built from this result.
   - Who to call, and which sections to have checked first.
   - Vegetation to report.
   - What to include in routine inspection.
   - What photo would help.
   - Always: "Do not touch, climb toward or try to move any overhead wire. Treat every wire as live." No repair
     instructions are given.
6. **Photo limitations:** only those the result shows, each with the sections it affects and what would help:
   - too small or far away to measure;
   - overlapping wires;
   - runs past the photo edge;
   - parts estimated across hidden or faint gaps;
   - the photo-quality cause (dark, glare, blur, out of focus, too small, few wires visible);
   - drop wires not checked for sag.
7. **Technical diagnostics** (collapsed, for administrators and researchers): model information, image quality,
   segmentation metrics, statistical analysis (binomial corroboration, p0, α, server verdict and reasons),
   processing performance, and configuration and execution (fingerprints, chord gate, pole code, analysis id,
   flags). No value was removed.

## 4.17.5 Website only

As in v2.14, WIREFRAME supports laptop and desktop browser windows of at least 1024 px. Below 700 px the screener
shows a notice asking for a computer. The brief asked for mobile review, but the owner's standing instruction is
that the system is a website and does not run on phones, so no phone layout was added.

## 4.17.6 Verification

| Check | Result |
|---|---|
| `pytest` | 528 passed (new `tests/test_v215_inspection_fields.py`; console, landing and portal tests updated) |
| `ruff check app ml scripts tests` | clean |
| `scripts/ui_check.py --mock` | 16 of 16 pass |
| `scripts/ui_check.py`, real photos (P-B1D137, the handrail photo, the night pole) | 3 of 3 pass |
| Upload behaviours (headless Chrome) | all pass |

The mocked check covers the four verdict states × light / dark × 1440 and 1024 px. It now also requires a
three-level risk summary, no model-status text, the retake step first when the photo is unclear, and "not a safety
inspection" on a no-risk result.

The real-photo check enters the name, city and barangay through the form and verifies them on the result page,
along with the original file name, the PDF, every view, layers, zoom, Fit and Reset, and table↔photo selection.
It found 0 console errors.

The upload behaviours checked were:
- no geolocation request on page load;
- a refused location shows a message and does not block the upload;
- the wrong type and an oversized file are rejected with clear messages;
- Remove and drag-and-drop work.

Results: `docs/thesis/results/v215_ui_check_mock.json` and `v215_ui_check_real.json`. Screenshots:
`runs/ui_shots_v215/`.
