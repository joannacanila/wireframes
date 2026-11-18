"""S12 (register D-28): Methodology & Limitations, one text for the result page and the PDF.

Every disclaimer and method note that used to be spread over the page (meter notes, photo limitations, the scope list)
is written here once, from the configured reference, so the page and the report cannot drift apart. Plain sentences, no
claims beyond what the measurement supports.
"""

from __future__ import annotations

from typing import Any


def _classes(pc: dict) -> str:
    return ", ".join(c.replace("-", " ") for c in pc["classes"])


def reference_text(pc: dict | None) -> str:
    if not pc:
        return "No sag reference is configured, so no wire is compared."
    parts = [f"{pc['label']}: {pc['sag_ratio'] * 100:.1f}% of the chord ({pc['unit']}), status {pc['status']}.",
             f"Source: {pc['source']}."]
    if pc.get("status_note"):
        parts.append(f"{pc['status_note']}.")
    if pc.get("effective_date"):
        parts.append(f"Effective date: {pc['effective_date']}.")
    parts.append(f"Applies to: {_classes(pc)}.")
    parts.append(f"{pc['zone_note']}.")
    if not pc["classification_enabled"]:
        parts.append("Before the zones can become severity classes, an applicable in-force criterion confirmed by a "
                     "licensed electrical engineer, a validated tolerance (the upper boundary, which no source gives "
                     "today) and a field-validated conversion from the photo ratio to physical sag are needed.")
    return " ".join(parts)


def topics(pc: dict | None) -> list[dict[str, Any]]:
    return [{"title": t, "text": x} for t, x in (
        ("Image sag ratio", "The largest perpendicular distance from the fitted curve to the chord, divided by the "
         "chord length, both measured in image pixels."),
        ("Chord", "The straight line between the two traced ends of a wire. For a wire that runs off the photo it "
         "joins the visible ends, so it is not the pole-to-pole span."),
        ("Curve fit", "A hanging-cable curve (catenary) is fitted to the traced centreline; the sag is read from the "
         "fitted curve, not from single pixels."),
        ("Incomplete visibility", "A wire clipped at the photo edge is measured over its visible part only (Edge "
         "Clipped). Its ratio can differ from the full span's."),
        ("Interpolation", "Where the wire mask has gaps (faint, occluded or crossing segments) the centreline is "
         "bridged across them; the bridged share is given per wire under Measurement details."),
        ("Uncertainty", "Each ratio carries +/-1 pixel over its chord. A ratio smaller than that is Within Resolution: "
         "no sag is resolvable at this image scale, which is not evidence of a tight or a slack wire. Where the "
         "uncertainty band crosses a zone boundary, the photo cannot place the wire on one side of it."),
        ("Image ratio is not physical sag", "One photo has no scale, range or camera pose, and the ratio changes with "
         "the viewing angle. It is not sag in metres and not a clearance."),
        ("Limits of one 2D photo", "Wires can overlap, hide behind each other or blend into the background. Pixel "
         "gaps are not physical distances, and a 2D overlap is not contact."),
        ("Reference used", reference_text(pc)),
        ("Wire types without a reference", "Telecom / fibre cable, abandoned wires and wires whose type the photo does "
         "not show clearly keep their measurement, with no comparison: no validated reference exists for them, and "
         "manufacturer data for fibre cable allow more than 3% sag under load."),
        ("Model limitations", "Wire detection and type classification come from trained models and can miss, merge "
         "or mislabel wires, especially distant, overlapping or night-time ones. Results can contain false positives "
         "and false negatives and must be verified by a qualified person."),
    )]


def _n(k: int, one: str, many: str | None = None) -> str:
    return f"{k} {one if k == 1 else (many or one + 's')}"


TYPE_WORD = {"power-distribution-lines": "power line", "telecom-cable": "telecom cable", "abandoned-wire": "abandoned wire"}


def summary_lines(r: dict) -> list[str]:
    """The result's summary sentences (page and PDF): measured, clipped, unmeasured, reference applicability and
    assessment, each counted from the stored wires and the server's states. Measured is never written as assessed."""
    wires = r.get("wires") or []
    dc = (r.get("diagnosis") or {}).get("counts")
    pc = r.get("comparison")
    meas = [w for w in wires if (w.get("states") or {}).get("measurement") in ("measured", "partial")]
    lines = []
    if dc:
        drop = dc.get("not_applicable", 0)
        lines.append(f"{_n(dc['detected'], 'wire')} detected: {dc['measured']} measured over the full span, "
                     f"{dc['measured_partial']} clipped at the photo edge (visible part measured), {dc['not_measured']} "
                     f"not measured" + (f", {_n(drop, 'drop wire')} not checked" if drop else "") + ".")
    else:
        lines.append(f"{_n(len(wires), 'wire')} detected, {len(meas)} measured.")
    if pc and meas:
        covered = sum(1 for w in meas if (w.get("comparison") or {}).get("position"))
        no_ref = [w for w in meas if not (w.get("comparison") or {}).get("position") and w.get("status") != "scored"]
        by: dict[str, int] = {}
        for w in no_ref:
            k = "type uncertain" if (w.get("states") or {}).get("reference_unavailable_reason") == "type_uncertain" \
                else TYPE_WORD.get(w.get("class"), "other wire")
            by[k] = by.get(k, 0) + 1
        if covered:
            lines.append(f"The {pc['label']} applies to {covered} of the measured wires"
                         + (f"; {len(no_ref)} have no validated reference ("
                            + ", ".join(f"{v} {k}" for k, v in by.items()) + ")" if no_ref else "") + ".")
        else:
            lines.append(f"No measured wire is covered by the {pc['label']}; the ratios are measurements only.")
    assessed = dc["assessed"] if dc else sum(1 for w in wires if w.get("status") == "scored")
    lines.append(f"{_n(assessed, 'wire')} assessed for severity." if assessed else
                 "No wire was assessed for severity: a measurement or a comparison is not an assessment.")
    return lines
