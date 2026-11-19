"""S17 (Part 3.2): reproducible segmentation and tracing evaluation of the DEPLOYED pipeline, by image condition.

Real labelled photos only (datasets/wireframe_seg val + test, field-shaped: the letterbox bars cropped off, as a phone
upload arrives; ml/experiments/s15_aspect.py). Nothing here is synthetic; the synthetic crossing / thin-wire cases are
controlled algorithm tests in tests/test_s17_segmentation.py and are never reported as model validation.

    python -m ml.experiments.s17_seg_eval dump  --variant baseline --split val     # runs/s17/<variant>-<split>.json
    python -m ml.experiments.s17_seg_eval score --variant baseline                 # val + test
    python -m ml.experiments.s17_seg_eval compare baseline candidate               # docs/thesis/results/s17_seg_*.json

Metrics (wire classes together, class-agnostic, unless stated):
  pixel      precision / recall / F1, IoU and Dice of the union wire mask (strict), and boundary precision / recall /
             F1 with a 2 px tolerance (thin structures: a 1 px shift is not an error)
  instance   a labelled wire is DETECTED when a predicted wire covers >= 20 px of it (widened 2 px, as S16);
             missed-wire rate = 1 - detected share; false-positive instances = predicted wires covering no labelled
             wire; false-positive area = predicted pixels > 3 px from every labelled wire, share of predicted pixels
  continuity per detected wire, on its labelled centreline (skeleton): coverage by all predictions (union), by the
             single best prediction (continuity), the number of predictions covering >= 5 % (fragments; > 1 =
             fragmented), duplicates (two predictions each covering >= 50 %), merges (one prediction covering >= 30 %
             of two labelled wires: an identity switch or a merge)
  endpoints  for each labelled wire's best prediction with a fitted chord: distance from each labelled extreme to the
             nearer fitted endpoint, in px and as a share of the labelled length
Conditions are measured from each photo (tertiles, no invented cut-offs) or read from its labels: dark (low
brightness tertile), bright, low-sky (low sky-share tertile), dense (high wire-count tertile), vegetation (a labelled
vegetation region), crossings (two labelled wires overlap after a 2 px widening), thin (median labelled width below the
median), low-resolution (shorter side < 600 px). Backlit, night, rooflines, antennas and phone model are not labelled
and are reported as such, not guessed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from skimage.morphology import skeletonize

from app.core.config import ROOT_DIR
from ml.experiments import s15_aspect as sa
from ml.experiments import v217_eval as v

DUMP = Path("runs/s17")
OUT = Path("docs/thesis/results")
K = {r: cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)) for r in (2, 3)}
WIRE_CLASSES = ("power-distribution-lines", "telecom-cable", "abandoned-wire")

# Post-processing variants (config overrides, the same mechanism as ml/experiments/v216_regression._cfg).
VARIANTS: dict[str, dict] = {
    "baseline": {},
    # crossing-aware pairing: one perfect matching per crossing instead of greedy joins
    "xjunction": {"segmentation": {"span_xjunction_tol_deg": 20.0, "span_xjunction_min_cross_deg": 10.0}},
    # stricter collinearity through a junction (fewer wrong joins, possibly more breaks)
    "turn25": {"segmentation": {"span_max_turn_deg": 25.0}},
    "xjunction_turn25": {"segmentation": {"span_xjunction_tol_deg": 20.0, "span_xjunction_min_cross_deg": 10.0,
                                          "span_max_turn_deg": 25.0}},
    # S17 synthetic finding: a wider junction cluster fuses the two Y junctions of a shallow crossing into one X
    # (fixes 20 deg crossings in tests/test_s17_segmentation.py); measured here before any adoption
    "cluster12": {"segmentation": {"span_cluster_radius": 12}},
}


def _widen(m: np.ndarray, r: int) -> np.ndarray:
    return cv2.dilate(m.astype(np.uint8), K[r]).astype(bool)


# ----------------------------------------------------------------------------- dump (runs the model; one at a time)
def dump(split: str, variant: str) -> Path:
    from app.pipeline.runner import run_analysis
    from app.pipeline.wirenet import make_segmenter
    from ml.experiments.v216_regression import _cfg
    cfg = _cfg(VARIANTS[variant])
    seg = make_segmenter(cfg.segmentation, ROOT_DIR)
    assert seg.load(), seg.error
    bx = sa.boxes(split)
    rows = []
    for p in v._paths(split):
        rgb = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
        y0, y1, x0, x1 = bx[p.stem]
        rgb = np.ascontiguousarray(rgb[y0:y1, x0:x1])
        o = run_analysis(rgb, cfg, seg)
        rows.append({"stem": p.stem, "shape": list(rgb.shape[:2]),
                     "wires": [{"cls": w.wire_class, "polygons": w.polygons, "status": w.status,
                                "reason": w.fail_reason, "flags": w.flags,
                                "sag": (w.fit or {}).get("sag_ratio"),
                                "endpoints": (w.fit or {}).get("endpoints"),
                                "centerline": [list(map(float, q)) for q in (w.centerline or [])][::4]}
                               for w in o.wires]})
        print(split, p.stem[:40], len(o.wires), flush=True)
    DUMP.mkdir(parents=True, exist_ok=True)
    out = DUMP / f"{variant}-{split}.json"
    out.write_text(json.dumps(rows))
    return out


# ----------------------------------------------------------------------------- conditions
def conditions(split: str, gts: dict) -> dict[str, dict]:
    bx = sa.boxes(split)
    out = {}
    for p in v._paths(split):
        y0, y1, x0, x1 = bx[p.stem]
        im = cv2.imread(str(p))[y0:y1, x0:x1]
        hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
        wires = [m for c, m in gts[p.stem] if c in WIRE_CLASSES]
        widths = []
        for m in wires:
            sk = skeletonize(m).sum()
            if sk:
                widths.append(m.sum() / sk)
        cross = any((_widen(a, 2) & _widen(b, 2)).any() for i, a in enumerate(wires) for b in wires[i + 1:])
        out[p.stem] = {"brightness": float(hsv[..., 2].mean()),
                       "sky": float(((hsv[..., 2] > 150) & (hsv[..., 1] < 90)).mean()),
                       "n_wires": len(wires), "width": float(np.median(widths)) if widths else 0.0,
                       "vegetation": any(c not in WIRE_CLASSES for c, _ in gts[p.stem]),
                       "crossings": cross, "short_side": int(min(im.shape[:2]))}
    return out


def condition_groups(stats: dict[str, dict]) -> dict[str, list[str]]:
    keys = list(stats)
    t = lambda f, q: float(np.quantile([stats[k][f] for k in keys], q))  # noqa: E731
    b1, b2, s1, d2 = t("brightness", 1 / 3), t("brightness", 2 / 3), t("sky", 1 / 3), t("n_wires", 2 / 3)
    wmed = t("width", 0.5)
    return {"all": keys,
            "dark (low brightness tertile)": [k for k in keys if stats[k]["brightness"] <= b1],
            "bright daylight (high brightness tertile)": [k for k in keys if stats[k]["brightness"] >= b2],
            "low sky (low sky-share tertile)": [k for k in keys if stats[k]["sky"] <= s1],
            "dense (high wire-count tertile)": [k for k in keys if stats[k]["n_wires"] >= d2],
            "vegetation labelled": [k for k in keys if stats[k]["vegetation"]],
            "crossing wires": [k for k in keys if stats[k]["crossings"]],
            "thin wires (below median width)": [k for k in keys if stats[k]["width"] < wmed],
            "low resolution (short side < 600 px)": [k for k in keys if stats[k]["short_side"] < 600]}


# ----------------------------------------------------------------------------- per-image scoring
def _extremes(m: np.ndarray) -> np.ndarray | None:
    ys, xs = np.nonzero(m)
    if len(xs) < 2:
        return None
    pts = np.stack([xs, ys], 1).astype(np.float64)
    c = pts.mean(0)
    u = np.linalg.svd(pts - c, full_matrices=False)[2][0]
    t = (pts - c) @ u
    return np.stack([pts[t.argmin()], pts[t.argmax()]])


def score_image(row: dict, gt: list[tuple[str, np.ndarray]]) -> dict:
    shape = tuple(row["shape"])
    preds = [(w, v._raster(w["polygons"], shape)) for w in row["wires"]]
    gws = [m for c, m in gt if c in WIRE_CLASSES]
    G = np.zeros(shape, bool)
    for m in gws:
        G |= m
    P = np.zeros(shape, bool)
    for _, m in preds:
        P |= m
    tp, fp, fn = int((P & G).sum()), int((P & ~G).sum()), int((~P & G).sum())
    Gt, Pt = _widen(G, 2), _widen(P, 2)
    G3 = _widen(G, 3)
    px = {"tp": tp, "fp": fp, "fn": fn, "p_sum": int(P.sum()), "g_sum": int(G.sum()),
          "b_tp_p": int((P & Gt).sum()), "b_tp_g": int((G & Pt).sum()), "fp_area": int((P & ~G3).sum())}
    # instances
    pw = [_widen(m, 2) for _, m in preds]
    psk = [skeletonize(m) for _, m in preds]
    G2 = [_widen(m, 2) for m in gws]
    skel = [skeletonize(m) for m in gws]
    cover = np.zeros((len(gws), len(preds)))
    hit = np.zeros((len(gws), len(preds)), bool)
    for i, (gm, sk) in enumerate(zip(gws, skel, strict=True)):
        gw = _widen(gm, 2)
        n = max(int(sk.sum()), 1)
        for j, (_, pm) in enumerate(preds):
            hit[i, j] = int((pm & gw).sum()) >= 20
            if hit[i, j]:
                cover[i, j] = (sk & pw[j]).sum() / n
    inst = []
    for i, gm in enumerate(gws):
        det = bool(hit[i].any())
        rec = {"detected": det, "length": int(skel[i].sum())}
        if det:
            union = np.zeros(shape, bool)
            for j in np.nonzero(hit[i])[0]:
                union |= pw[j]
            rec["coverage"] = float((skel[i] & union).sum() / max(int(skel[i].sum()), 1))
            rec["continuity"] = float(cover[i].max())
            rec["fragments"] = int((cover[i] >= 0.05).sum())
            # strict: only predictions that mostly LIE ON this wire (>= 50 % of their own centreline), so a
            # neighbouring parallel wire brushing this one's centreline is not counted as a fragment of it
            own = [j for j in np.nonzero(cover[i] >= 0.05)[0]
                   if psk[j].sum() and (psk[j] & G2[i]).sum() >= 0.5 * psk[j].sum()]
            rec["fragments_strict"] = len(own)
            rec["duplicate"] = bool((cover[i] >= 0.5).sum() >= 2)
            best = int(cover[i].argmax())
            ep = preds[best][0].get("endpoints")
            ex = _extremes(gm)
            if ep and ex is not None and len(ep) == 2:
                e = np.asarray(ep, float)
                d = [float(min(np.linalg.norm(x - e[0]), np.linalg.norm(x - e[1]))) for x in ex]
                L = float(np.linalg.norm(ex[1] - ex[0])) or 1.0
                rec["endpoint_px"] = d
                rec["endpoint_rel"] = [x / L for x in d]
        inst.append(rec)
    fp_inst = int((~hit.any(0)).sum()) if preds else 0
    merges = int(sum(((cover[:, j] >= 0.3).sum() >= 2) for j in range(len(preds)))) if gws and preds else 0
    meas = sum(1 for w, _ in preds if w.get("sag") is not None)
    return {"px": px, "inst": inst, "n_pred": len(preds), "fp_inst": fp_inst, "merges": merges, "measured": meas}


def aggregate(per: dict[str, dict], keys: list[str]) -> dict:
    if not keys:
        return {"images": 0}
    s = {k: sum(per[x]["px"][k] for x in keys) for k in per[keys[0]]["px"]}
    prec = s["tp"] / max(s["p_sum"], 1)
    rec = s["tp"] / max(s["g_sum"], 1)
    bp = s["b_tp_p"] / max(s["p_sum"], 1)
    br = s["b_tp_g"] / max(s["g_sum"], 1)
    inst = [r for x in keys for r in per[x]["inst"]]
    det = [r for r in inst if r["detected"]]
    eps = [d for r in det for d in r.get("endpoint_px", [])]
    epr = [d for r in det for d in r.get("endpoint_rel", [])]
    n_pred = sum(per[x]["n_pred"] for x in keys)
    f1 = lambda a, b: 2 * a * b / (a + b) if a + b else 0.0  # noqa: E731
    return {
        "images": len(keys), "labelled_wires": len(inst), "predicted_wires": n_pred,
        "pixel": {"precision": prec, "recall": rec, "f1": f1(prec, rec), "iou": s["tp"] / max(s["tp"] + s["fp"] + s["fn"], 1),
                  "dice": 2 * s["tp"] / max(2 * s["tp"] + s["fp"] + s["fn"], 1)},
        "boundary_2px": {"precision": bp, "recall": br, "f1": f1(bp, br)},
        "false_positive_area_share": s["fp_area"] / max(s["p_sum"], 1),
        "false_positive_instances": sum(per[x]["fp_inst"] for x in keys),
        "false_positive_instance_rate": sum(per[x]["fp_inst"] for x in keys) / max(n_pred, 1),
        "missed_wire_rate": 1 - len(det) / max(len(inst), 1),
        "fragmentation_rate": float(np.mean([r["fragments"] > 1 for r in det])) if det else None,
        "mean_fragments": float(np.mean([r["fragments"] for r in det])) if det else None,
        "fragmentation_rate_strict": float(np.mean([r["fragments_strict"] > 1 for r in det])) if det else None,
        "duplicate_rate": float(np.mean([r["duplicate"] for r in det])) if det else None,
        "merges_per_image": sum(per[x]["merges"] for x in keys) / len(keys),
        "coverage_mean": float(np.mean([r["coverage"] for r in det])) if det else None,
        "continuity_mean": float(np.mean([r["continuity"] for r in det])) if det else None,
        "continuity_median": float(np.median([r["continuity"] for r in det])) if det else None,
        "endpoint_px_median": float(np.median(eps)) if eps else None,
        "endpoint_rel_median": float(np.median(epr)) if epr else None,
        "endpoint_within_5pct": float(np.mean([d <= 0.05 for d in epr])) if epr else None,
        "endpoints_scored": len(eps),
        "measured_predictions": sum(per[x]["measured"] for x in keys),
    }


def score(variant: str) -> dict:
    res = {"variant": variant, "splits": {}}
    for split in ("val", "test"):
        f = DUMP / f"{variant}-{split}.json"
        if not f.exists():
            continue
        rows = json.loads(f.read_text())
        g = sa.gts(split, "field")
        per = {r["stem"]: score_image(r, g[r["stem"]]) for r in rows}
        groups = condition_groups(conditions(split, g))
        res["splits"][split] = {name: aggregate(per, [k for k in ks if k in per]) for name, ks in groups.items()}
        res["splits"][split]["_per_image"] = {k: {"n_pred": x["n_pred"], "fp_inst": x["fp_inst"], "merges": x["merges"],
                                                  "missed": sum(not r["detected"] for r in x["inst"]),
                                                  "fragmented": sum(r.get("fragments", 0) > 1 for r in x["inst"])}
                                              for k, x in per.items()}
    res["not_labelled"] = ["backlit", "night (distinct from dark)", "rooflines / building edges", "antennas",
                           "streetlights", "fences", "shadows", "occlusion", "phone model"]
    (OUT / f"s17_seg_{variant}.json").write_text(json.dumps(res, indent=1))
    return res


def compare(a: str, b: str) -> dict:
    ra = json.loads((OUT / f"s17_seg_{a}.json").read_text())
    rb = json.loads((OUT / f"s17_seg_{b}.json").read_text())
    keys = ("pixel.precision", "pixel.recall", "pixel.f1", "pixel.iou", "pixel.dice", "boundary_2px.f1",
            "false_positive_area_share", "false_positive_instances", "missed_wire_rate", "fragmentation_rate",
            "fragmentation_rate_strict",
            "duplicate_rate", "merges_per_image", "continuity_mean", "coverage_mean", "endpoint_rel_median",
            "endpoint_within_5pct", "predicted_wires")

    def get(d, k):
        for p in k.split("."):
            d = (d or {}).get(p)
        return d
    out = {}
    for split in ra["splits"]:
        for cond, x in ra["splits"][split].items():
            if cond.startswith("_"):
                continue
            y = rb["splits"].get(split, {}).get(cond, {})
            out[f"{split} | {cond} (n={x.get('images')})"] = {k: [get(x, k), get(y, k)] for k in keys}
    (OUT / f"s17_seg_compare_{a}_vs_{b}.json").write_text(json.dumps(out, indent=1))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["dump", "score", "compare"])
    ap.add_argument("names", nargs="*")
    ap.add_argument("--variant", default="baseline")
    ap.add_argument("--split", default="val", choices=["val", "test"])
    a = ap.parse_args()
    if a.stage == "dump":
        print(dump(a.split, a.variant))
    elif a.stage == "score":
        r = score(a.variant)
        for split, d in r["splits"].items():
            x = d["all"]
            print(split, json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in
                                     {**x["pixel"], "b_f1": x["boundary_2px"]["f1"], "miss": x["missed_wire_rate"],
                                      "frag": x["fragmentation_rate"], "frag_s": x["fragmentation_rate_strict"],
                                      "dup": x["duplicate_rate"],
                                      "merge/img": x["merges_per_image"], "fp_inst": x["false_positive_instances"],
                                      "fp_area": x["false_positive_area_share"], "cont": x["continuity_mean"],
                                      "ep_rel": x["endpoint_rel_median"]}.items()}))
    else:
        compare(*a.names)


if __name__ == "__main__":
    main()
