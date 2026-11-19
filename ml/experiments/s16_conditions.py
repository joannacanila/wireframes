"""S16 (Part 10.1): where is each labelled wire lost, and under which image conditions?

For every labelled wire in the held-out dumps (ml/experiments/s15_aspect.py) the stages are checked in order:
  detected     a predicted wire covers it (>= 20 px inside the label widened by 2 px, as v2.16 coverage)
  typed        the predicted pixels on it are mostly of its class
  measured     a predicted wire on it was measured (status measured / not_assessed with a sag ratio)
Images are split by their own tertiles (no invented cut-offs): brightness (mean luma of the photo content), density
(labelled wires per image), sky share (bright low-saturation share, a proxy for wires seen against sky). Offline.

    python -m ml.experiments.s16_conditions
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from ml.experiments import s15_aspect as sa
from ml.experiments import v217_eval as v

SETS = (("runs/s15/clamp-square-val.json", "val", "square"), ("runs/s15/clamp-field-test.json", "test", "field"))
K5 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))


def image_stats(split: str, form: str) -> dict[str, dict]:
    bx = sa.boxes(split)
    out = {}
    for p in v._paths(split):
        y0, y1, x0, x1 = bx[p.stem]
        im = cv2.imread(str(p))[y0:y1, x0:x1]
        hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
        out[p.stem] = {"brightness": float(hsv[..., 2].mean()),
                       "sky": float(((hsv[..., 2] > 150) & (hsv[..., 1] < 90)).mean())}
    return out


def stages(rows: list[dict], gts: dict) -> list[dict]:
    out = []
    for r in rows:
        shape = tuple(r["shape"])
        preds = [(w["cls"], v._raster(w["polygons"], shape), w) for w in r["wires"]]
        g = [(c, m) for c, m in gts[r["stem"]] if c != v.VEGETATION]
        for c, m in g:
            gw = cv2.dilate(m.astype(np.uint8), K5).astype(bool)
            on = [(pc, int((pm & gw).sum()), w) for pc, pm, w in preds]
            on = [t for t in on if t[1] >= 20]
            det = bool(on)
            tot = sum(t[1] for t in on)
            typed = det and sum(t[1] for t in on if t[0] == c) >= 0.5 * tot
            meas = det and any(t[2].get("sag") is not None and t[2]["status"] in ("scored", "not_assessed") for t in on)
            out.append({"stem": r["stem"], "cls": c, "detected": det, "typed": typed, "measured": meas,
                        "n_gt": len(g)})
    return out


def rates(xs: list[dict]) -> dict:
    n = len(xs)
    if not n:
        return {"n": 0}
    d = sum(x["detected"] for x in xs)
    return {"n": n, "detected": round(d / n, 3), "typed_given_detected": round(sum(x["typed"] for x in xs) / max(d, 1), 3),
            "measured_given_detected": round(sum(x["measured"] for x in xs) / max(d, 1), 3),
            "lost_at": {"detection": n - d, "typing": d - sum(x["typed"] for x in xs),
                        "measurement": d - sum(x["measured"] for x in xs)}}


def run() -> dict:
    rep: dict = {}
    for dump, split, form in SETS:
        rows = json.loads(Path(dump).read_text())
        st = stages(rows, sa.gts(split, form))
        stats = image_stats(split, form)
        for x in st:
            x |= stats[x["stem"]]
        part = {"all": rates(st)}
        for c in ("power-distribution-lines", "telecom-cable", "abandoned-wire"):
            part[c] = rates([x for x in st if x["cls"] == c])
        per_img = {s: stats[s] | {"n_gt": next((x["n_gt"] for x in st if x["stem"] == s), 0)} for s in stats}
        for key in ("brightness", "n_gt", "sky"):
            vals = np.array([per_img[s][key] for s in per_img])
            t1, t2 = np.percentile(vals, [33.3, 66.7])
            grp = {s: ("low" if per_img[s][key] <= t1 else "high" if per_img[s][key] > t2 else "mid") for s in per_img}
            part[f"by_{key}"] = {"tertiles": [round(float(t1), 3), round(float(t2), 3)],
                                 **{gname: rates([x for x in st if grp[x["stem"]] == gname]) for gname in ("low", "mid", "high")}}
        rep[split] = part
    print(json.dumps(rep, indent=1))
    Path("docs/thesis/results/s16_conditions.json").write_text(json.dumps(rep, indent=1))
    return rep


if __name__ == "__main__":
    run()
