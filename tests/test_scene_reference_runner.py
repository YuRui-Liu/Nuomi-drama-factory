from pathlib import Path
from types import SimpleNamespace

import pytest


def test_scene_canonical_replace_failure_rolls_back_workflow_and_canonical(
    monkeypatch, tmp_path
):
    from novelvideo.task_backend.runners import scene_reference

    candidate = tmp_path / "assets/scenes/hall/versions/master-v1.png"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"candidate")
    canonical = tmp_path / "assets/scenes/hall/master.png"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_bytes(b"official")
    real_replace = scene_reference.os.replace

    def fail_canonical_replace(source, target):
        if Path(target) == canonical:
            raise OSError("canonical replace failed")
        return real_replace(source, target)

    monkeypatch.setattr(scene_reference.os, "replace", fail_canonical_replace)
    with pytest.raises(OSError, match="canonical replace failed"):
        scene_reference._register_scene_reference_candidate(
            ctx=SimpleNamespace(state_dir=tmp_path, requester_username="system"),
            output_dir=tmp_path,
            scene=SimpleNamespace(name="hall", base_scene_id=""),
            kind="master",
            output_path=candidate,
            canonical_path=canonical,
            source_attempt_id="attempt",
            recipe_revision="1",
        )

    assert canonical.read_bytes() == b"official"
    assert not (tmp_path / "production_workflow.json").exists()


@pytest.mark.asyncio
async def test_scene_reference_runner_does_not_initialize_cognee(monkeypatch, tmp_path):
    from novelvideo.models import NovelScene
    from novelvideo.task_backend.runners import scene_reference

    scene = NovelScene(name="大厅", description="地下大厅")
    calls: dict[str, object] = {"clear_stale": []}

    class FakeSQLiteStore:
        def __init__(self, project_name, *, output_dir, state_dir):
            calls["store_args"] = (project_name, output_dir, state_dir)

        async def initialize(self):
            calls["sqlite_initialized"] = True

        async def get_scene(self, name):
            return scene if name == scene.name else None

        async def update_scene(self, *_args, **_kwargs):
            pass

        async def clear_scene_stale_reference_kind(self, name, kind):
            calls["clear_stale"].append((name, kind))
            return True

        async def close(self):
            calls["sqlite_closed"] = True

    async def fake_generate(**kwargs):
        calls["generate"] = kwargs
        output = Path(kwargs["output_path_override"])
        output.parent.mkdir(parents=True, exist_ok=True)
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

    assert result["path"].endswith(".png")
    assert result["slot_id"] == "scene:大厅:base:master"
    assert result["adoption_status"] == "provisional"
    assert (tmp_path / "assets" / "scenes" / "大厅" / "master.png").read_bytes() == b"png"
    assert calls["sqlite_initialized"] is True
    assert calls["sqlite_closed"] is True
    assert calls["generate"]["provider"] == "grsai"
    assert calls["generate"]["model"] == "gpt-image-2"
    assert "versions" in str(calls["generate"]["output_path_override"])
    assert calls["clear_stale"] == [("大厅", "master")]

    from novelvideo.production_workflow import ProductionWorkflowStore

    slot, versions = ProductionWorkflowStore(
        tmp_path / "production_workflow.json"
    ).get_slot("scene:大厅:base:master")
    assert slot.current_version_id == result["version_id"]
    version = versions[result["version_id"]]
    assert version.asset_path == Path(result["path"]).relative_to(tmp_path).as_posix()
    assert version.generation_metadata["anchor_kind"] == "master"
    assert version.generation_metadata["canonical_path"] == (
        "assets/scenes/大厅/master.png"
    )

    second = await scene_reference._run_scene_reference_asset(
        {
            "payload": {
                "scene_name": scene.name,
                "kind": "master",
                "output_dir": str(tmp_path),
            }
        },
        ctx,
    )
    second_slot, _second_versions = ProductionWorkflowStore(
        tmp_path / "production_workflow.json"
    ).get_slot("scene:大厅:base:master")

    assert second["adoption_status"] == "candidate"
    assert second["version_id"] != result["version_id"]
    assert second_slot.current_version_id == result["version_id"]
    assert calls["clear_stale"] == [("大厅", "master")]
