from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_character_state_prompt_is_a_fixed_front_side_back_resource_sheet() -> None:
    from novelvideo.generators.nanobanana_character import (
        build_character_state_sheet_prompt,
    )

    prompt = build_character_state_sheet_prompt(
        character_name="林默",
        character_tag="[LinM]",
        appearance="黑色防水夹克，灰色连帽衫，深色长裤",
        style_instructions="animated cinematic style",
        avoid_instructions="no text",
        ethnicity="Chinese",
        has_costume_reference=True,
    )

    assert "3-panel" in prompt
    assert "FRONT VIEW" in prompt
    assert "SIDE VIEW" in prompt
    assert "BACK VIEW" in prompt
    assert "Panel 1: FACE CLOSEUP" not in prompt
    assert "Panel 2: THREE-QUARTER" not in prompt
    assert "4-panel" not in prompt


@pytest.mark.asyncio
async def test_identity_state_generation_registers_candidates_without_overwriting_current(
    monkeypatch, tmp_path: Path
) -> None:
    from novelvideo.models import CharacterIdentity, NovelCharacter
    from novelvideo.production_workflow import AdoptionStatus, ProductionWorkflowStore
    from novelvideo.task_backend.runners import character_image

    identity = CharacterIdentity(
        identity_id="linmo-duty",
        character_name="林默",
        identity_name="值班员",
        appearance_details="黑色防水夹克，灰色连帽衫，深色长裤",
    )
    character = NovelCharacter(name="林默")
    character.identities = [identity]
    portrait = tmp_path / "assets" / "characters" / "林默" / "portrait.png"
    portrait.parent.mkdir(parents=True, exist_ok=True)
    portrait.write_bytes(b"portrait")
    generated_prompts: list[str] = []

    class FakeSQLiteStore:
        def __init__(self, *_args, **_kwargs):
            pass

        async def initialize(self):
            pass

        async def load_graph_state(self):
            pass

        def get_character(self, name):
            return character if name == "林默" else None

        async def close(self):
            pass

    async def fake_grsai_image(**kwargs):
        generated_prompts.append(str(kwargs["prompt"]))
        output = Path(kwargs["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(f"candidate-{len(generated_prompts)}".encode())
        return output

    monkeypatch.setattr("novelvideo.sqlite_store.SQLiteStore", FakeSQLiteStore)
    monkeypatch.setattr(character_image, "_generate_grsai_image", fake_grsai_image)
    monkeypatch.setattr(
        character_image,
        "get_task_manager",
        lambda: SimpleNamespace(update_progress_for_project=lambda *_a, **_k: None),
    )
    monkeypatch.setattr("novelvideo.api.deps.get_media_capability_store", lambda: object())
    monkeypatch.setattr("novelvideo.api.deps.get_media_credential_resolver", lambda: object())
    monkeypatch.setattr(
        "novelvideo.media_capabilities.runtime.configuration.load_grsai_runtime_configuration",
        lambda *_args: SimpleNamespace(model="gpt-image-2"),
    )
    monkeypatch.setattr(
        "novelvideo.project_config.load_project_config_file",
        lambda *_args: {"ethnicity": "Chinese", "production_recipe_version": "1"},
    )
    monkeypatch.setattr(
        "novelvideo.character_visual.CharacterVisualWorkspaceStore.get_confirmed_bible",
        lambda *_args: None,
    )

    ctx = SimpleNamespace(
        owner_project_label="frank/demo",
        owner_username="frank",
        project_name="demo",
        project_id="project-1",
        output_dir=tmp_path,
        state_dir=tmp_path / "state",
    )

    async def run(task_id: str):
        return await character_image._run_character_image(
            {
                "task_id": task_id,
                "task_type": "identity_image",
                "scope": "character:林默:identity:值班员",
                "payload": {
                    "mode": "identity_image",
                    "character_name": "林默",
                    "identity_id": identity.identity_id,
                    "identity_name": identity.identity_name,
                    "style": "anime",
                    "output_dir": str(tmp_path),
                },
            },
            ctx,
        )

    first = await run("attempt-1")
    canonical = tmp_path / "assets" / "characters" / "林默" / "identities" / "值班员.png"
    assert canonical.read_bytes() == b"candidate-1"
    assert first["adoption_status"] == AdoptionStatus.PROVISIONAL.value
    assert Path(first["path"]).name != canonical.name

    second = await run("attempt-2")
    assert canonical.read_bytes() == b"candidate-1"
    assert second["adoption_status"] == AdoptionStatus.CANDIDATE.value
    assert first["path"] != second["path"]

    workflow = ProductionWorkflowStore(ctx.state_dir / "production_workflow.json")
    slot, versions = workflow.get_slot("character:林默:state:linmo-duty")
    assert slot.current_version_id == first["version_id"]
    assert len(versions) == 2
    assert versions[second["version_id"]].generation_metadata == {
        "character_name": "林默",
        "identity_id": "linmo-duty",
        "state_id": "linmo-duty",
        "panel_layout": ["front", "side", "back"],
        "recipe_revision": "1",
        "reference_sources": [portrait.relative_to(tmp_path).as_posix()],
        "canonical_path": canonical.relative_to(tmp_path).as_posix(),
    }
    assert all("3-panel" in prompt for prompt in generated_prompts)
    assert json.loads((ctx.state_dir / "production_workflow.json").read_text("utf-8"))
