from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.deps import get_media_capability_store, get_media_credential_store
from novelvideo.media_capabilities.models import GRSAI_IMAGE_MODELS, ProviderAccount
from novelvideo.media_capabilities.store import MediaCapabilityStore


class FakeCredentialStore:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, reference: str) -> str | None:
        return self.values.get(reference)


def _client(
    monkeypatch,
    tmp_path,
    *,
    config: dict[str, object] | None = None,
    provider_model: str = "gpt-image-2",
    provider_enabled: bool = True,
    credential_available: bool = True,
    real_project_config: bool = False,
) -> tuple[TestClient, dict[str, object]]:
    from novelvideo.api.routes import characters
    from novelvideo import project_config

    current_config = dict(config or {})
    store = MediaCapabilityStore(tmp_path / "settings.db")
    store.save_provider(
        ProviderAccount(
            id="grsai-main",
            provider_type="grsai",
            base_url="https://grsai.example",
            model=provider_model,
            credential_ref="secret://grsai-main",
            enabled=provider_enabled,
        )
    )
    credentials = FakeCredentialStore(
        {"grsai-main": "test-only-key"} if credential_available else {}
    )

    async def fake_resolve_project(
        project: str,
        user: dict,
        *,
        required_role: str = "editor",
    ):
        assert project == "demo"
        return (
            SimpleNamespace(project_id="proj-demo"),
            "alice",
            "demo",
            tmp_path,
            str(tmp_path),
            object(),
        )

    def fake_update_project_config(username: str, project: str, apply) -> None:
        assert (username, project) == ("alice", "demo")
        apply(current_config)

    monkeypatch.setattr(characters, "_resolve_character_project", fake_resolve_project)
    if real_project_config:
        monkeypatch.setattr(project_config, "OUTPUT_DIR", tmp_path / "project-state")
        project_config.update_project_config_file(
            "alice",
            "demo",
            lambda persisted: persisted.update(current_config),
        )
    else:
        monkeypatch.setattr(
            characters,
            "load_project_config_file",
            lambda username, project: dict(current_config),
        )
        monkeypatch.setattr(
            characters,
            "update_project_config_file",
            fake_update_project_config,
        )

    app = FastAPI()
    app.include_router(characters.router, prefix="/api/v1")
    app.dependency_overrides[characters.get_api_user] = lambda: {
        "username": "alice",
        "role": "owner",
    }
    app.dependency_overrides[get_media_capability_store] = lambda: store
    app.dependency_overrides[get_media_credential_store] = lambda: credentials
    return TestClient(app), current_config


def test_get_uses_dynamic_grsai_model_options_without_legacy_labels(
    monkeypatch, tmp_path
) -> None:
    client, _config = _client(
        monkeypatch,
        tmp_path,
        config={"character_image_selection": "nano-banana-2"},
    )

    response = client.get("/api/v1/projects/demo/image-source-selection/character")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["image_source_selection"] == "nano-banana-2"
    assert data["options"] == {
        model: model
        for model in [
            "gpt-image-2",
            *sorted(GRSAI_IMAGE_MODELS - {"gpt-image-2"}),
        ]
    }
    assert "LingShan" not in response.text
    assert "newapi_" not in response.text


def test_patch_keeps_asset_selections_independent_and_does_not_change_render(
    monkeypatch, tmp_path
) -> None:
    client, config = _client(
        monkeypatch,
        tmp_path,
        config={"render_image_selection": "newapi_nanobanana2"},
    )

    selections = {
        "character": "gpt-image-2-vip",
        "scene": "nano-banana-2",
        "prop": "nano-banana-pro",
    }
    for asset_kind, selection in selections.items():
        response = client.patch(
            f"/api/v1/projects/demo/image-source-selection/{asset_kind}",
            json={"image_source_selection": selection},
        )
        assert response.status_code == 200
        assert response.json()["data"]["image_source_selection"] == selection

    assert config == {
        "render_image_selection": "newapi_nanobanana2",
        "character_image_selection": "gpt-image-2-vip",
        "scene_image_selection": "nano-banana-2",
        "prop_image_selection": "nano-banana-pro",
    }


