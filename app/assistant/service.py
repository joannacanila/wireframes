"""Answers for the WIREFRAME Assistant.

1. Retrieve the documentation sections that match the question (kb.search).
2. On an inspection page, build plain facts about that inspection (counts and statuses) for a signed-in user who
   may see it. Test records are shown to administrators only, as elsewhere in the portal.
3. With Claude configured (ANTHROPIC_API_KEY in the server environment, or provider "anthropic"), Claude writes the
   answer from those sources only and cites them; otherwise, or when the call fails, the closest documentation is
   returned as written. Nothing outside the sources is answered: an undocumented question gets "not documented".

What leaves the server when Claude is used: the question, the retrieved documentation and, if
``assistant_share_context`` is on, an anonymised inspection summary (no photo, pole ID, inspection name, place,
coordinates or reviewer). The assistant has no tools: it cannot change records, reviews or settings.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from app.assistant import intent, kb

log = logging.getLogger("wireframe.assistant")

MIN_SCORE = 2.5  # below this the best section does not answer the question
NOT_DOCUMENTED = ("I can't find that in the WIREFRAME documentation, so I can't answer it reliably. For questions about "
                  "a specific pole or a safety decision, ask a qualified professional; for system problems, ask your "
                  "administrator.")
SYSTEM_PROMPT = """You are the WIREFRAME Assistant, the built-in help for WIREFRAME, a computer-vision screening \
tool for overhead utility wires on poles. Your readers are barangay and city personnel, field inspectors and \
engineers, most of them not technical.

Answer only from the <sources> in the user turn: the WIREFRAME documentation and, when present, facts about the \
inspection the user has open. If the sources do not answer the question, say plainly that it is not covered in the \
WIREFRAME documentation and suggest asking an administrator or a qualified professional. Never invent features, \
pages, buttons, numbers, thresholds, formulas or capabilities. Do not explain formulas or internal parameters.

Be brief and scannable: one short opening sentence, then numbered steps for a procedure or bullets for \
related points; write interface labels in bold (for example **Map & Dispatch**). Never more than about 120 words. When the \
question is about the open inspection, use its facts. When you state what WIREFRAME does, keep it separate from \
what you recommend; recommendations always end with having a qualified professional verify the result. Never \
say or imply that a pole or wire is safe, that a low-risk result certifies anything, that no detected hazard means \
no hazard, or that an incomplete assessment counts as low risk. You cannot change inspections, reviews or \
settings; if asked to, explain where the user can do it themselves.

