from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import UploadFile

from novelvideo.api.schemas import IdentityCreate
from novelvideo.character_visual.identity_sheet import (
    IDENTITY_SHEET_LAYOUT_VERSION,
    IDENTITY_SHEET_PANEL_LAYOUT,
    IdentitySheetQualityReport,
    IdentitySheetStyleFamily,
)
from novelvideo.models import CharacterIdentity, NovelCharacter


class _CharacterStore:
    def __init__(self, character: NovelCharacter):
        self.character = character
        self.identity_updates: list[tuple[str, str, dict]] = []
        self.added_identities: list[CharacterIdentity] = []

    def get_character(self, name: str):
        if name == self.character.name:
            return self.character
        return None

    async def update_character_identity(self, name: str, identity_id: str, **updates):
        self.identity_updates.append((name, identity_id, updates))
        for identity in self.character.identities:
            if identity.identity_id == identity_id:
                for key, value in updates.items():
                    setattr(identity, key, value)
        return True

    async def add_character_identity(self, name: str, identity: CharacterIdentity):
        self.added_identities.append(identity)
        identities = self.character.identities
        identities.append(identity)
        self.character.identities = identities
        return True


def _patch_character_project(
    monkeypatch: pytest.MonkeyPatch,
    module,
    project_dir: Path,
    store: _CharacterStore,
) -> None:
    async def fake_resolve_character_project(
        project: str, user: dict, *, required_role: str = "editor"
    ):
        return _ctx(project_dir), "admin", "demo", project_dir, str(project_dir), store

    monkeypatch.setattr(module, "_resolve_character_project", fake_resolve_character_project)


def _ctx(project_dir: Path):
    return SimpleNamespace(
        project_id="proj_demo",
        owner_username="admin",
        project_name="demo",
        output_dir=project_dir,
        state_dir=project_dir / "_state",
        runtime_dir=project_dir / "_runtime",
        is_home_node=True,
    )


def _png_upload(filename: str = "upload.png") -> UploadFile:
    from PIL import Image

    payload = io.BytesIO()
    Image.new("RGB", (4, 4), color=(120, 80, 40)).save(payload, format="PNG")
    payload.seek(0)
    return UploadFile(filename=filename, file=payload)


def _write_png(path: Path, color: tuple[int, int, int]) -> bytes:
    from PIL import Image

    payload = io.BytesIO()
    Image.new("RGB", (4, 4), color=color).save(payload, format="PNG")
    data = payload.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _quality_report(*issues: str) -> IdentitySheetQualityReport:
    return IdentitySheetQualityReport(
        passed=not issues,
        checks={issue: True for issue in issues},
        issues=list(issues),
        style_family=IdentitySheetStyleFamily.THREE_D_STYLIZED,
    )


@pytest.mark.asyncio
async def test_delete_identity_costume_removes_file_and_clears_store(
    tmp_path, monkeypatch
):
    from novelvideo.api.routes import characters

    costume_path = tmp_path / "assets" / "characters" / "秦" / "identities" / "少年_costume.png"
    costume_path.parent.mkdir(parents=True)
    costume_path.write_bytes(b"costume")
    identity = CharacterIdentity(
        identity_id="秦_少年",
        character_name="秦",
        identity_name="少年",
        costume_image=str(costume_path),
    )
    character = NovelCharacter(name="秦")
    character.identities = [identity]
    store = _CharacterStore(character)
    _patch_character_project(monkeypatch, characters, tmp_path, store)

    response = await characters.delete_identity_costume(
        project="demo",
        name="秦",
        identity_id="秦_少年",
        user={"username": "admin"},
    )

    assert response == {"ok": True, "data": {"deleted": True}}
    assert not costume_path.exists()
    assert store.identity_updates == [("秦", "秦_少年", {"costume_image": ""})]


@pytest.mark.asyncio
async def test_delete_identity_costume_is_idempotent_when_file_missing(
    tmp_path, monkeypatch
):
    from novelvideo.api.routes import characters

    identity = CharacterIdentity(
        identity_id="秦_少年",
        character_name="秦",
        identity_name="少年",
        costume_image="",
    )
    character = NovelCharacter(name="秦")
    character.identities = [identity]
    store = _CharacterStore(character)
    _patch_character_project(monkeypatch, characters, tmp_path, store)

    response = await characters.delete_identity_costume(
        project="demo",
        name="秦",
        identity_id="秦_少年",
        user={"username": "admin"},
    )

    assert response == {"ok": True, "data": {"deleted": False}}
    assert store.identity_updates == [("秦", "秦_少年", {"costume_image": ""})]


