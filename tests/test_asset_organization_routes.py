from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType


class _Store:
    async def create_asset_folder(self, name: str):
        return {"id": "fld_1", "name": name}

    async def put_asset_organization(self, asset_type, asset_id, **values):
        return {
            "asset_key": f"{asset_type}:{asset_id}",
            "asset_type": asset_type,
            "asset_id": asset_id,
            **values,
        }

    async def close(self) -> None:
        pass


async def test_asset_organization_routes_preserve_public_response_shape(monkeypatch) -> None:
    api_root = Path(__file__).parents[1] / "src" / "novelvideo" / "api"
    api_package = ModuleType("novelvideo.api")
    api_package.__path__ = [str(api_root)]
    routes_package = ModuleType("novelvideo.api.routes")
    routes_package.__path__ = [str(api_root / "routes")]
    deps = ModuleType("novelvideo.api.deps")
    deps.make_sqlite_store_for_context = None
    deps.resolve_project_scope = None
    monkeypatch.setitem(sys.modules, "novelvideo.api", api_package)
    monkeypatch.setitem(sys.modules, "novelvideo.api.routes", routes_package)
    monkeypatch.setitem(sys.modules, "novelvideo.api.deps", deps)
    sys.modules.pop("novelvideo.api.routes.assets", None)
    assets = importlib.import_module("novelvideo.api.routes.assets")
    store = _Store()

    async def resolve(*_args, **_kwargs):
        return SimpleNamespace(ctx=object())

    async def make_store(_ctx):
        return store

    monkeypatch.setattr(assets, "resolve_project_scope", resolve)
    monkeypatch.setattr(assets, "make_sqlite_store_for_context", make_store)
    created = await assets.create_asset_folder(
        "proj", assets.AssetFolderCreate(name="主角"), user={}
    )
    placed = await assets.put_asset_organization(
        "proj",
        "identity",
        "lin-zhao",
        assets.AssetOrganizationUpdate(folder_id="fld_1", purpose="character"),
        user={},
    )
    assert created == {"ok": True, "data": {"id": "fld_1", "name": "主角"}}
    assert placed["data"]["asset_key"] == "identity:lin-zhao"
    assert placed["data"]["purpose"] == "character"
    organization_route = next(
        route
        for route in assets.router.routes
        if route.endpoint is assets.put_asset_organization
    )
    assert organization_route.path.endswith("/{asset_id:path}/organization")
    sys.modules.pop("novelvideo.api.routes.assets", None)
