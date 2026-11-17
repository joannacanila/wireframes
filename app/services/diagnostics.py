"""Diagnostic explanation of one analysis: what was observed and measured, and why a severity is or is not given.

S5 (18 Nov 2026). After S4 removed the unsupported sag, vegetation and conductor thresholds, no span is assessed, and
the result page, PDF, admin sheet and assistant had reduced the analysis to "Assessment incomplete". The computer-vision
analysis is unchanged and still valid: every wire is detected, traced, fitted and measured. This module states those
results per wire and per photo, in one vocabulary shared by every renderer.

Rules (register D-01, D-09, D-15, D-18, D-19):

* Every statement is derived from the stored analysis (status, reason code, flags, fit geometry, centreline provenance,
  vegetation gap). Nothing is estimated, and no threshold is introduced: the only comparison made is a measured ratio
  against its own 1 px resolution, which says whether the curvature is resolvable at all.
* Four things are kept apart: the image-derived geometric measurement, a physical measurement (never available from
  one uncalibrated photo), an engineering interpretation (needs a validated reference) and a safety or compliance
  determination (never made).
* A processing failure is never described as an assessment limitation, and a successful measurement is never
  described as a failure because no reference exists.

Measurement states of a wire:

    measured           fitted between both visible ends of the trace
    measured_partial   fitted, but the trace reaches the photo edge: the ratio describes the visible part only
    not_measured       detected and traced, but no curve could be fitted (overlap, too short, too faint, ...)
    not_applicable     a steep service drop or guy wire: not a pole-to-pole span
"""

from __future__ import annotations

import math
from typing import Any

from app.services.reasons import REASONS, wire_reason

CLASS_NAME = {"telecom-cable": "telecom / fibre cable", "power-distribution-lines": "power distribution line",
              "abandoned-wire": "abandoned wire"}
MEASURED_ONLY = {"no_validated_reference", "withdrawn_reference", "no_reference_for_class"}
PARTIAL = {"endpoint_unavailable"}

# What would allow a severity to be determined, per wire type (register "What would reopen each decision")
NEEDED_FOR_SEVERITY = {
    "telecom-cable": ("To assess sag: the cable model and its manufacturer sag-tension data, the span length, and a "
                      "field-validated method that converts the photo ratio into physical sag"),
    "power-distribution-lines": ("To assess sag: the applicable in-force criterion (for example the current Philippine "
                                 "Electrical Code Part 2 provision) confirmed by a licensed electrical engineer, the "
                                 "span length, and a field-validated method that converts the photo ratio into "
                                 "physical sag"),
    "abandoned-wire": ("No sag criterion applies to an abandoned wire; its hazard depends on its attachment and "
                       "clearance, which need a field check"),
}
NEEDED_NOT_MEASURED = {
    "residual_too_large": "A photo from a few steps along the street, so this wire separates from the others",
    "non_positive_a": "A photo from a few steps along the street, so this wire separates from the others",
    "chord_below_precision_gate": "A closer or full-resolution photo, so this wire appears longer",
    "too_short": "A closer or full-resolution photo, so this wire appears longer",
    "empty_mask": "A photo with the wire against open sky and in better light",
    "no_convergence": "Another photo of this span, or a field check",
    "degenerate": "Another photo of this span, or a field check",
}
UNAVAILABLE = (
    ("sag_severity", "Sag severity: no validated sag reference exists for any wire type, so no image ratio is "
                     "compared with a limit"),
    ("physical_sag", "Physical sag in metres: one photo has no scale, range or camera pose"),
    ("clearance", "Vegetation and ground clearance: pixel gaps in one perspective photo are not physical distances"),
    ("pole_verdict", "A pole-level safety verdict: it would need assessed spans, so none is made"),
)


def _pc(x: float) -> str:
    return f"{x * 100:.1f}%"


def _chord_px(w: dict) -> float | None:
    if w.get("chord_px"):
        return float(w["chord_px"])
    ends = (w.get("fit") or {}).get("endpoints")
    if ends and len(ends) == 2:
        (x0, y0), (x1, y1) = ends
        return math.hypot(x1 - x0, y1 - y0)
    return None


def measurement_state(w: dict) -> str:
    if w.get("status") == "service_drop":
        return "not_applicable"
    if w.get("sag_ratio") is None:
        return "not_measured"
    return "measured_partial" if w.get("reason_code") in PARTIAL else "measured"


def resolvable(w: dict) -> bool | None:
    """Whether the measured curvature exceeds the ratio's own 1 px resolution (None: no measurement or resolution)."""
    r, res = w.get("sag_ratio"), w.get("sag_ratio_resolution")
    if r is None or not res:
        return None
    return r >= res


def _has_reference(cfg, wire_class: str | None) -> bool:
    return bool(cfg is not None and wire_class and cfg.crs.has_reference(wire_class))


