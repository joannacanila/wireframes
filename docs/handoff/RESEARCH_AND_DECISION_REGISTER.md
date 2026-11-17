# Research and Decision Register

Every significant engineering or technical decision in WIREFRAME, its source and its verification status.
Status values: **Verified** (source opened and checked), **Empirical** (reproducible measurement in this repository),
**Derived** (follows mathematically from other entries), **Unsupported** (research completed, no applicable basis
exists; the value is removed from decision logic), **Blocked** (resolution needs a document only the owner can supply).

Last updated: 18 Nov 2026, session S4 (see SESSION_LOG.md). S4 resolved D-02, D-03, D-08, D-09, D-10, D-14, D-15
(section "S4 threshold resolution" below). D-04 stays Blocked on the current PEC Part 2 text.

| ID | Topic | Source | Exact finding | Status | Decision (S4) | Code / config | Tests | Thesis impact | Remaining limitation |
|---|---|---|---|---|---|---|---|---|---|
| D-01 | Sag ratio definition | Code; catenary geometry (Irvine 1981) | delta = max perpendicular distance curve-to-chord, x* = b + a asinh(m); ratio = delta / Euclidean chord, image px | Verified (code) | Keep as a **descriptive image measurement**, reported with its 1 px resolution; never a physical sag or a compliance value | `app/pipeline/catenary.py: _chord_sag`; `runner.py` (`sag_ratio_resolution`) | `tests/test_v21.py`, `tests/test_s4_thresholds.py` | Ch. III sag method; ref. 7.2, 7.8 | No camera-pose correction; no physical validation |
| D-02 | Telecom sag reference 4.0 % | See S4 section (14 sources) | No applicable source gives a universal allowable sag-to-span ratio for aerial telecom cable; sag is computed per cable from manufacturer sag-tension data. Origin of 0.040: initial commit, "DEVELOPMENT PLACEHOLDERS so that the pipeline can run" | **Unsupported** (research complete) | **Removed** from decision logic (Outcome C, with Outcome B noted). Telecom spans: measured, `not_assessed`, reason `no_validated_reference` | `config/wireframe.yaml: crs.delta_lim0: {}` | `tests/test_s4_thresholds.py` | Ch. II/III threshold text, Ch. IV results, Ch. V | Telecom severity no longer produced |
| D-03 | Minor-sag band 2.4 % (60 % of reference) | None (project decision 17 Nov 2026, commit ca08acb) | Derived only from the placeholder 4.0 % x 6/10; no source defines a "Minor sag" class | **Unsupported** | **Removed** (`levels.minor_from/critical_from` deleted from config); no Minor category | `config/wireframe.yaml: crs.levels` | `tests/test_s4_thresholds.py` | Severity scheme removed | none |
| D-04 | Power-line 3 % sag reference | ERB Res. 95-21 s.19 (1995); ERC Res. 02 s.2010 | 95-21 superseded by DSOAR (effective 17 Feb 2006); no in-force source verified | Verified (texts read) / **Blocked** | Not implemented; power lines measured, `not_assessed`, `no_validated_reference` | `crs.delta_lim0: {}` | `tests/test_s4_thresholds.py` | Ch. II regulatory review; Ch. V | **Owner action:** current PEC Part 2 article on sag (edition, article, exact text) |
| D-05 | Abandoned wires | No criteria found | Hazard depends on attachment, clearance, condition | Verified absence (S2) | Not assessed (`no_validated_reference`) | `crs.delta_lim0` | `test_abandoned_wires_are_not_held_to_the_telecom_operating_limit` | Ch. III | Not detected anyway (27 labels) |
| D-06 | Uncertain wire type | Policy | No class, no reference | Derived | Keep (`class_uncertain`) | `runner.py` | `tests/test_v221_ml.py` | Ch. III | none |
| D-07 | Both endpoints required | Geometry | Partial span chord is not the support-to-support chord | Derived | Keep (`endpoint_unavailable`); the "unverified level" review cue is removed with the bands | `crs.require_both_endpoints` | `tests/test_v221_ml.py` | Ch. III | none |
| D-08 | INCONCLUSIVE when < 50 % of candidates scored | Policy (17 Nov 2026) | 0.5 had no derivation | **Unsupported** value | Replaced by a threshold-free rule: NOT AT RISK requires that **every** measured span was assessed and found clear (`min_assessed_share: 1.0`); with no validated reference no span is assessed, so the verdict is INCONCLUSIVE | `config: hazard.min_assessed_share`; `hazard.py` | `tests/test_s4_thresholds.py` | Ch. III abstention | none |
| D-09 | Precision gate 100 px (f = 0.25) | Derived from sigma/L <= f * delta_lim | Depends on the removed delta_lim and an unjustified f | **Unsupported** | Gate removed (`min_chord_px: 0`). Each measured ratio carries its resolution 1 px / L (scaled to the model grid), shown with the value | `config: crs.min_chord_px`; `runner.py` | `tests/test_s4_thresholds.py` | Ch. III 3.6.5 | sigma = 1 px is an assumption (skeleton quantisation), not a measured noise level |
| D-10 | Corroboration p0 0.050 / 0.090 | Empirical, old bands | Only meaningful for severity bands | Not applicable | **Inactive**: no bands exist, nothing to corroborate; re-measurement not performed because there is no band to measure against | `hazard.py` (no scored spans) | n/a | Ch. III/IV | Must be re-measured if a validated reference is ever configured |
| D-11 | Centerline P/R/F1 tol 3 px / 1024 | Shit et al. 2021; code | Thin-structure evaluation | Verified / Empirical | Keep (segmentation metrics are unaffected by S4) | `centerline_eval.py` | release eval | Ch. III/IV | not the planned instance metric |
| D-12 | Transformers >= 5.17 | CERT/CC VU#456290 | | Verified | Done | `requirements.txt` | | Ch. IV | |
| D-13 | Backup | SQLite Online Backup API | DB only | Verified + Empirical | **Open (S5):** full DB + files backup | `app/core/database.py` | restore test | Ch. IV | files not covered |
| D-14 | Conductor values (weight, diameter, E, alpha, tension) | None ("ILLUSTRATIVE") | No data sheet for the photographed conductors | **Unsupported** | **Removed** from config. Change-of-state adjustment (Eq. 9) unreachable without a reference; the metre conversion of vegetation gaps (10.1 mm assumed diameter) removed: gaps in pixels only | `config: engineering.conductors: {}`; `hazard.py`; `sag_limits.py` | `tests/test_s4_thresholds.py` | Ch. III Eq. 9, Table 3.8 | none |
| D-15 | Vegetation contact margin 1 % / share 2 %; power-near-vegetation 15 px; contact -> AT RISK | "development defaults"; clearance standards are physical distances (S4 section) | Pixel gaps in a single perspective image do not measure physical clearance; a 2-D overlap does not establish contact (depth unknown) | **Unsupported** | Vegetation proximity kept as an **image observation** (pixel gap, "overlaps in the image"); it no longer raises AT RISK. The 15 px rule is removed | `config: hazard.vegetation_verdict: false`; `hazard.py` | `tests/test_s4_thresholds.py` | Ch. III vegetation rules | A physical clearance needs calibrated range measurement |
| D-16 | Reliability grades A/B/C, glare gate | Empirical (validation stress set) | | Empirical | Keep (photo quality only; never a hazard) | `quality.py` | | Ch. III/IV | synthetic conditions |
| D-17 | VNI q01/q99 | Empirical | | Empirical | Keep (priority index) | `config: vni` | | Ch. III | not local calibration |
| D-18 | Image-derived vs physical sag | Chen and Ding 2023; Safdarinezhad et al. 2022; Kampik et al. 2024 | | Verified (bibliographic) | Keep as limitation | ref. 7.8 | | Ch. II, III, V | full texts not reviewed |
| D-19 | Pole verdict after S4 | Derived from D-02..D-15 | No sag reference and no calibrated clearance remain | Derived | NOT AT RISK and AT RISK are no longer produced for new analyses; every photo with measured wires is **INCONCLUSIVE** ("Assessment incomplete") with the reasons; a found image observation (vegetation overlap) is listed for review | `hazard.py` | `tests/test_s4_thresholds.py` | Ch. III, IV, V | Pole-level screening verdict unavailable until a validated reference exists |
| D-20 | MSPI ranking (Eq. 11) | Thesis | Needs CRS values | Derived | New analyses have no CRS, so they are not ranked (`NO_VALID_WIRES`, shown as "not ranked: no assessed spans") | `mspi.py` (unchanged) | existing | Ch. III 3.6.6 | Ranking unavailable for new analyses |
| D-21 | Historical inspections (v2.23 and earlier) | Owner rule: no reprocessing | Stored levels/verdicts were produced with the withdrawn placeholder | Policy | Stored data unchanged; on display, a stored level carrying `development_limits` is shown as "withdrawn provisional value" and never as a severity; the record shows a notice | `app/services/plain.py`, `findings.js`, `report_pdf.py` | `tests/test_s4_thresholds.py` | Ch. IV | Re-analysis needs owner authorisation |

