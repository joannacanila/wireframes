"""S18: what WIREFRAME observed, kept apart from what an administrator decides.

Three records, never one status field:

  A. machine observations   detected wires, measured sag, problem-wire observations, photo-quality findings
                            (this module, computed from the stored analysis; never edited)
  B. screening indicators   the reference comparison per wire (comparison.py, D-37) and the triage RISK below
  C. administrative decision  review_flow.py (review progress + append-only decisions)

Problem wires
-------------
A wire is a *problem wire* only when the pipeline produced a supported, wire-specific observation:

  sag_exceeded           power line above its 3 % reference (ERB Res. 95-21 s.19; comparison attention zone)
  sag_beyond_typical     telecom / fibre cable above its typical 2-5 % range (comparison notable zone)
  possible_dangling_end  a geometric loose-end candidate on the span (hazard.loose_ends; "verify on site")
  vegetation_contact     the wire mask touches a detected vegetation region
  apparently_abandoned   the wire was classified as an abandoned-looking cable (a visual class, never "dead")

Tangling, unbundling and low clearance are NOT detected by the current model; they are never inferred here. An
administrator can record them (and confirmed critical hazards) in the decision.

Risk (operational triage rubric, NOT a statutory classification)
----------------------------------------------------------------
0 problem wires -> normal; ``moderate_from`` (1) .. ``high_from`` - 1 (9) -> moderate; ``high_from`` (10) or more ->
high. A *confirmed* critical hazard (recorded by an administrator in the current decision) overrides the count.
A low-clearance hazard can be confirmed only with a physical measurement, its clearance category and a documented
reference listed in the rubric; a photograph alone never confirms one. The rubric is stored in ``app_settings``
(``risk_rubric``), versioned and audited; legal references (the 3 % ERB value) are configuration, not this rubric.
"""

from __future__ import annotations

from datetime import date
from typing import Any

PROBLEM_TYPES: dict[str, str] = {
    "sag_exceeded": "Sag exceeds the applicable reference",
    "sag_beyond_typical": "Sag beyond the typical range",
    "possible_dangling_end": "Possible loose or dangling end",
    "vegetation_contact": "Vegetation in contact with the wire",
    "apparently_abandoned": "Apparently abandoned cable",
}
CRITICAL_TYPES: dict[str, str] = {
    "snapped_hanging": "Snapped or detached wire visibly hanging",
    "dangling_into_path": "Wire dangling into a road or pedestrian path",
    "sparking_arcing": "Visible sparking or arcing",
    "accessible_contact": "Conductor in contact with an accessible area",
    "low_clearance_measured": "Low clearance confirmed by physical measurement",
}
RISK_LABEL = {"normal": "Normal", "moderate": "Moderate", "high": "High"}

DEFAULT_RUBRIC: dict[str, Any] = {
    "version": 1,
    "effective_date": "2026-11-19",
    "basis": "WIREFRAME operational triage rubric. Not a statutory or engineering classification: the 0 / 1-9 / 10+ "
             "boundaries are internal triage settings, and pole loading cannot be judged from a wire count.",
    "problem_types": list(PROBLEM_TYPES),
    "moderate_from": 1,
    "high_from": 10,
    "critical_types": list(CRITICAL_TYPES),
    # documented clearance references a measured low-clearance hazard may cite; empty until one is documented
    "clearance_references": [],
}

CURRENT: dict[str, Any] = dict(DEFAULT_RUBRIC)  # loaded from app_settings at startup and after every change


class RubricError(ValueError):
    pass


def validate_rubric(new: dict[str, Any], old: dict[str, Any]) -> dict[str, Any]:
    """A rubric change: integer boundaries 1 <= moderate_from < high_from, known types only, an effective date and a
    reason. Returns the next version (old version + 1)."""
    try:
        mf, hf = int(new["moderate_from"]), int(new["high_from"])
    except (KeyError, TypeError, ValueError):
        raise RubricError("moderate_from and high_from must be whole numbers.") from None
    if not 1 <= mf < hf <= 1000:
        raise RubricError("Boundaries must satisfy 1 <= moderate_from < high_from.")
    pt = [t for t in new.get("problem_types", old["problem_types"]) if t in PROBLEM_TYPES]
    ct = [t for t in new.get("critical_types", old["critical_types"]) if t in CRITICAL_TYPES]
    eff = str(new.get("effective_date") or "")
    try:
        date.fromisoformat(eff)
    except ValueError:
        raise RubricError("effective_date must be YYYY-MM-DD.") from None
    refs = []
    for r in new.get("clearance_references", old.get("clearance_references", [])):
        if not (isinstance(r, dict) and r.get("id") and r.get("source") and r.get("min_height_m")):
            raise RubricError("A clearance reference needs id, source and min_height_m.")
        refs.append({"id": str(r["id"]), "source": str(r["source"]), "category": str(r.get("category") or ""),
                     "min_height_m": float(r["min_height_m"])})
    return {**old, "version": int(old.get("version", 1)) + 1, "effective_date": eff, "moderate_from": mf,
            "high_from": hf, "problem_types": pt, "critical_types": ct, "clearance_references": refs}


