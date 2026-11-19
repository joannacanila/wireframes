"""S14: the inspection's processing lifecycle and its plain-language findings, kept apart.

Before S14 one value carried everything: a photo that was processed successfully, whose wires were all measured,
was shown as "Assessment incomplete" / INCONCLUSIVE because no validated sag reference exists for most wire types.
That is an engineering fact about the *assessment*, not a failure of *processing*. This module keeps them separate:

  processing   pending | processing | completed | partially_completed | failed
               (pending = queued, not yet started; partially_completed = finished, some wires not (fully) measured)
  stages       detection, measurement, reference, comparison, assessment availability, as counts
  assessment   assessed | partially_assessed | provisional_comparison | inconclusive_no_reference | no_wires | unavailable

and gives every wire one plain finding a non-specialist can read. One rule set, used by the result page, the PDF,
the admin portal and the CSV, so no client re-derives it.

"Flagged for review" has one transparent rule: a power line whose image sag ratio is above the configured provisional
reference, or whose measurement uncertainty reaches it (comparison position ``above`` or ``at``,
app/services/comparison.py). It is a prompt for a qualified person to look, not a hazard rating; nothing else
(no wire type without a reference, no vegetation pixel rule, D-15) raises it. Cancelled is not a supported state: the
system has no cancel operation.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- processing lifecycle

PROCESSING_TEXT = {
    "pending": "Waiting to be analyzed",
    "processing": "Your image is being analyzed",
    "completed": "Photo analyzed",
    "partially_completed": "Photo analyzed; some wires could not be fully measured",
    "failed": "The analysis could not be completed",
}


def processing_state(status: str, wires: list[dict] | None, running: bool | None = None) -> str:
    if status == "processing":
        return "processing" if running or running is None else "pending"
    if status == "error":
        return "failed"
    ws = [w for w in wires or [] if w.get("status") != "service_drop"]
    if status == "fit_failed":
        return "partially_completed"
    if any(_measurement(w) != "measured" for w in ws):
        return "partially_completed"
    return "completed"


def _measurement(w: dict) -> str:
    st = (w.get("states") or {}).get("measurement")
    if st:
        return {"partial": "partial", "measured": "measured"}.get(st, "unmeasured")
    if w.get("sag_ratio") is None:
        return "unmeasured"
    return "partial" if w.get("reason_code") == "endpoint_unavailable" else "measured"


def stages(wires: list[dict]) -> dict[str, int]:
    ws = [w for w in wires if w.get("status") != "service_drop"]
    return {
        "detected": len(wires),
        "measured": sum(_measurement(w) == "measured" for w in ws),
        "measured_partial": sum(_measurement(w) == "partial" for w in ws),
        "unmeasured": sum(_measurement(w) == "unmeasured" for w in ws),
        "not_checked": len(wires) - len(ws),
        "reference_available": sum(bool((w.get("states") or {}).get("reference_available")) for w in ws),
        "compared": sum(bool((w.get("comparison") or {}).get("position")) for w in ws),
        # S17: a wire is assessed when the canonical reference classified it (comparison.classification)
        "assessed": sum(bool((w.get("comparison") or {}).get("classification")) for w in ws),
    }


# --------------------------------------------------------------------------- S17 pole-level aggregation (D-37)
# One deterministic rule. An unavailable span never erases a valid exceedance; a span the photo cannot measure, or
# whose type cannot be established, makes the result incomplete, never "exceeded" or "within".
POLE_RESULT = {
    "REFERENCE_THRESHOLD_EXCEEDED": "Reference threshold exceeded",
    "NO_THRESHOLD_EXCEEDANCE_DETECTED": "No threshold exceedance detected",
    "INCOMPLETE_ASSESSMENT": "Incomplete assessment",
    "MEASUREMENT_UNAVAILABLE": "Measurement unavailable",
    "NO_APPLICABLE_REFERENCE": "No applicable reference",
}
UNAVAILABLE_REASON = {
    "type_uncertain": "conductor type could not be established, so no reference could be selected",
    "empty_mask": "the wire is too faint in the photo to trace",
    "chord_below_precision_gate": "the visible span is too short to measure",
    "too_short": "the visible span is too short to measure",
    "residual_too_large": "the trace could not be separated from neighbouring wires",
    "non_positive_a": "the trace does not follow a single hanging curve",
    "kinked_trace": "the trace does not follow a single hanging curve",
    "fit_failed": "the curve fit failed",
    "endpoint_unavailable": "the span's endpoints are outside the photo",
}


def span_state(w: dict) -> tuple[str, str | None]:
    """(state, reason) for one wire: excluded (service drop), no_reference (a type with no reference, e.g. abandoned),
    unavailable (cannot be measured or typed), assessed (classified by its reference)."""
    if w.get("status") == "service_drop":
        return "excluded", "service_drop"
    c = w.get("comparison") or {}
    if c.get("classification") or c.get("position"):  # classified, or compared by a non-classifying reference
        return "assessed", None
    why = (w.get("states") or {}).get("reference_unavailable_reason")
    if why == "type_uncertain" or w.get("class_status") == "uncertain":
        return "unavailable", "type_uncertain"
    if why and why.startswith("no_reference"):
        return "no_reference", why
    if _measurement(w) == "unmeasured" or w.get("sag_ratio") is None:
        rc = w.get("reason_code") or w.get("fail_reason") or "fit_failed"
        return "unavailable", rc if rc in UNAVAILABLE_REASON else "fit_failed"
    return "unavailable", why or "fit_failed"


def pole_assessment(wires: list[dict], status: str = "complete") -> dict[str, Any]:
    states = [(w.get("index"), *span_state(w), w) for w in wires]
    assessed = [i for i, st, _, _ in states if st == "assessed"]
    exceeded = [i for i, st, _, w in states if st == "assessed" and needs_attention(w["comparison"])]
    above_typical = [i for i, st, _, w in states if st == "assessed" and w["comparison"].get("position") == "above"
                     and i not in exceeded]
    unavailable = [{"index": i, "reason": r, "text": UNAVAILABLE_REASON.get(r, "the span could not be measured")}
                   for i, st, r, _ in states if st == "unavailable"]
    no_ref = [i for i, st, _, _ in states if st == "no_reference"]
    if status in ("error",):
        code = "MEASUREMENT_UNAVAILABLE"
    elif exceeded:
        code = "REFERENCE_THRESHOLD_EXCEEDED"
    elif assessed and not unavailable:
        code = "NO_THRESHOLD_EXCEEDANCE_DETECTED"
    elif assessed:
        code = "INCOMPLETE_ASSESSMENT"
    elif no_ref and not unavailable:
        code = "NO_APPLICABLE_REFERENCE"
    else:
        code = "MEASUREMENT_UNAVAILABLE"
    applicable = len(assessed) + len(unavailable)
    return {"code": code, "label": POLE_RESULT[code], "applicable_spans": applicable, "assessed": assessed,
            "exceeded": exceeded, "above_typical": above_typical, "unavailable": unavailable,
            "no_reference": no_ref, "complete": not unavailable and bool(assessed),
            "no_exceedance_in_measured": bool(assessed) and not exceeded}


def assessment_text(pa: dict, proc: str) -> str:
    if proc in ("pending", "processing"):
        return "The photo has not been analyzed yet."
    n, a, u = pa["applicable_spans"], len(pa["assessed"]), len(pa["unavailable"])
    code = pa["code"]
    if code == "REFERENCE_THRESHOLD_EXCEEDED":
        k = len(pa["exceeded"])
        t = f"{k} of {a} assessed {'span exceeds' if k == 1 else 'spans exceed'} the applicable reference threshold."
        return t + (f" {u} further {'span' if u == 1 else 'spans'} could not be measured." if u else "")
    if code == "NO_THRESHOLD_EXCEEDANCE_DETECTED":
        return f"All {a} applicable {'span was' if a == 1 else 'spans were'} assessed; none exceeds its reference threshold."
    if code == "INCOMPLETE_ASSESSMENT":
        return (f"No measured span exceeds its reference threshold ({a} of {n} assessed). {u} "
                f"{'span' if u == 1 else 'spans'} could not be measured, so the pole assessment is incomplete.")
    if code == "NO_APPLICABLE_REFERENCE":
        return "The measured wires are of a type with no applicable sag reference; their measurements are reported."
    if proc == "failed":
        return "Processing failed, so no span could be measured."
    return "No span could be measured reliably from this photo." if n else "No overhead wire was detected in this photo."


def assessment_state(status: str, s: dict[str, int]) -> str:
    """S17: kept for callers that only have the stage counts (lifecycle tests, diagnostics)."""
    if status in ("error", "processing"):
        return "unavailable"
    if not s["detected"]:
        return "no_wires"
    return "assessed" if s["assessed"] else "unavailable"

# --------------------------------------------------------------------------- per-wire finding

CLASS_TEXT = {"power-distribution-lines": "Power line", "telecom-cable": "Telecom / fiber cable",
              "abandoned-wire": "Abandoned wire"}
# S14 (UX): plain words a non-specialist reads first; the category keys stay the engineering record
FINDING_TEXT = {"flagged": "Reference threshold exceeded", "measured": "Measured",
                "partial": "Partly outside the photo", "unmeasured": "Measurement unavailable",
                "not_checked": "Not assessed: service drop"}


def wire_type(w: dict) -> str:
    if w.get("class_status") == "uncertain" or "class_uncertain" in (w.get("flags") or []):
        return "Type uncertain"
    return CLASS_TEXT.get(w.get("class"), "Wire")


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def tone_of(cat: str, notable: bool, c: dict) -> str:
    """S17: the one semantic tone of a wire for every view: warn (exceeds its reference threshold), notice (above a
    typical range), ok (classified within its reference), none (no classification)."""
    if cat == "flagged":
        return "warn"
    if notable:
        return "notice"
    return "ok" if c.get("classification") else "none"


def reference_short(c: dict) -> str | None:
    if not c or c.get("reference") is None:
        return None
    lo, hi = c.get("lower_boundary"), c.get("upper_boundary")
    if hi is None:
        return f"{_pct(c['reference']).replace('.0', '')} reference"
    if c.get("reference_id") == "power" or lo is None:
        return f"{_pct(hi).replace('.0', '')} power-line reference (ERB 95-21 s.19)"
    return f"{_pct(lo).replace('.0', '')}–{_pct(hi).replace('.0', '')} typical range"


def needs_attention(c: dict | None) -> bool:
    """The one attention rule (S16 D-34, S17 D-37): the wire's position is one of its reference's attention zones
    (power: above the 3 % reference). Used by the finding, the display plan and the pole aggregation alike."""
    c = c or {}
    pos = c.get("position")
    return bool(pos) and pos in c.get("attention_zones", ["at", "above"] if not c.get("banded") else ["above"])


# S18 (Part 2.2): the sag meter's three bands. Power: below 2 % low, 2-3 % inclusive moderate, above 3 % high (ERB
# Res. 95-21 s.19 at 3 %); telecom / fibre: its own 2 % and 5 % bands. Never computed in a client.
METER_LEVEL = {"below": "low", "at": "moderate", "above": "high"}
METER_LEVEL_LABEL = {"low": "Low", "moderate": "Moderate", "high": "High"}


def wire_finding(w: dict) -> dict[str, Any]:
    """One wire, in plain words. Never a hazard or compliance claim; the measurement is described neutrally."""
    c = w.get("comparison") or {}
    pos = c.get("position")
    if w.get("status") == "service_drop":
        cat = "not_checked"
    elif needs_attention(c):
        # S16 (D-34): the reference's own attention zones (power: at / above the provisional 3 %); a telecom / fiber
        # cable beyond the typical ranges is "notable", never attention (see config ComparisonReference)
        cat = "flagged"
    else:
        cat = _measurement(w)
    reasons = {"above": ["above_reference"], "at": ["near_reference"]}.get(pos, []) if cat == "flagged" else []
    ratio = w.get("sag_ratio")
    obs: list[str] = []
    if cat == "not_checked":
        obs.append("Service drop to a building; service drops are outside the span sag assessment.")
    elif cat == "unmeasured":
        obs.append(_unmeasured_reason(w))
    else:
        part = cat == "partial" or c.get("state") == "measured_partial" or w.get("reason_code") == "endpoint_unavailable"
        if pos and c.get("banded"):
            z = c.get("zone_labels") or {}
            obs.append(f"Image sag ratio {_pct(ratio)}: {z.get(pos, pos)} for {c.get('audience') or 'this wire type'} "
                       f"({c.get('label')}).")
        elif pos == "above":
            obs.append(f"Image sag ratio {_pct(ratio)} exceeds the {_pct(c['reference'])} power-line reference.")
        elif pos == "at":
            obs.append(f"Image sag ratio {_pct(ratio)} is within the measurement uncertainty of the "
                       f"{_pct(c['reference'])} power-line reference.")
        elif pos == "below":
            obs.append(f"Image sag ratio {_pct(ratio)} is below the {_pct(c['reference'])} power-line reference.")
        elif ratio is not None and (w.get("states") or {}).get("within_resolution"):
            obs.append("Sag is smaller than one pixel at this image scale and cannot be resolved; this is not evidence "
                       "of a correctly tensioned span.")
        elif ratio is not None:
            obs.append(f"Image sag ratio {_pct(ratio)} of the visible span.")
        if part:
            obs.append("The span continues beyond the photo edge; only the visible portion was measured.")
        if not pos and ratio is not None:
            obs.append(_no_reference_reason(w))
    vc = w.get("veg_clearance") or {}
    if vc.get("gap_px") == 0:
        obs.append("Vegetation is in contact with this wire in the photo.")
    meter = meter_state(w, cat)
    notable = cat != "flagged" and bool(pos) and pos in c.get("notable_zones", [])
    sag = _sag(w, cat)
    return {"category": cat, "label": FINDING_TEXT[cat], "flagged": cat == "flagged", "notable": notable,
            "classification": c.get("classification"), "classification_label": c.get("classification_label"),
            "reference_id": c.get("reference_id"), "span_state": span_state(w)[0],
            "tone": tone_of(cat, notable, c), "reference_short": reference_short(c),
            # S18: the meter's band for every view (green / yellow / red), from the server's own comparison only
            "meter_level": METER_LEVEL.get(pos) if c.get("classification") or (pos and c.get("banded")) else None,
            "meter_level_label": METER_LEVEL_LABEL.get(METER_LEVEL.get(pos)) if c.get("classification")
            or (pos and c.get("banded")) else None,
            "span_reason": UNAVAILABLE_REASON.get(span_state(w)[1] or "") if span_state(w)[0] == "unavailable" else None,
            "reasons": reasons, "type": wire_type(w), "observations": obs, "include_default": cat == "flagged",
            "sag": sag, "meter": meter, "plain": plain_sentence(w, cat, meter),
            "priority": priority(w, cat, notable, sag)}


# S16 (D-34): one ordering for every view (page, PDF, admin). Groups never mix reference systems as if they meant the
# same thing: attention (a reference's attention zone) first, then notable, then the rest. Inside a group the
# conservative estimate (sag minus its calibrated 90 % band) decides, so an uncertain large number does not outrank
# a precise one; partial and unmeasured wires follow, service drops last.
GROUP_ORDER = {"attention": 0, "notable": 1, "measured": 2, "unmeasured": 3, "not_checked": 4}


def priority(w: dict, cat: str, notable: bool, sag: dict | None) -> dict[str, Any]:
    group = ("attention" if cat == "flagged" else "notable" if notable else "not_checked" if cat == "not_checked"
             else "unmeasured" if cat == "unmeasured" or sag is None else "measured")
    lower = None
    if sag:
        lower = round(max(0.0, sag["ratio"] - (w.get("sag_ratio_uncertainty") or w.get("sag_ratio_resolution") or 0)), 6)
    return {"group": group, "rank_key": [GROUP_ORDER[group], -(lower if lower is not None else -1.0), w.get("index", 0)],
            "lower_bound": lower}


# S14 citizen page (register D-30): what a resident reads. Everyday words, no reference arithmetic, never a hazard,
# compliance or ownership claim, and never "safe". The engineering wording stays in ``observations``.
CITIZEN_LABEL = {"flagged": "Reference threshold exceeded", "measured": "Measured, no reference",
                 "partial": "Partly measured", "unmeasured": "Measurement unavailable",
                 "not_checked": "Service drop, not assessed"}


# S15 (Part 4.1): ONE disclaimer for the citizen page, shown once (``overview.citizen.disclaimer``); the per-wire
# sentences and the summary state what was found and do not each repeat it.
# S17 (Part 5.4): the canonical Important Notice, verbatim; the page and the PDF both read this constant.
from app.core.notice import TEXT as DISCLAIMER  # noqa: E402  (the one canonical notice)

# S15 (Part 6.3): the status a resident sees in lists, from the real result (never "screened" when processing failed)
STATUS_LABEL = {"fail": "Processing failed", "proc": "Processing", "attn": "Reference threshold exceeded",
                "low": "Incomplete assessment", "ok": "Screened"}


def _certainty(w: dict, c: dict, pos: str) -> str:
    """Whether the zone holds once the calibrated 90 % band is allowed for: 'clear' or 'likely'."""
    r, u = w.get("sag_ratio"), w.get("sag_ratio_uncertainty")
    if r is None or u is None:
        return "likely"
    lo, hi = c.get("lower_boundary") or c.get("reference"), c.get("upper_boundary") or c.get("reference")
    if pos == "above":
        return "clear" if hi is not None and r - u > hi else "likely"
    if pos == "below":
        return "clear" if lo is not None and r + u < lo else "likely"
    return "likely"


def _qualifiers(w: dict, cat: str) -> str:
    """What this wire's own trace adds: visible part only, or split where it met another wire."""
    out = []
    if cat == "partial" or w.get("reason_code") == "endpoint_unavailable":
        out.append("Measured on the visible portion; the span continues beyond the photo edge.")
    if "kink_split" in (w.get("flags") or []):
        out.append("The trace was split where it meets another wire or support; the single-curve portion was measured.")
    return (" " + " ".join(out)) if out else ""


