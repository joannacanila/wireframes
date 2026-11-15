"""Adaptive enhancement (segmentation path only) and the training recipe."""

import types
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from app.core.config import EnhancementConfig
from app.pipeline import enhance as E
from app.pipeline.preprocess import preprocess
from app.pipeline.runner import run_analysis
from app.pipeline.segment import parse_yolo_seg_labels
from app.pipeline.vni import compute_vni


@pytest.fixture
def ecfg():
    return EnhancementConfig()


def scene_rgb(seed=0, h=480, w=640):
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), 150, np.uint8)
    for _ in range(12):
        x, y = int(rng.integers(0, w - 80)), int(rng.integers(h // 2, h - 40))
        img[y:, x:x + 60] = rng.integers(60, 200, 3)
    for k in range(4):
        cv2.line(img, (0, 80 + 30 * k), (w, 100 + 30 * k), (25, 25, 25), 2)
    return img


def test_off_mode_is_identity(ecfg):
    img = scene_rgb()
    out = E.enhance(img, ecfg.model_copy(update={"mode": "off"}))
    assert out.rgb is img and out.applied == []


def test_noise_estimator_recovers_sigma():
    rng = np.random.default_rng(1)
    flat = np.full((400, 400), 128.0) + rng.normal(0, 10, (400, 400))
    assert E.noise_sigma(np.clip(flat, 0, 255)) == pytest.approx(10, rel=0.08)


def test_clean_well_exposed_photo_is_left_alone(ecfg):
    assert E.decide(E.measure(scene_rgb()), ecfg) == []


def _wire_separation(img):
    lab_l = cv2.cvtColor(img, cv2.COLOR_RGB2LAB)[..., 0].astype(float)
    pts = [(int(round(80 + 40 * k + 20 * x / 640)), x) for k in range(4) for x in range(20, 620, 20)]
    return float(np.mean([lab_l[y - 6, x] - lab_l[y, x] for y, x in pts]))


def test_dusk_photo_gets_clahe_brighter_and_better_separated_wires(ecfg):
    """At clip limit 2.0 the gain is deliberately modest (the limit stops CLAHE from amplifying
    flat sky); whether it helps segmentation is an A/B question for the validation set."""
    img = np.full((480, 640, 3), 34, np.uint8)  # dusk / overcast sky
    for k in range(4):
        cv2.line(img, (0, 80 + 40 * k), (640, 100 + 40 * k), (24, 24, 24), 2)  # dark, barely separated wire
    img[300:, 100:220] = 46
    out = E.enhance(img, ecfg)
    assert "clahe" in out.applied
    assert E.measure(out.rgb).mean_l > E.measure(img).mean_l
    assert _wire_separation(out.rgb) > _wire_separation(img)


def test_noisy_photo_is_denoised(ecfg):
    rng = np.random.default_rng(2)
    noisy = np.clip(scene_rgb().astype(np.float32) + rng.normal(0, 14, (480, 640, 3)), 0, 255).astype(np.uint8)
    out = E.enhance(noisy, ecfg)
    assert out.applied[0] == "denoise"  # always first: contrast and sharpening would amplify noise
    assert E.measure(out.rgb).noise_sigma < 0.6 * E.measure(noisy).noise_sigma


def test_soft_photo_is_sharpened(ecfg):
    soft = cv2.GaussianBlur(scene_rgb(), (0, 0), 2.5)
    out = E.enhance(soft, ecfg)
    assert "unsharp" in out.applied
    assert E.measure(out.rgb).sharpness > E.measure(soft).sharpness


def test_vni_path_is_never_enhanced(cfg):
    """Section 3.6.2: the grayscale path must be identical with enhancement on or off."""
    rng = np.random.default_rng(3)
    img = np.clip(scene_rgb(h=900, w=1600).astype(np.float32) * 0.3 + rng.normal(0, 12, (900, 1600, 3)), 0, 255).astype(np.uint8)
    plain = preprocess(img, cfg.preprocessing)
    always = preprocess(img, cfg.preprocessing, cfg.enhancement.model_copy(update={"mode": "always"}))
    assert always.enhancement == ["denoise", "clahe", "unsharp"]
    np.testing.assert_array_equal(plain.gray, always.gray)
    assert compute_vni(plain.gray, cfg.vni) == compute_vni(always.gray, cfg.vni)
    assert plain.letterbox == always.letterbox  # masks still map to original coordinates
    assert not np.array_equal(plain.x_rgb, always.x_rgb)  # the model input did change


def test_runner_flags_enhancement_only_on_the_model_path(cfg, scene):
    class Empty:
        source = "model"
        def segment(self, x, lb): return []
    dark = (scene.rgb.astype(np.float32) * 0.25).astype(np.uint8)
    on_model = run_analysis(dark, cfg, Empty())
    assert "enhanced:clahe" in on_model.flags and on_model.input_quality["mean_l"] < 35
    h, w = dark.shape[:2]
    on_ref = run_analysis(dark, cfg, Empty(), detections=parse_yolo_seg_labels(scene.labels, w, h))
    assert not any(f.startswith("enhanced:") for f in on_ref.flags) and on_ref.enhancement is None
    mismatch = cfg.model_copy(update={"segmentation": cfg.segmentation.model_copy(update={"trained_enhancement": "off"})})
    assert "enhancement_mismatch" in run_analysis(scene.rgb, mismatch, Empty()).flags


# ----------------------------------------------------------------------------- training recipe

def test_training_recipe_values():
    from ml.train import AUGMENT, SCHEDULE, train_kwargs
    assert (AUGMENT["mosaic"], AUGMENT["mixup"]) == (0.8, 0.15)
    assert (AUGMENT["hsv_h"], AUGMENT["hsv_s"], AUGMENT["hsv_v"]) == (0.015, 0.7, 0.4)
    assert AUGMENT["perspective"] == 0.0 and AUGMENT["shear"] == 0.0 and AUGMENT["scale"] == 0.5  # gain 0.5-1.5
    assert AUGMENT["flipud"] == 0.0 and AUGMENT["degrees"] == 0.0
    assert SCHEDULE["cos_lr"] is True and SCHEDULE["warmup_epochs"] == 5
    kw = train_kwargs(Path("d.yaml"), "0", epochs=200, patience=25, batch=8, workers=8, cache="disk",
                      save_period=10, seed=42, project=Path("/tmp/r"), name="n", fraction=1.0)
    assert kw["imgsz"] == 640 and kw["patience"] == 25 and kw["amp"] is True and kw["overlap_mask"] is False


def test_trainer_fitness_is_mask_map50_95():
    pytest.importorskip("ultralytics")
    from ml.train import mask_map_trainer
    trainer = object.__new__(mask_map_trainer())
    metrics = {"metrics/mAP50-95(B)": 0.61, "metrics/mAP50-95(M)": 0.42, "fitness": 0.55}
    trainer.ema, trainer.world_size, trainer.best_fitness = None, 1, None
    trainer.validator = lambda _self: dict(metrics)
    got, fitness = trainer.validate()
    assert fitness == 0.42 and trainer.best_fitness == 0.42 and "fitness" not in got
    trainer.validator = lambda _self: {**metrics, "metrics/mAP50-95(M)": 0.30}
    trainer.validate()
    assert trainer.best_fitness == 0.42  # best.pt follows the best mask mAP50-95


def test_training_carries_the_dataset_enhancement_into_the_config(tmp_path):
    from ml.train import dataset_enhancement, update_config
    data = tmp_path / "data.yaml"
    data.write_text(yaml.safe_dump({"names": {}, "wireframe": {"enhancement_mode": "auto"}}))
    assert dataset_enhancement(data) == "auto"
    cfg_path = tmp_path / "wireframe.yaml"
    cfg_path.write_text((Path("config") / "wireframe.yaml").read_text())
    update_config(cfg_path, "models/x.pt", 0.31, "auto")
    seg = yaml.safe_load(cfg_path.read_text())["segmentation"]
    assert seg["trained_enhancement"] == "auto" and seg["conf_min"] == 0.31 and seg["conf_provisional"] is False
    _ = types  # noqa


def test_apple_silicon_defaults():
    from ml.train import DEVICE_DEFAULTS, train_kwargs
    assert DEVICE_DEFAULTS["mps"] == {"batch": 4, "workers": 0, "cache": "false", "amp": False, "save_period": 5}
    kw = train_kwargs(Path("d.yaml"), "mps", epochs=30, patience=25, batch=4, workers=0, cache="false",
                      save_period=5, seed=42, project=Path("/tmp/r"), name="n", fraction=1.0)
    assert kw["amp"] is False and kw["cache"] is False and kw["workers"] == 0 and kw["imgsz"] == 640


def test_best_weights_are_installed_whenever_validation_improves(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    pytest.importorskip("ultralytics")
    from ultralytics.nn.tasks import SegmentationModel

    import ml.train as T
    best = tmp_path / "best.pt"
    torch.save({"model": SegmentationModel("yolo11n-seg.yaml", nc=4, verbose=False), "optimizer": {"x": 1},
                "ema": None, "epoch": 3}, best)
    dest = tmp_path / "models" / "wireframe-yolo11m-seg.pt"
    monkeypatch.setattr(T, "INSTALLED", dest)
    monkeypatch.setattr(T, "install_weights", lambda b, d=dest, f=T.install_weights: f(b, d))
    monkeypatch.setattr(T, "ROOT_DIR", tmp_path)
    tr = types.SimpleNamespace(best=best, epoch=3, fitness=0.20, best_fitness=0.20)
    T.install_on_improvement(tr)
    assert dest.exists() and torch.load(dest, weights_only=False)["optimizer"] is None  # stripped, loadable
    first = dest.stat().st_mtime_ns
    tr.fitness = 0.10  # worse epoch: best.pt was not rewritten, nothing is copied
    T.install_on_improvement(tr)
    assert dest.stat().st_mtime_ns == first and not list(dest.parent.glob("*.tmp"))
    T.release_memory()  # safe with or without an accelerator
