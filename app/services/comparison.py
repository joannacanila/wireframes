"""S8 (register D-26): every measured wire compared with the configured sag-to-span reference.

One function, used by every view (result API, PDF, admin, CSV, assistant), so no client re-implements the rule.
It is a comparison of the IMAGE sag ratio with a reference whose status is stated (provisional unless a cited,
applicable source validates it). It never sets a level, a CRS or a verdict, and it is not stored: historical records
are compared on read with the reference configured now, and say so.

Zones (the three meter colours), S12 (register D-28): explicit lower / upper boundaries from the configuration
(both the 3 % reference while no engineering tolerance is validated) and the wire's own measurement uncertainty:
  below  (green):  ratio + uncertainty < lower boundary
  at     (amber):  ratio +/- uncertainty reaches the region between the boundaries (the photo cannot tell it apart)
  above  (red):    ratio - uncertainty > upper boundary
A value exactly on a boundary counts as "at". ``uncertainty_overlaps`` says when the band crosses a boundary.

``wire_states`` keeps the separate facts apart: measurement, coverage, reference applicability, comparison and
assessment, so no view has to read them out of one overloaded status.
"""

from __future__ import annotations

from typing import Any

from app.services.diagnostics import measurement_state

# S12 (D-28): neutral zone names, the same on the meter, the inventory, the PDF, the admin sheet and the CSV
POSITION_TEXT = {"below": "Below reference", "at": "Reference-tolerance region", "above": "Above reference"}


def _info(c, rid: str) -> dict[str, Any]:
    lo = c.sag_ratio if c.lower_boundary is None else c.lower_boundary
    hi = c.sag_ratio if c.upper_boundary is None else c.upper_boundary
    return {"id": rid, "sag_ratio": c.sag_ratio, "unit": c.unit, "status": c.status, "status_note": c.status_note,
            "effective_date": c.effective_date, "label": c.label, "source": c.source, "classes": list(c.classes),
            "lower_boundary": lo, "upper_boundary": hi, "tolerance_basis": c.tolerance_basis,
            "uncertainty": c.uncertainty, "classification_enabled": c.classification_enabled,
            "meter_scale_max": c.meter_scale_max, "banded": lo < hi and bool(c.zone_labels),
            "zone_labels": {**POSITION_TEXT, **c.zone_labels}, "zone_text": dict(c.zone_text),
            "audience": c.audience_label, "attention_zones": list(c.attention_zones),
            "notable_zones": list(c.notable_zones), "classifications": dict(c.classifications),
            "version": c.version, "title": c.title, "issuer": c.issuer, "published": c.published,
            "section": c.section, "source_url": c.source_url, "applicability": c.applicability,
            "interpretation": c.interpretation, "boundary_basis": dict(c.boundary_basis)}


# S17 (D-37): CRS is a fixed scaling of the image sag ratio (Equation 10 with its 4 % scale base: 10 / 0.04 = 250),
# shown for continuity with Chapter III. It is not an independent safety metric and never decides a class: the
# 3 % reference is CRS 7.5 and the 2 % descriptive boundary is CRS 5.0.
CRS_SCALE = 250.0


def crs_index(ratio: float | None) -> float | None:
    return None if ratio is None else round(min(10.0, CRS_SCALE * float(ratio)), 3)


def classify(ratio: float, lower: float, upper: float) -> str:
    """S17 (D-37): the one classification rule, on the UNROUNDED ratio. Middle band inclusive at both ends: exactly
    the upper reference (3.00 % for power) is within it, anything greater exceeds it."""
    r = float(ratio)
    return "above" if r > upper else "below" if r < lower else "at"


def references(cfg) -> list[dict[str, Any]]:
    """S15 (D-31): every enabled reference, each covering its own wire classes (power and telecom never share one)."""
    out = []
    for rid, c in (("power", cfg.comparison_reference), ("telecom", getattr(cfg, "telecom_reference", None))):
        if c is not None and c.enabled:
            out.append(_info(c, rid))
    return out


def reference_info(cfg, cls: str | None = None) -> dict[str, Any] | None:
    """The reference that covers wire class ``cls`` (None if no reference covers it). Without ``cls``: the primary
    (power-line) reference, as before S15."""
    refs = references(cfg)
    if cls is None:
        return next((r for r in refs if r["id"] == "power"), None)
    return next((r for r in refs if cls in r["classes"]), None)


def position_text(c: dict | None) -> str:
    if not c or not c.get("position"):
        return "Not measured" if c else "Not compared (no applicable reference)"
    return (c.get("zone_labels") or POSITION_TEXT).get(c["position"], POSITION_TEXT[c["position"]])


def type_uncertain(w: dict) -> bool:
    """D-27: a wire whose type the photo does not show clearly is never assumed to be a covered type."""
    return (w.get("class_status") == "uncertain" or w.get("reason_code") == "class_uncertain"
            or "class_uncertain" in (w.get("flags") or []))


