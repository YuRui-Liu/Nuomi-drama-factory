import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from novelvideo.api.routes import projects
from novelvideo.api.routes.projects import _media_defaults_payload
from novelvideo.api.schemas import MediaDefaultsRequest, ProjectUpdate, SingleVideoRequest
from novelvideo.media_capabilities.video.workflow_registry import (
    VideoWorkflowDefinition,
    VideoWorkflowRegistry,
    VideoWorkflowScene,
)
from novelvideo.media_capabilities.video.parameters import (
    VideoWorkflowParameterDefinition,
    VideoWorkflowParameterOption,
)


def _enum_parameter(
    key: str,
    default: str,
    *values: str,
) -> VideoWorkflowParameterDefinition:
    return VideoWorkflowParameterDefinition(
        key=key,
        label=key,
        default=default,
        options=tuple(
            VideoWorkflowParameterOption(value=value, label=value)
            for value in values
        ),
    )


def _workflow(
    model: str,
    *,
    supported_modes: tuple[str, ...] = ("i2va", "fl2va"),
    default_mode: str = "auto",
    parameters: tuple[VideoWorkflowParameterDefinition, ...] | None = None,
) -> VideoWorkflowDefinition:
    if parameters is None:
        parameters = (
            (_enum_parameter("resolution", "720p", "720p", "1080p"),)
            if model == "runninghub:minimax-h3"
            else ()
        )
    return VideoWorkflowDefinition(
        id=model,
        label=model,
        provider=model.partition(":")[0],
        adapter_key=model.partition(":")[2],
        scenes=frozenset({VideoWorkflowScene.NARRATIVE_GROUP}),
        supported_modes=supported_modes,
        default_mode=default_mode,
        is_default=model == "runninghub:minimax-h3",
        parameters=parameters,
    )


def _registry(*models: str) -> VideoWorkflowRegistry:
    return VideoWorkflowRegistry(tuple(_workflow(model) for model in models))


def _make_client(monkeypatch, tmp_path, *, registry=None):
    ctx = SimpleNamespace(
        project_id="demo",
        owner_username="tester",
        project_name="demo",
        state_dir=tmp_path,
        is_home_node=True,
    )

    async def resolve(*args, **kwargs):
        return ctx

    monkeypatch.setattr(projects, "resolve_project_context", resolve)
    monkeypatch.setattr(
        projects,
        "build_video_workflow_registry",
        lambda store, resolver: registry or _registry("runninghub:minimax-h3"),
    )
    app = FastAPI()
    app.include_router(projects.router, prefix="/api/v1")
    app.dependency_overrides[projects.get_api_user] = lambda: {
        "id": "user-1",
        "username": "tester",
    }
    app.dependency_overrides[projects.get_media_capability_store] = lambda: object()
    app.dependency_overrides[projects.get_media_credential_resolver] = lambda: object()
    return TestClient(app)


def test_media_defaults_use_real_stage_specific_image_models():
    assert _media_defaults_payload({}) == {
        "video_model": "runninghub:minimax-h3",
        "h3_mode": "auto",
        "narrative_sketch_provider": "grsai-main",
        "narrative_sketch_model": "nano-banana-2",
        "narrative_render_provider": "grsai-main",
        "narrative_render_model": "gpt-image-2",
        "narrative_render_image_size": "1K",
        "video_workflow_parameters": {
            "runninghub:minimax-h3": {"resolution": "720p"},
        },
    }


def test_media_defaults_request_preserves_independent_image_bindings():
    request = MediaDefaultsRequest(
        video_model="runninghub:minimax-h3",
        narrative_sketch_provider="grsai-backup",
        narrative_sketch_model="nano-banana-2-4k-cl",
        narrative_render_provider="grsai-main",
        narrative_render_model="gpt-image-2-vip",
    )

    assert request.narrative_sketch_model == "nano-banana-2-4k-cl"
    assert request.narrative_render_model == "gpt-image-2-vip"


def test_media_defaults_request_tracks_optional_workflow_parameters():
    omitted = MediaDefaultsRequest(video_model="runninghub:minimax-h3")
    provided = MediaDefaultsRequest(
        video_model="runninghub:minimax-h3",
        video_workflow_parameters={
            "runninghub:minimax-h3": {"resolution": "1080p"}
        },
    )

    assert omitted.video_workflow_parameters is None
    assert "video_workflow_parameters" not in omitted.model_fields_set
    assert provided.video_workflow_parameters == {
        "runninghub:minimax-h3": {"resolution": "1080p"}
    }
    assert "video_workflow_parameters" in provided.model_fields_set


