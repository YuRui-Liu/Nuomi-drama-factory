from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.assets.organization import AssetFolderNotFound, InvalidAssetPurpose
from novelvideo.sqlite_store import SQLiteStore


@pytest.fixture
async def store(tmp_path: Path):
    value = SQLiteStore(
        "demo",
        output_dir=str(tmp_path / "output"),
        state_dir=str(tmp_path / "state"),
    )
    await value.initialize()
    try:
        yield value
    finally:
        await value.close()


async def test_folder_create_rename_and_list_include_asset_count(store) -> None:
    created = await store.create_asset_folder("主角")
    renamed = await store.rename_asset_folder(created["id"], "主要角色")
    folders = await store.list_asset_folders()
    assert renamed["name"] == "主要角色"
    assert folders == [{**renamed, "asset_count": 0}]


async def test_asset_move_is_single_folder_and_filterable_by_purpose(store) -> None:
    first = await store.create_asset_folder("A")
    second = await store.create_asset_folder("B")
    original = await store.put_asset_organization(
        "identity", "lin-zhao", folder_id=first["id"], purpose="character"
    )
    moved = await store.put_asset_organization(
        "identity", "lin-zhao", folder_id=second["id"], purpose="character"
    )
    assert moved["asset_key"] == original["asset_key"] == "identity:lin-zhao"
    assert moved["asset_id"] == "lin-zhao"
    assert moved["folder_id"] == second["id"]
    assert await store.list_asset_organization(folder_id=first["id"]) == []
    assert await store.list_asset_organization(
        folder_id=second["id"], purpose="character"
    ) == [moved]


async def test_delete_folder_only_unfiles_and_does_not_touch_physical_asset(
    store, tmp_path: Path,
) -> None:
    physical = tmp_path / "output" / "assets" / "portrait.png"
    physical.parent.mkdir(parents=True, exist_ok=True)
    physical.write_bytes(b"asset")
    folder = await store.create_asset_folder("临时")
    placed = await store.put_asset_organization(
        "image", str(physical), folder_id=folder["id"], purpose="other"
    )
    deleted = await store.delete_asset_folder(folder["id"])
    rows = await store.list_asset_organization(purpose="other")
    assert deleted == {"id": folder["id"], "unfiled_count": 1}
    assert rows[0]["asset_key"] == placed["asset_key"]
    assert rows[0]["folder_id"] is None
    assert physical.read_bytes() == b"asset"


async def test_unknown_folder_and_invalid_purpose_are_rejected(store) -> None:
    with pytest.raises(AssetFolderNotFound):
        await store.put_asset_organization(
            "image", "asset-1", folder_id="missing", purpose="other"
        )
    with pytest.raises(InvalidAssetPurpose):
        await store.put_asset_organization(
            "image", "asset-1", folder_id=None, purpose="not-valid"
        )


async def test_formal_asset_count_includes_placements(store) -> None:
    assert await store.formal_asset_count() == 0
    await store.put_asset_organization(
        "video", "video-1", folder_id=None, purpose="video"
    )
    assert await store.formal_asset_count() == 1


async def test_unfiled_filter_uses_null_folder_semantics(store) -> None:
    folder = await store.create_asset_folder("Folder")
    await store.put_asset_organization(
        "video", "filed", folder_id=folder["id"], purpose="video"
    )
    unfiled = await store.put_asset_organization(
        "video", "unfiled", folder_id=None, purpose="video"
    )

    assert await store.list_asset_organization(unfiled=True) == [unfiled]
