"""v2.20: the plain reading of one screening result, shared by the formal PDF report and the assistant.

Mirrors the public result page (app/web/assets/field.js, ``headline`` / ``plainFinding``) so the page, the report
and the assistant use the same words. Every number comes from the stored result; nothing is estimated here.
"""

from __future__ import annotations

from typing import Any

# v2.21: the one wire-severity vocabulary (app/web/assets/findings.js SEVERITY): it names the stored wire level
SEVERITY = {"high": "Critical", "medium": "Minor sag", "low": "Within reference", None: "Not assessed"}
BAND = SEVERITY
CLASS = {"power-distribution-lines": "Power line", "telecom-cable": "Telecom cable", "abandoned-wire": "Abandoned wire"}
UNASSESSED = {
    "chord_below_precision_gate": "too small or too far away in the photo",
    "too_short": "too small or too far away in the photo",
    "residual_too_large": "overlapping or crossing other wires",
    "non_positive_a": "overlapping or crossing other wires",
    "empty_mask": "too faint to trace",
    "service_drop": "drop wires to buildings (not checked for sag)",
    "endpoint_unavailable": "endpoint off the photo (partial span)",
    "class_uncertain": "uncertain wire type",
    "no_reference_for_class": "no sag reference for the wire type (power or abandoned)",
    "no_validated_reference": "no validated sag reference (measured only)",
    "withdrawn_reference": "earlier provisional reference withdrawn (measured only)",
}


def measured(w: dict) -> str | None:
    """S4: the image-derived sag ratio as a measurement, with its 1 px resolution; never a severity."""
    if w.get("sag_ratio") is None:
        return None
    r = w.get("sag_ratio_resolution")
    res = f" (+/- {r * 100:.1f} points)" if r else ""
    return f"image-derived sag ratio {w['sag_ratio'] * 100:.1f}%{res}"


def wire_type(w: dict) -> str:
    return "Type uncertain" if w.get("class_status") == "uncertain" else CLASS.get(w.get("class"), "Wire")


def band(w: dict) -> str:
    return BAND[w.get("level") if w.get("status") == "scored" else None]


def finding(w: dict) -> str:
    edge = "truncated" in (w.get("flags") or [])
    if w.get("status") == "scored" and w.get("sag_ratio") is not None:
        s = f"{w['sag_ratio'] * 100:.1f}%"
        lim = w.get("delta_lim")
        over = lim is not None and w["sag_ratio"] >= lim
        lim_t = f" (reference {lim * 100:.1f}%)" if lim else ""
        base = {"high": (f"Exceeds reference limit: {s} sag{lim_t}" if over else f"Near reference limit: {s} sag{lim_t}"),
                "medium": f"Minor sag: {s}", "low": f"Within normal range: {s}"}.get(w.get("level"), f"Sag {s}")
        return base + ("; partly outside the photo" if edge else "")
    if w.get("status") == "service_drop":
        return "Drop wire to a building; not checked for sag"
    why = UNASSESSED.get(w.get("reason_code") or "")
    m = measured(w)
    if m and w.get("reason_code") in ("no_validated_reference", "withdrawn_reference"):
        return f"Measured, not assessed: {m}; no validated sag reference" + ("; partly outside the photo" if edge else "")
    if why:
        return f"Unable to assess: {why}" + (f"; {m}" if m else "")
    return "Partially outside the photograph" if edge else "Unable to assess from this photo"


def summary_title(c: dict[str, int]) -> str:
    """The status header, as on the result page (field.js summaryTitle)."""
    if c["critical"]:
        return f"Inspection summary: {c['critical']} {'wire needs' if c['critical'] == 1 else 'wires need'} attention"
    if c["minor_sag"]:
        return f"Inspection summary: no critical wires, {c['minor_sag']} with minor sag"
    return "Inspection summary: no wire flagged in this photo"


def span_id(i: int) -> str:
    return f"SP-{i:03d}"


def short_observation(w: dict) -> str:
    """Two or three words a reader can scan: where the sag stands and whether the whole span is in the photo."""
    lim, sag = w.get("delta_lim"), w.get("sag_ratio")
    over = lim is not None and sag is not None and sag >= lim
    what = {"high": "Over reference" if over else "Near reference", "medium": "Moderate sag"}.get(w.get("level"), "")
    kind = {"power-distribution-lines": "power", "telecom-cable": "telecom", "abandoned-wire": "abandoned"}.get(w.get("class"), "")
    bits = [what, "partial span" if "truncated" in (w.get("flags") or []) else "full span"]
    if kind and w.get("class_status") != "uncertain":
        bits.insert(0, kind.capitalize())
    if (w.get("veg_clearance") or {}).get("gap_px") == 0:
        bits.append("vegetation overlaps in photo")
    if w.get("class_status") == "uncertain":
        bits.append("type uncertain")
    return " · ".join(b for b in bits if b)


