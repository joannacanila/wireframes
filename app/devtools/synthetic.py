"""Synthetic pole scenes with exact ground truth (tests and UI development only).

Each wire is a true catenary y_up = a cosh((x - b)/a) + c, drawn with a given thickness,
and its polygon label is the exact outline of that stroke. Because a, b, c are known,
the expected sag ratio is known in closed form, which lets the tests check the whole
geometric path (rasterise -> skeleton -> LM fit -> delta/L) against the truth.

Images produced here are synthetic and are labelled as such wherever they are used.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from app.core.config import CLASSES
from app.pipeline.catenary import _chord_sag

ABANDONED, POWER, TELECOM = "abandoned-wire", "power-distribution-lines", "telecom-cable"


@dataclass(frozen=True)
class WireSpec:
    wire_class: str
    a: float  # pixels
    b: float  # x of lowest point
    y_attach: float  # image y of the left support
    x0: float
    x1: float
    thickness: int


@dataclass
class Scene:
    rgb: np.ndarray
    labels: str  # YOLO-seg lines
    wires: list[WireSpec]
    truth_sag_ratio: list[float]


def _curve(spec: WireSpec, n: int = 600) -> np.ndarray:
    xs = np.linspace(spec.x0, spec.x1, n)
    yu = spec.a * np.cosh((xs - spec.b) / spec.a)
    y_img = spec.y_attach + (yu[0] - yu)  # flip: image y grows downward
    return np.stack([xs, y_img], axis=1)


def _outline(curve: np.ndarray, half: float) -> np.ndarray:
    d = np.gradient(curve, axis=0)
    nrm = np.stack([-d[:, 1], d[:, 0]], axis=1)
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True)
    return np.concatenate([curve + nrm * half, (curve - nrm * half)[::-1]])


def make_scene(width: int = 1600, height: int = 1000, seed: int = 7, sag_scale: float = 1.0,
               clutter: int = 9) -> Scene:
    """``sag_scale`` > 1 deepens every sag (a is divided by it); ``clutter`` = building count."""
    rng = np.random.default_rng(seed)
    yy = np.linspace(0, 1, height)[:, None, None]
    sky = (np.array([196, 214, 232]) * (1 - yy) + np.array([236, 238, 240]) * yy).astype(np.float32)
    img = np.broadcast_to(sky, (height, width, 3)).copy()
    # buildings: low-frequency clutter
    for _ in range(clutter):
        x = int(rng.integers(0, width)); w = int(rng.integers(120, 320)); h = int(rng.integers(180, 420))
        shade = rng.integers(120, 190)
        img[height - h:, x:x + w] = (shade, shade - 6, shade - 14)
        for wy in range(height - h + 20, height - 20, 46):  # windows: high-frequency detail
            for wx in range(x + 14, min(width, x + w) - 20, 38):
                img[wy:wy + 22, wx:wx + 18] = (70, 84, 100)
    # poles
    for px in (150, 1450):
        img[120:, px - 14:px + 14] = (92, 88, 82)
        img[150:166, px - 90:px + 90] = (80, 76, 70)

    # a chosen so that delta/L spans realistic distribution-line sag ratios (~0.8% to 5%)
    specs = [
        WireSpec(POWER, a=19800, b=800, y_attach=160, x0=165, x1=1435, thickness=9),
        WireSpec(POWER, a=10600, b=800, y_attach=200, x0=165, x1=1435, thickness=8),
        WireSpec(TELECOM, a=6350, b=800, y_attach=290, x0=165, x1=1435, thickness=14),
        WireSpec(TELECOM, a=5000, b=672, y_attach=350, x0=165, x1=1180, thickness=5),  # a fiber drop
        WireSpec(ABANDONED, a=1840, b=532, y_attach=420, x0=165, x1=900, thickness=7),
    ]
    specs = [WireSpec(sp.wire_class, sp.a / sag_scale, sp.b, sp.y_attach, sp.x0, sp.x1, sp.thickness) for sp in specs]
    color = [(30, 30, 32), (30, 30, 32), (18, 18, 20), (40, 40, 44), (55, 50, 46)]
    lines, truth = [], []
    for spec, col in zip(specs, color, strict=True):
        c = _curve(spec)
        poly = _outline(c, spec.thickness / 2)
        cv2.fillPoly(img, [np.round(poly * 16).astype(np.int32)], col, cv2.LINE_AA, shift=4)
        norm = poly / (width, height)
        k = CLASSES.index(spec.wire_class)
        lines.append(f"{k} " + " ".join(f"{v:.6f}" for v in np.clip(norm, 0, 1).ravel()))
        length, delta, _, _ = _chord_sag(spec.a, spec.b, 0.0, spec.x0, spec.x1)
        truth.append(delta / length)
    img += rng.normal(0, 3.0, img.shape).astype(np.float32)
    rgb = np.clip(img, 0, 255).astype(np.uint8)
    return Scene(rgb, "\n".join(lines) + "\n", specs, truth)
