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
def shipped_cfg():
    return load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")


# S4 (register D-02..D-15): the shipped configuration has no sag reference, no conductor data and no vegetation
# verdict. The CRS, banding, corroboration and change-of-state code is kept for a future validated reference, and is
# tested with these TEST-ONLY values (the former v2.23 development placeholders). They are not engineering values
# and are never loaded by the application.
TEST_ONLY_REFERENCE = {"abandoned-wire": 0.040, "power-distribution-lines": 0.040, "telecom-cable": 0.040}
_TEST_ONLY_CONDUCTOR = {"weight_n_per_m": 2.12, "diameter_m": 0.0101, "area_m2": 6.24e-5, "elastic_modulus_pa": 7.6e10,
                        "thermal_expansion_per_c": 1.91e-5, "reference_tension_n": 2900.0}
TEST_ONLY_CONDUCTORS = {"power-distribution-lines": _TEST_ONLY_CONDUCTOR, "abandoned-wire": _TEST_ONLY_CONDUCTOR,
                        "telecom-cable": {"weight_n_per_m": 3.10, "diameter_m": 0.0200, "area_m2": 3.10e-5,
                                          "elastic_modulus_pa": 1.86e11, "thermal_expansion_per_c": 1.15e-5,
                                          "reference_tension_n": 3500.0}}


def with_test_reference(shipped, every_class: bool = False):
    """The shipped configuration plus the test-only reference. ``every_class``: the pre-v2.21 policy (Table 1.2
    cut-offs, every class compared, border spans measured); otherwise the v2.21-v2.23 policy (telecom only, bands at
    CRS 6 / 10, precision gate 100 px, NOT AT RISK with >= half assessed, vegetation verdict on)."""
    from app.core.config import Conductor
    conductors = {k: Conductor(**v) for k, v in TEST_ONLY_CONDUCTORS.items()}
    eng = shipped.engineering.model_copy(update={"conductors": conductors})
    if every_class:
        lv = shipped.crs.levels.model_copy(update={"minor_from": None, "critical_from": None})  # Table 1.2
        crs = shipped.crs.model_copy(update={"delta_lim0": dict(TEST_ONLY_REFERENCE), "assessed_classes": [],
                                             "require_both_endpoints": False, "levels": lv, "min_chord_px": 100.0})
        hz = shipped.hazard.model_copy(update={"vegetation_verdict": True, "min_assessed_share": 0.0})
    else:
        lv = shipped.crs.levels.model_copy(update={"minor_from": 6.0, "critical_from": 10.0})
        crs = shipped.crs.model_copy(update={"delta_lim0": dict(TEST_ONLY_REFERENCE), "assessed_classes": ["telecom-cable"],
                                             "levels": lv, "min_chord_px": 100.0})
        hz = shipped.hazard.model_copy(update={"vegetation_verdict": True, "min_assessed_share": 0.5})
    return shipped.model_copy(update={"crs": crs, "engineering": eng, "hazard": hz})


@pytest.fixture
def cfg(shipped_cfg, request):
    """The shipped configuration. Tests marked ``every_class_assessed`` check measurement mechanics on synthetic
    scenes with the pre-v2.21 policy; tests marked ``test_reference`` check the v2.21-v2.23 assessment mechanics.
    Both use the TEST-ONLY reference above (see with_test_reference)."""
    if request.node.get_closest_marker("every_class_assessed"):
        return with_test_reference(shipped_cfg, every_class=True)
    if request.node.get_closest_marker("test_reference"):
        return with_test_reference(shipped_cfg)
    return shipped_cfg


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


def sign_in_staff(c, st) -> None:
    """v2.20: technical result fields are served to signed-in staff only; tests that read them sign in first."""
    from scripts.create_admin import upsert_admin
    upsert_admin(st.db, *ADMIN, display_name="Test Engineer")
    r = c.post("/api/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]})
    assert r.status_code == 200, r.text