def recommended_action(w: dict) -> str:
    owner = {"power-distribution-lines": "utility", "telecom-cable": "provider"}.get(
        w.get("class") if w.get("class_status") != "uncertain" else "", "line operator")
    if w.get("level") == "high":
        return f"Field check; notify {owner}"
    if w.get("status") == "not_assessed":
        return "Not assessed: qualified review or field inspection"
    return "Monitor at next inspection"


def summary(r: dict[str, Any]) -> dict[str, Any]:
    """Headline, supporting lines and counts for one result (full or public serialisation)."""
    wires = r.get("wires") or []
    scored = [w for w in wires if w.get("status") == "scored"]
    flagged = [w for w in scored if w.get("level") in ("high", "medium")]
    high = [w for w in scored if w.get("level") == "high"]
    counts = {"critical": len(high), "minor_sag": sum(w.get("level") == "medium" for w in scored),
              "within_reference": sum(w.get("level") == "low" for w in scored), "not_assessed": len(wires) - len(scored)}
    certain = lambda w, c: w.get("class") == c and w.get("class_status") != "uncertain"  # noqa: E731
    tel = sum(certain(w, "telecom-cable") for w in flagged)
    pow_ = sum(certain(w, "power-distribution-lines") for w in flagged)
    pow_scored = sum(certain(w, "power-distribution-lines") for w in scored)
    n = lambda k, one, many: f"{k} {one if k == 1 else many}"  # noqa: E731
    lines = []
    if flagged:
        parts = [p for p in (tel and f"{n(tel, 'appears', 'appear')} to be telecommunications "
                             f"{'cable' if tel == 1 else 'cables'}",
                             pow_ and f"{n(pow_, 'appears', 'appear')} to be power {'line' if pow_ == 1 else 'lines'}") if p]
        if parts:
            lines.append(f"Of the {n(len(flagged), 'flagged wire', 'flagged wires')}, {' and '.join(parts)}.")
        if not pow_ and pow_scored:
            lines.append(f"None of the {n(pow_scored, 'power line', 'power lines')} that could be assessed was flagged. "
                         "This does not mean the location is electrically safe.")
    v = (r.get("hazard") or {}).get("verdict") or ""
    if r.get("status") == "error":
        head, level = "The photo could not be processed", "none"
    elif v.startswith("INCONCLUSIVE"):
        head, level = "Inspection summary: assessment incomplete", "none"
        why = next((x for x in (r.get("hazard") or {}).get("reasons") or [] if x.startswith("INCONCLUSIVE")), None)
        if why:
            lines.insert(0, why.split(":", 1)[1].strip().capitalize() + ".")
        no_ref = any(w.get("reason_code") in ("no_validated_reference", "withdrawn_reference") for w in wires)
        lines.append("This is not a low-risk result. " + (
            "No validated sag reference exists for these wires, so a retake will not complete the assessment; "
            "arrange a field inspection by a qualified person." if no_ref else
            "Retake the photo with both poles in the frame, or arrange a field check."))
    elif not scored:
        head, level = "Inspection summary: no span could be assessed", "none"
    elif v in ("AT RISK - HIGH", "AT RISK - MODERATE"):
        head, level = summary_title(counts), "high" if v == "AT RISK - HIGH" else "medium"
    else:
        head, level = summary_title(counts), "low"
        if flagged:
            lines.insert(0, f"{n(len(flagged), 'wire reads', 'wires read')} as sagging on its own; the pole as a whole "
                            "was not flagged, but these are worth a look.")
        lines.append("This is a screening of one photo, not a safety inspection.")
    reasons: dict[str, int] = {}
    for w in wires:
        if w.get("status") != "scored":
            k = UNASSESSED.get(w.get("reason_code") or "", "not measurable from this photo")
            reasons[k] = reasons.get(k, 0) + 1
    types: dict[str, int] = {}
    for w in wires:
        types[wire_type(w)] = types.get(wire_type(w), 0) + 1
    return {"headline": head, "level": level, "lines": lines, "counts": counts, "assessed": len(scored),
            "total": len(wires), "flagged": sorted(flagged, key=lambda w: (w.get("level") != "high", -(w.get("sag_ratio") or 0))),
            "unassessed_reasons": reasons, "types": types}
