"""v2.17: retake guardrail, centerline metrics, guardrail API."""

from types import SimpleNamespace as NS

import numpy as np
import pytest

from app.core.config import GuardrailConfig
from app.pipeline import centerline_eval as ce
from app.pipeline.guardrail import assess
from tests.conftest import upload, wait_done

CFG = GuardrailConfig(enabled=True)


def wire(i, status="scored", uncertain=False, share=None):
    return NS(index=i, status=status, flags=["class_uncertain"] if uncertain else [],
              fit={"class_evidence": share} if share else {})


def outcome(wires, grade="A", flags=(), suppressed=(), poles=None):
    return NS(wires=wires, quality={"grade": grade, "reason": "blurred" if grade.startswith("Retake") else None},
              flags=list(flags), suppressed=list(suppressed),
              scene=None if poles is None else {"poles_detected": poles})


def test_clean_result_is_acceptable():
    g = assess(outcome([wire(i) for i in range(6)], poles=[{"bbox": [0, 0, 1, 1]}]), CFG)
    assert g["quality_status"] == "acceptable" and not g["requires_retake"] and g["quality_issues"] == []


def test_two_strong_conflicts_request_a_retake():
    ws = [wire(0, uncertain=True, share={"power-distribution-lines": 0.55, "telecom-cable": 0.45}),
          wire(1, uncertain=True, share={"power-distribution-lines": 0.4, "telecom-cable": 0.6}),
          *[wire(i) for i in range(2, 10)]]
    g = assess(outcome(ws), CFG)
    assert g["requires_retake"] and g["proceed_with_warning"]
    assert [c["strand_id"] for c in g["classification_conflicts"]] == [0, 1]
    assert g["uncertain_strand_count"] == 2 and {0, 1} <= set(g["affected_strand_ids"])


def test_weak_uncertainty_is_not_a_conflict():
    ws = [wire(0, uncertain=True, share={"power-distribution-lines": 0.85, "telecom-cable": 0.15}),
          *[wire(i) for i in range(1, 10)]]
    g = assess(outcome(ws), CFG)
    assert g["classification_conflicts"] == [] and g["quality_status"] == "acceptable"


def test_poor_image_and_fragmentation_is_highly_uncertain():
    ws = [wire(i, status="too_short") for i in range(8)]
    g = assess(outcome(ws, grade="Retake recommended"), CFG)
    assert g["quality_status"] == "highly_uncertain" and g["unassessed_strand_count"] == 8
    assert {i["code"] for i in g["quality_issues"]} >= {"image_quality", "fragmentation"}


def test_missing_poles_is_information_only():
    g = assess(outcome([wire(i) for i in range(5)], poles=[]), CFG)
    assert g["quality_status"] == "acceptable" and g["quality_issues"][0]["code"] == "poles"


def test_structure_edges_need_attention():
    g = assess(outcome([wire(i) for i in range(4)], suppressed=[{"reason_code": "structure_edge"}] * 2), CFG)
    assert g["quality_status"] == "needs_attention"


def test_lens_outside_envelope_requests_retake():
    g = assess(outcome([wire(0)], flags=["lens_outside_envelope"]), CFG)
    assert g["requires_retake"] and g["quality_issues"][0]["code"] == "lens"


