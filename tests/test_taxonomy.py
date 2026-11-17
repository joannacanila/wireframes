"""Revised taxonomy (2026-11-14): Vegetation, abandoned-wire, power-distribution-lines, telecom-cable."""

from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml
from sqlalchemy import text

from app.core.config import CLASSES, VEGETATION, WIRE_CLASSES, VegetationConfig
from app.pipeline.runner import run_analysis, vegetation_contacts
from app.pipeline.segment import WireDetection, parse_yolo_seg_labels


def test_class_order_matches_the_dataset_export():
    assert CLASSES == ("Vegetation", "abandoned-wire", "power-distribution-lines", "telecom-cable")
    assert VEGETATION not in WIRE_CLASSES and len(WIRE_CLASSES) == 3


def test_config_carries_sag_parameters_for_wire_classes_only(cfg):
    assert set(cfg.crs.class_weights) == set(WIRE_CLASSES)
    # S4: references and conductor data exist only where verified (none); never for a non-wire class
    for block in (cfg.crs.delta_lim0, cfg.engineering.conductors):
        assert set(block) <= set(WIRE_CLASSES)


def _veg(mask):
    ys, xs = np.nonzero(mask)
    return WireDetection(VEGETATION, 0.8, mask, (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))


def test_vegetation_is_never_scored_and_flags_the_wires_it_touches(cfg, scene):
    h, w = scene.rgb.shape[:2]
    wires = parse_yolo_seg_labels(scene.labels, w, h)
    target = wires[0]  # the top distribution line
    tree = np.zeros((h, w), bool)
    ys, xs = np.nonzero(target.mask)
    x0 = int(np.median(xs))
    tree[max(0, ys.min() - 60):ys.max() + 4, x0 - 80:x0 + 80] = True  # canopy over that span

    class Empty:
        source = "model"
        def segment(self, x, lb): return []

    base = run_analysis(scene.rgb, cfg, Empty(), detections=wires)
    out = run_analysis(scene.rgb, cfg, Empty(), detections=wires + [_veg(tree)])
    assert len(out.wires) == len(wires) and all(x.wire_class != VEGETATION for x in out.wires)
    assert [x.crs for x in out.wires] == [x.crs for x in base.wires]  # CRS and numbering unchanged
    assert out.vni == base.vni
    (region,) = out.vegetation
    touched = [x for x in out.wires if "vegetation_contact" in x.flags]
    assert touched and region.touches == [x.index for x in touched]
    assert "vegetation_contact" in out.flags and "vegetation_contact" not in base.flags


def test_vegetation_alone_is_not_a_wire(cfg, scene):
    h, w = scene.rgb.shape[:2]
    m = np.zeros((h, w), bool)
    m[600:700, 100:300] = True

    class Empty:
        source = "model"
        def segment(self, x, lb): return []

    out = run_analysis(scene.rgb, cfg, Empty(), detections=[_veg(m)])
    assert out.status == "no_wires_detected" and len(out.vegetation) == 1 and out.vegetation[0].touches == []


def test_contact_margin_and_share():
    wire = np.zeros((100, 200), bool)
    wire[50:53, 10:190] = True
    near = np.zeros_like(wire)
    near[40:48, 80:120] = True  # 2 px above the wire
    vc = VegetationConfig(contact_margin_frac=0.0, min_contact_share=0.02)
    assert vegetation_contacts([wire], [near], (200, 100), vc) == [[]]  # no margin: not touching
    vc = VegetationConfig(contact_margin_frac=0.02, min_contact_share=0.02)  # 4 px margin
    assert vegetation_contacts([wire], [near], (200, 100), vc) == [[0]]
    vc = VegetationConfig(contact_margin_frac=0.02, min_contact_share=0.5)
    assert vegetation_contacts([wire], [near], (200, 100), vc) == [[]]  # too small a share of the wire


