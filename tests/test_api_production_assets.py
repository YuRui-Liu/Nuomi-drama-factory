from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path, Path]:
    from novelvideo.api import auth as api_auth
    from novelvideo.api.routes import production_assets

    project_dir = tmp_path / "output" / "demo"
    state_dir = tmp_path / "state" / "demo"
    project_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)

    async def resolve_project_scope(project: str, user: dict, *, required_role: str = "viewer"):
        assert project == "project-1"
        return SimpleNamespace(
            project_dir=project_dir,
            state_dir=str(state_dir),
            ctx=SimpleNamespace(owner_project_label="frank/demo"),
        )

    monkeypatch.setattr(production_assets, "resolve_project_scope", resolve_project_scope)
    app = FastAPI()
    app.include_router(production_assets.router, prefix="/api/v1")
    user = {"id": "local", "username": "frank"}
    app.dependency_overrides[api_auth.get_api_user] = lambda: user
    app.dependency_overrides[production_assets.get_api_user] = lambda: user
    return TestClient(app), project_dir, state_dir


def test_legacy_preview_is_read_only_for_character_scene_and_prop(tmp_path, monkeypatch):
    client, project_dir, state_dir = _client(tmp_path, monkeypatch)
    cases = [
        ("character:lin:portrait", "character_portrait", "assets/characters/lin/portrait.png"),
        ("scene:station:master", "scene_master", "assets/scenes/station/master.png"),
        ("prop:token:reference", "prop_reference", "assets/props/token/reference.png"),
    ]
    for _slot_id, _asset_kind, asset_path in cases:
        target = project_dir / asset_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"image")

    for slot_id, asset_kind, asset_path in cases:
        response = client.get(
            f"/api/v1/projects/project-1/production-assets/slots/{slot_id}",
            params={"asset_kind": asset_kind, "legacy_asset_path": asset_path},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["current_version"]["origin"] == "legacy_import"
        assert data["read_only"] is False

    assert not (state_dir / "production_workflow.json").exists()


def test_materialize_register_and_adopt_candidate_without_overwriting_current(tmp_path, monkeypatch):
    client, project_dir, state_dir = _client(tmp_path, monkeypatch)
    legacy_path = "assets/characters/lin/portrait.png"
    candidate_path = "assets/characters/lin/candidate-2.png"
    for asset_path in (legacy_path, candidate_path):
        target = project_dir / asset_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"image")

    imported = client.post(
        "/api/v1/projects/project-1/production-assets/slots/character:lin:portrait/legacy-import",
        json={"asset_kind": "character_portrait", "asset_path": legacy_path},
    )
    assert imported.status_code == 200
    legacy_version_id = imported.json()["data"]["current_version"]["version_id"]

    registered = client.post(
        "/api/v1/projects/project-1/production-assets/slots/character:lin:portrait/versions",
        json={
            "asset_kind": "character_portrait",
            "version_id": "candidate-2",
            "asset_path": candidate_path,
            "source_attempt_id": "attempt-2",
            "qc_passed": True,
            "generation_metadata": {"provider": "grsai", "model": "image-v2"},
        },
    )
    assert registered.status_code == 200
    assert registered.json()["data"]["slot"]["current_version_id"] == legacy_version_id
    assert registered.json()["data"]["version"]["adoption_status"] == "candidate"

    adopted = client.post(
        "/api/v1/projects/project-1/production-assets/slots/character:lin:portrait/versions/candidate-2/adopt",
        json={"reason": "角色脸部一致性更好"},
    )
    assert adopted.status_code == 200
    assert adopted.json()["data"]["slot"]["current_version_id"] == "candidate-2"
    assert (state_dir / "production_workflow.json").exists()


def test_asset_paths_cannot_escape_project_root(tmp_path, monkeypatch):
    client, _project_dir, _state_dir = _client(tmp_path, monkeypatch)
    response = client.post(
        "/api/v1/projects/project-1/production-assets/slots/character:lin:portrait/legacy-import",
        json={"asset_kind": "character_portrait", "asset_path": "../secret.png"},
    )
    assert response.status_code == 400


def test_adopting_character_state_candidate_updates_compatibility_asset(
    tmp_path, monkeypatch
):
    client, project_dir, _state_dir = _client(tmp_path, monkeypatch)
    canonical_path = "assets/characters/lin/identities/duty.png"
    candidate_path = "assets/characters/lin/identities/duty/versions/state-2.png"
    canonical = project_dir / canonical_path
    candidate = project_dir / candidate_path
    canonical.parent.mkdir(parents=True, exist_ok=True)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_bytes(b"old-state")
    candidate.write_bytes(b"new-state")

    imported = client.post(
        "/api/v1/projects/project-1/production-assets/slots/character:lin:state:duty/legacy-import",
        json={"asset_kind": "character_state", "asset_path": canonical_path},
    )
    assert imported.status_code == 200
    registered = client.post(
        "/api/v1/projects/project-1/production-assets/slots/character:lin:state:duty/versions",
        json={
            "asset_kind": "character_state",
            "version_id": "state-2",
            "asset_path": candidate_path,
            "qc_passed": True,
            "generation_metadata": {
                "canonical_path": canonical_path,
                "panel_layout": ["front", "side", "back"],
            },
        },
    )
    assert registered.status_code == 200

    adopted = client.post(
        "/api/v1/projects/project-1/production-assets/slots/character:lin:state:duty/versions/state-2/adopt",
        json={"reason": "三视图一致性更好"},
    )

    assert adopted.status_code == 200
    assert canonical.read_bytes() == b"new-state"


