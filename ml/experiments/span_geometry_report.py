"""Summarise ml.experiments.span_geometry: CRS level by inclination band, and test-retest p0.

    python -m ml.experiments.span_geometry_report docs/thesis/results/v29_span_geometry_val.json \
        --max-incl 35 --out docs/thesis/results/v29_span_geometry_summary_val.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np

BANDS = [(0, 10), (10, 20), (20, 35), (35, 50), (50, 90.01)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    ap.add_argument("--max-incl", type=float, default=35.0)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    data = json.loads(a.path.read_text())
    scored = [s for img in data for s in img["a"] if s["level"] is not None]
    bands = []
    for lo, hi in BANDS:
        sel = [s for s in scored if s["inclination_deg"] is not None and lo <= s["inclination_deg"] < hi]
        c = Counter(s["level"] for s in sel)
        n = len(sel)
        bands.append({"band_deg": [lo, min(hi, 90)], "spans": n, "high_share": c["high"] / n if n else None,
                      "medium_share": c["medium"] / n if n else None,
                      "median_chord_px": float(np.median([s["chord_px"] for s in sel])) if n else None})
    primary = lambda s: s["level"] is not None and s["inclination_deg"] is not None and s["inclination_deg"] <= a.max_incl  # noqa: E731
    both, disc_h, disc_m, n10, rep10 = 0, 0, 0, 0, 0
    for img in data:
        for i, j, _ in img["pairs"]:
            x, y = img["a"][i], img["b"][j]
            if not (primary(x) and primary(y)):
                continue
            both += 1
            disc_h += (x["level"] == "high") != (y["level"] == "high")
            disc_m += (x["level"] in ("high", "medium")) != (y["level"] in ("high", "medium"))
            for u, v in ((x, y), (y, x)):
                if u["crs"] is not None and u["crs"] >= 9.999:
                    n10 += 1
                    rep10 += v["level"] == "high"
    per_photo = [sum(primary(s) for s in img["a"]) for img in data]
    out = {"max_inclination_deg": a.max_incl, "scored_spans": len(scored), "by_inclination": bands,
           "steep_share_of_scored": sum(not primary(s) for s in scored) / max(len(scored), 1),
           "retest_pairs_primary": both,
           "p0_high": disc_h / (2 * both) if both else None, "p0_medium": disc_m / (2 * both) if both else None,
           "high_rate_primary": sum(s["level"] == "high" for s in scored if primary(s)) / max(sum(map(primary, scored)), 1),
           "medium_up_rate_primary": sum(s["level"] in ("high", "medium") for s in scored if primary(s))
           / max(sum(map(primary, scored)), 1),
           "crs10_measurements": n10, "crs10_reproduced_high": rep10,
           "primary_spans_per_photo": {"median": float(np.median(per_photo)), "min": int(min(per_photo)),
                                       "max": int(max(per_photo))}}
    a.out.write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
