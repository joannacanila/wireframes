"""S14 operational status for the admin portal: one concise status per inspection, with the one reason that explains it.

  processing_failed  the pipeline did not finish (stage and category in the failure record)
  processing         queued or running
  flagged            a reviewer flagged it, or a span is in its reference's attention zone (S17: a power line above
                     the 3 % ERB 95-21 reference; config ``attention_zones``, outcome.needs_attention, D-34/D-37)
  incomplete         processed, but no wire was found, or some wires could not be (fully) measured
  awaiting_review    processed, nothing flagged, nobody has reviewed it yet
  reviewed           a reviewer recorded a decision (other than "flagged")

A processing failure and a flagged inspection are different conditions and are never counted together.

S17: the screening RESULT (outcome.pole_assessment: exceeded / no exceedance / incomplete / unavailable) is reported
beside this queue status as ``result``, so the finding and the review lifecycle are never read out of one value.
"""

from __future__ import annotations

from typing import Any

from app.services.comparison import compare_wire, wire_states
from app.services.outcome import POLE_RESULT, pole_assessment, wire_finding

STATUS_LABEL = {"processing_failed": "Processing failed", "processing": "Processing", "flagged": "Needs review",
                "incomplete": "Incomplete", "awaiting_review": "Awaiting review", "reviewed": "Reviewed"}
STATUS_ORDER = {"processing_failed": 0, "flagged": 1, "incomplete": 2, "processing": 3, "awaiting_review": 4,
                "reviewed": 5}
FAIL_TEXT = {"model_unavailable": "the detection model was not available", "out_of_memory": "the server ran out of memory",
             "image_unreadable": "the photo could not be read", "persistence_error": "the result could not be saved",
             "interrupted": "processing was interrupted repeatedly", "inference_error": "the analysis stopped with an error"}


def _wire(d, a, cfg) -> dict[str, Any]:
    """A stored detection as the comparison reads it, from scalar columns only (list views never decode fit_json).
    The 1 px resolution uses the formula S4 stores (app/services/analysis.py ``_resolution``): max(scale, 1) / chord."""
    measured = d.status in ("scored", "not_assessed") and d.sag_ratio is not None
    res = None
    if measured and d.chord_px:
        scale = 1.0 if a.segmentation_source == "annotation" else max(a.width or 0, a.height or 0) / cfg.segmentation.imgsz
        res = round(max(scale, 1.0) / d.chord_px, 6)
    w = {"index": d.wire_index, "class": d.wire_class, "status": d.status, "reason_code": d.fail_reason,
         "flags": d.flags_json or [], "level": d.level, "sag_ratio": d.sag_ratio if measured else None,
         "sag_ratio_resolution": res,
         "class_status": "uncertain" if "class_uncertain" in (d.flags_json or []) else "confident"}
    w["comparison"] = compare_wire(w, cfg)
    w["states"] = wire_states(w, cfg, w["comparison"])
    return w


def wire_summary(a, cfg) -> dict[str, int]:
    ws = [_wire(d, a, cfg) for d in a.detections]
    fs = [wire_finding(w) for w in ws]
    att = [w for w, f in zip(ws, fs, strict=True) if f["flagged"]]
    pa = pole_assessment(ws)
    from app.services.screening import problem_wires
    probs = problem_wires(ws, (a.metadata_json or {}).get("hazard"))  # S18: supported observations only
    return {"problem": len(probs), "problem_wires": probs,"detected": len(ws), "flagged": len(att), "notable": sum(f["notable"] for f in fs),
            "result": pa["code"], "result_label": POLE_RESULT[pa["code"]], "assessed": len(pa["assessed"]),
            "unavailable": len(pa["unavailable"]),
            "flagged_power": sum(w["class"] == "power-distribution-lines" for w in att),
            "measured": sum(f["category"] in ("measured", "flagged", "partial") for f in fs),
            "partial": sum(f["category"] == "partial" for f in fs),
            "unmeasured": sum(f["category"] == "unmeasured" for f in fs),
            "not_checked": sum(f["category"] == "not_checked" for f in fs)}


def status_of(a, cfg) -> tuple[str, str, dict[str, int]]:
    """(status, reason, wire counts) for one inspection."""
    if a.status == "error":
        fl = (a.metadata_json or {}).get("failure") or {}
        why = FAIL_TEXT.get(fl.get("category") or a.error_code, "the analysis stopped with an error")
        return "processing_failed", f"Processing failed: {why}.", {}
    if a.status == "processing":
        return "processing", "Waiting for analysis.", {}
    c = wire_summary(a, cfg)
    rev = a.reviews[-1] if a.reviews else None
    if rev and rev.decision == "flagged":
        return "flagged", f"Flagged for field inspection by {rev.reviewer_name}.", c
    if rev:
        return "reviewed", f"{rev.decision.capitalize()} by {rev.reviewer_name}.", c
    if c["flagged"]:
        n = c["flagged"]
        what = "power line" if c["flagged_power"] == n else "wire"
        if what == "power line":
            return "flagged", (f"{n} power-line span{'s exceed' if n != 1 else ' exceeds'} the 3% sag reference."), c
        return "flagged", f"{n} span{'s exceed' if n != 1 else ' exceeds'} its reference threshold.", c
    more = (f" {c['notable']} telecom / fiber cable{'s' if c['notable'] != 1 else ''} above the typical range."
            if c["notable"] else "")
    if not c["detected"]:
        return "incomplete", "No wire section detected.", c
    short = c["partial"] + c["unmeasured"]
    if short:
        return "incomplete", f"{short} of {c['detected']} wires not fully measured.{more}", c
    return "awaiting_review", f"No threshold exceedance; {c['measured']} wires measured.{more}", c


def location_label(a) -> dict[str, Any]:
    """Human-readable place where it is recorded; coordinates stay secondary. Nothing is invented."""
    from app.services.contacts import city_of
    brgy = a.pole.barangay
    city = city_of(brgy, a.city) or a.city
    parts = [p for p in ((f"Brgy. {brgy}" if brgy else None), city) if p]
    loc = a.pole.location
    return {"label": ", ".join(parts) if parts else ("Coordinates only" if loc else "Location not recorded"),
            "barangay": brgy, "city": city,
            "coords": None if loc is None else [round(loc.latitude, 6), round(loc.longitude, 6)]}
