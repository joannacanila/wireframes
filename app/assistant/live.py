"""v2.20 WIREFRAME Assistant, layers 1 and 3: live operational answers and interface actions.

Layer 1 (live context). Questions about the current workload ("Which high-risk poles don't have GPS?", "What needs
review?", "Summarize today's dispatch priorities") are answered from the database, never by a language model:
every pole label, count and status in the answer is read from the records the signed-in user may see (operational
records; test records only for administrators who ask for them elsewhere). Nothing here leaves the server.

Layer 3 (interface actions). Requests such as "Show only high-risk poles", "Open pole SAL-001", "Find the poles
without GPS" or "Locate this inspection on the map" become one *navigation* action: an address inside the portal
that the browser opens and then reports. The assistant has no write actions: it cannot review, delete, re-classify
or move a pole. Asked to, it says where the user can do it.

Matching is deliberately simple (keywords and pole identifiers); a question that matches nothing here goes on to
the documentation answer (service.answer).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.orm import Analysis, Pole
from app.services import records
from app.services.labels import all_labels

POLE_REF = re.compile(r"\b([A-Z]{3}-\d{3}|P-[0-9A-F]{4,12})\b", re.I)
RISK_WORDS = {"high": r"high[- ]?risk|\bhigh\b|dangerous|urgent", "medium": r"moderate|medium",
              "low": r"low[- ]?risk|\blow\b", "unassessed": r"not assessed|unassessed"}
WRITE_VERBS = re.compile(r"\b(delete|remove|erase|approve|confirm|dismiss|override|mark .* as (a )?test|"
                         r"change the (risk|level)|set the (risk|level)|re-?classify)\b", re.I)


@dataclass
class Live:
    text: str
    poles: list[dict[str, Any]] = field(default_factory=list)  # compact pole cards
    action: dict[str, Any] | None = None  # {"type": "navigate", "href", "label"}
    kind: str = "live"


def _snapshot(s: Session) -> tuple[list[dict], dict[str, str]]:
    """Each pole's latest inspection (operational records only), as the dashboard counts them."""
    labels = all_labels(s)
    rows = s.scalars(select(Analysis).options(
        selectinload(Analysis.pole).selectinload(Pole.location),
        selectinload(Analysis.detections).load_only(*records.list_columns()),
        selectinload(Analysis.reviews)).order_by(Analysis.created_at.desc())).all()
    latest: dict[int, Analysis] = {}
    for a in rows:
        if records.is_operational(a) and a.pole_id not in latest:
            latest[a.pole_id] = a
    return [records.row(a, labels) for a in latest.values()], labels


def _card(r: dict, rank: int | None = None) -> dict[str, Any]:
    w = r.get("wires") or {}
    issue = None
    if r["risk"] in ("failed", "processing"):
        issue = None  # the risk label already says it
    elif w.get("high"):
        issue = f"{w['high']} wire section{'s' if w['high'] != 1 else ''} at high risk"
    elif w.get("medium"):
        issue = f"{w['medium']} wire section{'s' if w['medium'] != 1 else ''} at moderate risk"
    elif r["risk"] == "unassessed":
        issue = "No wire section could be assessed"
    return {"id": r["id"], "pole_code": r["pole_code"], "label": r.get("label") or r["pole_code"],
            "risk": r["risk"], "risk_label": records.RISK_LABEL.get(r["risk"], r["risk"]),
            "review": records.REVIEW_LABEL.get(r["review"], r["review"]),
            "place": ", ".join(x for x in (r.get("barangay"), r.get("city")) if x) or None,
            "location": r.get("location"), "issue": r.get("op_reason") or issue, "rank": rank,
            "status": r.get("op_status_label"), "review_status": r.get("review_status_label"),
            "triage": (r.get("risk_level") or {}).get("short"), "file": r.get("filename")}


def _name(c: dict) -> str:
    return c["label"] if c["label"] == c["pole_code"] else f"{c['label']} ({c['pole_code']})"


def _list_text(intro: str, cards: list[dict], empty: str, limit: int = 8) -> str:
    if not cards:
        return empty
    lines = [intro]
    for c in cards[:limit]:
        bits = [c.get("status") or c["risk_label"]] + ([c["place"]] if c["place"] else []) + ([c["issue"]] if c["issue"] else [])
        lines.append(f"- {_name(c)}: " + " · ".join(bits))
    if len(cards) > limit:
        lines.append(f"- and {len(cards) - limit} more")
    return "\n".join(lines)


