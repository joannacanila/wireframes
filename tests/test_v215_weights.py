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


def test_multi_part_file_in_a_subfolder_is_joined_and_verified(tmp_path, mirror, monkeypatch):
    """v2.18: files over GitHub's 100 MB limit are published as <name>.000, .001 ... and joined on download."""
    url, _, src = mirror
    data = bytes(range(256)) * 50
    (src / "hf" / "m").mkdir(parents=True)
    for i, k in enumerate(range(0, len(data), 5000)):
        (src / "hf" / "m" / f"w.bin.{i:03d}").write_bytes(data[k:k + 5000])
    w = weights.WeightFile("hf/m/w.bin", len(data), hashlib.sha256(data).hexdigest(), parts=3)
    monkeypatch.setattr(weights, "REQUIRED", (w,))
    assert weights.ensure(tmp_path / "models", url) == []
    assert (tmp_path / "models" / "hf" / "m" / "w.bin").read_bytes() == data
    assert not list((tmp_path / "models").rglob("*.part"))


def test_multi_part_with_a_missing_part_installs_nothing(tmp_path, mirror, monkeypatch):
    url, _, src = mirror
    (src / "x.bin.000").write_bytes(b"abc")
    w = weights.WeightFile("x.bin", 6, hashlib.sha256(b"abcdef").hexdigest(), parts=2)
    monkeypatch.setattr(weights, "REQUIRED", (w,))
    assert weights.ensure(tmp_path / "models", url) == ["x.bin"]
    assert not (tmp_path / "models" / "x.bin").exists()


def test_every_model_the_shipped_config_loads_is_provisioned():
    """The repository alone runs the system: each model file the shipped configuration names is downloadable."""
    import json

    from app.core.config import ROOT_DIR, load_analysis_config
    seg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml").segmentation
    names = {w.name for w in weights.REQUIRED}
    spec = json.loads((ROOT_DIR / seg.wirenet_weights).read_text())
    wanted = {Path(m["weights"]).name for m in spec["members"]} | {Path(seg.weights).name}
    for extra in (seg.strand_head, seg.pole_weights):
        if extra:
            wanted |= {Path(extra).name, Path(extra).with_suffix(".json").name} if extra == seg.strand_head \
                else {Path(extra).name}
    assert wanted <= names, wanted - names
    assert {"hf/dinov2-small/model.safetensors", "hf/upernet-convnext-tiny/model.safetensors"} <= names
