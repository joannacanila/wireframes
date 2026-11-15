"""Golden-output check for span extraction (v2.10 latency work).

    python -m ml.experiments.span_golden --write runs/span_golden_val.json   # before a change
    python -m ml.experiments.span_golden --check runs/span_golden_val.json   # after: must be identical

Runs the shipped span extractor (v2.8 final constants) on the cached validation probability maps
and records, per image, the SHA-256 of every span mask (in output order) and its confidence,
plus the extraction time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np

from ml.experiments.ablation_final import FINAL
from ml.experiments.sweep_spans import make


def run(cache: Path) -> dict:
    ex = make(FINAL)
    out, total = {}, 0.0
    for f in sorted(cache.glob("*.npz")):
        z = np.load(f)
        probs = z["probs"].astype(np.float32)
        t0 = time.perf_counter()
        spans = ex(probs, probs.argmax(0))
        total += time.perf_counter() - t0
        out[f.stem] = [[c, hashlib.sha256(np.packbits(m).tobytes()).hexdigest()[:16], round(float(s), 6)]
                       for c, m, s in spans]
    return {"spans": out, "seconds_total": round(total, 2)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path("runs/cache/val-v28"))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", type=Path)
    g.add_argument("--check", type=Path)
    a = ap.parse_args()
    r = run(a.cache)
    if a.write:
        a.write.write_text(json.dumps(r))
        print("written;", sum(map(len, r["spans"].values())), "spans;", r["seconds_total"], "s")
        return
    ref = json.loads(a.check.read_text())
    bad = [k for k in ref["spans"] if ref["spans"][k] != r["spans"].get(k)]
    print(f"identical: {not bad} ({len(ref['spans']) - len(bad)}/{len(ref['spans'])} images); "
          f"time {ref['seconds_total']} s -> {r['seconds_total']} s")
    if bad:
        raise SystemExit(f"mismatch on {bad[:3]}")


if __name__ == "__main__":
    main()
