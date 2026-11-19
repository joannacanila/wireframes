"""S16 (Part 8): HEIC / HEIF photos are accepted by content, kept upright, processed, and refused clearly when bad.

Fixtures (tests/fixtures/heic) are real HEIC files made with macOS ``sips`` from synthetic scenes with a red block in
the top-left corner, so any rotation or mirror shows: land / port (640 x 480 / 480 x 640), land_exif6 (stored sideways
with EXIF orientation 6), hires (4032 x 3024, an iPhone's native size), land.heif (same bytes, .heif name), a
truncated file, a non-image named .heic and an AVIF-branded header (a codec the decode-only build does not read).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.core.config import ROOT_DIR
from app.pipeline.acquisition import HEIF_AVAILABLE, ImageRejected, acquire, heif_brand, load_rgb

F = ROOT_DIR / "tests/fixtures/heic"
pytestmark = pytest.mark.skipif(not HEIF_AVAILABLE, reason="pi-heif not installed")


def _red_top_left(rgb: np.ndarray) -> bool:
    h, w = rgb.shape[:2]
    m = rgb[: h // 10, : w // 10].reshape(-1, 3).mean(0)
    return m[0] > 180 and m[1] < 80 and m[2] < 80


@pytest.mark.parametrize("name,size", [("land.heic", (640, 480)), ("port.heic", (480, 640)),
                                       ("land_exif6.heic", (640, 480)), ("land.heif", (640, 480))])
def test_heic_and_heif_decode_upright_by_content(cfg, name, size):
    r = acquire((F / name).read_bytes(), cfg.acquisition)
    h, w = r.image_rgb.shape[:2]
    assert (w, h) == size and r.metadata["format"] == "HEIF" and _red_top_left(r.image_rgb)
    assert r.sanitized_ext == ".png" and not r.gps  # stored as the analysed pixels, no location kept
    stored = load_rgb(r.sanitized_bytes)
    assert np.array_equal(stored, r.image_rgb)  # what is stored, shown and put in the PDF is what was analysed


def test_a_phone_sized_heic_is_downscaled_like_any_photo_not_thumbnailed(cfg):
    r = acquire((F / "hires.heic").read_bytes(), cfg.acquisition)
    h, w = r.image_rgb.shape[:2]
    cap = cfg.acquisition.max_side_px
    assert max(w, h) == cap and abs(w / h - 4032 / 3024) < 0.002 and _red_top_left(r.image_rgb)
    assert r.metadata["original_width"] == 4032 and "downscaled" in r.flags  # the full image, not an embedded thumbnail


@pytest.mark.parametrize("name,code,words", [("truncated.heic", "undecodable", "damaged"),
                                             ("fake.heic", "undecodable", "not a readable image"),
                                             ("brand_avif.avif", "unsupported_codec", "cannot read")])
def test_bad_heic_files_are_refused_with_a_clear_reason(cfg, name, code, words):
    with pytest.raises(ImageRejected) as e:
        acquire((F / name).read_bytes(), cfg.acquisition)
    assert e.value.code == code and words in str(e.value)


def test_brand_detection_reads_the_file_not_its_name():
    assert heif_brand((F / "land.heic").read_bytes()) in {"heic", "mif1", "heix"}
    assert heif_brand(b"\xff\xd8\xff\xe0" + b"\0" * 20) is None


def test_heic_upload_is_processed_end_to_end_whatever_the_client_claims(client):
    """Named photo.jpg and sent as image/jpeg: the server goes by the bytes. Labels trace the dark sagging line."""
    from tests.conftest import wait_done
    data = (F / "hires.heic").read_bytes()
    w, h = 2048, 1536  # after the long-edge cap
    xs = np.linspace(0.02, 0.98, 40)
    yc = 0.5 + 0.08 * ((xs - 0.5) ** 2 * 4 - 1)
    poly = np.concatenate([np.stack([xs, yc - 6 / h], 1), np.stack([xs[::-1], yc[::-1] + 6 / h], 1)])
    labels = "2 " + " ".join(f"{v:.6f}" for v in poly.ravel()) + "\n"
    r = client.post("/api/analysis/upload", files={"file": ("photo.jpg", data, "image/jpeg"),
                                                   "labels": ("p.txt", labels.encode(), "text/plain")})
    assert r.status_code == 202, r.text
    aid = r.json()["id"]
    assert wait_done(client, aid)["status"] == "complete"
    res = client.get(f"/api/analysis/{aid}/result").json()
    assert (res["image"]["width"], res["image"]["height"]) == (w, h)
    img = client.get(f"/api/analysis/{aid}/image")
    assert img.status_code == 200 and _red_top_left(load_rgb(img.content))
    assert any(x.get("sag_ratio") is not None for x in res["wires"])
    bad = client.post("/api/analysis/upload", files={"file": ("x.heic", (F / "truncated.heic").read_bytes(), "image/heic")})
    assert bad.status_code in (400, 415, 422) and "damaged" in bad.text


def test_the_page_accepts_heic():
    js = (ROOT_DIR / "app/web/assets/field.js").read_text()
    rules = (ROOT_DIR / "app/web/assets/upload-rules.js").read_text()
    admin = (ROOT_DIR / "app/web/assets/admin.js").read_text()
    html = (ROOT_DIR / "app/web/field.html").read_text()
    # S18: the Field Screener and the admin upload share one rule set (HEIC by extension when the MIME type is empty)
    assert '"image/heic"' in rules and "heic|heif" in rules and ".heic" in html and "HEIC" in html
    assert 'from "./upload-rules.js' in js and "checkPhoto(file, maxMb)" in js
    assert 'from "./upload-rules.js' in admin and "checkPhoto(x)" in admin and "${ACCEPT}" in admin
    assert 'x.type.startsWith("image/")' not in admin
    assert Path(ROOT_DIR / "requirements.txt").read_text().count("pi-heif") == 1
