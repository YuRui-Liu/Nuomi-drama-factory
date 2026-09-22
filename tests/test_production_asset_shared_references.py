import pytest

from tests.test_api_production_assets import _client


def test_deleting_version_preserves_file_referenced_by_another_slot(tmp_path, monkeypatch):
    client, project_dir, _state_dir = _client(tmp_path, monkeypatch)
    shared = project_dir / "shared.png"
    shared.write_bytes(b"shared reference image")
    for label in ("a", "b"):
        (project_dir / f"{label}.png").write_bytes(b"current image")
        endpoint = f"/api/v1/projects/project-1/production-assets/slots/character:{label}:portrait"
        response = client.post(endpoint + "/legacy-import", json={
            "asset_kind": "character_portrait", "asset_path": f"{label}.png",
        })
        assert response.status_code == 200
        response = client.post(endpoint + "/versions", json={
            "asset_kind": "character_portrait", "version_id": f"shared-{label}",
            "asset_path": "shared.png", "qc_passed": True,
        })
        assert response.status_code == 200
    response = client.delete(
        "/api/v1/projects/project-1/production-assets/slots/character:a:portrait/versions/shared-a"
    )
    assert response.status_code == 200
    assert shared.is_file(), "the other slot still uses this file"


@pytest.mark.parametrize("reference_kind", ["asset", "canonical"])
@pytest.mark.parametrize("fallback", [False, True])
def test_delete_current_protects_other_slot_asset_and_canonical_references(
    tmp_path, monkeypatch, reference_kind, fallback,
):
    client, project_dir, _state_dir = _client(tmp_path, monkeypatch)
    for name in ("shared.png", "other.png", "fallback.png"):
        (project_dir / name).write_bytes(name.encode())
    endpoint = "/api/v1/projects/project-1/production-assets/slots"
    original = client.post(endpoint + "/character:a:portrait/legacy-import", json={
        "asset_kind": "character_portrait", "asset_path": "shared.png",
    }).json()["data"]["current_version"]["version_id"]
    if fallback:
        response = client.post(endpoint + "/character:a:portrait/versions", json={
            "asset_kind": "character_portrait", "version_id": "fallback",
            "asset_path": "fallback.png", "qc_passed": True,
        })
        assert response.status_code == 200
    response = client.post(endpoint + "/character:b:portrait/versions", json={
        "asset_kind": "character_portrait", "version_id": "other",
        "asset_path": "shared.png" if reference_kind == "asset" else "other.png",
        "generation_metadata": {"canonical_path": "shared.png"} if reference_kind == "canonical" else {},
        "qc_passed": True,
    })
    assert response.status_code == 200
    response = client.delete(endpoint + f"/character:a:portrait/versions/{original}")
    # Replacing a shared canonical with different fallback bytes cannot be
    # made safe for the other slot; leave the operation unchanged instead.
    assert response.status_code == (409 if fallback else 200)
    assert (project_dir / "shared.png").read_bytes() == b"shared.png"
    if fallback:
        current = client.get(
            endpoint + "/character:a:portrait",
            params={"asset_kind": "character_portrait"},
        ).json()["data"]
        assert current["slot"]["current_version_id"] == original
