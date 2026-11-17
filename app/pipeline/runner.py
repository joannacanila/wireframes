"""Pipeline orchestration for one image (Figure 3.4: Algorithms 1 to 5).

Pure with respect to storage: takes decoded inputs, returns an ``AnalysisOutcome``.
Persistence, queueing and MSPI ranking across poles (Algorithm 6) live in services.

Terminal statuses (Figure 3.6 / 3.7):
    complete           at least one wire received a CRS
    no_wires_detected  no wire above the confidence threshold
    fit_failed         wires were detected but none could be scored

Vegetation detections (revised taxonomy) are separated before Algorithm 4: they are never
fitted or scored and never count as wires. They are returned as regions, and each wire they
touch carries the flag ``vegetation_contact``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import cv2
import numpy as np

from app.core.config import VEGETATION, AnalysisConfig, VegetationConfig
from app.pipeline.catenary import fit_catenary
from app.pipeline.crs import catenary_risk_score, risk_level
from app.pipeline.preprocess import Letterbox, preprocess
from app.pipeline.sag_limits import EngineeringContext, allowable_sag
from app.pipeline.segment import WireDetection, _bbox, mask_to_polygons
from app.pipeline.skeleton import extract_centerline
from app.pipeline.vni import compute_vni


class Segmenter(Protocol):
    source: str

    def segment(self, x_rgb: np.ndarray, lb: Letterbox) -> list[WireDetection]: ...


@dataclass
class WireOutcome:
    index: int
    wire_class: str
    confidence: float
    bbox: tuple[float, float, float, float]
    polygons: list[list[list[int]]]
    status: str  # scored | too_short | fit_failed | service_drop
    fail_reason: str | None
    centerline: list[tuple[float, float]]
    curve: list[tuple[float, float]]
    fit: dict[str, Any]
    delta_lim: float | None
    delta_lim0: float | None
    crs: float | None
    level: str | None
    flags: list[str]


@dataclass
class VegetationRegion:
    confidence: float
    bbox: tuple[float, float, float, float]
    polygons: list[list[list[int]]]
    touches: list[int]  # indices of the wires it is in contact with


@dataclass
class AnalysisOutcome:
    status: str
    vni_raw: float
    vni: float
    vni_band: str
    vni_clipped: bool
    wires: list[WireOutcome]
    flags: list[str]
    segmentation_source: str
    timings_ms: dict[str, float] = field(default_factory=dict)
    input_quality: dict | None = None  # measured before enhancement (segmentation path)
    enhancement: list[str] | None = None
    vegetation: list[VegetationRegion] = field(default_factory=list)
    hazard: dict | None = None  # v2.8 explainable verdict (app/pipeline/hazard.py)
    quality: dict | None = None  # v2.8 reliability grade (app/pipeline/quality.py)
    # v2.11: WireNet's full per-pixel class map (0 = background, i = CLASSES[i - 1]) at the original
    # resolution, and how much of it the extracted spans cover; None in annotation mode
    wire_mask: np.ndarray | None = field(default=None, repr=False)
    mask_stats: dict | None = None
    clearance: dict | None = None  # v2.11: {span index: {"gap_px", "gap_m_approx"}} to the nearest vegetation
    suppressed: list[dict] = field(default_factory=list)  # v2.13: spans removed by the ground gate, with reasons
    scene: dict | None = None  # v2.16: scene-parser context (pole regions); never creates or joins a wire
    guardrail: dict | None = None  # v2.17: retake guardrail (app/pipeline/guardrail.py); never changes a result

    @property
    def valid_crs(self) -> list[float]:
        return [w.crs for w in self.wires if w.crs is not None]


def _thin(points: np.ndarray, max_points: int = 400) -> list[tuple[float, float]]:
    """Subsample a centerline for transport; the fit always uses every point."""
    if len(points) <= max_points:
        idx = np.arange(len(points))
    else:
        idx = np.unique(np.linspace(0, len(points) - 1, max_points).round().astype(int))
    return [(round(float(points[i, 0]), 1), round(float(points[i, 1]), 1)) for i in idx]


def chord_inclination_deg(points) -> float:
    """Angle of the centreline's end-to-end chord from the image horizontal, 0-90 deg."""
    if points is None or len(points) < 2:
        return 0.0
    (x0, y0), (x1, y1) = points[0], points[-1]
    return float(np.degrees(np.arctan2(abs(y1 - y0), abs(x1 - x0))))


