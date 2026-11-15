"""v2.13 pre-inference CLAHE and oriented black-hat (tubeness) blending, measured on validation (frozen WireNet-R34).

    python -m ml.experiments.v213_enhance --out docs/thesis/results/v213_enhance_val.jsonl

A  CLAHE on CIELAB L* (clipLimit 2.0, 8x8 tiles) on every photo before the shipped preprocessing and WireNet
   (flip TTA); probabilities re-cached into runs/cache/val-clahe.
B  Oriented black-hat: for 12 orientations 0-165 deg, closing of L* with a 1x15 line minus L*; the maximum over
   orientations, normalised by its 99.5th percentile, is a thin-dark-line response T in [0, 1]. It is blended
   into WireNet's cached probabilities by boosting the wire classes, p_c <- p_c (1 + beta T), and renormalising, so
   it can only strengthen wire evidence WireNet already has (it never invents a class).
Each variant is scored like every version: span strict / tolerant mAP50 with the shipped extractor, plus the span
count and per-class tolerant recall. Timings of the extra steps are measured at 1024 px.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

from app.core.config import CLASSES, VEGETATION
from ml.experiments.ablation_final import FINAL
from ml.experiments.harness import score
from ml.experiments.sweep_spans import make

WIRE_IDX = [CLASSES.index(c) + 1 for c in CLASSES if c != VEGETATION]


def clahe_lstar(rgb: np.ndarray, clip: float = 2.0, tiles: int = 8) -> np.ndarray:
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    lab[..., 0] = cv2.createCLAHE(clipLimit=clip, tileGridSize=(tiles, tiles)).apply(lab[..., 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)


def line_kernel(length: int, angle_deg: float) -> np.ndarray:
    k = np.zeros((length, length), np.uint8)
    c, r = length // 2, length / 2
    dx, dy = np.cos(np.radians(angle_deg)) * r, np.sin(np.radians(angle_deg)) * r
    cv2.line(k, (int(round(c - dx)), int(round(c - dy))), (int(round(c + dx)), int(round(c + dy))), 1, 1)
    return k


def tubeness(rgb: np.ndarray, length: int = 15, n_angles: int = 12) -> np.ndarray:
    L = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)[..., 0]
    resp = np.zeros(L.shape, np.float32)
    for a in np.arange(n_angles) * (180 / n_angles):
        resp = np.maximum(resp, cv2.morphologyEx(L, cv2.MORPH_BLACKHAT, line_kernel(length, a)).astype(np.float32))
    hi = float(np.percentile(resp, 99.5)) or 1.0
    return np.clip(resp / hi, 0, 1)


def blend(probs: np.ndarray, t: np.ndarray, beta: float) -> np.ndarray:
    p = probs.copy()
    p[WIRE_IDX] *= 1 + beta * t[None]
    return p / p.sum(0, keepdims=True)


def blended_cache(src: Path, dst: Path, beta: float) -> Path:
    dst.mkdir(parents=True, exist_ok=True)
    for old in dst.glob("*.npz"):
        old.unlink()
    for f in sorted(src.glob("*.npz")):
        z = dict(np.load(f))
        rgb = cv2.cvtColor(cv2.imread(f"datasets/wireframe_seg/images/val/{f.stem}.jpg"), cv2.COLOR_BGR2RGB)
        z["probs"] = blend(z["probs"].astype(np.float32), tubeness(rgb), beta).astype(np.float16)
        np.savez(dst / f.name, **z)
    return dst


def clahe_cache(dst: Path) -> Path:
    """Re-cache validation with CLAHE applied before the shipped preprocessing (mirrors ml.experiments.cache)."""
    import yaml

    from app.core.config import ROOT_DIR, load_analysis_config
    from app.pipeline.preprocess import preprocess
    from app.pipeline.segment import parse_yolo_seg_labels
    from app.pipeline.wirenet import make_segmenter, predict_probs_tta
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    seg = make_segmenter(cfg.segmentation.model_copy(update={"conf_min": 0.01}), ROOT_DIR)
    assert seg.load(), seg.error
    data = Path("datasets/wireframe_seg/data.yaml")
    img_dir = data.parent / yaml.safe_load(data.read_text())["val"]
    dst.mkdir(parents=True, exist_ok=True)
    for p in sorted(img_dir.glob("*.jpg")):
        rgb = clahe_lstar(cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB))
        lbl = Path(str(p).replace("/images/", "/labels/")).with_suffix(".txt")
        gts = parse_yolo_seg_labels(lbl.read_text() if lbl.exists() else "", rgb.shape[1], rgb.shape[0])
        pre = preprocess(rgb, cfg.preprocessing, cfg.enhancement, cfg.segmentation.imgsz)
        veg = [d for d in seg.yolo.segment(pre.x_rgb, pre.letterbox) if d.wire_class == VEGETATION]
        probs = predict_probs_tta(seg._net, np.clip(pre.x_rgb * 255 + 0.5, 0, 255).astype(np.uint8), seg._device,
                                  cfg.segmentation.wirenet_tta)
        np.savez_compressed(dst / f"{p.stem}.npz", probs=probs.astype(np.float16),
                            veg_masks=np.array([d.mask for d in veg], bool).reshape(-1, *rgb.shape[:2]),
                            veg_scores=np.array([d.score for d in veg], np.float32),
                            gt_masks=np.array([g.mask for g in gts], bool).reshape(-1, *rgb.shape[:2]),
                            gt_classes=np.array([g.wire_class for g in gts]))
    return dst


def row(name: str, cache: Path, extra: dict) -> dict:
    r = score(cache, make(FINAL))
    t = r["tolerant"]
    out = {"variant": name, **extra, "strict_map50": r["strict"]["map50"], "tolerant_map50": t["map50"],
           "spans": sum(r["spans_all_scores"].values()),
           "power_recall": t["per_class"]["power-distribution-lines"]["recall"],
           "telecom_recall": t["per_class"]["telecom-cable"]["recall"]}
    print(json.dumps(out), flush=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path("runs/cache/val-v28"))
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    rgb = cv2.cvtColor(cv2.imread(str(next(Path("datasets/wireframe_seg/images/val").glob("*.jpg")))), cv2.COLOR_BGR2RGB)
    t0 = time.perf_counter()
    for _ in range(5):
        tubeness(rgb)
    t_tube = (time.perf_counter() - t0) / 5 * 1e3
    t0 = time.perf_counter()
    for _ in range(5):
        clahe_lstar(rgb)
    t_clahe = (time.perf_counter() - t0) / 5 * 1e3
    rows = [row("shipped", a.cache, {})]
    for beta in (0.25, 0.5, 1.0, 2.0):
        rows.append(row(f"blackhat_beta{beta}", blended_cache(a.cache, Path("runs/cache/_tube"), beta),
                        {"beta": beta, "ms": t_tube}))
    rows.append(row("clahe_L", clahe_cache(Path("runs/cache/val-clahe")), {"ms": t_clahe}))
    a.out.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


if __name__ == "__main__":
    main()
