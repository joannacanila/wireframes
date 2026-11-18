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


def reference_info(cfg) -> dict[str, Any] | None:
    c = cfg.comparison_reference
    if not c.enabled:
        return None
    lo = c.sag_ratio if c.lower_boundary is None else c.lower_boundary
    hi = c.sag_ratio if c.upper_boundary is None else c.upper_boundary
    return {"sag_ratio": c.sag_ratio, "unit": c.unit, "status": c.status, "status_note": c.status_note,
            "effective_date": c.effective_date, "label": c.label, "source": c.source, "classes": list(c.classes),
            "lower_boundary": lo, "upper_boundary": hi, "tolerance_basis": c.tolerance_basis,
            "uncertainty": c.uncertainty, "classification_enabled": c.classification_enabled,
            "meter_scale_max": c.meter_scale_max}


def type_uncertain(w: dict) -> bool:
    """D-27: a wire whose type the photo does not show clearly is never assumed to be a covered type."""
    return (w.get("class_status") == "uncertain" or w.get("reason_code") == "class_uncertain"
            or "class_uncertain" in (w.get("flags") or []))


def compare_wire(w: dict, cfg) -> dict[str, Any] | None:
    """The comparison for one wire, or None (reference off, drop wire, class not covered, or type uncertain)."""
    ref = reference_info(cfg)
    if ref is None or w.get("status") == "service_drop" or w.get("class") not in ref["classes"] or type_uncertain(w):
        return None
    state = measurement_state(w)
    out = {"reference": ref["sag_ratio"], "status": ref["status"], "label": ref["label"], "state": state,
           "position": None, "difference_pp": None, "zones": None, "uncertainty": None, "uncertainty_overlaps": False,
           "lower_boundary": ref["lower_boundary"], "upper_boundary": ref["upper_boundary"]}
    if state not in ("measured", "measured_partial"):
        return out
    r = float(w["sag_ratio"])
    res = float(w.get("sag_ratio_resolution") or 0.0) if ref["uncertainty"] == "resolution" else 0.0
    lo, hi = ref["lower_boundary"], ref["upper_boundary"]
    out["position"] = "above" if r - res > hi else "below" if r + res < lo else "at"
    out["difference_pp"] = round((r - ref["sag_ratio"]) * 100, 2)
    out["uncertainty"] = res or None
    # the band ratio +/- uncertainty crosses a boundary: the photo cannot place the wire on one side of it
    out["uncertainty_overlaps"] = bool(res) and any(r - res <= b <= r + res for b in (lo, hi))
    out["zones"] = {"at_from": max(0.0, lo - res), "above_from": hi + res, "lower": lo, "upper": hi}
    return out


COVERAGE = {"measured": "full_span", "measured_partial": "edge_clipped"}


def wire_states(w: dict, cfg, comparison: dict | None) -> dict[str, Any]:
    """S12 (D-28): the separate facts about one wire, for every view. Never a severity unless one was assessed."""
    state = measurement_state(w)
    ref = reference_info(cfg)
    measured = state in ("measured", "measured_partial")
    if ref is None:
        why = "reference_disabled"
    elif w.get("status") == "service_drop":
        why = "drop_wire"
    elif type_uncertain(w):
        why = "type_uncertain"
    elif w.get("class") not in ref["classes"]:
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
        "assessed": w.get("status") == "scored" and bool(w.get("level")),
        # a severity class from the comparison exists only for a validated reference with classification switched on
        "classification_available": bool(comparison and ref and ref["classification_enabled"]),
    }


def pole_comparison(r: dict, cfg) -> dict[str, Any] | None:
    ref = reference_info(cfg)
    if ref is None:
        return None
    counts = {"above": 0, "at": 0, "below": 0, "not_measured": 0}
    for w in r.get("wires") or []:
        c = w.get("comparison")
        if not c:
            continue
        counts[c["position"] or "not_measured"] += 1
    return {**ref, "counts": counts, "zone_note": zone_note(ref)}


def zone_note(ref: dict) -> str:
    """What the amber region means under the configuration in force (never claims a tolerance that is not set)."""
    lo, hi = ref["lower_boundary"], ref["upper_boundary"]
    unc = " widened by each wire's measurement uncertainty" if ref["uncertainty"] == "resolution" else ""
    if lo == hi:
        base = (f"Both zone boundaries are the {lo * 100:.1f}% reference: the reference-tolerance region is only the "
                f"wire's measurement uncertainty around it; no engineering tolerance or upper limit is validated")
    else:
        base = (f"The reference-tolerance region runs from {lo * 100:.1f}% to {hi * 100:.1f}%{unc} "
                f"({ref['tolerance_basis']})")
    return base + (", and the zones are severity classes (validated reference)" if ref["classification_enabled"]
                   else ", so the zones are a comparison, not severity classes")


def counts_text(pc: dict | None) -> str:
    if not pc:
        return ""
    n = pc["counts"]
    if not sum(n.values()):  # D-27: no wire of a covered type in this photo
        return f"No wire in this photo is covered by the {pc['label']}, so none is compared"
    return (f"Compared with the {pc['label']}: {n['above']} above the reference, {n['at']} in the reference-tolerance "
            f"region (within measurement uncertainty), {n['below']} below, {n['not_measured']} not measured (image sag "
            f"ratio, not physical sag; a comparison, not an assessment)")


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
    ref = reference_info(cfg)
    if ref is None:
        return None
    counts = {"above": 0, "at": 0, "below": 0, "not_measured": 0}
    for d in a.detections:
        c = compare_detection(d, cfg)
        if c:
            counts[c["position"] or "not_measured"] += 1
    return {**ref, "counts": counts}
