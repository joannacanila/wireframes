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
GROUND = ("road", "sidewalk", "floor", "earth", "path", "grass", "field", "sand", "dirt track", "runway", "rug")


class SceneParser:
    def __init__(self, device: str = "auto") -> None:
        self.device, self._net, self._proc, self._lock = device, None, None, threading.Lock()
        self.error: str | None = None
        self.ground_ids: list[int] = []

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


def ground_share(span_mask: np.ndarray, ground: np.ndarray) -> float:
    """Share of a span's pixels that lie on ground classes."""
    n = int(span_mask.sum())
    return float((span_mask & ground).sum() / n) if n else 0.0
