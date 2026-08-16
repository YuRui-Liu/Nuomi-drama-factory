from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_scene_reference_runner_does_not_initialize_cognee(monkeypatch, tmp_path):
    from novelvideo.models import NovelScene
    from novelvideo.task_backend.runners import scene_reference

    scene = NovelScene(name="大厅", description="地下大厅")
    calls: dict[str, object] = {}

    class FakeSQLiteStore:
        def __init__(self, project_name, *, output_dir, state_dir):
            calls["store_args"] = (project_name, output_dir, state_dir)

        async def initialize(self):
            calls["sqlite_initialized"] = True

        async def get_scene(self, name):
            return scene if name == scene.name else None

        async def update_scene(self, *_args, **_kwargs):
            pass

        async def close(self):
            calls["sqlite_closed"] = True

    async def fake_generate(**kwargs):
        calls["generate"] = kwargs
        output = Path(tmp_path) / "master.png"
        output.write_bytes(b"png")
        return output

    class FailCogneeStore:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("scene image generation must not initialize Cognee")

    monkeypatch.setattr("novelvideo.sqlite_store.SQLiteStore", FakeSQLiteStore)
    monkeypatch.setattr("novelvideo.cognee.CogneeStore", FailCogneeStore)
    monkeypatch.setattr(
        "novelvideo.generators.scene_reference_images.generate_scene_reference_image",
        fake_generate,
    )
    monkeypatch.setattr(
        scene_reference,
        "get_task_manager",
        lambda: SimpleNamespace(update_progress_for_project=lambda *_a, **_k: None),
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.get_media_capability_store", lambda: object()
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.get_media_credential_resolver", lambda: object()
    )
    monkeypatch.setattr(
        "novelvideo.media_capabilities.runtime.configuration.load_grsai_runtime_configuration",
        lambda *_args: SimpleNamespace(model="gpt-image-2"),
    )
    monkeypatch.setattr(
        "novelvideo.config.get_style_preset",
        lambda *_args, **_kwargs: {
            "label": "动漫",
            "style_instructions": "anime",
            "avoid_instructions": "text",
        },
    )

    ctx = SimpleNamespace(
        owner_project_label="frank/demo",
        owner_username="frank",
        project_name="demo",
        output_dir=tmp_path,
        state_dir=tmp_path,
    )
    result = await scene_reference._run_scene_reference_asset(
        {
            "payload": {
                "scene_name": scene.name,
                "kind": "master",
                "output_dir": str(tmp_path),
            }
        },
        ctx,
    )

    assert result["path"].endswith("master.png")
    assert calls["sqlite_initialized"] is True
    assert calls["sqlite_closed"] is True
    assert calls["generate"]["provider"] == "grsai"
    assert calls["generate"]["model"] == "gpt-image-2"