---

## S4 threshold resolution (18 Nov 2026)

### 1. What WIREFRAME measures (verified in code, not documentation)

1. **Endpoints.** The chord runs between the fitted curve's values at the first and last centerline abscissas
   (`catenary.py: _chord_sag`), i.e. the visible ends of the traced wire in the image, not detected attachment points.
2. **Sag.** The largest perpendicular distance from that inclined chord to the fitted catenary, closed form
   x* = b + a asinh(m).
3. **Ratio.** delta / L, both in image pixels, L the Euclidean (inclined) chord. Stored as `sag_ratio` (fraction).
4. **Projection.** Single perspective photo; no camera intrinsics, pose, range or scale are used. The ratio changes
   with viewing angle and with the span's depth extent; it is an image-plane quantity, not sag/span in metres.
5. **Before S4** the ratio was divided by 0.040 (CRS) and banded at CRS 6 / 10 (2.4 % / 4.0 %); levels drove the
   pole verdict through a binomial corroboration rule, and MSPI ranking used CRS.
6. **Class handling.** Since v2.21 only `telecom-cable` was compared with 0.040; power and abandoned wires were
   measured and not assessed.

### 2. Sources reviewed (accessed 18 Nov 2026)

| # | Source | Issuer, date, edition | Status | Accessed | Exact finding | Applicability |
|---|---|---|---|---|---|---|
| S1 | Recommendation ITU-T L.102, *Optical fibre cables for aerial application* | ITU-T SG15, approved 2025-11-29 (11/2025) | In force (ITU record, updated 2026-04-22) | **Full text read** (official PDF, 46 pp) | Scope: aerial telecom fibre cables not along power lines, self-supporting and lashed (cl. 1, 6.1-6.2). Tensile rating LS = 1.5 W, max 2700 N; long-term LL = 30 % of LS (cl. 7.2.2.1, p. 11). "Cables should be designed and installed to provide stability of ... cable sag/tension" (cl. 7.3.10, p. 15). "Knowledge of span, sag, wind and ice loads, as well as permitted ground clearances is necessary to design cables for use in aerial applications" (cl. 8.2.6, p. 20). Installation conditions are agreed "between the customer and the manufacturer". The word "sag" appears only in these two clauses. **No numerical sag-to-span limit.** | Applicable cable class; establishes that sag is a design input derived from cable-specific tension ratings, not a fixed ratio |
| S2 | Europacable Technical Newsletter, *Aerial cables in FTTH* | Europacable, June 2023 | Industry association guidance | Full text read (14 pp) | Sag f or f/L x 100 %; "Vertical distance at the center of the span, usually equal to 1% or 1,3% of the span". Worked example: 70 m span, initial sag 0.91 m at 10 C (1.3 %); under loading case A2 sag 1.92 m (2.7 %), tension 81.7 to 215.5 daN; heavier cases require spans <= 50 m to respect the cable's max allowable tension (235 daN for 0.3 % fibre elongation). Reducing sag increases tension. | Shows sag/span is an installation design target that varies 2x with loading for the same correct installation, and that low sag is not "safer" |
| S3 | FOA Reference, *OSP Construction: Aerial Cable Installation* | Fiber Optic Association, (c) 2024 | Training reference, no standard cited for the figures | Full page read | "Sag is often set as 1% of span length"; "Sag is generally limited to <2% of span length and maximum tension <30% of cable minimum breaking strength"; "Every span must be analyzed for the size of messenger, the tension required for the span length and cable weight to meet sag requirements" | Secondary; design practice, installation state unspecified; not a regulatory limit |
| S4 | Hengtong, *What are aerial fiber optic cable specifications?* | Hengtong Optic-Electric (vendor), 4 Nov 2025 | Vendor article | Full page read | "Final sag values typically range from 2-4% of span length under normal conditions, increasing to 6-10% under maximum ice and wind loading"; "Sag calculations account for cable weight, span length, temperature, and expected loading conditions" | Vendor, typical values only. Note: under this source a 4 % final sag is a *normal* condition, contradicting 4 % as "Critical" |
| S5 | Corning AEN014 *Sag and Tension - General*; OFS PowerGuide IP014A; AFL ADSS sag-tension charts; Superior Essex TG093; STL ADSS placement | Manufacturers | Product documents | **Not read** (HTTP 403 / 404 / >10 MB); only search-engine snippets: Corning "allowable sag is typically set at 1% (of span length) or based on a given installation tension"; OFS: tensions above its sag-tension tables "are not to be used unless specifically authorized"; tables need "the ruling span, the sag requirement, and the loading conditions" | Not cited as verified. Consistent with S1-S3: sag is cable-specific and table-driven |
| S6 | ARTA-DICT-DILG-DPWH-DHSUD-CAAP-NTC-ERC-NEA-PCC JMC No. 01 s.2021, *Streamlined Guidelines for the Issuance of Permits and Clearances for the Erection of Poles, Construction of Underground Fiber Ducts and Installation of Aerial and Underground Cables ...*; reiterated by DILG MC 2021-135; Revised JMC No. 01 s.2021 (ARTA, Feb 2022) | Joint agencies; launched 10 Dec 2021 | In force (permitting instrument) | **Full text not read**: arta.gov.ph, dilg.gov.ph and batasnatin.com refused automated access (HTTP 403). ARTA press releases and the ARTA document index read via search: subject is permits, clearances, a single excavation clearance, a list of documents that may not be required, and processing periods (7 working days, deemed approved) | Evidence available indicates a **permitting** instrument; no source describes a sag criterion in it. Not verified clause by clause: owner may supply the PDF |
| S7 | NTC memorandum circulars (search of NTC regional site, Supreme Court E-Library, WTO licensing index) | NTC | | Searched | No NTC circular on aerial cable sag found | none found |
| S8 | House Bill No. 1403, *Anti-Dangling Wires Act* (20th Congress) | House of Representatives | **Bill, not law** | Search snippet | Would require bundling sagging/dangling wires; no numerical criterion | Not in force |
| S9 | Meralco "Request to Attach" practice; LGU wire-clearing (Manila, Pasig, Makati 2025-2026 news) | Meralco, LGUs | Utility practice | News only | Attachments need utility approval; no published sag criterion found | Utility-specific specs not public |
| S10 | ERB Resolution No. 95-21 s.19; ERC Resolution No. 02 s.2010 | ERB 1995; ERC 2010 | 95-21 **superseded** (DSOAR, eff. 17 Feb 2006) | Full texts read (S3) | 3 % of distance between poles, electric power utilities only | Not current; not telecom |
| S11 | Philippine Electrical Code Part 2 (current edition) | IIEE / Board of Electrical Engineering | Paid publication | **Not accessible**; unofficial copies conflict | Cannot be cited | **Owner action** |
| S12 | Philippine Distribution Code (ERC) | ERC; 2001 edition cited by search; revisions not confirmed | Mandatory for distribution | Not read (no official full text found) | No sag or tree clearance figure in any snippet | Blocked |
| S13 | NESC (IEEE C2) clearance rules; NRECA *Guide for the Application of Clearance Requirements on Joint-Use Poles* (May 2025) | IEEE / NRECA (US) | US code, not adopted in PH except as fallback in superseded 95-21 s.10 | Search snippets only | Clearances are stated at **final sag under specified loading**; sag is computed for the conductor, not limited as a ratio | Not applicable in PH; and does not supply a ratio |
| S14 | Vegetation clearance (FERC/NERC FAC-003, Energy Safe Victoria, SDG&E, Powercor) | US / AU regulators | Foreign | Search snippets | All clearances are physical distances (metres) set per voltage and jurisdiction | Not PH; not measurable from pixels |
| S15 | Image-based sag studies (Energies 2019 single-camera Bezier method; M2VIP 2017 aerial image + laser ranging; Safdarinezhad et al. 2022 smartphone + accelerometer) | Peer-reviewed / conference | | Abstracts only | Every method that reports physical sag uses known scale or camera attitude (laser range, accelerometer, known geometry) | Confirms D-18: WIREFRAME has none of these inputs |

