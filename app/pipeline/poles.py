"""Pole detection (v2.16): a fine-tuned YOLO11n box detector (ml/poles/train.py) used as structural context only.

What it is used for
    * reporting where the poles are, and which wire ends lie at a pole (``ends_at_pole``)
    * optional, measured on validation: a wire that disappears behind a pole may be bridged across a longer gap,
      but only when the straight gap actually crosses a detected pole box (``wire_instances.stitch_spans``)

What it is never used for: creating a wire, joining two fragments that fail the direction / lateral tests, or
changing a CRS. The detector's training data label every post ("poste": utility, lamp and sign posts), so a box
means "pole-like structure", not "utility pole".

v2.17: the Street View detector finds no pole at all on upward-looking kerbside photos (domain gap: level 640 px
Curitiba panoramas). Two further backends, selected on the LVIS-derived validation split (ml/poles/eval_detectors.py):
    * ``grounding-dino:<hf id>``  open-vocabulary detector prompted with "utility pole." (Apache-2.0, no training)
    * a ``.pt`` YOLO checkpoint fine-tuned on LVIS ``telephone_pole`` (ml/poles/lvis_prepare.py, train_lvis)
``make_pole_detector`` picks the backend from ``pole_weights``; ``clean_boxes`` removes duplicate and group boxes.
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


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def clean_boxes(dets: list[dict], iou: float = 0.5, group_n: int = 2) -> list[dict]:
    """Greedy NMS, then drop a box that holds the centres of ``group_n`` or more kept boxes (a box around a
    cluster of poles is not a pole). Zero-shot detectors return both kinds."""
    keep: list[dict] = []
    for d in sorted(dets, key=lambda d: -d["confidence"]):
        if all(_iou(d["bbox"], k["bbox"]) < iou for k in keep):
            keep.append(d)
    out = []
    for d in keep:
        x0, y0, x1, y1 = d["bbox"]
        inside = sum(1 for k in keep if k is not d and x0 <= (k["bbox"][0] + k["bbox"][2]) / 2 <= x1
                     and y0 <= (k["bbox"][1] + k["bbox"][3]) / 2 <= y1)
        if inside < group_n:
            out.append(d)
    return out


class ZeroShotPoleDetector:
    """Open-vocabulary pole detection (Grounding DINO through Hugging Face transformers). Same interface as
    PoleDetector: ``detect(rgb)`` -> boxes in ``rgb`` pixels or None when the model cannot load."""

    def __init__(self, model_id: str, conf: float = 0.3, prompt: str = "utility pole.", device: str = "auto") -> None:
        self.model_id, self.conf, self.prompt, self.device = model_id, conf, prompt, device
        self._model = self._proc = None
        self._lock, self.error = threading.Lock(), None

    def load(self) -> bool:
        if self._model is not None:
            return True
        try:
            import torch
            from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
            self._dev = self.device if self.device != "auto" else ("mps" if torch.backends.mps.is_available() else "cpu")
            self._proc = AutoProcessor.from_pretrained(self.model_id)
            self._model = AutoModelForZeroShotObjectDetection.from_pretrained(self.model_id).to(self._dev).eval()
            return True
        except Exception as exc:  # offline without a cached copy, missing library
            self.error = f"zero-shot pole detector unavailable: {exc}"
            log.warning(self.error)
            return False

    def raw(self, rgb: np.ndarray, threshold: float) -> list[dict] | None:
        if not self.load():
            return None
        import torch
        from PIL import Image
        im = Image.fromarray(rgb)
        with self._lock, torch.no_grad():
            inp = self._proc(images=im, text=self.prompt, return_tensors="pt").to(self._dev)
            out = self._model(**inp)
            r = self._proc.post_process_grounded_object_detection(
                out, inp.input_ids, threshold=threshold, text_threshold=threshold, target_sizes=[im.size[::-1]])[0]
            r = {k: r[k].cpu() for k in ("boxes", "scores")}
            del inp, out
            if self._dev == "mps":  # varied image sizes otherwise grow the MPS cache without bound (8 GB machines)
                torch.mps.empty_cache()
        return [{"bbox": [round(float(v), 1) for v in b], "confidence": round(float(c), 3)}
                for b, c in zip(r["boxes"].numpy(), r["scores"].numpy(), strict=True)]

    def detect(self, rgb: np.ndarray) -> list[dict] | None:
        found = self.raw(rgb, self.conf)
        return None if found is None else clean_boxes(found)


def make_pole_detector(spec: str, conf: float, root: Path, device: str = "auto"):
    """``pole_weights`` -> detector: "grounding-dino:<hf id>" or a YOLO .pt path (relative to the project root)."""
    if spec.startswith("grounding-dino:"):
        return ZeroShotPoleDetector(spec.split(":", 1)[1], conf, device=device)
    p = Path(spec)
    return PoleDetector(p if p.is_absolute() else root / p, conf, device=device)
