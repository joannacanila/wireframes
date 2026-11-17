"""Explainable hazard verdict (v2.8): an interpretation layer *on top of* Chapter III's CRS.

Nothing here changes Equations 1-11. The verdict combines:

* Sag: the CRS level (Table 1.2) of spans whose chord passed the measurement-precision gate
  (``crs.min_chord_px``, see config). High -> AT RISK - HIGH, Medium -> AT RISK - MODERATE.
* Vegetation encroachment: the minimum pixel gap between each Vegetation mask and each
  power / telecom span (Euclidean distance transform of the vegetation mask):
      power-distribution-lines in contact (gap 0)      -> AT RISK - HIGH
      power within ``veg_moderate_px`` (default 15)    -> AT RISK - MODERATE
      telecom-cable in contact                         -> AT RISK - MODERATE
  (the rules the team specified; they are fixed, not tuned to outputs)
* Congestion ("spaghetti") index: spans and junction crossings per megapixel of image. Reported
  for information; it does not move the verdict, because no reference rating exists to
  calibrate a threshold against.
* v2.9 service drops (status "service_drop", chord steeper than crs.max_span_inclination_deg)
  get no sag verdict. They are checked for vegetation like every wire and, if
  ``low_frame_frac`` > 0, for reaching the lowest part of the frame (a framing proxy for a
  low-hanging drop over a footpath; no height is measured, so it only asks for a site check).
* v2.9 pole-level aggregation (``sag_rule="corroborated"``). Under "any" (v2.8) one span in a
  band sets the verdict, so with ~30 spans per pole a small per-span false-alarm rate p
  saturates the verdict (1 - (1 - p)^n). Under "corroborated" the number of spans in the band
  must be larger than the per-span test-retest false-alarm rate p0 explains: with n measurable
  spans, k*(n) = min{k : P(Bin(n, p0) >= k) <= alpha}; HIGH needs >= k*(n) high spans
  (p0 = p0_high), MODERATE needs >= k*(n) spans at medium or above (p0 = p0_medium). p0 is
  measured on validation (a photo and its mirror image as two measurements); alpha = 0.05 is
  the usual significance level, not tuned. Spans in a band that are not corroborated are listed
  as a NOTE, never dropped silently.
* Approximate clearance: pixel gaps converted with scale = conductor diameter (config,
  engineering.conductors) / median measured power-wire width, with the +-1 px width
  uncertainty propagated as a range. At 2-3 px wire widths the range is wide; it is labelled
  approximate everywhere it is shown.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from functools import lru_cache

import numpy as np
from scipy import ndimage as ndi

HIGH, MODERATE, NOT = "AT RISK - HIGH", "AT RISK - MODERATE", "NOT AT RISK"
INCONCLUSIVE = "INCONCLUSIVE - RETAKE PHOTO"
_NOT_ASSESSED = {"endpoint_unavailable": "run off the photo (endpoint not visible)",
                 "class_uncertain": "of uncertain type", "no_reference_for_class": "power or abandoned wires without a sag reference"}
_RANK = {NOT: 0, MODERATE: 1, HIGH: 2}


def apply_reliability(hazard: dict | None, quality: dict | None) -> dict | None:
    """An unreadable photo must never read as safe: when the reliability grade is "Retake
    recommended", NOT AT RISK becomes INCONCLUSIVE; a hazard that was found is kept, with a note."""
    if not hazard or not quality or quality.get("grade") != "Retake recommended":
        return hazard
    note = f"photo reliability low ({quality.get('reason') or 'model uncertainty high'}); retake to confirm"
    if hazard["verdict"] == NOT:
        return {**hazard, "verdict": INCONCLUSIVE, "reasons": [f"{INCONCLUSIVE}: {note}"] + hazard["reasons"]}
    return {**hazard, "reasons": hazard["reasons"] + [f"NOTE: {note}"]}
POWER, TELECOM = "power-distribution-lines", "telecom-cable"


@dataclass
class Hazard:
    verdict: str
    reasons: list[str]
    factors: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def mean_width_px(mask: np.ndarray) -> float:
    """Area / skeleton length: the mean stroke width of a thin mask. The skeleton is computed on the
    mask's bounding box (thinning is local, so this equals the full-frame skeleton; v2.10)."""
    from app.pipeline.wire_instances import skeleton_length
    return float(mask.sum()) / max(skeleton_length(mask), 1)


