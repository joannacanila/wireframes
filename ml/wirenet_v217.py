"""WireNet v2.17 fine-tune: per-class centerline topology, selected on the metric the screener reports.

Why: every v2.7-v2.12 WireNet was selected on strict mask IoU against 2-4 px wide hand-drawn polygons, while the
v2.17 question is centerline precision / recall / F1 per wire class on the *strands* the app shows. Three encoders
(R34, MiT-B1, ConvNeXt-T) and their fusions all reach telecom strand F1 0.71-0.73 on validation
(docs/thesis/results/v217_fusion_val.json), and 23 % of labelled telecom centerline is never or only weakly
predicted (v217_telecom_loss_val.json). This fine-tune changes what the model is trained and chosen for:

    loss   cross-entropy (class-weighted) + soft Dice (as Stage 1)
           + clDice on the wire union (as Stage 1)
           + per-class clDice on power and on telecom: a wire is rewarded for being continuous *in its own class*,
             which both recovers broken thin cables and discourages a cable whose pixels flip between classes
    data   train split only, 512 px tiles, 85 % centred on a wire pixel, half of those on a telecom pixel
    init   models/wirenet-r34.pt (never overwritten); lower learning rate, short schedule
    select each epoch: shipped inference (mirrored pass) on the validation photos, shipped strand extraction,
           score = mean of power and telecom strand centerline F1 (the reported numbers); the test split is never read

    python -m ml.wirenet_v217 --epochs 12 --out runs/wirenet/wirenet-r34-v217.pt
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np

from app.core.config import CLASSES, ROOT_DIR, load_analysis_config
from app.pipeline import centerline_eval as ce
from app.pipeline.wire_instances import strand_spans
from app.pipeline.wirenet import predict_probs_tta, span_params
from ml import wirenet as W

POWER = CLASSES.index("power-distribution-lines") + 1
TELECOM = CLASSES.index("telecom-cable") + 1


class TelecomTiles(W.TileDataset):
    """Stage 1 tiles, but half of the wire-centred tiles are centred on a telecom pixel."""

    def __init__(self, items, tile=512, per_image=6, wire_bias=0.85, telecom_share=0.5, seed=0):
        super().__init__(items, tile, per_image, wire_bias, True, seed)
        self.telecom_share = telecom_share

    def __getitem__(self, i):
        rgb, tgt, _ = self.items[i // self.per_image]
        if self.rng.random() < self.telecom_share * self.wire_bias and (tgt == TELECOM).any():
            # crop around a telecom pixel first, then let the parent's augmentation run on that crop
            pix = np.argwhere(tgt == TELECOM)
            cy, cx = pix[self.rng.integers(len(pix))]
            T = int(self.tile * 1.4)
            h, w = tgt.shape
            y0, x0 = int(np.clip(cy - T // 2, 0, max(0, h - T))), int(np.clip(cx - T // 2, 0, max(0, w - T)))
            sub = (rgb[y0:y0 + T, x0:x0 + T], tgt[y0:y0 + T, x0:x0 + T], "")
            items, self.items = self.items, [sub] * len(self.items)
            try:
                return super().__getitem__(i)
            finally:
                self.items = items
        return super().__getitem__(i)


def loss_v217(logits, target, cw, w_cls: float):
    loss, parts = W.wire_loss(logits, target, cw)
    prob = logits.softmax(1)
    extra = 0.0
    for c in (POWER, TELECOM):
        t = (target == c).float()[:, None]
        if t.sum() < 20:
            continue
        p = prob[:, c:c + 1]
        sp, st = W.soft_skel(p), W.soft_skel(t)
        tprec = ((sp * t).sum() + 1) / (sp.sum() + 1)
        tsens = ((st * p).sum() + 1) / (st.sum() + 1)
        extra = extra + (1 - 2 * tprec * tsens / (tprec + tsens))
    return loss + w_cls * extra, parts


def val_score(model, device, va, params, min_share=0.6) -> dict:
    per, tally = [], ce.StrandTally()
    for rgb, _, gts in va:
        p = predict_probs_tta(model, rgb, device, "flip")
        preds = [ce.Pred(s.wire_class, s.mask, s.class_status != "uncertain")
                 for s in strand_spans(p, W.WIRE_IDS, params, min_class_share=min_share)]
        tally.add(preds, gts, p.shape[1:])
        per.append(ce.image_counts(preds, gts, p.shape[1:]))
    s = ce.summarize(per, 0)
    return {"power": s["power-distribution-lines"], "telecom": s["telecom-cable"], "wire": s["wire"],
            "strand_accuracy": tally.report()["accuracy"]}


def main() -> None:
    import torch

    from app.pipeline.segment import parse_yolo_seg_labels
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("datasets/wireframe_seg/data.yaml"))
    ap.add_argument("--init", type=Path, default=ROOT_DIR / "models" / "wirenet-r34.pt")
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--per-image", type=int, default=6)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--w-cls", type=float, default=0.5, help="weight of the per-class clDice terms")
    ap.add_argument("--telecom-share", type=float, default=0.5)
    ap.add_argument("--out", default="runs/wirenet/wirenet-r34-v217.pt")
    a = ap.parse_args()
    out = Path(a.out)
    assert out.resolve() != (ROOT_DIR / "models" / "wirenet-r34.pt").resolve(), "never overwrite the shipped weights"
    out.parent.mkdir(parents=True, exist_ok=True)
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml").segmentation
    params = span_params(cfg)
    tr = W.load_split(a.data, "train")
    va = []
    for rgb, _, name in W.load_split(a.data, "val"):
        lbl = Path(f"datasets/wireframe_seg/labels/val/{Path(name).stem}.txt")
        gts = [(g.wire_class, g.mask) for g in parse_yolo_seg_labels(lbl.read_text() if lbl.exists() else "", *rgb.shape[:2])
               if g.wire_class in ce.WIRE_CLASSES]
        va.append((rgb, None, gts))
    ds = TelecomTiles(tr, 512, a.per_image, telecom_share=a.telecom_share)
    freq = np.bincount(np.concatenate([t.ravel() for _, t, _ in tr]), minlength=W.N_CLASSES).astype(float)
    cw = torch.tensor(np.clip((freq.sum() / (W.N_CLASSES * np.maximum(freq, 1))) ** 0.5, 1, 50), dtype=torch.float32).to(device)
    model = W.load_wirenet(a.init, device)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    steps = a.epochs * math.ceil(len(ds) / a.batch)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=steps, pct_start=0.15)
    out.with_suffix(".arch.json").write_text(json.dumps({"encoder": "resnet34", "arch": "Unet", "classes": W.N_CLASSES}))
    model.eval()
    base = val_score(model, device, va, params)
    log = [{"epoch": 0, **base}]
    best = (base["power"]["f1"] + base["telecom"]["f1"]) / 2
    print(f"epoch 0 (init): power F1 {base['power']['f1']:.3f} telecom P {base['telecom']['precision']:.3f} "
          f"R {base['telecom']['recall']:.3f} F1 {base['telecom']['f1']:.3f}", flush=True)
    for ep in range(1, a.epochs + 1):
        model.train()
        t0, order, tot = time.time(), np.random.default_rng(100 + ep).permutation(len(ds)), []
        for b in range(0, len(order), a.batch):
            xs, ys = zip(*(ds[int(i)] for i in order[b:b + a.batch]), strict=True)
            x = torch.from_numpy(np.stack(xs)).to(device)
            y = torch.from_numpy(np.stack(ys)).to(device)
            loss, _ = loss_v217(model(x), y, cw, a.w_cls)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            sched.step()
            tot.append(float(loss.detach()))
            if device == "mps" and (b // a.batch) % 10 == 0:
                torch.mps.empty_cache()
        model.eval()
        r = val_score(model, device, va, params)
        score = (r["power"]["f1"] + r["telecom"]["f1"]) / 2
        tag = ""
        torch.save(model.state_dict(), out.with_name(out.stem + "-last.pt"))
        if score > best:
            best = score
            torch.save(model.state_dict(), out)
            tag = " (new best)"
        log.append({"epoch": ep, "loss": float(np.mean(tot)), **r, "seconds": round(time.time() - t0, 1)})
        print(f"epoch {ep}/{a.epochs}: loss {np.mean(tot):.3f} | power P {r['power']['precision']:.3f} R "
              f"{r['power']['recall']:.3f} F1 {r['power']['f1']:.3f} | telecom P {r['telecom']['precision']:.3f} R "
              f"{r['telecom']['recall']:.3f} F1 {r['telecom']['f1']:.3f} | acc {r['strand_accuracy']:.3f} | "
              f"{time.time() - t0:.0f}s{tag}", flush=True)
        out.with_suffix(".log.json").write_text(json.dumps(log, indent=1, default=float))
        if device == "mps":
            torch.mps.empty_cache()


if __name__ == "__main__":
    main()
