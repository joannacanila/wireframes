# WIREFRAME Assistant knowledge base

<!-- v2.19. The only source the assistant answers from. Every statement here describes what the running system
does (app/ and config/wireframe.yaml) or a documented limitation (docs/thesis/WIREFRAME_THESIS_REFERENCE.md).
Sections start with "## Title {#id}"; an optional "Link:" line points to the portal page that covers the topic.
Keep it in plain language: no formulas, no internal parameter names. When the system changes, change this file;
tests/test_v219_assistant.py checks a sample of answers. -->

## What WIREFRAME is {#overview}
Link: /admin
WIREFRAME screens overhead utility wires from photographs of utility poles. It finds the wires in a photo, tells power lines, telecom cables and abandoned wires apart where it can, checks how far each measurable wire hangs, notes vegetation touching wires, and gives each inspection a risk level. It is a screening and decision-support tool for barangay and city personnel, utility inspectors and engineers. It does not replace a field inspection, and its results must be verified by a qualified professional before any engineering, maintenance or safety decision.

## Who made WIREFRAME {#authors}
WIREFRAME, and this assistant inside it, was developed as an undergraduate thesis project, "A Vision-Based Diagnostic Framework for Urban Overhead Wire Infrastructure Risk Assessment", by Canila, Tubianosa and Villamar. The WIREFRAME documentation does not record more about the authors, their school or their advisers; ask the research team for those details.

## Overlapping or crossing wires {#overlap}
Where wires cross or run very close together in the photo, their outlines touch, and the detection model can join two wires into one, split one wire into several pieces, or give a shared stretch two wire types. WIREFRAME merges pieces that belong to the same physical wire and keeps one wire type per wire, but a crossing it cannot separate is left as "not assessed" rather than measured wrongly. A clearer view helps: move a few steps along the street so the wires separate against the sky.

## Why telecom cables are missed more often {#telecom}
Telecom cables are usually thinner and darker than power lines and are often bundled, tied to a messenger wire or hung close to other cables, so in a photo they blend into each other and into the background. Telecom cable detection is the least accurate of the three wire types in WIREFRAME's tests; the Accuracy section has the measured figures. A missed telecom cable is not evidence that the cable is in good condition.

## Getting to the utility poles {#nav-poles}
Link: /admin/map
**To view or reach the utility poles:**
1. Open **Map & Locations** from the top bar.
2. Stay on the **On map** tab to see the poles that have a GPS location; the pin colour is the risk level.
3. Use the **Find a street or barangay** box on the map, or the search box in the list, to find a pole, street or barangay.
4. Click a pole's row or its pin: the map zooms to it and shows **Directions** and **Street View** links (Google Maps).
5. To visit several poles, tick their rows: they are added to the **Visit route** card.
Poles without a location are under **Needs pinning**; they must be pinned before they appear on the map.

## Opening an inspection or its PDF report {#nav-reports}
Link: /admin/inspections
**To open an inspection report:**
1. Open **Inspections** from the top bar (or click a row on the **Dashboard**).
2. Find the pole with the search box, the status tabs (**All**, **Needs review**, **Failed**, **Low risk**) or **Filters**.
3. Click the row, or **Open**, to see the inspection page.
4. Use **PDF report** at the top of the inspection page to download the printable report; **Findings CSV** gives the wire list.
From Map & Locations, a pole's **Open** link goes to the same inspection page.

## Poles that need pinning {#nav-pinning}
Link: /admin/map?tab=pinning
Poles with no usable GPS location are not drawn on the map and cannot be added to a route. Their place in the inspection order does not change.
1. Open **Map & Locations** and choose the **Needs pinning** tab; the number beside it is how many poles need a location.
2. Administrators: choose **Pin location** on a pole.
3. Click the map where the pole stands, or type its latitude and longitude and choose **Preview**.
4. Check the point and choose **Save location**. The pole moves to **On map**; the change is recorded in the activity log.

