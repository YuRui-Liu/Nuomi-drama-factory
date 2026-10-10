import pytest

from novelvideo.freezone.canvas_store import read_canvas, CanvasRevisionConflict
from novelvideo.freezone.orchestration.models import OrchestrationError, input_fingerprint


def seed(tmp_path):
    from novelvideo.freezone.orchestration.projection import create_canvas, patch_graph
    create_canvas(tmp_path, "canvas", project_id="p", name="test", actor_id="u")
    patch_graph(tmp_path, "canvas", base_revision=1, request_id="seed", operations=[
        {"op": "add_node", "node": {"id": "gen", "type": "imageGenNode",
         "position": {"x": 0, "y": 0}, "data": {"prompt": "a cat", "displayName": "Image"}}},
    ])
    return read_canvas(tmp_path, "canvas")


def test_created_canvas_round_trips_through_web_schema(tmp_path):
    from novelvideo.api.schemas import CanvasPayload
    from novelvideo.freezone.orchestration.projection import create_canvas
    canvas = create_canvas(tmp_path, "canvas", project_id="p", name="我的一集", actor_id="u")
    parsed = CanvasPayload.model_validate({**canvas, "save_source": "autosave"})
    assert parsed.canvas_scope == "default"
    assert parsed.metadata["display_name"] == "我的一集"


def test_patch_is_idempotent_and_revision_protected(tmp_path):
    from novelvideo.freezone.orchestration.projection import patch_graph
    seed(tmp_path)
    args = dict(base_revision=2, request_id="rename", operations=[
        {"op": "update_node", "node_id": "gen", "data": {"displayName": "mine"}}])
    patch_graph(tmp_path, "canvas", **args)
    patch_graph(tmp_path, "canvas", **args)
    assert read_canvas(tmp_path, "canvas")["revision"] == 3
    with pytest.raises(CanvasRevisionConflict):
        patch_graph(tmp_path, "canvas", **{**args, "request_id": "different"})


def test_projection_preserves_human_edits_and_replay_does_not_duplicate(tmp_path):
    from novelvideo.freezone.orchestration.projection import patch_graph, project_result
    frozen = seed(tmp_path)["nodes"][0]
    patch_graph(tmp_path, "canvas", base_revision=2, request_id="move", operations=[
        {"op": "update_node", "node_id": "gen", "position": {"x": 999, "y": 5}, "data": {"displayName": "mine"}}])
    for _ in range(2):
        project_result(tmp_path, "canvas", node_id="gen", expected_input=input_fingerprint(frozen),
                       attempt_id="attempt-1", result={"imageUrl": "/output/image.png"})
    canvas = read_canvas(tmp_path, "canvas")
    assert len(canvas["nodes"]) == 1
    assert canvas["revision"] == 4
    assert canvas["nodes"][0]["position"]["x"] == 999
    assert canvas["nodes"][0]["data"]["displayName"] == "mine"
    assert canvas["nodes"][0]["data"]["imageUrl"] == "/output/image.png"


def test_projection_rejects_changed_input(tmp_path):
    from novelvideo.freezone.orchestration.projection import patch_graph, project_result
    frozen = seed(tmp_path)["nodes"][0]
    patch_graph(tmp_path, "canvas", base_revision=2, request_id="edit", operations=[
        {"op": "update_node", "node_id": "gen", "data": {"prompt": "a dog"}}])
    with pytest.raises(OrchestrationError, match="changed"):
        project_result(tmp_path, "canvas", node_id="gen", expected_input=input_fingerprint(frozen),
                       attempt_id="attempt-1", result={"imageUrl": "/output/image.png"})
    assert "imageUrl" not in read_canvas(tmp_path, "canvas")["nodes"][0]["data"]


def test_projection_cannot_write_arbitrary_input_fields(tmp_path):
    from novelvideo.freezone.orchestration.projection import project_result
    frozen = seed(tmp_path)["nodes"][0]
    with pytest.raises(OrchestrationError):
        project_result(tmp_path, "canvas", node_id="gen", expected_input=input_fingerprint(frozen),
                       attempt_id="attempt-1", result={"prompt": "overwritten"})


def test_invalid_graph_patch_is_atomic(tmp_path):
    from novelvideo.freezone.orchestration.projection import patch_graph
    seed(tmp_path)
    with pytest.raises(OrchestrationError):
        patch_graph(tmp_path, "canvas", base_revision=2, request_id="bad", operations=[
            {"op": "update_node", "node_id": "gen", "data": {"prompt": "changed"}},
            {"op": "add_edge", "edge": {"id": "bad", "source": "missing", "target": "gen"}},
        ])
    assert read_canvas(tmp_path, "canvas")["nodes"][0]["data"]["prompt"] == "a cat"


def test_compose_creates_one_stable_output_node(tmp_path):
    from novelvideo.freezone.orchestration.projection import create_canvas, patch_graph, project_result
    create_canvas(tmp_path, "canvas", project_id="p", name="test", actor_id="u")
    node = {"id": "compose", "type": "videoComposeNode", "data": {}, "position": {"x": 0, "y": 0}}
    patch_graph(tmp_path, "canvas", base_revision=1, request_id="seed", operations=[{"op": "add_node", "node": node}])
    for attempt in ("attempt-1", "attempt-1", "attempt-2"):
        project_result(tmp_path, "canvas", node_id="compose", expected_input=input_fingerprint(node),
                       attempt_id=attempt, result={"resultVideoUrl": "/output/final.mp4"})
    canvas = read_canvas(tmp_path, "canvas")
    assert len(canvas["nodes"]) == 2
    assert len(canvas["edges"]) == 1
    output = next(n for n in canvas["nodes"] if n["id"] != "compose")
    assert output["data"]["referenceOnly"] is True
    assert output["data"]["videoUrl"] == "/output/final.mp4"
