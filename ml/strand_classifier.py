"""v2.17 strand-level wire-type classifier: one class per physical wire, decided from the whole wire and its scene.

WireNet decides the type pixel by pixel from a ~200 px receptive field; the v2.16 strand rule then sums those
pixel votes. A telecom cable and a power line can look identical in a 2 px crop, but they differ as whole
objects and by where they sit in the scene: power conductors hang highest on the pole, are near-straight and
evenly spaced, telecom cables hang below them, sag more, are thicker and often bundled. This head looks at each
*strand* (the unit the screener reports) with:

    model evidence   summed class shares over the strand (the v2.16 rule), mean P(wire), mean entropy
    geometry         length, chord / length (straightness), sag ratio, orientation, mean width
    scene position   mean / top height in the photo, share of other strands above it, its rank by height
    appearance       darkness against a ring of local background, colour, local texture
    pretrained       DINOv2-small (Apache-2.0, frozen) patch features pooled along the strand's skeleton:
                     context the segmentation network never had (pole tops, crossarms, building faces)

Labels and data hygiene
    * trained on the TRAIN split only, on strands that lie on a labelled power or telecom wire
      (>= 50 % of the skeleton within tolerance of one class); its target is that labelled class.
      Strands on no label are NOT used as negatives (the labels omit real wires); the head never removes
      a strand, it only names its type.
    * the head's probability replaces the pixel-vote share; a strand is "uncertain" when the head's top class
      probability is below ``min_conf`` (chosen on validation, never on test)

    python -m ml.strand_classifier features --cache runs/cache/train-v216 --split train
    python -m ml.strand_classifier features --cache runs/cache/val-v216 --split val
    python -m ml.strand_classifier fit      # -> models/strand_head_v217.pt (+ .json with feature stats)
    python -m ml.strand_classifier eval     # validation centerline metrics with the head vs the v2.16 rule
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from app.core.config import ROOT_DIR, load_analysis_config
from app.pipeline import centerline_eval as ce
from app.pipeline.strand_head import N_DINO, PT, TC, WIRE_IDS, Dino, Head, load_head, strand_features
from app.pipeline.wire_instances import strand_spans
from app.pipeline.wirenet import span_params

FEAT_DIR = Path("runs/v217/strand_feats")


# ----------------------------------------------------------------------------- pretrained features

# ----------------------------------------------------------------------------- hand features

def truth(strands, gts, shape, min_on: float = 0.5) -> list[str | None]:
    tol = ce.tolerance(shape)
    near = {c: ce._near(np.any([m for k, m in gts if k == c], axis=0), tol) if any(k == c for k, _ in gts)
            else np.zeros(shape, bool) for c in ce.WIRE_CLASSES}
    out = []
    for s in strands:
        sk = ce._skel(s.mask)
        n = int(sk.sum())
        on = {c: int((sk & near[c]).sum()) for c in ce.WIRE_CLASSES}
        best = max(on, key=on.get)
        out.append(best if n and on[best] >= min_on * n else None)
    return out


# ----------------------------------------------------------------------------- stages

def features(cache: Path, split: str, tau: float | None, tag: str) -> None:
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml").segmentation
    params = span_params(cfg)
    import os
    dino = Dino(os.environ.get("DINO_DEVICE"))
    rows = []
    FEAT_DIR.mkdir(parents=True, exist_ok=True)
    for f in sorted(cache.glob("*.npz")):
        z = np.load(f)
        probs = z["probs"].astype(np.float32)
        rgb = cv2.cvtColor(cv2.imread(f"datasets/wireframe_seg/images/{split}/{f.stem}.jpg"), cv2.COLOR_BGR2RGB)
        gts = [(c, m) for c, m in zip(z["gt_classes"].tolist(), z["gt_masks"], strict=True) if c in ce.WIRE_CLASSES]
        strands = strand_spans(probs, WIRE_IDS, params, tau=tau, min_class_share=0.0)
        if not strands:
            continue
        hand, dn = strand_features(strands, probs, rgb, dino.grid(rgb))
        for s, h, d, t in zip(strands, hand, dn, truth(strands, gts, probs.shape[1:]), strict=True):
            rows.append({"stem": f.stem, "hand": h, "dino": d, "truth": t, "pixel_class": s.wire_class})
        print(f"{f.stem[:40]} {len(strands)} strands", flush=True)
    np.savez_compressed(FEAT_DIR / f"{split}{tag}.npz", stem=np.array([r["stem"] for r in rows]),
                        hand=np.stack([r["hand"] for r in rows]), dino=np.stack([r["dino"] for r in rows]),
                        truth=np.array([r["truth"] or "" for r in rows]), pixel_class=np.array([r["pixel_class"] for r in rows]))


def _fit_net(head: Head, x: np.ndarray, y: np.ndarray, w: np.ndarray, epochs: int, seed: int = 0) -> None:
    import torch
    torch.manual_seed(seed)
    opt = torch.optim.AdamW(head.net.parameters(), lr=3e-3, weight_decay=1e-2)
    X, Y, Wt = torch.from_numpy(x), torch.from_numpy(y.astype(np.float32)), torch.from_numpy(w.astype(np.float32))
    head.net.train()
    for _ in range(epochs):
        opt.zero_grad()
        loss = (torch.nn.functional.binary_cross_entropy_with_logits(head.net(X)[:, 0], Y, reduction="none") * Wt).mean()
        loss.backward()
        opt.step()
    head.net.eval()


def fit(tag: str, out: Path) -> None:
    z = np.load(FEAT_DIR / f"train{tag}.npz")
    keep = np.isin(z["truth"], [PT, TC])
    hand, dino, y = z["hand"][keep], z["dino"][keep], (z["truth"][keep] == TC).astype(int)
    w = np.exp(hand[:, 8])  # log1p(skeleton length) -> length: longer strands carry more centerline
    w = w / w.mean()
    mu, sd = hand.mean(0), hand.std(0) + 1e-6
    dmu = dino.mean(0)
    u, s, vt = np.linalg.svd(dino - dmu, full_matrices=False)
    pca = vt[:N_DINO]
    psd = ((dino - dmu) @ pca.T).std(0) + 1e-6
    stats = {"mu": mu.tolist(), "sd": sd.tolist(), "dmu": dmu.tolist(), "pca": pca.tolist(), "psd": psd.tolist()}
    # epochs by 5-fold cross-validation on train images (grouped by photo), never on validation
    stems = z["stem"][keep]
    folds = {s: i % 5 for i, s in enumerate(sorted(set(stems)))}
    fid = np.array([folds[s] for s in stems])
    best_ep, best_acc = 0, -1
    for ep in (100, 200, 400, 800):
        acc = []
        for k in range(5):
            h = Head(stats)
            xtr = h.x(hand[fid != k], dino[fid != k])
            _fit_net(h, xtr, y[fid != k], w[fid != k], ep)
            pr = h.p_telecom(hand[fid == k], dino[fid == k]) >= 0.5
            acc.append(np.average(pr == y[fid == k], weights=w[fid == k]))
        print(f"cv epochs {ep}: length-weighted accuracy {np.mean(acc):.3f}", flush=True)
        if np.mean(acc) > best_acc:
            best_ep, best_acc = ep, float(np.mean(acc))
    base = float(np.average((hand[:, 1] > hand[:, 0]) == y, weights=w))  # the v2.16 pixel-vote rule
    head = Head(stats)
    _fit_net(head, head.x(hand, dino), y, w, best_ep)
    import torch
    torch.save(head.net.state_dict(), out)
    meta = {"stats": stats, "epochs": best_ep, "cv_accuracy": best_acc, "pixel_vote_accuracy_train": base,
            "n_train_strands": int(len(y)), "n_telecom": int(y.sum()), "dino": "facebook/dinov2-small", "n_dino": N_DINO}
    out.with_suffix(".json").write_text(json.dumps(meta))
    print(f"strand head: {len(y)} strands ({y.sum()} telecom), cv accuracy {best_acc:.3f} vs pixel vote {base:.3f}; "
          f"epochs {best_ep} -> {out}")


def _labelled(z, train_weight: float = 1.0):
    keep = np.isin(z["truth"], [PT, TC])
    w = np.exp(z["hand"][keep][:, 8])
    return z["hand"][keep], z["dino"][keep], (z["truth"][keep] == TC).astype(int), w / w.mean() * train_weight, z["stem"][keep]


def train_head(hand, dino, y, w, epochs: int) -> Head:
    mu, sd = hand.mean(0), hand.std(0) + 1e-6
    dmu = dino.mean(0)
    _, _, vt = np.linalg.svd(dino - dmu, full_matrices=False)
    pca = vt[:N_DINO]
    psd = ((dino - dmu) @ pca.T).std(0) + 1e-6
    h = Head({"mu": mu.tolist(), "sd": sd.tolist(), "dmu": dmu.tolist(), "pca": pca.tolist(), "psd": psd.tolist()})
    _fit_net(h, h.x(hand, dino), y, w, epochs)
    return h


def stack(tag: str, train_weight: float, epochs: int, out: Path | None) -> np.ndarray:
    """Stacking on validation: the WireNet members never trained on these photos, so their strands carry the
    errors the head must learn. Returns leave-one-photo-out predictions for every validation strand (the numbers
    reported); with ``out`` also fits the final head on all validation strands (+ train strands at train_weight)."""
    zv = np.load(FEAT_DIR / f"val{tag}.npz")
    hv, dv, yv, wv, sv = _labelled(zv)
    parts = [(hv, dv, yv, wv)]
    if train_weight > 0:
        ht, dt, yt, wt, _ = _labelled(np.load(FEAT_DIR / f"train{tag}.npz"), train_weight)
        parts.append((ht, dt, yt, wt))
    oof = np.full(len(zv["stem"]), np.nan, np.float32)
    for stem in sorted(set(zv["stem"].tolist())):
        tr = sv != stem
        X = [np.concatenate([hv[tr], *[q[0] for q in parts[1:]]]), np.concatenate([dv[tr], *[q[1] for q in parts[1:]]]),
             np.concatenate([yv[tr], *[q[2] for q in parts[1:]]]), np.concatenate([wv[tr], *[q[3] for q in parts[1:]]])]
        h = train_head(*X, epochs)
        rows = zv["stem"] == stem
        oof[rows] = h.p_telecom(zv["hand"][rows], zv["dino"][rows])
    if out is not None:
        h = train_head(np.concatenate([q[0] for q in parts]), np.concatenate([q[1] for q in parts]),
                       np.concatenate([q[2] for q in parts]), np.concatenate([q[3] for q in parts]), epochs)
        import torch
        torch.save(h.net.state_dict(), out)
        out.with_suffix(".json").write_text(json.dumps({
            "stats": h.stats, "epochs": epochs, "fit_on": "validation strands (stacking)" +
            (f" + train strands x{train_weight}" if train_weight else ""), "n_val_strands": int(len(yv)),
            "dino": "facebook/dinov2-small", "n_dino": N_DINO}))
    return oof


def _mixed(preds, gts, k=cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))) -> tuple[int, int]:
    """v2.16 mixed-class definition (ml/experiments/v217_eval.py): covered labelled wires, of which >= 2 classes."""
    cov = mixed = 0
    for _, gm in gts:
        gw = cv2.dilate(gm.astype(np.uint8), k).astype(bool)
        per: dict[str, int] = {}
        for pr in preds:
            x = int((pr.mask & gw).sum())
            if x >= 20:
                per[pr.wire_class] = per.get(pr.wire_class, 0) + x
        if per:
            cov += 1
            tot = sum(per.values())
            mixed += sum(1 for x in per.values() if x >= 0.2 * tot) >= 2
    return cov, mixed


def evaluate(cache: Path, head_path: Path | None, tau: float | None, tag: str, p_tel: np.ndarray | None = None,
             group_gap: float = 0.0) -> dict:
    """Validation centerline metrics: v2.16 pixel-vote classes vs head classes, over a min_conf grid."""
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml").segmentation
    params = span_params(cfg)
    z = np.load(FEAT_DIR / f"val{tag}.npz")
    by_stem: dict[str, list[int]] = {}
    for i, s in enumerate(z["stem"]):
        by_stem.setdefault(str(s), []).append(i)
    if p_tel is None:
        p_tel = load_head(head_path).p_telecom(z["hand"], z["dino"])
    grid = [0.5, 0.55, 0.6, 0.65, 0.7]
    res = {"v216_rule": {}, "head": {}}
    per = {("v216", m): [] for m in (0.5, 0.6, 0.9)} | {("head", m): [] for m in grid}
    tallies = {k: ce.StrandTally() for k in per}
    mixed: dict = {}
    for f in sorted(cache.glob("*.npz")):
        zz = np.load(f)
        probs = zz["probs"].astype(np.float32)
        shape = probs.shape[1:]
        gts = [(c, m) for c, m in zip(zz["gt_classes"].tolist(), zz["gt_masks"], strict=True) if c in ce.WIRE_CLASSES]
        strands = strand_spans(probs, WIRE_IDS, params, tau=tau, min_class_share=0.0)
        idx = by_stem.get(f.stem, [])
        assert len(idx) == len(strands), f"{f.stem}: feature rows do not match strands"
        pg = p_tel.astype(np.float64).copy()
        if group_gap > 0 and idx:
            from app.pipeline.strand_head import continuation_groups, group_probabilities
            keep = [j for j, st in enumerate(strands) if max(st.class_share, key=st.class_share.get) != "abandoned-wire"]
            sub = [strands[j] for j in keep]
            if len(sub) > 1:
                lengths = np.array([max(int(ce._skel(st.mask).sum()), 1) for st in sub], float)
                rows = np.asarray(idx)[keep]
                pg[rows] = group_probabilities(p_tel[rows], lengths, continuation_groups(sub, group_gap))
        for (kind, m), lst in per.items():
            preds = []
            for s, i in zip(strands, idx, strict=True):
                top = max(s.class_share, key=s.class_share.get)
                if kind == "v216" or top == "abandoned-wire":
                    conf = s.class_share[top] >= m if kind == "v216" else True
                    preds.append(ce.Pred(top, s.mask, conf))
                else:
                    pt = float(pg[i])
                    preds.append(ce.Pred(TC if pt >= 0.5 else PT, s.mask, max(pt, 1 - pt) >= m))
            tallies[(kind, m)].add(preds, gts, shape)
            lst.append(ce.image_counts(preds, gts, shape))
            cv, mx = _mixed(preds, gts)
            mixed[(kind, m)] = [a + b for a, b in zip(mixed.get((kind, m), [0, 0]), (cv, mx), strict=True)]
    fm = lambda d: f"P {d['precision']:.3f} R {d['recall']:.3f} F1 {d['f1']:.3f}"  # noqa: E731
    for (kind, m), lst in per.items():
        s = ce.summarize(lst, 1000)
        t = tallies[(kind, m)].report()
        mr = mixed[(kind, m)][1] / max(mixed[(kind, m)][0], 1)
        res["v216_rule" if kind == "v216" else "head"][m] = {"centerline": s, "strand_class": t, "mixed_rate": mr}
        print(f"{kind:5s} min {m}: power {fm(s[PT])} | telecom {fm(s[TC])} | wire {fm(s['wire'])} | "
              f"acc {t['accuracy']:.3f} unc {t['uncertain_rate']:.3f} mixed {mr:.4f}", flush=True)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["features", "fit", "eval", "stack"])
    ap.add_argument("--train-weight", type=float, default=0.0)
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--group-gap", type=float, default=0.0, help="continuation grouping: max end gap px (0 = off)")
    ap.add_argument("--cache", type=Path)
    ap.add_argument("--split", default="val")
    ap.add_argument("--tau", type=float, default=None, help="strand mask rule: None = argmax (shipped), else P(wire) >= tau")
    ap.add_argument("--tag", default="")
    ap.add_argument("--head", type=Path, default=ROOT_DIR / "models" / "strand_head_v217.pt")
    a = ap.parse_args()
    if a.stage == "features":
        features(a.cache, a.split, a.tau, a.tag)
    elif a.stage == "stack":
        oof = stack(a.tag, a.train_weight, a.epochs, a.head if a.save else None)
        r = evaluate(a.cache or Path("runs/cache/val-v216"), None, a.tau, a.tag, p_tel=oof, group_gap=a.group_gap)
        r["protocol"] = f"leave-one-photo-out on validation; train strands weight {a.train_weight}; epochs {a.epochs}"
        Path(f"docs/thesis/results/v217_strand_head_stack_val{a.tag}_tw{a.train_weight}"
             f"{'_g' + str(int(a.group_gap)) if a.group_gap else ''}.json").write_text(
            json.dumps(r, indent=1, default=str))
    elif a.stage == "fit":
        fit(a.tag, a.head)
    else:
        r = evaluate(a.cache or Path("runs/cache/val-v216"), a.head, a.tau, a.tag)
        Path(f"docs/thesis/results/v217_strand_head_val{a.tag}.json").write_text(json.dumps(r, indent=1, default=str))


if __name__ == "__main__":
    main()
