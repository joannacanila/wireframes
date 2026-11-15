"""WireNet inference and the hybrid segmenter (v2.7).

YOLO11-seg decides *which* spans exist, their class and confidence; its masks come from a
1/4-resolution prototype grid, which cannot represent a 2 px wire (a perfect 1/4-resolution
mask reaches a median IoU of 0.40 against the full-resolution outline). WireNet, a U-Net that
labels every pixel at the input resolution, supplies the pixels: each YOLO wire detection
becomes the WireNet pixels of its class inside the detection's dilated coarse mask.
Vegetation keeps YOLO's mask (bulky; the coarse grid is adequate).

Training lives in ml/wirenet.py; this module only loads weights and predicts.
"""

from __future__ import annotations

import logging
import math
import threading
from pathlib import Path

import cv2
import numpy as np

from app.core.config import CLASSES, VEGETATION
from app.pipeline.preprocess import Letterbox
from app.pipeline.segment import SegmenterUnavailable, WireDetection, YoloSegmenter, _bbox
from app.pipeline.wire_instances import extract_spans

log = logging.getLogger("wireframe")
N_CLASSES = len(CLASSES) + 1
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


def build_model(pretrained: bool = False, encoder: str = "resnet34", arch: str = "Unet"):
    import segmentation_models_pytorch as smp
    weights = (True if encoder.startswith("tu-") else "imagenet") if pretrained else None
    return getattr(smp, arch)(encoder, encoder_weights=weights, classes=N_CLASSES)


def model_arch(path: Path) -> dict:
    """{"encoder", "arch"} from the ``.arch.json`` written next to the weights (v2.12); WireNet-R34 when absent."""
    side = Path(path).with_suffix(".arch.json")
    if side.exists():
        import json
        d = json.loads(side.read_text())
        return {"encoder": d.get("encoder", "resnet34"), "arch": d.get("arch", "Unet")}
    return {"encoder": "resnet34", "arch": "Unet"}


def _load_single(path: Path, device: str):
    import torch
    model = build_model(**model_arch(path))
    model.load_state_dict(torch.load(path, map_location="cpu", weights_only=True))
    return model.to(device).eval()


def ensemble_members(spec: Path) -> list[tuple[Path, float]]:
    """(weights path, fusion weight) pairs from a v2.12 ensemble spec; relative paths resolve from the repo root."""
    import json
    d = json.loads(Path(spec).read_text())
    root = Path(spec).resolve().parents[1]
    out = []
    for m in d["members"]:
        p = Path(m["weights"])
        out.append((p if p.is_absolute() else root / p, float(m["weight"])))
    tot = sum(w for _, w in out)
    if not out or tot <= 0 or any(w < 0 for _, w in out):
        raise ValueError(f"invalid ensemble spec {spec}: weights must be >= 0 and sum > 0")
    return [(p, w / tot) for p, w in out if w > 0]


def _fused_module(members):
    import torch

    class FusedWireNet(torch.nn.Module):
        """Weighted mean of the members' class probabilities (v2.12). forward() returns log p, so the
        softmax every caller applies gives back exactly the fused probabilities (p sums to 1)."""

        def __init__(self, nets, weights):
            super().__init__()
            self.nets = torch.nn.ModuleList(nets)
            self.weights = list(weights)

        def forward(self, x):
            p = sum(w * n(x).softmax(1) for n, w in zip(self.nets, self.weights, strict=True))
            return torch.log(p.clamp_min(1e-12))

    return FusedWireNet([m for m, _ in members], [w for _, w in members])


def load_wirenet(path: Path, device: str = "cpu"):
    """One WireNet checkpoint, or (v2.12) a probability-fusion ensemble when ``path`` is a ``.json`` spec:
    {"members": [{"weights": "models/wirenet-r34.pt", "weight": 0.25}, ...]}."""
    path = Path(path)
    if path.suffix == ".json":
        members = [(_load_single(p, device), w) for p, w in ensemble_members(path)]
        return _fused_module(members).to(device).eval()
    return _load_single(path, device)


def predict_probs(model, rgb_u8: np.ndarray, device: str) -> np.ndarray:
    """C x H x W class probabilities at the input resolution (padded to a multiple of 32)."""
    import torch

    h, w = rgb_u8.shape[:2]
    H, W = math.ceil(h / 32) * 32, math.ceil(w / 32) * 32
    x = np.full((H, W, 3), 114, np.uint8)
    x[:h, :w] = rgb_u8
    x = ((x.astype(np.float32) / 255.0 - MEAN) / STD).transpose(2, 0, 1)[None]
    with torch.no_grad():
        p = model(torch.from_numpy(np.ascontiguousarray(x)).to(device)).softmax(1)[0, :, :h, :w]
    return p.float().cpu().numpy()