### 3. Formal threshold decision table

| Criterion | Existing value | Source found | Current status | Applicability | Final decision |
|---|---:|---|---|---|---|
| Telecom reference | 4.0 % | None. Origin: development placeholder (initial commit). Nearest sources give installation targets of 1-1.3 % (S2, S3), a design limit < 2 % (S3, no standard cited) or typical *final* sag of 2-4 % (S4) | No standard, regulation or manufacturer document makes 4.0 % a limit | Would need cable type, model, span, loading state and sag-tension table, none known to WIREFRAME | **Removed** |
| Telecom Minor boundary | 2.4 % | None (60 % of the placeholder) | No source defines a Minor class | n/a | **Removed** |
| Power-line reference | 3.0 % (not active since v2.21) | ERB 95-21 s.19 | Superseded 2006 | Electric power utilities | **Not used**; Blocked on PEC Part 2 |
| Pole "within reference" (low band) | < 2.4 % | None | Low sag means high tension (S2); "within reference" has no basis | n/a | **Removed** |
| Precision gate | 100 px (f 0.25) | Derived from the placeholder | Inherits D-02 | n/a | **Removed**; resolution reported |
| Abstention share | 0.5 | None | Policy | n/a | **Replaced** by "every measured span assessed" (1.0) |
| Corroboration p0 | 0.050 / 0.090 | Empirical on old bands | Inapplicable without bands | n/a | **Inactive** |
| Conductor data | weight, diameter, E, alpha, tension | "ILLUSTRATIVE" | No data sheet | n/a | **Removed** |
| Vegetation 15 px; contact -> AT RISK; margin 1 % / share 2 % | as listed | Development defaults; all real clearances are metres | Pixels are not clearances | n/a | **Removed from the verdict**; reported as image observation |