@pytest.mark.asyncio
async def test_add_identity_persists_age_group(tmp_path, monkeypatch):
    from novelvideo.api.routes import characters

    character = NovelCharacter(name="秦")
    store = _CharacterStore(character)
    _patch_character_project(monkeypatch, characters, tmp_path, store)

    response = await characters.add_identity(
        project="demo",
        name="秦",
        body=IdentityCreate(
            identity_name="幼年",
            age_group="child",
            appearance_details="粗布短衫",
        ),
        user={"username": "admin"},
    )

    assert response["ok"] is True
    assert response["data"]["age_group"] == "child"
    assert store.added_identities[0].age_group == "child"


@pytest.mark.asyncio
async def test_generate_identity_image_qc_failure_preserves_canonical_and_candidate(
    tmp_path, monkeypatch
):
    from novelvideo.api.routes import characters
    from novelvideo.generators import image_generator

    identity = CharacterIdentity(
        identity_id="秦_少年",
        character_name="秦",
        identity_name="少年",
        appearance_details="青色长衫",
    )
    character = NovelCharacter(name="秦", age_group="youth")
    character.identities = [identity]
    store = _CharacterStore(character)
    _patch_character_project(monkeypatch, characters, tmp_path, store)
    portrait = tmp_path / "assets/characters/秦/portrait.png"
    _write_png(portrait, (80, 90, 100))
    canonical = tmp_path / "assets/characters/秦/identities/少年.png"
    old_bytes = _write_png(canonical, (10, 20, 30))

    async def fake_generate(**kwargs):
        _write_png(Path(kwargs["output_path"]), (200, 180, 160))
        _write_png(Path(kwargs["output_path"]).with_name(f'{Path(kwargs["output_path"]).stem}_body_temp.png'), (1, 2, 3))
        return {"success": True, "layout_version": IDENTITY_SHEET_LAYOUT_VERSION}

    async def fail_qc(**_kwargs):
        return _quality_report("front_face_detected", "state_inconsistent")

    monkeypatch.setattr(image_generator, "generate_identity_image_unified", fake_generate)
    monkeypatch.setattr(characters, "assess_identity_sheet_quality", fail_qc)
    monkeypatch.setattr(characters, "load_project_config", lambda *_: {"visual_style": "3d"})
    monkeypatch.setattr(characters, "_resolve_character_image_model", lambda *_: "nanobanana")

    response = await characters.generate_identity_image(
        project="demo",
        name="秦",
        identity_id="秦_少年",
        user={"username": "admin"},
    )

    assert response["ok"] is False
    assert response["issues"] == ["front_face_detected", "state_inconsistent"]
    assert response["layout_version"] == IDENTITY_SHEET_LAYOUT_VERSION
    assert response["qc_passed"] is False
    assert response["quality_report"]["passed"] is False
    assert canonical.read_bytes() == old_bytes
    candidates = list((canonical.parent / "少年" / "versions").glob("*.png"))
    assert len(candidates) == 2
    from novelvideo.production_workflow import AdoptionStatus, ProductionWorkflowStore

    workflow = ProductionWorkflowStore(tmp_path / "_state/production_workflow.json")
    slot, versions = workflow.get_slot("character:秦:state:秦_少年")
    version = versions[slot.version_ids[-1]]
    assert slot.current_version_id is None
    assert version.adoption_status is AdoptionStatus.CANDIDATE
    assert version.qc_passed is False
    assert version.soft_issues == ["front_face_detected", "state_inconsistent"]
    metadata = version.generation_metadata
    assert metadata["layout_version"] == IDENTITY_SHEET_LAYOUT_VERSION
    assert metadata["panel_layout"] == list(IDENTITY_SHEET_PANEL_LAYOUT)
    assert metadata["face_source"] == "assets/characters/秦/portrait.png"
    assert metadata["quality_report"]["passed"] is False
    assert metadata["raw_candidate_path"].endswith("_body_temp.png")
    assert metadata["canonical_path"] == "assets/characters/秦/identities/少年.png"