def score_wire(
    det: WireDetection, index: int, image_size: tuple[int, int], cfg: AnalysisConfig, ctx: EngineeringContext,
    gate_scale: float = 1.0,
) -> WireOutcome:
    """Algorithms 4-5 for one span, plus (v2.16) its strand-level class evidence: ``fit["class_evidence"]`` holds the
    normalised class shares and an ``uncertain`` strand carries the ``class_uncertain`` flag."""
    w = _score_wire(det, index, image_size, cfg, ctx, gate_scale)
    share = getattr(det, "class_share", None)
    if share:
        w.fit = {**w.fit, "class_evidence": share}
    if getattr(det, "class_status", "confident") == "uncertain" and "class_uncertain" not in w.flags:
        w.flags = [*w.flags, "class_uncertain"]
    return w


def _score_wire(
    det: WireDetection, index: int, image_size: tuple[int, int], cfg: AnalysisConfig, ctx: EngineeringContext,
    gate_scale: float = 1.0,
) -> WireOutcome:
    # v2.16: the precision gate L >= sigma / (f * delta_lim) assumes sigma = 1 px of skeleton noise. Model masks are
    # predicted at segmentation.imgsz and resampled to the photo, so on a larger photo one model pixel is gate_scale
    # photo pixels and sigma (and the gate) scale with it. Reference polygons are drawn at the photo's resolution.
    min_chord = cfg.crs.min_chord_px * max(gate_scale, 1.0)
    cl = extract_centerline(det.mask, cfg.centerline.l_min_px, cfg.centerline.n_min_points)
    base = dict(
        index=index, wire_class=det.wire_class, confidence=round(det.score, 4),
        bbox=tuple(round(v, 1) for v in det.bbox), polygons=mask_to_polygons(det.mask),
        centerline=_thin(cl.points), curve=[], fit={}, delta_lim=None, delta_lim0=None,
        crs=None, level=None,
    )
    if not cl.ok:
        return WireOutcome(**base, status="too_short", fail_reason=cl.reason or "too_short", flags=["too_short_to_fit"])
    incl = chord_inclination_deg(cl.points)
    end_to_end = float(np.hypot(*(np.asarray(cl.points[-1]) - np.asarray(cl.points[0]))))
    if cfg.crs.max_span_inclination_deg and incl > cfg.crs.max_span_inclination_deg:
        if cfg.crs.min_chord_px and end_to_end < min_chord:
            # below the measurement-precision gate a fragment is neither a span nor a drop
            return WireOutcome(**{**base, "fit": {"inclination_deg": round(incl, 1), "chord_px": round(end_to_end, 1)}},
                               status="too_short", fail_reason="chord_below_precision_gate",
                               flags=["chord_below_precision_gate"])
        # v2.9: a steep chord is a service drop or guy wire, not a pole-to-pole span; Chapter III's
        # allowable sag ratio is defined for spans, so no CRS (vegetation rules still apply)
        return WireOutcome(**{**base, "fit": {"inclination_deg": round(incl, 1)}}, status="service_drop",
                           fail_reason=None, flags=["service_drop"])

    fit = fit_catenary(
        cl.points, image_size=image_size,
        max_rel_residual=cfg.catenary.max_rel_residual,
        border_margin_frac=cfg.catenary.border_margin_frac,
        max_function_evals=cfg.catenary.max_function_evals,
    )
    flags = ["truncated"] if fit.truncated else []
    fit_info = {
        k: getattr(fit, k)
        for k in ("a", "b", "c", "rms_residual_px", "rel_residual", "chord_px", "sag_px",
                  "sag_ratio", "sag_point", "chord_foot", "endpoints", "n_points", "iterations")
    }
    fit_info["pruned_px"] = cl.pruned_pixels
    fit_info["inclination_deg"] = round(incl, 1)
    if not fit.ok:
        return WireOutcome(**{**base, "fit": fit_info}, status="fit_failed", fail_reason=fit.reason,
                           flags=flags + [f"fit_failed:{fit.reason}"])
    fit_info["chord_gate_px"] = round(min_chord, 1)
    if cfg.crs.min_chord_px and fit.chord_px < min_chord:
        # too short to measure sag reliably: 1 px of skeleton noise would move delta/L by more
        # than the configured share of the allowable sag ratio (config crs.min_chord_px)
        return WireOutcome(**{**base, "fit": fit_info}, status="too_short", fail_reason="chord_below_precision_gate",
                           flags=flags + ["chord_below_precision_gate"])

    # v2.21 measurement validity: measured, but no severity, when the span or its class cannot support one
    if cfg.crs.require_both_endpoints and fit.truncated:
        # the visible part's sag is kept as an unverified review cue (never a finding): a partial span that already
        # sags into the Minor or Critical band must not let the pole read as low risk
        if not cfg.crs.assessed_classes or det.wire_class in cfg.crs.assessed_classes:
            lim = allowable_sag(det.wire_class, ctx, cfg.crs, cfg.engineering)
            fit_info["unverified_level"] = risk_level(
                catenary_risk_score(fit.sag_ratio, lim.delta_lim, cfg.crs.class_weights[det.wire_class]), cfg.crs.levels)
        return WireOutcome(**{**base, "fit": fit_info}, status="not_assessed", fail_reason="endpoint_unavailable",
                           flags=flags + ["endpoint_unavailable"])
    if getattr(det, "class_status", "confident") == "uncertain" and cfg.crs.assessed_classes:
        return WireOutcome(**{**base, "fit": fit_info}, status="not_assessed", fail_reason="class_uncertain",
                           flags=flags + ["class_uncertain"])
    if cfg.crs.assessed_classes and det.wire_class not in cfg.crs.assessed_classes:
        return WireOutcome(**{**base, "fit": fit_info}, status="not_assessed", fail_reason="no_reference_for_class",
                           flags=flags + ["no_reference_for_class"])

    limit = allowable_sag(det.wire_class, ctx, cfg.crs, cfg.engineering)
    w_class = cfg.crs.class_weights[det.wire_class]
    crs = catenary_risk_score(fit.sag_ratio, limit.delta_lim, w_class)
    return WireOutcome(**{
        **base, "curve": fit.curve(96), "fit": fit_info,
        "delta_lim": limit.delta_lim, "delta_lim0": limit.delta_lim0,
        "crs": round(crs, 3), "level": risk_level(crs, cfg.crs.levels),
        "status": "scored", "fail_reason": None, "flags": flags + list(limit.flags),
    })


