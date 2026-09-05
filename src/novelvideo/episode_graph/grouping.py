import hashlib
import json
from collections.abc import Sequence

from .models import EpisodeGraphGroup, EpisodeGraphSource


def _content_hash(episodes: Sequence[EpisodeGraphSource]) -> str:
    payload = [
        {
            "number": item.number,
            "source_revision": item.source_revision,
            "title": item.title,
            "content_sha256": hashlib.sha256(item.content.encode("utf-8")).hexdigest(),
        }
        for item in episodes
    ]
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def group_episode_sources(
    sources: Sequence[EpisodeGraphSource], max_size: int = 5
) -> list[EpisodeGraphGroup]:
    if max_size != 5:
        raise ValueError("episode graph max group size is fixed at 5")
    ordered = sorted(sources, key=lambda item: item.number)
    if len({item.number for item in ordered}) != len(ordered):
        raise ValueError("duplicate episode number")

    batches: list[list[EpisodeGraphSource]] = []
    for item in ordered:
        if not batches or len(batches[-1]) == max_size or item.number != batches[-1][-1].number + 1:
            batches.append([])
        batches[-1].append(item)
    return [
        EpisodeGraphGroup(
            first_episode=batch[0].number,
            last_episode=batch[-1].number,
            episodes=tuple(batch),
            content_hash=_content_hash(batch),
        )
        for batch in batches
    ]