def predict_probs_tta(model, rgb_u8: np.ndarray, device: str, mode: str = "none") -> np.ndarray:
    """Probability fusion over a mirrored pass ("flip") and, for "flip_ms", 0.85x and 1.15x
    scales (each also mirrored). Probabilities are averaged at the input resolution."""
    p = predict_probs(model, rgb_u8, device)
    if mode == "none":
        return p
    acc, n = p + predict_probs(model, np.ascontiguousarray(rgb_u8[:, ::-1]), device)[:, :, ::-1], 2
    if mode == "flip_ms":
        h, w = rgb_u8.shape[:2]
        for s in (0.85, 1.15):
            im = cv2.resize(rgb_u8, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
            for flip in (False, True):
                x = np.ascontiguousarray(im[:, ::-1]) if flip else im
                q = predict_probs(model, x, device)
                q = q[:, :, ::-1] if flip else q
                acc += np.stack([cv2.resize(c, (w, h), interpolation=cv2.INTER_LINEAR) for c in q])
                n += 1
    return acc / n


def uncertainty(probs: np.ndarray, probs_flip: np.ndarray | None = None) -> dict:
    """Per-image model uncertainty over pixels predicted as wire: mean normalised entropy U in
    [0, 1] and, with a mirrored pass, the share D of those pixels whose class flips between the
    two passes (test-time disagreement)."""
    cls = probs.argmax(0)
    wire = cls > 0
    wire &= cls != CLASSES.index(VEGETATION) + 1
    n = int(wire.sum())
    if n == 0:
        return {"wire_pixels": 0, "entropy": None, "disagreement": None}
    p = np.clip(probs[:, wire], 1e-6, 1.0)
    ent = float((-(p * np.log(p)).sum(0) / np.log(probs.shape[0])).mean())
    dis = None if probs_flip is None else float((probs_flip.argmax(0)[wire] != cls[wire]).mean())
    return {"wire_pixels": n, "entropy": round(ent, 4), "disagreement": None if dis is None else round(dis, 4)}


def class_masks_to_original(cls_map: np.ndarray, lb: Letterbox) -> dict[int, np.ndarray]:
    """Per-class binary masks from letterbox space to the original image. Area-weighted
    resampling with a low threshold keeps 1-2 px wires when the original is smaller than the
    model input (nearest-neighbour downsampling would drop them)."""
    crop = cls_map[lb.pad_y: lb.pad_y + lb.new_h, lb.pad_x: lb.pad_x + lb.new_w]
    out = {}
    for c in range(1, N_CLASSES):
        m = (crop == c).astype(np.float32)
        if not m.any():
            continue
        down = lb.orig_w < lb.new_w
        r = cv2.resize(m, (lb.orig_w, lb.orig_h), interpolation=cv2.INTER_AREA if down else cv2.INTER_LINEAR)
        out[c] = r >= (0.25 if down else 0.5)
    return out


def prob_to_original(prob: np.ndarray, lb: Letterbox) -> np.ndarray:
    crop = prob[lb.pad_y: lb.pad_y + lb.new_h, lb.pad_x: lb.pad_x + lb.new_w].astype(np.float32)
    return cv2.resize(crop, (lb.orig_w, lb.orig_h), interpolation=cv2.INTER_LINEAR)


def skeleton_instances(probs: np.ndarray, class_masks: dict[int, np.ndarray], lb: Letterbox, conf_min: float,
                       span_params: dict | None = None) -> list[WireDetection]:
    out = []
    for name in CLASSES:
        if name == VEGETATION:
            continue
        c = CLASSES.index(name) + 1
        m = class_masks.get(c)
        if m is None:
            continue
        for span, conf in extract_spans(m, prob_to_original(probs[c], lb), **(span_params or {})):
            if conf >= conf_min:
                out.append(WireDetection(name, conf, span, _bbox(span)))
    return out


def assemble_instances(class_masks: dict[int, np.ndarray], dets: list[WireDetection], dilate_px: int,
                       min_px: int = 20) -> list[WireDetection]:
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * dilate_px + 1, 2 * dilate_px + 1))
    out = []
    for d in dets:
        if d.wire_class == VEGETATION:
            out.append(d)
            continue
        cm = class_masks.get(CLASSES.index(d.wire_class) + 1)
        if cm is None:
            continue
        m = cv2.dilate(d.mask.astype(np.uint8), k).astype(bool) & cm
        if m.sum() >= min_px:
            out.append(WireDetection(d.wire_class, d.score, m, _bbox(m)))
    return out


