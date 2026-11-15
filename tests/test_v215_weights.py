"""Model weight provisioning (app/core/weights.py): a fresh clone downloads its own weights. Offline tests: a local
HTTP server stands in for GitHub."""

from __future__ import annotations

import hashlib
import http.server
import threading
from functools import partial
from pathlib import Path

import pytest

from app.core import weights


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    """Serve two small fake weight files over HTTP and point REQUIRED at them."""
    src = tmp_path / "mirror"
    src.mkdir()
    files = {"a.pt": b"wirenet" * 1000, "b.pt": b"yolo" * 777}
    req = []
    for name, data in files.items():
        (src / name).write_bytes(data)
        req.append(weights.WeightFile(name, len(data), hashlib.sha256(data).hexdigest()))
    monkeypatch.setattr(weights, "REQUIRED", tuple(req))
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(src))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", files, src
    srv.shutdown()


def test_missing_files_are_downloaded_and_verified(tmp_path, mirror):
    url, files, _ = mirror
    models = tmp_path / "models"
    assert [w.name for w in weights.missing(models)] == ["a.pt", "b.pt"]
    assert weights.ensure(models, url) == []
    for name, data in files.items():
        assert (models / name).read_bytes() == data
    assert not list(models.glob("*.part"))
    assert weights.status() == {"a.pt": "ready", "b.pt": "ready"}


def test_present_files_are_never_overwritten(tmp_path, mirror):
    url, _, _ = mirror
    models = tmp_path / "models"
    models.mkdir()
    (models / "a.pt").write_bytes(b"my retrained model")
    assert weights.ensure(models, url) == []
    assert (models / "a.pt").read_bytes() == b"my retrained model"
    assert weights.status()["a.pt"] == "present"


def test_checksum_mismatch_leaves_nothing_behind(tmp_path, mirror):
    url, _, src = mirror
    (src / "a.pt").write_bytes(b"tampered" * 875)  # same length as nothing in particular; wrong content
    models = tmp_path / "models"
    assert weights.ensure(models, url) == ["a.pt"]
    assert not (models / "a.pt").exists() and not (models / "a.pt.part").exists()
    assert weights.status()["a.pt"].startswith("failed")
    assert (models / "b.pt").exists()  # one bad file does not stop the others


def test_network_failure_never_raises(tmp_path, mirror):
    models = tmp_path / "models"
    assert weights.ensure(models, "http://127.0.0.1:9") == ["a.pt", "b.pt"]  # nothing listens on port 9
    assert not list(models.glob("*"))


def test_auto_download_can_be_switched_off(tmp_path, mirror, monkeypatch):
    monkeypatch.setenv("WIREFRAME_AUTO_DOWNLOAD", "0")
    assert weights.ensure_in_background(tmp_path / "models") is None
    assert all(v.startswith("missing") for v in weights.status().values())


def test_shipped_checksums_match_the_local_weights():
    models = Path(__file__).resolve().parents[1] / "models"
    for w in weights.REQUIRED:
        p = models / w.name
        if not p.exists():
            pytest.skip("weights not present on this machine")
        assert p.stat().st_size == w.size
        assert hashlib.sha256(p.read_bytes()).hexdigest() == w.sha256