def vegetation_contacts(wire_masks: list[np.ndarray], veg_masks: list[np.ndarray], image_size: tuple[int, int],
                        cfg: VegetationConfig) -> list[list[int]]:
    """For each vegetation mask, the positions in ``wire_masks`` it touches: wires with at least
    ``min_contact_share`` of their pixels within ``contact_margin_frac`` x the longer image side
    of the vegetation mask."""
    r = int(round(cfg.contact_margin_frac * max(image_size)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)) if r else None
    out = []
    for v in veg_masks:
        zone = cv2.dilate(v.astype(np.uint8), kernel).astype(bool) if kernel is not None else v
        out.append([i for i, m in enumerate(wire_masks)
                    if m.any() and (m & zone).sum() / m.sum() >= cfg.min_contact_share])
    return out


def _mask_iou(a: WireDetection, b: WireDetection) -> float:
    ax1, ay1, ax2, ay2 = a.bbox
    bx1, by1, bx2, by2 = b.bbox
    if ax2 <= bx1 or bx2 <= ax1 or ay2 <= by1 or by2 <= ay1:
        return 0.0
    inter = np.logical_and(a.mask, b.mask).sum()
    return float(inter / max(1, np.logical_or(a.mask, b.mask).sum()))


def merge_detections(dets: list[WireDetection], iou: float = 0.5) -> list[WireDetection]:
    """Greedy per-class mask NMS: of overlapping candidates the higher-confidence one is kept."""
    kept: list[WireDetection] = []
    for d in sorted(dets, key=lambda d: -d.score):
        if all(k.wire_class != d.wire_class or _mask_iou(k, d) <= iou for k in kept):
            kept.append(d)
    return kept