# --------------------------------------------------------------------------- problem wires
def _loose_spans(hazard: dict | None) -> set[int]:
    return {int(e["span"]) for e in ((hazard or {}).get("factors") or {}).get("loose_ends") or [] if "span" in e}


def wire_problems(w: dict, loose: set[int], rubric: dict | None = None) -> list[str]:
    """The supported problem observations of one wire (codes from PROBLEM_TYPES), in a fixed order."""
    from app.services.outcome import needs_attention
    rubric = rubric or CURRENT
    if w.get("status") == "service_drop":
        return []
    c = w.get("comparison") or {}
    out = []
    if needs_attention(c):
        out.append("sag_exceeded")
    elif c.get("position") and c["position"] in (c.get("notable_zones") or []):
        out.append("sag_beyond_typical")
    if w.get("index") in loose:
        out.append("possible_dangling_end")
    if "vegetation_contact" in (w.get("flags") or []) or (w.get("veg_clearance") or {}).get("gap_px") == 0:
        out.append("vegetation_contact")
    if w.get("class") == "abandoned-wire" and w.get("class_status") != "uncertain":
        out.append("apparently_abandoned")
    return [p for p in out if p in rubric["problem_types"]]


def problem_wires(wires: list[dict], hazard: dict | None, rubric: dict | None = None) -> list[dict[str, Any]]:
    loose = _loose_spans(hazard)
    out = []
    for w in wires:
        ps = wire_problems(w, loose, rubric)
        if ps:
            out.append({"index": w.get("index"), "types": ps, "labels": [PROBLEM_TYPES[p] for p in ps]})
    return out


def risk(total: int, problems: list[dict], critical: list[dict] | None = None,
         rubric: dict | None = None) -> dict[str, Any]:
    """The triage risk. ``total`` and ``len(problems)`` are reported separately and never substituted."""
    rubric = rubric or CURRENT
    crit = [c for c in critical or [] if c.get("type") in rubric["critical_types"]]
    n = len(problems)
    level = ("high" if crit or n >= rubric["high_from"] else "moderate" if n >= rubric["moderate_from"] else "normal")
    short = (f"{RISK_LABEL[level]} · {CRITICAL_TYPES[crit[0]['type']]}" if crit
             else f"{RISK_LABEL[level]} · {n} / {total} problem wire{'' if total == 1 else 's'}")
    return {"level": level, "label": RISK_LABEL[level], "problem_wires": n, "total_wires": total,
            "critical": crit, "critical_override": bool(crit), "short": short,
            "rubric_version": rubric.get("version"), "rubric_effective": rubric.get("effective_date"),
            "basis": "operational_triage_rubric"}


def validate_critical(items: list[dict], rubric: dict | None = None) -> list[dict]:
    """A confirmed critical hazard recorded by an administrator. A low-clearance hazard needs a physical measurement,
    its clearance category and a documented reference from the rubric, and the measurement must be below it."""
    rubric = rubric or CURRENT
    refs = {r["id"]: r for r in rubric.get("clearance_references") or []}
    out = []
    for c in items or []:
        t = c.get("type")
        if t not in CRITICAL_TYPES or t not in rubric["critical_types"]:
            raise RubricError(f"Unknown critical hazard type: {t}.")
        row = {"type": t, "label": CRITICAL_TYPES[t], "wire_index": c.get("wire_index"),
               "note": str(c.get("note") or "")[:500]}
        if t == "low_clearance_measured":
            m = c.get("measurement") or {}
            ref = refs.get(m.get("reference_id"))
            if ref is None:
                raise RubricError("A low-clearance hazard needs a documented clearance reference. None is configured "
                                  "for this category, so a photograph alone cannot confirm it.")
            try:
                h = float(m["height_m"])
            except (KeyError, TypeError, ValueError):
                raise RubricError("A low-clearance hazard needs the physically measured height in metres.") from None
            if not m.get("method"):
                raise RubricError("State how the height was measured (for example: measuring pole, laser rangefinder).")
            if h >= ref["min_height_m"]:
                raise RubricError(f"The measured {h} m is not below the {ref['min_height_m']} m of {ref['id']}.")
            row["measurement"] = {"height_m": h, "method": str(m["method"])[:120], "reference_id": ref["id"],
                                  "reference_source": ref["source"], "min_height_m": ref["min_height_m"]}
        elif not row["note"].strip():
            raise RubricError("Describe what was confirmed for a critical hazard.")
        out.append(row)
    return out