## Building a dispatch route {#route-build}
Link: /admin/map
1. Open **Map & Locations**, **On map** tab.
2. Tick the checkbox on each pole to visit; the **Visit route** card appears with numbered stops and a dashed line on the map.
3. Reorder stops with the up and down arrows, or remove one with ×.
4. **Shortest straight-line order** reorders the stops after the first one for the shortest straight-line path. It does not follow roads.
5. **Route PDF** prints the stops with coordinates; **Copy link** copies a link to the same route (sign-in required); **Google Maps** opens the route for road directions and travel time.
WIREFRAME shows straight-line distance only; road distance and travel time come from Google Maps.

## Filtering poles on the map {#filter-poles}
Link: /admin/map
In Map & Locations, the list has a search box (pole ID, pole code or barangay), status chips (**All**, **Processing failed**, **Needs review**, **Incomplete**, **Awaiting review**, **Reviewed**) and a barangay menu. They work together. **Unassigned** in the barangay menu lists poles with no recorded barangay. The base map switch (**Light**, **Dark**, **Satellite**) is at the bottom right of the map.

## Getting around the admin portal {#nav-portal}
Link: /admin
- **Dashboard** (/admin): a fleet line (poles, inspections, poles on the map, poles needing a location), the review queue with status tiles that filter it, and activity split into People and Automated.
- **Inspections** (/admin/inspections): every inspection, with search, status tabs, filters and bulk actions.
- **Map & Locations** (/admin/map): the poles on a map, the inspection order, dispatch routes and pinning.
- **Technical exports** (administrators): on **Inspections**, in the **Export** menu, below the list CSV and the summary PDF.
- **Field Screener** (/workspace): upload a photo for a new inspection.

## Assigning a barangay {#assign-barangay}
Link: /admin/inspections
Barangays come from the official list (PSGC). On **Inspections**, hover a row with no location and choose **Assign**, or select rows and use **Assign barangay**; on an inspection page use **Assign** next to Barangay. Start typing and pick the barangay from the suggestions. Text that is not an official barangay, such as a person's name, is not accepted.

## Who uses which part {#portals}
Link: /admin
There are two parts. The Field Screener (/workspace) needs no account: anyone with the link uploads a pole photo and gets a screening result and a PDF report. The admin portal (/admin) needs an account. It has three main pages: Dashboard (what needs attention), Inspections (every inspection, its findings, the professional review and the reports) and Map & Locations (where the poles are and which to inspect first). Accounts are either inspectors (the three main pages, reviews and reports) or administrators (the same pages, plus technical exports, technical diagnostics on each inspection, test records and deleting inspections). There is no separate System page.

## How an inspection works {#workflow}
Link: /workspace
1. Take a photo of the pole with its wires against the sky, from the street, in daylight.
2. Open the Field Screener on a laptop or desktop browser and upload the original photo (not a screenshot). You can add an inspection name, city, barangay and the location.
3. WIREFRAME removes the GPS data from the stored copy of the photo, finds the wires, measures them and shows the result: an overall risk level, every wire section with its condition, photo limitations and what to do next.
4. Download the PDF report if the result needs follow-up.
5. In the admin portal, a qualified professional reviews the inspection and records a decision.

## Submitting a photo {#submit}
Link: /workspace
Use the Field Screener at /workspace in a laptop or desktop browser; it is not designed for phone screens. Accepted files are JPEG, PNG, WebP, TIFF, BMP and GIF, up to the size limit shown on the upload form. Use the original full-resolution photo. The optional fields are the inspection name, city and barangay; "Use my location" fills in the coordinates from the browser, or the photo's own GPS is read on upload. The GPS data is removed from the copy of the photo that is stored. Administrators can also upload several photos at once from Inspections.

## Taking a good photo {#photo-tips}
Frame the pole and its wires on both sides, with the wires against open sky where possible. Shoot in daylight with the sun behind you or to one side, not into the sun. Hold the camera steady and focus on the wires. Step back or use a wider angle so each wire is visible from pole to pole; move a few steps along the street if wires overlap each other. Upload the original photo, not a screenshot or a compressed copy.

## How wires are detected {#detection}
Computer-vision models trained on labelled photos of Philippine streets mark the pixels that belong to wires. WIREFRAME then separates those pixels into individual wire sections and traces each one. Short gaps where a wire was hidden or faint can be joined by estimation; those parts are drawn dashed on the photo and are not counted as detected. A separate model finds vegetation, and a pole detector marks utility poles as context only; poles never create or join a wire.

