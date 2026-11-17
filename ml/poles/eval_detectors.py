"""v2.17: compare pole detectors on one labelled split with one definition (box IoU >= 0.5, one-to-one, greedy by score).

Detectors: a YOLO checkpoint (``*.pt``). (The v2.18 comparison also ran ``grounding-dino:<hf id>``; that backend was
removed from app/pipeline/poles.py in v2.23, and its recorded results stay in docs/thesis/results.) Each image is run once at a low threshold;
precision / recall / F1 are then computed at every threshold of a grid, AP50 (101-point) from the full ranked list.
The operating threshold is chosen on **val** (max F1) and then applied unchanged to **test**:

    python -m ml.poles.eval_detectors --data datasets/external/poles_lvis/data.yaml --split val \\
        --detector runs/detect/runs/poles/yolo11s-lvis/weights/best.pt
    python -m ml.poles.eval_detectors --split test --threshold-from docs/thesis/results/v217_poles_select_val.json ...

Reported per detector: AP50, the val-chosen threshold, P / R / F1 there with 95 % image-bootstrap intervals,
images / boxes, mean latency, and peak process memory (ru_maxrss).
"""

from __future__ import annotations

import argparse
import json
import resource
import time
from pathlib import Path

import cv2
import numpy as np
import yaml

from app.pipeline.evaluation import average_precision
from app.pipeline.poles import _iou, clean_boxes, make_pole_detector

GRID = [round(x, 2) for x in np.arange(0.1, 0.91, 0.05)]


def gt_boxes(lbl: Path, w: int, h: int) -> list[list[float]]:
    out = []
    for line in (lbl.read_text().splitlines() if lbl.exists() else []):
        if not line.strip():
            continue
        _, x, y, bw, bh = map(float, line.split()[:5])
        out.append([(x - bw / 2) * w, (y - bh / 2) * h, (x + bw / 2) * w, (y + bh / 2) * h])
    return out


SMALL = 32 ** 2  # COCO "small" (area < 32^2 px in the original image)


def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def match_status(pred: list[dict], gts: list[list[float]], thr: float = 0.5, ignore_small: bool = False) -> list[str]:
    """Per prediction (by descending score): "tp", "fp" or "ign". With ``ignore_small`` (COCO medium+large protocol)
    small ground-truth boxes are ignore regions: a prediction matched to one, or an unmatched prediction that is
    itself small, counts neither way. Non-ignored ground truth is matched first."""
    ign = [ignore_small and _area(g) < SMALL for g in gts]
    used, out = set(), []
    for d in sorted(pred, key=lambda d: -d["confidence"]):
        best, j = 0.0, -1
        for pass_ign in (False, True):
            for k, g in enumerate(gts):
                if ign[k] == pass_ign and k not in used and (v := _iou(d["bbox"], g)) > best:
                    best, j = v, k
            if best >= thr:
                break
        if best >= thr:
            used.add(j)
            out.append("ign" if ign[j] else "tp")
        else:
            out.append("ign" if ignore_small and _area(d["bbox"]) < SMALL else "fp")
    return out


def n_counted(gts, ignore_small: bool) -> int:
    return sum(1 for g in gts if not (ignore_small and _area(g) < SMALL))


def match(pred: list[dict], gts: list[list[float]], thr: float = 0.5) -> list[bool]:
    return [s == "tp" for s in match_status(pred, gts, thr)]


def run_detector(spec: str, images: list[Path], low: float, device: str = "auto") -> tuple[list[list[dict]], float]:
    det = make_pole_detector(spec, low, Path("."), device=device)
    raw_fn = (lambda rgb: det.raw(rgb, low)) if hasattr(det, "raw") else det.detect
    out, t = [], []
    for p in images:
        rgb = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
        t0 = time.perf_counter()
        found = raw_fn(rgb)
        t.append(time.perf_counter() - t0)
        if found is None:
            raise SystemExit(f"{spec}: {det.error}")
        out.append(found)
        if len(out) % 25 == 0:
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20
            print(f"  {spec}: {len(out)}/{len(images)} images, {np.mean(t):.2f} s/img, peak {rss:.0f} MB", flush=True)
    return out, float(np.mean(t[1:] or t))


def score(dets: list[list[dict]], gts: list[list[list[float]]], thr: float, zero_shot: bool,
          ignore_small: bool = False) -> dict:
    tp = fp = n_gt = 0
    per = []
    for d, g in zip(dets, gts, strict=True):
        d = [x for x in d if x["confidence"] >= thr]
        if zero_shot:
            d = clean_boxes(d)
        m = match_status(d, g, ignore_small=ignore_small)
        a, b, c = m.count("tp"), m.count("fp"), n_counted(g, ignore_small)
        per.append((a, b, c))
        tp, fp, n_gt = tp + a, fp + b, n_gt + c
    out = _prf(tp, fp, n_gt) | {"per_image": per}
    out["images_with_false_detection"] = sum(1 for _, b, _ in per if b)
    out["images_with_missed_pole"] = sum(1 for a, _, c in per if c > a)
    return out


