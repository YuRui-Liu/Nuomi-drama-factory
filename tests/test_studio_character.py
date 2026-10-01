from novelvideo.creative_studios.character import portable_character
import pytest


def test_personal_media_survives_source_removal_and_copies_independently(tmp_path):
    from PIL import Image
    from novelvideo.creative_studios.character import capture_character_media, restore_character_media
    source, library, target = (tmp_path / part for part in ("source", "library", "target"))
    portrait = source / "assets/characters/hero/portrait.png"
    portrait.parent.mkdir(parents=True)
    Image.new("RGB", (2, 2), "red").save(portrait)
    media = capture_character_media(source, {"name": "hero", "identities": []}, library, "card")
    portrait.unlink()
    paths = restore_character_media(library, "card", media, target, "copy", [])
    copied = target / "assets/characters/copy/portrait.png"
    assert copied.is_file() and paths[0] == copied
    copied.write_bytes(b"changed")
    assert (library / "media/card/0.png").read_bytes() != b"changed"


def test_personal_media_missing_or_outside_source_is_rejected(tmp_path):
    from novelvideo.creative_studios.character import capture_character_media
    with pytest.raises(ValueError, match="已采纳角色图像不存在"):
        capture_character_media(tmp_path / "source", {"name": "hero"}, tmp_path / "library", "card")
    external = tmp_path / "secret.png"
    external.write_bytes(b"secret")
    portrait = tmp_path / "source/assets/characters/hero/portrait.png"
    portrait.parent.mkdir(parents=True)
    portrait.symlink_to(external)
    with pytest.raises(ValueError, match="项目目录"):
        capture_character_media(tmp_path / "source", {"name": "hero"}, tmp_path / "library", "card")


@pytest.mark.asyncio
async def test_personal_card_routes_copy_adopted_portraits_across_projects(tmp_path, monkeypatch):
    from PIL import Image
    from types import SimpleNamespace
    from novelvideo.models import NovelCharacter, CharacterIdentity
    from novelvideo.api.routes import studio_character as routes
    from fastapi import HTTPException
    source, target = tmp_path / "source", tmp_path / "target"
    portrait = source / "assets/characters/hero/portrait.png"
    portrait.parent.mkdir(parents=True)
    Image.new("RGB", (2, 2), "blue").save(portrait)
    identity_image = source / "assets/characters/hero/identities/礼服.png"
    identity_image.parent.mkdir()
    Image.new("RGB", (2, 2), "red").save(identity_image)
    character = NovelCharacter(name="hero")
    character.identities = [CharacterIdentity(identity_id="hero_dress", character_name="hero", identity_name="礼服")]
    created = {}
    async def add(items, skip_existing):
        created.update({item.name: item for item in items})
        return [item.name for item in items]
    async def resolve(project, user, required_role):
        if project == "source":
            return SimpleNamespace(project_id="source"), None, None, source, None, SimpleNamespace(get_character=lambda name: character)
        return None, None, None, target, None, SimpleNamespace(get_character=created.get, add_characters_atomic=add)
    monkeypatch.setattr(routes, "_resolve_character_project", resolve)
    monkeypatch.setattr(routes, "get_user_base_dir", lambda user: tmp_path / user)
    saved = await routes.save_personal_card("source", "hero", {"username": "owner"})
    card_id = saved["data"]["id"]
    portrait.unlink()
    copied = await routes.copy_personal_card("target", card_id, routes.CardCopy(name="copy"), {"username": "owner"})
    assert copied["data"]["media_copied"] is True
    assert (target / "assets/characters/copy/portrait.png").is_file()
    assert (target / "assets/characters/copy/identities/礼服.png").is_file()
    assert created["copy"].identities[0].identity_id != "hero_dress"
    assert copied["data"]["voice_included"] is False
    library_image = tmp_path / "owner/.creative-studios/media" / card_id / "0.png"
    library_image.unlink()
    with pytest.raises(HTTPException) as error:
        await routes.copy_personal_card("target", card_id, routes.CardCopy(name="missing"), {"username": "owner"})
    assert error.value.status_code == 409
    assert "missing" not in created


def test_refresh_media_requires_task_submit_scope():
    import inspect
    import asyncio
    from fastapi import HTTPException
    from novelvideo.api.routes.studio_character import refresh_character_media
    dependency = inspect.signature(refresh_character_media).parameters["user"].default.dependency
    with pytest.raises(HTTPException) as error:
        result = dependency({"username": "viewer", "scopes": ["projects:write"], "auth_type": "api_key"})
        if inspect.isawaitable(result):
            asyncio.run(result)
    assert error.value.status_code == 403


def test_portable_card_keeps_design_but_not_project_paths():
    source = {"name": "原名", "face_prompt": "面部", "description": "事实", "is_main": True, "reference_audio_path": "private.wav", "identities": [{"identity_name": "礼服", "appearance_details": "红色礼服", "costume_image": "/private/costume.png"}]}
    card = portable_character(source)
    assert card["face_prompt"] == "面部"
    assert card["identities"] == [{"identity_name": "礼服", "appearance_details": "红色礼服", "face_prompt": "", "age_group": "", "body_type": ""}]
    assert "reference_audio_path" not in card
    assert card["is_main"] is False


def test_reference_matching_is_structural_and_exact():
    from novelvideo.creative_studios.character import references_character
    assert references_character({"asset_requirements": [{"kind": "character_identity", "entity_key": "小雨"}]}, {"小雨"})
    assert references_character({"data": {"referenceTarget": {"identity_id": "小雨_雨衣"}}}, {"小雨_雨衣"})
    assert not references_character({"prompt": "小雨的场景", "entity_key": "小雨天"}, {"小雨"})


