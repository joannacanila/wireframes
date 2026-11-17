"""v2.17: does the retake guardrail flag the photos the screener reads badly? (validation: clean + 7 conditions)

Reads the full-pipeline dumps (``ml.experiments.v217_eval dump``) and, per photo, the guardrail status the app
showed and the photo's wire centerline F1 against the labels. A useful guardrail sends low-F1 photos to
"retake" and leaves good ones alone. Reported: per status, photos and mean / median wire F1; the share of the
worst quartile of photos (F1) that were flagged (sensitivity) and of the rest that were not (specificity).
The thresholds in config/wireframe.yaml are judged here, on validation; the test split is not read.

    python -m ml.experiments.v217_guardrail --variant v217
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from app.pipeline import centerline_eval as ce
from ml.experiments.v217_eval import load_gts, preds_of

CONDS = ("clean", "lowlight", "glare", "motion", "defocus", "jpeg", "lowres", "rainhaze")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="v217")
    a = ap.parse_args()
    gts = load_gts("val")
    rows = []
    for c in CONDS:
        f = Path(f"runs/v217/dump/{a.variant}-val{'' if c == 'clean' else '-' + c}.json")
        if not f.exists():
            continue
        for r in json.loads(f.read_text()):
            g = [(k, m) for k, m in gts[r["stem"]] if k in ce.WIRE_CLASSES]
            w = ce.prf(ce.image_counts(preds_of(r), g, tuple(r["shape"]))["wire"])
            gr = r.get("guardrail") or {}
            rows.append({"cond": c, "stem": r["stem"], "f1": w["f1"] if w["f1"] is not None else 0.0,
                         "status": gr.get("quality_status", "none"), "retake": bool(gr.get("requires_retake")),
                         "codes": [i["code"] for i in gr.get("quality_issues", []) if i["severity"] != "info"]})
    f1 = np.array([r["f1"] for r in rows])
    q = float(np.percentile(f1, 25))
    worst = f1 <= q
    flagged = np.array([r["retake"] for r in rows])
    out = {"variant": a.variant, "photos": len(rows), "worst_quartile_f1": q,
           "sensitivity": float(flagged[worst].mean()) if worst.any() else None,
           "specificity": float((~flagged[~worst]).mean()) if (~worst).any() else None,
           "by_status": {}, "by_condition": {}, "by_issue": {}}
    for s in sorted({r["status"] for r in rows}):
        v = f1[[r["status"] == s for r in rows]]
        out["by_status"][s] = {"n": int(len(v)), "mean_f1": float(v.mean()), "median_f1": float(np.median(v))}
    for c in CONDS:
        sel = [r for r in rows if r["cond"] == c]
        if sel:
            out["by_condition"][c] = {"n": len(sel), "retake_share": float(np.mean([r["retake"] for r in sel])),
                                      "mean_f1": float(np.mean([r["f1"] for r in sel]))}
    for code in sorted({k for r in rows for k in r["codes"]}):
        v = f1[[code in r["codes"] for r in rows]]
        out["by_issue"][code] = {"n": int(len(v)), "mean_f1": float(v.mean())}
    print(json.dumps({k: v for k, v in out.items()}, indent=1))
    Path(f"docs/thesis/results/v217_guardrail_val_{a.variant}.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
