"""Compare glare-gate mechanisms on the validation diagnosis (v2.10).

    python -m ml.experiments.glare_gate_eval --diag docs/thesis/results/v210_glare_diag_val.jsonl \
        --out docs/thesis/results/v210_glare_gates_val.json

Positives: glare cases whose clean verdict was downgraded or that lost more than half of their
scored spans. Negatives: every bright-sky control case, and glare cases that kept their spans.
Each mechanism fires only on bright photos (near-white share >= 0.6; no non-bright condition
exceeds 0.444). The threshold of M2 / M3 maximises balanced accuracy over the candidate values;
leave-one-photo-out (both conditions of a photo held out together) reports how much of that is
fitting. Outcome counts: silent NOT AT RISK left on positives, and NOT AT RISK verdicts turned
INCONCLUSIVE on negatives (the usability cost).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

BRIGHT = 0.6
RANK = {"NOT AT RISK": 0, "AT RISK - MODERATE": 1, "AT RISK - HIGH": 2}


def content_px(stem: str, split: str = "val") -> int:
    from app.pipeline.enhance import _content
    img = cv2.cvtColor(cv2.imread(next(Path(f"datasets/wireframe_seg/images/{split}").glob(f"{stem}.jpg")).as_posix()),
                       cv2.COLOR_BGR2RGB)
    return int(_content(img, margin=0).sum())


def balanced(y, f):
    y, f = np.asarray(y, bool), np.asarray(f, bool)
    tpr = (f & y).sum() / max(y.sum(), 1)
    tnr = (~f & ~y).sum() / max((~y).sum(), 1)
    return (tpr + tnr) / 2, tpr, tnr


def best_tau(x, y, below=True):
    cands = sorted(set(np.round(x, 3)))
    mids = [(a + b) / 2 for a, b in zip(cands, cands[1:], strict=False)] or cands
    scored = [(balanced(y, (x < t) if below else (x > t))[0], t) for t in mids]
    return max(scored)[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--diag", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    rows = [json.loads(line) for line in a.diag.read_text().splitlines()]
    clean = {r["image"]: r for r in rows if r["condition"] == "clean"}
    cpx = {s: content_px(s) for s in clean}
    data = []
    for r in rows:
        if r["condition"] not in ("glare", "brightsky"):
            continue
        k = clean[r["image"]]
        masked = r["verdict"] in RANK and RANK[r["verdict"]] < RANK[k["verdict"]]
        pos = r["condition"] == "glare" and (masked or r["scored"] < 0.5 * k["scored"])
        data.append({**r, "pos": pos, "masked_to_not": r["condition"] == "glare" and r["verdict"] == "NOT AT RISK" and k["verdict"] != "NOT AT RISK",
                     "density": r["wire_px"] / cpx[r["image"]], "bright": r["bright_share"] >= BRIGHT})
    y = np.array([d["pos"] for d in data])
    stems = sorted({d["image"] for d in data})
    feats = {"M1 exposure": None, "M2 line contrast": "line_contrast", "M3 wire density": "density",
             "M2 or M3": ("line_contrast", "density", "or"), "M2 and M3": ("line_contrast", "density", "and")}
    res = {}
    for name, f in feats.items():
        def fit(ds, f=f):
            if f is None:
                return None
            yy = np.array([d["pos"] for d in ds])
            if isinstance(f, tuple):  # each component keeps its own single-feature threshold
                return tuple(best_tau(np.array([d[g] for d in ds]), yy) for g in f[:2])
            return best_tau(np.array([d[f] for d in ds]), yy)

        def fire(ds, tau, f=f):
            out = []
            for d in ds:
                if f is None:
                    hit = True
                elif isinstance(f, tuple):
                    h1, h2 = d[f[0]] < tau[0], d[f[1]] < tau[1]
                    hit = (h1 or h2) if f[2] == "or" else (h1 and h2)
                else:
                    hit = d[f] < tau
                out.append(d["bright"] and hit)
            return np.array(out)
        tau = fit(data)
        fired = fire(data, tau)
        loo_hits = []
        for s in stems:  # leave one photo (both conditions) out
            tr = [d for d in data if d["image"] != s]
            te = [d for d in data if d["image"] == s]
            t = fit(tr)
            loo_hits += [bool(p == d["pos"]) for p, d in zip(fire(te, t), te, strict=True)]
        ba, tpr, tnr = balanced(y, fired)
        res[name] = {"tau": tau, "balanced_acc": round(ba, 3), "recall": round(tpr, 3), "specificity": round(tnr, 3),
                     "loo_accuracy": round(float(np.mean(loo_hits)), 3),
                     "silent_not_left": int(sum(d["masked_to_not"] and not fz for d, fz in zip(data, fired, strict=True))),
                     "brightsky_fired": int(sum(fz for d, fz in zip(data, fired, strict=True) if d["condition"] == "brightsky")),
                     "brightsky_not_made_inconclusive": int(sum(fz and d["verdict"] == "NOT AT RISK" for d, fz in
                                                                zip(data, fired, strict=True) if d["condition"] == "brightsky"))}
        print(name, res[name])
    res["counts"] = {"positives": int(y.sum()), "negatives": int((~y).sum()),
                     "silent_not_without_gate": int(sum(d["masked_to_not"] for d in data))}
    print(res["counts"])
    a.out.write_text(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
