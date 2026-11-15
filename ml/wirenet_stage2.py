"""WireNet Stage 2: rescue abandoned-wire without giving up power / telecom (v2.7).

    python -m ml.wirenet_stage2 --init runs/wirenet/wirenet-r34.pt --epochs 25

Why abandoned-wire fails in Stage 1 (audit, DISCLOSURE v2.7): 14 training instances in 7 photos,
0.033% of pixels, and inconsistent polygons (some trace a door edge or a blurred pole, several
wrap a whole hanging bundle or the sky around it instead of the wire).

What Stage 2 does, branching from the best Stage 1 checkpoint:
1. Label tightening (train split only). Each abandoned-wire polygon keeps only the pixels that
   look like wire: the Stage 1 model's any-wire probability > 0.3 or a dark-ridge (black-hat)
   response, dilated 1 px. The rest of the polygon becomes IGNORE (no loss). A polygon that no
   wire runs through (wire pixels spanning < 50% of its length, or < 30 px) becomes IGNORE. Val and test labels are
   never changed, so the metric is not circular.
2. Oversampling: ``p_over`` of tiles are centred on an abandoned-wire pixel.
3. Copy-paste: tightened real instances pasted onto tiles (flip, scale 0.7-1.4, rotation +-25
   degrees, feathered edges).
4. Procedural dangling wires: drooping / hanging-end Bezier curves, 2-5 px, dark, anti-aliased.
5. Loss: focal cross-entropy (gamma 2, ignore 255) with Stage 1's square-root inverse-frequency
   weights for background / vegetation / power / telecom and a boosted abandoned-wire weight
   (6), soft Dice over the classes and clDice on the wire union. Synthetic wires are hanging-end
   only (an abandoned wire is told apart by its free end, not its look; slack loops between two
   anchors look like ordinary spans and taught "dark curve = abandoned"). Attempt 3 (weight 15 +
   logit adjustment on the abandoned logit, Menon et al. ICLR 2021) hit 6/6 validation instances
   but at 0.05 precision (282k predicted pixels): a flood. Logit adjustment is kept as an
   option (--tau). A first attempt that
   boosted every wire class 6-10x and logit-adjusted all classes made the model over-predict
   wire pixels: power / telecom IoU fell 0.265 / 0.232 -> 0.192 / 0.144 in two epochs.
6. Selection keeps power / telecom: a checkpoint is saved only if power and telecom IoU stay
   >= ``keep`` x their Stage 1 values, and then by abandoned-wire coverage F (instance hit
   rate x pixel precision within 6 px of a label; ``abandoned_coverage``). Strict IoU against
   the loose validation polygons fell as predictions got *thinner and more correct*.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import cv2
import numpy as np

from app.core.config import CLASSES
from ml import wirenet as W

IGNORE = 255
ABANDONED = CLASSES.index("abandoned-wire") + 1
POWER = CLASSES.index("power-distribution-lines") + 1
TELECOM = CLASSES.index("telecom-cable") + 1


# ----------------------------------------------------------------------------- label tightening

def wire_likeness(rgb: np.ndarray, probs: np.ndarray | None) -> np.ndarray:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    ridge = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    m = ridge > max(12, np.percentile(ridge, 90))
    if probs is not None:
        m |= probs[W.WIRE_IDS].sum(0) > 0.3
    return cv2.dilate(m.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)


def _extent(m: np.ndarray) -> int:
    ys, xs = np.nonzero(m)
    return int(max(np.ptp(xs), np.ptp(ys))) + 1 if len(ys) else 0


def tighten_abandoned(tgt: np.ndarray, like: np.ndarray, min_span: float = 0.5, min_px: int = 30
                      ) -> tuple[np.ndarray, dict]:
    """Keep a polygon only if wire-like pixels run through at least ``min_span`` of its length."""
    out = tgt.copy()
    n, comp = cv2.connectedComponents((tgt == ABANDONED).astype(np.uint8), connectivity=8)
    kept = dropped = 0
    for i in range(1, n):
        c = comp == i
        keep = c & like
        if keep.sum() < min_px or _extent(keep) < min_span * _extent(c):
            out[c] = IGNORE
            dropped += 1
        else:
            out[c & ~keep] = IGNORE
            kept += 1
    return out, {"kept": kept, "ignored": dropped}


# ----------------------------------------------------------------------------- augmentation

def instance_bank(items) -> list[tuple[np.ndarray, np.ndarray]]:
    bank = []
    for rgb, tgt, _ in items:
        n, comp = cv2.connectedComponents((tgt == ABANDONED).astype(np.uint8), connectivity=8)
        for i in range(1, n):
            ys, xs = np.nonzero(comp == i)
            if len(ys) < 30:
                continue
            y0, y1, x0, x1 = max(0, ys.min() - 6), ys.max() + 7, max(0, xs.min() - 6), xs.max() + 7
            bank.append((rgb[y0:y1, x0:x1].copy(), (comp[y0:y1, x0:x1] == i)))
    return bank


def content_mask(img: np.ndarray) -> np.ndarray:
    """Photo pixels, not the grey (114) letterbox padding."""
    pad = (np.abs(img.astype(np.int16) - 114) <= 6).all(axis=2)
    pad = cv2.morphologyEx(pad.astype(np.uint8), cv2.MORPH_OPEN, np.ones((9, 9), np.uint8)).astype(bool)
    return ~pad


def paste(img: np.ndarray, tgt: np.ndarray, patch: np.ndarray, mask: np.ndarray, rng,
          content: np.ndarray | None = None) -> None:
    s = float(rng.uniform(0.7, 1.4))
    ang = float(rng.uniform(-25, 25))
    if rng.random() < 0.5:
        patch, mask = patch[:, ::-1], mask[:, ::-1]
    h, w = mask.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), ang, s)
    cos, sin = abs(M[0, 0]), abs(M[0, 1])
    nw, nh = int(h * sin + w * cos) + 1, int(h * cos + w * sin) + 1
    M[0, 2] += nw / 2 - w / 2
    M[1, 2] += nh / 2 - h / 2
    p = cv2.warpAffine(np.ascontiguousarray(patch), M, (nw, nh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    m = cv2.warpAffine(np.ascontiguousarray(mask).astype(np.float32), M, (nw, nh), flags=cv2.INTER_LINEAR)
    T = img.shape[0]
    if nh >= T or nw >= T:
        return
    for _ in range(10):  # land on the photo, not on the letterbox padding
        y, x = int(rng.integers(0, T - nh)), int(rng.integers(0, T - nw))
        if content is None or content[y:y + nh, x:x + nw][m > 0.5].mean() >= 0.95:
            break
    else:
        return
    alpha = cv2.GaussianBlur(m, (0, 0), 0.8)[..., None]
    roi = img[y:y + nh, x:x + nw].astype(np.float32)
    img[y:y + nh, x:x + nw] = np.clip(alpha * p + (1 - alpha) * roi, 0, 255).astype(np.uint8)
    tgt[y:y + nh, x:x + nw][m > 0.5] = ABANDONED


def draw_dangling(img: np.ndarray, tgt: np.ndarray, rng, content: np.ndarray | None = None) -> None:
    """A drooping or hanging-end wire: cubic Bezier from an anchor, sagging under gravity."""
    for _ in range(10):
        if _draw_dangling_once(img, tgt, rng, content):
            return


def _draw_dangling_once(img, tgt, rng, content, hanging_share: float = 1.0) -> bool:
    T = img.shape[0]
    up = 4
    canvas = np.zeros((T * up, T * up), np.uint8)
    x0, y0 = rng.uniform(0.1, 0.9) * T, rng.uniform(0.05, 0.5) * T
    L = rng.uniform(80, 300)
    side = rng.choice([-1, 1])
    hanging = rng.random() < hanging_share  # free end hanging down, else a slack loop between two anchors
    if hanging:
        pts = [(x0, y0), (x0 + side * L * 0.4, y0 + L * 0.1), (x0 + side * L * 0.5, y0 + L * 0.5),
               (x0 + side * L * rng.uniform(0.3, 0.6), y0 + L)]
    else:
        x3 = x0 + side * L
        pts = [(x0, y0), (x0 + side * L * 0.3, y0 + L * 0.6), (x3 - side * L * 0.3, y0 + L * 0.6), (x3, y0 + rng.uniform(-20, 20))]
    t = np.linspace(0, 1, 200)[:, None]
    P = np.array(pts)
    curve = ((1 - t) ** 3) * P[0] + 3 * ((1 - t) ** 2) * t * P[1] + 3 * (1 - t) * t ** 2 * P[2] + t ** 3 * P[3]
    curve += rng.normal(0, 0.6, curve.shape)  # slight waviness
    if content is not None:
        xy = np.clip(np.round(curve).astype(int), 0, T - 1)
        if content[xy[:, 1], xy[:, 0]].mean() < 0.95:
            return False
    width = int(rng.integers(2, 6))
    cv2.polylines(canvas, [np.round(curve * up).astype(np.int32)], False, 255, width * up, cv2.LINE_AA)
    alpha = cv2.resize(canvas, (T, T), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    alpha = cv2.GaussianBlur(alpha, (0, 0), 0.5)[..., None]
    shade = float(rng.uniform(15, 70))
    color = np.clip(shade + rng.normal(0, 4, img.shape), 0, 255)
    img[:] = np.clip(alpha * color + (1 - alpha) * img.astype(np.float32), 0, 255).astype(np.uint8)
    tgt[alpha[..., 0] > 0.5] = ABANDONED
    return True


class Stage2Tiles(W.TileDataset):
    def __init__(self, items, bank, p_over=0.35, p_paste=0.35, p_synth=0.1, **kw):
        super().__init__(items, **kw)
        self.bank, self.p_over, self.p_paste, self.p_synth = bank, p_over, p_paste, p_synth
        self.ab_items = [i for i, (_, t, _) in enumerate(items) if (t == ABANDONED).any()]
        self.ab_pix = {i: np.argwhere(items[i][1] == ABANDONED) for i in self.ab_items}

    def __getitem__(self, i):
        if self.ab_items and self.rng.random() < self.p_over:
            j = int(self.rng.choice(self.ab_items))
            rgb, tgt, _ = self.items[j]
            cy, cx = self.ab_pix[j][self.rng.integers(len(self.ab_pix[j]))]
            T = self.tile
            y0 = int(np.clip(cy - T // 2 + self.rng.integers(-T // 4, T // 4), 0, max(0, tgt.shape[0] - T)))
            x0 = int(np.clip(cx - T // 2 + self.rng.integers(-T // 4, T // 4), 0, max(0, tgt.shape[1] - T)))
            img, t = rgb[y0:y0 + T, x0:x0 + T].copy(), tgt[y0:y0 + T, x0:x0 + T].copy()
            if self.rng.random() < 0.5:
                img, t = img[:, ::-1].copy(), t[:, ::-1].copy()
            img = self.aug(image=np.ascontiguousarray(img))["image"]
        else:
            x, t = super().__getitem__(i)
            img = np.clip((x.transpose(1, 2, 0) * W.STD + W.MEAN) * 255.0, 0, 255).astype(np.uint8)
            t = t.astype(np.uint8)
        img, t = np.ascontiguousarray(img), np.ascontiguousarray(t)
        content = content_mask(img)
        if self.bank and self.rng.random() < self.p_paste:
            for _ in range(int(self.rng.integers(1, 3))):
                patch, mask = self.bank[int(self.rng.integers(len(self.bank)))]
                paste(img, t, patch, mask, self.rng, content)
        if self.rng.random() < self.p_synth:
            draw_dangling(img, t, self.rng, content)
        x = ((img.astype(np.float32) / 255.0 - W.MEAN) / W.STD).transpose(2, 0, 1)
        return np.ascontiguousarray(x), t.astype(np.int64)


# ----------------------------------------------------------------------------- loss

def stage2_loss(logits, target, class_weights, log_prior, tau=1.0, gamma=2.0):
    import torch
    import torch.nn.functional as F

    valid = target != IGNORE
    tgt = torch.where(valid, target, torch.zeros_like(target))
    adj = logits + tau * log_prior.view(1, -1, 1, 1)  # logit adjustment, loss only
    logp = F.log_softmax(adj, 1)
    lp_t = logp.gather(1, tgt[:, None])[:, 0]
    focal = -((1 - lp_t.exp()) ** gamma) * lp_t * class_weights[tgt]
    ce = (focal * valid).sum() / valid.sum().clamp(min=1)
    prob = logits.softmax(1) * valid[:, None]
    onehot = F.one_hot(tgt, W.N_CLASSES).permute(0, 3, 1, 2).float() * valid[:, None]
    inter = (prob[:, 1:] * onehot[:, 1:]).sum((0, 2, 3))
    denom = prob[:, 1:].sum((0, 2, 3)) + onehot[:, 1:].sum((0, 2, 3))
    present = onehot[:, 1:].sum((0, 2, 3)) > 0
    dice = 1 - ((2 * inter + 1) / (denom + 1))[present].mean() if present.any() else prob.sum() * 0
    pw = prob[:, W.WIRE_IDS].sum(1, keepdim=True)
    tw = onehot[:, W.WIRE_IDS].sum(1, keepdim=True)
    sp, st = W.soft_skel(pw), W.soft_skel(tw)
    tprec = ((sp * tw).sum() + 1) / (sp.sum() + 1)
    tsens = ((st * pw).sum() + 1) / (st.sum() + 1)
    cldice = 1 - 2 * tprec * tsens / (tprec + tsens)
    return ce + dice + 0.5 * cldice


# ----------------------------------------------------------------------------- abandoned-wire metric

def abandoned_coverage(pred_ab: np.ndarray, gt_ab: np.ndarray, margin: int = 6) -> tuple[int, int, int, int]:
    """(instances hit, instances, predicted px within ``margin`` of a label, predicted px).
    An instance is hit when predicted abandoned-wire pixels run along >= 50% of its length.
    Robust to loosely drawn polygons, unlike IoU (validation abandoned polygons are a median
    10 px wide around a 2-3 px wire)."""
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * margin + 1, 2 * margin + 1))
    hits = n = 0
    nc, comp = cv2.connectedComponents(gt_ab.astype(np.uint8))
    for i in range(1, nc):
        c = comp == i
        if c.sum() < 30:
            continue
        n += 1
        ys, xs = np.nonzero(c)
        axis = 0 if np.ptp(ys) >= np.ptp(xs) else 1
        span = np.unique((ys, xs)[axis])
        near = pred_ab & cv2.dilate(c.astype(np.uint8), k).astype(bool)
        hits += np.isin(span, np.unique(np.nonzero(near)[axis])).mean() >= 0.5
    inside = int((pred_ab & cv2.dilate(gt_ab.astype(np.uint8), k).astype(bool)).sum())
    return hits, n, inside, int(pred_ab.sum())


# ----------------------------------------------------------------------------- train

def main() -> None:
    import torch

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=Path("datasets/wireframe_seg/data.yaml"))
    ap.add_argument("--init", type=Path, required=True)
    ap.add_argument("--epochs", type=int, default=25)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--per-image", type=int, default=6)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--tau", type=float, default=0.0, help="logit-adjustment strength (abandoned-wire logit only)")
    ap.add_argument("--abandoned-weight", type=float, default=6.0)
    ap.add_argument("--keep", type=float, default=0.95, help="power/telecom IoU floor vs Stage 1")
    ap.add_argument("--out", default="runs/wirenet/wirenet-r34-stage2.pt")
    a = ap.parse_args()
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = W.build_model(pretrained=False)
    model.load_state_dict(torch.load(a.init, map_location="cpu", weights_only=True))
    model = model.to(device).eval()
    tr, va = W.load_split(a.data, "train"), W.load_split(a.data, "val")

    def evaluate():
        acc = {c: np.zeros(4) for c in CLASSES}
        cov = np.zeros(4)
        for rgb, tgt, _ in va:
            pred = W.predict(model, rgb, device).argmax(0)
            for c, v in W.semantic_scores(pred, tgt).items():
                acc[c] += v
            if (tgt == ABANDONED).any() or (pred == ABANDONED).any():
                cov += abandoned_coverage(pred == ABANDONED, tgt == ABANDONED)
        hit = cov[0] / max(1, cov[1])
        prec = cov[2] / max(1, cov[3])
        ab = {"hit_rate": hit, "precision": prec, "f": 2 * hit * prec / max(1e-9, hit + prec), "pred_px": int(cov[3])}
        return ({c: v[0] / max(1, v[1]) for c, v in acc.items()}, {c: v[2] / max(1, v[3]) for c, v in acc.items()}, ab)

    base_iou, base_tol, base_ab = evaluate()
    print("stage1 abandoned coverage:", {k: round(v, 3) for k, v in base_ab.items()}, flush=True)
    print("stage1 val IoU:", {k: round(v, 3) for k, v in base_iou.items()}, flush=True)
    tight, stats = [], {"kept": 0, "ignored": 0}
    for rgb, tgt, name in tr:
        if (tgt == ABANDONED).any():
            t2, s = tighten_abandoned(tgt, wire_likeness(rgb, W.predict(model, rgb, device)))
            stats = {k: stats[k] + s[k] for k in stats}
            tight.append((rgb, t2, name))
        else:
            tight.append((rgb, tgt, name))
    bank = instance_bank(tight)
    print(f"abandoned-wire label tightening: {stats['kept']} instances kept (tightened), "
          f"{stats['ignored']} set to ignore; copy-paste bank {len(bank)}", flush=True)
    ds = Stage2Tiles(tight, bank, tile=512, per_image=a.per_image)
    freq = np.bincount(np.concatenate([t[t != IGNORE].ravel() for _, t, _ in tight]), minlength=W.N_CLASSES).astype(float)
    prior = freq / freq.sum()
    # Stage 1's square-root inverse-frequency weights (background, vegetation, power, telecom kept as
    # they were, so their balance does not move); only abandoned-wire is boosted
    full = freq / freq.sum()
    w = np.clip(np.sqrt(full.sum() / (W.N_CLASSES * np.maximum(full, 1e-9))), 1, 50)
    w[ABANDONED] = a.abandoned_weight
    cw = torch.tensor(w, dtype=torch.float32, device=device)
    # logit adjustment on the abandoned-wire logit only: the other classes keep Stage 1's calibration
    la = np.zeros(W.N_CLASSES)
    la[ABANDONED] = np.log(max(prior[ABANDONED], 1e-6) / prior[1:].mean())
    log_prior = torch.tensor(la, dtype=torch.float32, device=device)
    print("focal weights:", [round(x, 2) for x in cw.tolist()], "logit adjustment:", [round(x, 2) for x in log_prior.tolist()], flush=True)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    steps = a.epochs * math.ceil(len(ds) / a.batch)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=steps, pct_start=0.15)
    best, best_ab, log = -1.0, -1.0, []
    for ep in range(1, a.epochs + 1):
        model.train()
        t0, order, tot = time.time(), np.random.default_rng(100 + ep).permutation(len(ds)), []
        for b in range(0, len(order), a.batch):
            xs, ys = zip(*(ds[int(i)] for i in order[b:b + a.batch]), strict=True)
            loss = stage2_loss(model(torch.from_numpy(np.stack(xs)).to(device)),
                               torch.from_numpy(np.stack(ys)).to(device), cw, log_prior, a.tau)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            sched.step()
            tot.append(float(loss.detach()))
            if device == "mps" and (b // a.batch) % 10 == 0:
                torch.mps.empty_cache()
        model.eval()
        iou, tol, ab = evaluate()
        wire = float(np.mean([iou[c] for c in CLASSES if c != "Vegetation"]))
        guard = all(iou[c] >= a.keep * base_iou[c] for c in ("power-distribution-lines", "telecom-cable"))
        tag = ""
        out = Path(a.out)
        torch.save(model.state_dict(), out.with_name(out.stem + "-last.pt"))
        if guard and ab["f"] > best:  # selection: abandoned-wire coverage F, power/telecom guarded
            best = ab["f"]
            torch.save(model.state_dict(), out)
            tag = f" (new best -> {out})"
        ab_tol = tol["abandoned-wire"]
        if guard and ab_tol > best_ab:
            best_ab = ab_tol
            torch.save(model.state_dict(), out.with_name(out.stem + "-best-abandoned-tolerant.pt"))
            tag += " (new best abandoned tolerant)"
        log.append({"epoch": ep, "loss": float(np.mean(tot)), "iou": iou, "tolerant_iou": tol, "wire_miou": wire,
                    "abandoned_coverage": ab,
                    "guard": guard, "seconds": round(time.time() - t0, 1)})
        print(f"stage2 epoch {ep}/{a.epochs}: loss {np.mean(tot):.3f}, abandoned {iou['abandoned-wire']:.3f} "
              f"(tolerant {tol['abandoned-wire']:.3f}), power {iou['power-distribution-lines']:.3f}, "
              f"telecom {iou['telecom-cable']:.3f}, veg {iou['Vegetation']:.3f}, wire mIoU {wire:.3f}, "
              f"abandoned hit {ab['hit_rate']:.2f} prec {ab['precision']:.2f} F {ab['f']:.3f} ({ab['pred_px']} px), "
              f"guard {'ok' if guard else 'FAIL'} {time.time() - t0:.0f}s{tag}", flush=True)
        Path(a.out).with_suffix(".log.json").write_text(json.dumps(log, indent=2, default=float))
        if device == "mps":
            torch.mps.empty_cache()


if __name__ == "__main__":
    main()