def test_adopting_scene_candidate_clears_matching_stale_reference(
    tmp_path, monkeypatch
):
    client, project_dir, _state_dir = _client(tmp_path, monkeypatch)
    canonical_path = "assets/scenes/hall/master.png"
    candidate_path = "assets/scenes/hall/versions/master-2.png"
    canonical = project_dir / canonical_path
    candidate = project_dir / candidate_path
    canonical.parent.mkdir(parents=True, exist_ok=True)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_bytes(b"old-master")
    candidate.write_bytes(b"new-master")
    cleared: list[tuple[str, str]] = []

    class FakeSQLiteStore:
        def __init__(self, *_args, **_kwargs):
            pass

        async def initialize(self):
            pass

        async def clear_scene_stale_reference_kind(self, scene_name, kind):
            cleared.append((scene_name, kind))
            return True

        async def close(self):
            pass

    monkeypatch.setattr("novelvideo.sqlite_store.SQLiteStore", FakeSQLiteStore)

    imported = client.post(
        "/api/v1/projects/project-1/production-assets/slots/scene:hall:base:master/legacy-import",
        json={"asset_kind": "scene_base", "asset_path": canonical_path},
    )
    assert imported.status_code == 200
    registered = client.post(
        "/api/v1/projects/project-1/production-assets/slots/scene:hall:base:master/versions",
        json={
            "asset_kind": "scene_base",
            "version_id": "master-2",
            "asset_path": candidate_path,
            "qc_passed": True,
            "generation_metadata": {
                "canonical_path": canonical_path,
                "scene_id": "hall",
                "anchor_kind": "master",
            },
        },
    )
    assert registered.status_code == 200

    adopted = client.post(
        "/api/v1/projects/project-1/production-assets/slots/scene:hall:base:master/versions/master-2/adopt",
        json={"reason": "场景结构更准确"},
    )

    assert adopted.status_code == 200
    assert canonical.read_bytes() == b"new-master"
    assert cleared == [("hall", "master")]

    cleared.clear()
    no_canonical_path = "assets/scenes/hall/versions/master-3.png"
    no_canonical = project_dir / no_canonical_path
    no_canonical.write_bytes(b"candidate-without-canonical")
    registered = client.post(
        "/api/v1/projects/project-1/production-assets/slots/scene:hall:base:master/versions",
        json={
            "asset_kind": "scene_base",
            "version_id": "master-3",
            "asset_path": no_canonical_path,
            "qc_passed": True,
            "generation_metadata": {
                "scene_id": "hall",
                "anchor_kind": "master",
            },
        },
    )
    assert registered.status_code == 200
    adopted = client.post(
        "/api/v1/projects/project-1/production-assets/slots/scene:hall:base:master/versions/master-3/adopt",
        json={"reason": "仅切换候选，不替换标准图"},
    )
    assert adopted.status_code == 200
    assert canonical.read_bytes() == b"new-master"
    assert cleared == []
    mismatched_path = "assets/scenes/hall/versions/master-4.png"
    mismatched = project_dir / mismatched_path
    mismatched.write_bytes(b"mismatched-scene")
    registered = client.post(
        "/api/v1/projects/project-1/production-assets/slots/scene:hall:base:master/versions",
        json={
            "asset_kind": "scene_base",
            "version_id": "master-4",
            "asset_path": mismatched_path,
            "qc_passed": True,
            "generation_metadata": {
                "canonical_path": canonical_path,
                "scene_id": "other-scene",
                "anchor_kind": "master",
            },
        },
    )
    assert registered.status_code == 200
    adopted = client.post(
        "/api/v1/projects/project-1/production-assets/slots/scene:hall:base:master/versions/master-4/adopt",
        json={"reason": "验证元数据与槽位不匹配"},
    )
    assert adopted.status_code == 409
    assert canonical.read_bytes() == b"new-master"
    assert cleared == []

    wrong_target_path = "assets/scenes/hall/versions/master-5.png"
    wrong_target = project_dir / wrong_target_path
    wrong_target.write_bytes(b"wrong-canonical-target")
    other_canonical_path = "assets/scenes/other/master.png"
    other_canonical = project_dir / other_canonical_path
    other_canonical.parent.mkdir(parents=True, exist_ok=True)
    other_canonical.write_bytes(b"must-not-change")
    registered = client.post(
        "/api/v1/projects/project-1/production-assets/slots/scene:hall:base:master/versions",
        json={
            "asset_kind": "scene_base",
            "version_id": "master-5",
            "asset_path": wrong_target_path,
            "qc_passed": True,
            "generation_metadata": {
                "canonical_path": other_canonical_path,
                "scene_id": "hall",
                "anchor_kind": "master",
            },
        },
    )
    assert registered.status_code == 200
    adopted = client.post(
        "/api/v1/projects/project-1/production-assets/slots/scene:hall:base:master/versions/master-5/adopt",
        json={"reason": "验证标准图不能越槽覆盖"},
    )
    assert adopted.status_code == 409
    assert other_canonical.read_bytes() == b"must-not-change"
    assert canonical.read_bytes() == b"new-master"
    assert cleared == []


def test_scene_pano_slot_uses_director_world_canonical_path():
    from novelvideo.api.routes.production_assets import (
        _scene_slot_canonical_relative_path,
    )

    canonical = _scene_slot_canonical_relative_path(
        "scene:hall:base:pano",
        {"scene_id": "hall", "anchor_kind": "pano"},
    )

    assert canonical == Path("director_worlds/hall/v1/pano_360.png")
    assert (
        _scene_slot_canonical_relative_path(
            "scene:other:base:master",
            {"scene_id": "hall", "anchor_kind": "pano"},
        )
        is None
    )
    assert (
        _scene_slot_canonical_relative_path(
            "character:hall:portrait",
            {"scene_id": "hall", "anchor_kind": "pano"},
        )
        is None
    )
