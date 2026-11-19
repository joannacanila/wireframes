"""Knowledge base and retrieval (BM25 over sections of knowledge.md; no external service, no dependency).

The knowledge base is curated text, not source code: it holds no credentials, file paths of secrets, private
records or developer internals, so whatever is retrieved can be shown to any signed-in user.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

KB_PATH = Path(__file__).with_name("knowledge.md")
_HEAD = re.compile(r"^## (.+?) \{#([a-z0-9-]+)\}\s*$")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = set("""a an and are as at be been but by can could do does did for from has have how i if in into is it its
me my no not of on or our so than that the their them then there these they this to too us was we were what when
where which who why will with would you your about any just should may might""".split())
# plain-language variants a user may type -> words the documentation uses
_SYN = {
    "safe": ["certification", "certify", "safety"], "certify": ["certification", "safety"],
    "dangerous": ["high", "risk"], "danger": ["high", "risk"], "hazard": ["risk", "high"],
    "sagging": ["sag"], "sags": ["sag"], "droop": ["sag"], "drooping": ["sag"], "low": ["sag", "low"],
    "missing": ["detected", "missed"], "miss": ["missed", "detected"], "missed": ["detected"],
    "undetected": ["detected", "missed"], "detect": ["detected", "detection"], "found": ["detected"],
    "incomplete": ["incomplete", "partial", "assessed"], "partial": ["incomplete", "assessed"],
    "unassessed": ["assessed"], "grey": ["uncertain", "type"], "gray": ["uncertain", "type"],
    "unclassified": ["uncertain", "type"], "unknown": ["uncertain"],
    "gps": ["gps", "location"], "coordinates": ["gps", "location"], "location": ["gps", "location"],
    "directions": ["directions", "walking"], "navigate": ["directions"], "route": ["directions"],
    "rank": ["ranking", "ranked", "order"], "ranked": ["ranking", "order"], "priority": ["ranking", "order"],
    "prioritise": ["ranking"], "prioritize": ["ranking"], "dispatch": ["map", "order"],
    "pdf": ["pdf", "report"], "export": ["csv", "report", "reports"], "download": ["report", "csv"],
    "csv": ["csv", "reports"], "approve": ["review", "confirm"], "confirm": ["review", "confirm"],
    "override": ["review", "level"], "flag": ["flag", "review"], "dismiss": ["dismiss", "review"],
    "error": ["failed", "processing"], "fail": ["failed"], "fails": ["failed"], "failure": ["failed"],
    "crash": ["failed"], "retry": ["failed", "retry"], "upload": ["submitting", "photo"],
    "picture": ["photo"], "image": ["photo"], "retake": ["retake", "photo"], "blurry": ["photo", "blur"],
    "dark": ["photo", "light"], "accurate": ["accuracy"], "accuracy": ["accuracy", "f1"],
    "trees": ["vegetation"], "tree": ["vegetation"], "branches": ["vegetation"], "leaves": ["vegetation"],
    "login": ["sign", "accounts"], "password": ["accounts", "sign"], "account": ["accounts"],
    "chatbot": ["assistant"], "bot": ["assistant"], "warning": ["warnings", "retake"],
    "telecom": ["telecom", "types"], "fiber": ["telecom"], "fibre": ["telecom"], "abandoned": ["abandoned", "types"],
    "dead": ["abandoned"], "power": ["power"], "electric": ["power"], "map": ["map"],
    "moderate": ["moderate", "risk"], "medium": ["moderate", "risk"], "high": ["high", "risk"],
}


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    body: str
    link: str | None

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.body}"


def _tokens(s: str) -> list[str]:
    out = []
    for w in _WORD.findall(s.lower()):
        if w in _STOP:
            continue
        out.append(_stem(w))
    return out


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


@lru_cache(maxsize=4)
def sections(path: Path = KB_PATH) -> tuple[Section, ...]:
    """The sections of one knowledge file (S14: the staff guide, knowledge.md, or the citizen guide, citizen.md)."""
    text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.S)
    out, cur, title, sid, link = [], [], None, None, None
    for line in text.splitlines():
        m = _HEAD.match(line)
        if m:
            if sid:
                out.append(Section(sid, title, "\n".join(cur).strip(), link))
            title, sid, cur, link = m.group(1), m.group(2), [], None
        elif sid and line.startswith("Link: "):
            link = line[6:].strip()
        elif sid:
            cur.append(line)
    if sid:
        out.append(Section(sid, title, "\n".join(cur).strip(), link))
    return tuple(out)


@lru_cache(maxsize=4)
def _index(path: Path = KB_PATH):
    secs = sections(path)
    docs = [_tokens(s.title) * 3 + _tokens(s.body) for s in secs]  # titles weigh more
    df: dict[str, int] = {}
    for d in docs:
        for t in set(d):
            df[t] = df.get(t, 0) + 1
    avg = sum(map(len, docs)) / max(1, len(docs))
    return secs, docs, df, avg


def search(query: str, k: int = 4, page: str | None = None, path: Path = KB_PATH) -> list[tuple[Section, float]]:
    """Top-k sections by BM25, a small boost for the section that describes the current page."""
    secs, docs, df, avg = _index(path)
    q = []
    for t in _tokens(query):
        q.append(t)
        q += [_stem(x) for x in _SYN.get(t, [])]
    if not q:
        return []
    n, k1, b = len(docs), 1.4, 0.7
    scored = []
    for s, d in zip(secs, docs, strict=True):
        tf: dict[str, int] = {}
        for t in d:
            tf[t] = tf.get(t, 0) + 1
        score = 0.0
        for t in set(q):
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avg)) * q.count(t) ** 0.5
        if page and s.link and score > 0 and page.startswith(s.link) and s.link != "/admin":
            score *= 1.1
        if score > 0:
            scored.append((s, score))
    scored.sort(key=lambda x: -x[1])
    return scored[:k]


def by_id(sid: str, path: Path = KB_PATH) -> Section | None:
    return next((s for s in sections(path) if s.id == sid), None)
