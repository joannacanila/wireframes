"""Pure parts of the dataset pipeline (Section 3.4)."""

from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from ml.curate import (
    density_strata,
    duplicate_groups,
    grouped_stratified_split,
    hamming,
    parse_label_line,
    phash,
    run,
)
from ml.train import best_conf_from_curve


def test_phash_near_duplicates_close_and_different_images_far():
    rng = np.random.default_rng(0)
    a = cv2.GaussianBlur(rng.integers(0, 255, (240, 320), dtype=np.uint8), (9, 9), 0)
    b = cv2.resize(cv2.resize(a, (200, 150)), (320, 240))  # recompressed/rescaled copy
    c = cv2.GaussianBlur(rng.integers(0, 255, (240, 320), dtype=np.uint8), (9, 9), 0)
    assert hamming(phash(a), phash(b)) <= 6 < hamming(phash(a), phash(c))


def test_duplicate_groups_are_transitive():
    g = duplicate_groups({"a": 0b0000, "b": 0b0001, "c": 0b0011, "d": 0xFFFF}, max_dist=1)
    assert g["a"] == g["b"] == g["c"] != g["d"]


def test_label_parsing():
    assert parse_label_line("2 0.5 0.5 0.1 0.2")[1] == "box"
    assert parse_label_line("1 0.1 0.1 0.2 0.1 0.2 0.2")[1] == "polygon"
    with pytest.raises(ValueError):
        parse_label_line("1 0.1 0.1 0.2")


def test_density_tertiles():
    s, (t1, t2) = density_strata({str(i): i for i in range(9)})
    assert list(s.values()).count("Low") == 3 and list(s.values()).count("High") == 3


def test_split_ratio_and_groups_never_straddle():
    rng = np.random.default_rng(1)
    items = {f"img{i}": (f"g{i // 3}", ["Low", "Moderate", "High"][i % 3]) for i in range(900)}
    out = grouped_stratified_split(items, {"train": 0.7, "val": 0.15, "test": 0.15}, seed=3)
    share = {k: sum(v == k for v in out.values()) / 900 for k in ("train", "val", "test")}
    assert share["train"] == pytest.approx(0.7, abs=0.02) and share["test"] == pytest.approx(0.15, abs=0.02)
    by_group = {}
    for img, (g, _) in items.items():
        by_group.setdefault(g, set()).add(out[img])
    assert all(len(v) == 1 for v in by_group.values())
    _ = rng


def test_best_conf_maximises_mean_f1():
    px = np.linspace(0, 1, 11)
    f1 = np.array([np.exp(-((px - 0.4) ** 2) * 20), np.exp(-((px - 0.6) ** 2) * 20)])
    conf, _ = best_conf_from_curve(px, f1)
    assert conf == pytest.approx(0.5)


def _write_source(root: Path, name: str, names: list[str], items: list[tuple[np.ndarray, str]]):
    d = root / name
    (d / "train" / "images").mkdir(parents=True)
    (d / "train" / "labels").mkdir(parents=True)
    (d / "data.yaml").write_text(yaml.safe_dump({"names": names}))
    for i, (img, lab) in enumerate(items):
        cv2.imwrite(str(d / "train" / "images" / f"{i}.jpg"), img)
        (d / "train" / "labels" / f"{i}.txt").write_text(lab)


def test_end_to_end_curation(tmp_path):
    rng = np.random.default_rng(5)
    def img():
        return cv2.GaussianBlur(rng.integers(0, 255, (200, 300, 3), dtype=np.uint8), (15, 15), 0)
    poly = "0 0.1 0.5 0.9 0.5 0.9 0.52 0.1 0.52\n"
    base = img()
    _write_source(tmp_path / "raw", "Anomaly Detection", ["telecom_cable", "pole"],
                  [(base, poly), (base, poly), (img(), poly), (img(), "1 0.1 0.1 0.2 0.3 0.4 0.4\n")])
    _write_source(tmp_path / "raw", "wire", ["wire"], [(img(), "0 0.5 0.5 0.2 0.1\n")])  # box -> queue
    reg = yaml.safe_load((Path("ml") / "sources.yaml").read_text())
    for s in reg["sources"]:
        s["license"] = "CC BY 4.0" if s["name"] in {"Anomaly Detection", "wire"} else None
    reg["background_fraction_max"] = 0.5
    (tmp_path / "sources.yaml").write_text(yaml.safe_dump(reg))
    audit = run(tmp_path / "raw", tmp_path / "out", tmp_path / "sources.yaml", Path("ml") / "class_map.yaml")
    steps = {s["step"]: s for s in audit["steps"]}
    assert steps["1_licence"]["images"] == 5
    assert steps["3_duplicates"]["exact_removed"] == 1
    assert steps["5_labels"]["annotation_queue"] == 1
    data = yaml.safe_load((tmp_path / "out" / "data.yaml").read_text())
    assert list(data["names"].values()) == ["Vegetation", "abandoned-wire", "power-distribution-lines", "telecom-cable"]
    labels = list((tmp_path / "out" / "labels").rglob("*.txt"))
    lines = [ln for p in labels for ln in p.read_text().splitlines()]
    assert lines and all(ln.startswith("3 ") for ln in lines)  # telecom-cable is class 3; poles dropped
    assert "needs_polygons" in (tmp_path / "out" / "annotation_queue.csv").read_text()


