"""v2.20 human-readable pole labels, e.g. ``SAL-001`` for the first pole recorded in Salitran.

Derived only from what is recorded: the barangay typed on upload or assigned by an administrator, else whether the
pole has GPS (``LOC-``: located, no barangay; ``UNP-``: no location, "unpinned"). v2.21: the label is stored on
the pole when first given and changes only when that pole's own prefix changes (it gets the next free number). The pole code (``P-71D2F7``)
stays the immutable identifier in URLs, exports and the database; the label is for display.
"""

from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.orm import Pole, PoleLocation


def prefix(barangay: str | None, has_location: bool) -> str:
    from app.services.barangay import is_valid
    if barangay and is_valid(barangay):  # v2.21: only an official barangay names a pole
        a = unicodedata.normalize("NFKD", barangay).encode("ascii", "ignore").decode()
        letters = re.sub(r"[^A-Za-z]", "", re.sub(r"^(barangay|brgy\.?)\s+", "", a.strip(), flags=re.I))
        if len(letters) >= 3:
            return letters[:3].upper()
    return "LOC" if has_location else "UNP"


def all_labels(s: Session) -> dict[str, str]:
    """{pole_code: label} for every pole. v2.21: labels are stored on the pole. A pole keeps its label while its
    prefix still fits (same barangay, still located or still unpinned); only a pole whose own prefix changed gets a
    new number, the next free one in its new prefix. Before v2.21 labels were renumbered on every call by creation
    order, so pinning one pole shifted the labels of others."""
    located = set(s.scalars(select(PoleLocation.pole_id)).all())
    poles = s.scalars(select(Pole).order_by(Pole.id)).all()
    top: dict[str, int] = {}
    want = {p.id: prefix(p.barangay, p.id in located) for p in poles}
    keep = {p.id for p in poles if p.label and p.label.rsplit("-", 1)[0] == want[p.id]}
    for p in poles:
        if p.id in keep:
            pre, num = p.label.rsplit("-", 1)
            top[pre] = max(top.get(pre, 0), int(num) if num.isdigit() else 0)
    for p in poles:
        if p.id not in keep:
            top[want[p.id]] = top.get(want[p.id], 0) + 1
            p.label = f"{want[p.id]}-{top[want[p.id]]:03d}"
    return {p.pole_code: p.label for p in poles}
