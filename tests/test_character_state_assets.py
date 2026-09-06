from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image


def test_character_state_prompt_uses_identity_sheet_v2_contract() -> None:
    from novelvideo.character_visual.identity_sheet import build_identity_sheet_v2_prompt

    prompt = build_identity_sheet_v2_prompt(
        character_name="林默",
        character_tag="[LinM]",
        appearance="黑色防水夹克，灰色连帽衫，深色长裤",
        project_style="anime",
        style_instructions="animated cinematic style",
        avoid_instructions="no text",
        ethnicity="Chinese",
        has_costume_reference=True,
    )

    assert "3-panel" in prompt
    assert "LEFT 50%" in prompt
    assert "HEADLESS FRONT FULL BODY" in prompt
    assert "BACK FULL BODY" in prompt
    assert "Portrait is the sole facial identity authority" in prompt


@pytest.mark.parametrize(("value", "expected"), [(".", "untitled"), ("..", "untitled")])
def test_safe_asset_name_rejects_relative_directory_segments(value: str, expected: str) -> None:
    from novelvideo.task_backend.runners.character_image import _safe_asset_name

    assert _safe_asset_name(value) == expected


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
    Image.new("RGB", (320, 480), "gray").save(portrait)
    generated_prompts: list[str] = []
    generated_aspect_ratios: list[str] = []

    def custom_style(style_id: str, *, project_dir=None, **_kwargs):
        assert style_id == "custom_anime_realistic"
        assert Path(project_dir) == tmp_path
        return SimpleNamespace(
            style_family="live_action",
            animation_subtype="",
            style_instructions="custom practical photography",
        )

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
        generated_aspect_ratios.append(str(kwargs["aspect_ratio"]))
        output = Path(kwargs["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (900, 600), (120 + len(generated_prompts), 120, 120)).save(output)
        return output

    async def passing_qc(**_kwargs):
        from novelvideo.character_visual.identity_sheet import (
            IdentitySheetQualityReport,
            IdentitySheetStyleFamily,
            resolve_identity_sheet_style_family,
        )

        assert Path(_kwargs["project_dir"]) == tmp_path
        assert resolve_identity_sheet_style_family(
            _kwargs["style"], project_dir=_kwargs["project_dir"]
        ) is IdentitySheetStyleFamily.THREE_D_REALISTIC
        return IdentitySheetQualityReport(
            passed=True,
            checks={"front_face_detected": False, "back_face_visible": False},
            issues=[],
            style_family=IdentitySheetStyleFamily.TWO_D,
        )

    monkeypatch.setattr("novelvideo.sqlite_store.SQLiteStore", FakeSQLiteStore)
    monkeypatch.setattr(character_image, "_generate_grsai_image", fake_grsai_image)
    monkeypatch.setattr(character_image, "assess_identity_sheet_quality", passing_qc)
    monkeypatch.setattr(
        "novelvideo.services.style_service.StyleService.get_style",
        custom_style,
    )
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
                    "style": "custom_anime_realistic",
                    "output_dir": str(tmp_path),
                },
            },
            ctx,
        )

    first = await run("attempt-1")
    assert "credible skin and fabric detail" in generated_prompts[0]
    canonical = tmp_path / "assets" / "characters" / "林默" / "identities" / "值班员.png"
    assert canonical.read_bytes() == Path(first["path"]).read_bytes()
    assert first["adoption_status"] == AdoptionStatus.PROVISIONAL.value
    assert Path(first["path"]).name != canonical.name

    second = await run("attempt-2")
    assert canonical.read_bytes() == Path(first["path"]).read_bytes()
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
        "layout_version": "identity_sheet_v2",
        "panel_layout": ["portrait_3q", "front_headless", "back_fullbody"],
        "face_source": portrait.relative_to(tmp_path).as_posix(),
        "face_source_panel": "portrait_3q",
        "quality_report": {
            "passed": True,
            "checks": {"front_face_detected": False, "back_face_visible": False},
            "issues": [],
            "style_family": "2d",
        },
        "raw_candidate_path": versions[second["version_id"]].generation_metadata["raw_candidate_path"],
        "recipe_revision": "1",
        "reference_sources": [portrait.relative_to(tmp_path).as_posix()],
        "canonical_path": canonical.relative_to(tmp_path).as_posix(),
    }
    assert all("Identity Sheet v2" in prompt for prompt in generated_prompts)
    assert generated_aspect_ratios == ["3:2", "3:2"]
    assert first["layout_version"] == "identity_sheet_v2"
    assert first["qc_passed"] is True
    assert json.loads((ctx.state_dir / "production_workflow.json").read_text("utf-8"))


