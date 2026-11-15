"""Sections 3.3.1, 3.6.1 and 3.14.4: EXIF, envelope, GPS removal, dual-path preprocessing."""

import io

import numpy as np
import pytest
from PIL import Image
from PIL.ExifTags import IFD

from app.pipeline.acquisition import ImageRejected, acquire, file_has_gps
from app.pipeline.preprocess import preprocess


def exif_with(gps=True, focal35=35, when="2026:03:14 10:30:00", orientation=1) -> bytes:
    ex = Image.Exif()
    ex[0x0112] = orientation
    ex[0x010F] = "TestCam"
    sub = ex.get_ifd(IFD.Exif)
    sub[0xA405] = focal35
    sub[0x9003] = when
    if gps:
        g = ex.get_ifd(IFD.GPSInfo)
        g.update({1: "N", 2: (14.0, 19.0, 46.0), 3: "E", 4: (120.0, 56.0, 12.0)})
    return ex.tobytes()


def jpeg(rgb, exif=None):
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "JPEG", quality=90, **({"exif": exif} if exif else {}))
    return buf.getvalue()


@pytest.fixture
def photo():
    rng = np.random.default_rng(0)
    return rng.integers(0, 255, (480, 720, 3), dtype=np.uint8)


def test_gps_is_extracted_and_removed_from_stored_copy(cfg, photo, tmp_path):
    raw = jpeg(photo, exif_with(gps=True))
    p_in = tmp_path / "in.jpg"
    p_in.write_bytes(raw)
    assert file_has_gps(p_in)
    res = acquire(raw, cfg.acquisition)
    assert res.gps == pytest.approx((14 + 19 / 60 + 46 / 3600, 120 + 56 / 60 + 12 / 3600), abs=1e-6)
    out = tmp_path / "stored.jpg"
    out.write_bytes(res.sanitized_bytes)
    assert not file_has_gps(out)
    assert res.metadata["had_gps"] is True and "gps" not in res.metadata
    with Image.open(out) as im:  # non-GPS EXIF survives
        assert im.getexif().get(0x010F) == "TestCam"


def test_envelope_checks(cfg, photo):
    res = acquire(jpeg(photo, exif_with(focal35=18, when="2026:03:14 21:00:00")), cfg.acquisition,
                  distance_m=8, elevation_deg=75)
    status = {c.name: c.status for c in res.envelope}
    assert status == {"focal_length_35mm": "fail", "distance_m": "pass", "elevation_deg": "fail",
                      "daylight_hour": "fail"}
    assert "outside_envelope:focal_length_35mm" in res.flags
    assert res.image_rgb.shape == photo.shape  # flagged, never rejected


def test_missing_exif_marks_acquisition_unverified(cfg, photo):
    res = acquire(jpeg(photo), cfg.acquisition)
    assert "acquisition_unverified" in res.flags
    assert all(c.status == "unverified" for c in res.envelope)


def test_exif_orientation_is_applied(cfg, photo):
    res = acquire(jpeg(photo, exif_with(orientation=6, gps=False)), cfg.acquisition)
    assert res.image_rgb.shape[:2] == (720, 480)
    with Image.open(io.BytesIO(res.sanitized_bytes)) as im:
        assert im.size == (480, 720) and im.getexif().get(0x0112) == 1


@pytest.mark.parametrize("data,code", [(b"not an image", "undecodable")])
def test_objective_rejections(cfg, data, code):
    with pytest.raises(ImageRejected) as e:
        acquire(data, cfg.acquisition)
    assert e.value.code == code


def test_small_and_constant_images_rejected_when_opted_in(cfg):
    strict = cfg.acquisition.model_copy(update={"reject_low_quality": True})
    with pytest.raises(ImageRejected, match="shorter side"):
        acquire(jpeg(np.zeros((100, 100, 3), np.uint8) + 9), strict)
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (120, 120, 120)).save(buf, "PNG")
    with pytest.raises(ImageRejected) as e:
        acquire(buf.getvalue(), strict)
    assert e.value.code == "constant_image"


def test_small_and_constant_images_accepted_and_flagged_by_default(cfg):
    small = acquire(jpeg(np.zeros((100, 100, 3), np.uint8) + 9), cfg.acquisition)
    assert "resolution_too_low" in small.flags and small.image_rgb.shape == (100, 100, 3)
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (120, 120, 120)).save(buf, "PNG")
    assert "blank_image" in acquire(buf.getvalue(), cfg.acquisition).flags


def test_letterbox_rgb_path_and_inverse_mapping(cfg, photo):
    pre = preprocess(photo, cfg.preprocessing)
    lb = pre.letterbox
    assert pre.x_rgb.shape == (640, 640, 3) and pre.x_rgb.dtype == np.float32
    assert 0.0 <= pre.x_rgb.min() and pre.x_rgb.max() <= 1.0
    assert lb.new_w == 640 and lb.new_h == round(480 * 640 / 720) and lb.pad_x == 0
    assert np.allclose(pre.x_rgb[0, 0], 114 / 255)  # padding band
    m = np.zeros((640, 640), np.float32)
    m[lb.pad_y + 100 : lb.pad_y + 120, 200:260] = 1
    full = lb.mask_to_original(m) > 0.5
    ys, xs = np.nonzero(full)
    assert full.shape == (480, 720)
    assert xs.min() == pytest.approx(200 / lb.scale, abs=2) and ys.min() == pytest.approx(100 / lb.scale, abs=2)


def test_grayscale_path_is_direct_resize_with_gaussian(cfg, photo):
    pre = preprocess(photo, cfg.preprocessing)
    assert pre.gray.shape == (640, 640)
    # direct resize: no padding band, so the first row carries image content, not 114
    assert not np.allclose(pre.gray[0], 114)
    # smoothing lowers high-frequency variance of the noise image
    import cv2
    unsmoothed = cv2.resize(cv2.cvtColor(photo, cv2.COLOR_RGB2GRAY), (640, 640)).astype(np.float32)
    assert pre.gray.std() < unsmoothed.std()