After the answer, on its own last line, write "Sources:" followed by the ids of the sources you used, for example \
"Sources: risk-levels, review". Use only ids given in the sources."""


@dataclass
class Answer:
    text: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    mode: str = "local"  # "claude" | "local" | "fallback" (Claude configured but unavailable)
    inspection: dict[str, Any] | None = None  # facts shown with the answer
    notice: str | None = None
    nav: list[dict[str, str]] = field(default_factory=list)  # v2.21: portal destinations named in the answer
    suggest: list[str] = field(default_factory=list)  # v2.21: follow-up questions (clarifying choices)
    intent: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"answer": self.text, "sources": self.sources, "mode": self.mode, "inspection": self.inspection,
                "notice": self.notice, "nav": self.nav, "suggest": self.suggest, "intent": self.intent}


# --------------------------------------------------------------------------- inspection facts
def inspection_facts(a, cfg=None) -> dict[str, Any]:
    """Plain facts about one inspection, from the stored result. ``public`` is the anonymised part."""
    from app.services import records
    from app.services.reasons import wire_reason_short

    md = a.metadata_json or {}
    g = md.get("guardrail") or {}
    q = md.get("quality") or {}
    counts = records.wire_counts(a)
    not_assessed: dict[str, int] = {}
    for d in a.detections:
        if d.status not in ("scored", "service_drop"):
            k = wire_reason_short(d.fail_reason or d.status) or "Not measured"
            not_assessed[k] = not_assessed.get(k, 0) + 1
    veg = sorted({i for r in a.vegetation_json or [] for i in r.get("touches", [])})
    drops = sum(1 for d in a.detections if d.status == "service_drop")
    rv = records.review_status(a)
    public = {
        "risk_level": records.RISK_LABEL[records.risk(a)],
        "assessment": records.COMPLETENESS_LABEL[records.completeness(a)],
        "wire_sections": {"high_risk": counts["high"], "moderate_risk": counts["medium"], "low_risk": counts["low"],
                          "not_assessed": counts["unassessed"], "drop_wires_not_checked": drops,
                          # S5: wires with an image-derived sag ratio (a measurement, not a severity)
                          "measured_in_image": counts["measured"]},
        "not_assessed_reasons": not_assessed,
        "vegetation_overlapping_wire_sections_in_image": len(veg),
        "retake_warning": bool(g.get("requires_retake")),
        "photo_quality_note": q.get("reason") if q.get("grade") not in (None, "A") else None,
        "review_status": records.REVIEW_LABEL[rv],
        "status": a.status,
    }
    # S8 (register D-26): the configured sag reference, compared with the stored image ratios (not an assessment)
    from app.core.config import get_analysis_config
    from app.services.comparison import detection_counts
    public["sag_reference_comparison"] = detection_counts(a, cfg or get_analysis_config())
    return {"public": public, "pole_code": a.pole.pole_code, "inspection_name": a.inspection_name, "id": a.id}


def facts_summary(f: dict[str, Any]) -> str:
    """The inspection facts as short plain sentences (shown with every answer on an inspection page)."""
    p = f["public"]
    w = p["wire_sections"]
    lines = [f"Risk level: {p['risk_level']}. Assessment: {p['assessment']}. Review: {p['review_status']}."]
    total = w["high_risk"] + w["moderate_risk"] + w["low_risk"] + w["not_assessed"]
    if total:
        lines.append(f"Wire sections: {w['high_risk']} high risk, {w['moderate_risk']} moderate, {w['low_risk']} low, "
                     f"{w['not_assessed']} not assessed" + (f"; {w['drop_wires_not_checked']} drop wires not checked."
                                                            if w["drop_wires_not_checked"] else "."))
    else:
        lines.append("No wire section was detected.")
    if w.get("measured_in_image"):
        lines.append(f"{w['measured_in_image']} wire section(s) were measured in the image (image-derived sag ratio, "
                     "not physical sag); a measurement is not a severity.")
    if p.get("sag_reference_comparison"):
        from app.services.comparison import counts_text
        lines.append(counts_text(p["sag_reference_comparison"]) + ".")
    if p["not_assessed_reasons"]:
        lines.append("Not assessed because: " + "; ".join(f"{k.lower()} ({v})" for k, v in
                                                          p["not_assessed_reasons"].items()) + ".")
    if p["vegetation_overlapping_wire_sections_in_image"]:
        lines.append(f"Vegetation overlaps or adjoins {p['vegetation_overlapping_wire_sections_in_image']} wire "
                     "section(s) in the image (not a measured clearance; check on site).")
    if p["retake_warning"]:
        lines.append("A retake was recommended: the assessment may be inaccurate.")
    if p["photo_quality_note"]:
        lines.append(f"Photo quality: {p['photo_quality_note']}")
    return " ".join(lines)


# --------------------------------------------------------------------------- answering
def _claude_configured(settings) -> bool:
    prov = (settings.assistant_provider or "auto").lower()
    if prov == "local":
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return prov == "anthropic" or bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def status(settings) -> dict[str, Any]:
    return {"provider": "claude" if _claude_configured(settings) else "local",
            "model": settings.assistant_model if _claude_configured(settings) else None,
            "shares_inspection_summary": bool(settings.assistant_share_context)}


def _local_text(question: str, hits: list[tuple[kb.Section, float]]) -> str:
    """The best section as written; for a list section, the items that match the question when any do."""
    best = hits[0][0]
    qt = set(kb._tokens(question))
    paras = [p.strip() for p in best.body.split("\n") if p.strip()]
    items = [p for p in paras if p.startswith(("- ", "1.", "2.", "3.", "4.", "5."))]
    if items:
        match = [p for p in items if qt & set(kb._tokens(p.split(":")[0]))]
        intro = [p for p in paras if p not in items][:1]
        if match and len(match) < len(items):
            return "\n".join(intro + match)
    return "\n".join(paras)


def _sources(ids: list[str]) -> list[dict[str, Any]]:
    out = []
    for i in ids:
        s = kb.by_id(i)
        if s and all(o["id"] != i for o in out):
            out.append({"id": s.id, "title": s.title, "link": s.link})
    return out


def _call_claude(settings, question: str, history: list[dict], hits, facts: dict | None) -> tuple[str, list[str]]:
    import anthropic

    docs = "\n".join(f'<source id="{s.id}" title="{s.title}">\n{s.body}\n</source>' for s, _ in hits)
    if facts is not None:
        import json
        docs += f'\n<source id="this-inspection" title="The inspection the user has open">\n' \
                f'{json.dumps(facts["public"], indent=1)}\n</source>'
    messages: list[dict[str, Any]] = []
    for h in history[-6:]:
        if h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str) and h["content"].strip():
            messages.append({"role": h["role"], "content": h["content"][:2000]})
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    messages.append({"role": "user", "content": f"<sources>\n{docs}\n</sources>\n\nQuestion: {question}"})
    client = anthropic.Anthropic(timeout=45.0, max_retries=1)
    resp = client.beta.messages.create(
        model=settings.assistant_model, max_tokens=2000, system=SYSTEM_PROMPT, messages=messages,
        output_config={"effort": "low"},  # short grounded answers: low effort is enough and keeps replies fast
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("refusal")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    ids: list[str] = []
    m = re.search(r"\n?Sources?:\s*(.+)\s*$", text, flags=re.I)
    if m:
        ids = [x.strip(" .`[]") for x in m.group(1).split(",")]
        text = text[: m.start()].strip()
    valid = {s.id for s, _ in hits}
    return text, [i for i in ids if i in valid]


def answer(settings, question: str, page: str | None = None, history: list[dict] | None = None,
           analysis=None, cfg=None) -> Answer:
    question = question.strip()[:1000]
    facts = inspection_facts(analysis, cfg) if analysis is not None else None
    # v2.21: intent first. Navigation, workflow, identity and assistant questions are answered from their guide
    # sections as written (numbered steps that match the real controls), not from whichever section shares a word.
    route = intent.classify(question, page)
    if route.kind == "clarify":
        return Answer("I'm not sure what you're looking for. Did you mean one of these?", [], "clarify",
                      suggest=route.clarify, intent="clarify")
    if route.sections and route.kind in ("navigation", "workflow", "identity", "assistant", "documentation"):
        secs = [s for s in (kb.by_id(i) for i in route.sections) if s is not None]
        if secs:
            body = secs[0].body.strip()
            if route.kind == "documentation" and len(secs) > 1:
                body += "\n\n" + secs[1].body.strip()
            return Answer(body, _sources([s.id for s in secs[:2]]), "guide", nav=route.nav, intent=route.kind)
    query = question
    if facts and re.search(r"\b(this|it|finding|findings|result|inspection|pole|wire)\b", question, re.I):
        p = facts["public"]
        query += f" {p['risk_level']} {p['assessment']}" + (" retake" if p["retake_warning"] else "") \
            + (" not assessed" if p["wire_sections"]["not_assessed"] else "")
    hits = kb.search(query, k=4, page=page)
    if not hits or hits[0][1] < MIN_SCORE:
        hits = []
    shown = None if facts is None else {"pole_code": facts["pole_code"], "summary": facts_summary(facts)}
    about_inspection = facts is not None and re.search(
        r"\b(this|it|finding|findings|result|inspection|pole|wire|why)\b", question, re.I)
    if not hits and not about_inspection:
        return Answer(NOT_DOCUMENTED, [], "local", shown)
    if _claude_configured(settings):
        try:
            share = facts if (facts and settings.assistant_share_context) else None
            text, ids = _call_claude(settings, question, history or [], hits, share)
            if text:
                return Answer(text, _sources(ids or [s.id for s, _ in hits[:1]]), "claude", shown)
        except Exception as exc:  # provider down, credentials rejected, rate limited, refusal: never a dead end
            log.warning("assistant provider unavailable: %s", type(exc).__name__)
            if not hits:
                return Answer(NOT_DOCUMENTED, [], "fallback", shown)
            lead = f"This inspection: {facts_summary(facts)}\n\n" if about_inspection else ""
            return Answer(lead + _local_text(question, hits), _sources([s.id for s, _ in hits[:2]]), "fallback", shown)
    lead = f"This inspection: {facts_summary(facts)}\n\n" if about_inspection else ""
    if not hits:
        return Answer(lead + "For what a term means, ask about it directly, for example \"What does not assessed "
                      "mean?\".", [], "local", shown)
    return Answer(lead + _local_text(question, hits), _sources([s.id for s, _ in hits[:2]]), "local", shown)
