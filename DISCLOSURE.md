# WIREFRAME v2: disclosure log

Branch `refactor/chapter-iii-v2`, built from `main` at `90e1bc4`. This log records what changed, why, what
was verified and how, and what is still open. Development models were trained from v2.6 onwards and evaluated on
the test split as each version's report records (the evaluation results themselves are in `docs/thesis/`); none of
the Table 3.10 targets is met. (Until v2.12 this paragraph still read as written for v2.1, that no evaluation result
was claimed and no Table 3.10 target had been measured on test; it was out of date from v2.6 and is corrected here.)

---

## v2.15: landing, upload and results pages redesigned for non-specialists; inspection name and city

* **Frozen**: every model, threshold, calculation and verdict rule (`docs/thesis/chapter4_v2.15_site.md`).
* **Data**:
  * `analyses.inspection_name` and `analyses.city` (nullable; added by the existing column migration).
  * Optional upload form fields, cleaned on input.
  * `inspection {name, city, barangay}` in `/result`, and both fields in the listing; the PDF title uses the name.
  * The original file name is unchanged. The pole code is still generated internally but no longer asked for.
* **Landing**: one non-scrolling screen with the same identity. The old headline, metric counters and feature cards
  are removed; no claims are made.
* **Upload**:
  * A centred card with preview, Replace, Remove, and client-side type and size checks.
  * Location is requested only on click and never blocks the upload; no reverse geocoding.
  * The intro copy, "Load the sample", the pole code field and the "Model ready" label are removed.
* **Results**:
  * A plain-language assessment that never reads as a clearance.
  * A High / Moderate / Low summary, with unassessed sections never counted as low.
  * Photo limitations taken only from the result; next steps that never ask anyone to handle a wire.
  * Plain table findings, with technical detail under "Measurement details".
  * Diagnostics grouped and collapsed, with nothing removed.
* **Not done**:
  * No phone layout: website only, as instructed.
  * The fragment of a neighbouring cable on span 14 of P-B1D137 remains (§4.16.3).
* **Verified**: 528 tests pass; ruff is clean. `ui_check --mock` passes 16 of 16 and the real-photo check 3 of 3,
  including name, city and barangay persistence and the PDF. Upload behaviours pass in headless Chrome.

---

## v2.14: wire-geometry provenance, span ownership, inspection console redesign, website only

* **Frozen**: WireNet-R34 (`ab2353c7`), CRS, the hazard rule and its p0 values, the chord gate and the reliability
  grade. No training was done.
* **Diagnosis** of the "fragmented purple segments" on P-B1D137:
  * The purple was WireNet's raw telecom-pixel mask, which is genuinely fragmented on this photo.
  * The span assembler drew straight 2-px gap bridges into span masks: 1,299 px on this photo, in 4 of its 5
    high-risk spans.
  * Nearest-piece pixel assignment painted detached blobs into spans.
  * Line geometry was drawn half a pixel off.
* **Changed**:
  * Per-wire `provenance` (observed vs inferred pixels and bridged segments) and `reason_short` in the result API.
    Both are additive and computed on read, so older analyses get them too.
  * `span_own_component: true`: validation tolerant mAP50 0.035557 → 0.035554; 0 of 23 verdicts changed; per-span
    measurements changed on 10 of 23 photos (none on P-B1D137). p0 was not re-derived.
  * Inferred gaps drawn dashed and cut out of the span masks; pixel-exact mask rendering; +0.5 px line alignment.
* **Tested and rejected** (validation):
  * Probability-gated bridging at τ 0.02–0.30: no gain, losses from τ 0.15.
  * Lateral-offset gate at 3–12 px: losses of 0.0005–0.0031.
  * This run's shipped baseline is 0.0356, not v2.8's 0.0397, because the `runs/cache/val` cache differs. Every
    comparison uses the same cache.
* **Open**: on P-B1D137, span 14 (CRS 10.0) still includes a fragment of a neighbouring cable. In WireNet's mask the
  two are connected through the crossing tangle. The fit follows the upper cable; only the drawn mask is affected.
* **UI**: `/workspace` redesigned as inspection software, as listed in `docs/thesis/chapter4_v2.14_cv_ui.md` §4.16.4.
* **Website only**: the phone layout, the phone-width checks and the 10 phone screenshots and figure were removed.
  Below 700 px the page shows a desktop-required notice. The supported window width is at least 1024 px.
* **Verified**:
  * 524 tests pass; ruff is clean.
  * `ui_check --mock`: 16 of 16. `ui_check` on real photos: 3 of 3, including the PDF and the upload.
  * No overflow at 1024–1920 px.

---

## v2.13: ground-structure gate, field guidance, field screener redesign (branch `main`)

Full methods and tables: `docs/thesis/chapter4_v2.13_cv_ui.md`. The shipped WireNet-R34 (`ab2353c7`) is frozen and
was not retrained or modified; YOLO11-seg and the Chapter III mathematics (catenary fit, CRS, VNI) are unchanged.
Every threshold was decided on the 22 validation photos; a change was adopted only if validation span tolerant
mAP50 stayed at or above the shipped 0.0397.

**Hardware.** Official latency figures: the 8 GB Apple M1 (MPS), as for every version since v2.6. The unit tests,
the browser verification and the documentation build were also run on a Windows 11 PC (Python 3.13.17, CPU only);
no Windows timing is reported as, or mixed with, an M1 latency figure.

### Added
* **Ground gate** (`app/pipeline/scene.py`, `HybridSegmenter._ground_gate`; config `segmentation.ground_gate: true`,
  `ground_gate_share: 0.5`). UperNet-ConvNeXt-T trained on ADE20K (`openmmlab/upernet-convnext-tiny`, MIT licence,
  60 M parameters) labels ground classes at 384 px (0.26 s on the M1; 98.8% agreement with 576 px on the problem
  photo). A span with ≥ 50% of its pixels on road / sidewalk / floor / earth / path / grass / field / sand / dirt
  track / runway / rug is not scored and is returned in `suppressed_spans` with its reason; the result is flagged
  `ground_structures_suppressed`. The parser is skipped when no span reaches the lower half of the frame, and the
  gate fails open (`ground_gate_unavailable`) when the parser cannot load. SegFormer-B0 was rejected because of its
  NVIDIA research-only licence.
* **Field guidance** (`app/services/reasons.py`): per-span `action` (`wire_action`) and photo-level
  `retake_guidance` (`retake_summary`, when ≥ 40% of the non-drop spans are unmeasured).
* **Field screener redesign** (`app/web/field.html`, `assets/field.css`, `assets/field.js`; `viewer.js`
  `setOpacity`): dark theme by default with light toggle, Geist, gold accent, glass panels; hero verdict card
  (HAZARD DETECTED / REVIEW REQUIRED / NOT AT RISK / RETAKE PHOTO, worst CRS, measured vs total spans, one next
  action); Original / Detected spans / Full wire mask control with an overlay opacity slider; diagnostics in a
  collapsed drawer; 200–300 ms transitions; shimmer loader.
* `scripts/ui_check.py --mock` (four scenarios from one genuine `/result` payload, Playwright routing), the `v2.13`
  config in `scripts/stress_test_all_conditions.py`, `--no-ground-gate` in `latency_profile`,
  `ml/experiments/v213_enhance.py`, `v213_filters.py`, `v213_unlabelled.py`.
* `transformers` moved into the runtime `requirements.txt` (it was listed only in `requirements-ml.txt` as a v2.12
  experiment dependency, so a runtime-only install would have left the adopted gate permanently unavailable).

### Tested and rejected (all on validation)
* **Always-on CLAHE on L\***: tolerant mAP50 0.0397 -> 0.0356 (strict 0.0068 -> 0.0117).
* **Oriented black-hat tubeness blend**: β = 0.25 / 0.5 / 1 / 2 gave 0.0387 / 0.0367 / 0.0280 / 0.0336
  (`v213_enhance_val.jsonl`).
* **12° bridging**: 0.0357–0.0381 (60 px / 25° gave 0.0407, within noise; 70 px / 25° kept).
* **Closed-loop and isolated-below-network rules**: removed 0 labelled wires on validation but removed real wires on
  unlabelled street photos (validation labels are incomplete for such wires).
* **Upward-bulge rules**: removed labelled wires (1–2 true-positive spans).

