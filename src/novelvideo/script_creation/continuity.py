"""Small, source-linked continuity retrieval helpers."""

from .models import ContinuityFact


def build_episode_context(facts, *, episode: int):
    """Return authoritative facts known by an episode, with source versions intact."""
    if not 1 <= episode <= 100:
        raise ValueError("episode must be between 1 and 100")
    return tuple(
        fact for fact in facts
        if fact.adoption_status == "adopted"
        and fact.state in {"confirmed", "planned", "occurred", "retracted"}
        and (fact.episode_start is not None and fact.episode_start <= episode)
        and (fact.state == "retracted" or
             (fact.episode_end is not None and episode <= fact.episode_end))
        and fact.source_revision_id
        and fact.source_locator
    )
