from novelvideo.api import api_router
from novelvideo.api.routes import asset_imports


def test_asset_import_routes_are_registered():
    registrations = [route for route in api_router.routes if getattr(route, "original_router", None) is asset_imports.router]
    assert len(registrations) == 1
    paths = {f"/api/v1{route.path}" for route in asset_imports.router.routes}
    assert "/api/v1/projects/{project}/asset-imports/{asset_type}/preview" in paths
    assert "/api/v1/projects/{project}/asset-imports/{asset_type}/{import_id}/confirm" in paths
