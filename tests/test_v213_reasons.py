"""v2.13 field guidance (app/services/reasons.py): one per-span `action` and a photo-level `retake_guidance`, plus
the `suppressed_spans` list in the API result. Guidance is derived from photo size, span position and failure
code only; it never changes a measurement."""

from __future__ import annotations

import io
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import Settings
from app.core.state import build_state
from app.main import create_app
from app.services.reasons import SMALL_PHOTO_PX, retake_summary, touches_edge, wire_action
from tests.conftest import sign_in_staff
from tests.test_v211_field_ui import ORIGIN, MaskSegmenter

# ---------------------------------------------------------------- touches_edge


@pytest.mark.parametrize("bbox,expected", [
    ((0, 50, 100, 60), True),         # left edge
    ((50, 50, 199, 60), True),        # right edge (width 200: last column 199)
    ((50, 2, 100, 60), True),         # top, within the 3 px margin
    ((50, 50, 100, 98), True),        # bottom (height 100)
    ((10, 10, 190, 90), False),
    (None, False),
])
def test_touches_edge(bbox, expected):
    assert touches_edge(bbox, 200, 100) is expected


def test_touches_edge_without_size_is_false():
    assert touches_edge((0, 0, 10, 10), 0, 0) is False


# ---------------------------------------------------------------- wire_action

INNER = (100, 100, 400, 200)


def test_scored_span_has_no_action_unless_truncated():
    assert wire_action("scored", None, [], INNER, 2000, 1500) is None
    a = wire_action("scored", None, ["truncated"], INNER, 2000, 1500)
    assert a and "both poles" in a


def test_service_drop_has_no_action():
    assert wire_action("service_drop", None, [], INNER, 452, 678) is None


@pytest.mark.parametrize("code", ["chord_below_precision_gate", "too_short"])
def test_short_span_on_small_photo_asks_for_full_resolution(code):
    a = wire_action("fit_failed", code, [], INNER, 452, 678)
    assert a.startswith("Upload the full-resolution photo") and "452 px" in a  # the v2.13 problem photo


def test_short_span_on_full_resolution_photo():
    edge = wire_action("fit_failed", "chord_below_precision_gate", [], (0, 100, 300, 200), 4000, 3000)
    assert "runs off the edge" in edge
    inner = wire_action("fit_failed", "chord_below_precision_gate", [], INNER, 4000, 3000)
    assert inner.startswith("Move closer or zoom in")
    assert SMALL_PHOTO_PX == 1000


@pytest.mark.parametrize("code,needle", [
    ("residual_too_large", "does not cross others"),
    ("non_positive_a", "does not cross others"),
    ("no_convergence", "from the side"),
    ("degenerate", "from the side"),
])
def test_fit_failures_map_to_one_instruction(code, needle):
    assert needle in wire_action("fit_failed", code, [], INNER, 4000, 3000)


def test_unknown_failure_has_no_action():
    assert wire_action("fit_failed", "empty_mask", [], INNER, 4000, 3000) is None
    assert wire_action("fit_failed", None, None, INNER, 4000, 3000) is None


# ---------------------------------------------------------------- retake_summary

def _w(status, action=None):
    return {"status": status, "action": action}


def test_retake_summary_below_40_percent_is_none():
    wires = [_w("scored")] * 4 + [_w("fit_failed", "Move closer or zoom in")] * 2  # 2 / 6 = 33 %
    assert retake_summary(wires, 4000, 3000) is None


def test_retake_summary_picks_the_most_common_action():
    wires = [_w("scored")] * 2 + [_w("fit_failed", "Move closer or zoom in")] * 2 + [
        _w("fit_failed", "Shoot the span from the side")]
    s = retake_summary(wires, 4000, 3000)
    assert s == "3 of 5 spans could not be measured. Best next photo: move closer or zoom in."


def test_retake_summary_ignores_service_drops():
    wires = [_w("scored"), _w("scored"), _w("fit_failed", "Move closer")] + [_w("service_drop")] * 5
    assert retake_summary(wires, 4000, 3000) is None  # 1 / 3 non-drop spans


def test_retake_summary_needs_an_action():
    assert retake_summary([_w("fit_failed"), _w("fit_failed")], 4000, 3000) is None
    assert retake_summary([], 4000, 3000) is None
    assert retake_summary([_w("service_drop")], 4000, 3000) is None


# ---------------------------------------------------------------- API: action, retake_guidance, suppressed_spans

