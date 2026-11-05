"""Read-only experience retrieval over the checked-in case and technique catalogs."""

from __future__ import annotations

from math import isfinite

from novelvideo.freezone.video_director import techniques

from . import catalog


_TECHNIQUE_MODE = {
    "i2v": "i2v",
    "i2va": "i2v",
    "fl2v": "fl2v",
    "fl2va": "fl2v",
    "ref_only": "ref_only",
    "ref_plus_first": "ref_only",
    "ref_plus_first_last": "ref_only",
    "ref2va": "ref_only",
}

_MODES_WITH_LAST_FRAME = frozenset({"fl2v", "fl2va", "ref_plus_first_last"})


def _frame_constraint_matches(card, mode: str) -> bool:
    """Check catalog last-frame rules against the actual requested input mode."""
    has_last_frame = mode in _MODES_WITH_LAST_FRAME
    constraint = card.applicability.last_frame_constraint
    return not (
        (constraint == "required" and not has_last_frame)
        or (constraint == "forbidden" and has_last_frame)
    )


def search_experience(
    query: str,
    mode: str,
    duration_seconds: float,
    limit: int = 5,
) -> dict:
    """Return short, attributed case summaries and compatible technique IDs.

    Source prompts and media are intentionally excluded. The caller can use a
    case ID with ``get_case`` when it needs more detail.
    """
    normalized_mode = mode.strip().lower() if isinstance(mode, str) else ""
    technique_mode = _TECHNIQUE_MODE.get(normalized_mode)
    if technique_mode is None:
        raise ValueError(f"unsupported mode: {normalized_mode or '<empty>'}")
    if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float)):
        raise ValueError("duration_seconds must be a positive finite number")
    if not isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("duration_seconds must be a positive finite number")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")

    try:
        case_results = catalog.query_cases(q=query.strip(), limit=100)
        cards = techniques.list_techniques()
    except (catalog.CatalogUnavailable, OSError, ValueError):
        return {"items": [], "unavailable": True}

    compatible_by_case: dict[str, list[str]] = {}
    for card in cards:
        if card.status != "active":
            continue
        if not _frame_constraint_matches(card, normalized_mode):
            continue
        applicability = techniques.check_applicability(card, technique_mode, duration_seconds)
        if not applicability["compatible"]:
            continue
        for case_id in card.case_ids:
            compatible_by_case.setdefault(case_id, []).append(card.id)

    items = []
    for case in case_results["items"]:
        source_mode = str(case.get("mode") or "").strip().lower()
        source_technique_mode = _TECHNIQUE_MODE.get(source_mode)
        if source_technique_mode is not None and source_technique_mode != technique_mode:
            continue
        sources = [
            {
                "id": source["upstream_id"],
                "link": source["url"],
                "provenance": source["provenance"],
            }
            for source in case.get("sources", [])
            if source.get("upstream_id") and source.get("url") and source.get("provenance")
        ]
        if not sources:
            continue
        items.append({
            "id": case["id"],
            "summary": str(case.get("summary", ""))[:500],
            "applicability": {
                "requested_mode": normalized_mode,
                "requested_duration_seconds": duration_seconds,
                "source_metadata": {
                    "mode": case.get("mode"),
                    "duration": case.get("duration"),
                },
                "use_cases": case.get("use_cases", []),
            },
            "sources": sources,
            "compatible_technique_ids": compatible_by_case.get(case["id"], []),
        })
        if len(items) >= limit:
            break

    return {"items": items, "unavailable": False}


__all__ = ["search_experience"]
