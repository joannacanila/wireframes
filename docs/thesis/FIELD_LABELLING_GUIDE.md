# Field labelling guide (S16)

For people labelling WIREFRAME field photos. The labels become ground truth for training and evaluation, so a
missing or guessed label does more harm than a slow one.

## Workflow

1. `python -m ml.field_dataset --data-dir <var> --out runs/field/<version> --version <version>` builds a versioned
   set with leakage groups and fixed splits (`manifest.json`). Never relabel into an existing version.
2. Optional pre-annotation: `python -m ml.annotation_pack export ...` writes model candidates with
   `verified=false`. They are suggestions only.
3. Label in CVAT (polygons, as in `datasets/wireframe_seg`). Tick `verified` on every shape you have checked.
4. `python -m ml.annotation_pack import ...` accepts only photos where every shape is verified, into a new folder.
5. Quality check before use: a second person labels 10% of photos (at least 5), chosen across splits. Report
   per-class agreement (centreline F1 between the two labellers, same tolerance as `centerline_eval.py`). Photos with
   disagreement are resolved together and the rule added here.

## Classes

| Class | Label it when | Do not |
|---|---|---|
| `power-distribution-lines` | Conductors on insulators or crossarms at the top of the pole, bare or covered, including the neutral; secondary (low-voltage) lines on racks | Guess from colour alone |
| `telecom-cable` | Cables lashed to a messenger strand, round black cables on clamps below the power space, fibre boxes / slack loops on the same cable | Call it fibre unless something on the cable says so (see below) |
| `abandoned-wire` | A cable that ends hanging loose, is cut, coiled on the pole with no continuation, or is attached at one end only | Label a cable abandoned because it sags |
| `Vegetation` | Branches touching or within the wire span area | |

Draw one polygon per wire span between two supports. Where a wire runs off the photo, end the polygon at the edge.
At a crossing, each wire keeps its own polygon through the crossing; never join two wires.

## Ambiguous cases

* **Type not visible** (silhouette against bright sky, night, far away): label the wire with the class you can
  justify from position and fittings; if none, add the attribute `type_uncertain=true`. These wires count for
  detection and measurement, not for type accuracy.
* **Fibre.** There is no fibre class. Fibre is usually indistinguishable from other telecom cable in a photo. Record
  `fiber_evidence=marker|slack_loop|closure|none` on telecom cables. A separate fibre class is only worth training
  if a few hundred cables carry `marker` or `closure` evidence, so the class is learned from something visible.
* **Service drops** (pole to building): label as the cable's type with `service_drop=true`.
* **Unlabelled is not negative.** If you skip a wire, mark the photo `complete=false`; precision is only computed on
  complete photos.

## Conditions to record per photo

`night`, `backlit`, `dense` (more than about 20 wires), `rain`, `blur`: one tick each, so results can be reported
by condition (`ml/experiments/s16_conditions.py`).

## S17 instance annotation (hard-case pack)
The S17 field evaluation showed that the main segmentation failure is identity switches, not missing pixels:
- 79% of ownership transitions have no gap.
- Crossings at or below about 20° swap wires in `tests/test_s17_segmentation.py`.

Pixel masks cannot measure or train a fix for this. The next labels must be instance-level.

1. **Pack.** `docs/thesis/results/s17_hardcase_pack.json` lists the 20 labelled images where the baseline fails most
   (merges, missed wires, fragmented wires, false-positive instances). Regenerate it with
   `python -m ml.experiments.s17_hardcase_pack`. Annotate these first.
2. **Per wire:**
   - an instance ID that is stable through crossings and occlusions
   - a centreline polyline
   - both endpoints, or "leaves frame" at an edge
   - per-segment flags: `crossing`, `occluded`, `behind_pole`
   - conductor type: power / telecom / abandoned / unsure
   - fibre evidence, as in the existing class rules
3. **QA.** Double-label 20% of the pack and compare instance IDs per crossing. Adjudicate disagreements before export.
   Record each labeller's ID.
4. **Export.** Use `ml/field_dataset.py`: a versioned folder, the leakage groups kept, never overwritten. The
   instance IDs and flags go in a sidecar JSON next to the YOLO polygons.
5. **Before any change to the model or the span extractor:**
   - re-run `ml/experiments/s17_seg_eval.py` (dump → score → compare against baseline)
   - adopt only a variant that improves strict fragmentation without regressing continuity, endpoints, missed rate
     or false positives on both splits

   `cluster12` is the current candidate. It improves strict fragmentation (val 0.139 → 0.113, test 0.151 → 0.107) but
   regresses continuity and endpoints, so it is not adopted.
