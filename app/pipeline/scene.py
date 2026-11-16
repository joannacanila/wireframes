"""Pretrained scene parsing for ground-structure suppression (v2.13).

WireNet traces any thin dark line. At street level that includes road and kerb edges, manhole rims and
pavement joints, which can then be scored as sagging "wires". A pretrained ADE20K scene parser
(UperNet + ConvNeXt-Tiny, openmmlab/upernet-convnext-tiny, MIT licence, 60 M parameters, 0.26 s on the M1 GPU at
384 px) labels every pixel; a wire candidate lying mostly on ground classes is suppressed (it is still listed,
with the reason, in the diagnostics). Overhead wires are seen against sky, buildings or trees, so a span lying on
the road surface is not an overhead conductor. Fence and railing pixels are rarely labelled as such at this
resolution (the fence on the v2.13 problem photo is labelled "building"), so they are not used.
"""

from __future__ import annotations

import logging
import threading

import numpy as np

log = logging.getLogger(__name__)

MODEL_ID = "openmmlab/upernet-convnext-tiny"
# UperNet's pyramid pooling (1, 2, 3, 6) needs a feature map divisible by 6 on MPS: 384 / 32 = 12. 384 px runs in
# 0.26 s (576 px: 0.54 s) and its ground mask agrees with 576 px on 98.8% of the problem photo's pixels.
INPUT = 384
VEGETATION_CLASSES = ("tree", "grass", "plant", "palm", "flower", "field")
POLE_CLASSES = ("pole", "streetlight", "tower")
GROUND = ("road", "sidewalk", "floor", "earth", "path", "grass", "field", "sand", "dirt track", "runway", "rug")


class SceneParser:
    def __init__(self, device: str = "auto") -> None:
        self.device, self._net, self._proc, self._lock = device, None, None, threading.Lock()
        self.error: str | None = None
        self.ground_ids: list[int] = []
        self.vegetation_ids: list[int] = []
        self.pole_ids: list[int] = []

    def load(self) -> bool:
        if self._net is not None:
            return True
        try:
            import torch
            from transformers import AutoImageProcessor, AutoModelForSemanticSegmentation
            dev = self.device if self.device != "auto" else ("mps" if torch.backends.mps.is_available() else "cpu")
            self._proc = AutoImageProcessor.from_pretrained(MODEL_ID)
            self._net = AutoModelForSemanticSegmentation.from_pretrained(MODEL_ID).to(dev).eval()
            self._dev = dev
            names = {v: int(k) for k, v in self._net.config.id2label.items()}
            self.ground_ids = [names[n] for n in GROUND if n in names]
            self.vegetation_ids = [names[n] for n in VEGETATION_CLASSES if n in names]
            self.pole_ids = [names[n] for n in POLE_CLASSES if n in names]
            return True
        except Exception as exc:  # no network on first run, missing library
            self.error = f"scene parser unavailable: {exc}"
            log.warning(self.error)
            return False

    def labels(self, rgb: np.ndarray) -> np.ndarray | None:
        """ADE20K class index per pixel at the image's resolution (uint8), or None if unavailable."""
        if not self.load():
            return None
        import torch
        from PIL import Image
        with self._lock, torch.no_grad():
            x = self._proc(images=Image.fromarray(rgb), return_tensors="pt",
                           size={"height": INPUT, "width": INPUT})["pixel_values"].to(self._dev)
            lo = self._net(pixel_values=x).logits
            lab = torch.nn.functional.interpolate(lo, size=rgb.shape[:2], mode="bilinear").argmax(1)[0]
            return lab.to(torch.uint8).cpu().numpy()

    def ground_mask(self, labels: np.ndarray) -> np.ndarray:
        return np.isin(labels, self.ground_ids)

    def context(self, labels: np.ndarray, min_frac: float = 0.002) -> dict:
        """v2.16 scene context for the result: pole-like regions (ADE20K pole / streetlight / tower) as boxes, and the
        share of the frame per coarse class. Context only: a pole box never creates or joins a wire."""
        import cv2
        poles = np.isin(labels, self.pole_ids).astype(np.uint8)
        n, _, stats, _ = cv2.connectedComponentsWithStats(poles, connectivity=8)
        area_min = min_frac * labels.size
        boxes = [[int(x), int(y), int(x + w), int(y + h)] for x, y, w, h, a in stats[1:] if a >= area_min and h > w]
        return {"poles": boxes}


def ground_share(span_mask: np.ndarray, ground: np.ndarray) -> float:
    """Share of a span's pixels that lie on ground classes."""
    n = int(span_mask.sum())
    return float((span_mask & ground).sum() / n) if n else 0.0


def vegetation_share(mask: np.ndarray, labels: np.ndarray, vegetation_ids: list[int]) -> float:
    """v2.16: share of a region's pixels that the scene parser labels as vegetation."""
    n = int(mask.sum())
    return float(np.isin(labels[mask], vegetation_ids).sum() / n) if n else 0.0


def side_contrast(mask: np.ndarray, labels: np.ndarray, offset_frac: float = 0.012, min_offset: float = 8.0) -> float:
    """v2.16: share of a span's skeleton where the scene class at +d and -d along the span's normal differ.

    d = max(``min_offset``, ``offset_frac`` x the longer image side) clears the parser's 384 px resolution (a 1024 px
    photo: 12 px; 2048 px: 25 px). The normal is the perpendicular of the span's principal axis (spans are long and
    smooth; steep ones are service drops and not scored). Validation (docs/thesis/results/v216_context_val.json):
    at >= 0.6, 2 of 658 label-supported strands and 3 of 140 unsupported ones exceed it."""
    from app.pipeline.wire_instances import _crop_skeleton
    sk = _crop_skeleton(mask).astype(np.float64)
    if len(sk) < 5:
        return 0.0
    c = sk.mean(0)
    _, _, vt = np.linalg.svd(sk - c, full_matrices=False)
    nrm = np.array([-vt[0][1], vt[0][0]])
    d = max(min_offset, offset_frac * max(labels.shape))
    h, w = labels.shape
    a = np.rint(sk + d * nrm).astype(int)
    b = np.rint(sk - d * nrm).astype(int)
    ok = ((a[:, 0] >= 0) & (a[:, 0] < h) & (a[:, 1] >= 0) & (a[:, 1] < w)
          & (b[:, 0] >= 0) & (b[:, 0] < h) & (b[:, 1] >= 0) & (b[:, 1] < w))
    if ok.sum() < 5:
        return 0.0
    return float((labels[a[ok, 0], a[ok, 1]] != labels[b[ok, 0], b[ok, 1]]).mean())
