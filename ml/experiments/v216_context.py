"""v2.16 scene-context and trajectory-validity study (validation split).

For every predicted strand (v2.16 strand extractor, cached WireNet probabilities) and every YOLO vegetation region:

* whether it is supported by the human labels: a strand is "supported" when >= 50% of its pixels lie within 2 px of
  a labelled wire; a vegetation region when >= 50% of its pixels lie on labelled vegetation
* the share of its pixels on each ADE20K scene class (UperNet-ConvNeXt-T, the parser the ground gate already uses)
* for strands, the catenary fit's RMS residual in px divided by the strand's mean stroke width (a real wire fits its
  own centreline to within about its width; a chain of pieces from different wires does not)

The output is the evidence for the v2.16 structure gate, vegetation gate and trajectory gate; thresholds are taken
from these validation distributions only.

    python -m ml.experiments.v216_context --split val
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from app.core.config import CLASSES, ROOT_DIR, VEGETATION, load_analysis_config
from app.pipeline.hazard import mean_width_px
from app.pipeline.wire_instances import strand_spans
from app.pipeline.wirenet import span_params
from ml.experiments import harness

WIRE_IDS = [CLASSES.index(c) + 1 for c in CLASSES if c != VEGETATION]


def ade_cache(split: str, out: Path) -> None:
    import yaml

    from app.pipeline.scene import SceneParser
    out.mkdir(parents=True, exist_ok=True)
    spec = yaml.safe_load(Path("datasets/wireframe_seg/data.yaml").read_text())
    parser = SceneParser()
    for p in sorted((Path("datasets/wireframe_seg") / spec[split]).glob("*.jpg")):
        f = out / f"{p.stem}.npy"
        if f.exists():
            continue
        rgb = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
        np.save(f, parser.labels(rgb))


def id2label() -> dict[int, str]:
    from transformers import AutoConfig

    from app.pipeline.scene import MODEL_ID
    return {int(k): v for k, v in AutoConfig.from_pretrained(MODEL_ID).id2label.items()}


def fit_residual(mask: np.ndarray, cfg) -> float | None:
    from app.pipeline.catenary import fit_catenary
    from app.pipeline.skeleton import extract_centerline
    cl = extract_centerline(mask, cfg.centerline.l_min_px, cfg.centerline.n_min_points)
    if not cl.ok:
        return None
    fit = fit_catenary(cl.points, image_size=mask.shape[::-1], max_rel_residual=1.0,
                       border_margin_frac=cfg.catenary.border_margin_frac,
                       max_function_evals=cfg.catenary.max_function_evals)
    if fit.rms_residual_px is None or fit.chord_px is None or fit.chord_px < cfg.crs.min_chord_px:
        return None
    return float(fit.rms_residual_px)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val")
    ap.add_argument("--cache", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=Path("docs/thesis/results/v216_context_val.json"))
    a = ap.parse_args()
    cache = a.cache or Path(f"runs/cache/{a.split}-v216")
    ade_dir = Path(f"runs/cache/{a.split}-v216-ade")
    ade_cache(a.split, ade_dir)
    names = id2label()
    full = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    params = span_params(full.segmentation)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    strands, vegs = [], []
    for stem, z in harness.load(cache):
        lab = np.load(ade_dir / f"{stem}.npy")
        probs = z["probs"].astype(np.float32)
        gw = np.zeros(lab.shape, bool)
        gv = np.zeros(lab.shape, bool)
        for c, m in zip(z["gt_classes"].tolist(), z["gt_masks"], strict=True):
            if c == VEGETATION:
                gv |= m
            else:
                gw |= m
        gw = cv2.dilate(gw.astype(np.uint8), k).astype(bool)
        for s in strand_spans(probs, WIRE_IDS, params, min_class_share=0.9):
            if s.confidence < full.segmentation.conf_min:
                continue
            n = int(s.mask.sum())
            ids, cnt = np.unique(lab[s.mask], return_counts=True)
            res = fit_residual(s.mask, full)
            w = mean_width_px(s.mask)
            strands.append({"image": stem, "supported": float((s.mask & gw).sum()) / n >= 0.5, "px": n,
                            "width": round(w, 2), "rms_px": None if res is None else round(res, 3),
                            "rms_over_width": None if res is None else round(res / max(w, 1.0), 3),
                            "ade": {names[int(i)]: round(float(c) / n, 4) for i, c in zip(ids, cnt, strict=True)}})
        for m, sc in zip(z["veg_masks"], z["veg_scores"], strict=True):
            n = int(m.sum())
            if not n or sc < full.segmentation.conf_min:
                continue
            ids, cnt = np.unique(lab[m], return_counts=True)
            vegs.append({"image": stem, "supported": float((m & gv).sum()) / n >= 0.5, "score": round(float(sc), 3),
                         "ade": {names[int(i)]: round(float(c) / n, 4) for i, c in zip(ids, cnt, strict=True)}})
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"strands": strands, "vegetation": vegs}, indent=0))
    report(strands, vegs)


def report(strands, vegs) -> None:
    sup = [s for s in strands if s["supported"]]
    uns = [s for s in strands if not s["supported"]]
    print(f"strands: {len(strands)} supported {len(sup)} unsupported {len(uns)}")
    agg = {}
    for grp, rows in (("sup", sup), ("uns", uns)):
        for r in rows:
            for c, v in r["ade"].items():
                agg.setdefault(c, {"sup": 0.0, "uns": 0.0})[grp] += v * r["px"]
    tot = {g: sum(r["px"] for r in rows) for g, rows in (("sup", sup), ("uns", uns))}
    top = sorted(agg.items(), key=lambda kv: -(kv[1]["sup"] + kv[1]["uns"]))[:14]
    for c, v in top:
        print(f"  {c:18s} supported {v['sup'] / max(tot['sup'], 1):.3f}  unsupported {v['uns'] / max(tot['uns'], 1):.3f}")
    for g, rows in (("supported", sup), ("unsupported", uns)):
        r = np.array([x["rms_over_width"] for x in rows if x["rms_over_width"] is not None])
        if len(r):
            print(f"  rms/width {g}: n {len(r)} median {np.median(r):.2f} p90 {np.percentile(r, 90):.2f} "
                  f"p95 {np.percentile(r, 95):.2f} max {r.max():.2f}")
    vs = [v for v in vegs if v["supported"]]
    vu = [v for v in vegs if not v["supported"]]
    print(f"vegetation regions: {len(vegs)} supported {len(vs)} unsupported {len(vu)}")
    veg_names = ("tree", "grass", "plant", "palm", "flower", "field")
    for g, rows in (("supported", vs), ("unsupported", vu)):
        sh = [sum(v["ade"].get(n, 0) for n in veg_names) for v in rows]
        print(f"  ADE vegetation share {g}: {sorted(round(x, 2) for x in sh)}")


if __name__ == "__main__":
    main()
