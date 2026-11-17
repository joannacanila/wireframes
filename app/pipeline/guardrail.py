"""v2.17 photo-quality and classification-consistency guardrail: should the user retake this photo?

It runs after the analysis and reads only what the analysis already produced, so it never changes a wire, a class,
a CRS or the verdict; it decides whether the result is shown straight away, shown with a note, or held behind a
"retake / proceed anyway" prompt. Each check is one named issue with a measured value and its threshold:

    image_quality     the v2.8 reliability grade says "Retake recommended" (blur, glare, exposure, size, blank)
    lens              the photo is outside the 24-50 mm (35 mm eq.) capture envelope (sag withheld, v2.16)
    class_conflict    a strand carries strong evidence for two wire types at once (each >= conflict_min_share)
    uncertain_types   share of reported wires whose type is "uncertain"
    fragmentation     share of reported wires too short to measure (the photo yields pieces, not spans)
    structure         share of wire candidates removed as structure edges (flyover, sign, roofline)
    poles             no pole found: reported only (info), never a reason to retake on its own

Severity per issue: "info" < "attention" < "retake". The status is
    acceptable            no issue above info
    needs_attention       >= 1 attention issue
    retake_recommended    >= 1 retake issue                 -> requires_retake
    highly_uncertain      >= 2 retake issues                -> requires_retake
Thresholds live in config/wireframe.yaml (``guardrail``) and were chosen on the validation split and its eight
capture conditions (ml/experiments/v217_guardrail.py); they are not tuned on the test split.
"""

from __future__ import annotations

from typing import Any

ORDER = {"info": 0, "attention": 1, "retake": 2}


def _issue(code: str, severity: str, message: str, value: float | None = None, threshold: float | None = None,
           strands: list[int] | None = None) -> dict:
    return {"code": code, "severity": severity, "message": message,
            "value": None if value is None else round(float(value), 4),
            "threshold": threshold, "affected_strand_ids": strands or []}


def _level(value: float, attention: float, retake: float) -> str | None:
    if value >= retake:
        return "retake"
    if value >= attention:
        return "attention"
    return None


def assess(outcome: Any, cfg: Any) -> dict:
    """``outcome`` is an AnalysisOutcome, ``cfg`` a GuardrailConfig."""
    wires = list(outcome.wires or [])
    n = len(wires)
    issues: list[dict] = []
    q = outcome.quality or {}
    if q.get("grade") == "Retake recommended":
        issues.append(_issue("image_quality", "retake", q.get("reason") or "The photo is too hard to read for wires."))
    if "lens_outside_envelope" in (outcome.flags or []):
        issues.append(_issue("lens", "retake", "The photo was taken with an ultra-wide lens, which bends straight "
                                               "wires; retake with the main (1x) camera."))

    conflicts, uncertain = [], []
    for w in wires:
        if "class_uncertain" not in (w.flags or []):
            continue
        uncertain.append(w.index)
        share = (w.fit or {}).get("class_evidence") or {}
        top = sorted(share.items(), key=lambda kv: -kv[1])[:2]
        if len(top) == 2 and top[1][1] >= cfg.conflict_min_share:
            conflicts.append({"strand_id": w.index, "evidence": {k: round(v, 3) for k, v in top}})
    if conflicts:
        sev = "retake" if len(conflicts) >= cfg.conflict_retake_n else "attention"
        issues.append(_issue("class_conflict", sev,
                             f"{len(conflicts)} wire{'s' if len(conflicts) != 1 else ''} show strong evidence for two "
                             "different wire types; they are shown as 'type uncertain'.",
                             len(conflicts), cfg.conflict_retake_n, [c["strand_id"] for c in conflicts]))
    if n >= cfg.min_wires:
        u = len(uncertain) / n
        if lv := _level(u, cfg.uncertain_attention, cfg.uncertain_retake):
            issues.append(_issue("uncertain_types", lv, f"The type of {len(uncertain)} of {n} wires could not be "
                                 "told apart.", u, cfg.uncertain_retake, uncertain))
        short = [w.index for w in wires if w.status == "too_short"]
        f = len(short) / n
        if lv := _level(f, cfg.fragment_attention, cfg.fragment_retake):
            issues.append(_issue("fragmentation", lv, f"{len(short)} of {n} wires were found only in short pieces "
                                 "and could not be measured.", f, cfg.fragment_retake, short))
    structure = [s for s in (outcome.suppressed or []) if s.get("reason_code") in ("structure_edge", "ground_structure")]
    if structure and n + len(structure) >= cfg.min_wires:
        s = len(structure) / (n + len(structure))
        if lv := _level(s, cfg.structure_attention, cfg.structure_retake):
            issues.append(_issue("structure", lv, f"{len(structure)} line-like structure edges (flyover, sign, "
                                 "roofline) were found and ignored; similar edges may remain.", s, cfg.structure_retake))
    poles = (outcome.scene or {}).get("poles_detected")
    if poles is not None and not poles:
        issues.append(_issue("poles", "info", "No utility pole was found; findings are per wire, not per pole."))

    worst = max((ORDER[i["severity"]] for i in issues), default=0)
    n_retake = sum(i["severity"] == "retake" for i in issues)
    status = ("highly_uncertain" if n_retake >= 2 else "retake_recommended" if n_retake else
              "needs_attention" if worst == ORDER["attention"] else "acceptable")
    requires = status in ("retake_recommended", "highly_uncertain")
    affected = sorted({s for i in issues for s in i["affected_strand_ids"]})
    return {
        "version": "v2.17", "requires_retake": requires, "quality_status": status,
        "proceed_with_warning": requires, "quality_issues": issues,
        "quality_reasons": [i["message"] for i in issues if i["severity"] != "info"],
        "classification_conflicts": conflicts, "uncertain_strand_count": len(uncertain),
        "unassessed_strand_count": sum(w.status not in ("scored", "service_drop") for w in wires),
        "affected_strand_ids": affected,
    }
