"""Depth Anything V2 Small as a night-sky guard for YOLO vegetation (v2.12; CPU, validation + one night photo).

    python -m ml.experiments.depth_veg_guard --out docs/thesis/results/v212_veg_guard_val.json \
        --night runs/ui_inputs/P-D67F60_night.jpg

On the night pole P-D67F60 YOLO marks part of the black sky as Vegetation, which creates telecom
"vegetation contact" hazards. Candidate guards, applied per YOLO vegetation instance:
  G1 depth        drop if the instance lies in the far (sky) depth range: median relative inverse depth
                  of its pixels below the image's 10th percentile + 5% of the range
  G2 photometric  drop if near-black and textureless: mean L* < 15 and std L* < 4 inside the mask
  G3 both         G1 and G2
Validation (daytime, labelled vegetation, cached YOLO outputs): vegetation AP50 / precision / recall and
the number of instances each guard removes; the guard must not remove true vegetation. The night photo
(no labels) shows which instances each guard removes; a figure is written for the thesis.
Depth Anything V2 Small (Apache-2.0) runs on the CPU so it does not compete with GPU training.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from app.core.config import CLASSES, ROOT_DIR, VEGETATION, load_analysis_config
from app.pipeline import evaluation as ev

MODEL_ID = "depth-anything/Depth-Anything-V2-Small-hf"


def depth_model():
    from transformers import pipeline
    return pipeline("depth-estimation", model=MODEL_ID, device="cpu")


def relative_depth(pipe, rgb: np.ndarray) -> np.ndarray:
    """Relative inverse depth (larger = closer), resized to the image, normalised to 0-1."""
    from PIL import Image
    out = pipe(Image.fromarray(rgb))["predicted_depth"]
    d = out.squeeze().cpu().numpy().astype(np.float32)
    d = cv2.resize(d, (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_LINEAR)
    lo, hi = np.percentile(d, 1), np.percentile(d, 99)
    return np.clip((d - lo) / max(hi - lo, 1e-6), 0, 1)


def guards(mask: np.ndarray, depth: np.ndarray, l_star: np.ndarray) -> dict:
    far = np.percentile(depth, 10) + 0.05
    dmed = float(np.median(depth[mask])) if mask.any() else 1.0
    lm, ls = float(l_star[mask].mean()), float(l_star[mask].std())
    g1, g2 = dmed < far, (lm < 15 and ls < 4)
    return {"G1": bool(g1), "G2": bool(g2), "G3": bool(g1 and g2), "depth_median": dmed, "far_cut": float(far),
            "mean_l": lm, "std_l": ls}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path("runs/cache/val-v28"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--night", type=Path)
    ap.add_argument("--figures", type=Path, default=Path("docs/thesis/figures"))
    a = ap.parse_args()
    pipe = depth_model()
    rows, per_guard = [], {g: {"strict": [], "removed": 0, "removed_matched": 0} for g in ("none", "G1", "G2", "G3")}
    for f in sorted(a.cache.glob("*.npz")):
        z = np.load(f)
        rgb = cv2.cvtColor(cv2.imread(f"datasets/wireframe_seg/images/val/{f.stem}.jpg"), cv2.COLOR_BGR2RGB)
        depth = relative_depth(pipe, rgb)
        L = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)[..., 0].astype(np.float32) * (100 / 255)
        gts = [ev.Instance(c, m) for c, m in zip(z["gt_classes"].tolist(), z["gt_masks"], strict=True) if c == VEGETATION]
        vm, vs = z["veg_masks"], z["veg_scores"]
        info = [guards(m, depth, L) for m in vm]
        for g in per_guard:
            keep = [i for i in range(len(vm)) if g == "none" or not info[i][g]]
            preds = [ev.Instance(VEGETATION, vm[i], float(vs[i])) for i in keep]
            per_guard[g]["strict"].append((preds, gts))
            if g != "none":
                for i in set(range(len(vm))) - set(keep):
                    per_guard[g]["removed"] += 1
                    inter = max(((vm[i] & t.mask).sum() / max((vm[i] | t.mask).sum(), 1) for t in gts), default=0.0)
                    per_guard[g]["removed_matched"] += int(inter >= 0.5)
        rows.append({"image": f.stem, "instances": [{**x, "score": float(s)} for x, s in zip(info, vs, strict=True)]})
        print(f.stem[-10:], len(vm), "veg instances;", {g: sum(x[g] for x in info) for g in ("G1", "G2", "G3")}, flush=True)
    res = {}
    for g, d in per_guard.items():
        r = ev.evaluate_segmentation(d["strict"], 0.2)
        pc = r.per_class.get(VEGETATION)
        res[g] = {"veg_ap50": pc.ap50 if pc else None, "precision": pc.precision if pc else None,
                  "recall": pc.recall if pc else None, "n_pred": pc.n_pred if pc else 0,
                  "removed": d["removed"], "removed_true_vegetation": d["removed_matched"]}
        print(g, res[g], flush=True)
    night = None
    if a.night:
        from app.pipeline.preprocess import preprocess
        from app.pipeline.segment import YoloSegmenter
        cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
        yolo = YoloSegmenter(cfg.segmentation.model_copy(update={"device": "cpu"}), ROOT_DIR)
        assert yolo.load(), yolo.error
        rgb = cv2.cvtColor(cv2.imread(str(a.night)), cv2.COLOR_BGR2RGB)
        pre = preprocess(rgb, cfg.preprocessing, cfg.enhancement, cfg.segmentation.imgsz)
        veg = [d for d in yolo.segment(pre.x_rgb, pre.letterbox) if d.wire_class == VEGETATION]
        depth = relative_depth(pipe, rgb)
        L = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)[..., 0].astype(np.float32) * (100 / 255)
        night = [{**guards(d.mask, depth, L), "score": float(d.score), "pixels": int(d.mask.sum())} for d in veg]
        vis = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        for d, g in zip(veg, night, strict=True):
            col = (60, 60, 230) if g["G3"] else (60, 200, 60)
            cont, _ = cv2.findContours(d.mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(vis, cont, -1, col, 2)
        dvis = cv2.applyColorMap((depth * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
        a.figures.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(a.figures / "v212_night_veg_guard.jpg"), np.hstack([vis, dvis]), [cv2.IMWRITE_JPEG_QUALITY, 88])
        print("night instances:", night, flush=True)
    a.out.write_text(json.dumps({"model": MODEL_ID, "guards": res, "night": night, "val": rows}, indent=1, default=float))
    _ = CLASSES


if __name__ == "__main__":
    main()