## Wire types {#wire-types}
Each wire section is labelled as a power line, a telecom cable (including fibre optic) or an abandoned wire, from what the photo shows. When the photo does not show clearly whether a wire is a power or a telecom line, no type is claimed and the wire is shown in grey as "type uncertain". "Abandoned wire" means visible signs of abandonment only; its operating status is not verified. WIREFRAME does not determine who owns a wire.

## Risk levels {#risk-levels}
Link: /admin/inspections
Each inspection has a **screening result** for the pole, and separately a **status** in the review queue:
- **Reference threshold exceeded**: at least one power-line span exceeds the 3% sag reference. Refer it for professional inspection. An unmeasured span never removes an exceedance.
- **No threshold exceedance detected**: every applicable span was measured and none exceeds its reference. This is not a safety clearance.
- **Incomplete assessment**: no measured span exceeds its reference, but at least one span could not be measured (the reason is listed per wire).
- **Measurement unavailable**: no span could be measured from this photo.
- **No applicable reference**: only wires without a reference (for example abandoned wires) were found.
The status (Processing failed, Needs review, Incomplete, Awaiting review, Reviewed) says where the record is in review. It is a separate column and never changes the screening result. A pole can also show Needs review because a reviewer flagged it.
**High risk, Moderate risk and Low risk** are risk levels from earlier versions. Older inspections may still show them in exports; they came from criteria that were withdrawn, so they are not findings. Since S17 a pole has a screening result instead (above), and a high risk label never means more than "review this record".
## Why one wire reading may not decide the pole's level {#corroboration}
Since S17 each power-line span is classified on its own estimated sag, and the pole result is exceeded if any span exceeds the 3% reference. The calibrated measurement range is reported beside each estimate; it never changes the classification.

## Excessive sag {#sag}
Sag is how far a wire hangs below the straight line between its two visible ends. WIREFRAME fits a hanging-wire curve to each measurable wire section and reports its **estimated relative sag**: the largest distance from the curve to that line, divided by the line's length, in the photo. Where available, an **estimated measurement range** (a calibrated 90% range) is shown beside it. This is an image-derived screening estimate, not a surveyed sag.
**Power lines** of a clearly identified type are classified against ERB Resolution No. 95-21 (1995), Section 19, which says the sag of wires on electric utility poles shall not be greater than 3% of the distance between poles:
- below 2%: **Within lower reference range** (the 2% boundary is descriptive, not regulatory)
- 2% to 3% inclusive: **Within reference threshold**
- above 3%: **Reference threshold exceeded**
The classification uses the unrounded estimate, so 3.004% exceeds even though it displays as 3.0%. ERC Resolution No. 02, s. 2010 records that the DSOAR superseded ERB 95-21 for compliance purposes, so an image-derived classification is a screening result, never a compliance finding.

## Which wires the 3% reference applies to {#reference-scope}
The 3% reference applies only to **power lines** whose type the photo shows clearly. No 3% limit applies to telecom or fibre cable: no source gives one, and manufacturer data for fibre cable allow more than 3% sag under load. **Telecom and fibre** cables have their own typical ranges: below 2% "Below typical range", 2% to 5% "Within typical range", above 5% "Above typical range" (worth review by the provider, not an exceedance). Neither reference is ever applied to the other type. An abandoned wire's type and owner are not established, so no reference applies. A wire of uncertain type is not assumed to be a power line: its span is listed as measurement unavailable for the reference. Even for a power line the classification is image-based screening: not an assessment or a safety finding in the engineering sense, and not a compliance finding.

## Wires that were not assessed {#not-assessed}
A span cannot be classified when it could not be measured reliably or no reference applies. The per-wire reason is shown:
- **Runs off the photo**: at least one end is not visible, so the full span cannot be measured.
- **Conductor type uncertain**: no reference can be selected.
- **Too small, overlapping or too faint**: the shape could not be traced or separated from neighbouring wires.
- **No applicable reference**: abandoned wires.
Unmeasured spans make a pole "Incomplete assessment" only when no measured span exceeds its reference; they never hide an exceedance.