def test_result_payload_and_pdf_include_vegetation(client, scene):
    from tests.conftest import upload, wait_done
    tree = "0 0.40 0.05 0.60 0.05 0.60 0.16 0.40 0.16\n"  # a canopy over the top spans
    aid = upload(client, scene.rgb, scene.labels + tree, pole_code="VEG-1")
    assert wait_done(client, aid)["status"] == "complete"
    res = client.get(f"/api/analysis/{aid}/result").json()
    assert res["summary"]["vegetation_count"] == 1 and len(res["vegetation"]) == 1
    assert res["summary"]["vegetation_contact_wires"] == res["vegetation"][0]["touches"]
    assert all(x["class"] != VEGETATION for x in res["wires"])
    pdf = client.get(f"/api/reports/{aid}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def test_legacy_database_is_migrated(tmp_path):
    """A database created before the revision: old CHECK constraint, old class names."""
    import sqlite3

    from app.core.database import Database
    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.executescript("""
        CREATE TABLE poles (id INTEGER PRIMARY KEY, pole_code VARCHAR(64) NOT NULL UNIQUE, barangay VARCHAR(120),
            created_at DATETIME NOT NULL);
        CREATE TABLE analyses (id VARCHAR(32) PRIMARY KEY, pole_id INTEGER NOT NULL REFERENCES poles(id) ON DELETE CASCADE,
            original_filename VARCHAR(255) NOT NULL, stored_name VARCHAR(64) NOT NULL UNIQUE, label_name VARCHAR(64),
            status VARCHAR(24) NOT NULL, error_code VARCHAR(48), error_message TEXT, segmentation_source VARCHAR(16),
            width INTEGER NOT NULL, height INTEGER NOT NULL, vni_raw FLOAT, vni FLOAT, vni_band VARCHAR(16),
            metadata_json JSON NOT NULL, envelope_json JSON NOT NULL, context_json JSON NOT NULL, flags_json JSON NOT NULL,
            timings_json JSON NOT NULL, config_fingerprint VARCHAR(16) NOT NULL, submitted_via VARCHAR(8) NOT NULL,
            created_at DATETIME NOT NULL, completed_at DATETIME);
        CREATE TABLE detections (id INTEGER PRIMARY KEY, analysis_id VARCHAR(32) NOT NULL REFERENCES analyses(id)
            ON DELETE CASCADE, wire_index INTEGER NOT NULL, wire_class VARCHAR(32) NOT NULL, confidence FLOAT NOT NULL,
            status VARCHAR(16) NOT NULL, fail_reason VARCHAR(32), bbox_json JSON NOT NULL, polygons_json JSON NOT NULL,
            centerline_json JSON NOT NULL, curve_json JSON NOT NULL, fit_json JSON NOT NULL, chord_px FLOAT, sag_px FLOAT,
            sag_ratio FLOAT, delta_lim FLOAT, delta_lim0 FLOAT, crs FLOAT, level VARCHAR(8), flags_json JSON NOT NULL,
            CONSTRAINT ck_det_class CHECK (wire_class IN ('power_distribution_line', 'telecom_cable',
                'fiber_optic_drop', 'dead_wire')));
        CREATE INDEX ix_detections_analysis ON detections (analysis_id);
        INSERT INTO poles VALUES (1, 'P-1', NULL, '2026-01-01');
        INSERT INTO analyses VALUES ('a1', 1, 'x.jpg', 's1', NULL, 'complete', NULL, NULL, 'model', 10, 10, 0, 0, 'Low',
            '{}', '[]', '{}', '[]', '{}', 'f', 'admin', '2026-01-01', NULL);
    """)
    for i, c in enumerate(["fiber_optic_drop", "dead_wire", "telecom_cable", "power_distribution_line"], 1):
        con.execute("INSERT INTO detections (analysis_id, wire_index, wire_class, confidence, status, bbox_json, "
                    "polygons_json, centerline_json, curve_json, fit_json, crs, level, flags_json) "
                    "VALUES ('a1', ?, ?, 0.9, 'scored', '[]', '[]', '[]', '[]', '{}', 4.2, 'medium', '[]')", (i, c))
    con.commit()
    con.close()

    db = Database(f"sqlite:///{p}")
    db.create_all()
    db.create_all()  # idempotent
    with db.engine.connect() as c:
        rows = c.execute(text("SELECT wire_index, wire_class, crs FROM detections ORDER BY wire_index")).all()
        assert rows == [(1, "telecom-cable", 4.2), (2, "abandoned-wire", 4.2), (3, "telecom-cable", 4.2),
                        (4, "power-distribution-lines", 4.2)]
        assert c.execute(text("SELECT vegetation_json FROM analyses")).scalar() == "[]"
        assert c.execute(text("PRAGMA integrity_check")).scalar() == "ok"
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError), db.engine.begin() as c:  # the new constraint is in force
        c.execute(text("UPDATE detections SET wire_class = 'dead_wire' WHERE wire_index = 1"))
    with db.engine.begin() as c:  # cascade still works after the rebuild
        c.execute(text("DELETE FROM analyses"))
        assert c.execute(text("SELECT count(*) FROM detections")).scalar() == 0


