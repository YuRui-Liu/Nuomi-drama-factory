import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.routes import projects
from novelvideo.api.routes.projects import _media_defaults_payload
from novelvideo.api.schemas import MediaDefaultsRequest
from novelvideo.media_capabilities.video.workflow_registry import (
    VideoWorkflowDefinition,
    VideoWorkflowRegistry,
    VideoWorkflowScene,
)


def _workflow(
    model: str,
    *,
    supported_modes: tuple[str, ...] = ("auto", "i2va", "fl2va"),
    default_mode: str = "auto",
) -> VideoWorkflowDefinition:
    return VideoWorkflowDefinition(
        id=model,
        label=model,
        provider=model.partition(":")[0],
        adapter_key=model.partition(":")[2],
        scenes=frozenset({VideoWorkflowScene.NARRATIVE_GROUP}),
        supported_modes=supported_modes,
        default_mode=default_mode,
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


def test_get_media_defaults_uses_fallback_workflow_default_mode(
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
    assert response.json()["data"]["h3_mode"] == "i2va"


def test_get_media_defaults_uses_resolved_workflow_default_for_unsupported_mode(
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
    assert response.json()["data"]["h3_mode"] == "i2va"


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
        json={"video_model": "runninghub:minimax-h3", "h3_mode": "auto"},
    )

    assert response.status_code == 422
    assert json.loads(config_path.read_text(encoding="utf-8")) == original