## Why a wire was not detected {#missed-wires}
The photograph may not provide enough visual detail for reliable detection. This happens when wires blend into the background (buildings, trees, a bright or grey sky), overlap other wires or structures, are very thin or far away, or the photo is dark, blurred or compressed. Telecom cables are harder to detect than power lines. Take another photograph with better light and a clearer view of the wires against the sky. Absence of a detected wire or hazard does not mean there is none.

## Photo limitations {#limitations}
The Photo limitations section lists only what affected this inspection, each with what would help: wires too small to measure, overlapping wires, wires past the photo edge, parts filled by estimation, and image quality problems such as glare, darkness, blur, low resolution or a busy background. Read it before relying on a result.

## Incomplete assessments {#incomplete}
An **Incomplete assessment** means no measured span exceeds its reference but at least one span could not be measured. The result page lists each unmeasured span with its reason; a photo that shows both poles and the full span against open sky usually completes it. The Inspections page has an Incomplete tab.

## Retake warnings {#retake}
When the photo is unlikely to give a reliable result (wires hard to tell apart, conflicting wire types, or photo problems), the Field Screener asks before showing the result whether to retake the photo or proceed. Proceeding keeps the warning "Assessment may be inaccurate" on the result and in the PDF. A retake is linked to the earlier inspection, which is kept.

## When to retake a photo {#when-retake}
Retake the photo when the result is "Not assessed", when a retake warning appears, when many wire sections could not be assessed, or when the photo is dark, blurred, against the sun or cut off at the edges. If a retake is not possible, request a field inspection.

## Vegetation {#vegetation}
WIREFRAME marks vegetation in the photo and notes when it overlaps or adjoins a wire in the image. This is a visual observation from one photo, not a clearance measurement: a photo cannot tell whether a branch in front of or behind a wire touches it, and pixel distances are not metres. It does not change the pole's level. Report vegetation touching a power line to the electric utility or barangay; do not trim branches near wires yourself.

## Utility poles in the photo {#poles}
A pole detector draws dashed boxes around utility poles for context. It has not reached the accuracy target set for it, so its boxes may miss poles or mark lamp posts and other structures. It never changes how wires are found or scored.

## What WIREFRAME cannot determine {#cannot}
WIREFRAME works from a single photograph. It cannot measure real distances or clearances in metres, check electrical condition, load, tension, insulation, attachments, pole strength or leaning, see wires hidden by other objects, tell who owns a wire, or confirm whether an abandoned-looking wire is live. It can miss wires, misclassify wire types and give a wrong risk level. Treat every result as a screening that needs professional verification, and treat every wire as live.

## Low risk is not a safety certification {#not-certification}
WIREFRAME provides an inspection screening result, not a safety certification. A low-risk result means no measured wire in this photo was flagged; it does not certify that the pole or its wires are safe, and wires that could not be assessed are not covered. A qualified professional must determine whether the pole and its wires are safe. Nobody can mark a pole as safe in WIREFRAME.

## Accuracy {#accuracy}
On the project's held-out test photographs, wire detection agreed with the hand-drawn labels with an F1 score of about 0.81 overall (about 0.83 for power lines and 0.71 for telecom cables), and the wire-type check reached an accuracy of about 0.94 on the same test set. These figures come from a limited set of local photos; accuracy on other streets, cameras and conditions can be lower. The full evaluation is documented in the project's thesis reference.

## Administrative review {#review}
Link: /admin/inspections
Every inspection has a review status, kept separate from the machine screening:
- Not yet reviewed: no administrator has opened it.
- Partially reviewed: an administrator opened it (opening it records you as a reviewer).
- Review complete: an administrator recorded a decision.
To finish a review, open the inspection and use the Administrative review panel: choose a decision (For field inspection, For utility coordination, For scheduled cleanup, For monitoring, No further administrative action at this time, or Other documented decision), give the rationale (required), and optionally a follow-up and field observations. A confirmed critical hazard (a snapped or hanging wire, a wire dangling into a path, sparking, a conductor in an accessible area, or a physically measured low clearance against a documented reference) can be recorded; it sets the triage to High. The machine findings never change; the decision is stored beside them with your name, the time and what the screening showed when you decided. Failed inspections can be reviewed too.