def plain_sentence(w: dict, cat: str, meter: dict) -> str:
    """S17: one sentence from this wire's own result: its ratio, its class and what is specific to its evidence."""
    if cat == "not_checked":
        return "Service drop to a building; outside the span sag assessment."
    if cat == "unmeasured":
        st, why = span_state(w)
        return f"Measurement unavailable: {UNAVAILABLE_REASON.get(why or '', 'the span could not be measured')}."
    q = _qualifiers(w, cat)
    c = w.get("comparison") or {}
    pos, r = c.get("position"), w.get("sag_ratio")
    if pos and c.get("classification"):
        lo, hi = c["lower_boundary"], c["upper_boundary"]
        u = c.get("uncertainty")
        near = ""
        if c.get("uncertainty_overlaps") and u:
            near = (f" The estimated measurement range ({_pct(max(0.0, r - u))}-{_pct(r + u)}) includes a band "
                    f"boundary.")
        if c.get("reference_id") == "power":
            text = {"above": f"Estimated sag {_pct(r)} exceeds the {_pct(hi)} power-line reference. Refer this span "
                             f"for professional inspection.",
                    "at": f"Estimated sag {_pct(r)} is within the {_pct(hi)} power-line reference "
                          f"({_pct(lo)}-{_pct(hi)} band).",
                    "below": f"Estimated sag {_pct(r)} is below {_pct(lo)}, within the {_pct(hi)} power-line "
                             f"reference."}[pos]
        else:
            text = {"above": f"Estimated sag {_pct(r)} is above the {_pct(lo)}-{_pct(hi)} typical range for "
                             f"{c.get('audience') or 'this cable type'}. The provider may review this span.",
                    "at": f"Estimated sag {_pct(r)} is within the {_pct(lo)}-{_pct(hi)} typical range for "
                          f"{c.get('audience') or 'this cable type'}.",
                    "below": f"Estimated sag {_pct(r)} is below the {_pct(lo)}-{_pct(hi)} typical range for "
                             f"{c.get('audience') or 'this cable type'}."}[pos]
        return f"{text}{near}{q}".strip()
    if r is None:
        return f"Measurement unavailable.{q}".strip()
    if pos:  # compared without classification (a reference configured with classification_enabled: false)
        z = (c.get("zone_labels") or {}).get(pos, pos)
        near = " The measurement uncertainty includes the reference." if c.get("uncertainty_overlaps") else ""
        return f"Estimated sag {_pct(r)}: {z.lower()}, compared with the {c.get('label')}.{near}{q}".strip()
    if meter.get("reason") == "type_uncertain" or type_is_uncertain(w):
        return (f"Estimated sag {_pct(r)}. Conductor type could not be established, so no reference is applied."
                f"{q}").strip()
    if w.get("class") == "abandoned-wire":
        return f"Estimated sag {_pct(r)}. No sag reference applies to abandoned wires.{q}".strip()
    return f"Estimated sag {_pct(r)}. No applicable reference for this wire type.{q}".strip()


