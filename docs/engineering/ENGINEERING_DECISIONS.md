# Engineering decisions (index)

The authoritative record is `docs/thesis/RESEARCH_AND_DECISION_REGISTER.md`. It gives each decision's sources,
exact provisions, verification status and consequences, and it also holds the S6 engineering assumption register.
This page only groups the decision ids by subject, so an engineer can find the reason behind a rule. The titles are
quoted from the register. When this index and the register differ, the register wins.

## Sag measurement and references

| Id | Title (register) | Where it acts |
|---|---|---|
| D-01 | Sag ratio definition | `catenary.py`, thesis reference §7.2 |
| D-02 | Telecom sag reference 4.0 % (withdrawn) | `crs.delta_lim0: {}` |
| D-03 | Minor-sag band 2.4 % (withdrawn) | `crs.levels: {}` |
| D-04 | Power-line 3 % sag reference (removed from CRS in S4) | `crs` |
| D-09 | Precision gate 100 px (off; resolution reported instead) | `crs.min_chord_px: 0` |
| D-14 | Conductor values (removed; illustrative only) | `engineering.conductors: {}` |
| D-18 | Image-derived vs physical sag | every view's disclaimer |
| D-26 | Configured 3 % sag reference and full-colour meter (S8) | `comparison_reference` |
| D-27 | Power-line scope of the 3 % reference (S11) | `comparison_reference.classes` |
| D-28 | Explicit meter boundaries and the approved results page (S12) | `lower_boundary`, `upper_boundary` |
| D-29 | Model fluctuation and the no-reference meter (S13) | result view |
| D-31 | Strand-type parity, feature range, separate telecom / fibre reference (S15) | `telecom_reference`, `strand_head_clamp` |
| D-32 | Kink-aware span fit (S15) | `span_fit.py`, `catenary.kink_split` |
| D-34 | One display plan; telecom "beyond typical" is notable, not attention; calibrated 90 % band (S16) | `measurement_uncertainty`, `notable_zones` |
| D-37 | Canonical sag classification and pole-level result; one Important Notice (S17) | `comparison.py: classify`, `notice.js` |

## Wire classes and per-wire decisions

| Id | Title | Where it acts |
|---|---|---|
| D-05 | Abandoned wires | no reference for `abandoned-wire` |
| D-06 | Uncertain wire type | `class_uncertain` → not compared |
| D-07 | Both endpoints required | `crs.require_both_endpoints` |
| D-11 | Centerline P/R/F1 tolerance 3 px / 1024 | `app/pipeline/centerline_eval.py` |
| D-33 | Staged wire states, inconclusive cause, one bridged rule, one disclaimer (S15) | `comparison.py: wire_states`, `BRIDGED_MIN_SHARE` |

## Pole verdict, vegetation, reliability, ranking

| Id | Title | Where it acts |
|---|---|---|
| D-08 | INCONCLUSIVE when < 50 % of candidates scored (replaced by 100 %) | `hazard.min_assessed_share: 1.0` |
| D-10 | Corroboration p0 0.050 / 0.090 (dormant) | `hazard.sag_rule`, `p0_*` |
| D-15 | Vegetation contact margin and share; contact is no longer AT RISK | `vegetation`, `hazard.vegetation_verdict: false` |
| D-16 | Reliability grades A / B / C and the glare gate | `quality` |
| D-17 | VNI q01 / q99 | `vni` |
| D-19 | Pole verdict after S4 | `hazard.py` |
| D-20 | MSPI ranking (Equation 11) | `mspi`, `app/services/sites.py` |
| D-22 | Diagnostic output when no span can be assessed | `app/services/diagnostics.py` |

## Records, history, workflow

| Id | Title | Where it acts |
|---|---|---|
| D-13 | Backup | `scripts/backup.py` |
| D-21 | Historical inspections (v2.23 and earlier) | `app/services/basis.py` |
| D-23 | Withdrawn levels of pre-S4 records | `basis.py: level_withdrawn` |
| D-24 | Recovery baseline and result experience (S6) | — |
| D-30 | Consolidation: lifecycle states, flag rule, provider directory, citizen and admin redesign (S14) | `outcome.py`, `operations.py` |
| D-36 | Admin harmonisation: counts name their entity; one status vocabulary (S16) | admin views |
| D-38 | Three separate records: machine observations, screening indicators, administrative decisions (S18) | `screening.py`, `review_flow.py` |
| D-39 | S18 refinement: admin inspection cleanup, wire-level navigation, archive-based deletion, directory scope | `admin.py` archive / restore, `result-view.js` |

## Interface

| Id | Title | Where it acts |
|---|---|---|
| D-25 | Result page and three-zone sag meter (S7) | `result-view.js` |

## Platform and dependencies

| Id | Title | Where it acts |
|---|---|---|
| D-12 | Transformers ≥ 5.17 (CERT/CC VU#456290) | `requirements.txt` |
| D-35 | HEIC / HEIF uploads via pi-heif (S16) | `requirements.txt`, `app/services/analysis.py` |

## Making a new decision

1. Add the next id (D-40 onward) to the register, with its sources, exact provisions, verification status and
   consequences.
2. Change `config/wireframe.yaml` or the code, citing the id in a comment.
3. Add or adjust tests, and record the change in `docs/thesis/IMPLEMENTATION_CHANGELOG.md`.
4. Never change a stored machine observation. Display rules for historical records go through
   `app/services/basis.py`.
