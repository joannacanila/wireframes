"""Dual-path preprocessing (Section 3.6.1, Algorithm 1).

RGB path   : letterbox to the model input (segmentation.imgsz, e.g. 1024 x 1024; aspect ratio
             preserved by padding), scale to [0, 1].
Gray path  : grayscale, direct resize to 640 x 640 (no padding: a padded border would
             add artificial edges to the spectrum), 5 x 5 Gaussian filter.

The letterbox geometry is returned so that masks predicted on the 640 x 640 input can
be mapped back to the original image (Algorithm 3, MAP_TO_ORIGINAL).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from app.core.config import PreprocessingConfig


@dataclass(frozen=True)
class Letterbox:
    scale: float  # original px -> model px
    pad_x: int  # left padding in model px
    pad_y: int  # top padding in model px
    new_w: int  # resized content width in model px
    new_h: int
    size: int
    orig_w: int
    orig_h: int

    def mask_to_original(self, mask: np.ndarray) -> np.ndarray:
        """Crop the padding from a size x size mask and resize it to the original image."""
        if mask.shape != (self.size, self.size):
            raise ValueError(f"expected a {self.size}x{self.size} mask, got {mask.shape}")
        crop = mask[self.pad_y : self.pad_y + self.new_h, self.pad_x : self.pad_x + self.new_w]
        out = cv2.resize(
            crop.astype(np.float32), (self.orig_w, self.orig_h), interpolation=cv2.INTER_LINEAR
        )
        return out

    def point_to_original(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.pad_x) / self.scale, (y - self.pad_y) / self.scale


@dataclass(frozen=True)
class Preprocessed:
    x_rgb: np.ndarray  # size x size x 3, float32 in [0, 1]
    gray: np.ndarray  # size x size, float32 in [0, 255], smoothed if configured
    letterbox: Letterbox
    enhancement: list[str] | None = None  # steps applied to the RGB path only
    quality: dict | None = None


def letterbox_geometry(w: int, h: int, size: int) -> Letterbox:
    scale = min(size / w, size / h)
    new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
    return Letterbox(scale, (size - new_w) // 2, (size - new_h) // 2, new_w, new_h, size, w, h)


def letterbox(rgb: np.ndarray, size: int, fill: int) -> tuple[np.ndarray, Letterbox]:
    h, w = rgb.shape[:2]
    scale = min(size / w, size / h)
    new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    resized = cv2.resize(rgb, (new_w, new_h), interpolation=interp)
    pad_x = (size - new_w) // 2
    pad_y = (size - new_h) // 2
    canvas = np.full((size, size, 3), fill, dtype=np.uint8)
    canvas[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = resized
    return canvas, Letterbox(scale, pad_x, pad_y, new_w, new_h, size, w, h)


def grayscale_path(rgb: np.ndarray, cfg: PreprocessingConfig) -> np.ndarray:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    interp = cv2.INTER_AREA if min(gray.shape) > cfg.size else cv2.INTER_LINEAR
    g = cv2.resize(gray, (cfg.size, cfg.size), interpolation=interp).astype(np.float32)
    if cfg.gaussian_smoothing:
        k = cfg.gaussian_kernel
        g = cv2.GaussianBlur(g, (k, k), 0)  # sigma derived from kernel size
    return g


def to_working_scale(rgb: np.ndarray, long_side: int) -> np.ndarray:
    """Downscale (never upscale) so the longer side is ``long_side``: the enhancement scale."""
    h, w = rgb.shape[:2]
    s = long_side / max(h, w)
    return rgb if s >= 1 else cv2.resize(rgb, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)


def preprocess(rgb: np.ndarray, cfg: PreprocessingConfig, enhancement=None, model_size: int | None = None,
               strong: bool = False) -> Preprocessed:
    """Algorithm 1. ``enhancement`` (an EnhancementConfig) touches the RGB path only.

    ``model_size`` is the segmentation input (``segmentation.imgsz``); the gray path always
    stays at ``cfg.size`` (640), so the VNI does not change with the model's input size.
    ``strong`` selects the fallback enhancement profile. The letterbox geometry is always
    computed against the *original* image size, so masks map back to original coordinates
    whether or not the RGB path was enhanced at working scale.
    """
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
        raise ValueError("preprocess expects an H x W x 3 uint8 RGB array")
    size = model_size or cfg.size
    lb = letterbox_geometry(rgb.shape[1], rgb.shape[0], size)  # geometry from the original
    applied, quality, model_rgb = None, None, rgb
    if enhancement is not None and (enhancement.mode != "off" or strong):
        from app.pipeline.enhance import enhance, enhance_strong

        src, pre_steps = rgb, []
        if getattr(enhancement, "deblock", False):
            from app.pipeline.enhance import blockiness, deblock
            # at the original resolution: the 8 px grid is only aligned before any resize
            if blockiness(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)) >= enhancement.deblock_blockiness_min:
                src, pre_steps = deblock(rgb, enhancement.deblock_alpha, enhancement.deblock_beta), ["deblock"]
        work = to_working_scale(src, enhancement.working_long_side)
        e = enhance_strong(work, enhancement) if strong else enhance(work, enhancement)
        model_rgb, applied, quality = e.rgb, pre_steps + e.applied, e.quality.as_dict() if e.quality else None
    boxed = cv2.resize(model_rgb, (lb.new_w, lb.new_h), interpolation=cv2.INTER_AREA
                       if model_rgb.shape[1] > lb.new_w else cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), cfg.letterbox_fill, dtype=np.uint8)
    canvas[lb.pad_y: lb.pad_y + lb.new_h, lb.pad_x: lb.pad_x + lb.new_w] = boxed
    x_rgb = canvas.astype(np.float32) / 255.0
    # The VNI path always starts from the original photograph (Section 3.6.2).
    return Preprocessed(x_rgb=x_rgb, gray=grayscale_path(rgb, cfg), letterbox=lb,
                        enhancement=applied, quality=quality)
