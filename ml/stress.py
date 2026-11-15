"""Eight real-world capture conditions for the stress-test benchmark (v2.8).

Each condition is deterministic per image name (seeded from its hash) and scaled to the image
size, so every model and preprocessing variant is scored on identical inputs.

    clean      unchanged
    lowlight   gamma 2.4 (dusk underexposure) + Gaussian sensor noise sigma 6
    glare      backlit / washed-out sky: exposure x1.6 + 60, local contrast halved
    motion     handheld shake: linear kernel 1.5% of the longer side, random angle
    defocus    disk kernel, radius 0.5% of the longer side
    jpeg       JPEG quality 20 (blocking and ringing)
    lowres     480 px longer side, upscaled back (a low-resolution mobile upload)
    rainhaze   atmospheric haze (airlight 220, alpha 0.35) + slanted rain streaks
"""

from __future__ import annotations

import hashlib

import cv2
import numpy as np

from ml.degrade import disk_kernel, motion_kernel

CONDITIONS = ("clean", "lowlight", "glare", "motion", "defocus", "jpeg", "lowres", "rainhaze")
# v2.10 specificity control for the glare gate (not part of the 8-condition benchmark): a bright,
# overcast-looking exposure lift that keeps local contrast, i.e. glare without the washed-out wires
EXTRA_CONDITIONS = ("brightsky", "night")
# v2.11 "night": light conductors against a dark sky (street-lit night photo). Synthetic: luminance is
# inverted (wires become light, sky dark), compressed to a dark range and given sensor noise; chroma kept.


def _rng(name: str, cond: str) -> np.random.Generator:
    return np.random.default_rng(int(hashlib.sha256(f"{cond}:{name}".encode()).hexdigest()[:8], 16))


def apply_condition(rgb: np.ndarray, cond: str, name: str) -> np.ndarray:
    if cond == "clean":
        return rgb
    rng = _rng(name, cond)
    h, w = rgb.shape[:2]
    L = max(h, w)
    img = rgb.astype(np.float32)
    if cond == "lowlight":
        img = 255.0 * (img / 255.0) ** 2.4 + rng.normal(0, 6, img.shape)
    elif cond == "night":
        lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
        lab[..., 0] = np.clip((255.0 - lab[..., 0]) * 0.55 + rng.normal(0, 4, (h, w)), 0, 255)
        return cv2.cvtColor(lab.astype(np.uint8), cv2.COLOR_LAB2RGB)
    elif cond == "brightsky":
        # same exposure lift as glare, local contrast kept: bright sky, wires still dark against it
        img = np.clip(img * 1.6 + 60, 0, 255)
        local = cv2.GaussianBlur(img, (0, 0), 0.02 * L)
        lo = cv2.GaussianBlur(rgb.astype(np.float32), (0, 0), 0.02 * L)
        img = local + 1.0 * (rgb.astype(np.float32) - lo)
    elif cond == "glare":
        img = img * 1.6 + 60
        local = cv2.GaussianBlur(img, (0, 0), 0.02 * L)
        img = local + 0.5 * (img - local)
    elif cond == "motion":
        n = max(3, int(round(0.015 * L)) | 1)
        img = cv2.filter2D(img, -1, motion_kernel(n, float(rng.uniform(0, 180))))
    elif cond == "defocus":
        img = cv2.filter2D(img, -1, disk_kernel(max(1, int(round(0.005 * L)))))
    elif cond == "jpeg":
        ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 20])
        return cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
    elif cond == "lowres":
        s = 480 / L
        small = cv2.resize(rgb, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
        return cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)
    elif cond == "rainhaze":
        img = img * 0.65 + 0.35 * 220.0
        streaks = np.zeros((h, w), np.float32)
        ang = float(rng.uniform(-20, 20))
        for _ in range(int(0.0015 * h * w / 10)):
            x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
            ln = int(rng.integers(int(0.01 * L), int(0.03 * L) + 2))
            dx, dy = int(ln * np.sin(np.deg2rad(ang))), ln
            cv2.line(streaks, (x, y), (x + dx, y + dy), 1.0, 1)
        streaks = cv2.GaussianBlur(streaks, (0, 0), 0.6)[..., None]
        img = img * (1 - 0.35 * streaks) + 235.0 * 0.35 * streaks
    else:
        raise ValueError(f"unknown condition {cond!r}")
    return np.clip(img, 0, 255).astype(np.uint8)