@pytest.mark.asyncio
async def test_generate_identity_image_qc_pass_atomically_promotes_and_keeps_attempt(
    tmp_path, monkeypatch
):
    from novelvideo.api.routes import characters
    from novelvideo.generators import image_generator

    identity = CharacterIdentity(
        identity_id="秦_少年",
        character_name="秦",
        identity_name="少年",
        appearance_details="青色长衫",
    )
    character = NovelCharacter(name="秦", age_group="youth")
    character.identities = [identity]
    store = _CharacterStore(character)
    _patch_character_project(monkeypatch, characters, tmp_path, store)
    _write_png(tmp_path / "assets/characters/秦/portrait.png", (80, 90, 100))
    canonical = tmp_path / "assets/characters/秦/identities/少年.png"
    old_bytes = _write_png(canonical, (10, 20, 30))
    generated_bytes = b""
    qc_project_dir = None

    async def fake_generate(**kwargs):
        nonlocal generated_bytes
        generated_bytes = _write_png(Path(kwargs["output_path"]), (200, 180, 160))
        _write_png(Path(kwargs["output_path"]).with_name(f'{Path(kwargs["output_path"]).stem}_body_temp.png'), (1, 2, 3))
        return {"success": True, "layout_version": IDENTITY_SHEET_LAYOUT_VERSION}

    async def pass_qc(**kwargs):
        nonlocal qc_project_dir
        qc_project_dir = kwargs.get("project_dir")
        return _quality_report()

    monkeypatch.setattr(image_generator, "generate_identity_image_unified", fake_generate)
    monkeypatch.setattr(characters, "assess_identity_sheet_quality", pass_qc)
    monkeypatch.setattr(characters, "load_project_config", lambda *_: {"visual_style": "3d"})
    monkeypatch.setattr(characters, "_resolve_character_image_model", lambda *_: "nanobanana")

    response = await characters.generate_identity_image(
        project="demo",
        name="秦",
        identity_id="秦_少年",
        user={"username": "admin"},
    )

    assert response["ok"] is True
    assert response["data"]["layout_version"] == IDENTITY_SHEET_LAYOUT_VERSION
    assert response["data"]["qc_passed"] is True
    assert response["data"]["quality_report"]["passed"] is True
    assert qc_project_dir == str(tmp_path)
    assert canonical.read_bytes() == generated_bytes
    assert list(canonical.parent.glob("少年_*.png"))[0].read_bytes() == old_bytes
    candidates = list((canonical.parent / "少年" / "versions").glob("*.png"))
    assert len(candidates) == 2
    from novelvideo.production_workflow import AdoptionStatus, ProductionWorkflowStore

    workflow = ProductionWorkflowStore(tmp_path / "_state/production_workflow.json")
    slot, versions = workflow.get_slot("character:秦:state:秦_少年")
    version = versions[slot.current_version_id]
    assert version.adoption_status is AdoptionStatus.PROVISIONAL
    assert version.asset_path.endswith(".png")
    assert version.generation_metadata["quality_report"]["passed"] is True
    assert response["data"]["version_id"] == version.version_id
    assert response["data"]["adoption_status"] == "provisional"


@pytest.mark.asyncio
async def test_generate_identity_image_qc_exception_preserves_canonical(tmp_path, monkeypatch):
    from novelvideo.api.routes import characters
    from novelvideo.generators import image_generator

    identity = CharacterIdentity(
        identity_id="秦_少年",
        character_name="秦",
        identity_name="少年",
        appearance_details="青色长衫",
    )
    character = NovelCharacter(name="秦", age_group="youth")
    character.identities = [identity]
    store = _CharacterStore(character)
    _patch_character_project(monkeypatch, characters, tmp_path, store)
    _write_png(tmp_path / "assets/characters/秦/portrait.png", (80, 90, 100))
    canonical = tmp_path / "assets/characters/秦/identities/少年.png"
    old_bytes = _write_png(canonical, (10, 20, 30))

    async def fake_generate(**kwargs):
        _write_png(Path(kwargs["output_path"]), (200, 180, 160))
        return {"success": True}

    async def unavailable_qc(**_kwargs):
        raise TimeoutError("vision timeout")

    monkeypatch.setattr(image_generator, "generate_identity_image_unified", fake_generate)
    monkeypatch.setattr(characters, "assess_identity_sheet_quality", unavailable_qc)
    monkeypatch.setattr(characters, "load_project_config", lambda *_: {"visual_style": "3d"})
    monkeypatch.setattr(characters, "_resolve_character_image_model", lambda *_: "nanobanana")

    response = await characters.generate_identity_image(
        project="demo",
        name="秦",
        identity_id="秦_少年",
        user={"username": "admin"},
    )

    assert response["ok"] is False
    assert response["issues"] == ["qc_unavailable"]
    assert response["quality_report"]["passed"] is False
    assert canonical.read_bytes() == old_bytes