def _pixels(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """np.nonzero(mask), found inside the bounding box (same pixels, same row-major order)."""
    rows, cols = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
    if not len(rows):
        return np.zeros(0, np.intp), np.zeros(0, np.intp)
    ys, xs = np.nonzero(mask[rows[0]: rows[-1] + 1, cols[0]: cols[-1] + 1])
    return ys + rows[0], xs + cols[0]


def clearance_scale(power_masks: list[np.ndarray], diameter_m: float) -> dict | None:
    """Metres per pixel from the median power-wire width, with a +-1 px range."""
    widths = [mean_width_px(m) for m in power_masks if m.sum() >= 30]
    if not widths:
        return None
    w = float(np.median(widths))
    return {"median_wire_width_px": round(w, 2), "assumed_conductor_diameter_m": diameter_m,
            "m_per_px": diameter_m / w, "m_per_px_low": diameter_m / (w + 1.0),
            "m_per_px_high": diameter_m / max(w - 1.0, 0.5)}


def congestion(wire_masks: list[np.ndarray], image_shape: tuple[int, int]) -> dict:
    """Spans and junction crossings per megapixel."""
    from app.pipeline.wire_instances import _NEIGH, _skeleton
    mp = image_shape[0] * image_shape[1] / 1e6
    if not wire_masks:
        return {"spans": 0, "crossings": 0, "spans_per_mp": 0.0, "crossings_per_mp": 0.0}
    union = np.zeros(image_shape, bool)
    for m in wire_masks:
        union |= m
    sk = _skeleton(union)
    nb = ndi.convolve(sk.astype(np.uint8), _NEIGH, mode="constant") - 1
    _, crossings = ndi.label(ndi.binary_dilation(sk & (nb >= 3), iterations=3))
    return {"spans": len(wire_masks), "crossings": int(crossings),
            "spans_per_mp": round(len(wire_masks) / mp, 1), "crossings_per_mp": round(crossings / mp, 1)}


@lru_cache(maxsize=4096)
def corroboration_k(n: int, p0: float, alpha: float) -> int:
    """Smallest k with P(Bin(n, p0) >= k) <= alpha (k = n + 1 if none: never significant)."""
    from scipy.stats import binom
    for k in range(1, n + 1):
        if binom.sf(k - 1, n, p0) <= alpha:
            return k
    return n + 1


def _spans(ix: list[int]) -> str:
    return f"span{'s' if len(ix) > 1 else ''} {', '.join(map(str, ix))}"


def assess(wires: list[dict], veg_masks: list[np.ndarray], image_shape: tuple[int, int],
           conductor_diameter_m: float = 0.0101, veg_moderate_px: int = 15, rule=None,
           frame_rows: tuple[int, int] | None = None) -> Hazard:
    """wires: [{"index", "class", "mask", "level" (CRS level or None), "status"}].

    ``rule`` is a HazardConfig (sag_rule, p0_high, p0_medium, alpha, low_frame_frac); None keeps
    the v2.8 behaviour. ``frame_rows`` = first and last image rows with photo content."""
    verdict, reasons, f = NOT, [], {}
    sag_rule = getattr(rule, "sag_rule", "any")

    def raise_to(v: str, why: str) -> None:
        nonlocal verdict
        reasons.append(f"{v}: {why}")
        if _RANK[v] > _RANK[verdict]:
            verdict = v

    # sag (Chapter III CRS on measurable spans)
    scored = [w for w in wires if w["level"] is not None]
    too_short = sum(1 for w in wires if w["status"] == "too_short")
    by_level = {lv: [w["index"] for w in scored if w["level"] == lv] for lv in ("low", "medium", "high")}
    drops = [w for w in wires if w["status"] == "service_drop"]
    f["sag"] = {"scored_spans": len(scored), "too_short_spans": too_short, "service_drops": len(drops),
                "rule": sag_rule, **{k: v for k, v in by_level.items()}}
    notes: list[str] = []
    if sag_rule == "any":
        if by_level["high"]:
            raise_to(HIGH, f"sag above the high CRS band on {_spans(by_level['high'])}")
        elif by_level["medium"]:
            raise_to(MODERATE, f"sag in the medium CRS band on {_spans(by_level['medium'])}")
    else:
        n = len(scored)
        hi, med_up = by_level["high"], by_level["high"] + by_level["medium"]
        k_h, k_m = corroboration_k(n, rule.p0_high, rule.alpha), corroboration_k(n, rule.p0_medium, rule.alpha)
        f["sag"].update({"k_high": k_h, "k_medium": k_m})
        if len(hi) >= k_h:
            raise_to(HIGH, f"sag above the high CRS band on {len(hi)} of {n} measurable spans "
                           f"({_spans(sorted(hi))}; at least {k_h} needed to exceed measurement noise)")
        elif len(med_up) >= k_m:
            raise_to(MODERATE, f"sag in the medium CRS band or above on {len(med_up)} of {n} measurable spans "
                               f"({_spans(sorted(med_up))}; at least {k_m} needed to exceed measurement noise)")
        elif med_up:
            parts = ([f"{_spans(sorted(hi))} in the high CRS band ({len(hi)} of {n}, {k_h} needed)"] if hi else []) + \
                    ([f"{_spans(sorted(by_level['medium']))} in the medium band"] if by_level["medium"] else [])
            notes.append(f"NOTE: {' and '.join(parts)}; not corroborated ({len(med_up)} of {n} at medium or above, "
                         f"{k_m} needed; consistent with measurement noise); verify visually")
    # service drops: low in the frame (framing proxy, no height measured)
    low_frac = getattr(rule, "low_frame_frac", 0.0)
    if drops and low_frac > 0:
        top, bottom = frame_rows or (0, image_shape[0] - 1)
        cut = bottom - low_frac * (bottom - top + 1)
        low = [w["index"] for w in drops if w["mask"].any() and np.nonzero(w["mask"].any(axis=1))[0][-1] >= cut]
        f["low_drops"] = low
        if low:
            raise_to(MODERATE, f"service drop{'s' if len(low) > 1 else ''} {', '.join(map(str, low))} reach{'' if len(low) > 1 else 'es'} the lowest "
                               f"{low_frac:.0%} of the frame; check ground clearance over the footpath on site")
    # vegetation
    power = [w for w in wires if w["class"] == POWER]
    scale = clearance_scale([w["mask"] for w in power], conductor_diameter_m)
    f["clearance_scale"] = scale
    veg_rows = []
    if veg_masks:
        veg = np.zeros(image_shape, bool)
        for m in veg_masks:
            veg |= m
        dist, (iy, ix) = ndi.distance_transform_edt(~veg, return_indices=True)
        for w in wires:
            if w["class"] not in (POWER, TELECOM) or not w["mask"].any():
                continue
            ys, xs = _pixels(w["mask"])
            k = int(np.argmin(dist[ys, xs]))
            gap = float(dist[ys[k], xs[k]])
            row = {"span": w["index"], "class": w["class"], "gap_px": round(gap, 1),
                   "wire_point": [int(ys[k]), int(xs[k])], "vegetation_point": [int(iy[ys[k], xs[k]]), int(ix[ys[k], xs[k]])]}
            if scale:
                row["gap_m_approx"] = [round(gap * scale["m_per_px_low"], 2), round(gap * scale["m_per_px_high"], 2)]
            veg_rows.append(row)
            if w["class"] == POWER and gap == 0:
                raise_to(HIGH, f"vegetation in contact with power span {w['index']}")
            elif w["class"] == POWER and gap <= veg_moderate_px:
                raise_to(MODERATE, f"vegetation {gap:.0f} px from power span {w['index']}"
                         + (f" (approx. {row['gap_m_approx'][0]}-{row['gap_m_approx'][1]} m)" if scale else ""))
            elif w["class"] == TELECOM and gap == 0:
                raise_to(MODERATE, f"vegetation in contact with telecom span {w['index']}")
    f["vegetation"] = sorted(veg_rows, key=lambda r: r["gap_px"])[:10]
    f["congestion"] = congestion([w["mask"] for w in wires], image_shape)
    if verdict == NOT:
        n_p, n_t = sum(w["class"] == POWER for w in wires), sum(w["class"] == TELECOM for w in wires)
        parts = [f"{n_p} power and {n_t} telecom spans detected", (f"{len(scored)} measurable for sag, none corroborated above the low CRS band" if notes
                  else f"{len(scored)} measurable for sag, all in the low CRS band")
                 if scored else "no span long enough to measure sag"]
        if veg_rows:
            g = veg_rows and min(r["gap_px"] for r in veg_rows)
            parts.append(f"closest vegetation {g:.0f} px from a wire")
        else:
            parts.append("no vegetation near the wires")
        if drops:
            parts.insert(1, f"{len(drops)} steep service drop{'s' if len(drops) > 1 else ''} (not scored for sag)")
        reasons.append(NOT + ": " + "; ".join(parts))
    # v2.21: spans measured but not assessed (no visible endpoint, uncertain type, no reference for the class)
    unassessed = [w for w in wires if w["status"] == "not_assessed"]
    if unassessed:
        why: dict[str, int] = {}
        for w in unassessed:
            k = next((x for x in w.get("flags") or [] if x in _NOT_ASSESSED), "not_assessed")
            why[k] = why.get(k, 0) + 1
        f["not_assessed"] = why
        share = len(scored) / max(1, len(scored) + len(unassessed))
        detail = "; ".join(f"{n} {_NOT_ASSESSED.get(k, k)}" for k, n in sorted(why.items(), key=lambda x: -x[1]))
        need = getattr(rule, "min_assessed_share", 0.0)
        cue = sorted(w["index"] for w in unassessed if w.get("unverified_level") in ("medium", "high"))
        if verdict == NOT and need and cue:
            verdict = INCONCLUSIVE
            reasons.insert(0, f"{INCONCLUSIVE}: {_spans(cue)} run off the photo and the visible part already sags at "
                              "the Minor or Critical level; the full span could not be measured, so verify on site")
        elif verdict == NOT and need and share < need:
            verdict = INCONCLUSIVE
            reasons.insert(0, f"{INCONCLUSIVE}: only {len(scored)} of {len(scored) + len(unassessed)} measured spans "
                              f"could be assessed ({detail}); the rest are unknown, not low risk")
        else:
            notes.append(f"NOTE: {len(unassessed)} measured span{'s' if len(unassessed) > 1 else ''} not assessed "
                         f"({detail})")
    reasons.extend(notes)
    hz = Hazard(verdict, reasons, f)
    # v2.11: every span's closest vegetation (the factors keep the 10 closest); not part of as_dict()
    hz.clearance = {r["span"]: {k: r[k] for k in ("gap_px", "gap_m_approx") if k in r} for r in veg_rows}
    return hz
