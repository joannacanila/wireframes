"""v2.21 intent detection for the WIREFRAME Assistant (runs before retrieval).

Why: retrieval alone ranked by shared keywords. "How can I go to the poles?" lost every word except "pole" to the
stop-word list and came back with the ranking and pole-detector sections; "Who made you?" lost "who" and "you" and
fell below the answer threshold. The whole question is now normalised and classified first:

- identity     who made / built / developed WIREFRAME (or "you" / "this system"), the authors, the school
- assistant    what the assistant is and what it can do
- navigation   how to get to, find, open or access a page or thing ("go to the poles", "where are the reports")
- workflow     how to do a task (build / optimise / export a route, pin a pole, filter, assign a barangay)
- technical / documentation: everything else, answered by retrieval as before
- clarify      too vague to answer ("poles?"): a short question with choices instead of unrelated documentation

Navigation and workflow outrank shared keywords: "how do I get to the poles" is navigation even though it mentions
poles. Each route names documentation sections (knowledge.md) and portal destinations that exist (app routes).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CONTRACTIONS = {"what's": "what is", "where's": "where is", "who's": "who is", "how's": "how is", "can't": "cannot",
                "don't": "do not", "doesn't": "does not", "i'm": "i am", "it's": "it is", "whats": "what is",
                "wheres": "where is", "whos": "who is"}
SLANG = {"u": "you", "ur": "your", "pls": "please", "plz": "please", "wat": "what", "wer": "where", "hw": "how",
         "poles?": "poles", "pdfs": "pdf", "gps-less": "without gps", "dispatcher": "dispatch"}

# destinations that exist in app/web/assets/admin.js ROUTES and admin-map.js (?tab=pinning)
NAV = {
    "map": {"label": "Map & Locations", "href": "/admin/map"},
    "pinning": {"label": "Needs pinning (Map & Locations)", "href": "/admin/map?tab=pinning"},
    "inspections": {"label": "Inspections", "href": "/admin/inspections"},
    "dashboard": {"label": "Dashboard", "href": "/admin"},
    "screener": {"label": "Field Screener", "href": "/workspace"},
}


@dataclass
class Route:
    kind: str
    sections: list[str] = field(default_factory=list)
    nav: list[dict] = field(default_factory=list)
    clarify: list[str] = field(default_factory=list)  # suggested follow-up questions


def normalize(q: str) -> str:
    t = q.lower().strip()
    t = re.sub(r"[‘’`´]", "'", t)
    for a, b in CONTRACTIONS.items():
        t = re.sub(rf"\b{re.escape(a)}\b", b, t)
    t = " ".join(SLANG.get(w, w) for w in t.split())
    t = re.sub(r"[^\w\s/'-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


SELF = r"(you|yourself|this (system|app|application|tool|platform|website|site|project|assistant)|wireframe|the (system|app|assistant|project|platform)|it)"
MADE = r"(made|make|create|created|creator|creators|built|build|develop|developed|developer|developers|design|designed|wrote|program|programmed|author|authors|behind|own|owns)"
NAV_VERB = (r"(go to|get to|go see|reach|navigate|access|open|find|locate|see|view|look at|where (is|are|do i|can i|to)|"
            r"take me|bring me|show me where|how (do|can|would) i (get|go|find|open|see|reach|access|view))")
TARGETS = [  # (pattern, sections, nav keys) in priority order
    (r"\b(needs? pinning|unpinned|without (a )?(gps|location)|no (gps|location)|missing (gps|location)|pin (a |the )?(pole|location))\b", ["nav-pinning"], ["pinning"]),
    (r"\b(dispatch route|route|routes|stops?)\b", ["route-build"], ["map"]),
    (r"\b(poles?|map|dispatch|markers?|pins?|locations?|sites?)\b", ["nav-poles"], ["map"]),
    (r"\b(reports?|pdf|inspections?|results?|findings|csv)\b", ["nav-reports"], ["inspections"]),
    (r"\b(dashboard|home|overview|queue|needs attention)\b", ["nav-portal"], ["dashboard"]),
    (r"\b(system|models?|settings|test records|evaluation|technical exports?|admin tools)\b", ["system", "nav-portal"], ["inspections"]),
    (r"\b(screener|upload|new inspection|take a photo|submit)\b", ["submit", "nav-portal"], ["screener"]),
]
WORKFLOW = [
    (r"\b(directions?|walk|walking|drive|driving|street view|navigate to (a|the) pole)\b", ["directions", "nav-poles"], ["map"]),
    (r"\b(optimi[sz]e|reorder|re-order|best order|shortest|order (the )?stops)\b", ["route-build"], ["map"]),
    (r"\b(export|print|share|send|download|copy)\b.*\b(route|stops)\b|\broute (pdf|link)\b", ["route-build"], ["map"]),
    (r"\b(build|create|make|plan|start)\b.*\b(route|dispatch)\b|\b(add|select|choose|pick)\b.*\b(poles?|stops?)\b.*\b(route|dispatch)?", ["route-build"], ["map"]),
    (r"\b(pin|assign|set|add|enter)\b.*\b(location|gps|coordinates|pin)\b", ["nav-pinning"], ["pinning"]),
    (r"\b(assign|set|change|fix)\b.*\bbarangay\b", ["assign-barangay"], ["inspections"]),
    (r"\b(filter|sort)\b.*\b(poles?|map|risk|severity|barangay)\b", ["filter-poles"], ["map"]),
    (r"\b(review|confirm|flag|dismiss|override)\b.*\b(inspection|finding|result|pole)\b", ["review"], ["inspections"]),
    (r"\b(export|download|print)\b.*\b(report|pdf|csv|list)\b", ["reports", "nav-reports"], ["inspections"]),
]


def classify(question: str, page: str | None = None) -> Route:
    q = normalize(question)
    words = q.split()
    # identity: who made you / who developed this system / who are the researchers
    made_by = bool(re.search(rf"\bwho\b.*\b{MADE}\b", q)) and (bool(re.search(rf"\b{SELF}\b", q)) or len(words) <= 4)
    people = bool(re.search(r"\b(authors?|researchers?|proponents?|creators?|developers? of)\b", q)) and \
        bool(re.search(r"\b(who|name|names|list|tell|are)\b", q))
    school = bool(re.search(r"\b(which|what) (school|university|college|institution)\b", q))
    if made_by or people or school:
        return Route("identity", ["authors", "overview"])
    if re.search(r"\b(who|what) are you\b|\bwhat can you do\b|\bhow can you help\b|\bwhat (do|can) you (do|help)\b|\bwhat is this assistant\b", q):
        return Route("assistant", ["assistant"])
    if re.search(r"\bwhat is wireframe\b|\bwhat does wireframe do\b|\bpurpose of (wireframe|this (system|app))\b", q):
        return Route("documentation", ["overview", "portals"])
    # workflow before navigation: "how do I export the route" is a task, not a place
    if not re.search(r"\b(why|what does|what is|mean|means)\b", q):  # a task phrased as a question or an instruction
        for rx, secs, nav in WORKFLOW:
            if re.search(rx, q):
                return Route("workflow", secs, [NAV[k] for k in nav])
    if re.search(rf"\b{NAV_VERB}\b", q):
        for rx, secs, nav in TARGETS:
            if re.search(rx, q):
                return Route("navigation", secs, [NAV[k] for k in nav])
        return Route("navigation", ["nav-portal"], [NAV["dashboard"], NAV["inspections"], NAV["map"]])
    # one or two vague words with no verb: ask instead of guessing
    if len(words) <= 2 and not re.search(r"\b(what|why|how|when|which|is|are|does|mean|explain)\b", q):
        topic = " ".join(words)
        return Route("clarify", clarify=[f"How do I get to the {topic}?" if topic else "How do I get to the poles?",
                                         f"What does {topic} mean in WIREFRAME?" if topic else "What does high risk mean?",
                                         "What can you do?"])
    return Route("technical" if re.search(r"\b(sag|crs|vni|mspi|model|detect|detection|accuracy|f1|catenary|segment|classif|telecom|mask|confidence)\w*", q)
                 else "documentation")
