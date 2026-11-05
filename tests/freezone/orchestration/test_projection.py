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


def test_remove_edge_preserves_nodes_and_is_idempotent(tmp_path):
    from novelvideo.freezone.orchestration.projection import patch_graph
    seed(tmp_path)
    patch_graph(tmp_path, "canvas", base_revision=2, request_id="connect", operations=[
        {"op": "add_node", "node": {"id": "text", "type": "textAnnotationNode", "data": {"content": "hi"}}},
        {"op": "add_edge", "edge": {"id": "link", "source": "text", "target": "gen", "data": {"sourcePort": "text", "targetPort": "text"}}},
    ])
    args = dict(base_revision=3, request_id="disconnect", operations=[{"op": "remove_edge", "edge_id": "link"}])
    patch_graph(tmp_path, "canvas", **args)
    patch_graph(tmp_path, "canvas", **args)
    saved = read_canvas(tmp_path, "canvas")
    assert saved["edges"] == []
    assert len(saved["nodes"]) == 2
    assert saved["revision"] == 4


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


def test_stale_completion_marks_node_without_replacing_current_media(tmp_path):
    from novelvideo.freezone.orchestration.projection import mark_stale_result, project_result

    frozen = seed(tmp_path)["nodes"][0]
    project_result(
        tmp_path,
        "canvas",
        node_id="gen",
        expected_input=input_fingerprint(frozen),
        attempt_id="attempt-current",
        result={"imageUrl": "/output/current.png"},
    )
    from novelvideo.freezone.orchestration.projection import patch_graph

    patch_graph(tmp_path, "canvas", base_revision=3, request_id="change-input", operations=[
        {"op": "update_node", "node_id": "gen", "data": {"prompt": "a dog"}},
    ])

    outcome = mark_stale_result(
        tmp_path,
        "canvas",
        node_id="gen",
        expected_input=input_fingerprint(frozen),
        attempt_id="attempt-old-input",
    )

    current = read_canvas(tmp_path, "canvas")["nodes"][0]
    assert outcome == "stale"
    assert current["data"]["prompt"] == "a dog"
    assert current["data"]["imageUrl"] == "/output/current.png"
    assert current["data"]["orchestration"]["status"] == "stale"


def test_stale_completion_does_not_mark_a_newer_current_projection_stale(tmp_path):
    from novelvideo.freezone.orchestration.projection import mark_stale_result, patch_graph, project_result

    original = seed(tmp_path)["nodes"][0]
    project_result(
        tmp_path,
        "canvas",
        node_id="gen",
        expected_input=input_fingerprint(original),
        attempt_id="attempt-old-input",
        result={"imageUrl": "/output/old.png"},
    )
    patch_graph(tmp_path, "canvas", base_revision=3, request_id="change-input", operations=[
        {"op": "update_node", "node_id": "gen", "data": {"prompt": "a dog"}},
    ])
    current_input = read_canvas(tmp_path, "canvas")["nodes"][0]
    project_result(
        tmp_path,
        "canvas",
        node_id="gen",
        expected_input=input_fingerprint(current_input),
        attempt_id="attempt-current-input",
        result={"imageUrl": "/output/new.png"},
    )

    outcome = mark_stale_result(
        tmp_path,
        "canvas",
        node_id="gen",
        expected_input=input_fingerprint(original),
        attempt_id="attempt-old-input",
    )

    node = read_canvas(tmp_path, "canvas")["nodes"][0]
    assert outcome == "superseded"
    assert node["data"]["imageUrl"] == "/output/new.png"
    assert node["data"]["orchestration"]["status"] == "succeeded"


def test_image_generator_fingerprint_ignores_preview_ratio_but_tracks_request_ratio():
    from copy import deepcopy

    frozen = {
        "id": "image",
        "type": "imageGenNode",
        "data": {
            "prompt": "wide character portrait",
            "aspectRatio": "16:9",
            "requestAspectRatio": "16:9",
            "size": "1K",
        },
    }
    preview_hydrated = deepcopy(frozen)
    preview_hydrated["data"].update({
        "aspectRatio": "2:3",
        "imageNaturalWidth": 1024,
        "imageNaturalHeight": 1536,
        "imageAspectRatioUpdatedAt": 1792309675,
    })

    assert input_fingerprint(preview_hydrated) == input_fingerprint(frozen)

    changed_request = deepcopy(preview_hydrated)
    changed_request["data"]["requestAspectRatio"] = "2:3"
    assert input_fingerprint(changed_request) != input_fingerprint(frozen)