def ap50(dets, gts, zero_shot: bool, ignore_small: bool = False) -> float:
    tps, scores = [], []
    for ds, g in zip(dets, gts, strict=True):
        ds = sorted(clean_boxes(ds) if zero_shot else ds, key=lambda x: -x["confidence"])
        for x, st in zip(ds, match_status(ds, g, ignore_small=ignore_small), strict=True):
            if st != "ign":
                tps.append(st == "tp")
                scores.append(x["confidence"])
    return average_precision(scores, tps, sum(n_counted(g, ignore_small) for g in gts))


def _prf(tp, fp, n_gt) -> dict:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / n_gt if n_gt else None
    f = 2 * p * r / (p + r) if p and r else 0.0
    return {"precision": p, "recall": r, "f1": f, "tp": tp, "fp": fp, "fn": n_gt - tp}


def boot(per, n=2000, seed=0) -> dict:
    rng = np.random.default_rng(seed)
    arr = np.asarray(per, float)
    out = {"precision": [], "recall": [], "f1": []}
    for _ in range(n):
        s = arr[rng.integers(0, len(arr), len(arr))].sum(0)
        x = _prf(s[0], s[1], s[2])
        for k in out:
            if x[k] is not None:
                out[k].append(x[k])
    return {f"{k}_ci95": [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] for k, v in out.items() if v}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="datasets/external/poles_lvis/data.yaml")
    ap.add_argument("--split", default="val")
    ap.add_argument("--detector", action="append", required=True)
    ap.add_argument("--threshold-from", type=Path, default=None, help="val result json: reuse its chosen thresholds")
    ap.add_argument("--tag", default="")
    ap.add_argument("--prefix", default="v218")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--save-dets", default=None, help="folder: keep raw detections (for the verifier stage)")
    a = ap.parse_args()
    spec = yaml.safe_load(Path(a.data).read_text())
    root = Path(spec.get("path", Path(a.data).parent))
    images = sorted((root / spec[a.split]).glob("*.jpg"))
    gts = []
    for p in images:
        h, w = cv2.imread(str(p)).shape[:2]
        gts.append(gt_boxes(Path(str(p).replace("/images/", "/labels/")).with_suffix(".txt"), w, h))
    chosen = json.loads(a.threshold_from.read_text())["detectors"] if a.threshold_from else {}
    res = {"data": a.data, "split": a.split, "images": len(images), "boxes": sum(map(len, gts)), "detectors": {}}
    for d in a.detector:
        zs = d.startswith("grounding-dino:") or d.startswith("owlv2:")
        dets, lat = run_detector(d, images, 0.1 if zs else 0.01, a.device)
        entry = {"latency_s": lat, "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20}
        for proto, ig in (("all", False), ("medium_large", True)):
            curve = {t: {k: v for k, v in score(dets, gts, t, zs, ig).items() if k != "per_image"} for t in GRID}
            key = d if proto == "all" else f"{d}|medium_large"
            thr = chosen[key]["threshold"] if key in chosen else max(GRID, key=lambda t: curve[t]["f1"])
            at = score(dets, gts, thr, zs, ig)
            r = {"protocol": proto, "ap50": ap50(dets, gts, zs, ig), "threshold": thr,
                 "chosen_on": "val (reused)" if key in chosen else a.split,
                 **{k: v for k, v in at.items() if k != "per_image"}, **boot(at["per_image"]), **entry, "curve": curve}
            res["detectors"][key] = r
            print(f"{key}: AP50 {r['ap50']:.3f} | thr {thr} P {r['precision'] or 0:.3f} R {r['recall'] or 0:.3f} "
                  f"F1 {r['f1']:.3f} CI {[round(x, 3) for x in r.get('f1_ci95', [])]} | FP-imgs "
                  f"{r['images_with_false_detection']} miss-imgs {r['images_with_missed_pole']} | {lat:.2f} s/img",
                  flush=True)
        if a.save_dets:
            Path(a.save_dets).mkdir(parents=True, exist_ok=True)
            (Path(a.save_dets) / f"{Path(d).parent.parent.name if d.endswith('.pt') else d.split('/')[-1]}"
             f"_{a.split}.json").write_text(json.dumps({"images": [p.name for p in images], "dets": dets}))
        del dets
    name = Path(a.data).parent.name
    out = Path(f"docs/thesis/results/{a.prefix}_poles_{name}_{a.split}{a.tag}.json")
    out.write_text(json.dumps(res, indent=1))
    print(out)


if __name__ == "__main__":
    main()
