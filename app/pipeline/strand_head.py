"""v2.17 strand type head: one wire type per physical strand, from the whole strand and its scene.

Runtime half of ml/strand_classifier.py (training, data hygiene and the validation evidence are documented
there). Given the strands of one photo, the photo and the fused WireNet probabilities, the head returns
P(telecom | power-or-telecom) per strand from model evidence, geometry, scene position, appearance and frozen
DINOv2-small patch features pooled along the strand. Strands whose top pixel evidence is abandoned-wire keep
the v2.16 rule; the head never removes a strand.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import cv2
import numpy as np

from app.core.config import CLASSES
from app.pipeline import centerline_eval as ce

WIRE_IDS = [CLASSES.index(c) + 1 for c in CLASSES if c != "Vegetation"]
PT, TC = "power-distribution-lines", "telecom-cable"
DINO_ID = "facebook/dinov2-small"
DINO_PX = 1008  # 72 x 72 patches of 14 px
N_DINO = 32  # PCA dimensions kept
REF_SIDE = 1024  # training photos: lengths and widths are rescaled to this long side


class Dino:
    def __init__(self, device: str | None = None):
        import torch
        from transformers import AutoModel
        self.dev = device or ("mps" if torch.backends.mps.is_available() else "cpu")
        from app.core.weights import hf_source
        self.model = AutoModel.from_pretrained(hf_source("dinov2-small", DINO_ID)).to(self.dev).eval()

    def grid(self, rgb: np.ndarray) -> np.ndarray:
        """(72, 72, 384) patch features of the photo resized to 1008 px (aspect ignored: the photos are square)."""
        import torch
        x = cv2.resize(rgb, (DINO_PX, DINO_PX), interpolation=cv2.INTER_AREA).astype(np.float32) / 255
        x = (x - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
        t = torch.from_numpy(x.transpose(2, 0, 1)[None].astype(np.float32)).to(self.dev)
        with torch.no_grad():
            h = self.model(pixel_values=t).last_hidden_state[0, 1:]  # drop CLS
        g = DINO_PX // 14
        out = h.reshape(g, g, -1).float().cpu().numpy()
        if self.dev == "mps":
            torch.mps.empty_cache()
        return out



def _skel_points(m: np.ndarray) -> np.ndarray:
    s = ce._skel(m)
    ys, xs = np.nonzero(s)
    return np.stack([xs, ys], 1).astype(np.float32) if len(xs) else np.zeros((0, 2), np.float32)


def strand_features(strands, probs: np.ndarray, rgb: np.ndarray, grid: np.ndarray | None,
                    scale: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """(hand features (n, F), pooled DINO features (n, 384)) for the strands of one image. ``scale`` maps pixel
    lengths and widths to the 1024 px training photos (lengths in px x scale)."""
    H, W = probs.shape[1:]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.float32)
    lap = np.abs(cv2.Laplacian(gray, cv2.CV_32F, ksize=3))
    p_wire = probs[WIRE_IDS].sum(0)
    ent = -(probs * np.log(np.clip(probs, 1e-6, 1))).sum(0)
    ring_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    pts = [_skel_points(s.mask) for s in strands]
    ymeans = np.array([p[:, 1].mean() if len(p) else H for p in pts])
    hand, dino = [], []
    for s, p, ym in zip(strands, pts, ymeans, strict=True):
        n = max(len(p), 1)
        area = int(s.mask.sum())
        if len(p) >= 2:
            # principal axis: chord, sag (max perpendicular distance from the chord line)
            c = p.mean(0)
            u, sv, vt = np.linalg.svd(p - c, full_matrices=False)
            d = vt[0]
            proj = (p - c) @ d
            chord = float(proj.max() - proj.min()) + 1
            perp = (p - c) @ np.array([-d[1], d[0]])
            sag = float(perp.max() - perp.min()) / chord
            ang = abs(float(np.arctan2(d[1], d[0])))
            ang = min(ang, np.pi - ang)
        else:
            chord, sag, ang = 1.0, 0.0, 0.0
        m = s.mask
        ring = cv2.dilate(m.astype(np.uint8), ring_k).astype(bool) & ~m
        g_in, g_out = float(gray[m].mean()), float(gray[ring].mean()) if ring.any() else float(gray.mean())
        share = s.class_share or {}
        sh = [share.get(PT, 0.0), share.get(TC, 0.0), share.get("abandoned-wire", 0.0)]
        above = float((ymeans < ym).mean()) if len(ymeans) > 1 else 0.0
        hand.append([
            *sh, sh[1] - sh[0],
            float(p_wire[m].mean()), float(ent[m].mean()),
            float(probs[CLASSES.index(PT) + 1][m].mean()), float(probs[CLASSES.index(TC) + 1][m].mean()),
            np.log1p(n * scale), n / chord, sag, ang, area / n * scale,
            ym / H, (float(p[:, 1].min()) / H) if len(p) else 1.0, above, len(strands) / 20,
            (g_out - g_in) / 255, g_in / 255, g_out / 255,
            float(hsv[..., 1][m].mean()) / 255, float(hsv[..., 0][m].mean()) / 180,
            float(lap[ring].mean()) / 255 if ring.any() else 0.0,
            float(rgb[..., 2][m].mean() - rgb[..., 0][m].mean()) / 255,
        ])
        if grid is not None and len(p):
            g = grid.shape[0]
            ix = np.clip((p[:, 0] / W * g).astype(int), 0, g - 1)
            iy = np.clip((p[:, 1] / H * g).astype(int), 0, g - 1)
            cells = np.unique(iy * g + ix)
            dino.append(grid.reshape(g * g, -1)[cells].mean(0))
        else:
            dino.append(np.zeros(grid.shape[-1] if grid is not None else 384, np.float32))
    return np.asarray(hand, np.float32), np.asarray(dino, np.float32)


class Head:
    """Standardise -> [hand, PCA(dino)] -> 2-layer MLP (power vs telecom), L2 + early stopping by train CV."""

    def __init__(self, stats: dict, state: dict | None = None):
        import torch
        self.stats = stats
        d = len(stats["mu"]) + N_DINO
        self.net = torch.nn.Sequential(torch.nn.Linear(d, 32), torch.nn.ReLU(), torch.nn.Dropout(0.2), torch.nn.Linear(32, 1))
        if state:
            self.net.load_state_dict(state)
        self.net.eval()

    def x(self, hand: np.ndarray, dino: np.ndarray) -> np.ndarray:
        s = self.stats
        h = (hand - np.asarray(s["mu"])) / np.asarray(s["sd"])
        dz = (dino - np.asarray(s["dmu"])) @ np.asarray(s["pca"]).T / np.asarray(s["psd"])
        return np.concatenate([h, dz], 1).astype(np.float32)

    def p_telecom(self, hand: np.ndarray, dino: np.ndarray) -> np.ndarray:
        import torch
        with torch.no_grad():
            return torch.sigmoid(self.net(torch.from_numpy(self.x(hand, dino)))).numpy()[:, 0]



def load_head(path: Path) -> Head:
    import torch
    meta = json.loads(path.with_suffix(".json").read_text())
    return Head(meta["stats"], torch.load(path, map_location="cpu", weights_only=True))



def _ends(m: np.ndarray, k: int = 25) -> list[tuple[np.ndarray, np.ndarray]] | None:
    """The two ends of a strand: (end point, outward unit direction from a fit to the last ``k`` skeleton px)."""
    p = _skel_points(m)
    if len(p) < 4:
        return None
    c = p.mean(0)
    _, _, vt = np.linalg.svd(p - c, full_matrices=False)
    t = (p - c) @ vt[0]
    order = p[np.argsort(t)]
    out = []
    for seg, sign in ((order[:k], -1), (order[-k:], 1)):
        sc = seg.mean(0)
        _, _, v = np.linalg.svd(seg - sc, full_matrices=False)
        d = v[0] * (1 if v[0] @ vt[0] * sign >= 0 else -1)
        out.append((order[0] if sign < 0 else order[-1], d / (np.linalg.norm(d) + 1e-9)))
    return out


def continuation_groups(strands: list, max_gap: float, max_deg: float = 20.0) -> list[int]:
    """Union-find over strands whose ends meet head-on: end-to-end distance <= max_gap and both outward
    directions within max_deg of the line joining the ends (the pieces of one cable split at a junction,
    a crossing or an occluder). Returns a group id per strand."""
    parent = list(range(len(strands)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    ends = [_ends(st.mask) for st in strands]
    cos = np.cos(np.radians(max_deg))
    for i in range(len(strands)):
        for j in range(i + 1, len(strands)):
            if ends[i] is None or ends[j] is None:
                continue
            for pi, di in ends[i]:
                for pj, dj in ends[j]:
                    v = pj - pi
                    dist = float(np.linalg.norm(v))
                    if dist > max_gap:
                        continue
                    if dist < 3:  # touching ends: directions must be opposite (one continues the other)
                        ok = float(di @ -dj) >= cos
                    else:
                        u = v / dist
                        ok = float(di @ u) >= cos and float(dj @ -u) >= cos
                    if ok:
                        parent[find(i)] = find(j)
    return [find(i) for i in range(len(strands))]


def group_probabilities(p: np.ndarray, lengths: np.ndarray, groups: list[int]) -> np.ndarray:
    """Length-weighted mean log-odds per continuation group: every piece of one cable gets one type."""
    lo = np.log(np.clip(p, 1e-4, 1 - 1e-4) / np.clip(1 - p, 1e-4, 1))
    g = np.asarray(groups)
    out = p.astype(np.float64).copy()
    for k in set(groups):
        sel = g == k
        if sel.sum() > 1:
            out[sel] = 1 / (1 + np.exp(-np.average(lo[sel], weights=lengths[sel])))
    return out


class StrandHead:
    """Lazy, thread-safe wrapper used by the app: ``classify(strands, probs, rgb)`` rewrites class / status."""

    def __init__(self, path: Path, min_conf: float = 0.5, device: str | None = None, group_gap: float = 0.0) -> None:
        self.path, self.min_conf, self.device, self.group_gap = Path(path), min_conf, device, group_gap
        self._head = self._dino = None
        self._lock, self.error = threading.Lock(), None

    def load(self) -> bool:
        if self._head is not None:
            return True
        try:
            self._head = load_head(self.path)
            self._dino = Dino(self.device)
            return True
        except Exception as exc:  # missing weights, offline without a cached DINOv2
            self.error = f"strand head unavailable: {exc}"
            return False

    def classify(self, strands: list, probs: np.ndarray, rgb: np.ndarray) -> bool:
        """Mutates each strand's wire_class / class_status (and records ``head_p_telecom`` in class_share)."""
        if not strands or not self.load():
            return False
        with self._lock:
            hand, dn = strand_features(strands, probs, rgb, self._dino.grid(rgb), scale=REF_SIDE / max(rgb.shape[:2]))
            p = self._head.p_telecom(hand, dn)
        if self.group_gap > 0 and len(strands) > 1:
            keep = [i for i, st in enumerate(strands) if not (st.class_share and
                                                              max(st.class_share, key=st.class_share.get) == "abandoned-wire")]
            sub = [strands[i] for i in keep]
            groups = continuation_groups(sub, self.group_gap / (REF_SIDE / max(rgb.shape[:2])))
            lengths = np.array([max(int(ce._skel(st.mask).sum()), 1) for st in sub], float)
            p = p.astype(np.float64)
            p[keep] = group_probabilities(p[keep], lengths, groups)
        for s, pt in zip(strands, p, strict=True):
            share = s.class_share or {}
            if share and max(share, key=share.get) == "abandoned-wire":
                continue
            s.wire_class = TC if pt >= 0.5 else PT
            s.class_status = "confident" if max(pt, 1 - pt) >= self.min_conf else "uncertain"
            s.class_share = {**share, "head_p_telecom": round(float(pt), 4)}
        return True