def test_image_generator_auto_ratio_fallback_remains_an_input():
    from copy import deepcopy

    frozen = {
        "id": "image",
        "type": "imageGenNode",
        "data": {
            "prompt": "portrait",
            "aspectRatio": "16:9",
            "requestAspectRatio": "auto",
        },
    }
    changed_effective_ratio = deepcopy(frozen)
    changed_effective_ratio["data"]["aspectRatio"] = "2:3"

    assert input_fingerprint(changed_effective_ratio) != input_fingerprint(frozen)


def test_image_generator_fingerprint_ignores_empty_editor_defaults():
    from copy import deepcopy

    frozen = {
        "id": "image",
        "type": "imageGenNode",
        "data": {
            "prompt": "A rainy station at night",
            "model": "openai/gpt-image-2",
            "size": "2K",
            "quality": "high",
            "requestAspectRatio": "16:9",
            "extensionStyleId": "drama_ext.cinematic_photorealism",
        },
    }
    editor_saved = deepcopy(frozen)
    editor_saved["data"].update({
        "cameraSelection": None,
        "focusRegion": None,
        "referenceImageUrl": None,
        "styleTemplateId": None,
        "marks": [],
    })

    assert input_fingerprint(editor_saved) == input_fingerprint(frozen)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prompt", "A sunny station at dawn"),
        ("model", "openai/gpt-image-1"),
        ("referenceImageUrl", "/static/project/reference.png"),
        ("styleTemplateId", "noir-35mm"),
    ],
)
def test_image_generator_fingerprint_tracks_meaningful_request_changes(field, value):
    from copy import deepcopy

    frozen = {
        "id": "image",
        "type": "imageGenNode",
        "data": {
            "prompt": "A rainy station at night",
            "model": "openai/gpt-image-2",
            "size": "2K",
            "quality": "high",
            "requestAspectRatio": "16:9",
            "referenceImageUrl": None,
            "styleTemplateId": None,
        },
    }
    changed = deepcopy(frozen)
    changed["data"][field] = value

    assert input_fingerprint(changed) != input_fingerprint(frozen)


def test_projection_cannot_write_arbitrary_input_fields(tmp_path):
    from novelvideo.freezone.orchestration.projection import project_result
    frozen = seed(tmp_path)["nodes"][0]
    with pytest.raises(OrchestrationError):
        project_result(tmp_path, "canvas", node_id="gen", expected_input=input_fingerprint(frozen),
                       attempt_id="attempt-1", result={"prompt": "overwritten"})


def test_group_video_dialogue_source_is_frozen_input_and_survives_result_projection(tmp_path):
    from novelvideo.freezone.orchestration.projection import (
        create_canvas, patch_graph, project_result,
    )
    create_canvas(tmp_path, "canvas", project_id="p", name="test", actor_id="u")
    node = {
        "id": "video", "type": "narrativeGroupVideoNode",
        "position": {"x": 0, "y": 0},
        "data": {
            "operation": "video", "episodeNumber": 1,
            "groupId": "group-1", "directorRevisionId": "revision-1",
            "dialogueSource": "h3_native",
        },
    }
    patch_graph(tmp_path, "canvas", base_revision=1, request_id="seed-video", operations=[
        {"op": "add_node", "node": node},
    ])
    frozen = read_canvas(tmp_path, "canvas")["nodes"][0]
    external = {**frozen, "data": {**frozen["data"], "dialogueSource": "external_tts"}}
    assert input_fingerprint(frozen) != input_fingerprint(external)

    project_result(
        tmp_path, "canvas", node_id="video", expected_input=input_fingerprint(frozen),
        attempt_id="attempt-1",
        result={"videoUrl": "/output/group.mp4", "narrativeGroupResult": {
            "stage": "video", "dialogueSource": "h3_native",
        }},
    )
    projected = read_canvas(tmp_path, "canvas")["nodes"][0]
    assert projected["data"]["dialogueSource"] == "h3_native"


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
