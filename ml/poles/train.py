"""Fine-tune a pretrained YOLO11n detector for poles (v2.16). Apple-silicon settings: MPS, batch 8, 640 px, AMP off
(Ultralytics' AMP check is CUDA-only), 2 dataloader workers; resumable (Ultralytics saves last.pt every epoch).

    python -m ml.poles.train                       # train, then evaluate best.pt on val (selection) - test is separate
    python -m ml.poles.train --resume runs/detect/runs/poles/yolo11n/weights/last.pt
    python -m ml.poles.train --eval-test runs/detect/runs/poles/yolo11n/weights/best.pt   # once, after selection on val

v2.17 (LVIS telephone_pole set, ml/poles/lvis_prepare.py), selection by ml/poles/eval_detectors.py:
    python -m ml.poles.train --data datasets/external/poles_lvis/data.yaml --model yolo11s.pt --name yolo11s-lvis --epochs 60

v2.18 (more verified negatives, ml/poles/build_v218.py; small rotations because field photos are hand-held and tilted;
optional start from the Street View detector instead of COCO):
    python -m ml.poles.train --data datasets/external/poles_v218/data.yaml --name yolo11n-v218 --epochs 50 \
        --patience 15 --set degrees=5
    python -m ml.poles.train --data datasets/external/poles_v218/data.yaml --model runs/detect/runs/poles/yolo11n/weights/best.pt \
        --name yolo11n-v218-gsvinit --epochs 50 --patience 15 --set degrees=5
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def yaml_value(v: str):
    import yaml
    return yaml.safe_load(v)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="datasets/external/poles_yolo/data.yaml")
    ap.add_argument("--model", default="yolo11n.pt")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--resume", default=None)
    ap.add_argument("--eval-test", default=None)
    ap.add_argument("--name", default="yolo11n")
    ap.add_argument("--patience", type=int, default=8)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="extra Ultralytics train argument, e.g. --set degrees=5 (v2.18)")
    a = ap.parse_args()
    extra = {}
    for kv in a.set:
        k, v = kv.split("=", 1)
        extra[k] = yaml_value(v)
    import torch
    from ultralytics import YOLO
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    if a.eval_test:
        m = YOLO(a.eval_test)
        r = m.val(data=a.data, split="test", imgsz=a.imgsz, batch=a.batch, device=dev, project="runs/poles", name="test",
                  exist_ok=True, plots=True)
        out = {"split": "test", "weights": a.eval_test, "map50": float(r.box.map50), "map50_95": float(r.box.map),
               "precision": float(r.box.mp), "recall": float(r.box.mr)}
        Path("docs/thesis/results/v216_poles_test.json").write_text(json.dumps(out, indent=1))
        print(out)
        return
    if a.resume:
        YOLO(a.resume).train(resume=True)
    else:
        YOLO(a.model).train(data=a.data, epochs=a.epochs, batch=a.batch, imgsz=a.imgsz, device=dev, workers=2, amp=False,
                            project="runs/poles", name=a.name, exist_ok=True, patience=a.patience, seed=42, deterministic=True,
                            plots=True, cache=False, **extra)
    name = Path(a.resume).parents[1].name if a.resume else a.name
    best = Path(f"runs/detect/runs/poles/{name}/weights/best.pt")  # Ultralytics 8.4 prefixes runs/detect
    r = YOLO(str(best)).val(data=a.data, split="val", imgsz=a.imgsz, batch=a.batch, device=dev, project="runs/poles",
                            name=f"val-{name}", exist_ok=True)
    out = {"split": "val", "weights": str(best), "map50": float(r.box.map50), "map50_95": float(r.box.map),
           "precision": float(r.box.mp), "recall": float(r.box.mr)}
    tag = "v216_poles" if name == "yolo11n" else (f"v218_poles_{name}" if "v218" in name else f"v217_poles_{name}")
    Path(f"docs/thesis/results/{tag}_val.json").write_text(json.dumps(out, indent=1))
    print(out)


if __name__ == "__main__":
    main()
