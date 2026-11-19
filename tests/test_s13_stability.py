"""S13: model traceability and the silent strand-head fallback.

Root cause recorded in docs/thesis/IMPLEMENTATION_CHANGELOG.md (S13): repeated runs of the same photo are bit-identical
for one code version, configuration and checkpoint set (scripts/repeatability.py; stored duplicate uploads agree within
every config fingerprint). Differences came from version drift, so every result must name its exact checkpoints, and a
component that fails to load must be visible on the record rather than change the result silently.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.state import model_identity
from app.pipeline.runner import run_analysis
from app.pipeline.segment import WireDetection, parse_yolo_seg_labels

ROOT = Path(__file__).resolve().parents[1]


def _seg_cfg(shipped_cfg, tmp_path: Path, **files: bytes):
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    ens = tmp_path / "ens.json"
    ens.write_text(json.dumps({"members": [{"weights": str(tmp_path / "m1.pt")}, {"weights": str(tmp_path / "m2.pt")}]}))
    return shipped_cfg.segmentation.model_copy(update={
        "weights": str(tmp_path / "yolo.pt"), "wirenet_weights": str(ens),
        "pole_weights": str(tmp_path / "poles.pt"), "strand_head": str(tmp_path / "head.pt")})


def test_model_identity_names_every_configured_checkpoint_including_ensemble_members(shipped_cfg, tmp_path):
    cfg = _seg_cfg(shipped_cfg, tmp_path, **{"yolo.pt": b"a", "m1.pt": b"b", "m2.pt": b"c", "poles.pt": b"d", "head.pt": b"e"})
    ident = model_identity(cfg)
    assert set(ident["checkpoints"]) == {"yolo.pt", "m1.pt", "m2.pt", "poles.pt", "head.pt"}
    assert all(re.fullmatch(r"[0-9a-f]{16}", v) for v in ident["checkpoints"].values())
    assert re.fullmatch(r"[0-9a-f]{12}", ident["fingerprint"])
    assert model_identity(cfg) == ident  # stable for the same files


def test_model_identity_changes_when_a_checkpoint_changes_though_the_config_does_not(shipped_cfg, tmp_path):
    cfg = _seg_cfg(shipped_cfg, tmp_path, **{"yolo.pt": b"a", "m1.pt": b"b", "m2.pt": b"c", "poles.pt": b"d", "head.pt": b"e"})
    before = model_identity(cfg)
    (tmp_path / "m2.pt").write_bytes(b"c-retrained")
    after = model_identity(cfg)
    assert after["fingerprint"] != before["fingerprint"] and after["checkpoints"]["m2.pt"] != before["checkpoints"]["m2.pt"]


def test_a_missing_checkpoint_is_reported_not_hidden(shipped_cfg, tmp_path):
    cfg = _seg_cfg(shipped_cfg, tmp_path, **{"yolo.pt": b"a", "m1.pt": b"b", "poles.pt": b"d", "head.pt": b"e"})
    assert model_identity(cfg)["checkpoints"]["m2.pt"] is None


def test_shipped_configuration_points_at_the_pinned_weight_manifest(shipped_cfg):
    from app.core.weights import REQUIRED
    pinned = {w.name: w.sha256[:16] for w in REQUIRED}
    ident = model_identity(shipped_cfg.segmentation)
    assert set(ident["checkpoints"]) <= set(pinned)
    for name, digest in ident["checkpoints"].items():  # installed files must be the pinned ones
        assert digest is None or digest == pinned[name], name


class _HeadFails:
    """A segmenter whose strand head could not load for this photo (types come from segmentation only)."""
    source = "model"

    def __init__(self, dets, err):
        self.dets, self.last_head_error = dets, err

    def segment(self, x, lb):
        return self.dets


def test_a_strand_head_that_failed_to_load_is_flagged_on_the_result(cfg, scene):
    h, w = scene.rgb.shape[:2]
    dets = [WireDetection(d.wire_class, 0.6, d.mask, d.bbox) for d in parse_yolo_seg_labels(scene.labels, w, h)]
    failed = run_analysis(scene.rgb, cfg, _HeadFails(dets, "strand head unavailable: out of memory"))
    ok = run_analysis(scene.rgb, cfg, _HeadFails(dets, None))
    assert "strand_head_unavailable" in failed.flags and "strand_head_unavailable" not in ok.flags
    # reference annotations bypass the model, so the head is irrelevant there
    ref = run_analysis(scene.rgb, cfg, _HeadFails(dets, "x"), detections=dets)
    assert "strand_head_unavailable" not in ref.flags


def test_landing_disclaimer_is_the_only_landing_change():
    js = (ROOT / "app/web/assets/notice.js").read_text()
    # S14: the owner's disclaimer wording, and the notice opens as a modal dialog instead of expanding over the hero
    # S17: that wording is now THE canonical Important Notice (one text, no landing variant)
    assert ('export const NOTICE_TEXT = "WIREFRAME is an image-based decision-support tool designed to help document visible pole and wire '
            'conditions. Its results are preliminary and may contain errors or limitations. Findings should be reviewed by '
            'qualified professionals and must not replace an on-site inspection or applicable safety assessment.";') in js
    assert 'v === "landing"' in js and "dlg.showModal()" in js and 'aria-haspopup="dialog"' in js
    html = (ROOT / "app/web/landing.html").read_text()
    assert '<wf-notice variant="landing"></wf-notice>' in html


def test_assistant_answers_reference_scope_questions_from_the_knowledge_base():
    from app.assistant.kb import search
    for q in ("What 3% limit applies to telecom cables?", "Does the 3% reference apply to abandoned wires?",
              "Is a fibre cable above 3% too saggy?"):
        top = search(q)[0][0]
        assert top.id == "reference-scope", q
        assert "No 3% limit applies to telecom or fibre cable" in top.body and "not an assessment or a safety finding" in top.body
