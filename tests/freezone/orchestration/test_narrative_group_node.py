from __future__ import annotations

import pytest

from novelvideo.freezone.orchestration.models import OrchestrationError


def _bound_group_node(**overrides):
    data = {
        "operation": "validate",
        "episodeNumber": 2,
        "groupId": "group-2",
        "directorRevisionId": "revision-2",
    }
    data.update(overrides)
    return {"id": "group", "type": "narrativeGroupStoryboardNode", "data": data}


def test_bound_group_storyboard_is_a_registered_json_reference_node():
    from novelvideo.freezone.orchestration.adapters import available_operations
    from novelvideo.freezone.orchestration.capabilities import capabilities
    from novelvideo.freezone.orchestration.graph import execution_order, validate_graph
    from novelvideo.freezone.orchestration.models import frozen_node_result, input_fingerprint

    operations = available_operations()
    assert ("narrativeGroupStoryboardNode", "validate") in operations
    assert ("narrativeGroupStoryboardNode", "render") in operations
    assert ("narrativeGroupStoryboardNode", "retry_render") in operations
    entry = next(item for item in capabilities(operations)
                 if item["node_type"] == "narrativeGroupStoryboardNode")
    assert [port["name"] for port in entry["inputs"]] == ["json"]
    assert [port["name"] for port in entry["outputs"]] == ["json"]
    assert [(item["name"], item["available"]) for item in entry["operations"]] == [
        ("validate", True), ("render", True), ("retry_render", True),
    ]

    video = next(item for item in capabilities(operations)
                 if item["node_type"] == "narrativeGroupVideoNode")
    compose = next(item for item in capabilities(operations)
                   if item["node_type"] == "episodeComposeNode")
    assert [port["name"] for port in video["inputs"]] == ["json"]
    assert [port["name"] for port in video["outputs"]] == ["json"]
    assert [(item["name"], item["available"]) for item in video["operations"]] == [
        ("video", True), ("retry_video", True),
    ]
    assert [port["name"] for port in compose["inputs"]] == ["json"]
    assert [port["name"] for port in compose["outputs"]] == ["json", "video"]
    assert [(item["name"], item["available"]) for item in compose["operations"]] == [
        ("compose", True),
    ]

    nodes = [
        _bound_group_node(),
        {"id": "video", "type": "narrativeGroupVideoNode", "data": {
            "operation": "video", "episodeNumber": 2, "groupId": "group-2",
            "directorRevisionId": "revision-2",
        }},
        {"id": "compose", "type": "episodeComposeNode", "data": {
            "operation": "compose", "episodeNumber": 2,
            "directorRevisionId": "revision-2", "productionGroupIds": ["group-2"],
        }},
    ]
    edges = [{"id": "group-video", "source": "group", "target": "video",
              "data": {"sourcePort": "json", "targetPort": "json"}},
             {"id": "video-compose", "source": "video", "target": "compose",
              "data": {"sourcePort": "json", "targetPort": "json"}}]
    assert validate_graph(nodes, edges)["valid"] is True
    assert execution_order(nodes, edges, "compose") == ["group", "video", "compose"]
    assert frozen_node_result(nodes[0]) == {"narrativeGroupReference": {
        "episodeNumber": 2, "groupId": "group-2", "directorRevisionId": "revision-2",
    }}
    assert input_fingerprint(nodes[0]) != input_fingerprint(
        _bound_group_node(groupId="different-group")
    )


def test_episode_compose_requires_the_complete_explicit_group_set():
    from novelvideo.freezone.orchestration.graph import validate_graph
    from novelvideo.freezone.orchestration.adapters import _validate_episode_group_ids
    from novelvideo.freezone.orchestration.models import OrchestrationError

    valid = {"id": "compose", "type": "episodeComposeNode", "data": {
        "operation": "compose", "episodeNumber": 2,
        "directorRevisionId": "revision-2", "productionGroupIds": ["g1", "g2"],
    }}
    assert _validate_episode_group_ids(valid["data"]["productionGroupIds"]) == ["g1", "g2"]
    invalid_groups = [
        [], ["g1", "g1"], ["../g1"], ["g" * 129],
    ]
    assert validate_graph([valid], [])["valid"] is True
    for group_ids in invalid_groups:
        with pytest.raises(OrchestrationError):
            _validate_episode_group_ids(group_ids)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["episodeNumber", "groupId", "directorRevisionId"])
async def test_group_reference_validation_requires_real_episode_group_and_revision_bindings(
    tmp_path, missing,
):
    from novelvideo.freezone.orchestration.adapters import NodeAdapter

    node = _bound_group_node()
    node["data"].pop(missing)
    run = {"project_id": "project-1", "actor": {"id": "user-1"},
           "snapshot": {"nodes": [node], "edges": []}}

    with pytest.raises(OrchestrationError) as invalid:
        await NodeAdapter(tmp_path).submit(run, {"node_id": "group", "id": "attempt-1"}, [])
    assert invalid.value.code == "invalid_input"


@pytest.mark.asyncio
async def test_group_reference_validate_returns_only_its_structural_binding(tmp_path):
    from novelvideo.freezone.orchestration.adapters import NodeAdapter

    node = _bound_group_node(displayName="E02 · group-2", unrelated="ignored")
    run = {"project_id": "project-1", "actor": {"id": "user-1"},
           "snapshot": {"nodes": [node], "edges": []}}

    result = await NodeAdapter(tmp_path).submit(run, {"node_id": "group", "id": "attempt-1"}, [])

    assert result == {"local": True, "result": {"narrativeGroupReference": {
        "episodeNumber": 2, "groupId": "group-2", "directorRevisionId": "revision-2",
    }}}
