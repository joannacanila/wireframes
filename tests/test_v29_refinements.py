"""v2.9: motion-blur router, JPEG deblocking, X-junction matching, service drops, corroborated
pole-level aggregation, reliability reasons, diagnostic figure, migration."""

import cv2
import numpy as np
import pytest

from app.core.config import EnhancementConfig, HazardConfig, QualityConfig
from app.pipeline import enhance as E
from app.pipeline.hazard import HIGH, MODERATE, NOT, assess, corroboration_k
from ml.degrade import disk_kernel, motion_kernel

P, T = "power-distribution-lines", "telecom-cable"
SHAPE = (400, 800)


def _texture(seed=0, shape=(512, 512)):
    rng = np.random.default_rng(seed)
    g = cv2.GaussianBlur(rng.uniform(0, 255, shape).astype(np.float32), (0, 0), 1.2)
    g = (g - g.min()) / (g.max() - g.min()) * 255
    return np.repeat(g[..., None], 3, axis=2).astype(np.uint8)


def test_motion_coherence_separates_motion_from_defocus_and_sharp():
    img = _texture()
    mot = cv2.filter2D(img, -1, motion_kernel(15, 30.0))
    dfc = cv2.filter2D(img, -1, disk_kernel(5))
    c_sharp, c_mot, c_dfc = (E.measure(x, coherence_below=1e9).motion_coherence for x in (img, mot, dfc))
    assert c_mot > 0.3 and c_sharp < 0.16 and c_dfc < 0.16
    # the lost detail lies along the motion direction (30 deg in image x/y, y down = -30 deg)
    ang = E.measure(mot, coherence_below=1e9).motion_angle_deg
    assert min(abs(ang - 150), abs(ang - 30)) < 15


def test_router_skips_unsharp_under_motion_blur_only():
    img = _texture(1)
    mot = cv2.filter2D(img, -1, motion_kernel(41, 0.0))
    dfc = cv2.filter2D(img, -1, disk_kernel(9))
    on = EnhancementConfig(motion_route=True)
    assert "unsharp" not in E.enhance(mot, on).applied and "motion_smooth" in E.enhance(mot, on).applied
    assert "unsharp" in E.enhance(mot, EnhancementConfig()).applied          # v2.8 behaviour when off
    assert "unsharp" in E.enhance(dfc, on).applied                            # defocus still sharpened
    skip = EnhancementConfig(motion_route=True, motion_action="skip")
    out = E.enhance(mot, skip)
    assert "motion_skip" in out.applied and "unsharp" not in out.applied and "motion_smooth" not in out.applied


def test_router_does_not_treat_glare_as_motion():
    q = E.Quality(mean_l=97, contrast=3, clipped_high=0.7, noise_sigma=0.5, sharpness=10, motion_coherence=0.4)
    assert not E.is_motion_blur(q, EnhancementConfig(motion_route=True))
    assert E.is_motion_blur(q.__class__(**{**q.__dict__, "clipped_high": 0.0}), EnhancementConfig(motion_route=True))


def test_fallback_profile_never_sharpens_motion():
    mot = cv2.filter2D(_texture(2), -1, motion_kernel(41, 60.0))
    out = E.enhance_strong(mot, EnhancementConfig(motion_route=True))
    assert "unsharp_strong" not in out.applied and "motion_smooth" in out.applied


def test_motion_photo_stays_eligible_for_the_blur_fallback():
    from app.pipeline.runner import _degraded
    q = E.Quality(mean_l=60, contrast=15, clipped_high=0.0, noise_sigma=0.5, sharpness=10, motion_coherence=0.4)
    on = EnhancementConfig(motion_route=True)
    assert "unsharp" not in E.decide(q, on) and _degraded(q.as_dict(), None, on)


def test_service_drops_do_not_trigger_the_blur_fallback():
    from app.pipeline.runner import WireOutcome, _broken
    def w(status, crs):
        return WireOutcome(index=1, wire_class=P, confidence=0.9, bbox=(0, 0, 1, 1), polygons=[], status=status,
                           fail_reason=None, centerline=[], curve=[], fit={}, delta_lim=None, delta_lim0=None,
                           crs=crs, level=None, flags=[])
    assert not _broken([w("scored", 1.0), w("service_drop", None), w("service_drop", None)], 0.5)
    assert _broken([w("scored", 1.0), w("too_short", None), w("fit_failed", None)], 0.5)