def type_is_uncertain(w: dict) -> bool:
    return w.get("class_status") == "uncertain" or "class_uncertain" in (w.get("flags") or [])


def _flag_types(wires: list[dict]) -> str:
    kinds = {(w.get("comparison") or {}).get("reference_id") for w in wires if (w.get("finding") or {}).get("flagged")}
    return {frozenset({"power"}): "power lines", frozenset({"telecom"}): "telecom and fiber cables"}.get(
        frozenset(k for k in kinds if k), "their wire type")


def citizen_summary(r: dict, ov: dict) -> dict[str, Any]:
    """The result in one sentence and one short explanation, for the person who took the photo."""
    out = _summary(r, ov)
    return {**out, "status_label": STATUS_LABEL[out.pop("status")], "disclaimer": DISCLAIMER}


def _summary(r: dict, ov: dict) -> dict[str, Any]:
    """S17: the result in one heading and one sentence, from the pole-level aggregation."""
    proc = ov["processing"]
    wires = r.get("wires") or []
    if proc == "failed":
        return {"tone": "fail", "status": "fail", "title": "Processing failed",
                "text": "The photo could not be analyzed. Upload it again, or take a new photo."}
    if proc in ("pending", "processing"):
        return {"tone": "proc", "status": "proc", "title": "Analysis in progress",
                "text": "Analysis usually completes in under a minute."}
    if not wires:
        return {"tone": "partial", "status": "low", "title": "No overhead wire detected",
                "text": "Retake the photo with the wires against open sky. Concerns can still be reported using the "
                        "contacts below."}
    pa = ov.get("pole") or pole_assessment(wires, r.get("status", "complete"))
    n_ex, n_as, n_un = len(pa["exceeded"]), len(pa["assessed"]), len(pa["unavailable"])
    low_quality = (r.get("quality") or {}).get("grade") == "Retake recommended"
    tel = len(pa["above_typical"])
    tel_txt = (f" {tel} telecom or fiber {'cable is' if tel == 1 else 'cables are'} above the typical sag range."
               if tel else "")
    un_txt = f" {n_un} {'span' if n_un == 1 else 'spans'} could not be measured." if n_un else ""
    if pa["code"] == "REFERENCE_THRESHOLD_EXCEEDED":
        return {"tone": "attn", "status": "attn",
                "title": f"{n_ex} power-line {'span exceeds' if n_ex == 1 else 'spans exceed'} the 3% sag reference",
                "text": f"{n_ex} of {n_as} assessed {'span' if n_as == 1 else 'spans'} should be referred for "
                        f"professional inspection.{tel_txt}{un_txt}"}
    if pa["code"] == "NO_THRESHOLD_EXCEEDANCE_DETECTED":
        return {"tone": "partial" if tel else "ok", "status": "low" if low_quality else "ok",
                "title": "No threshold exceedance detected",
                "text": f"All {n_as} assessed {'span is' if n_as == 1 else 'spans are'} within their reference "
                        f"threshold.{tel_txt}"}
    if pa["code"] == "INCOMPLETE_ASSESSMENT":
        return {"tone": "partial", "status": "low",
                "title": "Incomplete assessment: no exceedance in measured spans",
                "text": f"{n_as} {'span was' if n_as == 1 else 'spans were'} assessed and none exceeds its reference "
                        f"threshold.{tel_txt}{un_txt} A clearer photo of those spans would complete the assessment."}
    if pa["code"] == "NO_APPLICABLE_REFERENCE":
        return {"tone": "partial", "status": "ok", "title": "Measured; no applicable reference",
                "text": "The measured wires are of a type with no sag reference. Their measurements are listed below."}
    return {"tone": "partial", "status": "low", "title": "Measurement unavailable",
            "text": f"No span could be measured reliably from this photo.{un_txt} Retake the photo with the full "
                    f"span against open sky."}