# centerline metrics ---------------------------------------------------------------------------------------------
def _line(shape, y, x0=10, x1=190, w=3):
    m = np.zeros(shape, bool)
    m[y - w // 2:y + w // 2 + 1, x0:x1] = True
    return m


def test_centerline_tolerates_a_one_pixel_offset_and_counts_false_positives():
    shape = (200, 200)
    gt = [("power-distribution-lines", _line(shape, 50))]
    pred = [ce.Pred("power-distribution-lines", _line(shape, 51)), ce.Pred("telecom-cable", _line(shape, 150))]
    c = ce.image_counts(pred, gt, shape)
    w = ce.prf(c["wire"])
    assert w["recall"] == pytest.approx(1.0, abs=0.02) and w["precision"] == pytest.approx(0.5, abs=0.02)
    p = ce.prf(c["power-distribution-lines"])
    assert p["precision"] == pytest.approx(1.0, abs=0.02)
    t = ce.prf(c["telecom-cable"])
    assert t["precision"] == 0 and t["recall"] is None  # no telecom label: every telecom pixel is a false positive


def test_uncertain_type_never_counts_as_a_correct_class():
    shape = (200, 200)
    gt = [("power-distribution-lines", _line(shape, 50))]
    pred = [ce.Pred("power-distribution-lines", _line(shape, 50), confident=False)]
    c = ce.image_counts(pred, gt, shape)
    assert ce.prf(c["wire"])["recall"] > 0.95  # found as a wire ...
    assert ce.prf(c["power-distribution-lines"])["recall"] == 0  # ... but not as Power
    t = ce.StrandTally()
    t.add(pred, gt, shape)
    r = t.report()
    assert r["accuracy"] == 0 and r["uncertain_rate"] == 1


def test_summary_has_bootstrap_intervals():
    shape = (200, 200)
    gt = [("telecom-cable", _line(shape, 80))]
    per = [ce.image_counts([ce.Pred("telecom-cable", _line(shape, 80))], gt, shape)] * 3
    s = ce.summarize(per, n_boot=50)
    assert s["images"] == 3 and s["telecom-cable"]["f1"] == pytest.approx(1.0, abs=0.02)
    assert len(s["telecom-cable"]["precision_ci95"]) == 2


# API ------------------------------------------------------------------------------------------------------------
def test_guardrail_choice_and_retake_link(client, scene):
    a = upload(client, scene.rgb, scene.labels)
    wait_done(client, a)
    assert client.post(f"/api/analysis/{a}/guardrail", json={"action": "nonsense"}).status_code == 422
    assert client.post("/api/analysis/nope/guardrail", json={"action": "proceed"}).status_code == 404
    r = client.post(f"/api/analysis/{a}/guardrail", json={"action": "proceed"})
    assert r.status_code == 200 and r.json()["choice"]["action"] == "proceed"
    assert client.get(f"/api/analysis/{a}/result").json()["guardrail"]["choice"]["action"] == "proceed"
    b = upload(client, scene.rgb, scene.labels, retake_of=a)
    wait_done(client, b)
    assert client.get(f"/api/analysis/{b}/result").json()["guardrail"]["retake_of"] == a
    old = client.get(f"/api/analysis/{a}/result").json()["guardrail"]["choice"]
    assert old["action"] == "retake" and old["replaced_by"] == b



def test_pdf_and_result_carry_the_guardrail_warning(client, scene):
    import pypdfium2 as pdfium

    from app.models.orm import Analysis
    a = upload(client, scene.rgb, scene.labels)
    wait_done(client, a)
    with client.state.db.session() as s:  # a stored retake recommendation, as the model path would write it
        x = s.get(Analysis, a)
        x.metadata_json = {**x.metadata_json, "guardrail": {"requires_retake": True, "quality_status": "retake_recommended",
                                                            "quality_reasons": ["Two wires show two wire types."]}}
    client.post(f"/api/analysis/{a}/guardrail", json={"action": "proceed"})
    g = client.get(f"/api/analysis/{a}/result").json()["guardrail"]
    assert g["requires_retake"] and g["choice"]["action"] == "proceed"
    pdf = client.get(f"/api/reports/{a}/pdf").content
    doc = pdfium.PdfDocument(pdf)
    text = " ".join(" ".join(doc[i].get_textpage().get_text_range() for i in range(len(doc))).split())  # line wraps
    doc.close()
    assert "Assessment may be inaccurate" in text and "chose to proceed" in text


# poles and annotation pack ----------------------------------------------------------------------------------------
def test_clean_boxes_removes_duplicates_and_group_boxes():
    from app.pipeline.poles import clean_boxes
    a = {"bbox": [10, 10, 30, 200], "confidence": 0.6}
    dup = {"bbox": [11, 12, 31, 198], "confidence": 0.4}
    b = {"bbox": [100, 20, 120, 210], "confidence": 0.5}
    group = {"bbox": [0, 0, 130, 220], "confidence": 0.3}  # holds the centres of a and b
    out = clean_boxes([a, dup, b, group])
    assert out == [a, b]


def test_annotation_import_accepts_only_fully_verified_images(tmp_path):
    from ml.annotation_pack import import_cvat
    xml = tmp_path / "x.xml"
    xml.write_text("""<annotations>
      <image id="0" name="a.jpg" width="100" height="100">
        <polygon label="telecom-cable" points="0,0;50,1;50,3;0,2"><attribute name="verified">true</attribute></polygon>
        <box label="utility-pole" xtl="10" ytl="10" xbr="20" ybr="90"><attribute name="verified">true</attribute></box>
      </image>
      <image id="1" name="b.jpg" width="100" height="100">
        <polygon label="wire_candidate" points="0,0;50,1;50,3"><attribute name="verified">false</attribute></polygon>
      </image></annotations>""")
    import_cvat(xml, tmp_path / "out")
    files = sorted(p.name for p in (tmp_path / "out" / "labels").iterdir())
    assert files == ["a.poles.txt", "a.regions.json", "a.txt"]
    assert (tmp_path / "out" / "labels" / "a.txt").read_text().startswith("3 ")  # telecom-cable class id
    assert (tmp_path / "out" / "labels" / "a.poles.txt").read_text().startswith("0 0.150000 0.500000")
