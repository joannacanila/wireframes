"""Batch street audit (v2.8, Feature C): a folder of photos or a survey video -> CSV + summary.

    python -m scripts.batch_inspect path/to/folder_or_video.mp4 --out runs/audit/2026-11-15
        [--every-s 2.0] [--dedupe-hamming 6] [--review-grades C "Retake recommended"]

Each photo goes through exactly the server's analysis (acquisition checks, preprocessing,
segmentation, CRS, hazard verdict, reliability grade). Outputs in --out:

    inspection.csv      one row per photo / frame (verdict, grade, counts, worst CRS, closest
                        vegetation gap, loose-end candidates, EXIF time and GPS when present)
    summary.json / .md  executive summary: verdict and grade counts, the highest-risk items
    diagnostics/        the three-panel diagnostic figure per photo
    review_queue/       LabelMe JSON (+ image) for high-risk and low-reliability photos, with the
                        model's polygons as editable starting labels: the active-learning queue
    rejected.csv        files that could not be analysed and why (corrupt, too small, ...)

Near-duplicate frames (64-bit pHash within --dedupe-hamming) are skipped. Nothing is uploaded.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import cv2

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VID_EXT = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}
FIELDS = ["item", "source", "frame_time_s", "status", "verdict", "grade", "reliability", "power_spans", "telecom_spans",
          "abandoned_spans", "scored_spans", "max_crs", "worst_level", "vegetation_regions", "closest_vegetation_px",
          "loose_end_candidates", "vni", "exif_time", "lat", "lon", "reasons"]


def iter_inputs(src: Path, every_s: float):
    """Yields (item name, source, frame time or None, encoded bytes or None, rgb or None)."""
    files = [src] if src.is_file() else sorted(p for p in src.rglob("*") if p.is_file())
    for p in files:
        ext = p.suffix.lower()
        if ext in IMG_EXT:
            yield p.name, str(p), None, p.read_bytes(), None
        elif ext in VID_EXT:
            cap = cv2.VideoCapture(str(p))
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            step = max(1, int(round(every_s * fps)))
            i = 0
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                if i % step == 0:
                    yield f"{p.stem}_t{i / fps:07.1f}s", str(p), round(i / fps, 2), None, cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                i += 1
            cap.release()


def labelme(name: str, image_file: str, h: int, w: int, result: dict) -> dict:
    shapes = []
    for wr in result["wires"]:
        for poly in wr["mask_polygons"]:
            if len(poly) >= 3:
                shapes.append({"label": wr["class"], "points": [[float(x), float(y)] for x, y in poly],
                               "group_id": wr["index"], "shape_type": "polygon", "flags": {}})
    for v in result["vegetation"]:
        for poly in v["mask_polygons"]:
            if len(poly) >= 3:
                shapes.append({"label": "Vegetation", "points": [[float(x), float(y)] for x, y in poly],
                               "group_id": None, "shape_type": "polygon", "flags": {}})
    return {"version": "5.4.1", "flags": {"needs_review": True}, "shapes": shapes, "imagePath": image_file,
            "imageData": None, "imageHeight": h, "imageWidth": w,
            "wireframe": {"verdict": (result.get("hazard") or {}).get("verdict"),
                          "grade": (result.get("quality") or {}).get("grade")}}


def run(src: Path, out: Path, every_s: float = 2.0, hamming: int = 6, review_grades=("C", "Retake recommended"),
        review_verdicts=("AT RISK - HIGH",), cfg=None, segmenter=None) -> dict:
    from app.core.config import ROOT_DIR, load_analysis_config
    from app.pipeline.acquisition import ImageRejected, acquire
    from app.pipeline.runner import run_analysis
    from app.pipeline.wirenet import make_segmenter
    from app.services.diagnostic import outcome_to_result, render_diagnostic
    from ml.curate import hamming as ham
    from ml.curate import phash

    cfg = cfg or load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    segmenter = segmenter or make_segmenter(cfg.segmentation, ROOT_DIR)
    if not segmenter.load():
        raise SystemExit(f"model unavailable: {segmenter.error}")
    for d in ("diagnostics", "review_queue"):
        (out / d).mkdir(parents=True, exist_ok=True)
    rows, rejected, hashes, skipped = [], [], [], 0
    for name, source, t, data, rgb in iter_inputs(src, every_s):
        meta, gps = {}, None
        try:
            if data is not None:
                # an offline audit skips unusable frames into rejected.csv (the server instead accepts them
                # and reports INCONCLUSIVE); v2.10 made acceptance the default, so opt in to rejection here
                acq = acquire(data, cfg.acquisition.model_copy(update={"reject_low_quality": True}))
                rgb, meta, gps = acq.image_rgb, acq.metadata, acq.gps
            elif min(rgb.shape[:2]) < cfg.acquisition.min_side_px:
                raise ImageRejected("resolution_too_low", "frame too small")
        except ImageRejected as exc:
            rejected.append({"item": name, "source": source, "code": exc.code, "message": str(exc)})
            continue
        h = phash(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))
        if any(ham(h, o) <= hamming for o in hashes):
            skipped += 1
            continue
        hashes.append(h)
        try:
            o = run_analysis(rgb, cfg, segmenter)
        except Exception as exc:  # one bad item never stops the audit
            rejected.append({"item": name, "source": source, "code": "analysis_error", "message": repr(exc)[:200]})
            continue
        res = outcome_to_result(o)
        hz, q = res.get("hazard") or {}, res.get("quality") or {}
        crs = [w["crs"] for w in res["wires"] if w["crs"] is not None]
        worst = max((w for w in res["wires"] if w["crs"] is not None), key=lambda w: w["crs"], default=None)
        veg = (hz.get("factors") or {}).get("vegetation") or []
        cnt = Counter(w["class"] for w in res["wires"])
        row = {"item": name, "source": source, "frame_time_s": t, "status": o.status, "verdict": hz.get("verdict"),
               "grade": q.get("grade"), "reliability": q.get("reliability"),
               "power_spans": cnt.get("power-distribution-lines", 0), "telecom_spans": cnt.get("telecom-cable", 0),
               "abandoned_spans": cnt.get("abandoned-wire", 0), "scored_spans": len(crs),
               "max_crs": max(crs) if crs else None, "worst_level": worst["level"] if worst else None,
               "vegetation_regions": len(res["vegetation"]), "closest_vegetation_px": veg[0]["gap_px"] if veg else None,
               "loose_end_candidates": len((hz.get("factors") or {}).get("loose_ends") or []),
               "vni": round(o.vni, 4), "exif_time": meta.get("captured_at"),
               "lat": gps[0] if gps else None, "lon": gps[1] if gps else None,
               "reasons": " | ".join(hz.get("reasons") or [])}
        rows.append(row)
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)
        cv2.imwrite(str(out / "diagnostics" / f"{safe}.jpg"), render_diagnostic(rgb, res, 700), [cv2.IMWRITE_JPEG_QUALITY, 85])
        if row["verdict"] in review_verdicts or row["grade"] in review_grades:
            img_file = f"{safe}.jpg"
            cv2.imwrite(str(out / "review_queue" / img_file), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 95])
            (out / "review_queue" / f"{safe}.json").write_text(json.dumps(labelme(name, img_file, *rgb.shape[:2], res), indent=1))
    with open(out / "inspection.csv", "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=FIELDS)
        wr.writeheader()
        wr.writerows(rows)
    with open(out / "rejected.csv", "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=["item", "source", "code", "message"])
        wr.writeheader()
        wr.writerows(rejected)
    summary = {"generated_at": datetime.now(UTC).isoformat(), "source": str(src), "analysed": len(rows),
               "rejected": len(rejected), "duplicates_skipped": skipped,
               "verdicts": dict(Counter(r["verdict"] for r in rows)), "grades": dict(Counter(r["grade"] for r in rows)),
               "review_queue": len(list((out / "review_queue").glob("*.json"))),
               "highest_risk": sorted((r for r in rows if r["max_crs"] is not None), key=lambda r: -r["max_crs"])[:10]}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    md = io.StringIO()
    md.write(f"# Street audit summary\n\nSource: `{src}` - {summary['generated_at']}\n\n")
    md.write(f"Analysed {len(rows)}, rejected {len(rejected)}, duplicate frames skipped {skipped}, "
             f"queued for review {summary['review_queue']}.\n\n| Verdict | Photos |\n|---|---|\n")
    for k, v in sorted(summary["verdicts"].items(), key=lambda kv: str(kv[0])):
        md.write(f"| {k} | {v} |\n")
    md.write("\n| Reliability grade | Photos |\n|---|---|\n")
    for k, v in sorted(summary["grades"].items(), key=lambda kv: str(kv[0])):
        md.write(f"| {k} | {v} |\n")
    md.write("\nComputational screening only; verdicts require review by a qualified professional.\n")
    (out / "summary.md").write_text(md.getvalue())
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--every-s", type=float, default=2.0, help="video frame interval in seconds")
    ap.add_argument("--dedupe-hamming", type=int, default=6)
    a = ap.parse_args()
    s = run(a.source, a.out, a.every_s, a.dedupe_hamming)
    print(json.dumps({k: v for k, v in s.items() if k != "highest_risk"}, indent=1))


if __name__ == "__main__":
    main()
