"""Trusted species context shared by identity generation and quality checks."""


def confirmed_nonhuman_species(character) -> str:
    facts = getattr(character, "voice_facts", None)
    species = str(getattr(facts, "species", "") or "").strip()
    if getattr(facts, "provenance", "unknown") not in {"source", "human"}:
        return ""
    if species.lower() in {"", "unknown", "未知", "人", "人类", "human", "homo sapiens"}:
        return ""
    return species