def _risks_in(q: str) -> list[str]:
    return [k for k, rx in RISK_WORDS.items() if re.search(rx, q, re.I)]


def handle(s: Session, question: str, page: str | None, analysis: Analysis | None, ranking: list[dict] | None
           ) -> Live | None:
    """A live answer or an action for ``question``, or None to fall through to the documentation."""
    q = question.strip()
    ql = q.lower()
    page = page or ""

    if WRITE_VERBS.search(ql) and re.search(r"\b(this|pole|inspection|record|wire|it)\b", ql):
        return Live("I can't change records. To record a decision, open the inspection and use Professional review "
                    "(confirm, flag, change the risk level or dismiss, with a note). Administrators can mark a record "
                    "as a test record, or delete it, from the inspection's \"…\" menu.", kind="refused")

    rows, labels = _snapshot(s)
    by_code = {r["pole_code"].upper(): r for r in rows}
    by_label = {(r.get("label") or "").upper(): r for r in rows if r.get("label")}
    rank_of = {x["pole_code"]: x.get("rank") for x in ranking or []}

    # ---- "open / show / locate pole SAL-001"
    m = POLE_REF.search(q)
    if m and re.search(r"\b(open|show|go to|find|locate|where|take me)\b", ql):
        ref = m.group(1).upper()
        r = by_label.get(ref) or by_code.get(ref)
        if r is None:
            return Live(f"No pole {ref} is in the operational records you can see.")
        c = _card(r, rank_of.get(r["pole_code"]))
        if re.search(r"\b(map|locate|where)\b", ql):
            if not r.get("location"):
                return Live(f"{_name(c)} has no GPS location yet, so it is listed under Needs pinning on Map & "
                            "Locations.", [c], {"type": "navigate", "href": f"/admin/map?pole={r['pole_code']}",
                                               "label": f"Open {c['label']} in Map & Locations"})
            return Live(f"Showing {_name(c)} on the map.", [c],
                        {"type": "navigate", "href": f"/admin/map?pole={r['pole_code']}", "label": f"Locate {c['label']}"})
        return Live(f"Opening the latest inspection of {_name(c)}.", [c],
                    {"type": "navigate", "href": f"/admin/inspections/{r['id']}", "label": f"Open {c['label']}"})

    # ---- "locate this inspection / pole on the map"
    if analysis is not None and re.search(r"\b(this|it)\b", ql) and re.search(r"\b(map|locate|where)\b", ql):
        code = analysis.pole.pole_code
        lab = labels.get(code, code)
        if analysis.pole.location is None:
            return Live(f"This pole ({lab}) has no GPS location yet. An administrator can drop a pin for it on Map & "
                        "Locations, under Needs pinning.", [],
                        {"type": "navigate", "href": f"/admin/map?pole={code}", "label": "Open Map & Locations"})
        return Live(f"Showing {lab} on the map.", [],
                    {"type": "navigate", "href": f"/admin/map?pole={code}", "label": f"Locate {lab}"})

    no_gps = re.search(r"(no|without|missing|lack\w*|don'?t have|do not have|unpinned|need\w* pinning)\s*(a\s+)?"
                       r"(gps|location|coordinates|pin)", ql) or "needs pinning" in ql or "unpinned" in ql
    risks = _risks_in(ql)
    showing = re.search(r"^\s*(show|filter|list|display|find|only)\b|\bshow (me )?only\b", ql)

    # ---- poles without GPS (optionally of one risk level)
    if no_gps:
        sel = [r for r in rows if not r.get("location") and (not risks or r["risk"] in risks)]
        sel.sort(key=lambda r: records_priority(r))
        cards = [_card(r, rank_of.get(r["pole_code"])) for r in sel]
        what = (" and ".join(records.RISK_LABEL[k].lower() for k in risks) + " poles") if risks else "poles"
        text = _list_text(f"{len(cards)} {what} have no GPS location:", cards,
                          f"Every {what[:-1] if what.endswith('s') else what} has a GPS location." if rows else
                          "There are no inspected poles yet.")
        if cards:
            text += "\nMissing GPS does not lower a pole's priority. An administrator can drop a pin for each one on " \
                    "Map & Locations, under Needs pinning."
        return Live(text, cards[:6], {"type": "navigate", "href": "/admin/map?tab=pinning",
                                      "label": "Show Needs pinning"} if showing or "map" in page else None)

    # ---- failed inspections
    if re.search(r"\b(fail\w*|error\w*|could not be processed|unprocessed)\b", ql):
        sel = [r for r in rows if r["risk"] in ("failed", "processing")]
        cards = [_card(r) for r in sel]
        text = _list_text(f"{len(cards)} pole{'s' if len(cards) != 1 else ''} have no finished inspection "
                          "(processing failed or still running):", cards, "No inspection is failed or waiting.")
        if cards:
            text += "\nOpen one to see whether it can be retried. A failed run is not a result: the pole is unscreened."
        return Live(text, cards[:6], {"type": "navigate", "href": "/admin/inspections?tab=failed",
                                      "label": "Show failed inspections"})

    # ---- needs review / urgent
    if re.search(r"\b(review|urgent|attention|pending|flagged)\b", ql) and re.search(
            r"\b(which|what|who|list|show|need|needs|pending|urgent)\b", ql) and not re.search(r"\bhow\b", ql):
        # S14: the dashboard's "Needs review" status (app/services/ops.py), so the numbers match the portal
        sel = [r for r in rows if r.get("op_status") == "flagged"]
        sel.sort(key=lambda r: records_priority(r))
        cards = [_card(r, rank_of.get(r["pole_code"])) for r in sel]
        intro = f"{len(cards)} pole{'s' if len(cards) != 1 else ''} need{'s' if len(cards) == 1 else ''} review, most serious first:"
        return Live(_list_text(intro, cards, "No pole needs review right now."), cards[:6],
                    {"type": "navigate", "href": "/admin/inspections?tab=flagged", "label": "Show Needs review"})

    # ---- show only <risk> poles (a filter on the current page)
    if showing and risks:
        k = risks[0]
        if "map" in page:
            href = f"/admin/map?risk={k}"
        else:
            href = f"/admin/inspections?risk={k}&latest_only=1"
        n = sum(1 for r in rows if r["risk"] == k)
        return Live(f"Showing the {n} pole{'s' if n != 1 else ''} whose latest inspection is "
                    f"{records.RISK_LABEL[k].lower()}.", [], {"type": "navigate", "href": href,
                                                               "label": f"Show {records.RISK_LABEL[k].lower()}"})

    # ---- workload / dispatch summary
    if re.search(r"\b(summar\w*|overview|workload|status of|how many|count|dispatch priorit\w*|priorities|today)\b", ql) \
            and not re.search(r"\b(this inspection|this pole|this result)\b", ql):
        counts = {k: sum(1 for r in rows if r["risk"] == k) for k in ("high", "medium", "low", "unassessed")}
        failed = sum(1 for r in rows if r["risk"] in ("failed", "processing"))
        pending = sum(1 for r in rows if r["review"] in ("pending", "flagged"))
        no_loc = sum(1 for r in rows if not r.get("location"))
        lines = [f"{len(rows)} registered poles: {counts['high']} high risk, {counts['medium']} moderate, "
                 f"{counts['low']} low, {counts['unassessed']} not assessed"
                 + (f", {failed} without a finished inspection." if failed else "."),
                 f"{pending} waiting for a review; {no_loc} without a GPS location."]
        ranked = [x for x in (ranking or []) if x.get("rank")]
        ranked.sort(key=lambda x: x["rank"])
        cards = []
        for x in ranked[:5]:
            r = by_code.get(x["pole_code"].upper())
            if r:
                cards.append(_card(r, x["rank"]))
        if cards:
            lines.append("Inspection order, first five:")
            for c in cards:
                lines.append(f"- {c['rank']}. {_name(c)}: {c['risk_label']}"
                             + (f" · {c['place']}" if c["place"] else "") + ("" if c["location"] else " · no GPS"))
        return Live("\n".join(lines), cards, {"type": "navigate", "href": "/admin/map", "label": "Open Map & Locations"}
                    if "dispatch" in ql or "priorit" in ql else None)
    return None


def records_priority(r: dict) -> tuple:
    order = {"failed": 0, "processing": 0, "high": 1, "medium": 2, "unassessed": 3, "low": 4}
    from datetime import datetime
    return (0 if r["review"] == "flagged" else 1, order.get(r["risk"], 9),
            -datetime.fromisoformat(r["created_at"]).timestamp())