def test_deblock_removes_grid_and_keeps_wire_edges(scene):
    bgr = cv2.cvtColor(scene.rgb, cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 15])
    jpg = cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
    gray = lambda x: cv2.cvtColor(x, cv2.COLOR_RGB2GRAY)  # noqa: E731
    db = E.deblock(jpg)
    assert E.blockiness(gray(db)) < E.blockiness(gray(jpg))
    wire = np.full((64, 64, 3), 200, np.uint8)
    wire[:, 31:33] = 30  # a dark 2 px wire across a block boundary region
    assert np.array_equal(E.deblock(wire), wire)  # a real edge is above alpha: untouched


def test_preprocess_deblocks_at_original_resolution(cfg, scene):
    from app.pipeline.preprocess import preprocess
    bgr = cv2.cvtColor(scene.rgb, cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 15])
    jpg = cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
    enh = cfg.enhancement.model_copy(update={"deblock": True})
    pre = preprocess(jpg, cfg.preprocessing, enh, cfg.segmentation.imgsz)
    assert pre.enhancement[0] == "deblock"
    clean = preprocess(scene.rgb, cfg.preprocessing, enh, cfg.segmentation.imgsz)
    assert "deblock" not in (clean.enhancement or [])
    np.testing.assert_array_equal(pre.gray, preprocess(jpg, cfg.preprocessing, None).gray)  # VNI path untouched


def _x_crossing(angle_deg=60):
    m = np.zeros((300, 300), np.uint8)
    cv2.line(m, (10, 150), (290, 150), 1, 3)
    r = np.deg2rad(angle_deg)
    c, d = (150, 150), 140
    cv2.line(m, (int(c[0] - d * np.cos(r)), int(c[1] - d * np.sin(r))), (int(c[0] + d * np.cos(r)), int(c[1] + d * np.sin(r))), 1, 3)
    return m.astype(bool)


@pytest.mark.parametrize("tol", [15, 20, 25])
def test_xjunction_matching_keeps_both_crossing_wires(tol):
    from app.pipeline.wire_instances import spans_from_class_mask
    m = _x_crossing(60)
    spans = spans_from_class_mask(m, m.astype(np.float32), min_len=15, max_turn_deg=35, cluster_radius=5,
                                  xjunction_tol_deg=tol)
    assert len(spans) == 2
    for s, _ in spans:  # each span runs across the whole crossing
        ys, xs = np.nonzero(s)
        assert max(xs.max() - xs.min(), ys.max() - ys.min()) > 200


def test_xjunction_rejects_near_parallel_pairs():
    from app.pipeline.wire_instances import _xjunction_pairs
    e = lambda deg: np.array([np.sin(np.deg2rad(deg)), np.cos(np.deg2rad(deg))])  # noqa: E731
    x = [(0, e(0)), (1, e(180)), (2, e(90)), (3, e(270))]
    assert sorted(map(sorted, _xjunction_pairs(x, 20, 30))) == [[0, 1], [2, 3]]
    shallow = [(0, e(0)), (1, e(180)), (2, e(20)), (3, e(200))]   # 20 deg crossing: a bundle
    assert _xjunction_pairs(shallow, 25, 30) is None
    assert _xjunction_pairs(x[:3], 20, 30) is None                  # T-junction: greedy rule applies


def test_steep_wire_is_a_service_drop_not_scored(cfg):
    from app.pipeline.runner import EngineeringContext, score_wire
    from app.pipeline.segment import WireDetection, _bbox
    m = np.zeros((800, 800), np.uint8)
    cv2.line(m, (300, 40), (520, 700), 1, 3)          # about 72 deg from horizontal
    det = WireDetection(P, 0.9, m.astype(bool), _bbox(m.astype(bool)))
    on = cfg.model_copy(update={"crs": cfg.crs.model_copy(update={"max_span_inclination_deg": 35.0})})
    w = score_wire(det, 1, (800, 800), on, EngineeringContext())
    assert w.status == "service_drop" and w.crs is None and w.fit["inclination_deg"] > 60
    off = cfg.model_copy(update={"crs": cfg.crs.model_copy(update={"max_span_inclination_deg": 0.0})})
    assert score_wire(det, 1, (800, 800), off, EngineeringContext()).status != "service_drop"