Field-by-field record for the removed telecom thresholds (owner's 17 items): (1-7) no title, issuer, date, edition,
clause, page or URL exists for 4.0 % or 2.4 %; (8) status: development placeholder; (9) cable type: none;
(10) installation conditions: none; (11) mandatory/recommended/manufacturer/research: none of these; (12) supports the
exact value: no; (13) supports WIREFRAME's measurement: no (all sources state sag in metres relative to the
horizontal span at a stated loading state); (14) supports the severity label: no; (15) interpretation: sag/span is a
cable-specific design target whose value depends on span, cable weight, tension rating (S1 cl. 7.2.2.1), temperature,
wind and ice (S1 cl. 7.3.9-7.3.10, S2 example: 1.3 % -> 2.7 % for one correct installation); a fixed visual
percentage cannot be derived from S1 because S1 defines the tensile limits, not a sag ratio, and converting a tensile
limit into a sag requires the cable's weight, span and loading; (16) limitations: manufacturer documents S5 and JMC
text S6 not read in full; (17) decision: removed.

**Outcome:** C (no applicable numerical threshold), with B noted: cable-specific requirements exist (manufacturer
sag-tension tables under S1's tensile ratings), but WIREFRAME cannot identify cable model, span length or loading,
so it cannot apply them. The image-derived ratio is retained as a descriptive measurement.

### 4. What would reopen each decision
- Telecom: the photographed operator's or manufacturer's sag-tension table for the cable model, plus span length and a
  physically validated mapping from the image ratio to sag/span (ref. 7.8 protocol).
- Power: the current PEC Part 2 sag article (edition, article, exact text) or another in-force ERC instrument, plus a
  licensed engineer's confirmation, plus the same physical validation.
- Vegetation: a calibrated range measurement (stereo, LiDAR or survey) and the applicable clearance rule.
