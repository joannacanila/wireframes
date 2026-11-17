"""v2.18: pole detector loading / failure, evaluation protocols (all boxes vs COCO medium+large), pole localisation
for the wire pipeline, and the CVAT annotation pack (no model output becomes a label)."""

import xml.etree.ElementTree as ET

import numpy as np
import pytest

from app.pipeline import poles as pl
from ml.poles import annotation_pack as pack
from ml.poles import eval_detectors as ev


def test_missing_yolo_weights_fail_soft(tmp_path):
    d = pl.make_pole_detector("missing.pt", 0.3, tmp_path)
    assert isinstance(d, pl.PoleDetector)
    assert d.detect(np.zeros((64, 64, 3), np.uint8)) is None and "no pole weights" in d.error


def test_corrupt_yolo_weights_fail_soft(tmp_path):
    (tmp_path / "bad.pt").write_bytes(b"not a checkpoint")
    d = pl.make_pole_detector("bad.pt", 0.3, tmp_path)
    assert d.detect(np.zeros((64, 64, 3), np.uint8)) is None and d.error


def test_zero_shot_spec_selects_grounding_dino_backend(tmp_path):
    d = pl.make_pole_detector("grounding-dino:some/model", 0.25, tmp_path)
    assert isinstance(d, pl.ZeroShotPoleDetector) and d.model_id == "some/model" and d.conf == 0.25


def test_yolo_inference_returns_boxes_in_image_pixels():
    pytest.importorskip("ultralytics")
    from app.core.config import ROOT_DIR
    w = ROOT_DIR / "yolo11n.pt"
    if not w.exists():
        pytest.skip("yolo11n.pt not present")
    d = pl.PoleDetector(w, conf=0.01, device="cpu")
    out = d.detect(np.full((200, 300, 3), 127, np.uint8))
    assert isinstance(out, list)
    for b in out:
        x0, y0, x1, y1 = b["bbox"]
        assert 0 <= x0 <= x1 <= 300 and 0 <= y0 <= y1 <= 200 and 0 <= b["confidence"] <= 1


def _d(b, c=0.9):
    return {"bbox": list(map(float, b)), "confidence": c}


def test_small_ground_truth_is_ignored_only_in_the_medium_large_protocol():
    gts = [[0, 0, 4, 40], [100, 0, 140, 300]]          # a 4x40 px distant pole (small) and a near pole
    pred = [_d([0, 0, 4, 40]), _d([101, 0, 140, 300])]
    assert ev.match_status(pred, gts) == ["tp", "tp"]
    assert ev.match_status(pred, gts, ignore_small=True) == ["ign", "tp"]
    s = ev.score([pred], [gts], 0.5, False, ignore_small=True)
    assert (s["tp"], s["fp"], s["fn"]) == (1, 0, 0)
    s = ev.score([[_d([300, 0, 303, 20])]], [gts], 0.5, False, ignore_small=True)  # small stray box: not an FP
    assert (s["tp"], s["fp"], s["fn"]) == (0, 0, 1) and s["images_with_missed_pole"] == 1


def test_large_false_detection_counts_in_both_protocols():
    gts = [[100, 0, 140, 300]]
    pred = [_d([400, 0, 460, 300])]
    for ig in (False, True):
        s = ev.score([pred], [gts], 0.5, False, ignore_small=ig)
        assert (s["tp"], s["fp"], s["fn"]) == (0, 1, 1) and s["images_with_false_detection"] == 1


def test_one_prediction_matches_one_pole():
    gts = [[100, 0, 140, 300]]
    s = ev.score([[_d([100, 0, 140, 300], 0.9), _d([101, 0, 141, 300], 0.8)]], [gts], 0.5, False)
    assert (s["tp"], s["fp"]) == (1, 1)


def test_ap50_is_one_for_a_perfect_ranked_list():
    gts = [[[100, 0, 140, 300]], [[10, 0, 60, 200]]]
    dets = [[_d(gts[0][0])], [_d(gts[1][0], 0.5)]]
    assert ev.ap50(dets, gts, False) == pytest.approx(1.0, abs=1e-6)


def test_pole_localisation_for_the_wire_pipeline():
    poles = [{"bbox": [100, 0, 120, 300], "confidence": 0.9}]
    assert pl.ends_at_pole([(119, 40), (400, 40)], poles, margin=0) == 1
    assert pl.ends_at_pole([(125, 40)], poles, margin=10) == 1
    assert pl.ends_at_pole([(125, 40)], poles, margin=0) == 0


def _xml(tmp_path, boxes, tag=True):
    root = ET.Element("annotations")
    el = ET.SubElement(root, "image", id="0", name="a.jpg", width="200", height="100")
    if tag:
        ET.SubElement(el, "tag", label=pack.TAG, source="manual")
    for label, verified, src in boxes:
        b = ET.SubElement(el, "box", label=label, xtl="10", ytl="0", xbr="30", ybr="100", occluded="0")
        ET.SubElement(b, "attribute", name="verified").text = verified
        ET.SubElement(b, "attribute", name="source").text = src
        ET.SubElement(b, "attribute", name="occluded_by").text = "wires"
    p = tmp_path / "x.xml"
    ET.ElementTree(root).write(p)
    return p


def _import(tmp_path, xml):
    img_dir = tmp_path / "imgs"
    img_dir.mkdir(exist_ok=True)
    (img_dir / "a.jpg").write_bytes(b"x")
    pack.SPLITS["unit"] = str(img_dir)
    try:
        return pack.import_cvat(xml, "unit", tmp_path / "out")
    finally:
        del pack.SPLITS["unit"]


def test_candidates_never_become_labels(tmp_path):
    r = _import(tmp_path, _xml(tmp_path, [("pole_candidate", "true", "model")]))
    assert r["imported"] == 0


def test_untagged_images_are_not_imported_even_if_verified(tmp_path):
    r = _import(tmp_path, _xml(tmp_path, [("utility-pole", "true", "")], tag=False))
    assert r["imported"] == 0


def test_verified_poles_and_negatives_are_imported(tmp_path):
    r = _import(tmp_path, _xml(tmp_path, [("utility-pole", "true", ""), ("not-utility-pole", "true", ""),
                                          ("not-utility-pole", "true", "unsure")]))
    assert r["imported"] == 1
    lbl = (tmp_path / "out" / "labels" / "unit" / "a.txt").read_text().split()
    assert lbl[0] == "0" and float(lbl[1]) == pytest.approx(0.1) and float(lbl[3]) == pytest.approx(0.1)
    import json
    side = json.loads((tmp_path / "out" / "unit_attributes.json").read_text())["a.jpg"]
    assert len(side["negatives"]) == 1 and side["poles"][0]["occluded_by"] == "wires"


def test_tagged_photo_without_poles_is_a_verified_pole_free_photo(tmp_path):
    r = _import(tmp_path, _xml(tmp_path, []))
    assert r["imported"] == 1 and (tmp_path / "out" / "labels" / "unit" / "a.txt").read_text() == ""
