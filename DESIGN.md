# WIREFRAME design system (S17, S18 semantics)

**Direction: a technical inspection instrument.** The page is a neutral paper surface with ink type. The photo is the
dominant element of a result. Colour carries meaning and never decorates. Source of truth:
`app/web/assets/styles.css` (tokens) and `app/web/assets/fonts.css` (type).

## Type
- **IBM Plex Sans** (variable 400–700) for all text.
- **IBM Plex Mono** (400/500/600) only for measurements, IDs, coordinates and counts.
- Both are self-hosted (latin subset, SIL OFL 1.1), with no third-party font requests.
- Scale (`--fs-*`): 12 / 13 / 14 / 16 / 20 / 24 px. Body is 14/1.5; result headings are 22–28 px, weight 600.

## Colour
| Token | Light | Dark | Meaning |
|---|---|---|---|
| `--bg` / `--surface` | #f4f5f6 / #fff | #0e1012 / #15181b | paper and sheet |
| `--text` / `-2` / `-3` | #16191d / #4d5560 / #69717c | #e8eaed / #aab1ba / #8a929c | ink, secondary, tertiary (all ≥ 4.5:1 on surface) |
| `--ink` | #16191d | #e8eaed | primary action background |
| `--accent` | #2b5f91 | #7fb0e6 | focus, selection, links: the one accent |
| `--warn` | #965800 | #e3a33a | reference threshold exceeded; needs review |
| `--ok` | #24804f | #46b97f | within the reference |
| `--crit` | #c42f2f | #ef6161 | emergency, failed; High on the sag meter and High triage |
| `--lv-mod` | #d4a20a | #e6b93b | Moderate (yellow) on the sag meter and in triage; text uses `--warn` for contrast |

S18 semantics: **green / yellow / red** mean low / moderate / high, and only the server decides which (`finding.meter_level`
for a wire's sag against its own reference; `screening.risk.level` for inspection triage). The two scales are labelled
separately ("Inspection triage" vs the per-wire meter) because a single wire can be High while the inspection is Moderate.
Review status (administrative) uses the accent, never a risk colour: an outlined square (not yet), half-filled (partially),
filled (complete). The semantic tone of a wire comes from the server (`finding.tone`). Wire class colours (power, telecom,
abandoned) appear only in the photo overlay; the Segmentation mask layer colours each traced wire individually.

## Space, shape, elevation
- Space: a 4 px scale (`--s1`…`--s12`).
- Radii: 4 / 6 / 10 px.
- Hairline borders separate sections. One soft shadow is used for overlays only.
- No gradients, glows, glass or shimmer.

## Components
- **Buttons:** primary is ink with ink-on text; secondary is surface with a hairline border; danger is `--crit`
  (emergency calls only).
- **Status tabs** (dashboard): a hairline row with the label and its live mono count; the selected tab has an ink
  underline. They replace the KPI cards.
- **Result label:** a text label tinted by tone. The **status badge** is a dot plus a label. They are never merged.
- **Wire card:** number disc in the class colour, type, classification label, estimated relative sag (mono), estimated
  measurement range, reference, the server's sentence, and per-wire quality notes. An exceeding card has a 3 px amber
  inset edge. When selected, the card shows a 2 px accent ring that matches the photo.
- **Important Notice:** the `<wf-notice>` component. It is shown once per page: in full at the foot of a result, as a
  toggle at the foot of the dashboard, and as a chip on the landing and upload screens.

## Motion
150–200 ms ease-out for state changes only. No looping decoration. Progress is shown only as the server's real
stages. `prefers-reduced-motion` is respected (the map pans without flying).

## Layout
- **Results:** report header (reference, status, headline, counts, dates, actions), then the photo (≈60%) beside the
  findings, then next steps beside contacts, then the methodology (folded), then the notice. Below 900 px it becomes a
  single column, with no horizontal scroll at 390 px.
- **Admin:** a full-width queue with a quiet activity area beneath.

## Known deviations (deferred)
The staff technical view (`?view=technical`) and the inspection sheet keep their S14 side-stripe markers. They were not
redesigned in S17.
