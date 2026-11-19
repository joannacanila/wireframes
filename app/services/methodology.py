"""S12 (register D-28): Methodology & Limitations, one text for the result page and the PDF.

Every disclaimer and method note that used to be spread over the page (meter notes, photo limitations, the scope list)
is written here once, from the configured reference, so the page and the report cannot drift apart. Plain sentences, no
claims beyond what the measurement supports.
"""

from __future__ import annotations

from typing import Any


def _classes(pc: dict) -> str:
    return ", ".join(c.replace("-", " ") for c in pc["classes"])


def _one_reference(pc: dict) -> str:
    """One reference in user-relevant terms: what it is, what it applies to, what its zones mean. S15 (Part 4.3): no
    research-process notes (retrieval, supersession investigation, source verification) - those are in the thesis
    register (docs/thesis/RESEARCH_AND_DECISION_REGISTER.md D-26, D-31)."""
    if pc.get("classification_enabled") and pc.get("id") == "power":
        lo, hi = pc["lower_boundary"] * 100, pc["upper_boundary"] * 100
        z = pc["zone_labels"]
        return (f"For power lines: the {hi:.0f}% reference of {pc.get('issuer') or 'the issuer'}, "
                f"{pc.get('source', '').split('(')[0].strip() or 'the cited resolution'} "
                f"({pc.get('section') or 'cited section'}), which limits sag to {hi:.0f}% of the distance between "
                f"poles. {z['below']} below {lo:.0f}%; {z['at'].lower()} from {lo:.0f}% to {hi:.0f}% inclusive; "
                f"{z['above'].lower()} above {hi:.0f}%. The {lo:.0f}% boundary is descriptive and has no regulatory "
                f"meaning. Classes use the unrounded image sag ratio; the measurement range is shown beside them.")
    if pc.get("banded"):
        lo, hi = pc["lower_boundary"] * 100, pc["upper_boundary"] * 100
        z = pc["zone_labels"]
        return (f"For {pc.get('audience') or _classes(pc)}: {z['below']} below {lo:.0f}%, {z['at'].lower()} from "
                f"{lo:.0f}% to {hi:.0f}%, {z['above'].lower()} above {hi:.0f}% of the chord. These are general ranges "
                "from industry guidance and cable makers, not the design limit of a particular cable, and the photo "
                "does not show whether a cable is self-supporting or lashed to a messenger.")
    src = (pc.get("source") or "").split(";")[0].strip()
    return (f"For {_classes(pc)}: a {pc['sag_ratio'] * 100:.1f}% sag-to-span comparison point ({pc['status']}"
            + (f"; source: {src}" if src else "") + f"). {pc['zone_note']}.")


def reference_text(pc: dict | None) -> str:
    if not pc:
        return "No sag reference is configured, so no wire is compared."
    refs = pc.get("by_reference") or [pc]
    out = " ".join(_one_reference(r) for r in refs)
    if pc["classification_enabled"]:
        out += (" The ratio is measured in the image against the visible endpoint chord, not against a surveyed "
                "pole-to-pole distance, so each class is a screening result. It is not a compliance finding or a "
                "safety certification.")
    else:
        out += (" These are comparisons with the image sag ratio, not severity ratings or compliance checks. Before the "
                "zones can become severity classes, an applicable in-force criterion confirmed by a licensed electrical "
                "engineer, a validated tolerance (the upper boundary, which no source gives today) and a "
                "field-validated conversion from the photo ratio to physical sag are needed.")
    return out


def topics(pc: dict | None) -> list[dict[str, Any]]:
    return [{"title": t, "text": x} for t, x in (
        ("Image sag ratio", "The largest perpendicular distance from the fitted curve to the chord, divided by the "
         "chord length, both measured in image pixels."),
        ("Chord", "The straight line between the two traced ends of a wire. For a wire that runs off the photo it "
         "joins the visible ends, so it is not the pole-to-pole span."),
        ("Curve fit", "A hanging-cable curve (catenary) is fitted to the traced centreline; the sag is read from the "
         "fitted curve, not from single pixels. A trace that bends sharply where it meets another wire or a support "
         "is split there and the part that hangs as one curve is measured, so a bend is not read as sag."),
        ("Incomplete visibility", "A wire clipped at the photo edge is measured over its visible part only (Edge "
         "Clipped). Its ratio can differ from the full span's."),
        ("Interpolation", "Where the wire mask has gaps (faint, occluded or crossing segments) the centreline is "
         "bridged across them; the bridged share is given per wire under Measurement details."),
        ("Uncertainty", "Each ratio carries an estimated measurement range: a 90% interval calibrated against "
         "labelled photos (it grows for short chords and rough fits), or +/-1 pixel over the chord where no "
         "calibration applies. A ratio smaller than one pixel is Within Resolution. The class is decided by the "
         "measured value; where the range includes a band boundary, the result states so."),
        ("Image ratio is not physical sag", "One photo has no scale, range or camera pose, and the ratio changes with "
         "the viewing angle. It is not sag in metres and not a clearance."),
        ("Limits of one 2D photo", "Wires can overlap, hide behind each other or blend into the background. Pixel "
         "gaps are not physical distances, and a 2D overlap is not contact."),
        ("Reference used", reference_text(pc)),
        ("Wire types without a reference", "Abandoned wires and wires whose type the photo does not show clearly keep "
         "their measurement, with no comparison: no reference applies to an unknown wire type. Telecom / fibre cable "
         "is never compared with the power-line value."),
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
        # S16 (D-34): each reference counts only the wires it compared (never the power value for telecom wires)
        refs = {x["id"]: x for x in (pc.get("by_reference") or [pc])}
        per: dict[str, int] = {}
        for w in meas:
            rid = (w.get("comparison") or {}).get("reference_id")
            if (w.get("comparison") or {}).get("position") and rid:
                per[rid] = per.get(rid, 0) + 1
        no_ref = [w for w in meas if not (w.get("comparison") or {}).get("position") and w.get("status") != "scored"]
        by: dict[str, int] = {}
        for w in no_ref:
            k = "type uncertain" if (w.get("states") or {}).get("reference_unavailable_reason") == "type_uncertain" \
                else TYPE_WORD.get(w.get("class"), "other wire")
            by[k] = by.get(k, 0) + 1
        parts = [f"{v} compared with the {refs[rid]['label']}" for rid, v in per.items() if rid in refs]
        if parts:
            lines.append(f"Of the {len(meas)} measured wires, " + "; ".join(parts)
                         + (f"; {len(no_ref)} have no applicable reference ("
                            + ", ".join(f"{v} {k}" for k, v in by.items()) + ")" if no_ref else "") + ".")
        else:
            lines.append("No measured wire is covered by a reference; the ratios are measurements only.")
    assessed = dc["assessed"] if dc else sum(1 for w in wires if w.get("status") == "scored")
    compared = sum(1 for w in wires if (w.get("comparison") or {}).get("position"))
    # S15 (D-33): say what WAS done (measured, compared) before what was not (a severity rating), so a wire that
    # needs review is never contradicted by a blanket "not assessed"
    if assessed:
        lines.append(f"{_n(assessed, 'wire')} rated for severity against a validated reference.")
    elif compared:
        lines.append(f"{_n(compared, 'wire')} compared with a reference; no severity rating is given, because no "
                     "reference is validated for that.")
    else:
        lines.append("No severity rating is given: no validated reference applies to the wires in this photo.")
    return lines
