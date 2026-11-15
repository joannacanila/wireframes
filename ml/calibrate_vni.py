"""Calibrate the VNI quantiles q01 and q99 (Equation 4) and compare radius factors.

    python -m ml.calibrate_vni path/to/calibration_images [--write]

Runs Algorithm 1's grayscale path and Equation 3 on every image, reports q01/q99 for the
radius factors 0.10, 0.15 and 0.20 examined in Section 3.6.2, and with --write stores the
values for the configured radius factor in config/wireframe.yaml. Calibration images must
be kept separate from test images (Section 3.4.6).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from app.core.config import ROOT_DIR, load_analysis_config, update_config_values
from app.pipeline.preprocess import grayscale_path
from app.pipeline.vni import calibrate_quantiles, vni_raw

EXT = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--name", help="calibration-set description stored in the config")
    a = ap.parse_args()
    cfg_path = ROOT_DIR / "config" / "wireframe.yaml"
    cfg = load_analysis_config(cfg_path)
    files = sorted(p for p in a.folder.rglob("*") if p.suffix.lower() in EXT)
    if len(files) < 20:
        raise SystemExit(f"{len(files)} images found; use a calibration set of at least 20")
    raws: dict[float, list[float]] = {0.10: [], 0.15: [], 0.20: []}
    for f in files:
        with Image.open(f) as im:
            rgb = np.asarray(ImageOps.exif_transpose(im).convert("RGB"))
        g = grayscale_path(rgb, cfg.preprocessing)
        for r in raws:
            raws[r].append(vni_raw(g, r, cfg.vni.remove_mean))
    report = {str(r): dict(zip(("q01", "q99"), calibrate_quantiles(v), strict=True)) for r, v in raws.items()}
    print(json.dumps({"images": len(files), "remove_mean": cfg.vni.remove_mean, "quantiles": report}, indent=2))
    if a.write:
        q = report[str(cfg.vni.radius_factor)]
        update_config_values(cfg_path, {"vni": {
            "q01": round(q["q01"], 6), "q99": round(q["q99"], 6),
            "calibration_set": a.name or f"{a.folder.name} ({len(files)} images)"}})
        print("config updated (comments preserved)")


if __name__ == "__main__":
    main()