def test_download_uses_env_key_and_skips_incomplete_sources(tmp_path, monkeypatch):
    import sys
    import types

    from ml.curate import download
    calls = []

    class V:
        def __init__(self, ws, p, v): self.key = (ws, p, v)
        def download(self, fmt, location, overwrite):
            calls.append((*self.key, fmt)); Path(location, "data.yaml").write_text("names: [wire]")

    class RF:
        def __init__(self, api_key): assert api_key == "secret"
        def workspace(self, ws): return types.SimpleNamespace(project=lambda p: types.SimpleNamespace(version=lambda v: V(ws, p, v)))

    monkeypatch.setitem(sys.modules, "roboflow", types.SimpleNamespace(Roboflow=RF))
    reg = yaml.safe_load((Path("ml") / "sources.yaml").read_text())
    by = {s["name"]: s for s in reg["sources"]}
    for s in reg["sources"]:  # isolate the two sources under test
        s["workspace"] = None
    by["Anomaly Detection"].update(workspace="owner", project="anomaly-detection", version=2, license="CC BY 4.0")
    by["Cable Detection"].update(workspace="owner", version=1, license="Proprietary")  # licence refused
    cfg = tmp_path / "s.yaml"; cfg.write_text(yaml.safe_dump(reg))
    monkeypatch.delenv("ROBOFLOW_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        download(tmp_path / "raw", cfg)
    monkeypatch.setenv("ROBOFLOW_API_KEY", "secret")
    log = {e["source"]: e for e in download(tmp_path / "raw", cfg)}
    assert calls == [("owner", "anomaly-detection", 2, "yolov11")]
    assert log["Anomaly Detection"]["status"] == "downloaded"
    assert "licence" in log["Cable Detection"]["reason"] and "missing" in log["Power Lines"]["reason"]
    cached = {e["source"]: e for e in download(tmp_path / "raw", cfg)}
    assert cached["Anomaly Detection"]["status"] == "cached"
    assert "secret" not in cfg.read_text()


def test_curation_applies_and_records_the_inference_enhancement(tmp_path):
    from app.core.config import EnhancementConfig
    rng = np.random.default_rng(9)
    big = cv2.GaussianBlur(rng.integers(0, 255, (900, 1600, 3), dtype=np.uint8), (15, 15), 0)
    _write_source(tmp_path / "raw", "Anomaly Detection", ["telecom_cable"], [(big, "0 0.1 0.5 0.9 0.5 0.9 0.52 0.1 0.52\n")])
    reg = yaml.safe_load((Path("ml") / "sources.yaml").read_text())
    for s in reg["sources"]:
        s["license"] = "CC BY 4.0" if s["name"] == "Anomaly Detection" else None
    (tmp_path / "s.yaml").write_text(yaml.safe_dump(reg))
    audit = run(tmp_path / "raw", tmp_path / "out", tmp_path / "s.yaml", Path("ml") / "class_map.yaml",
                enhancement=EnhancementConfig(mode="auto"))
    data = yaml.safe_load((tmp_path / "out" / "data.yaml").read_text())
    assert data["wireframe"] == {"enhancement_mode": "auto", "working_long_side": 640, "imgsz": None, "letterbox_fill": None}
    img = cv2.imread(str(next((tmp_path / "out" / "images").rglob("*.jpg"))))
    assert max(img.shape[:2]) == 640  # stored at the same working scale as inference
    assert audit["enhancement"]["mode"] == "auto"
