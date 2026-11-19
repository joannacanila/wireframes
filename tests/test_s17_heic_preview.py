"""S17 (Part 4): HEIC / HEIF preview before submission, through the upload's own decoder.

The preview must show exactly the pixels that will be analysed (same orientation and size cap), carry no metadata,
store nothing, and refuse damaged or unsupported files with the upload's own messages.
"""

from __future__ import annotations

import base64
import io

import numpy as np
import pytest
from PIL import Image

from app.core.config import ROOT_DIR
from app.models.orm import Analysis
from app.pipeline.acquisition import HEIF_AVAILABLE, acquire

F = ROOT_DIR / "tests/fixtures/heic"
pytestmark = pytest.mark.skipif(not HEIF_AVAILABLE, reason="pi-heif not installed")


def _decode(data_url: str) -> Image.Image:
    assert data_url.startswith("data:image/jpeg;base64,")
    return Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1])))


def _red_top_left(rgb: np.ndarray) -> bool:
    h, w = rgb.shape[:2]
    m = rgb[: h // 10, : w // 10].reshape(-1, 3).mean(0)
    return m[0] > 180 and m[1] < 80 and m[2] < 80


@pytest.mark.parametrize("name,size", [("land.heic", (640, 480)), ("port.heic", (480, 640)),
                                       ("land_exif6.heic", (640, 480)), ("land.heif", (640, 480))])
def test_heic_preview_is_upright_and_the_analysed_size(anon, name, size):
    r = anon.post("/api/analysis/preview", files={"file": (name, (F / name).read_bytes(), "image/heic")})
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["width"], d["height"]) == size and d["format"] == "HEIF"
    im = _decode(d["image"])
    assert im.size == size and _red_top_left(np.asarray(im.convert("RGB")))
    assert len(im.getexif()) == 0  # re-encoded without metadata


def test_preview_matches_what_the_upload_analyses(anon, cfg):
    data = (F / "hires.heic").read_bytes()
    d = anon.post("/api/analysis/preview", files={"file": ("photo.jpg", data, "image/jpeg")}).json()  # wrong name
    analysed = acquire(data, cfg.acquisition).image_rgb
    assert (d["width"], d["height"]) == (analysed.shape[1], analysed.shape[0])
    im = np.asarray(_decode(d["image"]).convert("RGB").resize((analysed.shape[1], analysed.shape[0])))
    assert np.abs(im.astype(int) - analysed.astype(int)).mean() < 6  # JPEG re-encode only
    assert "downscaled" in d["flags"]


@pytest.mark.parametrize("name,code", [("truncated.heic", "undecodable"), ("fake.heic", "undecodable"),
                                       ("brand_avif.avif", "unsupported_codec")])
def test_damaged_or_unsupported_files_are_refused_with_the_upload_reason(anon, name, code):
    r = anon.post("/api/analysis/preview", files={"file": (name, (F / name).read_bytes(), "image/heic")})
    assert r.status_code == 422 and r.json()["error"]["code"] == code


def test_preview_stores_nothing(client):
    with client.state.db.session() as s:
        before = s.query(Analysis).count()
    client.post("/api/analysis/preview", files={"file": ("land.heic", (F / "land.heic").read_bytes(), "image/heic")})
    with client.state.db.session() as s:
        assert s.query(Analysis).count() == before


def test_the_page_uses_the_server_preview_for_heic():
    js = (ROOT_DIR / "app/web/assets/field.js").read_text()
    assert "/api/analysis/preview" in js and "if (heic) serverPreview(file)" in js
    assert "decodedPreview || previewUrl" in js  # the processing screen shows the same decoded copy