def test_audit_counts_corrupt_and_unmapped_labels_and_reports_geometry(tmp_path):
    from ml.curate import run
    rng = np.random.default_rng(9)
    def img(h=200, w=300):
        return cv2.GaussianBlur(rng.integers(0, 255, (h, w, 3), dtype=np.uint8), (15, 15), 0)
    good = "3 0.05 0.5 0.95 0.5 0.95 0.52 0.05 0.52\n0 0.1 0.1 0.2 0.1 0.2 0.2\n"
    d = tmp_path / "raw" / "Wireframes-Prelabelled-Datasets"
    (d / "train" / "images").mkdir(parents=True)
    (d / "train" / "labels").mkdir(parents=True)
    (d / "data.yaml").write_text(yaml.safe_dump({"names": list(CLASSES) + ["Mystery"]}))
    items = [(img(), good), (img(240, 400), good), (img(), "3 0.1 0.5 0.9\n"),            # odd coordinate count
             (img(), "7 0.1 0.5 0.9 0.5 0.9 0.6\n"), (img(), "3 0.1 0.5 1.7 0.5 0.9 0.6\n"),  # bad index, > 1
             (img(), good + "4 0.1 0.1 0.2 0.1 0.2 0.2\n")]                               # unmapped name
    for i, (im, lab) in enumerate(items):
        cv2.imwrite(str(d / "train" / "images" / f"{i}.jpg"), im)
        (d / "train" / "labels" / f"{i}.txt").write_text(lab)
    reg = yaml.safe_load((Path("ml") / "sources.yaml").read_text())
    reg["sources"] = [s for s in reg["sources"] if s["name"] == "Wireframes-Prelabelled-Datasets"]
    (tmp_path / "sources.yaml").write_text(yaml.safe_dump(reg))
    audit = run(tmp_path / "raw", tmp_path / "out", tmp_path / "sources.yaml", Path("ml") / "class_map.yaml")
    labels = {s["step"]: s for s in audit["steps"]}["5_labels"]
    assert labels["corrupt_label_lines"] == 3 and labels["unmapped_labels"] == {"Mystery": 1}
    assert labels["images"] == 3  # the three corrupt images go to the annotation queue
    assert "corrupt_labels" in (tmp_path / "out" / "annotation_queue.csv").read_text()
    q = audit["label_quality"]["per_class"]
    assert q["Vegetation"] == {"instances": 3}  # counted, not judged for fragmentation
    assert q["telecom-cable"]["fragment_share"] == 0.0
    geo = audit["image_geometry"]
    assert geo["images"] == 3 and geo["distinct_sizes"] == 2 and geo["letterboxed"] == 0
