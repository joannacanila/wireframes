"""v2.10: universal image ingestion and the live-server HTTP 500 regression.

Every case goes through a multipart HTTP POST to /api/analysis/upload on the FastAPI app, the
background worker, GET /result and GET /pdf. The model is a stub that finds no wires, so the tests
need no GPU; the real models are exercised by scripts/server_check.py and the live check.
"""

from __future__ import annotations

import io
import sqlite3
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import Settings
from app.core.state import build_state
from app.main import create_app

ORIGIN = {"origin": "http://testserver"}
OK_STATUSES = {"complete", "no_wires_detected", "fit_failed"}


class NoWireSegmenter:
    """Stands in for YOLO + WireNet: loads, finds nothing, reports no wire pixels."""
    source, error, names, path = "model", None, {}, Path("stub.pt")
    last_uncertainty = {"wire_pixels": 0, "entropy": None, "disagreement": None}

    def load(self) -> bool:
        return True

    def segment(self, x, lb):
        return []


@pytest.fixture
def field(tmp_path, cfg):
    st = build_state(Settings(data_dir=tmp_path, start_worker=True), cfg, segmenter=NoWireSegmenter())
    with TestClient(create_app(st), headers=ORIGIN) as c:
        c.state = st
        yield c


def _photo(h=480, w=640, seed=0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), 190, np.uint8)
    img[: h // 2] = 225                                         # sky
    for y in range(h // 6, h // 2, max(h // 12, 2)):
        img[y: y + 2] = 40                                      # dark wires
    return np.clip(img.astype(int) + rng.integers(-6, 7, img.shape), 0, 255).astype(np.uint8)


def _bytes(img: Image.Image, fmt: str, **kw) -> bytes:
    b = io.BytesIO()
    img.save(b, fmt, **kw)
    return b.getvalue()


def _upload(c, data: bytes, name: str) -> tuple[int, dict]:
    r = c.post("/api/analysis/upload", files={"file": (name, data, "application/octet-stream")})
    return r.status_code, r.json()


def _finish(c, aid: str, timeout: float = 60.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = c.get(f"/api/analysis/{aid}").json()
        if s["status"] != "processing":
            return s
        time.sleep(0.05)
    raise TimeoutError(aid)


def _check(c, data: bytes, name: str) -> dict:
    code, body = _upload(c, data, name)
    assert code == 202, body
    s = _finish(c, body["id"])
    assert s["status"] in OK_STATUSES, s
    r = c.get(f"/api/analysis/{body['id']}/result")
    assert r.status_code == 200
    res = r.json()                                   # strict JSON parsed
    assert res["hazard"]["verdict"] in {"NOT AT RISK", "AT RISK - MODERATE", "AT RISK - HIGH",
                                        "INCONCLUSIVE - RETAKE PHOTO"}
    pdf = c.get(f"/api/reports/{body['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    return res


def _rgb():
    return Image.fromarray(_photo())


CASES = {
    "jpeg_rgb": lambda: _bytes(_rgb(), "JPEG", quality=90),
    "png_rgba": lambda: _bytes(_rgb().convert("RGBA"), "PNG"),
    "png_gray_L": lambda: _bytes(_rgb().convert("L"), "PNG"),
    "png_16bit": lambda: _bytes(Image.fromarray(np.asarray(_rgb().convert("L"), np.uint16) * 257), "PNG"),
    "jpeg_cmyk": lambda: _bytes(_rgb().convert("CMYK"), "JPEG"),
    "gif_palette_transparent": lambda: _bytes(_rgb().convert("P"), "GIF", transparency=0),
    "bmp": lambda: _bytes(_rgb(), "BMP"),
    "webp": lambda: _bytes(_rgb(), "WEBP"),
    "tiff": lambda: _bytes(_rgb(), "TIFF"),
}


@pytest.mark.parametrize("case", list(CASES))
def test_every_format_and_mode_is_accepted_and_reported(field, case):
    res = _check(field, CASES[case](), f"{case}.img")
    assert res["acquisition"]["metadata"]["width"] == 640 and res["acquisition"]["metadata"]["height"] == 480


def test_exif_rotated_phone_photo_is_stored_upright(field):
    img = _rgb()
    exif = Image.Exif()
    exif[0x0112] = 6                                   # rotate 90 deg CW on display
    res = _check(field, _bytes(img, "JPEG", exif=exif.tobytes()), "phone.jpg")
    md = res["acquisition"]["metadata"]
    assert (md["width"], md["height"]) == (480, 640)


def test_tiny_thumbnail_is_accepted_and_inconclusive(field):
    res = _check(field, _bytes(Image.fromarray(_photo(64, 64)), "JPEG"), "thumb.jpg")
    assert res["hazard"]["verdict"] == "INCONCLUSIVE - RETAKE PHOTO"
    assert "resolution too low" in res["quality"]["reason"].lower()
    assert "resolution_too_low" in res["flags"]


def test_blank_image_is_accepted_and_inconclusive(field):
    res = _check(field, _bytes(Image.new("RGB", (800, 600), (128, 128, 128)), "PNG"), "blank.png")
    assert res["hazard"]["verdict"] == "INCONCLUSIVE - RETAKE PHOTO"
    assert "blank" in res["quality"]["reason"].lower() and "blank_image" in res["flags"]


def test_huge_rgba_photo_is_downscaled_not_rejected(field):
    big = Image.fromarray(_photo(3000, 4000)).convert("RGBA")
    data = _bytes(big, "PNG", compress_level=1)
    res = _check(field, data, "huge.png")
    md = res["acquisition"]["metadata"]
    assert max(md["width"], md["height"]) == 2048 and (md["original_width"], md["original_height"]) == (4000, 3000)
    assert "downscaled" in res["flags"]


def test_not_an_image_is_a_clean_422(field):
    code, body = _upload(field, b"%PDF-1.4 definitely not a photo", "doc.jpg")
    assert code == 422 and body["error"]["code"] == "undecodable"


# analyses as created before the dual portals (copied from the live database that returned HTTP 500)
LEGACY_ANALYSES_DDL = """CREATE TABLE analyses (
    id VARCHAR(32) NOT NULL, pole_id INTEGER NOT NULL, original_filename VARCHAR(255) NOT NULL,
    stored_name VARCHAR(64) NOT NULL, label_name VARCHAR(64), status VARCHAR(24) NOT NULL, error_code VARCHAR(48),
    error_message TEXT, segmentation_source VARCHAR(16), width INTEGER NOT NULL, height INTEGER NOT NULL,
    vni_raw FLOAT, vni FLOAT, vni_band VARCHAR(16), metadata_json JSON NOT NULL, envelope_json JSON NOT NULL,
    context_json JSON NOT NULL, flags_json JSON NOT NULL, timings_json JSON NOT NULL,
    config_fingerprint VARCHAR(16) NOT NULL, created_at DATETIME NOT NULL, completed_at DATETIME,
    vegetation_json JSON NOT NULL DEFAULT '[]', PRIMARY KEY (id),
    CONSTRAINT ck_analysis_status CHECK (status IN ('processing', 'complete', 'no_wires_detected', 'fit_failed', 'error')),
    FOREIGN KEY(pole_id) REFERENCES poles (id) ON DELETE CASCADE, UNIQUE (stored_name));"""


def test_upload_works_on_a_database_created_before_submitted_via(tmp_path, cfg):
    """The live HTTP 500: a pre-dual-portal database lacked analyses.submitted_via."""
    st = build_state(Settings(data_dir=tmp_path, start_worker=False), cfg, segmenter=NoWireSegmenter())
    db = tmp_path / "wireframe.db"
    st.db.engine.dispose()
    con = sqlite3.connect(db)
    con.executescript("PRAGMA foreign_keys=OFF; DROP TABLE analyses;" + LEGACY_ANALYSES_DDL)
    con.commit()
    assert "submitted_via" not in {r[1] for r in con.execute("PRAGMA table_info(analyses)")}
    con.close()
    st2 = build_state(Settings(data_dir=tmp_path, start_worker=True), cfg, segmenter=NoWireSegmenter())
    with TestClient(create_app(st2), headers=ORIGIN) as c:
        _check(c, _bytes(_rgb(), "JPEG"), "pole.jpg")
    con = sqlite3.connect(db)
    assert con.execute("SELECT submitted_via FROM analyses").fetchone()[0] in {"field", "admin"}
    con.close()


def test_jsonable_handles_every_numpy_and_non_finite_type():
    import json
    from fractions import Fraction

    from app.services.analysis import _jsonable
    v = {"a": np.float32(1.5), "b": np.int64(3), "c": np.bool_(True), "d": float("nan"), "e": np.inf,
         "f": np.arange(3), np.int64(7): Fraction(1, 4), "g": [np.float64(-np.inf), (np.uint8(2),)], "h": b"x"}
    out = _jsonable(v)
    json.dumps(out, allow_nan=False)
    assert out == {"a": 1.5, "b": 3, "c": True, "d": None, "e": None, "f": [0, 1, 2], "7": 0.25,
                   "g": [None, [2]], "h": "x"}


def test_jpeg_gps_is_stripped_without_reencoding(cfg):
    from PIL.ExifTags import IFD

    from app.pipeline.acquisition import acquire, file_has_gps, strip_jpeg_metadata
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"
    gps = exif.get_ifd(IFD.GPSInfo)
    gps.update({1: "N", 2: (14.0, 35.0, 0.0), 3: "E", 4: (121.0, 0.0, 0.0)})
    img = Image.fromarray(_photo())
    b = io.BytesIO()
    img.save(b, "JPEG", quality=90, exif=exif.tobytes(), xmp=b"<x:xmpmeta>exif:GPSLatitude=14</x:xmpmeta>")
    raw = b.getvalue()
    assert b"GPS" in raw
    acq = acquire(raw, cfg.acquisition)
    assert acq.gps is not None and b"GPS" not in acq.sanitized_bytes            # coordinates kept apart, not in file
    stored = np.asarray(Image.open(io.BytesIO(acq.sanitized_bytes)).convert("RGB"))
    assert np.array_equal(stored, np.asarray(Image.open(io.BytesIO(raw)).convert("RGB")))   # no re-encode
    assert strip_jpeg_metadata(b"not a jpeg") is None and strip_jpeg_metadata(b"\xff\xd8\xff\xe1\x00") is None
    _ = file_has_gps
