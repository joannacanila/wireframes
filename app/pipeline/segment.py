"""Segmentation layer (Section 3.6.3, Algorithm 3).

Two interchangeable sources produce the same output (class, score, binary mask in
original-image coordinates):

* ``YoloSegmenter``: the fine-tuned YOLO11-seg model. The preprocessed 640 x 640 RGB
  array in [0, 1] (Algorithm 1) is passed as a BCHW tensor, so the model sees exactly
  the letterboxed input of the thesis and no second resize happens inside Ultralytics.
  Confidence filtering and per-class NMS (IoU 0.45, ``agnostic_nms=False``) run inside
  the Ultralytics predictor; masks are binarised at 0.5 and mapped to the original
  image through the letterbox geometry.
* ``AnnotationSegmenter``: rasterises YOLO-seg polygon labels supplied with the image.
  This is not a mock. It runs the geometric and risk layers on reference masks, which
  isolates catenary/CRS behaviour from segmentation error during validation. Results
  record ``segmentation_source = "annotation"``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from app.core.config import CLASSES, SegmentationConfig
from app.pipeline.preprocess import Letterbox

log = logging.getLogger(__name__)


@dataclass
class WireDetection:
    wire_class: str
    score: float
    mask: np.ndarray  # H x W bool, original image coordinates
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2


class SegmenterUnavailable(RuntimeError):
    pass


def _bbox(mask: np.ndarray) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) of the mask, from its row / column projections (no full nonzero; v2.10)."""
    rows, cols = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
    if not len(rows):
        raise ValueError("empty mask has no bounding box")
    return float(cols[0]), float(rows[0]), float(cols[-1] + 1), float(rows[-1] + 1)


class YoloSegmenter:
    source = "model"

    def __init__(self, cfg: SegmentationConfig, root: Path) -> None:
        self.cfg = cfg
        self._root = root
        self.path = (root / cfg.weights) if not Path(cfg.weights).is_absolute() else Path(cfg.weights)
        self._model = None
        self._lock = threading.Lock()
        self.error: str | None = None
        self._failed_sig: tuple[float, int] | None = None

    def load(self) -> bool:
        """Idempotent and cheap to call repeatedly: a missing file costs one stat(), and a file
        that failed to load is not retried until its mtime or size changes."""
        if self._model is not None:
            return True
        if not self.path.exists():
            self.error = f"No model file at {self._display_path()}. Train one with python -m ml.train."
            return False
        st = self.path.stat()
        sig = (st.st_mtime, st.st_size)
        if sig == self._failed_sig:
            return False
        self._failed_sig = sig  # cleared on success below
        try:
            from ultralytics import YOLO

            model = YOLO(str(self.path))
            if getattr(model, "task", None) != "segment":
                self.error = f"{self.path.name} is a '{model.task}' model; a segmentation model is required"
                return False
            names = {int(k): v for k, v in model.names.items()}
            unknown = set(names.values()) - set(CLASSES)
            if unknown:
                self.error = f"model classes {sorted(unknown)} are not WIREFRAME classes {list(CLASSES)}"
                return False
            self._model, self.names = model, names
            self.error, self._failed_sig = None, None
            return True
        except Exception as exc:  # ImportError, corrupt checkpoint, ...
            self.error = f"could not load model: {exc}"
            log.exception("model load failed")
            return False

    def _display_path(self) -> str:
        """Repo-relative when possible: absolute server paths are not shown to users."""
        try:
            return str(self.path.resolve().relative_to(self._root.resolve()))
        except ValueError:
            return self.path.name

    @property
    def ready(self) -> bool:
        return self._model is not None

    def segment(self, x_rgb: np.ndarray, lb: Letterbox) -> list[WireDetection]:
        if not self.load():
            raise SegmenterUnavailable(self.error or "model unavailable")
        import torch

        tensor = torch.from_numpy(np.ascontiguousarray(x_rgb.transpose(2, 0, 1)))[None]
        kw = dict(
            conf=self.cfg.conf_min, iou=self.cfg.iou_nms, imgsz=self.cfg.imgsz,
            agnostic_nms=False, retina_masks=True, verbose=False,
        )
        if self.cfg.device != "auto":
            kw["device"] = self.cfg.device
        with self._lock:  # Ultralytics predictors are not thread-safe
            result = self._model.predict(tensor, **kw)[0]
        if result.masks is None or result.boxes is None:
            return []
        masks = result.masks.data.cpu().numpy()
        cls = result.boxes.cls.cpu().numpy().astype(int)
        conf = result.boxes.conf.cpu().numpy()
        out = []
        for m, k, s in zip(masks, cls, conf, strict=True):
            if s < self.cfg.conf_min:
                continue
            full = lb.mask_to_original(m) >= self.cfg.mask_threshold  # BINARIZE + MAP_TO_ORIGINAL
            if full.any():
                out.append(WireDetection(self.names[int(k)], float(s), full, _bbox(full)))
        return out


def parse_yolo_seg_labels(text: str, width: int, height: int, names: tuple[str, ...] = CLASSES
                          ) -> list[WireDetection]:
    """YOLO-seg label lines: ``class x1 y1 x2 y2 ...`` with coordinates normalised to [0, 1]."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        parts = line.split()
        if not parts:
            continue
        try:
            k = int(parts[0])
            coords = np.array([float(v) for v in parts[1:]], dtype=np.float64)
        except ValueError as exc:
            raise ValueError(f"label line {n}: not numeric") from exc
        if not 0 <= k < len(names):
            raise ValueError(f"label line {n}: class index {k} outside 0..{len(names) - 1}")
        if coords.size < 6 or coords.size % 2:
            raise ValueError(f"label line {n}: a polygon needs at least three (x, y) pairs")
        if coords.min() < -1e-6 or coords.max() > 1 + 1e-6:
            raise ValueError(f"label line {n}: coordinates must be normalised to [0, 1]")
        poly = coords.reshape(-1, 2) * (width, height)
        mask = np.zeros((height, width), np.uint8)
        cv2.fillPoly(mask, [np.round(poly * 16).astype(np.int32)], 1, cv2.LINE_8, shift=4)
        m = mask.astype(bool)
        if m.any():
            out.append(WireDetection(names[k], 1.0, m, _bbox(m)))
    return out


def mask_to_polygons(mask: np.ndarray) -> list[list[list[int]]]:
    """Exact pixel-boundary contours (outer + holes) for compact, lossless overlay drawing.

    Rendered with the even-odd fill rule, these reproduce the binary mask.
    """
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    return [c.reshape(-1, 2).tolist() for c in contours if len(c) >= 3]
