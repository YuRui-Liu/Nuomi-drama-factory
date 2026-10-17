import pytest

from novelvideo.creation.node_execution import _validate_change
from novelvideo.creation.operations import OperationError


_NODE = {
    "id": "director",
    "type": "directorWorkspaceNode",
    "data": {"directorRevisionId": "revision-1", "episodeNumber": 1},
}


def test_director_operation_inputs_accept_only_typed_operation_shapes():
    blocking = {
        "label": "仓库调度",
        "actors": [{"id": "hero", "label": "甲", "x": 25, "y": 40, "facing": 90}],
        "camera": {"x": 50, "y": 85, "facing": 270},
        "lights": [{"id": "key", "label": "顶灯", "x": 20, "y": 15, "intensity": 50}],
    }

    assert _validate_change(_NODE, ["directorOperationInputs"], {
        "operation": "save_blocking", "sceneId": "scene-1", "groupIds": ["group-1"],
        "baseVersion": 2, "blocking": blocking,
    }) == ("directorOperationInputs",)
    assert _validate_change(_NODE, ["directorOperationInputs"], {
        "operation": "compile_blocking", "baseRevisionId": "revision-1",
    }) == ("directorOperationInputs",)
    assert _validate_change(_NODE, ["directorOperationInputs"], {
        "operation": "edit_shot", "revisionId": "revision-1", "groupId": "group-1",
        "shotId": "shot-1", "action": "甲转身离开",
    }) == ("directorOperationInputs",)


@pytest.mark.parametrize("inputs", [
    {"operation": "compile_blocking", "baseRevisionId": "other-revision"},
    {"operation": "edit_shot", "revisionId": "other-revision", "groupId": "group-1",
     "shotId": "shot-1", "action": "甲转身离开"},
    {"operation": "edit_shot", "revisionId": "revision-1", "groupId": "group-1",
     "shotId": "shot-1", "action": "甲转身离开", "arbitrary": {"url": "https://example.invalid"}},
    {"operation": "save_blocking", "sceneId": "scene-1", "groupIds": ["group-1"],
     "baseVersion": 2, "blocking": {"label": "坏输入", "camera": {"x": 900, "y": 0, "facing": 0}}},
])
def test_director_operation_inputs_reject_scope_drift_and_untyped_fields(inputs):
    with pytest.raises(OperationError):
        _validate_change(_NODE, ["directorOperationInputs"], inputs)
