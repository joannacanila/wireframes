"""Fine-tune YOLO11-seg on the curated dataset (Section 3.6.3).

    python -m ml.train --data datasets/wireframe_seg/data.yaml --epochs 60 --patience 15 --batch 2
    python -m ml.train --data ... --model yolo11s-seg.pt                      # less memory

Device is chosen automatically: CUDA if present, else Apple Silicon (MPS), else CPU with
``--allow-cpu``. Per-device defaults (``DEVICE_DEFAULTS``) apply to any option not given:

* Apple Silicon (MPS), the team's training machine: batch 4, no dataloader workers, no image
  cache, no AMP, a checkpoint every 5 epochs. Unified memory is shared with the OS; these keep
  the run inside it. ``gc.collect()`` and ``torch.mps.empty_cache()`` run after every training
  epoch and after every validation.
* CUDA: batch 8, 8 workers, disk cache, AMP. CPU: batch 4, 2 workers, no AMP.

Every time the validation mask mAP50-95 improves, the new best.pt is stripped of its optimizer
state and written atomically to models/wireframe-yolo11m-seg.pt, so a usable model is on disk
throughout the run, not only at the end. (The file name is the one the server loads, whichever
base weights were used.)

* 640 x 640, COCO-pretrained base weights; ``overlap_mask=False``: crossing wires keep separate
  instance masks.
* Anti-overfitting recipe (``AUGMENT``, ``SCHEDULE``): mosaic 0.8 (off for the last 10 epochs),
  mixup 0.15, HSV 0.015 / 0.7 / 0.4, scale gain 0.5-1.5, horizontal flips only (no vertical flip,
  rotation, perspective or shear: all break catenary geometry or span continuity), cosine LR with
  5 warmup epochs, plus field degradations (``field_degradations``: motion / defocus / Gaussian
  blur, CLAHE, flatter contrast) through Albumentations.
* Early stopping (patience) and best.pt both follow the validation *mask* mAP50-95
  (``mask_map_trainer``), not Ultralytics' blended box/mask fitness.
* The dataset's enhancement mode (written by ``ml.curate``) is recorded in the config, so the
  server flags inference that is preprocessed differently from training.
* After training: validation at NMS IoU 0.45 (the study setting; Ultralytics' default is
  0.7), the confidence threshold that maximises mean mask F1 on the validation set, and a
  JSON validation log. The threshold is written to config/wireframe.yaml with
  ``update_config_values`` (comments kept). The test set is not touched here
  (``python -m ml.evaluate segmentation --split test``).
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import platform
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import yaml

from app.core.config import CLASSES, ROOT_DIR

# Anti-overfitting recipe for thin wires. Ultralytics argument names; values from the study brief
# plus two physical constraints (flipud, degrees) explained beside them.
AUGMENT = {
    "mosaic": 0.8,          # 4-image mosaics: more wire contexts per batch
    "close_mosaic": 10,     # last 10 epochs without mosaic: finish on whole, uncut spans
    "mixup": 0.15,
    "hsv_h": 0.015, "hsv_s": 0.7, "hsv_v": 0.4,   # dawn/overcast/noon colour and exposure
    "perspective": 0.0,     # off: a perspective warp bends and breaks long thin spans
    "shear": 0.0,           # off, for the same reason
    "scale": 0.5,           # gain 0.5-1.5: poles photographed from 5-15 m
    "translate": 0.1,
    "fliplr": 0.5,          # a mirrored span is still a valid catenary
    "flipud": 0.0,          # an upside-down catenary is an arch, which the fitter rejects
    "degrees": 0.0,         # rotation would tilt the vertical that sag is measured against
}
SCHEDULE = {"cos_lr": True, "warmup_epochs": 5, "lr0": 0.01, "lrf": 0.01, "weight_decay": 0.0005}


def field_degradations() -> list:
    """Albumentations pixel transforms for blurred, shaken and low-contrast field photos (masks are
    untouched: these change pixels, not geometry). Each image gets at most one blur."""
    import albumentations as A

    return [
        A.OneOf([
            A.MotionBlur(blur_limit=(3, 9), p=1.0),          # hand shake
            A.Defocus(radius=(1, 3), alias_blur=(0.1, 0.3), p=1.0),  # missed focus
            A.GaussianBlur(blur_limit=(3, 7), p=1.0),        # soft lens / haze
        ], p=0.25),
        A.CLAHE(clip_limit=(1.0, 3.0), tile_grid_size=(8, 8), p=0.1),
        A.RandomBrightnessContrast(brightness_limit=0.25, contrast_limit=(-0.4, 0.1), p=0.3),  # mostly flatter
    ]

MIN_USABLE_F1 = 0.05  # below this the F1-optimal threshold is noise; never write it to the config

# Defaults per device kind; explicit command-line options win.
DEVICE_DEFAULTS = {
    "mps": {"batch": 4, "workers": 0, "cache": "false", "amp": False, "save_period": 5},
    "cuda": {"batch": 8, "workers": 8, "cache": "disk", "amp": True, "save_period": 10},
    "cpu": {"batch": 4, "workers": 2, "cache": "false", "amp": False, "save_period": 5},
}


def device_kind(device: str) -> str:
    return device if device in {"mps", "cpu"} else "cuda"


def best_conf_from_curve(px: np.ndarray, f1: np.ndarray) -> tuple[float, float]:
    """px: confidence grid (K,), f1: per-class F1 curves (C, K). Returns (conf, mean F1)."""
    mean = np.asarray(f1).mean(axis=0)
    i = int(np.argmax(mean))
    return float(px[i]), float(mean[i])


def update_config(path: Path, weights: str, conf: float, trained_enhancement: str | None = None,
                  imgsz: int | None = None) -> None:
    from app.core.config import update_config_values

    seg = {"weights": weights, "conf_min": round(conf, 3), "conf_provisional": False,
           "trained_enhancement": trained_enhancement}
    if imgsz:
        seg["imgsz"] = int(imgsz)
    update_config_values(path, {"segmentation": seg})


def mask_map_trainer():
    """SegmentationTrainer whose fitness is the validation *mask* mAP50-95.

    Ultralytics' default fitness blends box and mask metrics; it drives both early stopping
    (patience) and the choice of best.pt. For a segmentation-first pipeline both should follow
    the mask quality the skeleton and catenary depend on.
    """
    import torch.distributed as dist
    from ultralytics.models.yolo.segment import SegmentationTrainer

    class MaskMapTrainer(SegmentationTrainer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # read by Ultralytics' Albumentations transform when building the training dataset;
            # model.train() rejects it as an argument, so it is set here
            self.args.augmentations = field_degradations()

        def validate(self):
            if self.ema and self.world_size > 1:
                for buffer in self.ema.ema.buffers():
                    dist.broadcast(buffer, src=0)
            metrics = self.validator(self)
            if metrics is None:
                return None, None
            metrics.pop("fitness", None)
            fitness = float(metrics["metrics/mAP50-95(M)"])
            if self.best_fitness is None or self.best_fitness < fitness:
                self.best_fitness = fitness
            return metrics, fitness

    return MaskMapTrainer


def train_kwargs(data: Path, device: str, *, epochs: int, patience: int, batch: int, workers: int,
                 cache: str, save_period: int, seed: int, project: Path, name: str, fraction: float,
                 amp: bool | None = None, imgsz: int = 640) -> dict:
    return dict(
        data=str(data), task="segment", imgsz=imgsz, epochs=epochs, patience=patience, batch=batch,
        workers=workers, cache=False if cache == "false" else cache, device=device,
        amp=DEVICE_DEFAULTS[device_kind(device)]["amp"] if amp is None else amp, seed=seed, deterministic=True, overlap_mask=False,
        save_period=save_period, project=str(project), name=name, exist_ok=True, plots=True,
        fraction=fraction, **AUGMENT, **SCHEDULE,
    )


def dataset_enhancement(data_yaml: Path) -> str | None:
    """Enhancement mode the dataset was curated with (written by ml.curate)."""
    return (yaml.safe_load(data_yaml.read_text()).get("wireframe") or {}).get("enhancement_mode")


def resolve_device(requested: str | None, allow_cpu: bool) -> str:
    import torch

    if requested:
        return requested
    if torch.cuda.is_available():
        return "0"
    if torch.backends.mps.is_available():
        return "mps"
    if allow_cpu:
        return "cpu"
    raise SystemExit("No CUDA or Apple Silicon (MPS) device found; pass --allow-cpu to train on the CPU.")


def environment(device: str) -> dict:
    import torch
    import ultralytics

    env = {"python": platform.python_version(), "torch": torch.__version__, "ultralytics": ultralytics.__version__,
           "device": device, "platform": platform.platform()}
    if device not in {"cpu", "mps"} and torch.cuda.is_available():
        i = int(device.split(",")[0])
        p = torch.cuda.get_device_properties(i)
        env.update(gpu=p.name, vram_gb=round(p.total_memory / 2**30, 1), cuda=torch.version.cuda,
                   cudnn=torch.backends.cudnn.version())
    return env


def validation_log(val, conf: float, mean_f1: float) -> dict:
    seg = val.seg
    per_class = {}
    for i, c in enumerate(CLASSES):
        try:
            p, r, ap50, ap = seg.class_result(i)
        except (IndexError, ValueError):
            p = r = ap50 = ap = None
        per_class[c] = {"precision": p, "recall": r, "ap50": ap50, "ap50_95": ap}
    return {"mask_map50": float(seg.map50), "mask_map50_95": float(seg.map), "best_conf": conf,
            "mean_f1_at_best_conf": mean_f1, "iou_nms": 0.45, "per_class": per_class,
            "note": "Validation split. Ultralytics reports per-class P/R at its own operating point; "
                    "Equations 12-14 at conf_min are computed by ml.evaluate."}


INSTALLED = ROOT_DIR / "models" / "wireframe-yolo11m-seg.pt"


def install_weights(best: Path, dest: Path = INSTALLED) -> Path:
    """Strip optimizer state (inference needs only the EMA weights) and replace ``dest``
    atomically, so the server never reads a half-written file."""
    from ultralytics.utils.torch_utils import strip_optimizer

    dest.parent.mkdir(exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    strip_optimizer(best, s=str(tmp))
    os.replace(tmp, dest)
    return dest


def release_memory(_trainer=None) -> None:
    """Return cached allocator blocks after each epoch: on Apple Silicon the GPU shares memory
    with the OS, and the MPS cache otherwise grows until the run is killed."""
    import torch

    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
    elif torch.cuda.is_available():
        torch.cuda.empty_cache()


RELEASE_EVERY = 10  # training steps between MPS cache releases


def release_memory_steps(trainer) -> None:
    """on_train_batch_end (MPS only): mask tensors change size with the number of wires per photo,
    so the MPS allocator accumulates differently sized cached blocks within an epoch and unified
    memory climbs until macOS swaps (observed: 4.4 -> 6.4 GB and 1.2 -> 12 s/step by epoch 8).
    Releasing the cache every few steps keeps it flat; the cost is negligible."""
    import torch

    trainer._wf_steps = getattr(trainer, "_wf_steps", 0) + 1
    if trainer._wf_steps % RELEASE_EVERY == 0 and torch.backends.mps.is_available():
        torch.mps.empty_cache()


def install_on_improvement(trainer) -> None:
    """on_model_save: Ultralytics has just written best.pt iff this epoch set a new best. The
    config's segmentation.imgsz follows the installed model, so the server feeds it its own size."""
    if trainer.fitness is not None and trainer.best_fitness == trainer.fitness and trainer.best.exists():
        install_weights(trainer.best)
        cfg_path = ROOT_DIR / "config" / "wireframe.yaml"
        from app.core.config import load_analysis_config, update_config_values
        if cfg_path.exists() and load_analysis_config(cfg_path).segmentation.imgsz != trainer.args.imgsz:
            update_config_values(cfg_path, {"segmentation": {"imgsz": int(trainer.args.imgsz)}})
        print(f"epoch {trainer.epoch + 1}: validation mask mAP50-95 {trainer.fitness:.4f} (new best) -> "
              f"{INSTALLED.relative_to(ROOT_DIR)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=Path("datasets/wireframe_seg/data.yaml"))
    ap.add_argument("--model", default="yolo11m-seg.pt")
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--patience", type=int, default=25,
                    help="early stop after N epochs without a better validation mask mAP50-95")
    ap.add_argument("--imgsz", type=int, default=None, help="default: the dataset's letterbox size, else 640")
    ap.add_argument("--batch", type=int, default=None, help="default per device (MPS 4, CUDA 8, CPU 4); -1 = AutoBatch")
    ap.add_argument("--workers", type=int, default=None, help="dataloader workers; default per device (MPS 0)")
    ap.add_argument("--cache", default=None, choices=["disk", "ram", "false"],
                    help="decoded-image cache; default per device (MPS: off)")
    ap.add_argument("--save-period", type=int, default=None, help="extra checkpoint every N epochs (MPS: 5)")
    ap.add_argument("--device", default=None, help="'mps', CUDA index ('0') or 'cpu'; default: auto")
    ap.add_argument("--allow-cpu", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--project", default="runs/wireframe")
    ap.add_argument("--name", default="yolo11m-seg-640")
    ap.add_argument("--fraction", type=float, default=1.0, help="share of the training split to use")
    ap.add_argument("--resume", action="store_true", help="continue from <project>/<name>/weights/last.pt")
    ap.add_argument("--no-write-config", action="store_true", help="install the weights but leave the config")
    ap.add_argument("--no-install", action="store_true",
                    help="never copy weights into models/ (experiments): the server would load them")
    a = ap.parse_args()

    from ultralytics import YOLO

    names = yaml.safe_load(a.data.read_text())["names"]
    names = list(names.values()) if isinstance(names, dict) else list(names)
    if names != list(CLASSES):
        raise SystemExit(f"dataset classes {names} != {list(CLASSES)} (order matters: it is the label index)")
    device = resolve_device(a.device, a.allow_cpu)
    # Absolute: Ultralytics 8.4 nests a relative project under <runs_dir>/<task>/.
    project = Path(a.project).resolve()
    run_dir = project / a.name
    if a.imgsz is None:
        a.imgsz = (yaml.safe_load(a.data.read_text()).get("wireframe") or {}).get("imgsz") or 640
    for k, v in DEVICE_DEFAULTS[device_kind(device)].items():
        if k != "amp" and getattr(a, k) is None:
            setattr(a, k, v)
    env = environment(device)
    print(json.dumps(env | {"batch": a.batch, "workers": a.workers, "cache": a.cache,
                            "save_period": a.save_period, "imgsz": a.imgsz}, indent=2))

    model = YOLO(str(run_dir / "weights" / "last.pt") if a.resume else a.model)
    model.add_callback("on_train_epoch_end", release_memory)
    model.add_callback("on_fit_epoch_end", release_memory)  # validation allocates too
    if device_kind(device) == "mps":
        model.add_callback("on_train_batch_end", release_memory_steps)
    if not a.no_install and a.fraction >= 1.0:
        model.add_callback("on_model_save", install_on_improvement)
    if a.resume:  # same trainer, so the mask-mAP fitness and the field degradations carry over
        model.train(resume=True, trainer=mask_map_trainer())
    else:
        model.train(trainer=mask_map_trainer(), **train_kwargs(
            a.data, device, epochs=a.epochs, patience=a.patience, batch=a.batch, workers=a.workers,
            cache=a.cache, save_period=a.save_period, seed=a.seed, project=project, name=a.name,
            fraction=a.fraction, imgsz=a.imgsz))
    release_memory()
    run_dir = Path(getattr(model.trainer, "save_dir", run_dir))  # where Ultralytics actually wrote
    best = run_dir / "weights" / "best.pt"
    val = YOLO(str(best)).val(data=str(a.data), split="val", imgsz=a.imgsz, iou=0.45, conf=0.001,
                              batch=max(1, a.batch), device=device, plots=True, retina_masks=True)
    conf, f1 = best_conf_from_curve(val.seg.px, val.seg.f1_curve)
    enh = dataset_enhancement(a.data)
    log = {"generated_at": datetime.now(UTC).isoformat(), "environment": env, "data": str(a.data),
           "weights": str(best), "augmentation": AUGMENT, "schedule": SCHEDULE | {"patience": a.patience,
           "early_stopping_metric": "validation mask mAP50-95"}, "dataset_enhancement": enh,
           **validation_log(val, conf, f1)}
    (run_dir / "wireframe_validation.json").write_text(json.dumps(log, indent=2))
    print(f"validation mask mAP50={log['mask_map50']:.4f}  mAP50-95={log['mask_map50_95']:.4f}  "
          f"best conf={conf:.3f} (mean F1 {f1:.4f})  log: {run_dir / 'wireframe_validation.json'}")

    if a.no_install or a.fraction < 1.0:
        print("not installed (--no-install or a partial-data run); the server keeps its current model")
        return
    install_weights(best)  # the final best.pt (Ultralytics' stripped copy)
    print(f"installed {INSTALLED.relative_to(ROOT_DIR)} ({INSTALLED.stat().st_size / 2**20:.1f} MB)")
    if f1 <= MIN_USABLE_F1:
        raise SystemExit(f"mean validation F1 {f1:.3f} <= {MIN_USABLE_F1}: the confidence threshold would be "
                         "noise, so conf_min was left unchanged. The weights are installed; train longer or on "
                         "more data before trusting them.")
    if not a.no_write_config:
        update_config(ROOT_DIR / "config" / "wireframe.yaml", "models/wireframe-yolo11m-seg.pt", conf, enh, a.imgsz)
        print(f"config updated: conf_min={conf:.3f}, weights=models/wireframe-yolo11m-seg.pt")


if __name__ == "__main__":  # dataloader worker processes (spawn) re-import this module
    main()