SUPPRESSED = {"wire_class": "telecom-cable", "confidence": 0.71, "bbox": [30.0, 400.0, 600.0, 420.0],
              "polygons": [[[30, 400], [600, 400], [600, 420], [30, 420]]], "ground_share": 0.93,
              "reason_code": "ground_structure",
              "reason": "Lies on the road or pavement: a kerb, manhole or pavement edge, not an overhead wire"}


class GatedSegmenter(MaskSegmenter):
    """MaskSegmenter plus a ground-gate result, as HybridSegmenter reports it after _ground_gate."""

    def __init__(self, suppressed):
        super().__init__()
        self._sup = suppressed

    def segment(self, x, lb):
        out = super().segment(x, lb)
        self.last_suppressed = [dict(s) for s in self._sup]
        return out


def _client(tmp_path, cfg, suppressed):
    st = build_state(Settings(data_dir=tmp_path, start_worker=True), cfg, segmenter=GatedSegmenter(suppressed))
    return _Staff(create_app(st), st)


class _Staff(TestClient):
    """A client that signs in as staff on entry (v2.20: technical result fields are staff-only)."""

    def __init__(self, app, st):
        super().__init__(app, headers=ORIGIN)
        self._st = st

    def __enter__(self):
        c = super().__enter__()
        sign_in_staff(c, self._st)
        return c


def _photo(w=640, h=480):
    img = np.full((h, w, 3), 200, np.uint8)
    img[: h // 2] = 230
    cv2.randn(img, 200, 4)
    return img


def _result(c, rgb) -> dict:
    b = io.BytesIO()
    Image.fromarray(rgb).save(b, "PNG")
    r = c.post("/api/analysis/upload", files={"file": ("p.png", b.getvalue(), "image/png")})
    assert r.status_code == 202, r.text
    aid = r.json()["id"]
    for _ in range(600):
        if c.get(f"/api/analysis/{aid}").json()["status"] != "processing":
            break
        time.sleep(0.05)
    res = c.get(f"/api/analysis/{aid}/result")
    assert res.status_code == 200
    return res.json()


def test_result_lists_suppressed_spans_and_flags_them(tmp_path, cfg):
    with _client(tmp_path / "a", cfg, []) as c:
        baseline = _result(c, _photo())
    with _client(tmp_path / "b", cfg, [SUPPRESSED]) as c:
        res = _result(c, _photo())
    sup = res["suppressed_spans"]
    assert len(sup) == 1 and sup[0]["reason_code"] == "ground_structure" and sup[0]["ground_share"] == 0.93
    assert "ground_structures_suppressed" in res["flags"]
    # suppressed traces are never scored or listed as wires: the wire list is the ungated one
    assert [(w["index"], w["crs"]) for w in res["wires"]] == [(w["index"], w["crs"]) for w in baseline["wires"]]


def test_unavailable_parser_is_flagged_not_listed(tmp_path, cfg):
    with _client(tmp_path, cfg, [{"unavailable": True}]) as c:
        res = _result(c, _photo())
    assert res["suppressed_spans"] == [] and "ground_gate_unavailable" in res["flags"]
    assert "ground_structures_suppressed" not in res["flags"]


def test_result_without_suppression_has_empty_list(tmp_path, cfg):
    with _client(tmp_path, cfg, []) as c:
        res = _result(c, _photo())
    assert res["suppressed_spans"] == []
    assert not {"ground_structures_suppressed", "ground_gate_unavailable"} & set(res["flags"])


@pytest.mark.test_reference
def test_unmeasured_span_gets_action_and_photo_gets_retake_guidance(tmp_path, cfg):
    with _client(tmp_path, cfg, []) as c:
        res = _result(c, _photo())
    short = [w for w in res["wires"] if w["crs"] is None]
    assert short, "the 60 px span must be unmeasured"
    assert {w["reason_code"] for w in short} <= {"chord_below_precision_gate", "too_short"}
    assert all("480 px" in w["action"] for w in short)  # 640 x 480 photo: short side under SMALL_PHOTO_PX
    scored = [w for w in res["wires"] if w["crs"] is not None]
    assert all(w["action"] is None for w in scored if "truncated" not in (w.get("flags") or []))
    # >= 40 % of the spans unmeasured: one photo-level instruction, built from the most common span action
    n, total = len(short), len([w for w in res["wires"] if w["status"] != "service_drop"])
    assert n / total >= 0.4
    g = res["retake_guidance"]
    assert g and g.startswith(f"{n} of {total} spans could not be measured") and "full-resolution" in g