@pytest.mark.parametrize(
    "mode", ("auto", "t2va", "i2va", "fl2va", "l2va", "ref2va")
)
def test_api_project_and_single_video_schemas_accept_official_h3_modes(mode):
    assert MediaDefaultsRequest(
        video_model="runninghub:minimax-h3", h3_mode=mode
    ).h3_mode == mode
    assert ProjectUpdate(h3_mode=mode).h3_mode == mode
    assert SingleVideoRequest(h3_mode=mode).h3_mode == mode


def _install_h3_single_video_route(monkeypatch, tmp_path):
    from novelvideo.api.routes import generation
    from novelvideo.media_capabilities.video import catalog

    ctx = SimpleNamespace(project_id="demo", state_dir=tmp_path)

    class Store:
        async def get_beats_as_dicts(self, episode):
            assert episode == 1
            return [
                {
                    "beat_number": 1,
                    "video_mode": "first_frame",
                    "video_prompt": "A stable shot.",
                }
            ]

    async def resolve_project(*args, **kwargs):
        return SimpleNamespace(
            ctx=ctx,
            username="tester",
            project_name="demo",
            output_dir=str(tmp_path),
        )

    async def make_store(resolved_ctx):
        assert resolved_ctx is ctx
        return Store()

    async def audio_duration(*args, **kwargs):
        return None

    enqueue_calls = []

    async def enqueue(*args, **kwargs):
        enqueue_calls.append((args, kwargs))
        return SimpleNamespace(
            task_state=SimpleNamespace(task_id="task-1"),
            backend="celery",
            queue="video",
        )

    monkeypatch.setattr(generation, "_resolve_generation_project", resolve_project)
    monkeypatch.setattr(generation, "make_sqlite_store_for_context", make_store)
    monkeypatch.setattr(generation, "_api_audio_duration_seconds", audio_duration)
    monkeypatch.setattr(
        generation,
        "get_task_backend",
        lambda: SimpleNamespace(enqueue_project_task=enqueue),
    )
    monkeypatch.setattr(generation, "get_media_capability_store", lambda: object())
    monkeypatch.setattr(generation, "get_media_credential_resolver", lambda: object())
    monkeypatch.setattr(
        catalog,
        "list_video_models",
        lambda *args: (
            SimpleNamespace(
                id="runninghub:minimax-h3",
                available=True,
                unavailable_reason=None,
                supported_modes=("i2va", "fl2va"),
            ),
        ),
    )
    frame = tmp_path / "frames" / "ep001" / "beat_01.png"
    frame.parent.mkdir(parents=True, exist_ok=True)
    frame.write_bytes(b"frame")
    return generation, enqueue_calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "backend",
    (
        pytest.param(None, id="default-underscore"),
        "runninghub-minimax-h3",
        "runninghub:minimax-h3",
    ),
)
@pytest.mark.parametrize("mode", ("t2va", "l2va", "ref2va"))
async def test_single_video_h3_aliases_reject_unverified_modes_before_enqueue(
    monkeypatch, tmp_path, backend, mode
):
    generation, enqueue_calls = _install_h3_single_video_route(
        monkeypatch, tmp_path
    )

    with pytest.raises(HTTPException) as raised:
        body = SingleVideoRequest(h3_mode=mode)
        if backend is not None:
            body = SingleVideoRequest(video_backend=backend, h3_mode=mode)
        await generation.generate_single_video(
            project="demo",
            episode_num=1,
            beat_num=1,
            body=body,
            user={"username": "tester"},
        )

    assert raised.value.status_code == 422
    assert raised.value.detail == {
        "code": "h3.mode_unsupported_by_workflow",
        "mode": mode,
        "workflow": "runninghub:minimax-h3",
    }
    assert enqueue_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "backend",
    (
        pytest.param(None, id="default-underscore"),
        "runninghub-minimax-h3",
        "runninghub:minimax-h3",
    ),
)
async def test_single_video_h3_aliases_prepare_explicit_fl_tail_before_enqueue(
    monkeypatch, tmp_path, backend
):
    generation, enqueue_calls = _install_h3_single_video_route(
        monkeypatch, tmp_path
    )

    with pytest.raises(HTTPException) as raised:
        body = SingleVideoRequest(h3_mode="fl2va")
        if backend is not None:
            body = SingleVideoRequest(video_backend=backend, h3_mode="fl2va")
        await generation.generate_single_video(
            project="demo",
            episode_num=1,
            beat_num=1,
            body=body,
            user={"username": "tester"},
        )

    assert raised.value.status_code == 400
    assert raised.value.detail == "MiniMax H3 fl2va mode requires a last frame"
    assert enqueue_calls == []


