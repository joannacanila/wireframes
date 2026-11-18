"""S8 (register D-26): every measured wire compared with the configured sag-to-span reference.

One function, used by every view (result API, PDF, admin, CSV, assistant), so no client re-implements the rule.
It is a comparison of the IMAGE sag ratio with a reference whose status is stated (provisional unless a cited,
applicable source validates it). It never sets a level, a CRS or a verdict, and it is not stored: historical records
are compared on read with the reference configured now, and say so.

Zones (the three meter colours) need only the reference and the wire's own measurement resolution:
  below  (green):  ratio + resolution < reference
  at     (amber):  reference within ratio +/- resolution (the photo cannot tell the ratio from the reference)
  above  (red):    ratio - resolution > reference
"""

from __future__ import annotations

from typing import Any

from app.services.diagnostics import measurement_state

POSITION_TEXT = {"below": "Below reference", "at": "At reference (within resolution)", "above": "Above reference"}


def reference_info(cfg) -> dict[str, Any] | None:
    c = cfg.comparison_reference
    if not c.enabled:
        return None
    return {"sag_ratio": c.sag_ratio, "status": c.status, "label": c.label, "source": c.source, "classes": list(c.classes)}


def compare_wire(w: dict, cfg) -> dict[str, Any] | None:
    """The comparison for one wire, or None (reference off, drop wire, class not covered)."""
    ref = reference_info(cfg)
    if ref is None or w.get("status") == "service_drop" or w.get("class") not in ref["classes"]:
        return None
    state = measurement_state(w)
    out = {"reference": ref["sag_ratio"], "status": ref["status"], "label": ref["label"], "state": state,
           "position": None, "difference_pp": None, "zones": None}
    if state not in ("measured", "measured_partial"):
        return out
    r, res = float(w["sag_ratio"]), float(w.get("sag_ratio_resolution") or 0.0)
    lim = ref["sag_ratio"]
    out["position"] = "above" if r - res > lim else "below" if r + res < lim else "at"
    out["difference_pp"] = round((r - lim) * 100, 2)
    out["zones"] = {"at_from": max(0.0, lim - res), "above_from": lim + res}
    return out


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
    return {**ref, "counts": counts}


def counts_text(pc: dict | None) -> str:
    if not pc:
        return ""
    n = pc["counts"]
    if not sum(n.values()):  # S9 (D-27): no wire of a covered type in this photo
        return f"No wire in this photo is covered by the {pc['label']}, so none is compared"
    return (f"Compared with the {pc['label']}: {n['above']} above, {n['at']} at the reference within the measurement "
            f"resolution, {n['below']} below, {n['not_measured']} not measured (image sag ratio, not physical sag; "
            f"a comparison, not an assessment)")


def compare_detection(d, cfg) -> dict[str, Any] | None:
    """The same comparison for a stored detection (exports, assistant), from the stored measurement only."""
    f = d.fit_json or {}
    ratio = d.sag_ratio if d.sag_ratio is not None else f.get("sag_ratio")
    w = {"status": d.status, "class": d.wire_class, "reason_code": d.fail_reason,
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
