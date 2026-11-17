"""Evaluation runs (Sections 3.9 - 3.11). Each subcommand writes one section of
var/evaluation/report.json (a local record of the latest evaluation runs; the thesis copies live in docs/thesis/results).

    # Segmentation on the held-out test set - run ONCE, for the final figures (Section 3.4.5)
    python -m ml.evaluate segmentation --data datasets/wireframe_seg/data.yaml --split test
    python -m ml.evaluate segmentation --data ... --split test --degraded [--no-enhancement]
    python -m ml.evaluate ultralytics --data ... --split test [--degraded]   # per-class box + mask

    # Agreement with the engineer's reference assessments (second part, after clearance)
    python -m ml.evaluate reference --clutter clutter.csv --severity severity.csv --priority priority.csv

    # Supplementary: geometric layer on synthetic wires with exact ground truth
    python -m ml.evaluate geometry --scenes 40

CSV formats
    clutter.csv   analysis_id,clutter_rating          (1-5, H1)
    severity.csv  analysis_id,wire_index,severity     (0-10, H3)
    priority.csv  pole_code,reference_rank            (1 = inspect first, H4)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from app.core.config import ROOT_DIR, get_settings, load_analysis_config
from app.pipeline import evaluation as ev

REPORT_NAME = "report.json"


def report_path() -> Path:
    return get_settings().data_dir / "evaluation" / REPORT_NAME


def _clean(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, (float, np.floating)):
        return None if not math.isfinite(float(v)) else float(v)
    if isinstance(v, np.integer):
        return int(v)
    return v


def write_section(name: str, payload: dict, path: Path | None = None) -> Path:
    path = path or report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(path.read_text()) if path.exists() else {}
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    data[name] = _clean({**payload, "generated_at": datetime.now(UTC).isoformat(),
                         "config_fingerprint": cfg.fingerprint})
    path.write_text(json.dumps(data, indent=2))
    return path


# ----------------------------------------------------------------------------- geometry


def geometry_benchmark(n_scenes: int = 40, seed: int = 0) -> dict:
    """Run Algorithms 4-5 on synthetic wires whose catenary is known exactly."""
    from app.devtools.synthetic import make_scene
    from app.pipeline.catenary import _chord_sag
    from app.pipeline.runner import score_wire
    from app.pipeline.sag_limits import EngineeringContext
    from app.pipeline.segment import parse_yolo_seg_labels

    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    # v2.21: the geometric layer only; the assessment policy (classes with a reference, both endpoints in frame)
    # decides whether a measured span gets a severity, which this benchmark does not evaluate
    cfg = cfg.model_copy(update={"crs": cfg.crs.model_copy(update={"assessed_classes": [], "require_both_endpoints": False})})
    rng = np.random.default_rng(seed)
    curve_err, ratio_err, a_err, failures, n = [], [], [], 0, 0
    for i in range(n_scenes):
        sc = make_scene(width=1600, height=1000, seed=1000 + i, sag_scale=float(rng.uniform(0.35, 1.6)),
                        clutter=int(rng.integers(2, 14)))
        h, w = sc.rgb.shape[:2]
        dets = parse_yolo_seg_labels(sc.labels, w, h)
        for spec, det in zip(sc.wires, dets, strict=True):
            n += 1
            out = score_wire(det, 1, (w, h), cfg, EngineeringContext())
            if out.status != "scored":
                failures += 1
                continue
            def truth(x, s=spec):
                return s.y_attach + (s.a * np.cosh((s.x0 - s.b) / s.a) - s.a * np.cosh((x - s.b) / s.a))
            x0, x1 = out.fit["endpoints"][0][0], out.fit["endpoints"][1][0]
            length, delta, _, _ = _chord_sag(spec.a, spec.b, 0.0, x0, x1)
            curve_err.append(ev.curve_rmse_px(np.asarray(out.curve), truth))
            ratio_err.append(ev.relative_error(out.fit["sag_ratio"], delta / length))
            a_err.append(ev.relative_error(out.fit["a"], spec.a))
    return {
        "synthetic": True,
        "description": "Rendered catenary wires with exact parameters; reference masks (no segmentation error). "
                       "Measures the geometric layer only, not field accuracy.",
        "scenes": n_scenes, "wires": n, "fit_failures": failures,
        "curve_rmse_px": ev.summarize(curve_err),
        "relative_sag_ratio_error": ev.summarize(ratio_err),
        "relative_a_error": ev.summarize(a_err),
    }


# ----------------------------------------------------------------------------- segmentation


def labels_dir_for(img_dir: Path) -> Path:
    """YOLO convention: the last 'images' component of the path becomes 'labels'."""
    parts = list(img_dir.parts)
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == "images":
            parts[i] = "labels"
            return Path(*parts)
    raise ValueError(f"{img_dir} has no 'images' component")


def segmentation_eval(data_yaml: Path, split: str, degraded: bool = False, enhancement: bool = True,
                      weights: str | None = None, wirenet: str | None = "config", device: str | None = None,
                      ap_floor: float = 0.01, sweep: bool = False, imgsz: int | None = None,
                      instances: str | None = None, condition: str | None = None,
                      overrides: dict | None = None) -> dict:
    """Equations 12-14 and AP on one split, through the screener's own path: enhancement, the
    model's input size and the blur fallback (``finalize_detections``). ``degraded`` applies
    ml.degrade to every image first; ``enhancement=False`` switches the enhancement and the
    fallback off (the before/after comparison)."""
    import yaml
    from PIL import Image, ImageOps

    from app.pipeline.preprocess import preprocess
    from app.pipeline.runner import _degraded, finalize_detections
    from app.pipeline.sag_limits import EngineeringContext
    from app.pipeline.segment import parse_yolo_seg_labels
    from app.pipeline.wirenet import make_segmenter
    from ml.degrade import degrade

    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    for section, vals in (overrides or {}).items():  # e.g. {"enhancement": {...}, "segmentation": {...}}
        cfg = cfg.model_copy(update={section: getattr(cfg, section).model_copy(update=vals)})
    if not enhancement:
        cfg = cfg.model_copy(update={"enhancement": cfg.enhancement.model_copy(update={"mode": "off", "fallback": False})})
    seg_cfg = cfg.segmentation.model_copy(update={"conf_min": ap_floor, **({"weights": weights} if weights else {}),
                                                  **({} if wirenet == "config" else {"wirenet_weights": wirenet}),
                                                  **({"device": device} if device else {}),
                                                  **({"imgsz": imgsz} if imgsz else {}),
                                                  **({"wirenet_instances": instances} if instances else {})})
    if imgsz:
        cfg = cfg.model_copy(update={"segmentation": cfg.segmentation.model_copy(update={"imgsz": imgsz})})
    spec = yaml.safe_load(data_yaml.read_text())
    root = Path(spec.get("path", data_yaml.parent))
    img_dir = root / spec[split]
    lbl_dir = labels_dir_for(img_dir)
    seg = make_segmenter(seg_cfg, ROOT_DIR)
    if not seg.load():
        raise SystemExit(f"model unavailable: {seg.error}")
    import time as _time

    import cv2
    images, tolerant, fallback = [], [], {"pass": 0, "used": 0}
    t_start, n_img = _time.perf_counter(), 0
    for p in sorted(img_dir.iterdir()):
        if p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        with Image.open(p) as im:
            rgb = np.asarray(ImageOps.exif_transpose(im).convert("RGB"))
        if degraded:
            rgb, _ = degrade(rgb, p.name)
        if condition:
            from ml.stress import apply_condition
            rgb = apply_condition(rgb, condition, p.name)
        lbl = lbl_dir / f"{p.stem}.txt"
        gts = parse_yolo_seg_labels(lbl.read_text() if lbl.exists() else "", rgb.shape[1], rgb.shape[0])
        pre = preprocess(rgb, cfg.preprocessing, cfg.enhancement, cfg.segmentation.imgsz)
        first = seg.segment(pre.x_rgb, pre.letterbox)
        # the fallback decides on spans scored at the operating threshold, as the screener does
        kept = [d for d in first if d.score >= cfg.segmentation.conf_min]
        flags: list[str] = []
        # the fallback can only fire on a degraded photo; skip the (costly) span scoring otherwise
        if cfg.enhancement.fallback and _degraded(pre.quality, rgb, cfg.enhancement):
            veg, dets, _, flags = finalize_detections(rgb, pre, kept, cfg, seg, EngineeringContext())
        fallback["pass"] += "fallback_pass" in flags
        fallback["used"] += "fallback_used" in flags
        preds = (first if "fallback_used" not in flags else list(veg) + list(dets)
                 + [d for d in first if d.score < cfg.segmentation.conf_min])  # AP needs the low scores too
        n_img += 1
        images.append(([ev.Instance(d.wire_class, d.mask, d.score) for d in preds],
                       [ev.Instance(g.wire_class, g.mask) for g in gts]))
        r = max(1, int(round(0.002 * max(rgb.shape[:2]))))  # 2 px at 1024
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        widen = lambda m, k=k: cv2.dilate(m.astype(np.uint8), k).astype(bool)  # noqa: E731
        tolerant.append(([ev.Instance(d.wire_class, widen(d.mask), d.score) for d in preds],
                         [ev.Instance(g.wire_class, widen(g.mask)) for g in gts]))
    if sweep:  # threshold calibration (validation split only): mean F1 over classes with ground truth
        grid = [round(x, 2) for x in np.arange(0.05, 0.80, 0.05)]
        scores = {}
        for t in grid:
            r = ev.evaluate_segmentation(images, t)
            f1s = [v.f1 for v in r.per_class.values() if v.n_gt and v.f1 is not None]
            scores[t] = float(np.mean(f1s)) if f1s else 0.0
        return {"split": split, "f1_by_threshold": scores, "best_conf": max(scores, key=scores.get),
                "best_mean_f1": max(scores.values())}
    rep = ev.evaluate_segmentation(images, cfg.segmentation.conf_min)
    tol = ev.evaluate_segmentation(tolerant, cfg.segmentation.conf_min)
    return {"split": split, "degraded": degraded, "enhancement": cfg.enhancement.mode,
            "fallback_enabled": cfg.enhancement.fallback, "fallback": fallback,
            "n_images": rep.n_images, "conf_threshold": rep.conf_threshold, "imgsz": cfg.segmentation.imgsz,
            "ap_score_floor": ap_floor, "condition": condition,
            "seconds_per_image_incl_eval": round((_time.perf_counter() - t_start) / max(n_img, 1), 2),
            "iou_nms": cfg.segmentation.iou_nms, "map50": rep.map50, "map50_95": rep.map50_95,
            "per_class": {c: asdict(v) for c, v in rep.per_class.items()}, "meets_target": rep.targets,
            "weights": seg_cfg.weights, "wirenet": seg_cfg.wirenet_weights,
            "instances": seg_cfg.wirenet_instances if seg_cfg.wirenet_weights else "yolo-only",
            "tolerant_2px": {"note": "supplementary: masks widened by 2 px (at 1024) before IoU; not Equation 5",
                             "map50": tol.map50, "map50_95": tol.map50_95,
                             "per_class": {c: asdict(v) for c, v in tol.per_class.items()}}}


def ultralytics_eval(data_yaml: Path, split: str, weights: str, imgsz: int, degraded: bool = False) -> dict:
    """Model-level metrics from Ultralytics' validator (no WIREFRAME enhancement or fallback):
    per-class precision, recall, box AP50, mask AP50 and mask AP50-95 at NMS IoU 0.45, with
    retina masks. ``degraded`` evaluates a degraded copy of the split written by ml.degrade."""
    import tempfile

    import yaml
    from ultralytics import YOLO

    from app.core.config import CLASSES
    from ml.degrade import degrade_split

    spec = yaml.safe_load(data_yaml.read_text())
    root = Path(spec.get("path", data_yaml.parent))
    data = data_yaml
    if degraded:
        tmp = Path(tempfile.mkdtemp(prefix="wireframe-degraded-"))
        data = degrade_split(root / spec[split], labels_dir_for(root / spec[split]), tmp)
        split = "test"
    m = YOLO(weights).val(data=str(data), split=split, imgsz=imgsz, iou=0.45, conf=0.001, batch=1,
                          retina_masks=True, plots=False, verbose=False, device=None)
    per = {}
    for i, c in enumerate(CLASSES):
        try:
            bp, br, b50, b5095 = m.box.class_result(m.box.ap_class_index.tolist().index(i))
            mp, mr, m50, m5095 = m.seg.class_result(m.seg.ap_class_index.tolist().index(i))
        except ValueError:  # class absent from this split
            bp = br = b50 = b5095 = mp = mr = m50 = m5095 = None
        per[c] = {"box_precision": bp, "box_recall": br, "box_ap50": b50, "box_ap50_95": b5095, "mask_precision": mp,
                  "mask_recall": mr, "mask_ap50": m50, "mask_ap50_95": m5095}
    return {"split": split, "degraded": degraded, "weights": weights, "imgsz": imgsz, "iou_nms": 0.45,
            "retina_masks": True, "box_map50": float(m.box.map50), "mask_map50": float(m.seg.map50),
            "mask_map50_95": float(m.seg.map), "per_class": per}


# ----------------------------------------------------------------------------- reference


def _read(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def reference_eval(clutter: Path | None, severity: Path | None, priority: Path | None,
                   n_resamples: int = 10_000) -> dict:
    from sqlalchemy import select

    from app.core.database import Database
    from app.models.orm import Analysis, Detection
    from app.pipeline.mspi import SiteInput, rank_sites
    from app.services.sites import latest_per_pole

    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    db = Database(get_settings().resolved_database_url)
    out: dict[str, Any] = {}
    with db.session() as s:
        if clutter:
            rows = _read(clutter)
            pairs = [(s.get(Analysis, r["analysis_id"]), float(r["clutter_rating"])) for r in rows]
            pairs = [(a.vni, c) for a, c in pairs if a is not None and a.vni is not None]
            out["h1"] = ev.evaluate_h1(*zip(*pairs, strict=True), n_resamples=n_resamples) if len(pairs) >= 3 \
                else {"error": "fewer than 3 matched images"}
        if severity:
            got = []
            for r in _read(severity):
                d = s.scalar(select(Detection).where(Detection.analysis_id == r["analysis_id"],
                                                     Detection.wire_index == int(r["wire_index"])))
                if d is not None and d.crs is not None:
                    got.append((d.crs, float(r["severity"])))
            out["h3"] = ev.evaluate_h3(*zip(*got, strict=True), levels_cut=(cfg.crs.levels.low_below,
                                       cfg.crs.levels.high_above), n_resamples=n_resamples) if len(got) >= 3 \
                else {"error": "fewer than 3 matched wires"}
        if priority:
            ref = {r["pole_code"]: float(r["reference_rank"]) for r in _read(priority)}
            sites = [SiteInput(p.pole_code, a.vni, a.valid_crs) for p, a in latest_per_pole(s) if p.pole_code in ref]
            ranked = [r for r in rank_sites(sites, cfg.mspi.alpha, cfg.mspi.beta, cfg.mspi.gamma) if r.rank]
            if len(ranked) >= 3:
                out["h4"] = ev.evaluate_h4([r.rank for r in ranked], [ref[r.site_id] for r in ranked],
                                           n_resamples=n_resamples)
            else:
                out["h4"] = {"error": "fewer than 3 ranked poles in the priority file"}
    return {"synthetic": False, **out}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("geometry")
    g.add_argument("--scenes", type=int, default=40)
    s = sub.add_parser("segmentation")
    s.add_argument("--data", type=Path, required=True)
    s.add_argument("--split", default="test", choices=["val", "test"])
    s.add_argument("--degraded", action="store_true", help="apply ml.degrade (blur + low contrast) first")
    s.add_argument("--no-enhancement", action="store_true", help="enhancement and blur fallback off")
    s.add_argument("--weights", help="default: the configured model")
    s.add_argument("--wirenet", default="config", help="WireNet weights, 'none' for YOLO masks only, default: config")
    s.add_argument("--instances", choices=["skeleton", "yolo"], help="span source with WireNet (default: config)")
    u = sub.add_parser("ultralytics")
    u.add_argument("--data", type=Path, required=True)
    u.add_argument("--split", default="test", choices=["val", "test"])
    u.add_argument("--weights", default=str(ROOT_DIR / "models" / "wireframe-yolo11m-seg.pt"))
    u.add_argument("--imgsz", type=int, default=None, help="default: the configured segmentation.imgsz")
    u.add_argument("--degraded", action="store_true")
    u.add_argument("--name", help="report section name")
    r = sub.add_parser("reference")
    r.add_argument("--clutter", type=Path)
    r.add_argument("--severity", type=Path)
    r.add_argument("--priority", type=Path)
    r.add_argument("--resamples", type=int, default=10_000)
    a = ap.parse_args()
    if a.cmd == "geometry":
        payload, name = geometry_benchmark(a.scenes), "geometry_synthetic"
    elif a.cmd == "segmentation":
        payload = segmentation_eval(a.data, a.split, a.degraded, not a.no_enhancement, a.weights,
                                    None if a.wirenet == "none" else a.wirenet, instances=a.instances)
        name = f"segmentation_{a.split}" + ("_degraded" if a.degraded else "") + ("_raw" if a.no_enhancement else "")
    elif a.cmd == "ultralytics":
        imgsz = a.imgsz or load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml").segmentation.imgsz
        payload = ultralytics_eval(a.data, a.split, a.weights, imgsz, a.degraded)
        name = a.name or f"ultralytics_{a.split}" + ("_degraded" if a.degraded else "")
    else:
        payload, name = reference_eval(a.clutter, a.severity, a.priority, a.resamples), "reference"
    path = write_section(name, payload)
    print(json.dumps(_clean(payload), indent=2)[:4000])
    print(f"written: {path}")


if __name__ == "__main__":
    main()