def _unflip(d: WireDetection) -> WireDetection:
    m = np.ascontiguousarray(d.mask[:, ::-1])
    return WireDetection(d.wire_class, d.score, m, _bbox(m))


def _degraded(quality: dict | None, rgb: np.ndarray, cfg) -> bool:
    """Blurry, low-contrast, dark or washed out by the enhancement thresholds."""
    from app.pipeline.enhance import Quality, decide, measure
    from app.pipeline.preprocess import to_working_scale

    q = Quality(**quality) if quality else measure(to_working_scale(rgb, cfg.working_long_side))
    return bool({"clahe", "unsharp", "motion_smooth", "motion_skip"} & set(decide(q, cfg)))


def _measured(w: WireOutcome) -> bool:
    """A wire the pipeline could measure: scored, or a service drop (classified, not a failure).
    Coarse fragments that are simply too steep are counted as measured so the v2.9 drop split
    does not change when the blur fallback fires."""
    return w.crs is not None or w.status == "service_drop"


def _broken(wires: list[WireOutcome], share: float) -> bool:
    return not wires or sum(not _measured(w) for w in wires) / len(wires) >= share


def finalize_detections(rgb: np.ndarray, pre, found: list[WireDetection], cfg: AnalysisConfig, segmenter,
                        ctx: EngineeringContext, allow_fallback: bool = True):
    """Split off vegetation, score the wires and, for a degraded photo whose first pass gave no or
    broken spans, run the blur fallback. Returns (vegetation, wire detections, wire outcomes,
    flags). Shared by run_analysis and ml.evaluate, so evaluation measures what the screener does."""
    h, w = rgb.shape[:2]
    gate_scale = max(w, h) / cfg.segmentation.imgsz if allow_fallback else 1.0  # model path only (v2.16)

    def score_all(dets_: list[WireDetection]):
        veg_ = [d for d in dets_ if d.wire_class == VEGETATION]
        wire_dets = sorted((d for d in dets_ if d.wire_class != VEGETATION),
                           key=lambda d: (d.bbox[1], d.bbox[0]))  # stable, top-to-bottom numbering
        return veg_, wire_dets, [score_wire(d, i + 1, (w, h), cfg, ctx, gate_scale) for i, d in enumerate(wire_dets)]

    veg, dets, wires = score_all(found)
    flags: list[str] = []
    e = cfg.enhancement
    if allow_fallback and e.fallback and _broken(wires, e.fallback_unscored_share) and _degraded(pre.quality, rgb, e):
        # Blur fallback: stronger contrast + sharpening, plus a mirrored pass (Ultralytics'
        # augment=True is a no-op for segmentation heads), merged by mask NMS.
        flags.append("fallback_pass")
        size = cfg.segmentation.imgsz
        strong = preprocess(rgb, cfg.preprocessing, e, size, strong=True)
        extra = list(segmenter.segment(strong.x_rgb, strong.letterbox))
        if e.fallback_tta_hflip:
            mirrored = preprocess(np.ascontiguousarray(rgb[:, ::-1]), cfg.preprocessing, e, size, strong=True)
            extra += [_unflip(d) for d in segmenter.segment(mirrored.x_rgb, mirrored.letterbox)]
        veg2, dets2, wires2 = score_all(merge_detections(list(veg) + list(dets) + extra, cfg.segmentation.iou_nms))
        if sum(map(_measured, wires2)) > sum(map(_measured, wires)):
            veg, dets, wires = veg2, dets2, wires2
            flags.append("fallback_used")
    return veg, dets, wires, flags


