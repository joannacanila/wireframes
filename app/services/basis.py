"""Assessment basis of a stored result (S4, 18 Nov 2026; register D-19, D-21).

Results analysed by v2.23 or earlier used provisional criteria that research found unsupported and that were removed:
the telecom sag reference (4.0 %), the Minor-sag band (2.4 %), the vegetation rules in pixels and the assumed
conductor data. Those inspections are never reprocessed and their stored rows are never changed. When they are shown
(page, PDF, admin, assistant), their severity labels and screening verdict are not presented as findings:

* a stored wire level whose class has no validated reference in the current configuration is shown as "not
  assessed" (reason ``withdrawn_reference``), with the stored value kept under ``historical`` for audit;
* a stored pole verdict from before S4 that is not already INCONCLUSIVE is shown as INCONCLUSIVE with a notice, the
  stored verdict kept under ``historical_verdict``.

New analyses carry ``metadata_json["assessment_basis"] = BASIS`` and are shown as stored.
"""

from __future__ import annotations

from typing import Any

BASIS = "S4-2026-11-18"
INCONCLUSIVE = "INCONCLUSIVE - RETAKE PHOTO"
WITHDRAWN_NOTICE = ("This inspection was analysed with provisional sag and vegetation criteria (WIREFRAME v2.23 or "
                    "earlier) that were withdrawn on 18 Nov 2026 because no engineering source supports them. Its "
                    "original severity labels and verdict are not findings; the measurements remain. A new analysis "
                    "or a field inspection is needed.")


def is_current(md: dict | None) -> bool:
    return (md or {}).get("assessment_basis") == BASIS


def hazard_view(md: dict | None) -> dict | None:
    """The pole verdict to show for a stored result."""
    hz = (md or {}).get("hazard")
    if not hz or is_current(md) or hz.get("verdict") == INCONCLUSIVE:
        return hz
    return {**hz, "verdict": INCONCLUSIVE, "historical_verdict": hz.get("verdict"),
            "historical_reasons": hz.get("reasons") or [],
            "reasons": [f"{INCONCLUSIVE}: {WITHDRAWN_NOTICE}"], "withdrawn_criteria": True}


def verdict_of(md: dict | None) -> str | None:
    return (hazard_view(md) or {}).get("verdict")


def level_withdrawn(level: str | None, wire_class: str | None, cfg: Any, md: dict | None = None) -> bool:
    """A stored level is withdrawn when its class has no validated reference in the current configuration, or (with
    ``md``, S5) when the record was analysed before S4: its level was computed against the withdrawn placeholder, so
    it stays withdrawn even if a validated reference is configured later."""
    if not level:
        return False
    return not cfg.crs.has_reference(wire_class or "") or (md is not None and not is_current(md))
