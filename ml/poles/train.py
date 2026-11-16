"""Fine-tune a pretrained YOLO11n detector for poles (v2.16). Apple-silicon settings: MPS, batch 8, 640 px, AMP off
(Ultralytics' AMP check is CUDA-only), 2 dataloader workers; resumable (Ultralytics saves last.pt every epoch).

    python -m ml.poles.train                       # train, then evaluate best.pt on val (selection) - test is separate
    python -m ml.poles.train --resume runs/detect/runs/poles/yolo11n/weights/last.pt
    python -m ml.poles.train --eval-test runs/detect/runs/poles/yolo11n/weights/best.pt   # once, after selection on val
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="datasets/external/poles_yolo/data.yaml")
    ap.add_argument("--model", default="yolo11n.pt")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--resume", default=None)
    ap.add_argument("--eval-test", default=None)
    a = ap.parse_args()
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
                            project="runs/poles", name="yolo11n", exist_ok=True, patience=8, seed=42, deterministic=True,
                            plots=True, cache=False)
    best = Path("runs/detect/runs/poles/yolo11n/weights/best.pt")  # Ultralytics 8.4 prefixes runs/detect
    r = YOLO(str(best)).val(data=a.data, split="val", imgsz=a.imgsz, batch=a.batch, device=dev, project="runs/poles",
                            name="val", exist_ok=True)
    out = {"split": "val", "weights": str(best), "map50": float(r.box.map50), "map50_95": float(r.box.map),
           "precision": float(r.box.mp), "recall": float(r.box.mr)}
    Path("docs/thesis/results/v216_poles_val.json").write_text(json.dumps(out, indent=1))
    print(out)


if __name__ == "__main__":
    main()