def test_patch_response_uses_the_selection_written_by_this_request(
    monkeypatch, tmp_path
) -> None:
    from novelvideo.api.routes import characters

    client, config = _client(monkeypatch, tmp_path)

    def save_then_simulate_racing_write(username: str, project: str, apply) -> None:
        apply(config)
        config["scene_image_selection"] = "gpt-image-2"

    monkeypatch.setattr(
        characters,
        "update_project_config_file",
        save_then_simulate_racing_write,
    )

    response = client.patch(
        "/api/v1/projects/demo/image-source-selection/scene",
        json={"image_source_selection": "nano-banana-2"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["image_source_selection"] == "nano-banana-2"


def test_patch_persists_only_target_key_in_real_project_config(
    monkeypatch, tmp_path
) -> None:
    from novelvideo import project_config

    initial = {
        "character_image_selection": "gpt-image-2-vip",
        "scene_image_selection": "gpt-image-2",
        "prop_image_selection": "nano-banana-pro",
        "render_image_selection": "newapi_nanobanana2",
        "unrelated_setting": {"keep": True},
    }
    client, _config = _client(
        monkeypatch,
        tmp_path,
        config=initial,
        real_project_config=True,
    )

    patch_response = client.patch(
        "/api/v1/projects/demo/image-source-selection/scene",
        json={"image_source_selection": "nano-banana-2"},
    )

    assert patch_response.status_code == 200
    assert patch_response.json()["data"]["image_source_selection"] == "nano-banana-2"
    assert project_config.load_project_config_file("alice", "demo") == {
        **initial,
        "scene_image_selection": "nano-banana-2",
    }

    get_response = client.get("/api/v1/projects/demo/image-source-selection/scene")
    assert get_response.status_code == 200
    assert get_response.json()["data"]["image_source_selection"] == "nano-banana-2"


def test_get_falls_back_to_first_catalog_item_for_stale_selection(
    monkeypatch, tmp_path
) -> None:
    client, _config = _client(
        monkeypatch,
        tmp_path,
        config={"scene_image_selection": "newapi_gpt_image2"},
        provider_model="nano-banana-pro",
    )

    response = client.get("/api/v1/projects/demo/image-source-selection/scene")

    assert response.status_code == 200
    data = response.json()["data"]
    assert next(iter(data["options"])) == "nano-banana-pro"
    assert data["image_source_selection"] == "nano-banana-pro"


def test_get_returns_empty_selection_and_options_when_catalog_is_empty(
    monkeypatch, tmp_path
) -> None:
    client, config = _client(
        monkeypatch,
        tmp_path,
        config={"prop_image_selection": "gpt-image-2"},
        credential_available=False,
    )

    response = client.get("/api/v1/projects/demo/image-source-selection/prop")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "asset_kind": "prop",
        "image_source_selection": "",
        "options": {},
    }

    patch_response = client.patch(
        "/api/v1/projects/demo/image-source-selection/prop",
        json={"image_source_selection": "gpt-image-2"},
    )
    assert patch_response.status_code == 400
    assert config == {"prop_image_selection": "gpt-image-2"}


def test_patch_rejects_selection_outside_current_catalog(monkeypatch, tmp_path) -> None:
    client, config = _client(monkeypatch, tmp_path)

    response = client.patch(
        "/api/v1/projects/demo/image-source-selection/character",
        json={"image_source_selection": "newapi_gpt_image2"},
    )

    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert config == {}


def test_legacy_character_endpoint_reuses_dynamic_catalog_logic(
    monkeypatch, tmp_path
) -> None:
    client, config = _client(
        monkeypatch,
        tmp_path,
        config={"character_image_selection": "removed-model"},
        provider_model="nano-banana-pro",
    )

    get_response = client.get("/api/v1/projects/demo/character-image-selection")
    assert get_response.status_code == 200
    assert get_response.json()["data"]["character_image_selection"] == "nano-banana-pro"
    assert next(iter(get_response.json()["data"]["options"])) == "nano-banana-pro"

    patch_response = client.patch(
        "/api/v1/projects/demo/character-image-selection",
        json={"character_image_selection": "gpt-image-2-vip"},
    )
    assert patch_response.status_code == 200
    assert (
        patch_response.json()["data"]["character_image_selection"] == "gpt-image-2-vip"
    )
    assert (
        patch_response.json()["data"]["options"]
        == get_response.json()["data"]["options"]
    )
    assert config["character_image_selection"] == "gpt-image-2-vip"