@pytest.mark.asyncio
async def test_qc_failure_registers_candidate_but_preserves_canonical(monkeypatch, tmp_path: Path) -> None:
    from novelvideo.character_visual.identity_sheet import (
        IdentitySheetQualityReport,
        IdentitySheetStyleFamily,
    )
    from novelvideo.models import CharacterIdentity, NovelCharacter
    from novelvideo.task_backend.runners import character_image

    identity = CharacterIdentity(identity_id="i1", character_name="林默", identity_name="值班员", appearance_details="夹克")
    character = NovelCharacter(name="林默")
    character.identities = [identity]
    portrait = tmp_path / "assets/characters/林默/portrait.png"
    portrait.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 480), "gray").save(portrait)
    canonical = tmp_path / "assets/characters/林默/identities/值班员.png"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(b"official")

    async def generate(**kwargs):
        output = Path(kwargs["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (900, 600), "gray").save(output)
        return output

    async def fail_qc(**_kwargs):
        return IdentitySheetQualityReport(passed=False, checks={"dead_eyes": True}, issues=["dead_eyes"], style_family=IdentitySheetStyleFamily.TWO_D)

    monkeypatch.setattr(character_image, "_generate_grsai_image", generate)
    monkeypatch.setattr(character_image, "assess_identity_sheet_quality", fail_qc)
    generation = await character_image._generate_identity_image(
        character=character, ethnicity="Chinese", identity_id="i1", identity_name="值班员",
        output_dir=tmp_path, style="anime", model="m", task_type="identity_image", scope="", update=lambda *_: None,
    )
    result = character_image._register_character_state_candidate(
        ctx=SimpleNamespace(state_dir=tmp_path / "state"), output_dir=tmp_path,
        character_name="林默", identity_id="i1", generation=generation,
        source_attempt_id="attempt", recipe_revision="1",
    )

    assert canonical.read_bytes() == b"official"
    assert result["qc_passed"] is False
    from novelvideo.production_workflow import ProductionWorkflowStore
    _slot, versions = ProductionWorkflowStore(tmp_path / "state/production_workflow.json").get_slot(
        "character:林默:state:i1"
    )
    version = versions[result["version_id"]]
    assert version.qc_passed is False
    assert version.soft_issues == ["dead_eyes"]
    assert version.adoption_status.value == "candidate"


@pytest.mark.asyncio
async def test_age_variant_requires_identity_portrait_before_model_call(monkeypatch, tmp_path: Path) -> None:
    from novelvideo.models import CharacterIdentity, NovelCharacter
    from novelvideo.task_backend.runners import character_image

    identity = CharacterIdentity(
        identity_id="old", character_name="林默", identity_name="老年",
        age_group="old", appearance_details="灰色长袍",
    )
    character = NovelCharacter(name="林默", age_group="youth")
    character.identities = [identity]
    portrait = tmp_path / "assets/characters/林默/portrait.png"
    portrait.parent.mkdir(parents=True)
    Image.new("RGB", (320, 480), "gray").save(portrait)
    called = False

    async def generate(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("transport must not be called")

    monkeypatch.setattr(character_image, "_generate_grsai_image", generate)
    with pytest.raises(RuntimeError, match="Identity Portrait"):
        await character_image._generate_identity_image(
            character=character, ethnicity="Chinese", identity_id="old", identity_name="老年",
            output_dir=tmp_path, style="anime", model="m", task_type="identity_image", scope="", update=lambda *_: None,
        )
    assert called is False


@pytest.mark.asyncio
async def test_qc_unavailable_registers_visible_candidate_and_preserves_canonical(
    monkeypatch, tmp_path: Path
) -> None:
    from novelvideo.character_visual.identity_sheet import (
        IdentitySheetQualityReport,
        IdentitySheetStyleFamily,
    )
    from novelvideo.models import CharacterIdentity, NovelCharacter
    from novelvideo.task_backend.runners import character_image

    identity = CharacterIdentity(
        identity_id="i1", character_name="林默", identity_name="值班员", appearance_details="夹克"
    )
    character = NovelCharacter(name="林默")
    character.identities = [identity]
    portrait = tmp_path / "assets/characters/林默/portrait.png"
    portrait.parent.mkdir(parents=True)
    Image.new("RGB", (320, 480), "gray").save(portrait)
    canonical = tmp_path / "assets/characters/林默/identities/值班员.png"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(b"official")

    async def generate(**kwargs):
        output = Path(kwargs["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (900, 600), "gray").save(output)
        return output

    async def unavailable_qc(**_kwargs):
        return IdentitySheetQualityReport(
            passed=False,
            checks={"qc_unavailable": False},
            issues=["qc_unavailable"],
            style_family=IdentitySheetStyleFamily.TWO_D,
        )

    monkeypatch.setattr(character_image, "_generate_grsai_image", generate)
    monkeypatch.setattr(character_image, "assess_identity_sheet_quality", unavailable_qc)
    generation = await character_image._generate_identity_image(
        character=character, ethnicity="Chinese", identity_id="i1", identity_name="值班员",
        output_dir=tmp_path, style="anime", model="m", task_type="identity_image", scope="", update=lambda *_: None,
    )
    result = character_image._register_character_state_candidate(
        ctx=SimpleNamespace(state_dir=tmp_path / "state"), output_dir=tmp_path,
        character_name="林默", identity_id="i1", generation=generation,
        source_attempt_id="attempt", recipe_revision="1",
    )

    assert canonical.read_bytes() == b"official"
    from novelvideo.production_workflow import ProductionWorkflowStore
    _slot, versions = ProductionWorkflowStore(tmp_path / "state/production_workflow.json").get_slot(
        "character:林默:state:i1"
    )
    version = versions[result["version_id"]]
    assert result["qc_passed"] is False
    assert version.soft_issues == ["qc_unavailable"]
    assert version.generation_metadata["quality_report"]["issues"] == ["qc_unavailable"]
    assert version.generation_metadata["layout_version"] == "identity_sheet_v2"
    assert Path(tmp_path / version.asset_path).is_file()


def test_canonical_staging_failure_does_not_write_workflow(monkeypatch, tmp_path: Path) -> None:
    from novelvideo.character_visual.identity_sheet import (
        IdentitySheetQualityReport,
        IdentitySheetStyleFamily,
    )
    from novelvideo.task_backend.runners import character_image

    candidate = tmp_path / "assets/characters/林默/identities/值班员/versions/v1.png"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"candidate")
    portrait = tmp_path / "assets/characters/林默/portrait.png"
    portrait.parent.mkdir(parents=True, exist_ok=True)
    portrait.write_bytes(b"portrait")
    canonical = tmp_path / "assets/characters/林默/identities/值班员.png"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_bytes(b"official")
    generation = character_image.CharacterStateGeneration(
        output_path=candidate,
        canonical_path=canonical,
        reference_paths=(portrait,),
        prompt="prompt",
        state_id="i1",
        raw_candidate_path=candidate.with_suffix(".raw.png"),
        face_source=portrait,
        quality_report=IdentitySheetQualityReport(
            passed=True,
            checks={"front_face_detected": False, "back_face_visible": False},
            issues=[],
            style_family=IdentitySheetStyleFamily.TWO_D,
        ),
    )

    def fail_copy(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(character_image.shutil, "copy2", fail_copy)
    with pytest.raises(OSError, match="disk full"):
        character_image._register_character_state_candidate(
            ctx=SimpleNamespace(state_dir=tmp_path / "state"),
            output_dir=tmp_path,
            character_name="林默",
            identity_id="i1",
            generation=generation,
            source_attempt_id="attempt",
            recipe_revision="1",
        )

    assert canonical.read_bytes() == b"official"
    assert not (tmp_path / "state/production_workflow.json").exists()


def test_canonical_replace_failure_rolls_back_workflow_and_canonical(
    monkeypatch, tmp_path: Path
) -> None:
    from novelvideo.character_visual.identity_sheet import (
        IdentitySheetQualityReport,
        IdentitySheetStyleFamily,
    )
    from novelvideo.task_backend.runners import character_image

    candidate = tmp_path / "assets/characters/林默/identities/值班员/versions/v1.png"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"candidate")
    portrait = tmp_path / "assets/characters/林默/portrait.png"
    portrait.parent.mkdir(parents=True, exist_ok=True)
    portrait.write_bytes(b"portrait")
    canonical = tmp_path / "assets/characters/林默/identities/值班员.png"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_bytes(b"official")
    generation = character_image.CharacterStateGeneration(
        output_path=candidate,
        canonical_path=canonical,
        reference_paths=(portrait,),
        prompt="prompt",
        state_id="i1",
        raw_candidate_path=candidate.with_suffix(".raw.png"),
        face_source=portrait,
        quality_report=IdentitySheetQualityReport(
            passed=True,
            checks={"front_face_detected": False, "back_face_visible": False},
            issues=[],
            style_family=IdentitySheetStyleFamily.TWO_D,
        ),
    )
    real_replace = character_image.os.replace

    def fail_canonical_replace(source, target):
        if Path(target) == canonical:
            raise OSError("canonical replace failed")
        return real_replace(source, target)

    monkeypatch.setattr(character_image.os, "replace", fail_canonical_replace)
    with pytest.raises(OSError, match="canonical replace failed"):
        character_image._register_character_state_candidate(
            ctx=SimpleNamespace(state_dir=tmp_path / "state"),
            output_dir=tmp_path,
            character_name="林默",
            identity_id="i1",
            generation=generation,
            source_attempt_id="attempt",
            recipe_revision="1",
        )

    assert canonical.read_bytes() == b"official"
    assert not (tmp_path / "state/production_workflow.json").exists()


def test_workflow_save_failure_never_promotes_canonical(monkeypatch, tmp_path: Path) -> None:
    from novelvideo.character_visual.identity_sheet import (
        IdentitySheetQualityReport,
        IdentitySheetStyleFamily,
    )
    from novelvideo.production_workflow import ProductionWorkflowStore
    from novelvideo.task_backend.runners import character_image

    candidate = tmp_path / "assets/characters/林默/identities/值班员/versions/v1.png"
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"candidate")
    portrait = tmp_path / "assets/characters/林默/portrait.png"
    portrait.parent.mkdir(parents=True, exist_ok=True)
    portrait.write_bytes(b"portrait")
    canonical = tmp_path / "assets/characters/林默/identities/值班员.png"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_bytes(b"official")

    monkeypatch.setattr(
        ProductionWorkflowStore,
        "_save",
        lambda _self: (_ for _ in ()).throw(OSError("workflow save failed")),
    )
    with pytest.raises(OSError, match="workflow save failed"):
        character_image._register_character_state_candidate(
            ctx=SimpleNamespace(state_dir=tmp_path / "state"),
            output_dir=tmp_path,
            character_name="林默",
            identity_id="i1",
            generation=character_image.CharacterStateGeneration(
                output_path=candidate,
                canonical_path=canonical,
                reference_paths=(portrait,),
                prompt="prompt",
                state_id="i1",
                raw_candidate_path=candidate.with_suffix(".raw.png"),
                face_source=portrait,
                quality_report=IdentitySheetQualityReport(
                    passed=True,
                    checks={"front_face_detected": False, "back_face_visible": False},
                    issues=[],
                    style_family=IdentitySheetStyleFamily.TWO_D,
                ),
            ),
            source_attempt_id="attempt",
            recipe_revision="1",
        )

    assert canonical.read_bytes() == b"official"
    assert not (tmp_path / "state/production_workflow.json").exists()


def test_concurrent_candidate_registration_keeps_both_versions(tmp_path: Path) -> None:
    from novelvideo.character_visual.identity_sheet import (
        IdentitySheetQualityReport,
        IdentitySheetStyleFamily,
    )
    from novelvideo.production_workflow import ProductionWorkflowStore
    from novelvideo.task_backend.runners import character_image

    root = tmp_path
    portrait = root / "assets/characters/林默/portrait.png"
    portrait.parent.mkdir(parents=True)
    portrait.write_bytes(b"portrait")
    canonical = root / "assets/characters/林默/identities/值班员.png"
    report = IdentitySheetQualityReport(
        passed=True,
        checks={"front_face_detected": False, "back_face_visible": False},
        issues=[],
        style_family=IdentitySheetStyleFamily.TWO_D,
    )

    def register(version_id: str):
        candidate = root / f"assets/characters/林默/identities/值班员/versions/{version_id}.png"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(version_id.encode())
        return character_image._register_character_state_candidate(
            ctx=SimpleNamespace(state_dir=root / "state"),
            output_dir=root,
            character_name="林默",
            identity_id="i1",
            generation=character_image.CharacterStateGeneration(
                output_path=candidate,
                canonical_path=canonical,
                reference_paths=(portrait,),
                prompt="prompt",
                state_id="i1",
                raw_candidate_path=candidate.with_suffix(".raw.png"),
                face_source=portrait,
                quality_report=report,
            ),
            source_attempt_id=version_id,
            recipe_revision="1",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(register, ["v1", "v2"]))

    slot, versions = ProductionWorkflowStore(root / "state/production_workflow.json").get_slot(
        "character:林默:state:i1"
    )
    assert set(versions) == {"v1", "v2"}
    assert {result["version_id"] for result in results} == {"v1", "v2"}
    assert canonical.read_bytes() == Path(
        versions[slot.current_version_id].asset_path
    ).stem.encode()