def test_media_defaults_use_vip_2k_default_and_preserve_explicit_size():
    assert _media_defaults_payload(
        {"narrative_render_model": "gpt-image-2-vip"}
    )["narrative_render_image_size"] == "2K"
    assert _media_defaults_payload(
        {
            "narrative_render_model": "gpt-image-2-vip",
            "narrative_render_image_size": "4K",
        }
    )["narrative_render_image_size"] == "4K"


def test_get_media_defaults_falls_back_from_legacy_newapi_model(monkeypatch, tmp_path):
    (tmp_path / "project_config.json").write_text(
        json.dumps({"video_backend": "newapi_seedance-1.0-pro-fast"}),
        encoding="utf-8",
    )
    client = _make_client(monkeypatch, tmp_path)

    response = client.get("/api/v1/projects/demo/media-defaults")

    assert response.status_code == 200
    assert response.json()["data"]["video_model"] == "runninghub:minimax-h3"


def test_get_media_defaults_fills_known_workflow_schema_defaults(monkeypatch, tmp_path):
    client = _make_client(monkeypatch, tmp_path)

    response = client.get("/api/v1/projects/demo/media-defaults")

    assert response.status_code == 200
    assert response.json()["data"]["video_workflow_parameters"] == {
        "runninghub:minimax-h3": {"resolution": "720p"}
    }


def test_get_media_defaults_preserves_future_registered_model(monkeypatch, tmp_path):
    future_model = "future:director-v2"
    (tmp_path / "project_config.json").write_text(
        json.dumps({"video_backend": future_model}), encoding="utf-8"
    )
    client = _make_client(
        monkeypatch,
        tmp_path,
        registry=_registry("runninghub:minimax-h3", future_model),
    )

    response = client.get("/api/v1/projects/demo/media-defaults")

    assert response.status_code == 200
    assert response.json()["data"]["video_model"] == future_model


def test_get_media_defaults_preserves_requested_mode_across_workflow_fallback(
    monkeypatch, tmp_path
):
    (tmp_path / "project_config.json").write_text(
        json.dumps(
            {
                "video_backend": "unknown:video",
                "h3_mode": "fl2va",
            }
        ),
        encoding="utf-8",
    )
    registry = VideoWorkflowRegistry(
        (
            _workflow(
                "runninghub:minimax-h3",
                supported_modes=("i2va",),
                default_mode="i2va",
            ),
        )
    )
    client = _make_client(monkeypatch, tmp_path, registry=registry)

    response = client.get("/api/v1/projects/demo/media-defaults")

    assert response.status_code == 200
    assert response.json()["data"]["video_model"] == "runninghub:minimax-h3"
    assert response.json()["data"]["h3_mode"] == "fl2va"


def test_get_media_defaults_preserves_auto_as_request_strategy(
    monkeypatch, tmp_path
):
    future_model = "future:director-v2"
    (tmp_path / "project_config.json").write_text(
        json.dumps({"video_backend": future_model, "h3_mode": "auto"}),
        encoding="utf-8",
    )
    registry = VideoWorkflowRegistry(
        (
            _workflow("runninghub:minimax-h3"),
            _workflow(
                future_model,
                supported_modes=("i2va",),
                default_mode="i2va",
            ),
        )
    )
    client = _make_client(monkeypatch, tmp_path, registry=registry)

    response = client.get("/api/v1/projects/demo/media-defaults")

    assert response.status_code == 200
    assert response.json()["data"]["video_model"] == future_model
    assert response.json()["data"]["h3_mode"] == "auto"


def test_get_media_defaults_preserves_supported_mode(monkeypatch, tmp_path):
    future_model = "future:director-v2"
    (tmp_path / "project_config.json").write_text(
        json.dumps({"video_backend": future_model, "h3_mode": "fl2va"}),
        encoding="utf-8",
    )
    registry = VideoWorkflowRegistry(
        (
            _workflow("runninghub:minimax-h3"),
            _workflow(
                future_model,
                supported_modes=("i2va", "fl2va"),
                default_mode="i2va",
            ),
        )
    )
    client = _make_client(monkeypatch, tmp_path, registry=registry)

    response = client.get("/api/v1/projects/demo/media-defaults")

    assert response.status_code == 200
    assert response.json()["data"]["video_model"] == future_model
    assert response.json()["data"]["h3_mode"] == "fl2va"


