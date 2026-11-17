"""v2.21 barangay validation against the official list (PSGC, app/data/barangays_cavite.json).

A barangay is stored only when it matches an official barangay name. Matching tolerates the ways people type them:
case, accents, a "Brgy." / "Barangay" prefix, extra spaces and punctuation, and Arabic instead of Roman numerals
("anabu 2-d" -> "Anabu II-D", "Salitran 3" -> "Salitran III"). Anything else (a person's name, "Test Barangay",
a street) is not a barangay: the upload keeps the typed text in the inspection's metadata as ``barangay_entered``
and leaves the barangay unassigned; an administrator's assignment is refused with a message.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data" / "barangays_cavite.json"
ROMAN = {"1": "i", "2": "ii", "3": "iii", "4": "iv", "5": "v", "6": "vi", "7": "vii", "8": "viii", "9": "ix", "10": "x"}


def _key(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"^\s*(barangay|brgy\.?|bgy\.?)\s+", "", t)
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    return " ".join(ROMAN.get(w, w) for w in t.split())


@lru_cache(maxsize=1)
def _index() -> tuple[dict[str, list[dict]], list[dict]]:
    rows = json.loads(DATA.read_text(encoding="utf-8"))["barangays"]
    idx: dict[str, list[dict]] = {}
    for r in rows:
        idx.setdefault(_key(r["name"]), []).append(r)
    return idx, rows


def resolve(text: str | None, city: str | None = None) -> str | None:
    """The official barangay name for ``text``, or None when it is not one."""
    if not text or not text.strip():
        return None
    hits = _index()[0].get(_key(text))
    if not hits:
        return None
    if city and len(hits) > 1:
        ck = _key(re.sub(r"(?i)^city of\s+", "", city))
        pick = [h for h in hits if ck and ck in _key(h["city"])]
        if pick:
            return pick[0]["name"]
    return hits[0]["name"]


def suggest(q: str, limit: int = 8) -> list[dict]:
    """Official barangays whose name starts with (then contains) the typed text, for the assignment picker."""
    k = _key(q or "")
    if not k:
        return []
    rows = _index()[1]
    starts = [r for r in rows if _key(r["name"]).startswith(k)]
    has = [r for r in rows if k in _key(r["name"]) and r not in starts]
    return (starts + has)[:limit]


def is_valid(name: str | None) -> bool:
    return bool(name) and resolve(name) == name