def wire_diagnosis(w: dict, cfg=None) -> dict[str, Any]:
    """Per-wire explanation: ``state``, ``assessed``, ``resolvable`` and ordered ``lines`` ({key, text})."""
    state = measurement_state(w)
    cls, uncertain = w.get("class"), w.get("class_status") == "uncertain"
    name = CLASS_NAME.get(cls, "wire")
    lines: list[dict[str, str]] = []
    add = lambda k, t: lines.append({"key": k, "text": t})  # noqa: E731

    # 1. detection and type
    if uncertain:
        add("detection", "Detected as a wire. The type evidence is split between power and telecom, so no type is "
                         "claimed")
    else:
        add("detection", f"Detected as a {name}")

    # 2. trace (centreline provenance: how much was bridged across mask gaps)
    share = (w.get("provenance") or {}).get("centerline_inferred_share")
    if share is not None:
        add("trace", "Centreline traced along the mask without gaps" if share == 0 else
            f"Centreline traced; {share * 100:.0f}% of its length was bridged across gaps in the mask (faint, occluded "
            "or crossing segments), so that part is interpolated")

    # 3. measurement
    r, res, chord = w.get("sag_ratio"), w.get("sag_ratio_resolution"), _chord_px(w)
    pm = f" ± {res * 100:.1f} points" if res else ""
    if state == "not_applicable":
        add("measurement", REASONS["service_drop"])
    elif state == "not_measured":
        add("measurement", "Not measured: " + (wire_reason(w.get("status") or "", w.get("reason_code"),
                                                           w.get("flags"))[1] or "no curve could be fitted").rstrip("."))
    else:
        where = ("between the visible ends of the trace" if state == "measured" else
                 "to the visible part only (the trace reaches the photo edge, so this is not the pole-to-pole span)")
        add("measurement", f"Hanging curve (catenary) fitted {where}"
            + (f"; chord {chord:.0f} px" if chord else "") + f". Image-derived sag ratio {_pc(r)}{pm}")
        if resolvable(w) is False:
            add("resolution", "The trace is straight to within the 1 pixel resolution: no sag is resolvable at this "
                              "image scale (this is not evidence of a tight or a slack wire)")
        add("physical", "Physical sag not established: one photo has no scale, range or camera pose, and the ratio "
                        "changes with the viewing angle")

    # 4. image observations (never presented as physical defects)
    veg = w.get("veg_clearance") or {}
    if veg.get("gap_px") == 0:
        add("vegetation", "Vegetation overlaps or adjoins this wire in the image. A 2-D overlap does not show contact "
                          "or a clearance distance; check on site")
    elif veg.get("gap_px") is not None:
        add("vegetation", f"Nearest vegetation in the image: {veg['gap_px']:.0f} px (an image distance, not a "
                          "clearance)")
    flags = w.get("flags") or []
    if "truncated" in flags and state != "measured_partial":
        add("edge", "The trace touches the photo edge: the span may continue out of frame")
    if "lens_outside_envelope" in flags:
        add("lens", "Ultra-wide lens: the shape is distorted by the lens")

    # 5. engineering interpretation
    rc = w.get("reason_code")
    if w.get("status") == "scored" and w.get("level"):
        add("assessment", f"Assessed against the configured sag reference ({_pc(w['delta_lim'])})"
            if w.get("delta_lim") else "Assessed against the configured sag reference")
        assessed = True
    else:
        assessed = False
        if state == "not_applicable":
            add("assessment", "Severity not determined: drop wires are not checked for sag")
        elif state == "not_measured":
            add("assessment", "Severity not determined: there is no measurement")
        elif rc == "withdrawn_reference":
            add("assessment", "Severity not determined: " + REASONS["withdrawn_reference"].split(": ", 1)[1])
        else:
            why = {"endpoint_unavailable": "the full span is not in the photo",
                   "class_uncertain": "the wire type is uncertain"}.get(rc)
            no_ref = not _has_reference(cfg, cls) or rc in MEASURED_ONLY
            parts = [p for p in (why, f"no validated sag reference exists for a {name}" if no_ref and not uncertain
                                 else None) if p]
            add("assessment", "Severity not determined: " + ("; ".join(parts) if parts else "not assessed"))

    # 6. evidence that would complete the assessment
    needed: list[str] = []
    if state == "not_measured":
        needed.append(NEEDED_NOT_MEASURED.get(rc or "", "Another photo of this span, or a field check"))
    if state == "measured_partial":
        needed.append("A photo with both supporting poles of this span in the frame")
    if uncertain and state != "not_applicable":
        needed.append("Confirmation of the wire type (a field check or a clearer photo)")
    if not assessed and state != "not_applicable" and not uncertain and not _has_reference(cfg, cls) \
            and cls in NEEDED_FOR_SEVERITY:
        needed.append(NEEDED_FOR_SEVERITY[cls])
    return {"state": state, "assessed": assessed, "resolvable": resolvable(w), "lines": lines, "needed": needed}