@pytest.mark.asyncio
async def test_impact_reports_unplanned_episode_without_fabricated_assets(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from novelvideo.api.routes import studio_character
    class Store:
        def get_character(self, name):
            return SimpleNamespace(name=name, identities=[])
        async def list_episodes(self):
            return [SimpleNamespace(number=1, character_names=["小雨"])]
    async def resolve(*args, **kwargs):
        return None, "owner", "project", tmp_path, str(tmp_path), Store()
    monkeypatch.setattr(studio_character, "_resolve_character_project", resolve)
    result = await studio_character.character_impact("project", "小雨", {"username": "owner"})
    assert result["data"]["groups"] == []
    assert "无活动导演方案" in result["data"]["warnings"][0]


@pytest.mark.asyncio
async def test_refresh_rejects_video_before_successful_render(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from novelvideo.api.routes import studio_character
    from novelvideo.narrative_groups import service
    async def resolve(*args, **kwargs):
        return SimpleNamespace(state_dir=tmp_path), "owner", "p", tmp_path, str(tmp_path), None
    async def impact(*args, **kwargs):
        return {"data": {"groups": [{"episode": 1, "group_id": "g"}]}}
    monkeypatch.setattr(studio_character, "_resolve_character_project", resolve)
    monkeypatch.setattr(studio_character, "character_impact", impact)
    monkeypatch.setattr(service, "load_groups", lambda *args: [SimpleNamespace(id="g", stages={"render": SimpleNamespace(status="failed", revision=3, needs_regeneration=True)})])
    with pytest.raises(HTTPException, match="上游分镜") as error:
        await studio_character.refresh_character_media("p", "角色", studio_character.RefreshCharacterMedia(episode=1, group_id="g", stage="video", expected_render_revision=3, idempotency_key="once"), {"username": "owner"})
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_refresh_uses_video_revision_and_saved_workflow(tmp_path, monkeypatch):
    from types import SimpleNamespace as S
    from novelvideo.api.routes import studio_character, narrative_groups
    from novelvideo.narrative_groups import service
    async def resolve(*args, **kwargs):
        return S(state_dir=tmp_path), "owner", "p", tmp_path, str(tmp_path), None
    async def impact(*args, **kwargs):
        return {"data": {"groups": [{"episode": 1, "group_id": "g"}]}}
    group = S(id="g", stages={"render": S(status="completed", revision=5, needs_regeneration=False), "video": S(status="completed", revision=2)}, video_settings=S(workflow_id="saved-workflow", revision=3), video_plan=S(revision=4), video_reference_settings=S(revision=6))
    async def enqueue(project, episode, group_id, user, request, *args):
        assert request.revision == 2
        assert request.plan_revision == 4
        assert request.model == "saved-workflow"
        return {"ok": True, "task_id": "test-task"}
    monkeypatch.setattr(studio_character, "_resolve_character_project", resolve)
    monkeypatch.setattr(studio_character, "character_impact", impact)
    monkeypatch.setattr(service, "load_groups", lambda *args: [group])
    monkeypatch.setattr(narrative_groups, "_enqueue_group_video", enqueue)
    monkeypatch.setattr("novelvideo.project_config.load_project_config_file_from_state_dir", lambda *args: {"aspect_ratio": "16:9"})
    monkeypatch.setattr("novelvideo.api.deps.get_media_capability_store", lambda: None)
    monkeypatch.setattr("novelvideo.api.deps.get_media_credential_resolver", lambda: None)
    result = await studio_character.refresh_character_media("p", "角色", studio_character.RefreshCharacterMedia(episode=1, group_id="g", stage="video", expected_render_revision=5, idempotency_key="once"), {"username": "owner"})
    assert result["task_id"] == "test-task"


def test_personal_library_is_owned_by_authenticated_user(tmp_path, monkeypatch):
    from novelvideo.api.routes import studio_character
    monkeypatch.setattr(studio_character, "get_user_base_dir", lambda username: tmp_path / username)
    owner = studio_character._personal_store({"username": "owner"})
    owner.save("character", "card", "角色", {"character": {"name": "角色"}}, 0)
    assert studio_character._personal_store({"username": "other"}).get("character", "card") is None


@pytest.mark.asyncio
async def test_costume_binding_publishes_atomically_without_modifying_other_characters(tmp_path, monkeypatch):
    from types import SimpleNamespace as S
    from novelvideo.api.routes import studio_character
    calls = []
    class Store:
        def get_character(self, name):
            return S(identities=[S(identity_id="new"), S(identity_id="old")])
        def get_episode(self, episode):
            return S(number=episode, identity_ids=["old", "other"], identity_default_map={"角色": "old", "其他": "other"})
        async def publish_identity_plan_atomic(self, **kwargs):
            calls.append(kwargs)
    async def resolve(*args, **kwargs):
        return S(project_id="p"), "owner", "p", tmp_path, str(tmp_path), Store()
    monkeypatch.setattr(studio_character, "_resolve_character_project", resolve)
    result = await studio_character.apply_costume("p", "角色", studio_character.ApplyCostume(episode=1, identity_id="new", expected_episode_digest="baseline"), {})
    assert result["ok"] is True
    assert calls[0]["identity_default_map"] == {"角色": "new", "其他": "other"}
    assert calls[0]["episode_identity_ids"] == ["other", "new"]
    assert calls[0]["episode_identity_baseline_digest"] == "baseline"
    assert calls[0]["characters"] == []