# --------------------------------------------------------------------------- photo-quality guidance
def photo_tips(r: dict, qcfg=None, enh=None) -> list[dict[str, str]]:
    """Specific, evidence-based advice for THIS photo: each tip names the measured condition that triggered it.
    Nothing is said when the evidence is absent (no tilt or pole-overlap advice: neither is measured)."""
    md_q = ((r.get("segmentation_input") or {}).get("quality")) or {}
    grade = r.get("quality") or {}
    unc = grade.get("uncertainty") or {}
    tips: list[dict[str, str]] = []
    min_l = getattr(qcfg, "min_mean_l", 18.0) if qcfg else 18.0
    blur_max = getattr(enh, "blur_laplacian_max", 60.0) if enh else 60.0
    min_px = getattr(qcfg, "min_wire_pixels", 400) if qcfg else 400
    if md_q.get("mean_l") is not None and md_q["mean_l"] < max(min_l, 25):
        tips.append({"code": "dark", "observation": "The photo is dark, so thin wires have little contrast.",
                     "action": "Retake the photo in brighter daylight, with the sky behind the wires."})
    if md_q.get("sharpness") is not None and md_q["sharpness"] <= blur_max:
        shake = md_q.get("motion_coherence", 0) >= getattr(enh, "motion_coherence_min", 0.16) if enh else \
            md_q.get("motion_coherence", 0) >= 0.16
        tips.append({"code": "motion_blur" if shake else "blur",
                     "observation": "The wires are smeared by camera movement." if shake
                     else "The photo is out of focus, so thin wires are blurred.",
                     "action": "Hold the phone steady and tap the wires to focus before taking the photo."})
    if md_q.get("clipped_high") is not None and md_q["clipped_high"] >= 0.10:
        tips.append({"code": "glare", "observation": "Bright sky glare washes out part of the wires.",
                     "action": "Stand so the sun is behind you, or retake when the sky is less bright."})
    if unc.get("wire_pixels") is not None and unc["wire_pixels"] < min_px:
        tips.append({"code": "small", "observation": "The wires are very small in the photo.",
                     "action": "Use the camera's optical zoom, or move closer while keeping the whole pole and its "
                               "wires in the frame."})
    edges = _cut_edges(r)
    if "top" in edges:
        tips.append({"code": "top_cut", "observation": "Wire attachment points at the top of the pole are outside the "
                     "frame.", "action": "Step back and include the entire pole top and its upper connections."})
    if edges & {"left", "right"}:
        tips.append({"code": "span_cut", "observation": "Some spans continue past the side of the photo, so only part "
                     "of them could be measured.", "action": "Step back or turn slightly so each span's two ends are "
                     "both in the photo."})
    if "bottom" in edges:
        tips.append({"code": "bottom_cut", "observation": "Lower wire attachments are cut off at the bottom of the "
                     "photo.", "action": "Include the pole base and the lower wire attachments."})
    veg = [w for w in r.get("wires") or [] if "vegetation_contact" in (w.get("flags") or [])]
    if veg:
        tips.append({"code": "vegetation", "observation": f"Trees cover part of {len(veg)} "
                     f"wire{'' if len(veg) == 1 else 's'}.",
                     "action": "Try another angle with a clearer view of the wires."})
    return tips[:3]