def _sag(w: dict, cat: str) -> dict[str, Any] | None:
    """The estimated sag as people read it: a percentage of the visible span, with its 1 px uncertainty."""
    ratio = w.get("sag_ratio")
    if cat in ("unmeasured", "not_checked") or ratio is None:
        return None
    res = w.get("sag_ratio_resolution")
    u = w.get("sag_ratio_uncertainty")
    rng = u if u is not None else res
    return {"ratio": ratio, "text": _pct(ratio), "uncertainty": f"±{res * 100:.1f}" if res else None,
            "band90": u, "band90_text": f"±{u * 100:.1f}" if u is not None else None,
            # S17 (Part 5.3): the estimated measurement range, from the calibrated 90 % band (else +/-1 px); shown only
            # when the pipeline computed one. "calibrated" says which.
            "range_text": f"{_pct(max(0.0, ratio - rng))}–{_pct(ratio + rng)}" if rng else None,
            "range_kind": "calibrated_90" if u is not None else ("resolution" if res else None),
            "within_resolution": bool((w.get("states") or {}).get("within_resolution")),
            "partial": cat == "partial" or w.get("reason_code") == "endpoint_unavailable"}


METER_NOTE = {
    "no_reference_for_telecom": "No verified comparison is available for telecom or fiber cable, so this "
                                "measurement is shown without a rating.",
    "no_reference_for_abandoned": "No verified comparison is available for an abandoned wire, so this measurement "
                                  "is shown without a rating.",
    "type_uncertain": "The wire type could not be identified reliably, so no comparison is applied.",
    "no_reference_for_class": "No verified comparison is available for this wire type.",
    "reference_disabled": "No comparison reference is configured.",
}


