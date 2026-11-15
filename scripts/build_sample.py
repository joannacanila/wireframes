"""Build the synthetic sample shown on the landing page and used by the console's
"Run synthetic sample" control.

    python -m scripts.build_sample

Writes app/web/assets/samples/synthetic-pole.{jpg,txt,result.json}. The result is produced by
the real pipeline (Algorithms 1-5, reference masks) and serialised with the same keys as
GET /api/analysis/{id}/result, so the landing page renders it with the console's own viewer.
The image is synthetic (app/devtools/synthetic.py) and is labelled as such on the page.
"""

from __future__ import annotations

import io
import json

from PIL import Image

from app.core.config import ROOT_DIR, load_analysis_config
from app.devtools.synthetic import make_scene
from app.pipeline.mspi import mspi
from app.pipeline.runner import run_analysis
from app.pipeline.segment import parse_yolo_seg_labels
from app.services.analysis import _jsonable

OUT = ROOT_DIR / "app" / "web" / "assets" / "samples"


def main() -> None:
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    sc = make_scene(width=1280, height=800, seed=11, sag_scale=0.62, clutter=8)
    buf = io.BytesIO()
    Image.fromarray(sc.rgb).save(buf, "JPEG", quality=86, optimize=True, progressive=True)
    jpg = buf.getvalue()
    with Image.open(io.BytesIO(jpg)) as im:  # analyse exactly the bytes that are served
        import numpy as np
        rgb = np.asarray(im.convert("RGB"))
    dets = parse_yolo_seg_labels(sc.labels, rgb.shape[1], rgb.shape[0])
    o = run_analysis(rgb, cfg, segmenter=None, detections=dets)  # type: ignore[arg-type]
    crs = o.valid_crs
    wires = [{
        "index": w.index, "class": w.wire_class, "confidence": w.confidence, "status": w.status,
        "fail_reason": w.fail_reason, "bbox": list(w.bbox), "mask_polygons": w.polygons,
        "centerline": w.centerline, "catenary_curve": w.curve, "fit": _jsonable(w.fit),
        "chord_px": w.fit.get("chord_px"), "sag_px": w.fit.get("sag_px"),
        "sag_ratio": w.fit.get("sag_ratio") if w.status == "scored" else None,
        "delta_lim": w.delta_lim, "delta_lim0": w.delta_lim0, "crs": w.crs, "level": w.level, "flags": w.flags,
    } for w in o.wires]
    worst = max((w for w in wires if w["crs"] is not None), key=lambda w: w["crs"])
    result = {
        "synthetic": True, "status": o.status, "segmentation_source": o.segmentation_source,
        "image": {"url": "/assets/samples/synthetic-pole.jpg", "width": rgb.shape[1], "height": rgb.shape[0]},
        "vni": {"raw": o.vni_raw, "value": o.vni, "band": o.vni_band, "radius_factor": cfg.vni.radius_factor},
        "summary": {"wire_count": len(wires), "scored_count": len(crs), "max_crs": max(crs),
                    "mean_crs": sum(crs) / len(crs), "level": worst["level"],
                    "max_sag_ratio": max(w["sag_ratio"] for w in wires if w["sag_ratio"] is not None)},
        "mspi": {"value": mspi(o.vni, crs, cfg.mspi.alpha, cfg.mspi.beta, cfg.mspi.gamma),
                 "weights": [cfg.mspi.alpha, cfg.mspi.beta, cfg.mspi.gamma]},
        "wires": wires, "config_fingerprint": cfg.fingerprint,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "synthetic-pole.jpg").write_bytes(jpg)
    (OUT / "synthetic-pole.txt").write_text(sc.labels)
    (OUT / "synthetic-pole.result.json").write_text(json.dumps(_jsonable(result), separators=(",", ":")))
    print({p.name: p.stat().st_size for p in OUT.iterdir()})
    print("levels:", [w["level"] for w in wires], "mspi:", round(result["mspi"]["value"], 3), "vni:", round(o.vni, 3))


if __name__ == "__main__":
    main()