class HybridSegmenter:
    """YOLO11-seg detections refined to full resolution by WireNet."""

    source = "model"

    def __init__(self, yolo: YoloSegmenter, wirenet_path: Path, dilate_frac: float = 0.006,
                 device: str = "auto", instances: str = "skeleton", tta: str = "none") -> None:
        self.instances, self.tta = instances, tta
        self.yolo, self.wirenet_path, self.dilate_frac, self.device = yolo, wirenet_path, dilate_frac, device
        self._net, self._device = None, "cpu"
        self.last_uncertainty: dict | None = None
        self.last_class_masks: dict[int, np.ndarray] | None = None  # v2.11: full WireNet mask for the UI
        self.last_polarity: str | None = None  # v2.11: "normal" / "inverted" when dual polarity was checked
        self.last_suppressed: list[dict] = []  # v2.13: spans removed by the ground gate, with the reason
        self._scene = None
        self._lock = threading.Lock()
        self.wirenet_error: str | None = None

    def _infer(self, x: np.ndarray) -> tuple[np.ndarray, dict]:
        """WireNet probabilities (with the configured TTA) and their uncertainty summary."""
        if self.tta == "flip":
            p1 = predict_probs(self._net, x, self._device)
            p2 = predict_probs(self._net, np.ascontiguousarray(x[:, ::-1]), self._device)[:, :, ::-1]
            return (p1 + p2) / 2, uncertainty(p1, p2)
        probs = predict_probs_tta(self._net, x, self._device, self.tta)
        return probs, uncertainty(probs)

    # the admin portal and the API read these from the active segmenter
    @property
    def path(self) -> Path:
        return self.yolo.path

    @property
    def names(self):
        return self.yolo.names

    @property
    def error(self) -> str | None:
        return self.yolo.error or self.wirenet_error

    @property
    def ready(self) -> bool:
        return self.yolo.ready and self._net is not None

    def load(self) -> bool:
        if not self.yolo.load():
            return False
        if self._net is not None:
            return True
        if not self.wirenet_path.exists():
            self.wirenet_error = f"No WireNet weights at {self.wirenet_path.name}"
            return False
        try:
            import torch
            self._device = self.device if self.device != "auto" else (
                "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu")
            self._net = load_wirenet(self.wirenet_path, self._device)
            self.wirenet_error = None
            return True
        except Exception as exc:  # corrupt file, missing library
            self.wirenet_error = f"could not load WireNet: {exc}"
            log.exception("WireNet load failed")
            return False

    def segment(self, x_rgb: np.ndarray, lb: Letterbox) -> list[WireDetection]:
        if not self.load():
            raise SegmenterUnavailable(self.error or "model unavailable")
        dets = self.yolo.segment(x_rgb, lb)
        self.last_class_masks = None
        if self.instances == "yolo" and not any(d.wire_class != VEGETATION for d in dets):
            return dets
        with self._lock:
            x = np.clip(x_rgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
            probs, self.last_uncertainty = self._infer(x)
            self.last_polarity = None
            cfg = self.yolo.cfg
            if getattr(cfg, "dual_polarity", False):
                content = x[lb.pad_y: lb.pad_y + lb.new_h, lb.pad_x: lb.pad_x + lb.new_w]
                if mean_l_star(content) <= cfg.dual_polarity_max_mean_l:
                    inv_probs, inv_unc = self._infer(invert_luminance(x, lb))
                    use = wire_pixel_count(inv_probs) >= cfg.dual_polarity_ratio * max(wire_pixel_count(probs), 1)
                    self.last_polarity = "inverted" if use else "normal"
                    if use:
                        probs, self.last_uncertainty = inv_probs, inv_unc
        masks = class_masks_to_original(probs.argmax(0), lb)
        self.last_class_masks = masks
        if self.instances == "skeleton":
            veg = [d for d in dets if d.wire_class == VEGETATION]
            spans = skeleton_instances(probs, masks, lb, self.yolo.cfg.conf_min, span_params(self.yolo.cfg))
            return veg + self._ground_gate(spans, x, lb)
        dilate = max(2, int(round(self.dilate_frac * max(lb.orig_w, lb.orig_h))))
        return assemble_instances(masks, dets, dilate)


    def _ground_gate(self, spans: list[WireDetection], x: np.ndarray, lb: Letterbox) -> list[WireDetection]:
        """v2.13: drop spans lying mostly on ground classes (kerbs, manholes, pavement joints); record them."""
        self.last_suppressed = []
        cfg = self.yolo.cfg
        if not getattr(cfg, "ground_gate", False) or not spans:
            return spans
        # the road and pavement lie below the wires in a pole photo: when no span reaches the lower half of the
        # frame there is nothing to gate, so the scene parser is not run (saves 0.26 s; output identical)
        mid = lb.orig_h / 2
        if not any(d.bbox[3] > mid for d in spans):
            return spans
        from app.pipeline.scene import SceneParser, ground_share
        from app.pipeline.segment import mask_to_polygons
        if self._scene is None:
            self._scene = SceneParser(self._device)
        content = np.ascontiguousarray(x[lb.pad_y: lb.pad_y + lb.new_h, lb.pad_x: lb.pad_x + lb.new_w])
        lab = self._scene.labels(content)
        if lab is None:  # parser unavailable: keep every span (fail open, flagged by the runner)
            self.last_suppressed = [{"unavailable": True}]
            return spans
        ground = cv2.resize(self._scene.ground_mask(lab).astype(np.uint8), (lb.orig_w, lb.orig_h),
                            interpolation=cv2.INTER_NEAREST).astype(bool)
        keep = []
        for d in spans:
            share = ground_share(d.mask, ground)
            if share >= cfg.ground_gate_share:
                self.last_suppressed.append({
                    "wire_class": d.wire_class, "confidence": round(d.score, 4),
                    "bbox": [round(v, 1) for v in d.bbox], "polygons": mask_to_polygons(d.mask),
                    "ground_share": round(share, 3), "reason_code": "ground_structure",
                    "reason": "Lies on the road or pavement: a kerb, manhole or pavement edge, not an overhead wire"})
            else:
                keep.append(d)
        return keep


def mean_l_star(rgb: np.ndarray) -> float:
    """Mean CIELAB L* (0-100) of an RGB uint8 image."""
    return float(cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)[..., 0].mean() * (100.0 / 255.0))