def compare_wire(w: dict, cfg) -> dict[str, Any] | None:
    """The comparison for one wire, or None (reference off, drop wire, class not covered, or type uncertain)."""
    ref = reference_info(cfg, w.get("class"))
    if ref is None or w.get("status") == "service_drop" or type_uncertain(w):
        return None
    state = measurement_state(w)
    out = {"reference": ref["sag_ratio"], "status": ref["status"], "label": ref["label"], "state": state,
           "reference_id": ref["id"], "banded": ref["banded"], "zone_labels": ref["zone_labels"],
           "zone_text": ref["zone_text"], "audience": ref["audience"],
           "attention_zones": ref["attention_zones"], "notable_zones": ref["notable_zones"],
           "position": None, "difference_pp": None, "zones": None, "uncertainty": None, "uncertainty_overlaps": False,
           "lower_boundary": ref["lower_boundary"], "upper_boundary": ref["upper_boundary"],
           "classification_enabled": ref["classification_enabled"], "classification": None,
           "classification_label": None, "reference_version": ref["version"], "crs_index": None}
    if state not in ("measured", "measured_partial"):
        return out
    r = float(w["sag_ratio"])
    lo, hi = ref["lower_boundary"], ref["upper_boundary"]
    if ref["classification_enabled"]:
        # S17: the point estimate decides; the calibrated 90 % band (else the 1 px resolution) is reported beside it
        pos = classify(r, lo, hi)
        res = float(w.get("sag_ratio_uncertainty") or w.get("sag_ratio_resolution") or 0.0)
        out["classification"] = ref["classifications"][pos]
        out["classification_label"] = ref["zone_labels"][pos]
        out["zones"] = {"at_from": lo, "above_from": hi, "lower": lo, "upper": hi}
    else:
        res = float(w.get("sag_ratio_resolution") or 0.0) if ref["uncertainty"] == "resolution" else 0.0
        pos = "above" if r - res > hi else "below" if r + res < lo else "at"
        out["zones"] = {"at_from": max(0.0, lo - res), "above_from": hi + res, "lower": lo, "upper": hi}
    out["position"] = pos
    out["difference_pp"] = round((r - ref["sag_ratio"]) * 100, 2)
    out["uncertainty"] = res or None
    # the band ratio +/- uncertainty crosses a boundary: the photo cannot place the wire on one side of it
    out["uncertainty_overlaps"] = bool(res) and any(r - res <= b <= r + res for b in (lo, hi))
    if ref["id"] == "power":
        out["crs_index"] = crs_index(r)
    return out


COVERAGE = {"measured": "full_span", "measured_partial": "edge_clipped"}
# S15 (D-33): one definition of an "estimated" (bridged) trace for every view - the results page, the diagnostics, the
# PDF. Before S15 the page counted >= 5% and the diagnostics > 0%, so one photo read "9 wires" and "10 wires".
BRIDGED_MIN_SHARE = 0.05


def bridged(w: dict) -> bool:
    return ((w.get("provenance") or {}).get("centerline_inferred_share") or 0.0) >= BRIDGED_MIN_SHARE


def wire_states(w: dict, cfg, comparison: dict | None) -> dict[str, Any]:
    """S12 (D-28): the separate facts about one wire, for every view. Never a severity unless one was assessed."""
    state = measurement_state(w)
    ref = reference_info(cfg, w.get("class"))
    measured = state in ("measured", "measured_partial")
    if not references(cfg):
        why = "reference_disabled"
    elif w.get("status") == "service_drop":
        why = "drop_wire"
    elif type_uncertain(w):
        why = "type_uncertain"
    elif ref is None:
        why = {"telecom-cable": "no_reference_for_telecom", "abandoned-wire": "no_reference_for_abandoned"}.get(w.get("class"), "no_reference_for_class")
    else:
        why = None
    r, res = w.get("sag_ratio"), w.get("sag_ratio_resolution") or 0.0
    return {
        "measurement": {"measured": "measured", "measured_partial": "partial"}.get(state, "not_applicable" if state == "not_applicable" else "unmeasured"),
        "coverage": COVERAGE.get(state),
        "within_resolution": bool(measured and r is not None and res and r < res),  # same rule as diagnostics.resolvable
        "uncertainty_available": bool(measured and res),
        "reference_available": why is None,
        "reference_unavailable_reason": why,
        "comparison": comparison["position"] if comparison else None,
        "reference_id": ref["id"] if ref and why is None else None,
        "assessed": w.get("status") == "scored" and bool(w.get("level")),
        # a severity class from the comparison exists only for a validated reference with classification switched on
        "classification_available": bool(comparison and ref and ref["classification_enabled"]),
        "bridged": bridged(w),
        # S15 (D-33): the six stages, separately (Part 1.5): what exists for this wire and what does not
        "stages": {
            "detection": True,
            "type": "uncertain" if type_uncertain(w) else ("known" if w.get("class") else "unknown"),
            "measurement": "full" if state == "measured" else "partial" if state == "measured_partial"
            else "not_applicable" if state == "not_applicable" else "none",
            "reference": ref["id"] if ref is not None and why is None else None,
            "comparison": comparison["position"] if comparison else None,
            "assessment": "severity" if w.get("status") == "scored" and w.get("level") else None,
        },
    }


