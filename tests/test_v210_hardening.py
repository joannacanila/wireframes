"""v2.10: glare gate, output-identical latency optimisations, scoped linting."""

import tomllib
from pathlib import Path

import cv2
import numpy as np
import pytest
from scipy import ndimage as ndi

from app.core.config import QualityConfig
from app.pipeline import enhance as E
from app.pipeline import wire_instances as WI
from app.pipeline.quality import glare_masked, grade
from ml.stress import EXTRA_CONDITIONS, apply_condition

ROOT = Path(__file__).resolve().parents[1]
UNC = {"wire_pixels": 5000, "entropy": 0.1, "disagreement": 0.05}


# ------------------------------------------------------------------ glare gate

def _sky_with_wires(lift=0.0, squash=1.0):
    """Grey sky with dark 2 px wires; lift raises the exposure, squash compresses local contrast."""
    img = np.full((512, 768, 3), 150, np.float32)
    for y in range(60, 460, 40):
        cv2.line(img, (0, y), (767, y + 30), (40, 40, 40), 2)
    img = img + lift
    local = cv2.GaussianBlur(img, (0, 0), 15)
    return np.clip(local + squash * (img - local), 0, 255).astype(np.uint8)


def test_line_visibility_separates_glare_from_bright_sky():
    glare = E.measure(_sky_with_wires(lift=110, squash=0.25))
    bright = E.measure(_sky_with_wires(lift=110, squash=1.0))
    assert glare.bright_share > 0.6 and bright.bright_share > 0.6
    assert glare.line_contrast < 15 < bright.line_contrast
    assert E.measure(_sky_with_wires()).bright_share < 0.6


def test_glare_gate_grades_retake_only_when_bright_and_washed_out():
    cfg = QualityConfig(glare_gate=True)
    washed = {"mean_l": 95, "clipped_high": 0.3, "bright_share": 0.9, "line_contrast": 8.0}
    contrasty = {**washed, "line_contrast": 25.0}
    dim = {**washed, "bright_share": 0.4}
    g = grade(UNC, washed, cfg)
    assert g["grade"] == "Retake recommended" and "glare washed out" in g["reason"]
    assert grade(UNC, contrasty, cfg)["grade"] == "A" and grade(UNC, dim, cfg)["grade"] == "A"
    assert grade(UNC, washed, QualityConfig(glare_gate=False))["grade"] == "A"     # v2.9 behaviour when off
    assert not glare_masked({"mean_l": 95}, cfg)                                   # v2.9 records lack the fields


def test_glare_gate_turns_safe_verdict_inconclusive_but_keeps_hazards():
    from app.pipeline.hazard import HIGH, INCONCLUSIVE, NOT, apply_reliability
    q = grade(UNC, {"mean_l": 95, "clipped_high": 0.3, "bright_share": 0.9, "line_contrast": 8.0},
              QualityConfig(glare_gate=True))
    assert apply_reliability({"verdict": NOT, "reasons": ["NOT AT RISK: x"], "factors": {}}, q)["verdict"] == INCONCLUSIVE
    assert apply_reliability({"verdict": HIGH, "reasons": ["AT RISK - HIGH: x"], "factors": {}}, q)["verdict"] == HIGH


def test_shipped_config_enables_the_validated_gate(cfg):
    assert cfg.quality.glare_gate and cfg.quality.glare_bright_min == 0.6 and cfg.quality.glare_line_contrast_max == 15.0


@pytest.mark.parametrize("cond", ["brightsky"])
def test_brightsky_control_is_deterministic_and_keeps_contrast(scene, cond):
    assert cond in EXTRA_CONDITIONS
    a = apply_condition(scene.rgb, cond, "p.jpg")
    np.testing.assert_array_equal(a, apply_condition(scene.rgb, cond, "p.jpg"))
    g = E.measure(apply_condition(scene.rgb, "glare", "p.jpg"))
    b = E.measure(a)
    assert b.mean_l > E.measure(scene.rgb).mean_l and b.line_contrast > g.line_contrast


# ------------------------------------------------- latency: identical outputs

def test_label_coords_equal_argwhere():
    rng = np.random.default_rng(0)
    lab, n = ndi.label(rng.random((120, 160)) > 0.7, structure=np.ones((3, 3)))
    got = WI._label_coords(lab, n)
    for i in range(n):
        np.testing.assert_array_equal(got[i], np.argwhere(lab == i + 1))


def _reference_candidates(ends, max_gap, cos_lim):
    """The v2.8 nested loop, kept verbatim as the specification."""
    cands = []
    for i in range(len(ends)):
        for j in range(i + 1, len(ends)):
            for ea, ta in ((ends[i][0], ends[i][1]), (ends[i][2], ends[i][3])):
                for eb, tb in ((ends[j][0], ends[j][1]), (ends[j][2], ends[j][3])):
                    gap = eb - ea
                    d = float(np.hypot(*gap))
                    if d > max_gap or -float(np.dot(ta, tb)) < cos_lim:
                        continue
                    if d > 1.0:
                        u = gap / d
                        if float(np.dot(ta, u)) < cos_lim or float(np.dot(tb, -u)) < cos_lim:
                            continue
                    cands.append((d, i, j, ea, eb))
    return cands


@pytest.mark.parametrize("seed", range(5))
def test_stitch_candidates_equal_the_v28_loop_including_ties(seed):
    rng = np.random.default_rng(seed)
    ends = []
    for _ in range(25):
        a, b = rng.integers(0, 60, 2).astype(float), rng.integers(0, 60, 2).astype(float)  # integer grid: many ties
        t = rng.normal(size=2)
        t /= np.hypot(*t)
        ends.append((a, -t, b, t, 10.0))
    cos_lim = np.cos(np.deg2rad(35))
    ref = sorted(_reference_candidates(ends, 30.0, cos_lim), key=lambda c: c[0])
    got = sorted(WI._stitch_candidates(ends, 30.0, cos_lim), key=lambda c: c[0])
    assert len(ref) == len(got) > 0
    for r, g in zip(ref, got, strict=True):
        assert r[:3] == g[:3] and np.array_equal(r[3], g[3]) and np.array_equal(r[4], g[4])


def test_crop_helpers_equal_full_frame_versions():
    from app.pipeline.hazard import _pixels, mean_width_px
    from app.pipeline.segment import _bbox
    m = np.zeros((300, 400), bool)
    cv2.line(m.view(np.uint8), (10, 20), (390, 280), 1, 3)
    m[0, 0] = m[299, 399] = True                                       # touches the image border
    ys, xs = np.nonzero(m)
    py, px = _pixels(m)
    assert np.array_equal(ys, py) and np.array_equal(xs, px)
    assert _bbox(m) == (float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1))
    full = float(m.sum()) / max(int(WI._skeleton(m).sum()), 1)
    assert mean_width_px(m) == pytest.approx(full, rel=0, abs=0)
    with pytest.raises(ValueError):
        _bbox(np.zeros((5, 5), bool))


# ------------------------------------------------------------ scoped linting

def test_ruff_is_scoped_to_the_project():
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["ruff"]
    assert cfg["force-exclude"] is True
    assert {".agents", ".claude", ".venv", "venv"} <= set(cfg["extend-exclude"])