def _cut_edges(r: dict) -> set[str]:
    """Edges where a measured span's endpoint touches the frame (endpoint_unavailable): evidence the span continues."""
    img = r.get("image") or {}
    W, H = img.get("width") or 0, img.get("height") or 0
    if not (W and H):
        return set()
    m = 0.02 * max(W, H)
    out: set[str] = set()
    for w in r.get("wires") or []:
        if w.get("reason_code") != "endpoint_unavailable":
            continue
        for x, y in ((w.get("fit") or {}).get("endpoints") or [])[:2]:
            if y <= m:
                out.add("top")
            elif y >= H - m:
                out.add("bottom")
            if x <= m:
                out.add("left")
            elif x >= W - m:
                out.add("right")
    return out


# --------------------------------------------------------------------------- the result's "what we know"
def summary(r: dict, critical: list[dict] | None = None, qcfg=None, enh=None) -> dict[str, Any]:
    """The machine summary every result view shows first: total wires, problem wires, risk and one plain finding."""
    wires = r.get("wires") or []
    ov = r.get("overview") or {}
    pole = ov.get("pole") or {}
    hz = (r.get("acquisition") or {}).get("metadata", {}).get("hazard") if r.get("acquisition") else None
    probs = problem_wires(wires, hz if hz is not None else r.get("hazard"))
    total = len(wires)
    rk = risk(total, probs, critical)
    exceeded = len(pole.get("exceeded") or [])
    measured = len(pole.get("assessed") or [])
    unavailable = len(pole.get("unavailable") or [])
    if r.get("status") == "error":
        finding = "Image processing could not be completed for this photo."
    elif r.get("status") == "processing":
        finding = "The photo is being analysed."
    elif not total:
        finding = "No overhead wire could be traced in this photo."
    elif exceeded:
        finding = (f"{exceeded} measured power span{'s exceed' if exceeded != 1 else ' exceeds'} the 3% reference.")
    elif probs:
        finding = f"{len(probs)} visible wire{'s' if len(probs) != 1 else ''} need{'' if len(probs) != 1 else 's'} a " \
                  "closer look."
    elif measured:
        finding = "No measured span exceeds its applicable reference."
    else:
        finding = "The wires were detected; their sag could not be measured from this photo."
    tips = photo_tips(r, qcfg, enh)
    return {"total_wires": total, "problem_wires": probs, "problem_count": len(probs), "risk": rk,
            "finding": finding, "measured_spans": measured, "exceeded_spans": exceeded,
            "unmeasured_spans": unavailable, "photo_tips": tips,
            "next_steps": next_steps(r, probs, tips, unavailable)}


def next_steps(r: dict, probs: list[dict], tips: list[dict], unavailable: int) -> list[dict[str, str]]:
    """What the person can do, from the findings only (one action each; the diagnosis is not repeated)."""
    byi = {w.get("index"): w for w in r.get("wires") or []}
    types = {t for p in probs for t in p["types"]}
    classes = {byi.get(p["index"], {}).get("class") for p in probs}
    out: list[dict[str, str]] = []
    if r.get("status") == "error":
        return [{"code": "retry", "title": "Upload the photo again",
                 "text": "Processing did not finish, so nothing was measured. Upload it again or take a new photo."}]
    if "sag_exceeded" in types:
        out.append({"code": "contact_power", "provider": "electricity", "title": "Contact the electricity distributor",
                    "text": "Share the report so they can arrange a professional inspection of the listed span."})
    if "possible_dangling_end" in types:
        out.append({"code": "report_dangling", "provider": "any", "title": "Report the possible loose wire end",
                    "text": "Keep clear of it and ask the provider to verify it on site."})
    if types & {"sag_beyond_typical"} or (classes & {"telecom-cable"} and types - {"sag_exceeded"}):
        out.append({"code": "contact_telecom", "provider": "telecom", "title": "Ask the telecom or cable provider to "
                    "check the listed cable", "text": "Coverage in the area does not show who owns a cable; the "
                    "provider confirms it."})
    if types & {"apparently_abandoned", "vegetation_contact"}:
        out.append({"code": "report_cleanup", "provider": "any", "title": "Report the cable for verification",
                    "text": "Apparently abandoned cables and wires touching trees are for the provider or the barangay "
                            "to verify and clear."})
    if tips or unavailable or not (r.get("wires") or []):
        out.append({"code": "retake", "title": "Retake the photo for a fuller result",
                    "text": tips[0]["action"] if tips else "Include the whole pole and both ends of each span, against "
                                                           "open sky."})
    if not out:
        out.append({"code": "none", "title": "No action is needed from this photo",
                    "text": "If you remain concerned about this pole, contact your barangay or the provider."})
    return out
