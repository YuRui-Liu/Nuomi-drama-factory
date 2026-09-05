from novelvideo.episode_graph.merge import merge_extractions
from novelvideo.episode_graph.models import (
    ConflictValues,
    EpisodeGraphExtraction,
    GraphEntity,
    GraphEvent,
    GraphRelation,
)


def test_merge_unions_sources_and_preserves_conflicting_attributes():
    merged = merge_extractions(
        [
            EpisodeGraphExtraction(
                group_key="e2-e6",
                entities=[
                    GraphEntity(
                        name="王总", kind="character", attributes={"description": "董事长"}, source_episodes={2}
                    )
                ],
            ),
            EpisodeGraphExtraction(
                group_key="e7-e11",
                entities=[
                    GraphEntity(
                        name="  王总  ",
                        kind="character",
                        attributes={"description": "总经理"},
                        source_episodes={7},
                    )
                ],
            ),
        ]
    )
    item = merged.entities[0]
    assert item.source_episodes == {2, 7}
    assert item.attributes["description"] == ConflictValues(values=["总经理", "董事长"])
    assert merged.conflict_count == 1


def test_merge_is_invariant_to_extraction_order():
    first = EpisodeGraphExtraction(
        group_key="e2-e6",
        entities=[
            GraphEntity(
                name="  王总 ",
                kind="character",
                attributes={"description": "董事长"},
                source_episodes={2},
            )
        ],
    )
    second = EpisodeGraphExtraction(
        group_key="e7-e11",
        entities=[
            GraphEntity(
                name="王总",
                kind="character",
                attributes={"description": "总经理"},
                source_episodes={7},
            )
        ],
    )
    forward = merge_extractions([first, second])
    reverse = merge_extractions([second, first])
    assert forward == reverse
    assert forward.entities[0].name == "王总"


def test_native_list_attribute_is_not_confused_with_conflict_values():
    same = merge_extractions(
        [
            EpisodeGraphExtraction(
                group_key="e2-e6",
                entities=[GraphEntity(name="A", kind="prop", attributes={"tags": ["x", "y"]}, source_episodes={2})],
            ),
            EpisodeGraphExtraction(
                group_key="e7-e11",
                entities=[GraphEntity(name="A", kind="prop", attributes={"tags": ["x", "y"]}, source_episodes={7})],
            ),
        ]
    )
    assert same.entities[0].attributes["tags"] == ["x", "y"]
    assert same.conflict_count == 0

    different = merge_extractions(
        [
            EpisodeGraphExtraction(
                group_key="e2-e6",
                entities=[GraphEntity(name="A", kind="prop", attributes={"tags": ["z"]}, source_episodes={2})],
            ),
            EpisodeGraphExtraction(
                group_key="e7-e11",
                entities=[GraphEntity(name="A", kind="prop", attributes={"tags": ["x", "y"]}, source_episodes={7})],
            ),
        ]
    )
    assert different.entities[0].attributes["tags"] == ConflictValues(values=[["x", "y"], ["z"]])
    assert different.conflict_count == 1


def test_events_and_relations_use_stable_keys_and_union_sources():
    extraction = EpisodeGraphExtraction(
        group_key="e2-e6",
        events=[
            GraphEvent(episode=2, ordinal=1, description="A", source_episodes={2}),
            GraphEvent(episode=2, ordinal=1, description="A", source_episodes={2}),
        ],
        relations=[
            GraphRelation(
                source_key="character:a",
                relation_type="meets",
                target_key="character:b",
                episode=2,
                source_episodes={2},
            ),
            GraphRelation(
                source_key="character:a",
                relation_type="meets",
                target_key="character:b",
                episode=2,
                source_episodes={3},
            ),
        ],
    )
    merged = merge_extractions([extraction])
    assert len(merged.events) == 1
    assert len(merged.relations) == 1
    assert merged.relations[0].source_episodes == {2, 3}
