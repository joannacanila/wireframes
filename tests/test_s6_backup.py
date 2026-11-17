"""S6 backup and restore (register D-13): temporary directories only, never the production data."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from scripts import backup
from tests.conftest import ADMIN, ORIGIN, upload, wait_done


@pytest.fixture
def stored(client, scene):
    aid = upload(client, scene.rgb, scene.labels)
    assert wait_done(client, aid)["status"] == "complete"
    assert client.get(f"/api/reports/{aid}/pdf").status_code == 200  # a report file on disk as well
    return client, aid


def test_backup_restore_round_trip_and_app_on_the_restored_copy(stored, tmp_path_factory, cfg):
    client, aid = stored
    data = client.state.settings.data_dir
    out = backup.create(data, tmp_path_factory.mktemp("bk"))
    m = json.loads((out / backup.MANIFEST).read_text())
    assert out.name.startswith("wireframe-backup-") and not list(out.parent.glob(".tmp-*"))
    assert m["database"]["integrity"] == "ok" and m["database"]["rows"]["analyses"] >= 1
    assert any(e["path"].startswith("uploads/") for e in m["entries"])
    v = backup.verify(out)
    assert v["ok"], {k: v[k] for k in ("mismatched", "unlisted")}

    to = tmp_path_factory.mktemp("restored") / "var"
    res = backup.restore(out, to)
    assert res["ok"] and res["database"]["rows"] == m["database"]["rows"]
    for e in m["entries"]:  # every file restored byte for byte
        assert backup._sha256(to / e["path"]) == e["sha256"]

    # the application starts on the restored directory and serves the historical inspection, its photo and its PDF
    from app.core.config import ROOT_DIR, Settings
    from app.main import build_state, create_app
    from app.pipeline.segment import YoloSegmenter
    absent = YoloSegmenter(cfg.segmentation.model_copy(update={"weights": str(to / "no-model.pt")}), ROOT_DIR)
    state = build_state(Settings(data_dir=to, start_worker=False), cfg, segmenter=absent)
    with TestClient(create_app(state), headers=ORIGIN) as c:
        assert c.post("/api/auth/login", json={"username": ADMIN[0], "password": ADMIN[1]}).status_code == 200
        r = c.get(f"/api/analysis/{aid}/result").json()
        assert r["status"] == "complete" and r["wires"]
        assert c.get(r["image"]["url"]).status_code == 200
        pdf = c.get(f"/api/reports/{aid}/pdf")
        assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def test_verify_detects_tampering_and_restore_never_overwrites(stored, tmp_path_factory):
    client, _ = stored
    out = backup.create(client.state.settings.data_dir, tmp_path_factory.mktemp("bk"))
    target = tmp_path_factory.mktemp("busy")
    (target / "keep.txt").write_text("user file")
    with pytest.raises(backup.BackupError, match="not empty"):
        backup.restore(out, target)
    assert (target / "keep.txt").read_text() == "user file"
    up = next(p for p in (out / "uploads").rglob("*") if p.is_file())
    up.write_bytes(up.read_bytes() + b"x")
    v = backup.verify(out)
    assert not v["ok"] and v["mismatched"]
    with pytest.raises(backup.BackupError, match="verification"):
        backup.restore(out, tmp_path_factory.mktemp("fresh") / "var")


def test_destination_inside_the_data_directory_is_refused(stored):
    client, _ = stored
    data = client.state.settings.data_dir
    with pytest.raises(backup.BackupError, match="outside"):
        backup.create(data, data / "backups")