## Review records and history {#review-history}
Link: /admin/inspections
Decisions are never overwritten. "Amend decision" stores a new decision and marks the previous one superseded; "Reopen review" (with a reason) returns the inspection to Partially reviewed. Both are in the audit trail with the previous and new values, and the decision history is listed in the review panel. Several reviewers can work on one inspection; each is recorded. The inspection page also lists the pole's other inspections.

## When to request professional review {#when-review}
Record a decision for every inspection, starting with High and Moderate triage, failed processing, retake warnings, vegetation touching a power line, or anything that seems wrong. When in doubt, choose For field inspection.

## Problem wires and inspection triage {#triage}
Link: /admin/inspections
A problem wire is a detected wire with a supported observation: measured sag beyond its reference or typical range, a possible loose or dangling end, vegetation contact, or an apparently abandoned cable. Detected wires and problem wires are counted separately. Inspection triage counts problem wires: 0 is Normal, 1 to 9 is Moderate, 10 or more is High, and a confirmed critical hazard sets High. This is an operational triage rubric (versioned and configurable), not a legal or statutory classification. Each wire's sag meter is a different scale: green Low, yellow Moderate, red High against that wire's reference (for power lines below 2%, 2 to 3%, above 3%).

## Dashboard {#dashboard}
Link: /admin
The first line counts poles, inspections, poles on the map, poles needing a location and poles awaiting a review. "Inspections by pole" shows each pole's latest inspection with its file name, location, detected wires, triage (for example "Moderate · 3 / 12 problem wires") and review status. Tabs filter by review status (All, Not yet reviewed, Partially reviewed, Review complete) and the Screening chips by triage (High, Moderate, Normal) or by Processing issue and Missing location; every count matches the rows it shows. Activity lists real events, with automated events (photo received, processing completed) marked apart from people's actions. Review progress shows how many poles are in each review status.

## Inspections page {#inspections}
Link: /admin/inspections
Inspections lists every inspection: file name, location, date, detected wires, problem wires, triage, review status and the administrative decision. Search by file name, inspection or report ID, pole ID, place or coordinates. Tabs filter by review status, the Screening chips by triage or processing issue or missing location, and Filters by barangay, date and more. Preview opens a quick review drawer (J and K step through rows) with the same result view and decision panel as the inspection page. Download the filtered list as CSV, which includes the triage, problem wires, review status and decision.

## The annotated photo {#viewer}
Link: /admin/inspections
"How did we know?" shows the evidence in six layers: Original, Pole detection, Segmentation mask (each traced wire in its own colour), Drawn wires, Problem wires and Combined. Zoom with the buttons, the scroll wheel or a pinch, drag to move, and Reset view to see the whole photo. Hover or click a wire in the photo or the list to highlight it and see its sag meter.

## Map & Locations {#map}
Link: /admin/map
Map & Locations shows the latest inspection of each pole on a map and the inspection order beside it. Marker colour is the pole's status, the same one the Dashboard and Inspections show (red processing failed, amber needs review, grey incomplete, dark grey awaiting review, green reviewed); a group of nearby poles shows a ring with its mix of statuses. Filter by status and barangay. One search box filters the poles as you type; press Find to also look up a street, barangay or landmark. Select a pole to open it in Google Maps, get walking directions, open Street View where Google has it, or open its inspection.

## How poles are ranked {#ranking}
Link: /admin/map
The inspection order puts first the poles whose photos show the worst and most widespread wire conditions in the most cluttered scenes. It combines the pole's worst wire condition, the average condition of its measured wires and how visually cluttered the scene is. Poles with no assessed wire are listed last without a position; while no validated sag reference exists, that applies to every pole. The ranking only orders poles for inspection; each pole keeps its own risk level. The weighting is set by administrators.

## GPS and locations {#gps}
Link: /admin/map
A pole's location comes from the photo's GPS data, from "Use my location" on upload, or from coordinates an administrator enters. GPS is removed from the stored photo, and the location is kept only on the pole record, which only signed-in accounts can see. Poles without a location are still listed and ranked, marked "No GPS", but cannot be shown on the map. Filter by "Has GPS" or "No GPS" to find them. To add a location, upload a new photo with location enabled.

