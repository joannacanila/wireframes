"""Blur / low-contrast resilience (v2.7): adaptive unsharp, fallback pass, 1024 letterbox curation."""

from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from app.pipeline import enhance as E
from app.pipeline.preprocess import preprocess
from app.pipeline.runner import merge_detections, run_analysis
from app.pipeline.segment import WireDetection, _bbox, parse_yolo_seg_labels
from app.pipeline.vni import compute_vni


def test_unsharp_amount_grows_with_blur(cfg):
    e = cfg.enhancement
    q = lambda s: E.Quality(50, 20, 0, 1, s)  # noqa: E731
    assert E.unsharp_amount_for(q(e.blur_laplacian_max), e) == pytest.approx(e.unsharp_amount)
    assert E.unsharp_amount_for(q(0.0), e) == pytest.approx(e.unsharp_amount_max)
    assert e.unsharp_amount < E.unsharp_amount_for(q(e.blur_laplacian_max / 2), e) < e.unsharp_amount_max
    assert E.unsharp_amount_for(q(10 * e.blur_laplacian_max), e) == pytest.approx(e.unsharp_amount)  # clamped


@pytest.mark.parametrize("sigma", [2.0, 3.0, 5.0])
def test_every_blurred_photo_is_sharpened(cfg, scene, sigma):
    """Regression: with a fixed 1 px radius and threshold 4, a strongly blurred photo had no
    detail above the threshold and was returned unchanged (gain 1.0 at sigma 5)."""
    img = cv2.GaussianBlur(scene.rgb, (0, 0), sigma)
    out = E.enhance(img, cfg.enhancement)
    assert "unsharp" in out.applied
    assert E.measure(out.rgb).sharpness > 1.5 * E.measure(img).sharpness


def test_model_input_size_does_not_change_the_vni(cfg, scene):
    a = preprocess(scene.rgb, cfg.preprocessing, cfg.enhancement, model_size=640)
    b = preprocess(scene.rgb, cfg.preprocessing, cfg.enhancement, model_size=1024)
    assert a.x_rgb.shape == (640, 640, 3) and b.x_rgb.shape == (1024, 1024, 3) and b.letterbox.size == 1024
    np.testing.assert_array_equal(a.gray, b.gray)
    assert compute_vni(a.gray, cfg.vni) == compute_vni(b.gray, cfg.vni)
    strong = preprocess(scene.rgb, cfg.preprocessing, cfg.enhancement, model_size=1024, strong=True)
    np.testing.assert_array_equal(strong.gray, a.gray)  # the fallback profile never reaches the VNI


def _det(cls, mask, score):
    return WireDetection(cls, score, mask, _bbox(mask))


def test_merge_keeps_the_higher_confidence_of_overlapping_masks():
    m = np.zeros((50, 100), bool)
    m[20:26, 5:95] = True  # shifted by 1 row below: IoU 5/7 > 0.5
    shifted = np.roll(m, 1, axis=0)
    other = np.zeros_like(m)
    other[40:43, 5:95] = True
    out = merge_detections([_det("telecom-cable", m, 0.4), _det("telecom-cable", shifted, 0.7),
                            _det("power-distribution-lines", m, 0.3), _det("telecom-cable", other, 0.2)])
    assert sorted((d.wire_class, d.score) for d in out) == [
        ("power-distribution-lines", 0.3), ("telecom-cable", 0.2), ("telecom-cable", 0.7)]


class _Recorder:
    """Finds nothing on the first pass; on enhanced/mirrored passes returns the reference spans."""
    source = "model"

    def __init__(self, dets, rgb):
        self.dets, self.calls, self.rgb = dets, 0, rgb

    def segment(self, x, lb):
        self.calls += 1
        if self.calls == 1:
            return []
        if self.calls == 3:  # the mirrored pass: return mirrored masks, the runner flips them back
            return [_det(d.wire_class, np.ascontiguousarray(d.mask[:, ::-1]), d.score) for d in self.dets]
        return self.dets


@pytest.mark.test_reference
def test_blur_fallback_recovers_spans_on_a_degraded_photo(cfg, scene):
    h, w = scene.rgb.shape[:2]
    ref = [WireDetection(d.wire_class, 0.6, d.mask, d.bbox) for d in parse_yolo_seg_labels(scene.labels, w, h)]
    blurred = cv2.GaussianBlur(scene.rgb, (0, 0), 6)
    seg = _Recorder(ref, blurred)
    out = run_analysis(blurred, cfg, seg)
    assert seg.calls == 3 and {"fallback_pass", "fallback_used"} <= set(out.flags)
    assert len(out.wires) == len(ref) and out.status == "complete"  # mirrored duplicates merged away
    crisp = _Recorder(ref, scene.rgb)
    run_analysis(scene.rgb, cfg, crisp)
    assert crisp.calls == 1  # a sharp, well-exposed photo never triggers the fallback


