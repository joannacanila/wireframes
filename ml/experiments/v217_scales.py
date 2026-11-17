"""v2.17: does multi-scale or upsampled tiled WireNet inference recover the thin wires the 1024 px pass misses?
(validation, centerline metrics; selection only on val). The labels are 1024 px, so every variant is scored at 1024.

    flip       shipped: 1024 pass + mirrored pass (the cached probabilities, runs/cache/val-v216)
    flip_ms    + 0.85x and 1.15x passes (each mirrored)
    tiled2x    image enlarged 2x, 1024 px tiles with 192 px overlap and a linear blend, mirrored, shrunk back
    fuse       mean of flip and tiled2x probabilities

    python -m ml.experiments.v217_scales
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import numpy as np

from app.core.config import CLASSES, ROOT_DIR, load_analysis_config
from app.pipeline import centerline_eval as ce
from app.pipeline.wire_instances import strand_spans
from app.pipeline.wirenet import make_segmenter, predict_probs, predict_probs_tta, span_params
from ml.experiments import harness
from ml.experiments.v216_strands import WIRE_IDS


def tiled(net, dev, rgb, T=1024, ov=192):
    h, w = rgb.shape[:2]
    acc, wt = np.zeros((5, h, w), np.float32), np.zeros((h, w), np.float32)
    ramp = np.minimum(np.minimum(np.arange(T) + 1, np.arange(T)[::-1] + 1).astype(np.float32) / ov, 1)
    win = np.outer(ramp, ramp)
    ys = list(range(0, max(h - T, 0) + 1, T - ov))
    xs = list(range(0, max(w - T, 0) + 1, T - ov))
    if ys[-1] + T < h:
        ys.append(h - T)
    if xs[-1] + T < w:
        xs.append(w - T)
    for y in ys:
        for x in xs:
            t = np.ascontiguousarray(rgb[y:y + T, x:x + T])
            p = (predict_probs(net, t, dev) + predict_probs(net, np.ascontiguousarray(t[:, ::-1]), dev)[:, :, ::-1]) / 2
            ww = win[:t.shape[0], :t.shape[1]]
            acc[:, y:y + T, x:x + T] += p * ww
            wt[y:y + T, x:x + T] += ww
    return acc / wt


def score(probs_list, gts_list, params, min_share):
    out = {}
    for mode in ("pixel", "strands"):
        per, tally = [], ce.StrandTally()
        for probs, gts in zip(probs_list, gts_list, strict=True):
            shape = probs.shape[1:]
            if mode == "pixel":
                am = probs.argmax(0)
                preds = [ce.Pred(CLASSES[i - 1], am == i) for i in WIRE_IDS]
            else:
                preds = [ce.Pred(s.wire_class, s.mask, s.class_status != "uncertain")
                         for s in strand_spans(probs, WIRE_IDS, params, min_class_share=min_share)]
                tally.add(preds, gts, shape)
            per.append(ce.image_counts(preds, gts, shape))
        out[mode] = {"centerline": ce.summarize(per, 200), "strand_class": tally.report() if mode == "strands" else None}
    return out


def main() -> None:
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    seg = make_segmenter(cfg.segmentation, ROOT_DIR)
    assert seg.load(), seg.error
    net, dev = seg._net, seg._device
    params = span_params(cfg.segmentation)
    variants: dict[str, list] = {"flip": [], "flip_ms": [], "tiled2x": [], "fuse": []}
    gts_list, secs = [], {k: 0.0 for k in variants}
    for stem, z in harness.load(Path("runs/cache/val-v216")):
        rgb = cv2.cvtColor(cv2.imread(f"datasets/wireframe_seg/images/val/{stem}.jpg"), cv2.COLOR_BGR2RGB)
        gts_list.append([(c, m) for c, m in zip(z["gt_classes"].tolist(), z["gt_masks"], strict=True) if c in ce.WIRE_CLASSES])
        flip = z["probs"].astype(np.float32)
        variants["flip"].append(flip.astype(np.float16))  # float16 storage: 8 GB machine
        t = time.perf_counter()
        variants["flip_ms"].append(predict_probs_tta(net, rgb, dev, "flip_ms").astype(np.float16))
        secs["flip_ms"] += time.perf_counter() - t
        t = time.perf_counter()
        big = cv2.resize(rgb, (rgb.shape[1] * 2, rgb.shape[0] * 2), interpolation=cv2.INTER_CUBIC)
        p2 = tiled(net, dev, big)
        p2 = np.stack([cv2.resize(c, (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_AREA) for c in p2])
        secs["tiled2x"] += time.perf_counter() - t
        variants["tiled2x"].append(p2.astype(np.float16))
        variants["fuse"].append(((flip + p2) / 2).astype(np.float16))
        print(stem[:40], flush=True)
    res = {}
    for k, v in variants.items():
        res[k] = score([x.astype(np.float32) for x in v], gts_list, params, 0.6)
        res[k]["seconds_per_image"] = secs.get(k, 0) / len(v)
        c = res[k]["strands"]["centerline"]
        px = res[k]["pixel"]["centerline"]
        f = lambda m: f"P {m['precision']:.3f} R {m['recall']:.3f} F1 {m['f1']:.3f}"  # noqa: E731
        print(f"{k:8s} pixel telecom {f(px['telecom-cable'])} | strands wire {f(c['wire'])} power "
              f"{f(c['power-distribution-lines'])} telecom {f(c['telecom-cable'])} | {res[k]['seconds_per_image']:.1f} s")
    Path("docs/thesis/results/v217_scales_val.json").write_text(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