## Getting walking directions to a pole {#directions}
Link: /admin/map
Select a pole with GPS on Map & Locations, then use "Directions" for walking directions in Google Maps from where you are, "Google Maps" to see the place, or "Street View" to look at the street first where Google has imagery. These open Google Maps in a new tab; WIREFRAME sends only the coordinates.

## Reports {#reports}
Link: /admin/inspections
From an inspection: the PDF report (the annotated photo, findings, each wire, photo limitations, professional reviews and the Important Notice) and a findings CSV (one row per wire: type, condition, finding). From Inspections: the filtered inspection list as CSV. From Map & Locations: the inspection priority report (PDF). Every report and CSV carries the Important Notice. Reports are generated when you open them, so they include the latest reviews.

## What a PDF report contains {#pdf}
The inspection PDF has the inspection name, place, pole and time; the Important Notice; any retake warning; the annotated photo; the overall assessment and its reasons; wire counts by type; every wire with its condition and notes; processing notes; and the professional review record (or "Not yet reviewed"). It prints on A4 with the notice in every page footer.

## Test records {#test-records}
QA runs, synthetic samples, UI checks, timing runs and stock photos are marked as test records. They are kept in the database but left out of the Dashboard, Inspections, Map & Locations, the exports and the assistant. Administrators can show them on **Inspections** (Filters, **Include test records**) and re-classify any record with **Mark as operational** or **Mark as test**.

## Administrator tools {#system}
Link: /admin/inspections
There is no separate System page. Administrators find their tools where the work is: **Technical exports** (wire measurements, site manifest, technical priority report) on **Inspections**; **Technical diagnostics** at the bottom of each inspection; test records through the Inspections filters; failed inspections under the **Failed** tab with **Retry** on the inspection. If processing is unavailable, the Dashboard shows a warning.

## An inspection failed {#failed}
Link: /admin/inspections
"Processing failed" means the photo could not be processed, for example a damaged file or an internal error. Open the inspection and use Retry. If it fails again, upload the photo again, or another photo, and tell an administrator, who can open **View system trace** on the inspection.

## The photo cannot be processed {#cannot-process}
Check that the file is a photo in a supported format (JPEG, PNG, WebP, TIFF, BMP, GIF) and under the size limit, and that it is the original photo, not a screenshot. Very small or blank images cannot be screened. If the upload form says photo screening is not switched on, the detection model is not installed or not loaded on this server; an administrator must fix this.

## No wires were detected {#no-wires}
If no wire is found, the photo may not show the wires clearly enough: they may be too thin or far away, against a busy background, in poor light, or out of frame. Take a new photo with the wires against the sky, from closer, in daylight. If wires are clearly visible and still not found, record a review and flag the pole for further inspection. No detected wire does not mean there are no wires or no hazard.

## Processing warnings {#warnings}
"Inspection processing unavailable" means the detection model is not loaded, so new photos cannot be screened; existing inspections can still be viewed and reviewed. "Preparing the inspection models" appears on a new installation while the models download. "Photo screening is switched off" means an administrator requires reference annotations for validation work. "Assessment may be inaccurate" is a retake warning on one inspection.

## Signing in and accounts {#accounts}
Link: /admin/login
Accounts are created by an administrator on the server with the create_admin command (role admin or inspector). There is no self sign-up and no default account. Too many failed sign-ins lock the account for a few minutes. Sessions end on sign-out and expire after a period of inactivity.

## About this assistant {#assistant}
The WIREFRAME Assistant is the help built into the admin portal. It can:
- **Find your way**: where a page or control is and how to use it, for example "How do I build a dispatch route?".
- **Answer from live records**: "Which high-risk poles have no GPS?", "What needs review?", "Summarize today's dispatch priorities".
- **Open pages for you**: "Show failed inspections", "Open pole LOC-001", "Locate this inspection on the map".
- **Explain results**: risk levels, sag, wires that were not assessed and the inspection you have open.
It cannot change inspections, record reviews, delete records or change settings, and it says so when something is not documented. It does not replace professional engineering judgement.
