"""Bind retrieved experience cases and compatible techniques to a segment.

The real search_experience returns {"items": [...], "unavailable": bool}; each
item exposes id, summary, applicability, sources and compatible_technique_ids.
Technique versions are resolved from the local catalogue because the search
result only carries technique IDs.
"""

from __future__ import annotations

from novelvideo.freezone.video_director.techniques import list_techniques
from novelvideo.technique_library.experience import search_experience


def _case_reference(item: dict) -> dict:
    sources = item.get("sources") or []
    source = sources[0] if sources else {}
    return {
        "case_id": str(item["id"]),
        "source_id": str(source.get("id") or ""),
        "provenance": str(source.get("provenance") or ""),
    }


def _technique_reference(compatible_ids) -> dict | None:
    allowed = set(compatible_ids or [])
    if not allowed:
        return None
    try:
        cards = list_techniques()
    except (OSError, ValueError):
        return None
    for card in cards:
        if card.id in allowed and card.status == "active":
            return {"id": card.id, "version": card.version}
    return None


def bind_experience(*, intent: str, mode: str, duration: float,
                    user_technique: dict | None) -> tuple[dict | None, dict | None]:
    """Pick the first usable case plus one compatible, active technique.

    No match is a normal creation path (no case, no technique). An explicit
    user technique is never replaced silently: it must be compatible, and a
    missing catalogue raises technique_unavailable instead of binding nothing.
    """
    found = search_experience(intent, mode, duration, 5)
    items = found.get("items") or []
    if not items:
        if user_technique is not None:
            if found.get("unavailable"):
                raise ValueError(
                    "technique_unavailable: experience catalog is unavailable"
                )
            raise ValueError(
                "technique_conflict: requested technique has no compatible case"
            )
        return None, None

    case_reference = _case_reference(items[0])
    if user_technique is not None:
        compatible = items[0].get("compatible_technique_ids") or []
        if user_technique.get("id") not in compatible:
            raise ValueError(
                "technique_conflict: requested technique is not compatible"
            )
        return dict(user_technique), case_reference

    return _technique_reference(items[0].get("compatible_technique_ids")), case_reference


__all__ = ["bind_experience"]