def test_put_media_defaults_preserves_unspecified_image_bindings(monkeypatch, tmp_path):
    original = {
        "video_backend": "newapi_seedance-1.0-pro-fast",
        "h3_mode": "auto",
        "narrative_sketch_provider": "grsai-main",
        "narrative_sketch_model": "gpt-image-2",
        "narrative_render_provider": "grsai-main",
        "narrative_render_model": "gpt-image-2",
    }
    config_path = tmp_path / "project_config.json"
    config_path.write_text(json.dumps(original), encoding="utf-8")
    client = _make_client(monkeypatch, tmp_path)

    response = client.put(
        "/api/v1/projects/demo/media-defaults",
        json={"video_model": "runninghub:minimax-h3", "h3_mode": "auto"},
    )

    assert response.status_code == 200
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["video_backend"] == "runninghub:minimax-h3"
    for field in (
        "narrative_sketch_provider",
        "narrative_sketch_model",
        "narrative_render_provider",
        "narrative_render_model",
    ):
        assert persisted[field] == original[field]
    assert response.json()["data"]["narrative_sketch_model"] == "gpt-image-2"


def test_put_media_defaults_rejects_unknown_without_persisting(monkeypatch, tmp_path):
    original = {
        "video_backend": "runninghub:minimax-h3",
        "h3_mode": "i2va",
    }
    config_path = tmp_path / "project_config.json"
    config_path.write_text(json.dumps(original), encoding="utf-8")
    client = _make_client(monkeypatch, tmp_path)

    response = client.put(
        "/api/v1/projects/demo/media-defaults",
        json={"video_model": "unknown:video", "h3_mode": "auto"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "Video workflow is unavailable for narrative groups"
    )
    assert json.loads(config_path.read_text(encoding="utf-8")) == original


def test_put_media_defaults_rejects_unsupported_mode_without_persisting(
    monkeypatch, tmp_path
):
    original = {
        "video_backend": "runninghub:minimax-h3",
        "h3_mode": "i2va",
    }
    config_path = tmp_path / "project_config.json"
    config_path.write_text(json.dumps(original), encoding="utf-8")
    registry = VideoWorkflowRegistry(
        (
            _workflow(
                "runninghub:minimax-h3",
                supported_modes=("i2va",),
                default_mode="i2va",
            ),
        )
    )
    client = _make_client(monkeypatch, tmp_path, registry=registry)

    response = client.put(
        "/api/v1/projects/demo/media-defaults",
        json={"video_model": "runninghub:minimax-h3", "h3_mode": "fl2va"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "h3.mode_unsupported_by_workflow",
        "mode": "fl2va",
        "workflow": "runninghub:minimax-h3",
    }
    assert json.loads(config_path.read_text(encoding="utf-8")) == original


def test_put_media_defaults_accepts_auto_outside_transport_modes(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "project_config.json"
    config_path.write_text("{}", encoding="utf-8")
    registry = VideoWorkflowRegistry(
        (
            _workflow(
                "runninghub:minimax-h3",
                supported_modes=("i2va",),
                default_mode="i2va",
            ),
        )
    )
    client = _make_client(monkeypatch, tmp_path, registry=registry)

    response = client.put(
        "/api/v1/projects/demo/media-defaults",
        json={"video_model": "runninghub:minimax-h3", "h3_mode": "auto"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["h3_mode"] == "auto"
    assert json.loads(config_path.read_text(encoding="utf-8"))["h3_mode"] == "auto"


def test_put_media_defaults_saves_resolved_workflow_parameters(monkeypatch, tmp_path):
    config_path = tmp_path / "project_config.json"
    config_path.write_text("{}", encoding="utf-8")
    client = _make_client(monkeypatch, tmp_path)

    response = client.put(
        "/api/v1/projects/demo/media-defaults",
        json={
            "video_model": "runninghub:minimax-h3",
            "video_workflow_parameters": {
                "runninghub:minimax-h3": {"resolution": "1080p"}
            },
        },
    )

    assert response.status_code == 200
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["video_workflow_parameters"] == {
        "runninghub:minimax-h3": {"resolution": "1080p"}
    }
    assert response.json()["data"]["video_workflow_parameters"] == {
        "runninghub:minimax-h3": {"resolution": "1080p"}
    }


def test_put_media_defaults_without_workflow_parameters_preserves_existing(
    monkeypatch, tmp_path
):
    original_parameters = {
        "runninghub:minimax-h3": {"resolution": "1080p"},
    }
    config_path = tmp_path / "project_config.json"
    config_path.write_text(
        json.dumps({"video_workflow_parameters": original_parameters}),
        encoding="utf-8",
    )
    client = _make_client(monkeypatch, tmp_path)

    response = client.put(
        "/api/v1/projects/demo/media-defaults",
        json={"video_model": "runninghub:minimax-h3"},
    )

    assert response.status_code == 200
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["video_workflow_parameters"] == original_parameters


def test_put_media_defaults_updates_only_requested_namespace_with_full_values(
    monkeypatch, tmp_path
):
    future = "future:director-v2"
    registry = VideoWorkflowRegistry(
        (
            _workflow("runninghub:minimax-h3"),
            _workflow(
                future,
                parameters=(
                    _enum_parameter("quality", "standard", "standard", "high"),
                    _enum_parameter("speed", "normal", "normal", "fast"),
                ),
            ),
        )
    )
    config_path = tmp_path / "project_config.json"
    config_path.write_text(
        json.dumps(
            {
                "video_workflow_parameters": {
                    "runninghub:minimax-h3": {"resolution": "720p"},
                    future: {"quality": "high", "speed": "fast"},
                }
            }
        ),
        encoding="utf-8",
    )
    client = _make_client(monkeypatch, tmp_path, registry=registry)

    response = client.put(
        "/api/v1/projects/demo/media-defaults",
        json={
            "video_model": "runninghub:minimax-h3",
            "video_workflow_parameters": {future: {"quality": "standard"}},
        },
    )

    assert response.status_code == 200
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["video_workflow_parameters"] == {
        "runninghub:minimax-h3": {"resolution": "720p"},
        future: {"quality": "standard", "speed": "normal"},
    }


@pytest.mark.parametrize(
    "parameters",
    [
        {"unknown:workflow": {"resolution": "1080p"}},
        {"runninghub:minimax-h3": {"unknown": "1080p"}},
        {"runninghub:minimax-h3": {"resolution": "4K"}},
    ],
    ids=("unknown-workflow", "unknown-key", "unknown-value"),
)
def test_put_media_defaults_rejects_invalid_workflow_parameters_without_writing(
    monkeypatch, tmp_path, parameters
):
    original = {
        "video_backend": "runninghub:minimax-h3",
        "video_workflow_parameters": {
            "runninghub:minimax-h3": {"resolution": "720p"}
        },
    }
    config_path = tmp_path / "project_config.json"
    original_text = json.dumps(original)
    config_path.write_text(original_text, encoding="utf-8")
    client = _make_client(monkeypatch, tmp_path)

    response = client.put(
        "/api/v1/projects/demo/media-defaults",
        json={
            "video_model": "runninghub:minimax-h3",
            "video_workflow_parameters": parameters,
        },
    )

    assert response.status_code == 422
    assert config_path.read_text(encoding="utf-8") == original_text


@pytest.mark.parametrize(
    ("legacy_resolution", "expected"),
    [("720p", "720p"), ("1080p", "1080p"), ("4K", "720p")],
)
def test_get_media_defaults_reads_legacy_h3_resolution_without_writing(
    monkeypatch, tmp_path, legacy_resolution, expected
):
    config_path = tmp_path / "project_config.json"
    original_text = json.dumps(
        {"video_resolution": legacy_resolution, "project_uuid": "stable-project-id"}
    )
    config_path.write_text(original_text, encoding="utf-8")
    client = _make_client(monkeypatch, tmp_path)

    response = client.get("/api/v1/projects/demo/media-defaults")

    assert response.status_code == 200
    assert response.json()["data"]["video_workflow_parameters"] == {
        "runninghub:minimax-h3": {"resolution": expected}
    }
    assert config_path.read_text(encoding="utf-8") == original_text


def test_get_media_defaults_does_not_apply_legacy_resolution_when_h3_namespace_exists(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "project_config.json"
    config_path.write_text(
        json.dumps(
            {
                "video_resolution": "1080p",
                "video_workflow_parameters": {"runninghub:minimax-h3": {}},
            }
        ),
        encoding="utf-8",
    )
    client = _make_client(monkeypatch, tmp_path)

    response = client.get("/api/v1/projects/demo/media-defaults")

    assert response.status_code == 200
    assert response.json()["data"]["video_workflow_parameters"] == {
        "runninghub:minimax-h3": {"resolution": "720p"}
    }
