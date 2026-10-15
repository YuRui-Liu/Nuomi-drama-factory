from pathlib import Path

import pytest
from PIL import Image

from novelvideo.freezone.orchestration.models import OrchestrationError


@pytest.mark.asyncio
async def test_cancelled_planner_releases_canvas_instead_of_running_forever(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.freezone.orchestration.adapters import NodeAdapter
    from novelvideo.api.routes import freezone
    import novelvideo.task_state as task_state
    async def resolve(*args, **kwargs):
        return (SimpleNamespace(),)
    monkeypatch.setattr(freezone, '_resolve_freezone_project', resolve)
    monkeypatch.setattr(task_state, 'get_task_manager', lambda: SimpleNamespace(
        get_task_for_project=lambda *args, **kwargs: SimpleNamespace(status='cancelled', error=None)))
    run = {'project_id': 'p', 'actor': {}, 'snapshot': {'nodes': [{'id': 'script', 'type': 'scriptNode'}]}}
    attempt = {'node_id': 'script', 'submission': {'plan': True, 'job_id': 'job'}}
    result = await NodeAdapter(tmp_path).reconcile(run, attempt)
    assert result['status'] == 'cancelled'


def test_image_node_parameters_override_stale_request_and_include_style_camera():
    from novelvideo.freezone.orchestration.adapters.requests import build_request
    node = {"id": "n", "type": "imageGenNode", "data": {
        "prompt": "portrait", "aspectRatio": "3:2", "requestAspectRatio": "16:9",
        "size": "2K", "quality": "high", "model": "gpt-image-2",
        "styleTemplateId": "cinematic", "cameraSelection": {"cameraBodyId": "Alexa", "lensId": "Prime", "focalLengthMm": 50, "aperture": "f/2.8"},
        "request": {"provider": "openai", "aspect_ratio": "1:1", "quality": "low"}}}
    _, body = build_request(node, "generate", [], canvas_id="c", attempt_id="a")
    assert body["aspect_ratio"] == "16:9"
    assert body["quality"] == "high"
    assert body["style"] == {"template_id": "cinematic"}
    assert body["camera"]["focal_length_mm"] == 50
    assert body["image_size"] == "2K"


def test_extension_style_is_composed_from_catalog():
    from novelvideo.freezone.orchestration.adapters.requests import build_request
    node = {"id": "n", "type": "imageGenNode", "data": {"prompt": "portrait", "extensionStyleId": "drama_ext.cinematic_photorealism"}}
    _, body = build_request(node, "generate", [], canvas_id="c", attempt_id="a")
    assert "[Extension style:" in body["prompt"]


def test_unknown_extension_style_fails_before_submission():
    from novelvideo.freezone.orchestration.adapters.requests import build_request
    with pytest.raises(OrchestrationError, match="style"):
        build_request({"id": "n", "type": "imageGenNode", "data": {"extensionStyleId": "missing-style"}}, "generate", [], canvas_id="c", attempt_id="a")


def test_unimplemented_image_batch_does_not_silently_generate_one():
    from novelvideo.freezone.orchestration.adapters.requests import build_request
    with pytest.raises(OrchestrationError, match="count"):
        build_request({"id": "n", "type": "imageGenNode", "data": {"count": 3}}, "generate", [], canvas_id="c", attempt_id="a")


@pytest.mark.asyncio
async def test_only_proven_local_capacity_rejection_can_resume_unknown(tmp_path):
    from novelvideo.freezone.orchestration.adapters import NodeAdapter
    adapter = NodeAdapter(tmp_path)
    run = {"project_id": "p"}
    for message, expected in [("project p default lane is full (10/10)", "not_submitted"),
                              ("lost response", "submission_unknown"),
                              ("project other default lane is full (10/10)", "submission_unknown")]:
        result = await adapter.reconcile_submission(run, {"submission": None, "error": {"message": message}})
        assert result["status"] == expected


def test_image_request_consumes_upstream_references_and_text():
    from novelvideo.freezone.orchestration.adapters.requests import build_request
    node = {"id": "image", "type": "imageGenNode", "data": {"prompt": "paint", "model": "openai/gpt-image-2"}}
    name, body = build_request(node, "generate", [
        {"node_id": "ref", "result": {"imageUrl": "/static/projects/p/ref.png"}},
        {"node_id": "source", "result": {"text": "a blue room"}},
    ], canvas_id="canvas", attempt_id="a")
    assert name == "freezone_gen"
    assert body["reference_urls"] == ["/static/projects/p/ref.png"]
    assert "a blue room" in body["prompt"]
    assert body["provider"] == "openai" and body["model"] == "gpt-image-2"


def test_compose_requires_explicit_order_not_upstream_completion_order():
    from novelvideo.freezone.orchestration.adapters.requests import build_request
    node = {"id": "compose", "type": "videoComposeNode", "data": {"timeline": [
        {"node_id": "first", "duration": 4}, {"node_id": "second", "duration": 6}]}}
    upstream = [{"node_id": "second", "result": {"videoUrl": "/second.mp4"}},
                {"node_id": "first", "result": {"videoUrl": "/first.mp4"}}]
    name, body = build_request(node, "compose", upstream, canvas_id="canvas", attempt_id="a")
    assert name == "freezone_video_compose"
    assert [i["source_url"] for i in body["tracks"][0]["items"]] == ["/first.mp4", "/second.mp4"]
    assert [i["timeline_start"] for i in body["tracks"][0]["items"]] == [0, 4]
    node["data"] = {}
    with pytest.raises(OrchestrationError, match="timeline"):
        build_request(node, "compose", upstream, canvas_id="canvas", attempt_id="a")


def test_reference_input_order_obeys_saved_order():
    from novelvideo.freezone.orchestration.adapters.requests import build_request
    node = {"id": "n", "type": "imageNode", "data": {"prompt": "edit", "referenceOrder": ["b", "a"]}}
    name, body = build_request(node, "edit", [
        {"node_id": "a", "result": {"imageUrl": "/a.png"}},
        {"node_id": "b", "result": {"imageUrl": "/b.png"}}], canvas_id="canvas", attempt_id="x")
    assert body["base_url"] == "/b.png"
    assert body["extra_reference_urls"] == ["/a.png"]


def test_video_request_uses_actual_schema_duration_and_references():
    from novelvideo.freezone.orchestration.adapters.requests import build_request
    node = {"id": "video", "type": "videoNode", "data": {"prompt": "go", "durationSec": 12}}
    _, body = build_request(node, "reference", [{"node_id": "image", "result": {"imageUrl": "/ref.png"}}], canvas_id="c", attempt_id="a")
    assert body["duration_seconds"] == 12
    assert body["references"] == [{"type": "image", "url": "/ref.png"}]


def test_director_draft_converts_browser_shape_without_losing_frames():
    from novelvideo.freezone.orchestration.adapters.requests import director_draft
    converted = director_draft({"schemaVersion": 1, "revision": 2, "modelId": "minimax-h3", "aspectRatio": "16:9", "resolution": "720p",
                               "references": [], "segments": [{"id": "s", "prompt": "act", "durationSeconds": 8,
                               "firstFrame": {"imageId": "im", "url": "/im.png"}, "lastFrame": None}]})
    assert converted["segments"][0]["duration_seconds"] == 8
    assert converted["segments"][0]["first_frame"]["image_id"] == "im"


def test_world_request_uses_public_source_url_field():
    from novelvideo.freezone.orchestration.adapters.requests import build_request
    from novelvideo.api.schemas import FreezoneImageTo3GSRequest
    _, body = build_request({"id": "world", "type": "threeDWorldNode", "data": {}}, "generate",
                           [{"node_id": "ref", "result": {"imageUrl": "/ref.png"}}], canvas_id="c", attempt_id="a")
    assert FreezoneImageTo3GSRequest.model_validate(body).source_url == "/ref.png"


def test_media_verification_decodes_output_and_hashes(tmp_path):
    from novelvideo.freezone.orchestration.verification import verify_media
    Image.new("RGB", (32, 24), "blue").save(tmp_path / "image.png")
    result = verify_media(tmp_path, "p", "/static/projects/p/image.png", "image")
    assert result["width"] == 32 and result["height"] == 24
    assert len(result["sha256"]) == 64
    (tmp_path / "bad.png").write_text("fake image")
    with pytest.raises(OrchestrationError, match="decode"):
        verify_media(tmp_path, "p", "/static/projects/p/bad.png", "image")


@pytest.mark.parametrize("url", ["/static/projects/other/a.png", "https://outside/a.png", "../a.png", "/static/projects/p/%2e%2e/a.png"])
def test_media_reference_cannot_escape_project(tmp_path, url):
    from novelvideo.freezone.orchestration.verification import local_media_path
    with pytest.raises(OrchestrationError):
        local_media_path(tmp_path, "p", url)


@pytest.mark.asyncio
async def test_text_input_local_adapter_is_consumable_without_fake_task(tmp_path):
    from novelvideo.freezone.orchestration.adapters import NodeAdapter
    from novelvideo.freezone.orchestration.store import RunStore
    from novelvideo.freezone.orchestration.scheduler import Scheduler
    from novelvideo.freezone.orchestration.projection import create_canvas, patch_graph
    create_canvas(tmp_path, "canvas", project_id="p", name="test", actor_id="u")
    canvas = patch_graph(tmp_path, "canvas", base_revision=1, request_id="seed", operations=[
        {"op": "add_node", "node": {"id": "a", "type": "textAnnotationNode", "data": {"text": "完整原文"}}}])
    store = RunStore(tmp_path)
    run = store.create(canvas, target="a", request_id="r", project_id="p", settings={"concurrency": 1}, actor={"id": "u"})
    result = await Scheduler(store, NodeAdapter(tmp_path)).drive(run["id"], poll_seconds=0.001)
    attempt = result["attempts"]["a"][-1]
    assert result["status"] == "succeeded"
    assert attempt["result"]["text"] == "完整原文"
    assert "task_id" not in attempt["submission"]
    assert attempt["artifacts"][0]["kind"] == "text"