def meter_state(w: dict, cat: str | None = None) -> dict[str, Any]:
    """Which sag meter a wire gets, decided here from the stored comparison (never by the page):

      reference  the server compared the wire with a configured reference (zones and boundaries in ``comparison``)
      neutral    measured, but no reference applies: the value on a neutral scale, no zones, no rating
      none       nothing was measured
    """
    cat = cat or _measurement(w)
    c = w.get("comparison") or {}
    if c.get("position") and c.get("banded"):
        return {"kind": "reference", "label": c.get("label"), "banded": True, "zone": c["position"],
                "zone_label": (c.get("zone_labels") or {}).get(c["position"]),
                "note": (c.get("zone_text") or {}).get(c["position"]) or ""}
    if c.get("position"):
        status = c.get("status") or "provisional"
        return {"kind": "reference", "label": c.get("label"),
                "note": (f"Compared with the {status} {_pct(c['reference'])} reference for "
                         f"{'power lines' if w.get('class') == 'power-distribution-lines' else 'this wire type'}. "
                         "It is a comparison point, not a legal safety limit.")}
    if cat in ("unmeasured", "not_checked") or w.get("sag_ratio") is None:
        return {"kind": "none", "note": None}
    why = (w.get("states") or {}).get("reference_unavailable_reason")
    if w.get("class_status") == "uncertain":
        why = "type_uncertain"
    return {"kind": "neutral", "reason": why, "note": METER_NOTE.get(why, METER_NOTE["no_reference_for_class"])}