def test_short_steep_fragment_is_too_short_not_a_drop(cfg):
    from app.pipeline.runner import EngineeringContext, score_wire
    from app.pipeline.segment import WireDetection, _bbox
    m = np.zeros((800, 800), np.uint8)
    cv2.line(m, (300, 600), (320, 680), 1, 3)          # steep, ~82 px long
    det = WireDetection(P, 0.9, m.astype(bool), _bbox(m.astype(bool)))
    on = cfg.model_copy(update={"crs": cfg.crs.model_copy(update={"max_span_inclination_deg": 35.0, "min_chord_px": 100.0})})
    w = score_wire(det, 1, (800, 800), on, EngineeringContext())
    assert w.status == "too_short" and w.fail_reason == "chord_below_precision_gate"


def test_corroboration_k_is_a_binomial_test():
    from scipy.stats import binom
    for n, p0 in ((1, 0.03), (2, 0.058), (22, 0.058), (40, 0.1)):
        k = corroboration_k(n, p0, 0.05)
        assert binom.sf(k - 1, n, p0) <= 0.05 and (k == 1 or binom.sf(k - 2, n, p0) > 0.05)
    assert corroboration_k(1, 0.2, 0.05) == 2  # one span at a high noise rate is never significant


def _wire(y, x0=20, x1=780):
    m = np.zeros(SHAPE, np.uint8)
    cv2.line(m, (x0, y), (x1, y + 10), 1, 3)
    return m.astype(bool)


def _rows(levels, cls=P):
    return [{"index": i + 1, "class": cls, "mask": _wire(20 + 15 * i), "level": lv, "status": "scored"}
            for i, lv in enumerate(levels)]


RULE = HazardConfig(enabled=True, sag_rule="corroborated", p0_high=0.058, p0_medium=0.10)


def test_single_outlier_span_does_not_saturate_the_pole():
    rows = _rows(["high"] + ["low"] * 19)
    assert assess(rows, [], SHAPE).verdict == HIGH                       # v2.8 rule
    hz = assess(rows, [], SHAPE, rule=RULE)
    assert hz.verdict == NOT and any(r.startswith("NOTE: span 1 in the high CRS band (1 of 20") for r in hz.reasons)
    assert "none corroborated" in next(r for r in hz.reasons if r.startswith("NOT AT RISK"))


def test_corroborated_sag_raises_the_verdict():
    k = corroboration_k(20, 0.058, 0.05)
    hz = assess(_rows(["high"] * k + ["low"] * (20 - k)), [], SHAPE, rule=RULE)
    assert hz.verdict == HIGH and hz.factors["sag"]["k_high"] == k
    km = corroboration_k(20, 0.10, 0.05)
    assert assess(_rows(["medium"] * km + ["low"] * (20 - km)), [], SHAPE, rule=RULE).verdict == MODERATE
    assert assess(_rows(["high"]), [], SHAPE, rule=RULE.model_copy(update={"p0_high": 0.03})).verdict == HIGH


def test_vegetation_contact_is_absolute_under_the_corroborated_rule():
    veg = np.zeros(SHAPE, bool)
    veg[0:30, 300:400] = True
    assert assess(_rows(["low"] * 10), [veg], SHAPE, rule=RULE).verdict == HIGH


def test_low_hanging_service_drop_is_moderate():
    drop = np.zeros(SHAPE, np.uint8)
    cv2.line(drop, (400, 20), (460, 390), 1, 3)
    rows = [{"index": 1, "class": T, "mask": drop.astype(bool), "level": None, "status": "service_drop"}]
    hz = assess(rows, [], SHAPE, rule=RULE.model_copy(update={"low_frame_frac": 0.15}))
    assert hz.verdict == MODERATE and "service drop 1 reaches" in hz.reasons[0]
    assert assess(rows, [], SHAPE, rule=RULE).verdict == NOT
    assert "1 steep service drop" in assess(rows, [], SHAPE, rule=RULE).reasons[-1]


