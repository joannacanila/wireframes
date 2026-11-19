"""S17 Part 3: the hard-case annotation pack (a manifest, no data copied or changed).

Ranks the labelled val/test images by where the baseline pipeline fails (strictly fragmented wires = identity
switches, merges, missed wires, false-positive instances) from docs/thesis/results/s17_seg_baseline.json and writes
docs/thesis/results/s17_hardcase_pack.json: which images to re-annotate first with instance-level labels (see
docs/thesis/FIELD_LABELLING_GUIDE.md, "S17 instance annotation"). Deterministic.

    PYTHONPATH=. python -m ml.experiments.s17_hardcase_pack [n]
"""
from __future__ import annotations

import json
import sys

from app.core.config import ROOT_DIR

R = ROOT_DIR / "docs/thesis/results"


def main(n: int = 20) -> None:
    base = json.loads((R / "s17_seg_baseline.json").read_text())
    rows = []
    for split in ("val", "test"):
        for stem, x in base["splits"][split]["_per_image"].items():
            why = {k: x[k] for k in ("fragmented", "merges", "missed", "fp_inst") if x.get(k)}
            score = 2 * x["merges"] + 2 * x["missed"] + x["fragmented"] + 0.5 * x["fp_inst"]
            rows.append({"split": split, "stem": stem, "score": round(score, 1), "failures": why})
    rows.sort(key=lambda r: (-r["score"], r["split"], r["stem"]))
    pack = {"version": "S17-HC-1", "source": "s17_seg_baseline.json (variant: baseline, shipped parameters)",
            "rule": "score = 2*merges + 2*missed + fragmented + 0.5*false-positive instances",
            "annotate": ["instance id per wire", "centerline polyline", "both endpoints (or 'leaves frame')",
                         "crossing / occlusion flags per segment", "conductor type (power / telecom / abandoned / unsure)"],
            "qa": "double-label 20% of the pack; adjudicate instance-id disagreements before export",
            "images": rows[:n]}
    (R / "s17_hardcase_pack.json").write_text(json.dumps(pack, indent=1))
    print(len(pack["images"]), [r["stem"][:30] for r in pack["images"][:3]])


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 20)
