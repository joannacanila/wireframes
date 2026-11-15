"""WireNet: full-resolution thin-wire semantic segmentation (v2.7).

Why: at 1024 px the median power / telecom wire is 2.1 px wide. YOLO-seg predicts masks on a
1/4-resolution prototype grid; even a perfect 1/4-resolution mask reaches a median IoU of 0.40
against the full-resolution label, and only 17-21% of wire instances can ever pass IoU 0.5
(ml/wirenet audit, DISCLOSURE v2.7). SAM 2.1 prompted with the *true* boxes and skeleton
points reaches a median IoU of 0.04-0.08 on wires. WireNet predicts every pixel at full input
resolution instead.

    python -m ml.wirenet train --data datasets/wireframe_seg/data.yaml --epochs 40
    python -m ml.wirenet eval  --data datasets/wireframe_seg/data.yaml --split val

Model   U-Net, ResNet-34 encoder (ImageNet weights), 5 classes: background, Vegetation,
        abandoned-wire, power-distribution-lines, telecom-cable (CLASSES order + 1).
Data    the curated 1024 x 1024 letterboxed set; random 512 x 512 crops, 80% centred on a
        labelled wire pixel (wire-dense tiles), horizontal flip, scale 0.75-1.25, and the same
        field degradations as ml.train (motion / defocus / Gaussian blur, CLAHE, flat contrast).
Loss    cross-entropy (class-weighted) + soft Dice over the four classes + clDice on the union of
        the three wire classes (Shit et al., CVPR 2021: topology-preserving centreline Dice).
Output  per-pixel class at full resolution. Instances: ``wirenet_instances`` intersects the
        class map with each YOLO detection's dilated coarse mask (one span per detection).
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import cv2
import numpy as np

from app.core.config import CLASSES, ROOT_DIR

N_CLASSES = len(CLASSES) + 1  # + background
WIRE_IDS = [CLASSES.index(c) + 1 for c in CLASSES if c != "Vegetation"]
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)
DEFAULT_WEIGHTS = ROOT_DIR / "models" / "wirenet-r34.pt"


# ----------------------------------------------------------------------------- data

def semantic_target(label_text: str, h: int, w: int) -> np.ndarray:
    """Class map (0 = background). Vegetation is drawn first so wires crossing it keep their class."""
    t = np.zeros((h, w), np.uint8)
    rows = []
    for ln in label_text.splitlines():
        p = ln.split()
        if len(p) >= 7:
            rows.append((int(p[0]), np.array(list(map(float, p[1:]))).reshape(-1, 2)))
    for k, pts in sorted(rows, key=lambda r: r[0] != CLASSES.index("Vegetation")):
        cv2.fillPoly(t, [np.round(pts * (w, h) * 16).astype(np.int32)], k + 1, cv2.LINE_8, shift=4)
    return t


def load_split(data_yaml: Path, split: str) -> list[tuple[np.ndarray, np.ndarray, str]]:
    import yaml

    spec = yaml.safe_load(data_yaml.read_text())
    root = Path(spec.get("path", data_yaml.parent))
    out = []
    for p in sorted((root / spec[split]).glob("*.jpg")):
        rgb = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
        lbl = Path(str(p).replace("/images/", "/labels/")).with_suffix(".txt")
        out.append((rgb, semantic_target(lbl.read_text() if lbl.exists() else "", *rgb.shape[:2]), p.name))
    return out


class TileDataset:
    def __init__(self, items, tile=512, per_image=8, wire_bias=0.8, augment=True, seed=0):
        import albumentations as A

        from ml.train import field_degradations
        self.items, self.tile, self.per_image, self.wire_bias = items, tile, per_image, wire_bias
        self.rng = np.random.default_rng(seed)
        self.pix = [np.argwhere(np.isin(t, WIRE_IDS)) for _, t, _ in items]
        self.aug = A.Compose(field_degradations()) if augment else None
        self.augment = augment

    def __len__(self):
        return len(self.items) * self.per_image

    def __getitem__(self, i):
        rgb, tgt, _ = self.items[i // self.per_image]
        if self.augment:
            s = float(self.rng.uniform(0.75, 1.25))
            if abs(s - 1) > 0.02:
                rgb = cv2.resize(rgb, None, fx=s, fy=s, interpolation=cv2.INTER_LINEAR)
                tgt = cv2.resize(tgt, (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_NEAREST)
        h, w = tgt.shape
        T = self.tile
        pix = np.argwhere(np.isin(tgt, WIRE_IDS)) if self.augment else self.pix[i // self.per_image]
        if len(pix) and self.rng.random() < self.wire_bias:
            cy, cx = pix[self.rng.integers(len(pix))]
        else:
            cy, cx = self.rng.integers(h), self.rng.integers(w)
        y0 = int(np.clip(cy - T // 2, 0, max(0, h - T)))
        x0 = int(np.clip(cx - T // 2, 0, max(0, w - T)))
        img, t = rgb[y0:y0 + T, x0:x0 + T], tgt[y0:y0 + T, x0:x0 + T]
        if img.shape[:2] != (T, T):
            img = cv2.copyMakeBorder(img, 0, T - img.shape[0], 0, T - img.shape[1], cv2.BORDER_CONSTANT, value=(114,) * 3)
            t = cv2.copyMakeBorder(t, 0, T - t.shape[0], 0, T - t.shape[1], cv2.BORDER_CONSTANT, value=0)
        if self.augment:
            if self.rng.random() < 0.5:
                img, t = img[:, ::-1], t[:, ::-1]
            img = self.aug(image=np.ascontiguousarray(img))["image"]
        x = ((img.astype(np.float32) / 255.0 - MEAN) / STD).transpose(2, 0, 1)
        return np.ascontiguousarray(x), np.ascontiguousarray(t).astype(np.int64)


# ----------------------------------------------------------------------------- model / loss

def build_model(pretrained: bool = True, encoder: str = "resnet34", arch: str = "Unet"):
    import segmentation_models_pytorch as smp
    weights = (True if encoder.startswith("tu-") else "imagenet") if pretrained else None
    return getattr(smp, arch)(encoder, encoder_weights=weights, classes=N_CLASSES)


def soft_skel(x, iters: int = 10):
    """Differentiable morphological skeleton (clDice, Shit et al. 2021)."""
    import torch.nn.functional as F

    def erode(z):
        return -F.max_pool2d(-z, 3, 1, 1)

    def open_(z):
        return F.max_pool2d(erode(z), 3, 1, 1)

    skel = F.relu(x - open_(x))
    for _ in range(iters):
        x = erode(x)
        delta = F.relu(x - open_(x))
        skel = skel + F.relu(delta - skel * delta)
    return skel


def wire_loss(logits, target, class_weights):
    import torch.nn.functional as F

    ce = F.cross_entropy(logits, target, weight=class_weights)
    prob = logits.softmax(1)
    onehot = F.one_hot(target, N_CLASSES).permute(0, 3, 1, 2).float()
    inter = (prob[:, 1:] * onehot[:, 1:]).sum((0, 2, 3))
    denom = prob[:, 1:].sum((0, 2, 3)) + onehot[:, 1:].sum((0, 2, 3))
    present = onehot[:, 1:].sum((0, 2, 3)) > 0
    dice = 1 - ((2 * inter + 1) / (denom + 1))[present].mean() if present.any() else prob.sum() * 0
    pw = prob[:, WIRE_IDS].sum(1, keepdim=True)
    tw = onehot[:, WIRE_IDS].sum(1, keepdim=True)
    sp, st = soft_skel(pw), soft_skel(tw)
    tprec = ((sp * tw).sum() + 1) / (sp.sum() + 1)
    tsens = ((st * pw).sum() + 1) / (st.sum() + 1)
    cldice = 1 - 2 * tprec * tsens / (tprec + tsens)
    return ce + dice + 0.5 * cldice, {"ce": float(ce.detach()), "dice": float(dice.detach()), "cldice": float(cldice.detach())}


# ----------------------------------------------------------------------------- inference

def predict(model, rgb: np.ndarray, device: str, tile: int = 1024) -> np.ndarray:
    """Per-pixel class probabilities (C x H x W) at the input resolution; sizes padded to /32."""
    import torch

    h, w = rgb.shape[:2]
    H, W = math.ceil(h / 32) * 32, math.ceil(w / 32) * 32
    x = np.full((H, W, 3), 114, np.uint8)
    x[:h, :w] = rgb
    x = ((x.astype(np.float32) / 255.0 - MEAN) / STD).transpose(2, 0, 1)[None]
    with torch.no_grad():
        p = model(torch.from_numpy(np.ascontiguousarray(x)).to(device)).softmax(1)[0, :, :h, :w]
    return p.float().cpu().numpy()


def load_wirenet(path: Path = DEFAULT_WEIGHTS, device: str = "cpu"):
    import torch
    model = build_model(pretrained=False)
    model.load_state_dict(torch.load(path, map_location="cpu", weights_only=True))
    return model.to(device).eval()


def wirenet_instances(prob: np.ndarray, dets, dilate_px: int = 6, min_px: int = 20):
    """One full-resolution instance per detection: WireNet pixels of the detection's class inside
    its dilated coarse mask. Vegetation keeps its own mask (it is bulky; 1/4 resolution suffices)."""
    from app.pipeline.segment import WireDetection, _bbox

    cls_map = prob.argmax(0)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * dilate_px + 1, 2 * dilate_px + 1))
    out = []
    for d in dets:
        if d.wire_class == "Vegetation":
            out.append(d)
            continue
        zone = cv2.dilate(d.mask.astype(np.uint8), k).astype(bool)
        m = zone & (cls_map == CLASSES.index(d.wire_class) + 1)
        if m.sum() >= min_px:
            out.append(WireDetection(d.wire_class, d.score, m, _bbox(m)))
    return out


# ----------------------------------------------------------------------------- train / eval

def semantic_scores(pred: np.ndarray, tgt: np.ndarray) -> dict:
    """Per-class IoU and 2 px tolerant IoU (both dilated) over a set of images (accumulated)."""
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    out = {}
    for c in range(1, N_CLASSES):
        p, t = pred == c, tgt == c
        pd = cv2.dilate(p.astype(np.uint8), k).astype(bool)
        td = cv2.dilate(t.astype(np.uint8), k).astype(bool)
        out[CLASSES[c - 1]] = [(p & t).sum(), (p | t).sum(), (pd & td).sum(), (pd | td).sum()]
    return out


def train(a) -> None:
    import torch

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    tr, va = load_split(a.data, "train"), load_split(a.data, "val")
    ds = TileDataset(tr, a.tile, a.per_image)
    freq = np.bincount(np.concatenate([t.ravel() for _, t, _ in tr]), minlength=N_CLASSES).astype(float)
    cw = torch.tensor(np.clip((freq.sum() / (N_CLASSES * np.maximum(freq, 1))) ** 0.5, 1, 50), dtype=torch.float32)
    print("class pixel share:", dict(zip(["bg", *CLASSES], np.round(freq / freq.sum(), 5), strict=True)), "ce weights:", cw.tolist())
    model = build_model(encoder=a.encoder, arch=a.arch).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    steps = a.epochs * math.ceil(len(ds) / a.batch)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=steps, pct_start=0.1)
    cw = cw.to(device)
    best, best_tol, log = -1.0, -1.0, []
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # v2.12: the architecture travels with the weights, so the app can rebuild the right network
    out.with_suffix(".arch.json").write_text(json.dumps({"encoder": a.encoder, "arch": a.arch, "classes": N_CLASSES}))
    for ep in range(1, a.epochs + 1):
        model.train()
        t0, order, tot = time.time(), np.random.default_rng(ep).permutation(len(ds)), []
        for b in range(0, len(order), a.batch):
            xs, ys = zip(*(ds[int(i)] for i in order[b:b + a.batch]), strict=True)
            x = torch.from_numpy(np.stack(xs)).to(device)
            y = torch.from_numpy(np.stack(ys)).to(device)
            loss, parts = wire_loss(model(x), y, cw)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            sched.step()
            tot.append(float(loss.detach()))
            if device == "mps" and (b // a.batch) % 10 == 0:
                torch.mps.empty_cache()
        model.eval()
        acc = {c: np.zeros(4) for c in CLASSES}
        for rgb, tgt, _ in va:
            pred = predict(model, rgb, device).argmax(0)
            for c, v in semantic_scores(pred, tgt).items():
                acc[c] += v
        iou = {c: v[0] / max(1, v[1]) for c, v in acc.items()}
        tiou = {c: v[2] / max(1, v[3]) for c, v in acc.items()}
        wire = float(np.mean([iou[c] for c in CLASSES if c != "Vegetation"]))
        rec = {"epoch": ep, "loss": float(np.mean(tot)), "iou": iou, "tolerant_iou": tiou, "wire_miou": wire,
               "seconds": round(time.time() - t0, 1)}
        log.append(rec)
        tag = ""
        wire_tol = float(np.mean([tiou[c] for c in ("power-distribution-lines", "telecom-cable")]))
        rec["wire_tolerant"] = wire_tol
        torch.save(model.state_dict(), out.with_name(out.stem + "-last.pt"))  # every epoch
        if wire > best:
            best = wire
            torch.save(model.state_dict(), out)  # best strict wire mIoU (the selection used for results)
            tag = " (new best strict -> " + str(out) + ")"
        if wire_tol > best_tol:
            best_tol = wire_tol
            torch.save(model.state_dict(), out.with_name(out.stem + "-best-tolerant.pt"))
            tag += " (new best tolerant)"
        print(f"wirenet epoch {ep}/{a.epochs}: loss {rec['loss']:.3f}, wire mIoU {wire:.3f} "
              f"(power {iou['power-distribution-lines']:.3f}, telecom {iou['telecom-cable']:.3f}, "
              f"abandoned {iou['abandoned-wire']:.3f}, veg {iou['Vegetation']:.3f}; tolerant power "
              f"{tiou['power-distribution-lines']:.3f} telecom {tiou['telecom-cable']:.3f}) {rec['seconds']}s{tag}",
              flush=True)
        if device == "mps":
            torch.mps.empty_cache()
        out.with_suffix(".log.json").write_text(json.dumps(log, indent=2, default=float))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--data", type=Path, default=Path("datasets/wireframe_seg/data.yaml"))
    t.add_argument("--epochs", type=int, default=40)
    t.add_argument("--batch", type=int, default=4)
    t.add_argument("--tile", type=int, default=512)
    t.add_argument("--per-image", type=int, default=6)
    t.add_argument("--lr", type=float, default=3e-4)
    t.add_argument("--out", default=str(DEFAULT_WEIGHTS))
    t.add_argument("--encoder", default="resnet34", help="v2.12: smp encoder, e.g. mit_b1, tu-convnext_tiny")
    t.add_argument("--arch", default="Unet", help="v2.12: smp architecture")
    a = ap.parse_args()
    if a.cmd == "train":
        train(a)


if __name__ == "__main__":
    main()