def test_fallback_is_off_for_reference_masks_and_when_disabled(cfg, scene):
    h, w = scene.rgb.shape[:2]
    blurred = cv2.GaussianBlur(scene.rgb, (0, 0), 6)
    seg = _Recorder([], blurred)
    off = cfg.model_copy(update={"enhancement": cfg.enhancement.model_copy(update={"fallback": False})})
    assert "fallback_pass" not in run_analysis(blurred, off, seg).flags and seg.calls == 1
    out = run_analysis(blurred, cfg, seg, detections=parse_yolo_seg_labels(scene.labels, w, h))
    assert "fallback_pass" not in out.flags


def test_letterbox_coords_round_trip():
    from ml.curate import letterbox_coords, letterbox_image
    img = np.zeros((300, 900, 3), np.uint8)
    cv2.line(img, (90, 150), (810, 150), (255, 255, 255), 3)
    poly = [0.1, 0.49, 0.9, 0.49, 0.9, 0.51, 0.1, 0.51]
    boxed = letterbox_image(img, 1024, 114)
    out = np.array(letterbox_coords(poly, 900, 300, 1024)).reshape(-1, 2) * 1024
    assert boxed.shape == (1024, 1024, 3) and boxed[0, 0, 0] == 114  # padded top band
    region = np.zeros((1024, 1024), np.uint8)
    cv2.fillPoly(region, [np.round(out).astype(np.int32)], 1)
    line = boxed[..., 0] > 128
    inside = region.astype(bool)
    assert (line & ~inside).sum() < 0.05 * line.sum() and line[inside].mean() > 0.4  # polygon sits on the line


def test_external_sources_never_reach_val_or_test(tmp_path):
    from ml.curate import run
    rng = np.random.default_rng(3)
    good = "3 0.05 0.5 0.95 0.5 0.95 0.52 0.05 0.52\n"
    def write(name, names, n, h=200, w=300):
        d = tmp_path / "raw" / name
        (d / "train" / "images").mkdir(parents=True)
        (d / "train" / "labels").mkdir(parents=True)
        (d / "data.yaml").write_text(yaml.safe_dump({"names": names}))
        for i in range(n):
            im = cv2.GaussianBlur(rng.integers(0, 255, (h, w, 3), dtype=np.uint8), (9, 9), 0)
            cv2.imwrite(str(d / "train" / "images" / f"{i}.jpg"), im)
            (d / "train" / "labels" / f"{i}.txt").write_text(good)
    write("Wireframes-Prelabelled-Datasets", ["Vegetation", "abandoned-wire", "power-distribution-lines",
                                              "telecom-cable"], 20)
    write("Extra", ["x", "y", "z", "telecom_cable"], 12, 240, 320)
    reg = yaml.safe_load((Path("ml") / "sources.yaml").read_text())
    reg["sources"] = [s for s in reg["sources"] if s["name"] == "Wireframes-Prelabelled-Datasets"] + [
        {"name": "Extra", "license": "CC BY 4.0"}]
    (tmp_path / "s.yaml").write_text(yaml.safe_dump(reg))
    audit = run(tmp_path / "raw", tmp_path / "out", tmp_path / "s.yaml", Path("ml") / "class_map.yaml", imgsz=1024)
    assert audit["split_isolation"] == {"train_only_images": 12, "train_only_in_val_or_test": 0}
    for split in ("val", "test"):
        assert not list((tmp_path / "out" / "images" / split).glob("extra__*"))
    imgs = list((tmp_path / "out" / "images").rglob("*.jpg"))
    assert imgs and all(cv2.imread(str(p)).shape == (1024, 1024, 3) for p in imgs)
    data = yaml.safe_load((tmp_path / "out" / "data.yaml").read_text())
    assert data["wireframe"]["imgsz"] == 1024


def test_field_degradations_are_pixel_only():
    pytest.importorskip("albumentations")
    import albumentations as A

    from ml.train import field_degradations
    t = field_degradations()
    names = {type(x).__name__ for x in t} | {type(y).__name__ for x in t if isinstance(x, A.OneOf) for y in x.transforms}
    assert {"MotionBlur", "Defocus", "GaussianBlur", "CLAHE", "RandomBrightnessContrast"} <= names
    assert not any(isinstance(x, A.DualTransform) for x in t)  # geometry untouched, masks stay valid


def test_degradation_is_deterministic_and_degrades(scene):
    from ml.degrade import degrade
    a, info = degrade(scene.rgb, "pole_001.jpg")
    b, _ = degrade(scene.rgb, "pole_001.jpg")
    np.testing.assert_array_equal(a, b)
    assert info["blur"] in {"motion", "defocus", "gaussian"} and 0.5 <= info["contrast_factor"] <= 0.65
    assert E.measure(a).sharpness < 0.5 * E.measure(scene.rgb).sharpness
    assert E.measure(a).contrast < 0.7 * E.measure(scene.rgb).contrast
    kinds = {degrade(scene.rgb[:200, :300], f"{i}.jpg")[1]["blur"] for i in range(30)}
    assert kinds == {"motion", "defocus", "gaussian"}
