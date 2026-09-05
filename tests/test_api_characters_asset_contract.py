from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.models import CharacterIdentity, NovelCharacter

pytestmark = pytest.mark.m04


class _CharacterStore:
    def __init__(self, characters: list[NovelCharacter] | None = None):
        self.characters = {character.name: character for character in characters or []}

    def get_all_characters(self):
        return list(self.characters.values())

    def get_character(self, name: str):
        return self.characters.get(name)

    async def add_character(self, character: NovelCharacter):
        self.characters[character.name] = character

    async def update_character(self, name: str, **updates):
        character = self.characters[name]
        for key, value in updates.items():
            setattr(character, key, value)

    async def set_character_extraction_locked(self, name: str, locked: bool):
        character = self.characters[name]
        changed = character.extraction_locked != locked
        character.extraction_locked = locked
        return changed

    async def rename_character(self, old_name: str, new_name: str):
        character = self.characters.pop(old_name)
        character.name = new_name
        for identity in character.identities:
            identity.character_name = new_name
            identity.identity_id = f"{new_name}_{identity.identity_name}"
        self.characters[new_name] = character

    async def delete_character(self, name: str):
        self.characters.pop(name, None)


def _client(monkeypatch, tmp_path, store: _CharacterStore):
    from novelvideo.api.routes import characters

    project_dir = tmp_path / "output" / "admin" / "demo"
    project_dir.mkdir(parents=True)

    async def fake_resolve_project(project: str, user: dict, *, required_role: str = "editor"):
        return (
            SimpleNamespace(project_id="proj_demo", output_dir=project_dir, is_home_node=True),
            "admin",
            "demo",
            project_dir,
            str(project_dir),
            store,
        )

    monkeypatch.setattr(characters, "_resolve_character_project", fake_resolve_project)
    monkeypatch.setattr(
        characters,
        "make_static_url_for_context",
        lambda ctx, rel, local_path=None: f"/static/projects/{ctx.project_id}/{rel}",
    )

    app = FastAPI()
    app.include_router(characters.router)
    app.dependency_overrides[characters.get_api_user] = lambda: {"username": "admin"}
    return TestClient(app)


