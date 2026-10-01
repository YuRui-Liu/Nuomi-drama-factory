from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from novelvideo.api.routes import episodes


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["identity_ids", "scene_ids", "prop_ids"])
async def test_binding_editor_rejects_foreign_assets_before_write(monkeypatch, field):
    store = SimpleNamespace(get_episode=lambda n: SimpleNamespace(scene_menu=[], prop_menu=[]),
        get_all_characters=lambda: [], list_scenes=AsyncMock(return_value=[]),
        list_props=AsyncMock(return_value=[]), update_episode=AsyncMock())
    monkeypatch.setattr(episodes, "resolve_project_scope", AsyncMock(return_value=SimpleNamespace(ctx=None, username="u", project_name="p")))
    monkeypatch.setattr(episodes, "make_sqlite_store", AsyncMock(return_value=store))
    body = {"identity_ids": [], "scene_ids": [], "prop_ids": []}
    body[field] = ["other-project"]
    result = await episodes.update_episode_asset_bindings("p", 2, body, {})
    assert result["ok"] is False
    store.update_episode.assert_not_called()


@pytest.mark.asyncio
async def test_binding_editor_preserves_selected_metadata_and_only_updates_target(monkeypatch):
    scene = {"scene_id": "山门", "time_of_day": "night"}
    prop = {"prop_id": "铃", "marker_color": "red"}
    ep = SimpleNamespace(scene_menu=[scene], prop_menu=[prop], identity_default_map={"白尾": "old"})
    store = SimpleNamespace(get_episode=lambda n: ep, get_all_characters=lambda: [],
        list_scenes=AsyncMock(return_value=[SimpleNamespace(name="山门")]),
        list_props=AsyncMock(return_value=[SimpleNamespace(name="铃")]), update_episode=AsyncMock())
    monkeypatch.setattr(episodes, "resolve_project_scope", AsyncMock(return_value=SimpleNamespace(ctx=None, username="u", project_name="p")))
    monkeypatch.setattr(episodes, "make_sqlite_store", AsyncMock(return_value=store))
    monkeypatch.setattr(episodes, "_episode_detail_payload", lambda ep, n: {"number": n})
    result = await episodes.update_episode_asset_bindings("p", 2, {"identity_ids": [], "scene_ids": ["山门"], "prop_ids": []}, {})
    assert result["ok"] is True
    store.update_episode.assert_awaited_once_with(2, identity_ids=[], identity_default_map={}, scene_menu=[scene], prop_menu=[])
