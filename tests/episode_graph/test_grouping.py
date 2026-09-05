from novelvideo.episode_graph.grouping import group_episode_sources
from novelvideo.episode_graph.models import EpisodeGraphSource


def source(number: int, *, revision: int = 1, content: str | None = None) -> EpisodeGraphSource:
    return EpisodeGraphSource(
        number=number,
        title=f"E{number}",
        content=content or f"正文{number}",
        source_revision=revision,
    )


def test_groups_e2_to_e30_into_six_groups_of_at_most_five():
    groups = group_episode_sources([source(number) for number in range(2, 31)])
    assert [[item.number for item in group.episodes] for group in groups] == [
        [2, 3, 4, 5, 6],
        [7, 8, 9, 10, 11],
        [12, 13, 14, 15, 16],
        [17, 18, 19, 20, 21],
        [22, 23, 24, 25, 26],
        [27, 28, 29, 30],
    ]


def test_gap_starts_a_new_group():
    groups = group_episode_sources([source(2), source(3), source(8), source(9)])
    assert [[item.number for item in group.episodes] for group in groups] == [[2, 3], [8, 9]]


def test_hash_is_stable_across_input_order_and_sensitive_to_source_fields():
    first = group_episode_sources([source(3), source(2)])[0]
    reordered = group_episode_sources([source(2), source(3)])[0]
    changed = group_episode_sources([source(2), source(3, revision=2)])[0]
    changed_content = group_episode_sources([source(2), source(3, content="changed")])[0]
    assert first.content_hash == reordered.content_hash
    assert first.content_hash != changed.content_hash
    assert first.content_hash != changed_content.content_hash


def test_duplicate_episode_and_non_fixed_group_size_are_rejected():
    import pytest

    with pytest.raises(ValueError, match="duplicate episode number"):
        group_episode_sources([source(2), source(2)])
    with pytest.raises(ValueError, match="fixed at 5"):
        group_episode_sources([source(2)], max_size=4)