def _tally(comps, ref: dict) -> dict[str, int]:
    counts = {"above": 0, "at": 0, "below": 0, "not_measured": 0}
    for c in comps:
        if c and c.get("reference_id", "power") == ref["id"]:
            counts[c["position"] or "not_measured"] += 1
    return counts


def pole_comparison(r: dict, cfg) -> dict[str, Any] | None:
    """The primary (power) reference with its counts, as before S15, plus ``by_reference``: one entry per enabled
    reference (power, telecom), each counting only the wires it covers."""
    refs = references(cfg)
    if not refs:
        return None
    comps = [w.get("comparison") for w in r.get("wires") or []]
    by = [{**ref, "counts": _tally(comps, ref), "zone_note": zone_note(ref)} for ref in refs]
    main = next((x for x in by if x["id"] == "power"), by[0])
    return {**main, "by_reference": by}


def zone_note(ref: dict) -> str:
    """What the amber region means under the configuration in force (never claims a tolerance that is not set)."""
    lo, hi = ref["lower_boundary"], ref["upper_boundary"]
    if ref.get("banded"):
        z = ref["zone_labels"]
        return (f"Contextual bands for {ref.get('audience') or 'this wire type'}: {z['below']} below {lo * 100:.0f}%, "
                f"{z['at']} {lo * 100:.0f}-{hi * 100:.0f}%, {z['above']} above {hi * 100:.0f}% ({ref['tolerance_basis']}); "
                "general guidance, not a cable-specific design limit or a severity class")
    unc = " widened by each wire's measurement uncertainty" if ref["uncertainty"] == "resolution" else ""
    if lo == hi:
        base = (f"Both zone boundaries are the {lo * 100:.1f}% reference: the reference-tolerance region is only the "
                f"wire's measurement uncertainty around it; no engineering tolerance or upper limit is validated")
    else:
        base = (f"The reference-tolerance region runs from {lo * 100:.1f}% to {hi * 100:.1f}%{unc} "
                f"({ref['tolerance_basis']})")
    return base + (", and the zones are severity classes (validated reference)" if ref["classification_enabled"]
                   else ", so the zones are a comparison, not severity classes")


def _one_text(pc: dict) -> str:
    n = pc["counts"]
    if not sum(n.values()):  # D-27: no wire of a covered type in this photo
        return f"No wire in this photo is covered by the {pc['label']}, so none is compared"
    if pc.get("banded"):
        z = pc["zone_labels"]
        return (f"Compared with the {pc['label']}: {n['below']} {z['below'].lower()}, {n['at']} {z['at'].lower()}, "
                f"{n['above']} {z['above'].lower()}, {n['not_measured']} not measured (image sag ratio, not physical sag)")
    return (f"Compared with the {pc['label']}: {n['above']} above the reference, {n['at']} in the reference-tolerance "
            f"region (within measurement uncertainty), {n['below']} below, {n['not_measured']} not measured (image sag "
            f"ratio, not physical sag; a comparison, not an assessment)")


def counts_text(pc: dict | None) -> str:
    if not pc:
        return ""
    return ". ".join(_one_text(x) for x in pc.get("by_reference") or [pc])


def detection_states(d, cfg) -> dict[str, Any]:
    """The same states for a stored detection (exports), from the stored measurement only."""
    f = d.fit_json or {}
    ratio = d.sag_ratio if d.sag_ratio is not None else f.get("sag_ratio")
    w = {"status": d.status, "class": d.wire_class, "reason_code": d.fail_reason, "flags": d.flags_json or [],
         "level": d.level, "sag_ratio": ratio if d.status in ("scored", "not_assessed") else None,
         "sag_ratio_resolution": f.get("sag_ratio_resolution")}
    return wire_states(w, cfg, compare_wire(w, cfg))


def compare_detection(d, cfg) -> dict[str, Any] | None:
    """The same comparison for a stored detection (exports, assistant), from the stored measurement only."""
    f = d.fit_json or {}
    ratio = d.sag_ratio if d.sag_ratio is not None else f.get("sag_ratio")
    w = {"status": d.status, "class": d.wire_class, "reason_code": d.fail_reason, "flags": d.flags_json or [],
         "sag_ratio": ratio if d.status in ("scored", "not_assessed") else None, "sag_ratio_resolution": f.get("sag_ratio_resolution")}
    return compare_wire(w, cfg)


def detection_counts(a, cfg) -> dict[str, Any] | None:
    refs = references(cfg)
    if not refs:
        return None
    comps = [compare_detection(d, cfg) for d in a.detections]
    by = [{**ref, "counts": _tally(comps, ref)} for ref in refs]
    main = next((x for x in by if x["id"] == "power"), by[0])
    return {**main, "by_reference": by}
