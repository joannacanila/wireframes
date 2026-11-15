"""Synthetic field degradation for the blurred / low-contrast test pass (v2.7).

Every image gets exactly one blur and a contrast reduction, chosen deterministically from its
file name, so the degraded test set is identical on every run:

  blur      motion blur (hand shake): length 1.2% of the longer side, random angle
            defocus (missed focus):   disk radius 0.4% of the longer side
            Gaussian (soft lens/haze): sigma 0.3% of the longer side
  contrast  pixel values pulled towards the image mean by a factor 0.5-0.65, brightness +-20

Sizes are relative to the image, so a 1024 px image gets a ~12 px motion streak, a ~4 px
defocus disk or sigma ~3 px. These are moderate, common field conditions, not worst cases.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np


def _rng(name: str) -> np.random.Generator:
    return np.random.default_rng(int(hashlib.sha256(name.encode()).hexdigest()[:8], 16))


def motion_kernel(length: int, angle_deg: float) -> np.ndarray:
    k = np.zeros((length, length), np.float32)
    k[length // 2, :] = 1.0
    rot = cv2.getRotationMatrix2D((length / 2 - 0.5, length / 2 - 0.5), angle_deg, 1.0)
    k = cv2.warpAffine(k, rot, (length, length))
    return k / max(k.sum(), 1e-6)


def disk_kernel(radius: int) -> np.ndarray:
    d = 2 * radius + 1
    k = cv2.circle(np.zeros((d, d), np.float32), (radius, radius), radius, 1.0, -1)
    return k / k.sum()


def degrade(rgb: np.ndarray, name: str) -> tuple[np.ndarray, dict]:
    rng = _rng(name)
    long_side = max(rgb.shape[:2])
    kind = ["motion", "defocus", "gaussian"][int(rng.integers(0, 3))]
    img = rgb.astype(np.float32)
    if kind == "motion":
        length = max(3, int(round(0.012 * long_side)) | 1)
        angle = float(rng.uniform(0, 180))
        img = cv2.filter2D(img, -1, motion_kernel(length, angle))
        info = {"blur": kind, "length_px": length, "angle_deg": round(angle, 1)}
    elif kind == "defocus":
        radius = max(1, int(round(0.004 * long_side)))
        img = cv2.filter2D(img, -1, disk_kernel(radius))
        info = {"blur": kind, "radius_px": radius}
    else:
        sigma = 0.003 * long_side
        img = cv2.GaussianBlur(img, (0, 0), sigma)
        info = {"blur": kind, "sigma_px": round(sigma, 2)}
    alpha = float(rng.uniform(0.5, 0.65))
    beta = float(rng.uniform(-20, 20))
    img = (img - img.mean()) * alpha + img.mean() + beta
    info |= {"contrast_factor": round(alpha, 3), "brightness_shift": round(beta, 1)}
    return np.clip(img, 0, 255).astype(np.uint8), info


def degrade_split(img_dir: Path, lbl_dir: Path, out_root: Path) -> Path:
    """Write a degraded copy of one split (images + unchanged labels) and a data.yaml for it."""
    import json

    import yaml

    (out_root / "images" / "test").mkdir(parents=True, exist_ok=True)
    (out_root / "labels" / "test").mkdir(parents=True, exist_ok=True)
    log = {}
    for p in sorted(img_dir.glob("*.jpg")):
        rgb = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
        out, info = degrade(rgb, p.name)
        cv2.imwrite(str(out_root / "images" / "test" / p.name), cv2.cvtColor(out, cv2.COLOR_RGB2BGR),
                    [cv2.IMWRITE_JPEG_QUALITY, 95])
        lbl = lbl_dir / f"{p.stem}.txt"
        (out_root / "labels" / "test" / lbl.name).write_text(lbl.read_text() if lbl.exists() else "")
        log[p.name] = info
    (out_root / "degradations.json").write_text(json.dumps(log, indent=2))
    from app.core.config import CLASSES
    (out_root / "data.yaml").write_text(yaml.safe_dump({"train": "images/test", "val": "images/test",
                                                        "test": "images/test", "names": dict(enumerate(CLASSES))},
                                                       sort_keys=False))
    return out_root / "data.yaml"