def pole_diagnosis(r: dict, cfg=None) -> dict[str, Any]:
    """Per-photo explanation built from the serialised result and its per-wire diagnoses."""
    wires = r.get("wires") or []
    diags = [(w, w.get("diagnosis") or wire_diagnosis(w, cfg)) for w in wires]
    by = lambda s: [w["index"] for w, d in diags if d["state"] == s]  # noqa: E731
    measured, partial, unmeasured, drops = by("measured"), by("measured_partial"), by("not_measured"), by("not_applicable")
    assessed = [w["index"] for w, d in diags if d["assessed"]]
    flat = [w["index"] for w, d in diags if d["resolvable"] is False]
    reasons: dict[str, int] = {}
    for w, d in diags:
        if d["state"] == "not_measured":
            k = (w.get("reason_short") or w.get("reason_code") or "not measured").strip()
            reasons[k] = reasons.get(k, 0) + 1

    status = r.get("status")
    processing = {"complete": "Processed: wires detected and measured",
                  "no_wires_detected": "Processed: no wire detected",
                  "fit_failed": "Processed: wires detected, but none could be measured",
                  "error": "Processing failed", "processing": "Processing"}.get(status, status or "")
    if status == "error":
        stage = "failed"
    elif status == "processing":
        stage = "processing"
    elif not wires:
        stage = "no_wires"
    elif assessed:
        stage = "assessed" if len(assessed) == len(wires) - len(drops) else "partially_assessed"
    elif measured or partial:
        stage = "measured_not_assessed"
    else:
        stage = "detected_not_measured"

    obs: list[str] = []
    veg_ids = sorted(w["index"] for w in wires if (w.get("veg_clearance") or {}).get("gap_px") == 0)
    if veg_ids:
        obs.append(f"Vegetation overlaps or adjoins {len(veg_ids)} wire{'s' if len(veg_ids) > 1 else ''} in the image "
                   f"({', '.join(map(str, veg_ids[:12]))}{'…' if len(veg_ids) > 12 else ''}); a photo cannot measure "
                   "physical clearance, so check on site")
    elif r.get("vegetation"):
        obs.append(f"Vegetation detected in the photo ({len(r['vegetation'])} region"
                   f"{'s' if len(r['vegetation']) > 1 else ''}); none overlaps a traced wire in the image")
    bridged = [w["index"] for w in wires if ((w.get("provenance") or {}).get("centerline_inferred_share") or 0) > 0]
    if bridged:
        obs.append(f"{len(bridged)} traced wire{'s were' if len(bridged) > 1 else ' was'} partly bridged across mask "
                   "gaps (faint, occluded or crossing segments)")
    if partial:
        obs.append(f"{len(partial)} wire{'s reach' if len(partial) > 1 else ' reaches'} the photo edge, so only the "
                   "visible part was measured")
    if flat:
        obs.append(f"{len(flat)} measured wire{'s are' if len(flat) > 1 else ' is'} straight to within the 1 pixel "
                   "resolution (no sag resolvable at this image scale)")
    q = r.get("quality") or {}
    if q.get("reason") and q.get("grade") not in (None, "A"):
        obs.append(f"Photo quality: {q['reason']}")

    needed: list[str] = []
    for _, d in diags:
        for n in d["needed"]:
            if n not in needed:
                needed.append(n)

    n_meas = len(measured) + len(partial)
    if status == "error":
        why = "The photo could not be processed, so nothing was analysed. This is a processing failure, not a finding."
    elif not wires:
        why = ("No wire was detected. That does not mean there are no wires or no hazard: wires can blend into the "
               "sky or background.")
    elif assessed:
        why = None
    elif n_meas:
        mw = [w for w, d in diags if d["state"] in ("measured", "measured_partial")]
        causes = []
        no_ref = sorted({CLASS_NAME.get(w.get("class"), "wire") + "s" for w in mw
                         if w.get("class_status") != "uncertain" and (w.get("reason_code") in MEASURED_ONLY
                                                                       or not _has_reference(cfg, w.get("class")))})
        if no_ref:
            causes.append(f"no validated sag reference exists for {' or '.join(no_ref)}")
        if partial:
            causes.append(f"{len(partial)} span{'s run' if len(partial) > 1 else ' runs'} off the photo")
        n_unc = sum(w.get("class_status") == "uncertain" for w in mw)
        if n_unc:
            causes.append(f"{n_unc} wire type{'s are' if n_unc > 1 else ' is'} uncertain")
        why = (f"{n_meas} of {len(wires)} detected wire{'s were' if len(wires) > 1 else ' was'} measured in the image, "
               f"but no span could be assessed ({'; '.join(causes) or 'see each wire'}), and one photo gives no "
               "physical sag or clearance. A pole-level determination therefore cannot be made; this is not a "
               "low-risk result.")
    else:
        why = ("Wires were detected, but none could be measured from this photo, so no determination can be made. It "
               "is not a low-risk result.")

    return {
        "stage": stage, "processing": processing,
        "counts": {"detected": len(wires), "measured": len(measured), "measured_partial": len(partial),
                   "not_measured": len(unmeasured), "not_applicable": len(drops), "assessed": len(assessed),
                   "within_resolution": len(flat)},
        "not_measured_reasons": reasons, "observations": obs, "why": why,
        "unavailable": [] if assessed or not wires or status == "error" else [t for _, t in UNAVAILABLE],
        "needed": needed,
        # a retake can only help wires that were not measured or only partly measured, never a missing reference
        "retake_helps": bool(unmeasured or partial),
    }