@pytest.mark.asyncio
async def test_generate_age_identity_requires_identity_portrait_before_transport(
    tmp_path, monkeypatch
):
    from novelvideo.api.routes import characters
    from novelvideo.generators import image_generator

    identity = CharacterIdentity(
        identity_id="秦_幼年",
        character_name="秦",
        identity_name="幼年",
        age_group="child",
        appearance_details="粗布短衫",
    )
    character = NovelCharacter(name="秦", age_group="youth")
    character.identities = [identity]
    store = _CharacterStore(character)
    _patch_character_project(monkeypatch, characters, tmp_path, store)
    called = False

    async def fake_generate(**_kwargs):
        nonlocal called
        called = True
        return {"success": True}

    monkeypatch.setattr(image_generator, "generate_identity_image_unified", fake_generate)

    response = await characters.generate_identity_image(
        project="demo",
        name="秦",
        identity_id="秦_幼年",
        user={"username": "admin"},
    )

    assert response == {
        "ok": False,
        "error": "年龄变体必须先生成或上传 Identity Portrait",
    }
    assert called is False


@pytest.mark.asyncio
async def test_generate_identity_unified_keeps_bool_default_and_opt_in_structured(
    tmp_path, monkeypatch
):
    from novelvideo.generators import image_generator
    from novelvideo.generators import nanobanana_character

    class FakeGenerator:
        def __init__(self, *args, **kwargs):
            pass

        async def generate_identity_with_reference(self, **_kwargs):
            return SimpleNamespace(success=True, error=None)

    monkeypatch.setattr(nanobanana_character, "NanoBananaCharacterGenerator", FakeGenerator)
    kwargs = {
        "character_name": "秦",
        "identity_prompt": "青色长衫",
        "reference_image_path": str(tmp_path / "portrait.png"),
        "output_path": str(tmp_path / "candidate.png"),
        "model": "nanobanana",
    }

    legacy = await image_generator.generate_identity_image_unified(**kwargs)
    structured = await image_generator.generate_identity_image_unified(
        **kwargs, structured=True
    )

    assert legacy is True
    assert structured == {
        "success": True,
        "error": None,
        "image_path": str(tmp_path / "candidate.png"),
        "layout_version": IDENTITY_SHEET_LAYOUT_VERSION,
    }


@pytest.mark.asyncio
async def test_upload_identity_costume_returns_project_context_url(tmp_path, monkeypatch):
    from novelvideo.api.routes import characters

    identity = CharacterIdentity(
        identity_id="秦_少年",
        character_name="秦",
        identity_name="少年",
    )
    character = NovelCharacter(name="秦")
    character.identities = [identity]
    store = _CharacterStore(character)

    async def fake_resolve_character_project(
        project: str, user: dict, *, required_role: str = "editor"
    ):
        return _ctx(tmp_path), "admin", "demo", tmp_path, str(tmp_path), store

    monkeypatch.setattr(characters, "_resolve_character_project", fake_resolve_character_project)
    monkeypatch.setattr(
        characters,
        "make_static_url_for_context",
        lambda ctx, rel, local_path=None: f"/static/projects/{ctx.project_id}/{rel}",
    )

    response = await characters.upload_identity_costume(
        project="demo",
        name="秦",
        identity_id="秦_少年",
        file=_png_upload(),
        user={"username": "admin"},
    )

    assert response["ok"] is True
    assert response["data"]["costume_image_url"] == (
        "/static/projects/proj_demo/assets/characters/秦/identities/少年_costume.png"
    )
    assert store.identity_updates == [
        (
            "秦",
            "秦_少年",
            {"costume_image": str(tmp_path / "assets/characters/秦/identities/少年_costume.png")},
        )
    ]