### Known limitations
* A fence-rail trace on the problem photo (#18) is not removed: the scene parser labels the railing as building, and
  no cue separates railings from real cables in front of facades. The trace is unmeasured and does not affect the
  verdict.
* The gate's benefit is shown on unlabelled live photos only; the validation crops contain no ground.
* A first M1 latency profile under load (load average 5.8) showed gate-on jumps larger than the 0.26 s parser cost
  (night pole 2.29 -> 4.23 s, demo photo 2 1.98 -> 3.24 s). The < 3.0 s target is open until a quiet re-run.

### Verified
* Windows, Python 3.13.17: **508 passed, 4 skipped** (skips need `ultralytics` / `albumentations`); scoped ruff
  clean. New: `tests/test_v213_ground_gate.py` (fake scene parser, offline), `tests/test_v213_reasons.py` (guidance
  and the API's `suppressed_spans`, flags, `action`, `retake_guidance`), `tests/test_v213_viewer_opacity.py` (Node).
* Browser (Chrome, `--mock`): 16 / 16 scenario × theme × viewport combinations pass, zero console errors
  (`docs/thesis/results/ui_check_v213.json`).
* **Bug found and fixed**: at 390 px the spans table scrolled sideways inside its panel and clipped each span's
  reason and retake instruction; below 520 px each span is now a card.
* Ground gate on validation: 0 spans removed, tolerant mAP50 unchanged at 0.0397 (`v213_filters_val.json`).

### Pending (M1 with the dataset and models)
* **[PENDING QUIET M1 RUN]** latency re-measurement, gate off / on, on a quiet machine.
* **[PENDING QUIET M1 RUN]** whole-pipeline golden check with the gate on
  (`--check docs/thesis/results/v210_pipeline_golden_v29_val.json`).
* **[PENDING QUIET M1 RUN]** the test split, once, on frozen v2.13 (`--configs v2.13`).
* The real-photo mode of `scripts/ui_check.py` with the models installed.

---

## v2.12: complementary backbones, probability fusion, monocular depth (branch `v2.12-multi-model-ensemble`)

Full methods and tables: `docs/thesis/chapter4_v2.12_ensemble.md`. The shipped WireNet-R34 (`ab2353c7`),
`v2.6-baseline`, `7867d56`, `dc1177b`, `5989657`, `7cdb244` and `eff526a` are untouched. New checkpoints are
saved separately as `models/v212_*.pt`.

**Hardware.** Every training, evaluation and latency run in this log from v2.6 onwards was executed locally on one
Apple Silicon M1 laptop with 8 GB of unified memory (MPS GPU backend, shared CPU/GPU memory). No cloud GPU was
used. The memory limit decided which backbones could be trained (MiT-B2 ran out of memory at batch 4), the batch
size (2 for the new backbones) and that only one heavy process ran at a time.

### Added
* `--encoder` / `--arch` in the WireNet trainer and an `.arch.json` sidecar per checkpoint, which the application
  reads (`app/pipeline/wirenet.py: model_arch`); files without a sidecar load as ResNet-34 U-Net, as before.
* `ml.experiments.cache --wirenet` to cache any candidate's validation probabilities.
* `ml/experiments/ensemble_eval.py` (weighted probability fusion; pixel and span scoring),
  `depth_veg_guard.py`, `depth_sag.py`, `ensemble_bootstrap.py`; `transformers` 4.57.6 (added to `requirements-ml.txt`) for Depth Anything V2 Small (Apache-2.0); `--tta` in `ml.experiments.cache`, `--wirenet` in `latency_profile`; the `v2.12` config in `scripts/stress_test_all_conditions.py`.
* **Ensemble specs**: `segmentation.wirenet_weights` may name a JSON spec (`models/v212_ensemble.json`); `load_wirenet` then builds a probability-fusion module. The shipped config is unchanged.
* `scripts/build_master_manuscript.py` and `docs/thesis/MASTER_CHAPTER4_MANUSCRIPT.md`.

### Trained (kept as separate files, not shipped)
* **MiT-B1 U-Net** (`models/v212_mitb1.pt`, SHA-256 `f595ef68…`): 40 epochs, batch 2, 4.66 h; best epoch 32.
* **ConvNeXt-Tiny U-Net** (`models/v212_convnext_t.pt`, `9633cee8…`): 20 epochs, batch 2, 3.39 h; best epoch 16.
  Standard recipe (one-cycle, end-to-end, validation after every epoch). A 14-epoch progressive-unfreezing schedule
  was queued briefly and cancelled before it ran; its code was removed.
* Neither weight file is committed (66 MB and 128 MB); logs and `.arch.json` sidecars are.

### Tested, passed the accuracy gate, not shipped
* **Three-model probability fusion** (R34 0.25 / MiT-B1 0.5 / ConvNeXt 0.25, `models/v212_ensemble.json`).
  Validation span tolerant mAP50 0.0397 -> 0.0537; power / telecom tolerant F1 0.792 / 0.677 -> 0.819 / 0.684. It
  beats R34 in 94.4% of 2,000 paired bootstrap resamples, but every 95% interval includes zero.
  **Not shipped**: 4.71 s median per photo and 5.47 s on the dense pole (R34: 1.75 s / 1.82 s), over the 3.5 s
  limit. Memory was stable (peak 1.49 GB). Kept as an opt-in configuration; the user chose this option.
* **Single-pass fusion** (no flip TTA): fits the time limit but gains less (best 0.0503) and would require
  re-calibrating p0 and the reliability grade, which were measured with R34 + flip. Not adopted.
* **v2.12 ships v2.11's pipeline**: whole-pipeline golden check 22/22 identical; `wirenet-r34.pt` still `ab2353c7`.

### Tested and rejected
* **MiT-B2 and SegFormer-B2.** MiT-B2 ran out of memory at batch 4; SegFormer-B2's 2.31 s per 1024² inference does
  not fit the latency budget. HRNet-W18 was probed but not trained (one ConvNet was budgeted; ConvNeXt was chosen).
* **Depth gate for YOLO vegetation (G1).** It removes the night-sky false positive on P-D67F60, but also 2 true
  vegetation instances on validation; vegetation AP50 0.024 -> 0.0025.
* **Photometric gates (G2: mean L* < 15 and std < 4; G3: G1 and G2).** No validation loss, but they miss the night
  false positive (std L* 5.17).
* **Darkness-only rule (mean L* < 15).** It removes a true tree in low light and 22/31 low-light instances: unsafe.
* **Perspective correction of sag.** Not attempted: high-band share rises with depth recession (3.2% -> 10.4%,
  Fisher p = 0.0039), but relative depth has no metric scale and there is no ground-truth sag. Reported as a
  finding only.

### Known limitations
* The YOLO night-sky vegetation false positive remains.
* Image-space sag is perspective-sensitive (above).
* The new backbones were trained at batch 2, not 4; ConvNeXt-Tiny for 20 epochs, not 40.

### Verified
* 462 tests pass (10 new in `tests/test_v212_ensemble.py`: arch sidecar, ensemble spec validation, fused softmax =
  weighted mean of member probabilities, shipped config still single R34); scoped ruff is clean.
* In-app fusion reproduces the offline fusion on validation (max probability difference 0.0005, 99.998% pixel
  agreement, tolerant mAP50 0.0536 vs 0.0537).
* Whole-pipeline golden check of the shipped configuration: identical on 22/22 validation photos.
* Test split, run once on the frozen v2.12 (`v212_spans_test.jsonl`): clean 0.0204 / 0.0469, low light
  0.0053 / 0.0222, synthetic night 0.0091 / 0.0313, every metric field identical to v2.11. No fusion, backbone or
  vegetation guard was run on test.
* Housekeeping: six stale background wait loops from earlier sessions (their `pgrep` patterns matched themselves)
  were found and stopped; they ran no computation.

---

## v2.11: dual-polarity wire detection, full-mask exposure, field inspection console (branch `v2.11-field-ui`)

Full methods and tables: `docs/thesis/chapter4_v2.11_field_ui.md`. No model was retrained and no new checkpoint
was made. WireNet (`ab2353c7`), `v2.6-baseline`, `7867d56`, `dc1177b`, `5989657` and `7cdb244` are untouched.

### Accepted
* **Dual-polarity WireNet inference** (`segmentation.dual_polarity`). On dark photos (mean L* <= 40) WireNet also
  runs on the photo with L* inverted, and the inverted pass is used if it finds >= 1.2x the wire pixels. Root
  cause: at night conductors are lighter than the sky, but every training photo has dark wires on a bright sky.
  - Validation: 0 of 176 daytime cases switch, and clean and low light are byte-identical (golden check 22/22).
  - Synthetic night (validation): tolerant mAP50 0.0051 -> 0.0312.
  - The user's dense night pole P-D67F60: 12 -> 27 spans (8 -> 18 measured); wire pixels 9,694 -> 23,464.
* **Everything the model detected is exposed.** The full WireNet class mask is served at
  `/api/analysis/{id}/wiremask.png` with coverage statistics. Every unmeasured span carries a reason code and a
  plain-language reason (`app/services/reasons.py`); per-span vegetation clearance, telemetry and the corroboration
  parameters are in the API.
* **`/workspace` rebuilt as an inspection console:**
  - a daylight slate theme scoped to the field page;
  - a 58/42 viewer and detail split, with layer toggles and zoom;
  - a telemetry bar;
  - a retake banner, then hazards, then the verdict and KPIs;
  - an urgency-grouped span table, linked both ways to the canvas;
  - collapsed diagnostics;
  - action-first ordering on phones.

### Tested and rejected
* **Hysteresis thresholding** of wire probability: pixel recall 0.80 -> up to 0.89, but tolerant mAP50
  0.0397 -> 0.018-0.028, because neighbouring wires merge.
* **External datasets:** WireSegHR (no licence; generic wire class; only 420 test images released), MPCD (aerial)
  and railway OCS (catenary) do not pass the gates. No fine-tuning was done.

### Known limitations
* The night gain is measured on a synthetic night condition; the real night photo has no labels.
* YOLO vegetation is unvalidated at night and marks dark sky as vegetation on P-D67F60.
* Dark photos pay two extra WireNet passes.

### Verified
* 446 tests pass (15 new in `tests/test_v211_field_ui.py`); ruff is clean.
* Headless Chrome (Playwright, `scripts/ui_check.py`) on 5 photos and at phone width: 0 console or page errors;
  all layer toggles, table->canvas and canvas->table selection work; PDFs are valid; no horizontal overflow.
* Test split (one run; `v211_spans_test.jsonl`): clean 0.0204 / 0.0469 and low light 0.0053 / 0.0222, both identical
  to v2.10. Synthetic night: tolerant mAP50 0.0090 -> 0.0313, power recall 0 -> 0.217. Telecom recall rose only
  0.003 -> 0.025 (validation: 0.106).
* `scripts/ui_check.py` re-run after the last UI fixes: all 5 cases pass. Verified in the browser: the vegetation
  toggle is off when disabled; on a phone the order is banner, verdict, viewer, table; there is no focus outline;
  the intake form is centred.

---

## v2.10.1: live-server HTTP 500 fix and universal image ingestion (branch `v2.10-hardening`)

**Root cause of the HTTP 500.** Every upload to the live server failed at the first database insert:
`sqlite3.OperationalError: table analyses has no column named submitted_via`. The persistent database in
`var/` predated the dual portals, and `migrate()` only knew one column by name (`vegetation_json`). Tests
and `scripts/server_check.py` always build a fresh database, so neither could see it. The traceback is
kept in `runs/inspection_server_http500.log`.

### Fixed
* **Schema migration** (`app/core/database.py`, `add_missing_columns`): every ORM column missing from an
  existing SQLite table is added at startup, and existing rows are backfilled from the column's default. A
  future NOT NULL column without a default fails loudly at startup with its name, not on every request.
  The live database was backed up first (`var/backups/wireframe-pre-v210-migration.db`, integrity ok);
  its 4 existing analyses were backfilled with `submitted_via = 'admin'`.
* **Universal ingestion** (`app/pipeline/acquisition.py`):
  - `to_rgb` normalises every Pillow mode to 8-bit RGB:
    - EXIF rotation applied;
    - 16-bit scaled, not clipped;
    - alpha composited over white;
    - CMYK, palette, greyscale and 1-bit converted;
    - multi-frame files use their first frame.
  - BMP and GIF are now accepted.
  - Photos are downscaled to 2048 px on the long edge (`acquisition.max_side_px`). The stored copy is the
    analysed image, so report overlays line up.
  - The upload cap is now 64 MB (was 40).
  - Photos below 320 px or blank are **accepted and flagged**. The reliability grade turns them into
    INCONCLUSIVE - RETAKE PHOTO with the reason. `scripts/batch_inspect.py` still rejects them into
    `rejected.csv` (`reject_low_quality`).
  - Non-images remain a clean 422.
  - Every reader of a stored upload (worker, PDF, admin) uses the same decoder (`load_rgb`).
* **The server now analyses exactly the uploaded pixels.** GPS removal used to re-encode every JPEG
  (`quality="keep"`). That changed pixels slightly, and through the verdict instability of v2.10 it flipped
  a demo pole from HIGH to NOT AT RISK. Metadata is now removed at the JPEG segment level: EXIF is rebuilt
  without GPS, XMP and IPTC are dropped, and the image data is copied byte for byte. The decoded pixels are
  checked to be identical before the copy is used.
* **Strict JSON:** `_jsonable` handles numpy scalars, bools and arrays, NaN and Inf, `Fraction`, `Decimal`,
  bytes and non-string keys. It is applied to every JSON column and to the API result. Unhandled exceptions
  are logged with a traceback and returned as structured JSON.

### Verified
* 426 tests pass, including 17 new HTTP-upload regression tests (`tests/test_v210_ingestion.py`):
  - every format and mode;
  - EXIF rotation;
  - 64x64, blank and 4000x3000 RGBA photos;
  - non-image input;
  - the exact legacy schema of the live database;
  - JSON sanitising;
  - lossless GPS stripping.
* **Live server over real HTTP** (`scripts/live_upload_check.py`, `docs/thesis/results/live_upload_check_v210.json`):
  4 demo samples, plus 64x64, 4000x3000 RGBA PNG (37.7 MB), greyscale and blank. Every upload returned 202
  and its result and PDF 200, with 0 HTTP 500s.
* The whole-pipeline golden check against v2.9 is still identical on 22/22 validation photos.

---

## v2.10: glare gate, output-identical latency rewrite, scoped lint (branch `v2.10-hardening`)

Full methods and tables: `docs/thesis/chapter4_v2.10_hardening.md`. No model was retrained. WireNet
(`ab2353c7`), the span constants and the corroborated-verdict rule are unchanged. `v2.6-baseline`, `7867d56`,
`dc1177b` and `5989657` are untouched. Rules were derived on validation; the test split was used once.

### Accepted
* **Photometric glare gate** (`quality.glare_gate`): near-white share >= 0.6 and thin-line contrast < 15 L* units
  -> Retake recommended, so NOT AT RISK becomes INCONCLUSIVE and a found hazard is kept with a note.
  - Chosen over an exposure-only gate and a model wire-density gate. On validation it has recall 1.0,
    specificity 0.78 and leave-one-photo-out accuracy 0.86.
  - Validation: silent NOT AT RISK under glare 5 -> 0; clean photos unchanged; 3 of 22 bright-sky control
    photos made INCONCLUSIVE.
  - Test: silent NOT AT RISK under glare 7 -> 3; span-dropout cases 2 -> 0.
* **Latency.** Span assembly no longer scans the full frame per fragment: single-pass label coordinates,
  bounding-box masks, vectorised stitching in the original loop order. Proven output-identical to v2.9
  (whole-pipeline digest, 22/22 validation photos; reference written by v2.9 from a git worktree).
  Dense poles are 35-46% faster in a paired benchmark; the 81-span pole takes 2.3 s on a quiet machine
  (v2.9: 4.58 s).
* **Scoped lint:** `[tool.ruff] extend-exclude` + `force-exclude`; `.agents/`, `.claude/`, virtualenvs and
  data are never linted or fixed.
* `ml/stress.py` `brightsky` control condition (validation only; not in the 8-condition benchmark).
  `scripts/server_check.py --repeat` reports medians and the machine load.

### Tested and rejected
* **Exposure-only glare gate:** rejects 22 of 22 bright-sky photos. Kept as a documented policy option.
* **Model wire-density gate:** misses 2 of 5 silent cases.
* **Borderline band around the corroboration threshold:** does not stabilise verdicts (41% -> 47-50% changed)
  and gives back up to half of v2.9's NOT AT RISK verdicts.

### Open, quantified
* **Verdict instability.** With at least 80% of spans retained, mild degradations change the pole verdict in
  41% of validation cases (46 of 111). The motivating glare photo from v2.9 is this case, not span dropout.
  A fix needs a steadier per-span sag measurement.
* **Latency under sustained load.** On this fanless laptop the CPU stages slow by up to 2-3x under sustained
  load and OS background activity. The dense pole measured 4.0-4.9 s in a median-of-3 run at load average
  about 3.8. The < 3.5 s target is met for single uploads on a quiet machine, not guaranteed under load.

---

## v2.9: corroborated pole verdicts, demo output, three negative results (branch `v2.9-refinement`)

Full methods and tables: `docs/thesis/chapter4_v2.9_refinements.md`; defense reference:
`docs/thesis/defense_summary_v2.9.md`. No model was retrained. WireNet (SHA-256 prefix `ab2353c7`), the YOLO
detector, `v2.6-baseline`, `7867d56` (v2.7) and `dc1177b` (v2.8) are untouched. Rules and constants were
decided on validation.

### Accepted
* **Corroborated pole-level sag aggregation** (`hazard.sag_rule: corroborated`). A band sets the verdict only
  when more spans are in it than the per-span test-retest false-alarm rate explains: a one-sided binomial
  test, alpha = 0.05. p0 (high 0.050, medium-or-above 0.090, all scored spans, 492 pairs) was measured on validation by re-measuring each
  photo mirrored. Uncorroborated spans are listed as NOTE lines; the vegetation rules are unchanged.
  Validation verdicts, high / moderate / not at risk: 17 / 3 / 2 -> 2 / 10 / 10; test: 18 / 3 / 0 -> 7 / 7 / 7.
* **Reliability reasons**: every grade below A names the cause (glare, darkness, too few wire pixels,
  camera shake, focus, low light, model uncertainty).
* **Diagnostic figure v2**: grade badge and reason, class and level legends, S/D labels, clearance as
  "~m +/- u", and a strip of verdict reasons. Also in the web screener and the PDF.
* **Motion-coherence measurement** (content-normalised structure tensor). It separates 22/22 synthetic
  motion photos from 22/22 defocused ones. It is used only for the reliability reason.
* **`scripts/server_check.py`**, a repeatable end-to-end check.

### Tested and rejected (code kept, switched off)
* **Motion-blur router** (no unsharp masking under directional blur): +0.021 power pixel IoU, but span
  tolerant mAP50 0.0177 -> 0.0098-0.0143 on validation motion photos.
* **JPEG deblocking**: tolerant tie, strict 0.0058 -> 0.0043.
* **Service-drop split by chord inclination (> 35 deg)**: a visual audit of 24 validation "drops" found
  pole-to-pole spans seen in perspective. The low-hanging-drop proxy that depends on it is off too.
* **X-junction perfect matching**: identical to the greedy rule with v2.8's tangents (0.0397); worse with
  far tangents (0.0353).

### Bugs found and fixed
* The blur fallback was coupled to the unsharp decision: routing a photo away from unsharp disabled its
  fallback (`runner._degraded`).
* The blur fallback counted a classified "service drop" as a failed wire (`runner._measured`).
* Steep fragments were classified before the 100 px precision gate.
* The motion measure's padding mask treated any grey pixel near the letterbox value as padding; padding is
  now whole rows and columns.

### Test-split disclosure
The test split was evaluated more than once in this version. No threshold or rule was chosen from it, and
every superseded run is kept in `docs/thesis/results/superseded/`:
- A first locked run differed slightly from v2.8 (8-condition tolerant mean 0.0355 against 0.0360). Tracing
  it found the drop/fallback coupling bug.
- Validation then exposed the drop misclassification.
- The final segmentation run uses the configuration in this commit and reproduces v2.8 exactly.
- The verdict layer was re-run on test once more, after p0 was re-measured on validation over all scored spans.
  Test verdicts were 6 / 7 / 8 with the earlier subset value, and 7 / 7 / 7 with the final value.
- Server check: 1.34-4.58 s from upload to result; 13 of 14 under 4.5 s (the 81-span pole: 4.58 s). All 14 PDFs
  were valid.

### Housekeeping
`ruff check --fix .` was run once over the whole working tree. It also reformatted two untracked files
outside the project (`.agents/skills/caveman-compress/scripts/benchmark.py`, `compress.py`): lint-only
changes; both still compile.

---

## v2.8: span assembly, hazard interpretation, robustness (branch `v2.8-overnight-sprint`)

Full methods, ablations and limitations are in `docs/thesis/chapter4_v2.8_autonomous_upgrades.md`.
No model was retrained: the v2.7 WireNet weights (SHA-256 prefix `ab2353c7`) and the YOLO detector are
unchanged. Constants were chosen on the validation split; the test split was used once, for the final
tables.

### Accepted (each measured on validation, and each passing the team's guard of >= 97% of v2.7's
### power/telecom tolerant pixel IoU)
* Span stitching: collinear gap bridging (70 px, 25 deg) plus the coordinate-descent constants.
  Validation tolerant mAP50 0.0283 (v2.7) -> 0.0397, power spans per label 1.84 -> 1.24.
* Pre-processing: edge-preserving denoise (bilateral filter, triggered by noise or JPEG blockiness);
  the CLAHE darkness trigger turned off (dark-triggered CLAHE cost 0.04 power IoU in low light); flip
  TTA for WireNet. Mean over 8 stress conditions: 0.3788 -> 0.3872.
* CRS measurement-precision gate: chord >= 100 px, derived from sigma / L <= 0.25 delta_lim. Not tuned
  to change verdicts.
* Hazard verdict (AT RISK - HIGH / MODERATE / NOT AT RISK) with plain-English reasons, the team's
  vegetation rules, an approximate clearance with an explicit uncertainty range, and an informational
  congestion index. Chapter III's equations are unchanged.
* Reliability grade A / B / C / Retake recommended from R = 1 - (U + D)/2. Spearman rho 0.64 with
  per-image accuracy; thresholds calibrated on validation.
* Diagnostic three-panel figure embedded in the PDF; batch street-audit CLI with a LabelMe
  review queue; 8-condition stress benchmark script.

### Tested and rejected
* Multi-scale Retinex: lowered low-light and glare accuracy.
* Multi-scale TTA: +0.006 or less at 2-3.5 s per image.
* Topological loose-end detector: about 10 candidates per photo, precision below 0.03. Shipped
  disabled as a research module.
* Pseudo-label self-training: not attempted, because no candidate set reached the required > 0.75
  precision.

### Honest findings
* "Always high" is only partly fixed. Fragment noise is removed (high-band spans per split
  90 -> 46 val, 120 -> 75 test), but 17 of 22 val and 18 of 21 test photos still read high. The
  causes are the placeholder delta_lim (high band = sag ratio > 2.4%), a per-photo worst-of-about-30-spans
  rule (P(>= 1 high) is about 0.88), and CRS not separating drop and service wires from spans. These
  were not tuned; they are left for the engineer's calibration (H3) and a team decision on aggregation.
* Glare is unsolved for every variant (power IoU about 0.08); the grade asks for a retake.
* Per-image false positives are largely unlabelled real wires in dense bundles (label
  incompleteness).

### Verification
* 351 tests passed (294 in v2.7), `ruff check` clean, the field UI's JavaScript parses.
* WireNet weights byte-identical to v2.7 (SHA-256 prefix ab2353c7 before and after).
* Test split, 8 conditions: tolerant mAP50 0.0298 (v2.7) -> 0.0360 (v2.8), strict 0.0196 -> 0.0192 (a tie).
  Regressions: JPEG and glare (strict), and motion blur is still best with enhancement off.
* Server end to end: 16 uploads; latency 1.20-3.45 s (median 2.83 s); all PDFs valid.
  Fixed during the check: unreadable photos with no wires read NOT AT RISK; they now read INCONCLUSIVE - RETAKE PHOTO.

---

## v2.7: 1024 px, blur resilience, full-resolution wire segmentation (WireNet)

All figures, tables and limitations are in `docs/thesis/chapter4_v2.7_results.md`; raw results are in
`docs/thesis/results/`. The test split was used once, for the final figures. This section records what
changed in the system and why.

### Rollback point
* `010d11b` (tag `v2.6-baseline`): the v2.6 code and model, force-added past `.gitignore` at the
  user's request. Nothing has been pushed.

### Data
* The 145-image span-level export (a superset of the earlier 99) is the only source. It is curated
  to 1024 × 1024 letterboxes ("Fit", padding 114 to match inference). The Roboflow default is black
  padding; 114 is used deliberately so training and inference see the same padding.
* `ml/curate.py`: `--imgsz` letterbox output with polygon transform; external sources default to
  `split: train_only`, so neither they nor any near-duplicate group they belong to can reach val or test.
* External search: no source passed the gate (`ml/sources.yaml: rejected`). The thesis cites no
  Kaggle dataset, and no Kaggle token is present.

### Blur and low-contrast resilience
* `app/pipeline/enhance.py`: unsharp masking is blur-adaptive — amount 0.6 → 1.5, radius ×1 → ×3 and
  detail threshold ×1 → ×0.25 as the variance of the Laplacian falls below the blur threshold. **Bug
  fixed:** the fixed-parameter version left strongly blurred photos unsharpened (gain 1.0).
  `enhance_strong` is the fallback profile.
* `app/pipeline/runner.py: finalize_detections`: blur fallback on degraded photos. Ultralytics'
  `augment=True` is a no-op for segmentation heads (its test-time augmentation runs only when the head
  is exactly `Detect`), so the fallback adds its own mirrored pass, merged by mask NMS. It is shared
  with `ml.evaluate`, so evaluation measures what the screener does.
* `preprocess(..., model_size)`: the model input (1024) is separate from the VNI gray path (640).
  A test pins that the VNI does not change with the model's input size.
* `ml/train.py`: Albumentations field degradations (motion, defocus and Gaussian blur, CLAHE, flatter
  contrast), set inside the trainer because `model.train()` rejects the argument; perspective and shear
  set to 0. `--resume` keeps the custom trainer, which it previously dropped.
* `ml/degrade.py` and `ml.evaluate --degraded / --no-enhancement / ultralytics`: the degraded test pass.

### Training on Apple Silicon
* YOLO11s-seg at 1024, batch 2, swapped (7 s → 44 s per step). Batch 1 with Ultralytics' nominal-batch
  accumulation (`nbs` 64) keeps the effective batch at 64 and ran at 1.1 s per step. The MPS
  allocator still crept up, from 4.4 to 6.4 GB by epoch 8; `release_memory_steps` (`empty_cache`
  every 10 steps) kept it flat at 3.4 GB.
* Early stopping ended the first 1024 run on an epoch-1 "best" with 1.3% mask recall. Training was
  continued from epoch 16, and its best checkpoint, global epoch 22, is deployed.

### Thin-wire masks: diagnosis and the fix
* A perfect 1/4-resolution mask of a 2.1 px wire reaches a median IoU of 0.40, and only 17–21% of
  wire instances can reach 0.5, so YOLO-seg's wire mask AP is capped. SAM 2.1 with the true boxes
  reaches a median IoU of 0.04–0.08 on wires.
* **WireNet** (`ml/wirenet.py`, `app/pipeline/wirenet.py`): a U-Net with a ResNet-34 encoder that
  predicts every pixel at full resolution, trained on wire-dense 512 tiles with cross-entropy + Dice
  + clDice. Best validation pixel IoU: power 0.265 and telecom 0.232 (tolerant 0.525 and 0.418).
* At the operating threshold the 1024 detector proposes almost no wires (test: 0 power and 4 telecom
  spans), so refining its proposals cannot recover wires. Spans are therefore extracted from WireNet's
  class map (`app/pipeline/wire_instances.py`). On test, tolerant mAP50 rises from 0.0199 to 0.0375
  and strict from 0.0198 to 0.0204; v2.6 scores 0.0005.
* Config: `segmentation.wirenet_weights`, `wirenet_instances: skeleton`, `imgsz: 1024`, and
  `conf_min: 0.20`, chosen on the validation split and written with `update_config_values()`. The
  `enhancement.working_long_side` is 1024.

### Abandoned-wire (Stage 2): attempted, not adopted
* `ml/wirenet_stage2.py` covers label tightening, oversampling, copy-paste, hanging-end synthesis,
  focal loss with class weights, logit adjustment, a power/telecom guard and coverage-F selection.
  Four attempts were made; each either over-predicted abandoned-wire or degraded power and telecom.
  Attempt 3 hit 6/6 validation instances at 0.01 precision, and no inference bias recovered precision.
* The deployed WireNet is Stage 1. The reasons, and what more labelled data is needed, are in Chapter 4
  §4.3.6. All Stage 2 checkpoints are kept in `runs/wireframe/_keep/`.

### Verification
* **294 tests passed**, `ruff check` clean on app, ml, tests and scripts.
* The end-to-end server check passed: `HybridSegmenter` ready with the four classes; 8 uploads (4
  standard, 4 degraded) completed in 1.4–3.9 s with valid PDFs; enhancement flags present on degraded
  uploads (`docs/thesis/results/server_check_v27.json`).
* NumPy-2 audit regex fixed: it had flagged the `np.ptp()` function, which NumPy 2 keeps.

### Open (see Chapter 4 §4.4–4.5)
* Abandoned-wire needs several hundred tightly traced instances. Spans fragment: 466 predicted against
  253 labelled for power. Every per-photo maximum CRS is "high" because of short fragments; scoring
  should require a minimum chord length (not yet implemented). The `conf_min` comment still says
  "provisional"; `conf_provisional: false` is the authoritative flag.

---

## v2.6: trained on Apple Silicon (MPS)

### `ml/train.py`
* This Mac (Apple M1, 8 GB unified memory, MPS) is the training machine; the CUDA workstation assumptions were removed.
  `DEVICE_DEFAULTS["mps"]`: batch 4, workers 0, no cache, AMP off, a checkpoint every 5 epochs. Explicit options win.
* `gc.collect()` and `torch.mps.empty_cache()` run after every training epoch (`on_train_epoch_end`) and after every
  validation (`on_fit_epoch_end`).
* `on_model_save`: whenever an epoch sets a new best validation mask mAP50-95, `best.pt` is stripped of optimizer
  state and atomically replaces `models/wireframe-yolo11m-seg.pt`. A usable model is on disk throughout the run.
  Ultralytics also counts a tie as a new best, so the early epochs that tied at 0 were copied too.
* The `--smoke` option from v2.5 was removed. The F1 guard now only withholds the confidence threshold; the weights are
  installed either way.

### The run (2026-11-14)
* **YOLO11m-seg:** ran out of unified memory (`kIOGPUCommandBufferCallbackErrorOutOfMemory`) on the first batch at
  batch 4. As instructed, the base was switched to **YOLO11s-seg**. The installed file keeps the name
  `wireframe-yolo11m-seg.pt` because that is the path the server loads; the model inside is the small one.
* **YOLO11s-seg:** 30 epochs, imgsz 640, MPS, 1.2 h, about 4.5 GB. The best epoch was 12 (mask mAP50-95 0.028); epochs
  13–30 did not beat it.
* **Validation at NMS IoU 0.45** (15 images): mask mAP50 0.086, mask mAP50-95 0.026, box mAP50 0.141. The F1-optimal
  confidence threshold is 0.190 (mean mask F1 0.125), written to `segmentation.conf_min` with `update_config_values()`;
  `conf_provisional` is now false.
* **Per class (mask, Ultralytics' operating point):**

  | Class | P | R | AP50 | AP50-95 |
  |---|---|---|---|---|
  | Vegetation | 0.64 | 0.33 | 0.35 | 0.11 |
  | abandoned-wire | 0 | 0 | 0 | 0 |
  | power-distribution-lines | 0 | 0 | 0 | 0 |
  | telecom-cable | 0 | 0 | 0 | 0 |

  **All mask AP comes from Vegetation; no wire class reaches IoU 0.5 against the span polygons.** The model does
  detect wires (the server check below finds and fits some), but its masks do not overlap the 1–3 px span outlines
  well enough to count. Likely causes: the mask head works at 1/4 of the input resolution, so a 2 px wire is half a
  mask pixel; only 69 training images; and curation rescales images to a 640 long side, thinning the wires further.
  This model is good enough to exercise the field screener, but it is not a result for Table 3.10.
* Log: `runs/wireframe/yolo11s-seg-640-mps/wireframe_validation.json` (git-ignored).

### Server check
The FastAPI app was started with a temporary data directory. It loaded `models/wireframe-yolo11m-seg.pt` with classes
`['Vegetation', 'abandoned-wire', 'power-distribution-lines', 'telecom-cable']` and analysed test-split images
through the public upload: 2 complete, 2 `fit_failed`, 1 refused. The refused image was curated down to 640 × 271,
which is below the 320 px minimum; full-resolution field photos are not affected.

### Open
* The `conf_min` line still carries its original comment ("provisional; to be set on the validation set..."), kept
  because comments are preserved. `conf_provisional: false` is the authoritative flag.
* Next steps for the wire masks: train at `--imgsz 1024` with `--batch 2` (the masks become 1/4 of 1024), curate with
  a larger working long side, and add relabelled images.

---

## v2.5: revised taxonomy, span-level dataset, vegetation routing

### Taxonomy (the team's revision, 2026-11-14)
* Classes, in dataset index order: `Vegetation`, `abandoned-wire`, `power-distribution-lines`, `telecom-cable`
  (`app/core/config.py`: `CLASSES`; `WIRE_CLASSES` = the three sag-scored classes). The names are the dataset's own,
  so a trained checkpoint's `names` match without a mapping layer. `telecom-cable` now includes fiber optic drops;
  `abandoned-wire` replaces `dead_wire`.
* `config/wireframe.yaml`: `crs.class_weights`, `crs.delta_lim0` and `engineering.conductors` are keyed by the three wire
  classes. The `fiber_optic_drop` conductor entry was removed, so fiber drops now use the telecom-cable conductor values.
  This affects Equation 9 for fiber drops and should be revisited with the consulting engineer. Key renames were made
  as line edits, because `update_config_values()` changes values only; a diff confirmed that every comment is
  preserved except the one on the removed entry. Value changes (the VNI calibration below) went through
  `update_config_values()`.
* A model trained on the old taxonomy is rejected by the loader (tested). `ml.train` refuses a dataset whose class
  list or order differs.

### Vegetation (new; not a Chapter III equation)
* Vegetation masks are separated before Algorithm 4. They are never skeletonised, fitted or scored, and they never
  enter CRS, MSPI, wire counts or wire numbering. A test confirms that adding vegetation leaves every CRS and the VNI
  unchanged.
* A wire is flagged `vegetation_contact` when at least `min_contact_share` (0.02) of its mask lies within
  `contact_margin_frac` (0.01) × the image's longer side of a vegetation mask. Both values are a new provisional
  `vegetation` block. The analysis is also flagged, and the regions are stored in `analyses.vegetation_json` with the
  wires each one touches. The field portal shows a note and a Vegetation layer; the PDF counts vegetation areas.
* Vegetation does **not** change any score. How encroachment should weigh into risk is a methodology decision for the
  team. Chapter III's equations are unchanged.

### Database migration (`app/core/database.py: migrate`)
* SQLite cannot alter a CHECK constraint, so on startup a `detections` table that still lists the old class names is
  rebuilt (rename, create, copy, drop, `foreign_key_check`) and stored names are mapped
  (`fiber_optic_drop`→`telecom-cable`, `dead_wire`→`abandoned-wire`, …). Stored scores are kept as computed.
  `vegetation_json` is added when missing. The migration is idempotent and tested on a legacy schema; it was also
  checked on a copy of the live `var/wireframe.db` (10 detections, integrity ok).

### Dataset: the 2026-11-14 export
* The export at the path given (`Wireframes-Prelabelled-Datasets.yolov11`) contains **99 images, all in `train/`**,
  with "No pre-processing", so each photo keeps its own size. It is not 640 × 640 Fit. The `v2i` export in the same
  folder is 640 × 640 Fit with 965 images, but its labels are the v1 fragments (18,554 / 29,300 instances, 72% / 86%
  fragments), so the span-level 99 were used.
* It was not copied verbatim into `datasets/wireframe_seg/`: its `data.yaml` points at `../train/images` (outside the
  folder) and its val/test splits are empty. It replaced `datasets/raw/Wireframes-Prelabelled-Datasets/` (the v1 copy;
  the v1 export is still in Downloads), and `python -m ml.curate` regenerated `datasets/wireframe_seg/`. Stale
  `*.cache` files were deleted.
* Audit (`datasets/wireframe_seg/audit.json`):
  * **Labels:** 0 corrupt label lines, 0 unmapped labels, 0 exact or near duplicates, 0 queued for annotation,
    all 4 classes present.
  * **Instances:** Vegetation 170; abandoned-wire 20 (70% fragments); power-distribution-lines 1,264 (median extent
    0.51, 11% fragments); telecom-cable 1,644 (0.46, 11%). Median 29 per image; 0 images mostly fragments
    (v1: 844 of 962).
  * **Geometry:** 99 distinct sizes, aspect ratios 0.76 to 3.87, 0 letterboxed: undistorted. Curation rescales to a
    640 long side (aspect kept), as at inference.
  * **Split:** 69 / 15 / 15, balanced by density stratum.
  * **Privacy:** `manual_only` (no detector under OpenCV 5), so all 99 are listed for manual review.
* `data.yaml` no longer writes an absolute `path`, so Ultralytics resolves the splits against the file's own folder
  and the dataset survives being copied to the training PC. Verified from two working directories.
* The audit gained `corrupt_label_lines`, `unmapped_labels` (previously a malformed line aborted the run and an
  unknown name was silently dropped) and `image_geometry`.
* **Caveats:** 69 training images is very small for YOLO11m-seg (about 22M parameters), and abandoned-wire has
  20 instances in total, so per-class metrics on a 15-image validation split are not reliable.

### VNI recalibrated
* The previous calibration set (v1 train split) is no longer in the project and may overlap the new test split.
  Recalibrated on the 69 unenhanced originals behind the new train split: q01 = 0.034001, q99 = 0.243397 (r_L 0.15),
  written with `update_config_values()`. Still provisional (Section 3.4.6). The synthetic sample now reads
  VNI 0.146 and MSPI 0.419.

### Training
* Training targets this Apple Silicon Mac (MPS); there is no CUDA workstation. See v2.6.

---

## v2.4: the team dataset, real VNI calibration, illumination-invariant VNI

### Dataset integrated: `Wireframes-Prelabelled-Datasets` v1 (team Roboflow export, CC BY 4.0)
* Copied to `datasets/raw/Wireframes-Prelabelled-Datasets/` and curated by `python -m ml.curate` into `datasets/wireframe_seg/` (git-ignored):
  - 965 photos in; 3 near-duplicates removed; **962 kept**
  - split **673 / 145 / 144**, balanced across the density strata (instance-count tertiles 36 and 56)
  - every label is a polygon, so the bounding-box router queued none
  - `Vegetation` dropped; `abandoned-wire` → `dead_wire`; `power-distribution-lines` → `power_distribution_line`; `telecom-cable` → `telecom_cable`
* **Label-quality audit (new: `audit.json → label_quality`).** §3.4.3 requires one polygon per visible span. This export doesn't meet that rule:

  | class | instances | median span (share of image) | fragments (< 25 % of image) |
  |---|---|---|---|
  | power_distribution_line | 18,531 | 0.12 | **69 %** |
  | telecom_cable | 29,146 | 0.06 | **84 %** |
  | dead_wire | 103 | 0.12 | 83 % |
  | fiber_optic_drop | **0** | — | — |

  Median: **45 polygons per photo**, and 844 of 962 photos are mostly fragments. Visual inspection also showed fragments offset from the wires they label, and high-voltage lines on crossarm insulators labelled `telecom-cable`.
* **Consequences, stated plainly:**
  1. A model trained on fragments learns to predict fragments, and a fragment's centerline is too short for the catenary fit (§3.6.5) to measure sag. The CRS needs span-length masks.
  2. With **no fiber-optic examples**, that class cannot be learned, and its AP is undefined.
  3. **103 dead-wire instances** is too few for a reliable class.
  4. The export was resized to **432 × 432 with "Stretch"**: catenary shapes are distorted, and images are upsampled to 640 for training. Re-export with "Fit (black edges)" or no resize at ≥ 640 px.
* **What would fix it:** merge each wire's fragments into one polygon per span, re-check the classes against Table 3.5, and add fiber-optic and dead-wire examples. The audit reports progress on each.

### Source register (`ml/sources.yaml`)
* The team's Universe URLs give the workspace and project slugs for Power Lines, reference-images-2, power_lines_6_10, Anomaly Detection, YOLOv8 test and Cable Detection. Four sources are "local catalog entries" with no URL.
* **Licences and versions are still blank.** Roboflow refuses automated page fetches (HTTP 403 on all six), and nothing was guessed. The licence gate keeps those sources out until a person copies the licences in.

### VNI: illumination-invariant (Equation 3 amended), calibrated on real photos
* The team's specification requires an illumination-invariant VNI. Equation 3 as written isn't (see the finding under v2: the same texture scored 0.47 dark and 0.036 bright). **`vni.remove_mean` is now `true`.** A test confirms VNI_raw is unchanged under any I → a·I + b. **The manuscript's Equation 3 should say the image mean is removed before the Hann window.**
* **q01 / q99 recalibrated:** 0.020767 / 0.186493 at r_L = 0.15, computed with `ml.calibrate_vni` on the **673 unenhanced training-split photos**. Test photos were kept out, as §3.4.6 requires, and enhancement never reaches the VNI. This replaces the made-up placeholders. It's still provisional, because §3.4.6 calibrates on locally captured images; the config records the calibration set.
* Radius study (§3.6.2) on the same photos: q01 / q99 = 0.039 / 0.342 (factor 0.10), 0.021 / 0.186 (0.15), 0.010 / 0.102 (0.20).

### Verification
**249 tests passed**, `ruff check` clean, no NumPy 2.0 removals (static scan).

### Tooling fix
`ml.calibrate_vni --write` and `ml.train` used to rewrite `config/wireframe.yaml` through a YAML round trip, which **deleted every comment**, including the provisional and provenance notes. Both now use `update_config_values()`. It edits only the target lines, keeps all comments, refuses unknown keys, and re-validates the file before writing. A test covers this.

### Training here vs. on the RTX 4060 (superseded in v2.6: the Mac is the training machine)
One YOLO11m-seg epoch on this M1 (8 GB, MPS) took more than 10 minutes, so a full run (up to 200 epochs, early stopping at patience 25) is not practical on this machine. On the workstation:
```
python -m ml.train --data datasets/wireframe_seg/data.yaml        # CUDA, batch 8, the v2.3 recipe
```
The F1 guard (mean validation F1 ≤ 0.05) still prevents installing a model that hasn't learned anything.

## v2.3: field-photo enhancement and the anti-overfitting training recipe

### Already in place before this round (re-verified, not rebuilt)
The dual portals, the landing CTAs, the bounding-box router (`annotation_queue.csv`) and the evaluation suite (Eq. 5, 12–18) were built in v2.1 and v2.2. The whole suite passes again.

### Adaptive enhancement (`app/pipeline/enhance.py`)
* **Steps, in fixed order:**
  1. **Non-local-means denoising** (`cv2.fastNlMeansDenoisingColored`). Its strength scales with the noise level estimated by Immerkær's method (1996); the estimator is tested to recover a known noise level of 10 within 8%.
  2. **CLAHE** on the CIELAB L channel only (8×8 tiles, clip limit 2.0).
  3. **Unsharp masking** with a detail threshold.
  
  Denoising comes first because contrast and sharpening would otherwise amplify noise.
* **`auto` mode** measures each photo (lightness, contrast, clipped highlights, noise level, sharpness) and runs only the steps it needs. The thresholds are flagged `provisional`. The measured quality and the steps applied are stored with the analysis and shown in the admin Acquisition tab.
* **Scope, by design:**
  - The **segmentation input only**. The VNI grayscale path is always built from the unenhanced photo, because §3.6.2 fixes it at a 5×5 Gaussian so that VNI values stay comparable. CLAHE and sharpening would inflate VNI. A test checks that the grayscale array and the VNI are bit-identical with enhancement on or off.
  - **Skipped for reference-mask analyses**, which never run segmentation.
  - **Run at the model's working scale** (640 px long side). It takes 0.41 s on a 12-megapixel photo with all three steps forced.
* **Training/inference consistency:**
  - `ml.curate` applies the same enhancement, at the same scale, to the training images, and records the mode in `data.yaml`.
  - `ml.train` copies that mode into `segmentation.trained_enhancement`.
  - At inference the server adds the flag `enhancement_mismatch` if the preprocessing differs from what the model was trained on. An enhanced input to a model trained on raw images is a domain shift, and usually lowers accuracy.
* **Measured effect, reported honestly:** at the specified clip limit 2.0, CLAHE gives thin, low-contrast wires a **modest** boost. On a synthetic dusk scene, wire-to-sky separation went from 13 to 14 lightness levels. The clip limit exists to stop CLAHE amplifying flat sky. **Whether enhancement improves segmentation is an empirical question:** compare `enhancement.mode: off` and `auto` on the validation set before you adopt it.
* **Not done as briefed:** "sharpening … before Zhang-Suen skeletonization". Skeletonization runs on the predicted binary mask, not the photo, so sharpening can help it only indirectly, through better masks. That is how it is implemented and documented.

### Training recipe (`ml/train.py`)
| Setting | Value | Note |
|---|---|---|
| Mosaic / close mosaic | 0.8 / last 10 epochs | finish on whole, uncut spans |
| Mixup | 0.15 | |
| HSV hue / saturation / value | 0.015 / 0.7 / 0.4 | |
| Perspective | 0.001 | mild keystone, as from a 30–60° camera elevation |
| Scale | 0.5 (gain 0.5–1.5) | |
| Horizontal flip | 0.5 | a mirrored span is still a valid catenary |
| **Vertical flip, rotation** | **0, 0** | **an upside-down catenary is an arch, which the fitter rejects; rotation tilts the vertical that sag is measured against** |
| LR schedule | cosine, 5 warmup epochs | |
| Early stopping | patience 25 on **validation mask mAP50-95** | `MaskMapTrainer` replaces Ultralytics' blended box/mask fitness, so **both early stopping and the choice of `best.pt`** follow mask quality. A unit test with a stub validator checks this. |
| Overlapping masks | off | crossing wires keep separate instance masks |

A 2-epoch smoke run on CPU (synthetic data, `--no-install`) confirmed the trainer class and every argument as applied by Ultralytics 8.4. `models/` was untouched.

### Corrections to the brief
* The evaluation equations are in **§3.10–3.11** (§3.7 is the Operational Framework).
* "Catenary curve RMSE ≤ 1.00 CRS point" is the thesis's **H3**: RMSE between CRS and the engineer's 0–10 severity ratings. It isn't a curve-fitting error. Curve-fit accuracy is reported separately, as the supplementary synthetic benchmark.
* **mAP50-95 has no target** in Table 3.10. It is reported, not judged.
* Risk labels stay **Low / Medium / High** (Table 1.2).

## v2.2: two portals (open field screener and authenticated admin command centre)

### Routing
| Route | Who | What |
|---|---|---|
| `/` | everyone | Landing page. Primary CTA **Launch Field Screener**; a subtle **Admin portal** link in the header and footer |
| `/workspace`, `/workspace/result/{id}` | everyone, no sign-in | **Side A, field screener**: photo → risk card, overlay, one-pin map, PDF |
| `/admin/login`, `/admin`, `/admin/*` | signed-in admins | **Side B, command centre**: Overview, Analyses (+ detail), Sites, Poles (+ history), Models & queue, Exports, Evaluation |
| retired | | the v2.1 console (`/sites`, `/evaluation`, `/analysis/{id}`, `index.html`, `app.js`) now returns 404 |

### API access matrix
| Public (field) | Admin only (`401 auth_required` otherwise) |
|---|---|
| `POST /api/analysis/upload` (rate-limited); `GET /api/analysis/{id}`, `/result`, `/image`; `GET /api/reports/{id}/pdf`; `GET /api/health`, `/api/config`, `/api/evaluation/metrics`; `/api/auth/*` | `GET /api/analysis` (listing), `POST …/retry`, `DELETE …`; all of `/api/dashboard/*` (map, priority, sensitivity, summary); all of `/api/admin/*` |

An analysis id is a 128-bit random capability. A field user sees their own result, or one they were sent a link to, and nothing can be enumerated. **The multi-pole map, rankings and exports are admin-only because §3.14.4 limits access to pole coordinates to the research team.** The landing page therefore shows the GeoJSON schema with the synthetic sample, not live poles.

### Security (verified by `tests/test_portals.py`)
* **Passwords:** scrypt (N=2^14, r=8, p=1, 16-byte salt), standard library. Minimum 12 characters. **No default account:** create one with `python -m scripts.create_admin <username>`, which prompts for the password; resetting it signs the account out everywhere.
* **Sessions:**
  * a 256-bit token in an `HttpOnly; SameSite=Strict` cookie, with `Secure` via `WIREFRAME_COOKIE_SECURE=1` behind HTTPS
  * **only its SHA-256 is stored**
  * 8-hour expiry, and logout revokes it server-side (a replayed cookie is refused)
* **CSRF:** SameSite=Strict, plus an Origin/Referer check on every state-changing admin request and on login.
* **Login throttling:** 5 failures per client and username per 15 minutes, then 429 with `Retry-After`. Unknown usernames take the same time and get the same message, so usernames can't be enumerated.
* **Anonymous uploads:** 20 per client per 15 minutes (`WIREFRAME_PUBLIC_UPLOADS_PER_WINDOW`). They **cannot change an existing pole's barangay or location**; only an admin can correct those. Each analysis records `submitted_via` (`field` or `admin`).
* **Admin pages** send `X-Frame-Options: DENY` and `robots: noindex`.

### Command-centre features
| Requested | Built | Notes |
|---|---|---|
| Advanced metrics | Per-wire table of a, b, c, RMS residual (px and ÷ L), L, δ, δ/L, δ_lim, CRS, pruned spur pixels, point count; power-spectrum PNG with the r_L circle; acquisition envelope and flags | Tooltips explain each quantity in plain language |
| MSPI tuning + live sensitivity | α/β sliders (γ = 1 − α − β) re-rank poles, and the **66-cell sensitivity grid is recomputed against the current slider weights**; click a cell to apply it | `GET /api/dashboard/sensitivity?alpha&beta&gamma` |
| Temporal degradation | Per-pole history: max CRS over time on Low/Medium/High bands, and a table of max/mean CRS, max δ/L and VNI | **Pole-level aggregates:** wires are not matched one-to-one across photographs, and the UI says so |
| Batch & export | Site manifest CSV, wire-measurement CSV (UTF-8 BOM so Excel opens it correctly), executive multi-site PDF (ranking, review state, rank range over the weight grid, disclaimer on every page), batch upload with same-named `.txt` polygons | CSV, not `.xlsx`, to avoid a new dependency; it opens directly in Excel |
| Overrides / review | Confirm, override (with level) or dismiss, with a note **required** for override and dismiss. Stored in `reviews` **beside** the computed scores, which never change. Shown in both portals and printed in the PDF; an unreviewed PDF says "Not yet reviewed" | Supports §3.7: a human reviewer stays responsible |
| Model / reference toggle | Lists checkpoints in `models/`; **Activate** loads and validates (segment task, four classes) **before** swapping; "Require reference masks" switch; queue depth, running and failed items with Retry | Settings persist in `app_settings` |

### Field screener
* One focused flow: drop or choose a photo, optionally add a pole code, barangay and **device location** (browser geolocation, the user's choice), then screen.
* **Progress is real.** "Uploaded" and "location removed" complete when the server accepts the photo, because GPS stripping happens during ingest; "finding wires" completes when the pipeline does. Nothing is simulated.
* The result is a bold **Low / Medium / High risk** card with a plain-language reason ("…scores 7.5 of 10. Scores above 6 of 10 are rated high risk."), the overlay with two toggles, the wire list, a one-pin map and the PDF.
* With no model installed, a calm inline note asks for reference polygons and offers the sample.
* Recent screenings are kept **on the device only** (localStorage).
* At 390 px: no horizontal overflow, and every control is at least 44 px tall.

### Decisions that differ from the brief
* **"Moderate Risk" → "Medium risk":** Table 1.2 of the manuscript uses Low / Medium / High, and the UI matches it.
* **No all-poles map in the open portal:** §3.14.4 (above). The field map shows the submitted pole only.
* **Design skills:** the rules were applied by hand (typography-led, one primary action per screen, hairline tables, no nested boxes, hover and press feedback, reduced motion). No skill scripts were run.

### Bugs found and fixed while verifying v2.2
1. **Thread-unsafe queue hand-off:** `asyncio.Queue.put_nowait` was called from whatever event loop handled the request, so jobs submitted from another loop never woke the worker. `submit()` now uses `call_soon_threadsafe` on the worker's loop.
2. **The overview counted inspections as poles:** a pole photographed three times counted three times. It now uses each pole's latest inspection, as the MSPI does. A regression test was added.
3. **`[hidden]` lost to component `display` rules**, which would have shown the field screener's validation box in field mode and the override-level field during "Confirm". A global rule fixes it.
4. **Raw model errors leaked absolute server paths** ("weights not found: /Users/…"). Errors are now plain language with repo-relative paths.
5. **Field map and viewer measured zero-size containers** because they were built while hidden. They're now shown first, and the viewer re-fits on resize until the user pans or zooms.
6. **Sample runs dropped "Use my location"**, and **the mobile primary button collapsed to 24 px** (`flex: 1` in a column). Both fixed.
7. **`/api/auth/me` returned 401 when signed out**, which logged an error on every visit. It now returns `{"user": null}`; signed-out is a normal state.
8. **Dead code removed:** the v2.1 console, its banner code path, and **110 unused CSS rules** (pruned by a selector-usage scan; Leaflet's runtime classes kept).

### Browser verification
A Playwright run on system Chrome covered:

* landing → **Launch Field Screener** → location → sample → "High risk" verdict
* an anonymous visit to `/admin/analyses` → redirected to login
* a wrong password → generic error
* sign-in → overview → analyses → detail → override recorded → spectrum tab → sites → pole history → models → manifest download → sign-out → login page

The only console entry was the deliberate wrong-password 401.

## v2.1: landing page, evaluation suite, Roboflow and GPU training, self-healing console

### What was added
| Area | Change | Verified by |
|---|---|---|
| Routes | `GET /` landing page, `GET /workspace` console (plus `/analysis/{id}`, `/sites`, `/evaluation`), `GET /api/evaluation/metrics`, `POST /api/analysis/{id}/retry` | `tests/test_v21.py` |
| Evaluation (§3.10–3.11) | `app/pipeline/evaluation.py`: IoU (Eq. 5); P/R/F1 at the operating threshold and IoU 0.50 (Eq. 12–14); COCO 101-point AP, mAP@50 and mAP@50:95 with greedy per-class matching; RMSE, MAE, Pearson, Spearman with average ranks (Eq. 15–18); linear-weighted Cohen's κ and the confusion matrix; 10,000-resample percentile bootstrap CIs; Table 3.10 target checks | `tests/test_evaluation.py`, checked against scipy and hand-computed AP and κ |
| Evaluation runs | `python -m ml.evaluate segmentation, reference, geometry`. Each writes one section of `var/evaluation/report.json`; the API reports `not_run` / `complete` / `stale` (config changed) | `test_evaluation_metrics_not_run_then_reported` |
| Geometry benchmark | 40 synthetic scenes, 200 wires, 0 fit failures. Curve RMSE median **0.27 px** (p95 0.42); sag-ratio error median **1.1%** (p95 2.8%, max 10.7%). **Synthetic, reference masks: measures the geometric layer only** | run on this machine; labelled "supplementary" in the UI |
| Landing page | Hero renders **real pipeline output** (the synthetic sample) with the console's own viewer. The method panels compute Eq. 3–4 and 8–10 **live in the browser**. The evaluation table reads the API. No figure is typed in by hand | `tests/test_web_math.py`: `spectral.js` equals the Python pipeline to 1e-9 (FFT, Hann, VNI, chord sag, CRS, levels) |
| Brand | `app/web/assets/logo.svg`: two poles with crossarms and insulators, a hung catenary with its perpendicular sag, a spectral trace. Replaces the PNG | rendered and checked at 24 / 48 / 192 px |
| Console | Status banner (field / validation / offline) driven by `/api/health`; mode pill; "Run the synthetic sample"; error cards with Retry / Upload with masks instead of raw error text; Metrics view | browser-driven flow (landing → console → sample → result) with **no page or console errors** |
| Self-healing | Health re-checks the weights on every poll, so installing the model switches to field mode **without a restart**. Failed loads are cached by file mtime/size, so a corrupt checkpoint is not re-read every poll. On the switch, the console offers to re-queue failed analyses | `test_retry_*` |
| Upload guard | No model and no labels → **503 `model_unavailable` with a hint, before anything is stored**. Content is validated first, so a bad file is still reported as a bad file | `test_upload_without_model_or_labels_is_refused_before_storage` |
| PDF | Header badge: "VALIDATION MODE · REFERENCE MASKS" or "FIELD MODE · YOLO11-SEG MODEL" | `test_pdf_carries_validation_mode_badge` |
| Roboflow | `python -m ml.curate --download`. The key comes from `$ROBOFLOW_API_KEY` (never stored in the YAML). A source downloads only with workspace, project, version and an allowed licence; anything else is skipped with its reason in `audit.json` | `test_download_uses_env_key_and_skips_incomplete_sources` (fake SDK) |
| GPU training | `ml/train.py` for the RTX 4060 (8 GB; superseded in v2.6 by Apple Silicon defaults): CUDA auto-detect with a clear error and the PyTorch CUDA link if absent, AMP, batch 8 (or `-1` AutoBatch), 8 workers, disk cache, cosine LR, `save_period`, `overlap_mask=False`, validation at IoU 0.45, `wireframe_validation.json` log with GPU, CUDA and cuDNN versions | **smoke-run end to end on CPU** (1 epoch, tiny synthetic set) |

### Requests handled differently, and why
* **"High-contrast edge segmentation" fallback: not built.** Edges cannot be classified into the four Table 3.5 classes. Building and window edges would reach the catenary fitter as "wires" and receive CRS scores, which is the uncalibrated mock your first brief ruled out. The fallback is reference masks plus the 503 guard, the mode badges, and retry.
* **Roboflow slugs:** Table 3.4 gives none. `project` and `version` are prefilled from the Chapter IV **Table 4.6** export folders and marked "verify". "Power Lines" is ambiguous across three checkpoints. Workspace owners and licences are left blank because the licence gate depends on them.
* **"Section 3.7":** the evaluation equations are in §3.10–3.11; §3.7 is the Operational Framework. **Kendall's τ** is not in Chapter III, so it is computed but labelled supplementary next to the specified Spearman ρ.
* **Bounding-box router:** already in place since v2 (boxes go to `annotation_queue.csv`, never converted to polygons). Unchanged, still tested.
* **Design toolchain:** `design-taste-frontend`'s rules were applied: h1 names the category and buyer, one hard fact on the first screen, the product's real UI in the hero, one section per screen, equal content edges, hover and press feedback, a single entrance, no violet, and no invented figures. Its palette-dealing and gate scripts were not run: they generate standalone sites from their own starter kit and would replace WIREFRAME's established tokens.

### Bugs found and fixed during v2.1
1. **The training smoke run installed untrained weights.** The 1-epoch run copied its `best.pt` into `models/`, and the server loaded it as a field model. Analyses scored by it were deleted. Now: training refuses to install or write a threshold when mean validation F1 ≤ 0.05, and `--no-install` / fractional runs never touch `models/`.
2. **Ultralytics 8.4 nests relative `project` paths** under `runs/<task>/`, so the script looked in the wrong place for `best.pt`. It now uses an absolute path and reads `trainer.save_dir`.
3. **The landing page's sticky nav would scroll away** after the first screen: the console stylesheet clamps `body` to 100% height. Unclamped for the landing page.
4. **Uppercase label styles turned δ into Δ** and ρ into P in three places. Labels keep their case now.
5. **The model guard ran before content validation**, so a corrupt upload was told to "attach polygons". It now runs after validation and still before storage.
6. Requesting `/result` mid-run logged a 409 in the browser. The console now checks status first.

### Still open
* No trained model, no curated dataset, no segmentation or reference-agreement figures. Every Table 3.10 row reads "not yet measured", and that is the correct state.
* Workspace owners and licences for the ten sources; ownership of the three "Power-Lines-1" checkpoints.
* The `vni.remove_mean` decision (see the Equation 3 finding below).

---

## 1. Verification status

| Check | Result | How |
|---|---|---|
| Test suite | see the latest section below; `ruff check` clean | `pytest` (Python 3.13.2, NumPy 2.5.4, SciPy 1.18.1, OpenCV 5.0.0, SQLAlchemy 2.1.8, FastAPI 0.162, Ultralytics 8.x, PyTorch 2.x CPU) |
| NumPy 2.0 removals (`ndarray.ptp`, `np.float_`, `np.NaN`, `np.trapz`, `np.in1d`, `np.row_stack`, …) | none in the codebase | `tests/test_codebase_audit.py` scans every `.py` file. The old code used `.ptp`-style patterns; the new code uses none |
| Import / dependency audit | every `app.*` module imports; `requirements.txt` covers all runtime imports | `test_every_app_module_imports`, `test_requirements_cover_imports` |
| Zhang-Suen | vectorised version **identical, pixel for pixel,** to a literal transcription of the §3.6.4 conditions (6 random masks) | `test_vectorised_zhang_suen_equals_literal_algorithm` |
| Catenary fit | recovers `a` within 2% and δ/L within 1% on known curves; full mask → skeleton → fit path within 3% | `tests/test_catenary_crs.py` |
| Closed-form δ | matches a 400,001-point brute-force search to 1e-7 relative | `test_closed_form_sag_matches_dense_search` |
| Change-of-state (Eq. 9) | root satisfies the cubic; identity at reference state; heat slackens, wind tightens | `test_change_of_state_identity_and_monotonicity` |
| GPS removal | GPS IFD and GPS strings absent from the stored file; other EXIF kept; coordinates stored only in `pole_locations` | `test_gps_is_extracted_and_removed_from_stored_copy`, `test_upload_strips_gps_and_stores_location_separately` |
| FK integrity | `PRAGMA foreign_keys` = 1 on every pooled connection; raw-SQL delete cascades; orphans and domain violations rejected | `tests/test_database.py` |
| End-to-end | upload → queue → result → GeoJSON → PDF (text extracted and checked for the disclaimer) → delete | `tests/test_api.py` |
| UI | workspace, canvas overlay, and sites map rendered in headless Chrome and inspected visually | screenshots taken during development (not committed) |
| YOLO11-seg inference path | Wiring verified (not detection quality). A randomly initialised **4-class** YOLO11n-seg receives the letterboxed float BCHW tensor (predictor `orig_shape` = 640×640, so no second resize), runs per-class NMS, and returns 640² masks that map to the original resolution. The full pipeline runs on model output. Detect-only and foreign-class checkpoints are rejected | `tests/test_yolo_integration.py` (skips if Ultralytics is not installed) |
| Dependency-migration breaks found | (1) OpenCV 5 removed `CascadeClassifier`, so the privacy screen is now version-aware and fails closed. (2) With Ultralytics imported, Pillow raises `ModuleNotFoundError: pi_heif` on non-image bytes, so a garbage upload returned 500; it now returns 422 `undecodable` | both covered by tests |

---

## 2. Mathematical refactor, original vs. Chapter III

The original column is the code on `main` (`backend/utils/cv/*`, `backend/formulas.py`), which Chapter IV,
Table 4.1, also describes.

| Item | Original (`main`) | Now (exactly as Chapter III) |
|---|---|---|
| Preprocessing | Long side ≤ 512 px; 3×3 blur on both paths; no letterbox | **Alg. 1**: RGB letterbox to 640² (pad 114) scaled to [0, 1]; gray direct resize to 640² + **5×5** Gaussian; letterbox geometry kept for MAP_TO_ORIGINAL |
| VNI | **Magnitude** spectrum, radius **0.08**·min(H,W), axis bands excluded, **no window, no rescaling, no bands** | **Eq. 1–4**: 2D **periodic Hann** window, **power** \|F\|², share beyond r_L = **0.15**·min(M,N)/2 over r > 0, **q01/q99** clip, **Table 3.7** bands (upper bounds inclusive) |
| Centerline | **Median y per column**. No thinning, no pruning, no longest path (crossing wires mixed) | **Alg. 4**: **Zhang-Suen** thinning, **8-connected** pixel graph, iterative **spur pruning < 30 px**, **double-BFS** longest path, n_min check, ordered from the left end |
| Catenary fit | `curve_fit` with two ad hoc starts, default method; no failure checks | **Eq. 6–7**: y flipped, **parabola starting values**, `curve_fit(method="lm")` with an analytic Jacobian in normalised coordinates; fails on non-convergence, **a ≤ 0**, or residual/L > τ; **truncation flag** at the border |
| Sag ratio | **Vertical** deviation of raw points from a chord **÷ chord length** | **Eq. 8**: δ = largest **perpendicular** distance of the **fitted curve** from the chord, in **closed form** (x* = b + a·asinh(m)); δ_rel = δ/L |
| Engineering factors | none | **Eq. 9**: resultant load √(w² + w_w²), wind load ½ρV²C_d·d, **change-of-state cubic** (elasticity + thermal expansion) solved with Brent's method, catenary midspan sag; falls back to δ_lim,0 with flag `default_parameters` |
| CRS | **0–100** from constants 0.03 / 0.17 + residual penalty | **Eq. 10**: min{10, 10·(δ_rel/δ_lim)·w_class}; levels **Low < 3.0 ≤ Medium ≤ 6.0 < High** |
| Site ranking | Sort by max then mean image score | **Eq. 11 / Alg. 6** MSPI = αVNI + β·mean(CRS)/10 + γ·max(CRS)/10 (α+β+γ = 1, validated); unranked poles flagged `NO_VALID_WIRES`; level of the worst wire shown |
| Sensitivity | none | 66-triple simplex grid (step 0.1): Spearman ρ vs. baseline, top-k overlap, best/worst rank per pole (Table 3.11) |
| Removed | detection-confidence "severity", `detection_risk`, `barangay_risk`, 50/50 final score, PEC metric clearance from an assumed pole height | Not in Chapter III, and based on uncalibrated constants (Chapter IV §4.1.7 says so). The PEC clearance converted pixels to metres from an assumed pole height |

### Finding that needs a methodology decision: Hann-window DC leakage in Eq. 3

Equation 3 excludes only r = 0. The Hann window spreads the image mean into the bins at r = 1–2, and those
bins count in the denominator. As a result, **VNI_raw depends on scene brightness**: in the test, the same
stripe texture scored **0.47 dark and 0.036 bright**, and 53% of that image's power came from leakage alone.
That would weaken H1, the VNI versus clutter-rating correlation. Subtracting the mean before windowing removes
the effect. This is standard practice, but it is a change to Eq. 3 as published, so it is exposed as
`vni.remove_mean` and the default `false` keeps Chapter III exactly. Both behaviours are tested in
`test_equation_3_as_written_depends_on_brightness_mean_removal_does_not`. **Recommendation:** raise it with
your adviser and compare both settings during the §3.6.2 radius study.

### Interpretation choices where Chapter III is open

| Point | Choice | Reason |
|---|---|---|
| Chord endpoints | Fitted curve at the first and last centerline x | §3.6.5 text says "two ends of the fitted centerline". This makes δ ≥ 0 and well defined. Alg. 5 line 5 (raw points) differs by the end residuals only |
| Residual limit τ | RMS residual ÷ L ≤ 0.02 | Scale-invariant like δ_rel; to be set on the validation set |
| Graph edges | Diagonal edges dropped when the two pixels share an orthogonal neighbour | Removes Zhang-Suen corner triangles that made path pixels look like junctions. The 8-connected components are unchanged |
| Pruning | Only the shortest spur per junction per pass | A Y-fork at a wire end otherwise erases both arms and shortens the span |
| Hann form | Periodic (DFT-even) | Standard for spectral analysis; Chapter III does not specify |
| Multiple photos per pole | Latest finished analysis represents the pole | Thesis assumes one standardized photo per pole |
| Quality check | Only objective rejections (undecodable, short side < 320 px, constant image) | The old blur/darkness rejections (Laplacian < 50, mean < 15) had no source. The envelope only **flags** images (Alg. 1, line 2) |

---

## 3. Backend architecture

* **Layout:** `app/core` (settings, DB, state), `app/models` (ORM), `app/pipeline` (pure math, no I/O), `app/services` (ingest, queue, ranking, GeoJSON, PDF), `app/api` (routes), `app/web` (UI).
* **Config:** `config/wireframe.yaml`, validated by Pydantic at startup. Wrong weights, q99 ≤ q01, non-positive limits, or even kernels are rejected. A SHA-256 fingerprint of the config is stored with every analysis and report, and every block is tagged `provisional`.
* **Database (SQLAlchemy 2):** tables `poles`, `pole_locations`, `analyses`, `detections`, `reports`.
  * `PRAGMA foreign_keys = ON` is set on every connection and asserted at startup.
  * `ON DELETE CASCADE` is in the schema and mirrored by ORM `passive_deletes`.
  * CHECK constraints cover status, class, level, band, VNI ∈ [0, 1], CRS ∈ [0, 10], and "a CRS exists exactly when the wire is scored".
  * One report per analysis (unique). UTC-aware timestamps: SQLite drops the offset, which would make the browser read times 8 h off.
* **GPS coordinates** live only in `pole_locations`, never in stored images (§3.14.4).
* **Queue:** a single `asyncio` consumer runs analyses in a worker thread. Inference stays serial because Ultralytics predictors are not thread-safe. Analyses left in `processing` by a restart are re-enqueued.
* **States:** `processing → complete | no_wires_detected | fit_failed | error`, with `error_code` (`model_unavailable`, `pipeline_exception`). Upload rejections (`undecodable`, `resolution_too_low`, `constant_image`, `invalid_labels`, `invalid_location`, `too_large`) return 4xx and leave no orphan files.
* **No mock detector.** The old code silently returned nine fixed detections when the model was missing, and the database did not mark them. It is gone. The validation path, reference annotations, is explicit and recorded as `segmentation_source = "annotation"`.
* **PDF:** ReportLab.
  * The disclaimer is on page 1 and in every page footer.
  * Contents: counts per class, a per-wire metrics table, VNI and band, MSPI rank, flags, acquisition envelope, and a parameter fingerprint.
  * Plain-language headers are used because the standard PDF fonts have no Greek glyphs; δ had rendered as ■.

## 4. Frontend

* A framework-free, three-view app (Analyze, analysis detail, Sites) built from ES modules. Typography is Geist and Geist Mono with tabular numerals. Dark and light themes use the logo gold as the only accent, and risk colours are used only for risk.
* **Canvas overlay** (`viewer.js`):
  * Masks are pixel-boundary polygons filled even-odd and aligned to the pixel grid.
  * Layers: fitted catenary, dashed chord, perpendicular δ segment, a screen-space "sag/L · CRS" callout, and a toggleable skeleton.
  * Colour by risk or by class.
  * Wheel zoom about the cursor, pointer-captured pan, double-click to fit; HiDPI rendering.
  * Hit-testing on masks and curves, with a tooltip.
  * Selecting a wire focuses it in the inspector, showing L, δ, δ/L, δ_lim, a, residual, pruned px, and the CRS formula with the values filled in.
* **Map** (`sites.js`): Leaflet with OpenStreetMap tiles. CARTO now requires an API key. Pins are coloured by level and numbered by rank, and the list is synchronised with the map. Live α/β sliders (γ = 1 − α − β) re-rank through the API, and the sensitivity panel shows min and mean ρ, plus each pole's rank spread across the grid.
* **Toasts:** a Sonner-style stack written in vanilla JS (Sonner is React-only and this UI has no build step). It covers stacking, expand on hover, timers that pause on hover or when the tab is hidden, promise and loading toasts, and transitions instead of keyframes.
* **Accessibility:** focus rings, listbox roles with arrow-key navigation, `aria-live` toasts, `prefers-reduced-motion`, and hover effects only for fine pointers. Layout works at phone width.

## 5. Dataset pipeline (Phase 1)

`ml/curate.py` implements the §3.4.2 steps in order and writes the count after each one to `audit.json`:
licence, viewpoint, exact and near duplicates (pHash), privacy, label mapping, background cap, density
tertiles, and a 70:15:15 stratified split.

**Deliberate deviation from the request:** bounding boxes are **not** converted to polygons. A box around a
thin diagonal wire becomes a filled rectangle, which would teach the model wrong masks and break the
skeleton. Box-only images, and generic "wire"/"cable" labels that cannot be assigned to one of the four
Table 3.5 classes from the label alone, go to `annotation_queue.csv` for hand-drawn polygons (§3.4.3).

**Group-whole split:** near-duplicates never straddle train and test. A first version could split a duplicate
group across strata; a test caught it and it was fixed.

**Privacy screen:** Haar cascades on OpenCV 4.x. OpenCV 5 removed `CascadeClassifier`; there, YuNet is used if
a model path is given, and otherwise **every** image is listed for manual review (it fails closed).

`ml/train.py` fine-tunes YOLO11m-seg at 640 px with `overlap_mask=False`, because crossing wires need separate
masks. It validates at NMS IoU 0.45, chooses the confidence threshold that maximises mean mask F1 on the
validation set, and writes it back to the config. `ml/calibrate_vni.py` computes q01/q99 for radius factors
0.10, 0.15 and 0.20.

---

## 6. Not done, or not verifiable here

1. **No model trained, and no dataset built.** This machine is an M1 with 8 GB and has no Roboflow key. The ten source slugs and licences in `ml/sources.yaml` are blank on purpose: I did not guess them. The curation and training code is unit-tested on synthetic folders only.
2. **No trained weights were run.** The YOLO path was exercised only with random weights (§1), so mask quality, the conf_min choice, and every Table 3.10 metric remain untested until `ml/train.py` runs on the curated dataset.
3. **Every parameter is provisional:** q01/q99, τ, n_min, δ_lim,0 (development placeholders, flag `development_limits`), conductor data (illustrative magnitudes, flag `illustrative_conductor_data`), class weights, MSPI weights, and conf_min. The thesis assigns all of these to second-part calibration.
4. **Synthetic data:** the demo and fixture images (`app/devtools/synthetic.py`, pole codes `SYN-…`) are rendered catenaries with exact ground truth. They test geometry, not perception, and must never appear as field results.
5. **Removed without replacement:** login, RBAC, batch upload and admin routes from the old `backend/`. Chapter III's use-case diagram has a System Administrator who manages the configuration. In this version the configuration is a file, and no web route can write it.
6. **Evaluation metrics** (Eq. 12–18, bootstrap CIs, weighted κ) are not implemented. Ultralytics computes the segmentation metrics; the reference-assessment statistics need the second-part ratings.
