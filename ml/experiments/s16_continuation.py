"""S16 (D-34): longest skeleton path vs good-continuation path, on held-out labelled wires.

Every predicted wire on one labelled wire (s15_measure.pairs) is re-measured from its mask with each path rule and the
deployed span fit; the error is against the labelled wire over the same extent (same rule for the label). Offline.

    python -m ml.experiments.s16_continuation
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from app.core.config import ROOT_DIR, load_analysis_config
from app.pipeline.skeleton import extract_centerline
from app.pipeline.span_fit import fit_span
from ml.experiments import s15_aspect as sa
from ml.experiments import s15_measure as sm

SETS = (("runs/s15/clamp-square-val.json", "val", "square"), ("runs/s15/clamp-field-test.json", "test", "field"))


def measure(mask, cfg, rule):
    cl = extract_centerline(mask, cfg.centerline.l_min_px, cfg.centerline.n_min_points, path_rule=rule)
    if not cl.ok:
        return None
    h, w = mask.shape
    f = fit_span(cl.points, image_size=(w, h), max_rel_residual=cfg.catenary.max_rel_residual,
                 border_margin_frac=cfg.catenary.border_margin_frac, kink_ratio=cfg.catenary.kink_ratio,
                 kink_check_from=cfg.catenary.kink_check_from)
    return f.sag_ratio if f.ok else None


def run() -> dict:
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    rep = {}
    for dump, split, form in SETS:
        ps = sm.pairs(json.loads(Path(dump).read_text()), sa.gts(split, form), cfg)
        out = {}
        for rule in ("longest", "continuation"):
            got = []
            for p in ps:
                ref = p["ref"]["ratio"] if p["ref"] and p["ref"]["ok"] else None
                got.append((p["cls"], measure(p["pred_mask"], cfg, rule), ref))
            ok = [(c, m, r) for c, m, r in got if m is not None and r is not None]
            err = np.array([(m - r) * 100 for _, m, r in ok])
            s = sm.summary(err)
            s["measured_share"] = round(len(ok) / max(1, len(got)), 3)
            for name, cls, th in (("telecom_above_5", "telecom-cable", 0.05), ("power_above_3", "power-distribution-lines", 0.03)):
                fl = [(m, r) for c, m, r in ok if c == cls and m > th]
                s[name] = {"flagged": len(fl), "label_above": int(sum(r > th for m, r in fl))}
            s["over_5pct"] = int(sum(m > 0.05 for _, m, _ in ok))
            out[rule] = s
        rep[split] = out
        print(split, json.dumps(out, indent=1))
    Path("docs/thesis/results/s16_continuation.json").write_text(json.dumps(rep, indent=1))
    return rep


if __name__ == "__main__":
    run()