@pytest.mark.asyncio
async def test_upload_identity_portrait_returns_project_context_url(tmp_path, monkeypatch):
    from novelvideo.api.routes import characters

    identity = CharacterIdentity(
        identity_id="秦_少年",
        character_name="秦",
        identity_name="少年",
    )
    character = NovelCharacter(name="秦")
    character.identities = [identity]
    store = _CharacterStore(character)

    async def fake_resolve_character_project(
        project: str, user: dict, *, required_role: str = "editor"
    ):
        return _ctx(tmp_path), "admin", "demo", tmp_path, str(tmp_path), store

    monkeypatch.setattr(characters, "_resolve_character_project", fake_resolve_character_project)
    monkeypatch.setattr(
        characters,
        "make_static_url_for_context",
        lambda ctx, rel, local_path=None: f"/static/projects/{ctx.project_id}/{rel}",
    )

    response = await characters.upload_identity_portrait(
        project="demo",
        name="秦",
        identity_id="秦_少年",
        file=_png_upload(),
        user={"username": "admin"},
    )

    assert response["ok"] is True
    assert response["data"]["portrait_image_url"] == (
        "/static/projects/proj_demo/assets/characters/秦/identities/秦_少年_portrait.png"
    )
    assert store.identity_updates == [
        (
            "秦",
            "秦_少年",
            {
                "portrait_image": str(
                    tmp_path / "assets/characters/秦/identities/秦_少年_portrait.png"
                )
            },
        )
    ]


@pytest.mark.asyncio
async def test_upload_identity_image_backs_up_existing_file(tmp_path, monkeypatch):
    from novelvideo.api.routes import characters

    identity = CharacterIdentity(
        identity_id="秦_少年",
        character_name="秦",
        identity_name="少年",
    )
    character = NovelCharacter(name="秦")
    character.identities = [identity]
    store = _CharacterStore(character)
    _patch_character_project(monkeypatch, characters, tmp_path, store)

    target = tmp_path / "assets" / "characters" / "秦" / "identities" / "少年.png"
    old_bytes = _write_png(target, (10, 20, 30))

    response = await characters.upload_identity_image(
        project="demo",
        name="秦",
        identity_name="少年",
        file=_png_upload(),
        user={"username": "admin"},
    )

    assert response["ok"] is True
    backups = list(target.parent.glob("少年_*.png"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == old_bytes
    assert target.read_bytes() != old_bytes


@pytest.mark.asyncio
async def test_character_asset_history_lists_backups(tmp_path, monkeypatch):
    from novelvideo.api.routes import characters

    identity = CharacterIdentity(
        identity_id="秦_少年",
        character_name="秦",
        identity_name="少年",
    )
    character = NovelCharacter(name="秦")
    character.identities = [identity]
    store = _CharacterStore(character)
    _patch_character_project(monkeypatch, characters, tmp_path, store)

    target = tmp_path / "assets" / "characters" / "秦" / "identities" / "少年.png"
    _write_png(target, (10, 20, 30))
    backup = target.parent / "少年_20260603112233.png"
    backup.write_bytes(target.read_bytes())

    response = await characters.list_character_asset_history(
        project="demo",
        name="秦",
        kind="identity",
        identity_id="秦_少年",
        user={"username": "admin"},
    )

    assert response["ok"] is True
    assert response["data"]["current_url"]
    assert response["data"]["entries"][0]["history_id"] == backup.name
    assert response["data"]["entries"][0]["url"]


@pytest.mark.asyncio
async def test_restore_character_asset_history_backs_up_current_and_restores_backup(
    tmp_path, monkeypatch
):
    from novelvideo.api.routes import characters

    identity = CharacterIdentity(
        identity_id="秦_少年",
        character_name="秦",
        identity_name="少年",
    )
    character = NovelCharacter(name="秦")
    character.identities = [identity]
    store = _CharacterStore(character)
    _patch_character_project(monkeypatch, characters, tmp_path, store)

    target = tmp_path / "assets" / "characters" / "秦" / "identities" / "少年.png"
    current_bytes = _write_png(target, (200, 20, 30))
    backup = target.parent / "少年_20260603112233.png"
    old_bytes = _write_png(backup, (10, 20, 30))

    response = await characters.restore_character_asset_history(
        project="demo",
        name="秦",
        body=SimpleNamespace(
            kind="identity",
            identity_id="秦_少年",
            history_id=backup.name,
        ),
        user={"username": "admin"},
    )

    assert response["ok"] is True
    assert target.read_bytes() == old_bytes
    new_backups = [
        path for path in target.parent.glob("少年_*.png") if path.name != "少年_20260603112233.png"
    ]
    assert len(new_backups) == 1
    assert new_backups[0].read_bytes() == current_bytes