@pytest.mark.parametrize("q,unc,needle", [
    ({"clipped_high": 0.95, "mean_l": 98}, {"wire_pixels": 5000, "entropy": 0.1, "disagreement": 0.1}, "glare washed out"),
    ({"clipped_high": 0.0, "mean_l": 3}, {"wire_pixels": 5000, "entropy": 0.1, "disagreement": 0.1}, "too dark"),
    ({"clipped_high": 0.0, "mean_l": 50, "sharpness": 10, "motion_coherence": 0.4},
     {"wire_pixels": 5000, "entropy": 0.4, "disagreement": 0.4}, "motion blur"),
    ({"clipped_high": 0.0, "mean_l": 50, "sharpness": 10, "motion_coherence": 0.02},
     {"wire_pixels": 5000, "entropy": 0.4, "disagreement": 0.4}, "out of focus"),
    ({"clipped_high": 0.0, "mean_l": 50}, {"wire_pixels": 10, "entropy": 0.1, "disagreement": 0.1}, "Almost no wire"),
])
def test_reliability_reason_names_the_cause(q, unc, needle):
    from app.pipeline.quality import grade
    g = grade(unc, q, QualityConfig(), EnhancementConfig())
    assert g["grade"] != "A" and needle in g["reason"]


def test_grade_a_has_no_reason():
    from app.pipeline.quality import grade
    g = grade({"wire_pixels": 5000, "entropy": 0.05, "disagreement": 0.0}, {"mean_l": 50, "clipped_high": 0.0}, QualityConfig())
    assert g["grade"] == "A" and g["reason"] is None


def test_diagnostic_marks_drops_and_prints_reasons(scene):
    from app.services.diagnostic import render_diagnostic
    span = {"index": 1, "class": P, "mask_polygons": [[[10, 10], [500, 12], [500, 16], [10, 14]]],
            "catenary_curve": [], "level": "high", "crs": 8.0, "status": "scored"}
    drop = {"index": 2, "class": T, "mask_polygons": [[[300, 20], [306, 20], [340, 300], [334, 300]]],
            "catenary_curve": [], "level": None, "crs": None, "status": "service_drop"}
    res = {"wires": [span, drop], "vegetation": [],
           "hazard": {"verdict": MODERATE, "reasons": ["AT RISK - MODERATE: something long " * 6, "NOTE: x"],
                      "factors": {}},
           "quality": {"grade": "Retake recommended", "reliability": 0.3,
                       "reason": "Severe sky glare washed out the overhead conductors"}}
    img = render_diagnostic(scene.rgb, res)
    bare = render_diagnostic(scene.rgb, {**res, "hazard": {**res["hazard"], "reasons": []}})
    assert img.shape[0] > bare.shape[0] and img.shape[1] == bare.shape[1]  # reasons strip below the panels


def test_database_gains_the_service_drop_status(tmp_path):
    import sqlite3

    from sqlalchemy import text

    from app.core.database import Database
    p = tmp_path / "v28.db"
    db = Database(f"sqlite:///{p}")
    db.create_all()
    con = sqlite3.connect(p)  # make the constraint look like v2.8's
    ddl = con.execute("SELECT sql FROM sqlite_master WHERE name='detections'").fetchone()[0]
    assert "'service_drop'" in ddl
    con.executescript("PRAGMA foreign_keys=OFF; ALTER TABLE detections RENAME TO d_old;"
                      + ddl.replace(", 'service_drop'", "") + "; DROP TABLE d_old;")
    con.commit()
    con.close()
    db2 = Database(f"sqlite:///{p}")
    db2.create_all()
    with db2.engine.connect() as c:
        assert "'service_drop'" in c.execute(text("SELECT sql FROM sqlite_master WHERE name='detections'")).scalar()
