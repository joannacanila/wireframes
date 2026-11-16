"""Pole detection (v2.16): a fine-tuned YOLO11n box detector (ml/poles/train.py) used as structural context only.

What it is used for
    * reporting where the poles are, and which wire ends lie at a pole (``ends_at_pole``)
    * optional, measured on validation: a wire that disappears behind a pole may be bridged across a longer gap,
      but only when the straight gap actually crosses a detected pole box (``wire_instances.stitch_spans``)

What it is never used for: creating a wire, joining two fragments that fail the direction / lateral tests, or
changing a CRS. The detector's training data label every post ("poste": utility, lamp and sign posts), so a box
means "pole-like structure", not "utility pole".
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


class PoleDetector:
    def __init__(self, weights: Path, conf: float = 0.35, imgsz: int = 640, device: str = "auto") -> None:
        self.weights, self.conf, self.imgsz, self.device = Path(weights), conf, imgsz, device
        self._model, self._lock, self.error = None, threading.Lock(), None

    def load(self) -> bool:
        if self._model is not None:
            return True
        if not self.weights.exists():
            self.error = f"no pole weights at {self.weights.name}"
            return False
        try:
            import torch
            from ultralytics import YOLO
            self._dev = self.device if self.device != "auto" else ("mps" if torch.backends.mps.is_available() else "cpu")
            self._model = YOLO(str(self.weights))
            return True
        except Exception as exc:  # corrupt file, missing library
            self.error = f"pole detector unavailable: {exc}"
            log.warning(self.error)
            return False

    def detect(self, rgb: np.ndarray) -> list[dict] | None:
        """[{"bbox": [x0, y0, x1, y1], "confidence"}] in ``rgb`` pixel coordinates, or None when unavailable."""
        if not self.load():
            return None
        with self._lock:
            r = self._model.predict(rgb[..., ::-1], imgsz=self.imgsz, conf=self.conf, device=self._dev, verbose=False)[0]
        boxes = r.boxes.xyxy.cpu().numpy() if r.boxes is not None else np.zeros((0, 4))
        confs = r.boxes.conf.cpu().numpy() if r.boxes is not None else np.zeros(0)
        return [{"bbox": [round(float(v), 1) for v in b], "confidence": round(float(c), 3)}
                for b, c in zip(boxes, confs, strict=True)]


def ends_at_pole(endpoints, poles: list[dict], margin: float) -> int:
    """How many of a span's two endpoints ((x, y) pairs) lie inside a pole box grown by ``margin`` px."""
    n = 0
    for x, y in endpoints or []:
        for p in poles:
            x0, y0, x1, y1 = p["bbox"]
            if x0 - margin <= x <= x1 + margin and y0 - margin <= y <= y1 + margin:
                n += 1
                break
    return n
