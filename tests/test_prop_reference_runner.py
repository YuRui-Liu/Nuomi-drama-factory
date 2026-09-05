from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_prop_reference_runner_registers_candidates_without_overwriting_current(
    monkeypatch,
    tmp_path,
):
    from novelvideo.models import NovelProp
    from novelvideo.task_backend.runners import prop_reference

    prop = NovelProp(
        name="手机",
        prop_type="document",
        visual_prompt="黑色旧手机，右上角裂纹；屏幕内容由后期叠加",
    )
    calls: list[dict] = []

    class FakeSQLiteStore:
        def __init__(self, *_args, **_kwargs):
            pass

        async def initialize(self):
            pass

        async def get_prop(self, name):
            return prop if name == prop.name else None

        async def close(self):
            pass

    async def fake_generate(**kwargs):
        calls.append(kwargs)
        output = Path(kwargs["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(f"candidate-{len(calls)}".encode())
        return output

    monkeypatch.setattr("novelvideo.sqlite_store.SQLiteStore", FakeSQLiteStore)
    monkeypatch.setattr(
        "novelvideo.task_backend.runners.character_image._generate_grsai_image",
        fake_generate,
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.get_media_capability_store", lambda: object()
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.get_media_credential_resolver", lambda: object()
    )
    monkeypatch.setattr(
        "novelvideo.media_capabilities.runtime.configuration.load_grsai_runtime_configuration",
        lambda *_args: SimpleNamespace(model="runtime-model"),
    )
    monkeypatch.setattr(
        prop_reference,
        "get_task_manager",
        lambda: SimpleNamespace(update_progress_for_project=lambda *_a, **_k: None),
    )

    ctx = SimpleNamespace(
        owner_project_label="frank/demo",
        owner_username="frank",
        project_name="demo",
        output_dir=tmp_path,
        state_dir=tmp_path,
    )
    envelope = {
        "task_id": "attempt-1",
        "payload": {
            "prop_name": prop.name,
            "style": "anime",
            "model": "requested-model",
            "output_dir": str(tmp_path),
        },
    }

    first = await prop_reference._run_prop_reference_asset(envelope, ctx)
    second = await prop_reference._run_prop_reference_asset(
        {**envelope, "task_id": "attempt-2"}, ctx
    )

    canonical = tmp_path / "assets" / "props" / prop.name / "reference_3view.png"
    assert canonical.read_bytes() == b"candidate-1"
    assert first["slot_id"] == "prop:手机:reference"
    assert first["adoption_status"] == "provisional"
    assert second["adoption_status"] == "candidate"
    assert calls[0]["model"] == "requested-model"
    assert calls[0]["aspect_ratio"] == "16:9"
    assert "front, strict side, and back" in calls[0]["prompt"]
    assert "readable text" in calls[0]["prompt"]
    assert all("versions" in str(call["output_path"]) for call in calls)

    from novelvideo.production_workflow import ProductionWorkflowStore

    slot, versions = ProductionWorkflowStore(
        tmp_path / "production_workflow.json"
    ).get_slot("prop:手机:reference")
    assert slot.current_version_id == first["version_id"]
    assert len(versions) == 2
    assert versions[second["version_id"]].generation_metadata["canonical_path"] == (
        "assets/props/手机/reference_3view.png"
    )
