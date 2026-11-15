from __future__ import annotations

import io
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import ROOT_DIR, Settings, load_analysis_config
from app.core.state import build_state
from app.devtools.synthetic import make_scene
from app.main import create_app


@pytest.fixture(scope="session")
def cfg():
    return load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")


@pytest.fixture(scope="session")
def scene():
    return make_scene()


def jpeg_bytes(rgb, exif: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    kw = {"exif": exif} if exif else {}
    Image.fromarray(rgb).save(buf, "JPEG", quality=92, **kw)
    return buf.getvalue()


ADMIN = ("tester", "correct horse battery staple")
ORIGIN = {"origin": "http://testserver"}


@pytest.fixture
def app_state(tmp_path: Path, cfg):
    # Hermetic: never load whatever model happens to be installed in models/ (the config, and so its
    # fingerprint, stays the shipped one).
    from app.core.config import ROOT_DIR
    from app.pipeline.segment import YoloSegmenter
    absent = YoloSegmenter(cfg.segmentation.model_copy(update={"weights": str(tmp_path / "no-model.pt")}), ROOT_DIR)
    state = build_state(Settings(data_dir=tmp_path, start_worker=True), cfg, segmenter=absent)
    from scripts.create_admin import upsert_admin
    upsert_admin(state.db, *ADMIN, display_name="Test Engineer")
    return state


@pytest.fixture
def client(app_state):
    """A signed-in administrator (most tests exercise admin features)."""
    with TestClient(create_app(app_state), headers=ORIGIN) as c:
        r = c.post("/api/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]})
        assert r.status_code == 200, r.text
        c.state = app_state
        yield c


@pytest.fixture
def anon(client):
    """An anonymous field user on the same running app (no session cookie)."""
    c = TestClient(client.app, headers=ORIGIN)
    c.state = client.state
    return c


def wait_done(client, aid: str, timeout: float = 30.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = client.get(f"/api/analysis/{aid}").json()
        if s["status"] != "processing":
            return s
        time.sleep(0.05)
    raise TimeoutError(aid)


def upload(client, rgb, labels: str | None = None, **data) -> str:
    files = {"file": ("pole.jpg", jpeg_bytes(rgb), "image/jpeg")}
    if labels is not None:
        files["labels"] = ("pole.txt", labels.encode(), "text/plain")
    r = client.post("/api/analysis/upload", files=files, data={k: str(v) for k, v in data.items()})
    assert r.status_code == 202, r.text
    return r.json()["id"]
