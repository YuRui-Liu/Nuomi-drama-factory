"""Source-bound voice facts, independent of visual design and voice assignment."""

from typing import Literal

from pydantic import BaseModel, Field


class VoiceFacts(BaseModel):
    species: str = ""
    age_group: str = ""
    vocalization_mode: Literal["dialogue", "nonverbal", "both", "none", "unknown"] = "unknown"
    voice_traits: str = ""
    evidence: list[str] = Field(default_factory=list)
    provenance: Literal["source", "human", "unknown"] = "unknown"
    conflicts: list[str] = Field(default_factory=list)


def merge_voice_facts(existing: VoiceFacts, incoming: VoiceFacts) -> VoiceFacts:
    """Missing fragments never erase facts; human confirmation wins over extraction."""
    if existing.provenance == "human" and incoming.provenance != "human":
        return existing.model_copy(deep=True)
    result = existing.model_copy(deep=True)
    result.evidence = list(dict.fromkeys([*existing.evidence, *incoming.evidence]))
    result.conflicts = list(dict.fromkeys([*existing.conflicts, *incoming.conflicts]))
    for field in ("species", "age_group", "voice_traits", "vocalization_mode"):
        unknown = "unknown" if field == "vocalization_mode" else ""
        old, new = getattr(existing, field), getattr(incoming, field)
        if incoming.provenance == "human" and new != unknown:
            setattr(result, field, new)
            result.conflicts = [conflict for conflict in result.conflicts if not conflict.startswith(f"{field}:")]
        elif any(conflict.startswith(f"{field}:") for conflict in result.conflicts):
            setattr(result, field, unknown)
        elif new == unknown:
            continue
        elif old == unknown or old == new:
            setattr(result, field, new)
        else:
            result.conflicts.append(f"{field}: {old} <> {new}")
            setattr(result, field, unknown)
    if incoming.provenance == "human":
        result.provenance = "human"
    elif result.evidence:
        result.provenance = "source"
    return result


def merge_voice_facts_json(existing: str, incoming: str) -> str:
    """SQLite UDF: merge against the row at write time, not a stale snapshot."""
    return merge_voice_facts(
        VoiceFacts.model_validate_json(existing or "{}"),
        VoiceFacts.model_validate_json(incoming or "{}"),
    ).model_dump_json()


def merge_voice_age(existing_facts: str, incoming_facts: str, old_age: str, new_age: str) -> str:
    facts = VoiceFacts.model_validate_json(merge_voice_facts_json(existing_facts, incoming_facts))
    if any(conflict.startswith("age_group:") for conflict in facts.conflicts):
        return ""
    return facts.age_group or new_age or old_age or ""