def invert_luminance(x: np.ndarray, lb: Letterbox | None = None) -> np.ndarray:
    """L* inverted (chroma kept) inside the letterbox content; the padding keeps its fill value, as in
    training, so the model sees an ordinary letterbox around a polarity-flipped photo (v2.11)."""
    out = x.copy()
    sl = (slice(None), slice(None)) if lb is None else (slice(lb.pad_y, lb.pad_y + lb.new_h),
                                                         slice(lb.pad_x, lb.pad_x + lb.new_w))
    lab = cv2.cvtColor(np.ascontiguousarray(x[sl]), cv2.COLOR_RGB2LAB)
    lab[..., 0] = 255 - lab[..., 0]
    out[sl] = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
    return out


def wire_pixel_count(probs: np.ndarray) -> int:
    wire = [CLASSES.index(c) + 1 for c in CLASSES if c != VEGETATION]
    return int(np.isin(probs.argmax(0), wire).sum())


def span_params(cfg) -> dict:
    return {"min_px": cfg.span_min_px, "min_len": cfg.span_min_len, "max_turn_deg": cfg.span_max_turn_deg,
            "cluster_radius": cfg.span_cluster_radius, "max_gap": cfg.span_max_gap,
            "gap_angle_deg": cfg.span_gap_angle_deg, "draw_bridge": cfg.span_draw_bridge,
            "min_span_len": cfg.span_min_stitched_len, "own_component": cfg.span_own_component}


def make_segmenter(cfg, root: Path):
    """YOLO alone, or YOLO + WireNet when ``segmentation.wirenet_weights`` is set."""
    yolo = YoloSegmenter(cfg, root)
    if not getattr(cfg, "wirenet_weights", None):
        return yolo
    p = Path(cfg.wirenet_weights)
    return HybridSegmenter(yolo, p if p.is_absolute() else root / p, cfg.wirenet_dilate_frac, cfg.device,
                           cfg.wirenet_instances, cfg.wirenet_tta)