def _mask_summary(class_masks: dict | None, dets, shape) -> tuple[np.ndarray | None, dict | None]:
    """Label map of WireNet's class masks, its pixel counts per class, and the share of detected wire
    pixels that ended up inside an extracted span (the rest are fragments too short or too broken to
    become a span; they are shown in the UI's full-mask layer, never silently dropped)."""
    if not class_masks:
        return None, None
    from app.core.config import CLASSES, VEGETATION
    label = np.zeros(shape, np.uint8)
    for c, m in sorted(class_masks.items()):
        label[m] = c
    wire_ids = [CLASSES.index(c) + 1 for c in CLASSES if c != VEGETATION]
    wire = np.isin(label, wire_ids)
    spans = np.zeros(shape, bool)
    for d in dets:
        if d.wire_class != VEGETATION:
            spans |= d.mask
    n_wire = int(wire.sum())
    return label, {"pixels": {CLASSES[c - 1]: int((label == c).sum()) for c in range(1, len(CLASSES) + 1)},
                   "wire_pixels": n_wire, "wire_pixels_in_spans": int((wire & spans).sum()),
                   "span_coverage": round(float((wire & spans).sum()) / n_wire, 4) if n_wire else None}


def wide_lens(envelope: list[dict] | None) -> tuple[float, float, float] | None:
    """(focal 35 mm eq., envelope min, max) when the EXIF focal length failed the envelope *below* its minimum."""
    for c in envelope or []:
        if c.get("name") == "focal_length_35mm" and c.get("status") == "fail" and c.get("value") is not None:
            lo, hi = (c.get("expected") or [None, None])[:2]
            if lo is not None and c["value"] < lo:
                return float(c["value"]), float(lo), float(hi)
    return None


def _reliable(hazard, quality):
    from app.pipeline.hazard import apply_reliability
    return apply_reliability(hazard, quality)


def _quality(cfg: AnalysisConfig, segmenter, pre, detections) -> dict | None:
    if not cfg.quality.enabled or detections is not None:
        return None
    from app.pipeline.enhance import measure
    from app.pipeline.quality import grade
    q = pre.quality
    if q is None:  # enhancement off: measure anyway, for the exposure check
        q = measure((pre.x_rgb * 255).astype(np.uint8)).as_dict()
    q = {**q, "image_min_side": int(min(pre.letterbox.orig_w, pre.letterbox.orig_h))}
    return grade(getattr(segmenter, "last_uncertainty", None), q, cfg.quality, cfg.enhancement)


