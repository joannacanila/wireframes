"""Per-image reliability grade (v2.8, Feature B).

Reliability R = 1 - (U + D) / 2, from WireNet's mean normalised entropy U over pixels predicted
as wire and the plain-vs-mirrored disagreement D (U alone when test-time augmentation is off).
Grades come from thresholds on R calibrated on the validation stress set against measured
per-image accuracy (docs/thesis/chapter4_v2.8_autonomous_upgrades.md); a photo with almost no
predicted wire pixels, or one the enhancement step measured as unreadable (near-black or
near-white), is "Retake recommended" regardless.

v2.9: every grade below A carries a plain-English ``reason`` naming the most likely cause, from
the measured photo quality (the same measurements the enhancement router uses). The wording
does not change the grade.
"""

from __future__ import annotations

GRADES = ("A", "B", "C", "Retake recommended")
ADVICE = {"A": "results reliable", "B": "results usable; check the flagged spans",
          "C": "low reliability; verify on site or retake", "Retake recommended": "photo unreadable for wire analysis; retake"}


def reliability(unc: dict | None) -> float | None:
    if not unc or unc.get("entropy") is None:
        return None
    d = unc.get("disagreement")
    return round(1.0 - (unc["entropy"] + (d if d is not None else unc["entropy"])) / 2.0, 4)


def glare_masked(q: dict, cfg) -> bool:
    """v2.10: bright frame and washed-out thin dark lines (see enhance.line_visibility)."""
    return bool(getattr(cfg, "glare_gate", False) and q.get("bright_share") is not None
                and q.get("line_contrast") is not None
                and q["bright_share"] >= cfg.glare_bright_min and q["line_contrast"] < cfg.glare_line_contrast_max)


def too_small(q: dict, cfg) -> bool:
    return q.get("image_min_side") is not None and q["image_min_side"] < getattr(cfg, "min_side_px", 0)


def blank(q: dict, cfg) -> bool:
    return q.get("contrast") is not None and q["contrast"] < getattr(cfg, "min_contrast", 0.0)


def cause(unc: dict | None, q: dict, cfg, enh=None) -> str:
    """Most likely reason a photo is hard to read, in the order a field worker can act on."""
    if blank(q, cfg):
        return "The photo is blank or uniform: no visible content to analyse"
    if too_small(q, cfg):
        return (f"Photo resolution too low ({q['image_min_side']} px on the short side; at least "
                f"{cfg.min_side_px} px needed): retake closer or upload the full-resolution photo")
    if q.get("clipped_high", 0) > cfg.max_clipped or glare_masked(q, cfg):
        return "Severe sky glare washed out the overhead conductors"
    if q.get("mean_l") is not None and q["mean_l"] < cfg.min_mean_l:
        return "Photo too dark: the conductors are not visible"
    if (unc or {}).get("wire_pixels", 0) < cfg.min_wire_pixels:
        return "Almost no wire pixels found: wires too small in the frame, out of frame or washed out"
    blur_max = getattr(enh, "blur_laplacian_max", 60.0)
    if q.get("sharpness") is not None and q["sharpness"] <= blur_max:
        if q.get("motion_coherence", 0) >= getattr(enh, "motion_coherence_min", 0.16):
            return "Camera shake (motion blur) smeared the conductors"
        return "Photo out of focus: thin wires are blurred"
    if q.get("clipped_high", 0) >= 0.10:
        return "Bright sky glare lowered the contrast of the conductors"
    if q.get("mean_l") is not None and q["mean_l"] < 25:
        return "Low light: conductors have little contrast against the background"
    return "The model is uncertain about some wire pixels (clutter or low contrast)"


def grade(unc: dict | None, quality: dict | None, cfg, enh=None) -> dict:
    r = reliability(unc)
    q = quality or {}
    unreadable = (q.get("mean_l") is not None and (q["mean_l"] < cfg.min_mean_l or q.get("clipped_high", 0) > cfg.max_clipped))
    unreadable = unreadable or glare_masked(q, cfg) or too_small(q, cfg) or blank(q, cfg)
    if r is None or (unc or {}).get("wire_pixels", 0) < cfg.min_wire_pixels or unreadable:
        g = "Retake recommended"
    elif r >= cfg.grade_a:
        g = "A"
    elif r >= cfg.grade_b:
        g = "B"
    elif r >= cfg.grade_c:
        g = "C"
    else:
        g = "Retake recommended"
    return {"grade": g, "reliability": r, "advice": ADVICE[g], "uncertainty": unc,
            "reason": None if g == "A" else cause(unc, q, cfg, enh)}