def test_create_character_accepts_react_extra_payload(monkeypatch, tmp_path):
    store = _CharacterStore()
    client = _client(monkeypatch, tmp_path, store)

    response = client.post(
        "/projects/demo/characters",
        json={
            "name": "秦昭",
            "role": "主角",
            "is_main": True,
            "gender": "男",
            "age_group": "middle",
            "description": "冷静的捕快",
            "face_prompt": "sharp eyes, stern face",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "data": {
            "name": "秦昭",
            "role": "主角",
            "is_main": True,
            "gender": "男",
            "age_group": "middle",
            "description": "冷静的捕快",
            "face_prompt": "sharp eyes, stern face",
        },
    }
    saved = store.get_character("秦昭")
    assert saved is not None
    assert saved.is_main is True
    assert saved.gender == "男"
    assert saved.age_group == "middle"
    assert saved.description == "冷静的捕快"
    assert saved.face_prompt == "sharp eyes, stern face"


def test_create_main_character_unsets_previous_main(monkeypatch, tmp_path):
    store = _CharacterStore(
        [
            NovelCharacter(name="旧主角", role="主角", is_main=True),
            NovelCharacter(name="配角", role="配角", is_main=False),
        ]
    )
    client = _client(monkeypatch, tmp_path, store)

    response = client.post(
        "/projects/demo/characters",
        json={"name": "新主角", "role": "主角", "is_main": True},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert store.get_character("旧主角").is_main is False
    assert store.get_character("新主角").is_main is True


def test_update_main_character_unsets_previous_main(monkeypatch, tmp_path):
    store = _CharacterStore(
        [
            NovelCharacter(name="秦昭", role="主角", is_main=True),
            NovelCharacter(name="沈青", role="配角", is_main=False),
        ]
    )
    client = _client(monkeypatch, tmp_path, store)

    response = client.patch("/projects/demo/characters/沈青", json={"is_main": True})

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "data": {"name": "沈青", "updated_fields": ["is_main"]},
    }
    assert store.get_character("秦昭").is_main is False
    assert store.get_character("沈青").is_main is True


def test_list_characters_repairs_duplicate_narrator_main(monkeypatch, tmp_path):
    store = _CharacterStore(
        [
            NovelCharacter(name="陆辰", role="主角", is_main=True),
            NovelCharacter(name="沈月白", role="女主", is_main=True),
            NovelCharacter(name="赵广年", role="配角", is_main=False),
        ]
    )
    client = _client(monkeypatch, tmp_path, store)

    response = client.get("/projects/demo/characters")

    assert response.status_code == 200
    mains = [item["name"] for item in response.json()["data"] if item["is_main"]]
    assert mains == ["陆辰"]
    assert store.get_character("陆辰").is_main is True
    assert store.get_character("沈月白").is_main is False


def test_extraction_lock_patch_is_idempotent_and_list_exposes_state(monkeypatch, tmp_path):
    store = _CharacterStore([NovelCharacter(name="林昭", extraction_locked=False)])
    client = _client(monkeypatch, tmp_path, store)

    first = client.patch(
        "/projects/demo/characters/林昭/extraction-lock",
        json={"extraction_locked": True},
    )
    repeated = client.patch(
        "/projects/demo/characters/林昭/extraction-lock",
        json={"extraction_locked": True},
    )
    listed = client.get("/projects/demo/characters")

    expected = {
        "ok": True,
        "data": {"name": "林昭", "extraction_locked": True},
    }
    assert first.status_code == 200
    assert first.json() == expected
    assert repeated.status_code == 200
    assert repeated.json() == expected
    assert store.get_character("林昭").extraction_locked is True
    assert listed.json()["data"][0]["extraction_locked"] is True


def test_selecting_visual_proposal_builds_draft_bible_that_can_be_confirmed(
    monkeypatch, tmp_path
):
    store = _CharacterStore([NovelCharacter(name="林昭")])
    client = _client(monkeypatch, tmp_path, store)
    proposal = {
        "proposal_id": "proposal-1",
        "title": "冷峻捕快",
        "rationale": "突出克制与行动力",
        "recommended": True,
        "face_shape": "窄长脸",
        "facial_features": ["深眼窝", "薄唇"],
        "hair_style": "利落高马尾",
        "body_type": "清瘦挺拔",
        "distinctive_features": ["左眉断痕"],
        "identity_anchors": ["窄长脸", "左眉断痕", "薄唇"],
        "asymmetry_detail": "左眉略低",
        "quality_issues": [],
        "outfit_states": {"default": "深色捕快服"},
    }
    seeded = client.patch(
        "/projects/demo/characters/林昭/visual-workspace",
        json={"design_proposals": [proposal]},
    )
    assert seeded.status_code == 200

    selected = client.patch(
        "/projects/demo/characters/林昭/visual-workspace",
        json={"selected_proposal_id": "proposal-1"},
    )

    assert selected.status_code == 200
    data = selected.json()["data"]
    assert data["selected_proposal_id"] == "proposal-1"
    assert data["visual_bible"] == {
        "character_id": "林昭",
        "revision_id": "proposal:proposal-1",
        "status": "draft",
        "face_shape": "窄长脸",
        "facial_features": ["深眼窝", "薄唇"],
        "hair_style": "利落高马尾",
        "body_type": "清瘦挺拔",
        "distinctive_features": ["左眉断痕"],
        "outfit_states": {"default": "深色捕快服"},
        "identity_anchors": ["窄长脸", "左眉断痕", "薄唇"],
        "source_fact_ids": [],
        "confirmed_by": None,
    }

    confirmed = client.post(
        "/projects/demo/characters/林昭/visual-workspace/confirm",
        json={"confirmed_by": "director"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["data"]["visual_bible"]["status"] == "confirmed"
    assert confirmed.json()["data"]["visual_bible"]["confirmed_by"] == "director"


def test_incomplete_visual_bible_cannot_be_confirmed(monkeypatch, tmp_path):
    store = _CharacterStore([NovelCharacter(name="林昭")])
    client = _client(monkeypatch, tmp_path, store)
    seeded = client.patch(
        "/projects/demo/characters/林昭/visual-workspace",
        json={
            "visual_bible": {
                "character_id": "林昭",
                "revision_id": "empty-draft",
                "status": "draft",
            }
        },
    )
    assert seeded.status_code == 200

    response = client.post(
        "/projects/demo/characters/林昭/visual-workspace/confirm",
        json={"confirmed_by": "director"},
    )
    assert response.status_code == 409
    assert response.json()["error_code"] == "CHARACTER_VISUAL_BIBLE_INCOMPLETE"


def test_character_and_identity_lists_expose_asset_history_links(monkeypatch, tmp_path):
    character = NovelCharacter(name="林昭", role="主角")
    character.identities = [
        CharacterIdentity(
            identity_id="林昭_青年",
            character_name="林昭",
            identity_name="青年",
        )
    ]
    store = _CharacterStore([character])
    client = _client(monkeypatch, tmp_path, store)

    characters_response = client.get("/projects/demo/characters")
    identities_response = client.get("/projects/demo/characters/林昭/identities")

    assert characters_response.status_code == 200
    char_item = characters_response.json()["data"][0]
    assert char_item["history_url"] == (
        "/api/v1/projects/proj_demo/characters/%E6%9E%97%E6%98%AD/asset-history?kind=portrait"
    )
    assert char_item["restore_url"] == (
        "/api/v1/projects/proj_demo/characters/%E6%9E%97%E6%98%AD/asset-history/restore"
    )

    assert identities_response.status_code == 200
    identity_item = identities_response.json()["data"][0]
    assert identity_item["history_url"] == (
        "/api/v1/projects/proj_demo/characters/%E6%9E%97%E6%98%AD/"
        "asset-history?kind=identity&identity_id=%E6%9E%97%E6%98%AD_%E9%9D%92%E5%B9%B4"
    )
    assert identity_item["restore_url"] == (
        "/api/v1/projects/proj_demo/characters/%E6%9E%97%E6%98%AD/asset-history/restore"
    )


def test_update_character_can_rename_like_nicegui(monkeypatch, tmp_path):
    store = _CharacterStore([NovelCharacter(name="秦昭", role="主角")])
    client = _client(monkeypatch, tmp_path, store)

    response = client.patch(
        "/projects/demo/characters/秦昭",
        json={"name": "秦照", "face_prompt": "calm eyes"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "data": {
            "name": "秦照",
            "updated_fields": ["name", "face_prompt"],
            "renamed_from": "秦昭",
        },
    }
    assert store.get_character("秦昭") is None
    renamed = store.get_character("秦照")
    assert renamed is not None
    assert renamed.face_prompt == "calm eyes"


def test_delete_character_route_removes_character(monkeypatch, tmp_path):
    store = _CharacterStore([NovelCharacter(name="秦昭", role="主角")])
    client = _client(monkeypatch, tmp_path, store)

    response = client.post("/projects/demo/characters/秦昭/delete")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "data": {"name": "秦昭", "deleted": True},
    }
    assert store.get_character("秦昭") is None