def run_analysis(
    rgb: np.ndarray,
    cfg: AnalysisConfig,
    segmenter: Segmenter,
    ctx: EngineeringContext | None = None,
    detections: list[WireDetection] | None = None,
    envelope: list[dict] | None = None,
) -> AnalysisOutcome:
    """Run Algorithms 1-5. ``detections`` bypasses the segmenter (reference annotations).

    v2.16 ``envelope``: the acquisition checks (Section 3.3.1). When the 35 mm-equivalent focal length is below the
    envelope (an ultra-wide lens), barrel distortion bends straight wires, so a sag measured in the image is not the
    wire's sag: CRS values are still computed and stored (audit), every scored wire is flagged
    ``lens_outside_envelope``, and sag does not raise the verdict (vegetation rules still apply)."""
    ctx = ctx or EngineeringContext()
    t: dict[str, float] = {}
    t0 = time.perf_counter()
    # Enhancement serves segmentation only: skipped when reference masks replace the model.
    model_size = cfg.segmentation.imgsz
    pre = preprocess(rgb, cfg.preprocessing, None if detections is not None else cfg.enhancement, model_size)
    t["preprocess"] = (time.perf_counter() - t0) * 1e3

    t0 = time.perf_counter()
    v = compute_vni(pre.gray, cfg.vni)
    t["vni"] = (time.perf_counter() - t0) * 1e3

    t0 = time.perf_counter()
    dets = detections if detections is not None else segmenter.segment(pre.x_rgb, pre.letterbox)
    t["segmentation"] = (time.perf_counter() - t0) * 1e3
    class_masks = getattr(segmenter, "last_class_masks", None) if detections is None else None  # first pass
    suppressed = list(getattr(segmenter, "last_suppressed", None) or []) if detections is None else []
    polarity = getattr(segmenter, "last_polarity", None) if detections is None else None

    flags: list[str] = []
    if cfg.vni.provisional:
        flags.append("vni_uncalibrated")
    if v.clipped:
        flags.append("vni_outside_calibration_range")
    if cfg.segmentation.conf_provisional and detections is None:
        flags.append("confidence_threshold_provisional")
    if pre.enhancement:
        flags.extend(f"enhanced:{s}" for s in pre.enhancement)
    if polarity == "inverted":
        flags.append("polarity_inverted")  # v2.11: night photo, light conductors on a dark sky
    if any(s.get("unavailable") for s in suppressed):
        flags.append("ground_gate_unavailable")  # v2.13: scene parser could not load; every span kept
        suppressed = []
    elif suppressed:
        codes = {x.get("reason_code") for x in suppressed}
        if "ground_structure" in codes:
            flags.append("ground_structures_suppressed")
        if "structure_edge" in codes:
            flags.append("structure_edges_suppressed")    # v2.16
        if "not_vegetation" in codes:
            flags.append("non_vegetation_suppressed")     # v2.16
    trained = cfg.segmentation.trained_enhancement
    if detections is None and trained is not None and trained != cfg.enhancement.mode:
        flags.append("enhancement_mismatch")  # inference preprocessing differs from training

    h, w = rgb.shape[:2]
    t0 = time.perf_counter()
    veg, dets, wires, fb_flags = finalize_detections(rgb, pre, dets, cfg, segmenter, ctx,
                                                     allow_fallback=detections is None)
    flags.extend(fb_flags)
    poles = getattr(segmenter, "last_poles", None) if detections is None else None
    if poles:
        from app.pipeline.poles import ends_at_pole
        margin = 0.01 * max(h, w)
        for wo in wires:
            if wo.fit.get("endpoints"):
                wo.fit = {**wo.fit, "pole_ends": ends_at_pole(wo.fit["endpoints"], poles, margin)}
    touches = vegetation_contacts([d.mask for d in dets], [v.mask for v in veg], (w, h), cfg.vegetation)
    for t_list in touches:
        for i in t_list:
            if "vegetation_contact" not in wires[i].flags:
                wires[i].flags.append("vegetation_contact")
    regions = [VegetationRegion(round(v.score, 4), tuple(round(x, 1) for x in v.bbox), mask_to_polygons(v.mask),
                                [wires[i].index for i in t_list]) for v, t_list in zip(veg, touches, strict=True)]
    if any(r.touches for r in regions):
        flags.append("vegetation_contact")
    hazard, clearance = None, None
    if cfg.hazard.enabled:
        from app.pipeline.hazard import assess
        from app.pipeline.loose_ends import loose_ends
        wide = wide_lens(envelope)
        if wide is not None:
            flags.append("lens_outside_envelope")
            for wo in wires:
                if wo.status == "scored" and "lens_outside_envelope" not in wo.flags:
                    wo.flags.append("lens_outside_envelope")
        rows = [{"index": wo.index, "class": wo.wire_class, "mask": d.mask,
                 "level": None if wide is not None else wo.level, "status": wo.status, "flags": list(wo.flags),
                 "unverified_level": None if wide is not None else (wo.fit or {}).get("unverified_level")}
                for wo, d in zip(wires, dets, strict=True)]
        cond = cfg.engineering.conductors.get("power-distribution-lines")
        content_rows = np.flatnonzero(~(np.abs(rgb.astype(np.int16) - cfg.preprocessing.letterbox_fill) <= 3)
                                      .all(axis=2).all(axis=1))
        hz = assess(rows, [v.mask for v in veg], (h, w), cond.diameter_m if cond else 0.0101,
                    cfg.hazard.veg_moderate_px, rule=cfg.hazard,
                    frame_rows=(int(content_rows[0]), int(content_rows[-1])) if len(content_rows) else (0, h - 1))
        if cfg.hazard.loose_ends:
            content = ~((np.abs(rgb.astype(np.int16) - cfg.preprocessing.letterbox_fill) <= 6).all(axis=2))
            le = [e for e in loose_ends([d.mask for d in dets], content, cfg.hazard.border_px, cfg.hazard.isolation_px,
                                        cfg.hazard.cluster_px, cfg.hazard.cluster_n) if e.score >= cfg.hazard.loose_end_tau]
            hz.factors["loose_ends"] = [{"span": wires[e.span].index, "y": round(e.y, 1), "x": round(e.x, 1),
                                         "score": round(e.score, 3), "downward": round(e.downward, 3)} for e in le]
            if le:
                flags.append("loose_end_candidate")
                hz.reasons.append("INFO: possible loose / dangling wire end on span"
                                  f"{'s' if len(le) > 1 else ''} {', '.join(str(wires[e.span].index) for e in le)} "
                                  "(geometric candidate; verify on site)")
        if wide is not None:
            from app.pipeline.hazard import INCONCLUSIVE, NOT
            note = (f"sag not assessed: the photo was taken with an ultra-wide lens ({wide[0]:g} mm equivalent, outside "
                    f"the {wide[1]:g}-{wide[2]:g} mm capture envelope), which bends straight wires; retake with the "
                    "main (1x) camera")
            if hz.verdict == NOT:
                hz.verdict = INCONCLUSIVE
                hz.reasons.insert(0, f"{INCONCLUSIVE}: {note}")
            else:
                hz.reasons.append(f"NOTE: {note}")
        hazard = hz.as_dict()
        clearance = getattr(hz, "clearance", None)
    t["geometry_and_risk"] = (time.perf_counter() - t0) * 1e3

    if not wires:
        status = "no_wires_detected"
    elif not any(x.crs is not None or x.status == "service_drop" for x in wires):
        status = "fit_failed"
    else:
        status = "complete"
    wire_mask, mask_stats = _mask_summary(class_masks, dets, rgb.shape[:2])
    outcome = AnalysisOutcome(
        wire_mask=wire_mask, mask_stats=mask_stats, clearance=clearance, suppressed=suppressed,
        scene=None if detections is not None else (
            {**(getattr(segmenter, "last_scene", None) or {}), **({"poles_detected": poles} if poles is not None else {})}
            or None),
        status=status, vni_raw=v.vni_raw, vni=v.vni, vni_band=v.band, vni_clipped=v.clipped,
        wires=wires, flags=flags, segmentation_source="annotation" if detections is not None else segmenter.source,
        timings_ms={k: round(x, 1) for k, x in t.items()},
        input_quality=pre.quality, enhancement=pre.enhancement, vegetation=regions,
        hazard=_reliable(hazard, quality := _quality(cfg, segmenter, pre, detections)), quality=quality,
    )
    if cfg.guardrail.enabled and detections is None:
        from app.pipeline.guardrail import assess as guard
        outcome.guardrail = guard(outcome, cfg.guardrail)
    return outcome
