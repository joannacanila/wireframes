# WIREFRAME: product context

## What it is
WIREFRAME is an image-based decision-support tool for overhead distribution poles. From a single photo it finds the
overhead wires, estimates how much each span sags relative to the straight line between its visible ends, and compares
that estimate with a published reference. It documents visible conditions. It does not certify safety or compliance
and does not replace an on-site inspection.

## Who uses it
- **Citizens and barangay staff (Field Screener, no account).** They photograph a pole they are concerned about, read
  the result, and contact the right organization. Usually on a phone, often outdoors, with no engineering background.
- **Engineering reviewers (Admin portal, signed in).** They triage the queue, review results, locate poles, keep the
  contact directory current, and export reports. They work on desktops and tablets.

## Surfaces and modes
| Surface | Path | Mode |
|---|---|---|
| Landing | `/` | Persuade (one screen, one action) |
| Field Screener: upload, processing, results | `/workspace` | Operate (the result is read as a report) |
| Dashboard, Inspections, Map & Locations, Directory, inspection sheet | `/admin/*` | Operate |
| Methodology, PDF report | in-page, `/api/reports/*` | Read |

## Product truths the interface must keep
- **References** (register D-37):
  - Power lines: 3% relative sag (ERB Res. 95-21 s.19, regulatory). Three bands: below 2% "Within lower reference
    range", 2–3% inclusive "Within reference threshold", above 3% "Reference threshold exceeded". The 2% boundary is
    descriptive only.
  - Telecom and fibre: their own 2–5% typical range. It is never applied to power lines, and the power reference is
    never applied to telecom.
- **Server-side decisions.** Classification uses the unrounded ratio and is computed on the server. The frontend
  holds no thresholds.
- **Pole result.** Any exceedance makes the pole "Exceeded". Unavailable spans with no exceedance make it
  "Incomplete". If nothing could be measured, it is "Measurement unavailable". An unavailable span never hides an
  exceedance.
- **Separate fields.** The screening result (what the photo showed) and the queue status (where the record is in
  review) are separate, everywhere.
- **Important Notice.** One canonical text, shown once per page:
  > WIREFRAME is an image-based decision-support tool designed to help document visible pole and wire conditions. Its
  > results are preliminary and may contain errors or limitations. Findings should be reviewed by qualified
  > professionals and must not replace an on-site inspection or applicable safety assessment.
- **Out of scope.** No dispatch capability, and no invented contacts. Contacts appear only once a person has verified
  them.

## Voice
Professional, plain and specific. Name the measurement and the reference; recommend a concrete next action. Never
"safe", never "give or take", never alarm. Red is for emergencies only.