def _no_reference_reason(w: dict) -> str:
    why = (w.get("states") or {}).get("reference_unavailable_reason")
    return {"no_reference_for_telecom": "No validated comparison is available for this cable type.",
            "no_reference_for_abandoned": "No validated comparison is available for an abandoned wire.",
            "type_uncertain": "The wire type is uncertain, so no comparison is made.",
            }.get(why, "No validated comparison is available for this wire.")


def _unmeasured_reason(w: dict) -> str:
    rc = w.get("reason_code") or ""
    return f"Measurement unavailable: {UNAVAILABLE_REASON.get(rc, 'the span could not be measured from this photo')}."


# --------------------------------------------------------------------------- pole-level overview

def overview(r: dict, running: bool | None = None) -> dict[str, Any]:
    """The summary every view shows first. Counts come from the stored wires; nothing is hard-coded."""
    wires = r.get("wires") or []
    proc = processing_state(r.get("status", ""), wires, running)
    st = stages(wires)
    fs = {w["index"]: (w.get("finding") or wire_finding(w)) for w in wires}
    flagged = [w["index"] for w in wires if fs[w["index"]]["flagged"]]
    plan = display_plan(wires, fs)
    measured = st["measured"] + st["measured_partial"]
    incomplete = st["measured_partial"] + st["unmeasured"]
    pole = pole_assessment(wires, r.get("status", ""))
    assess = ("unavailable" if proc in ("pending", "processing", "failed") else pole["code"].lower())
    if proc == "failed":
        headline = "Processing failed"
        lines = ["Upload the photo again, or take a new photo."]
    elif proc in ("pending", "processing"):
        headline, lines = PROCESSING_TEXT[proc], []
    elif not wires:
        headline = "No overhead wire detected"
        lines = ["Retake the photo with the wires against open sky."]
    else:
        headline = pole["label"]
        lines = [assessment_text(pole, proc)]
    return {
        "processing": proc, "processing_text": PROCESSING_TEXT[proc],
        "assessment": assess, "assessment_text": assessment_text(pole, proc), "pole": pole,
        "headline": headline, "lines": lines,
        "counts": {"detected": st["detected"], "measured": measured, "measured_full": st["measured"],
                   "measured_partial": st["measured_partial"], "unmeasured": st["unmeasured"],
                   "not_checked": st["not_checked"], "flagged": len(flagged), "notable": len(plan["notable"]),
                   "incomplete": incomplete,
                   "reference_available": st["reference_available"], "compared": st["compared"],
                   "assessed": st["assessed"]},
        "flagged": plan["attention"],
        "notable": plan["notable"],
        "display": plan,
        "include_default": plan["attention"],
        "inclusion_rule": "Wires that need review are included by default. You can add or remove any wire; the "
                          "inspection record itself is never changed.",
    }


