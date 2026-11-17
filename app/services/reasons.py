"""Plain-language reason for every wire the screener could not measure (v2.11).

One mapping, used by the API (field screener, admin) and the PDF, so a technician never sees a bare
"not measured". Codes are stable for clients; texts are for people.
"""

from __future__ import annotations

REASONS = {
    "chord_below_precision_gate": ("Chord under {gate} px: too short to measure sag reliably "
                                   "(1 px of tracing error would move the sag ratio too much)"),
    "too_short": "Too few centreline points to fit a hanging curve",
    "empty_mask": "Empty mask: nothing left to trace",
    "residual_too_large": "Does not follow one hanging curve (likely crossing or merged wires)",
    "non_positive_a": "Curves upward, not like a hanging wire (likely a merged or bent trace)",
    "no_convergence": "Curve fit did not converge",
    "degenerate": "Too straight or too short a baseline to estimate curvature",
    "service_drop": "Steep service drop or guy wire: not a pole-to-pole span, not scored for sag",
    # v2.21 measurement validity (the wire was traced and measured, but the measurement cannot support a severity)
    "endpoint_unavailable": ("Not assessed: endpoint unavailable. The trace reaches the edge of the photo, so at "
                             "least one pole attachment is not visible and the full span cannot be measured"),
    "class_uncertain": ("Not assessed: uncertain infrastructure type. The photo does not show clearly whether "
                        "this is a power line or a telecom cable, so no sag reference applies"),
    "no_reference_for_class": ("Not assessed: no configured sag reference for this wire type. Power distribution "
                               "lines and abandoned wires need their own engineering criteria; the telecom limit is "
                               "not applied"),
    # S4 (register D-02..D-05): no applicable standard gives a sag limit that an image measurement can be compared with
    "no_validated_reference": ("Not assessed: no validated sag reference. No applicable standard, regulation or "
                               "manufacturer document gives a sag limit for this wire type that a photo measurement "
                               "can be compared with, so the image-derived sag ratio is shown as a measurement only"),
    "withdrawn_reference": ("Not assessed: this result was produced with a provisional sag reference that was "
                            "withdrawn on 18 Nov 2026 (no engineering source supports it); the measurement is kept, "
                            "the earlier severity is not a finding"),
}
# v2.14: the table label for each code (field reading); the full sentence above stays in the API and the detail view
REASONS_SHORT = {
    "chord_below_precision_gate": "Insufficient visible length",
    "too_short": "Insufficient visible length",
    "empty_mask": "Nothing left to trace",
    "residual_too_large": "Ambiguous wire path",
    "non_positive_a": "Ambiguous wire path",
    "no_convergence": "Curve fit failed",
    "degenerate": "Baseline too short or straight",
    "service_drop": "Service drop (not scored)",
    "endpoint_unavailable": "Endpoint unavailable",
    "class_uncertain": "Uncertain infrastructure type",
    "no_reference_for_class": "No sag reference for this wire type",
    "no_validated_reference": "No validated sag reference",
    "withdrawn_reference": "Earlier reference withdrawn",
}


def wire_reason_short(code: str | None) -> str | None:
    return None if code is None else REASONS_SHORT.get(code, "Not measured")


SCORED_NOTES = {
    "truncated": "Touches the photo edge: the span may continue out of frame",
    "vegetation_contact": "Vegetation overlaps or adjoins this wire in the photo (not a measured clearance; check on site)",
    "lens_outside_envelope": "Ultra-wide lens: the sag shown is distorted by the lens and is not used for the verdict",
    "class_uncertain": "Wire type uncertain: the photo does not clearly show whether this is a power or a telecom line",
}


def wire_reason(status: str, fail_reason: str | None, flags: list[str] | None, gate_px: float = 100.0
                ) -> tuple[str | None, str | None]:
    """(reason_code, reason_text) for an unmeasured wire; (None, None) for a scored one."""
    if status == "scored":
        return None, None
    if status == "service_drop":
        return "service_drop", REASONS["service_drop"]
    code = fail_reason or status
    text = REASONS.get(code, code.replace("_", " ").capitalize() if code else "Not measured")
    return code, text.format(gate=int(gate_px))


def wire_notes(flags: list[str] | None) -> list[str]:
    return [SCORED_NOTES[f] for f in flags or [] if f in SCORED_NOTES]


# v2.13: what the field operator can do about an unmeasured span. Derived only from the photo size, the span's
# position in the frame and the failure code; it never changes a measurement.
SMALL_PHOTO_PX = 1000


def touches_edge(bbox, width: int, height: int, margin: float = 3.0) -> bool:
    if not bbox or not width or not height:
        return False
    x0, y0, x1, y1 = bbox
    return x0 <= margin or y0 <= margin or x1 >= width - 1 - margin or y1 >= height - 1 - margin


def wire_action(status: str, fail_reason: str | None, flags: list[str] | None, bbox, width: int, height: int
                ) -> str | None:
    """One retake instruction for an unmeasured (or edge-truncated) span; None when nothing helps."""
    edge = touches_edge(bbox, width, height) or "truncated" in (flags or [])
    short_side = min(width or 0, height or 0)
    if status == "scored":
        return "Step back so both poles of this span are in the frame" if "truncated" in (flags or []) else None
    if status == "service_drop":
        return None
    if fail_reason in ("chord_below_precision_gate", "too_short"):
        if short_side and short_side < SMALL_PHOTO_PX:
            return (f"Upload the full-resolution photo (this one is {short_side} px on its short side), "
                    "or move closer so the span looks longer")
        if edge:
            return "Move back or centre the full pole-to-pole span: it runs off the edge of the photo"
        return "Move closer or zoom in so this span fills more of the frame"
    if fail_reason in ("residual_too_large", "non_positive_a"):
        return "Shoot from a few metres along the street, where this wire does not cross others"
    if fail_reason in ("no_convergence", "degenerate"):
        return "Shoot the span from the side, so its sag is visible against the sky"
    if fail_reason == "endpoint_unavailable":
        return "Step back so both poles of this span are in the frame"
    return None


def retake_summary(wires: list[dict], width: int, height: int) -> str | None:
    """Photo-level guidance when many spans could not be measured (>= 40% of the non-drop spans)."""
    spans = [w for w in wires if w.get("status") != "service_drop"]
    unmeasured = [w for w in spans if w.get("status") != "scored"]
    if not spans or len(unmeasured) / len(spans) < 0.4:
        return None
    actions = [w.get("action") for w in unmeasured if w.get("action")]
    if not actions:
        return None
    top = max(set(actions), key=actions.count)
    return f"{len(unmeasured)} of {len(spans)} spans could not be measured. Best next photo: {top[0].lower()}{top[1:]}."