def display_plan(wires: list[dict], fs: dict[int, dict]) -> dict[str, list[int]]:
    """S16 (D-34): which wire goes where, in which order, decided once. ``attention`` and ``notable`` and ``others``
    are disjoint and together hold every wire except service drops (``not_checked``); each list is in priority order
    (finding.priority.rank_key). Every view and count reads these lists, so a wire cannot appear twice."""
    ranked = sorted(wires, key=lambda w: fs[w["index"]]["priority"]["rank_key"])
    out: dict[str, list[int]] = {"attention": [], "notable": [], "others": [], "not_checked": []}
    for w in ranked:
        g = fs[w["index"]]["priority"]["group"]
        out["attention" if g == "attention" else "notable" if g == "notable" else "not_checked"
            if g == "not_checked" else "others"].append(w["index"])
    return out


def _n(k: int, one: str, many: str) -> str:
    return f"{k} {one if k == 1 else many}"


def wire_id(report: str, index: int) -> str:
    """S18: e.g. ``WF-415C41C6-W03``."""
    return f"{report}-W{int(index):02d}"


def report_id(analysis_id: str | None) -> str | None:
    """The citizen-facing reference for a result: on the page, in the copied message and on the PDF (S16)."""
    return f"WF-{analysis_id[:8].upper()}" if analysis_id else None


def attach(r: dict, running: bool | None = None) -> dict:
    """Add ``finding`` to every wire and ``overview`` to the result (in place)."""
    r["report_id"] = report_id(r.get("id"))
    # S18: the wire ID, stable for the life of the record: inspection reference + the stored wire number
    # (detections.wire_index, fixed when the photo was processed; never a list position)
    for w in r.get("wires") or []:
        if r["report_id"] and w.get("index") is not None:
            w["wire_id"] = wire_id(r["report_id"], w["index"])
    for w in r.get("wires") or []:
        w["finding"] = wire_finding(w)
    r["overview"] = overview(r, running)
    r["overview"]["citizen"] = citizen_summary(r, r["overview"])
    for w in r.get("wires") or []:
        f = w["finding"]
        # a measured wire with no comparison point is not "checked" against anything: never shown as green / fine
        if f["meter"]["kind"] == "neutral" and f["category"] == "measured":
            f["citizen_label"] = "Measured, not rated"
        elif f["meter"].get("banded") and f["category"] in ("measured", "partial"):
            f["citizen_label"] = f["meter"]["zone_label"]  # S15: the telecom band by name, e.g. "Typical range"
        else:
            f["citizen_label"] = CITIZEN_LABEL[f["category"]]
    return r
