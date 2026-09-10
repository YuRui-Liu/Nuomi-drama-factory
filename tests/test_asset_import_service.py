import pytest

from novelvideo.asset_imports import AssetImportService, AssetType
from novelvideo.models import NovelCharacter, NovelScene
from novelvideo.sqlite_store import SQLiteStore


@pytest.mark.asyncio
async def test_confirm_only_fills_empty_fields_and_is_single_use(tmp_path):
    store = SQLiteStore("test/assets", output_dir=str(tmp_path / "out"), state_dir=str(tmp_path / "state"))
    await store.initialize()
    await store.add_character(NovelCharacter(name="谢砚秋", role="用户设定", description=""))
    service = AssetImportService(store)
    preview = await service.preview(
        asset_type=AssetType.CHARACTER,
        filename="人物表.md",
        payload="""# 人物表\n## 谢砚秋\n- 身份：碑刻修复师。\n- 视觉锚点：青灰窄袖衣。\n""".encode(),
        project_id="p1",
        user_id="u1",
    )
    # Simulate a concurrent edit after preview.
    await store.update_character("谢砚秋", description="用户新描述")
    result = await service.confirm(preview.import_id, AssetType.CHARACTER, "p1", "u1")
    char = store.get_character("谢砚秋")
    assert char.role == "用户设定"
    assert char.description == "用户新描述"
    assert result.skipped_count == 1
    with pytest.raises(ValueError, match="已确认"):
        await service.confirm(preview.import_id, AssetType.CHARACTER, "p1", "u1")
    await store.close()


@pytest.mark.asyncio
async def test_preview_scope_is_enforced(tmp_path):
    store = SQLiteStore("test/assets", output_dir=str(tmp_path / "out"), state_dir=str(tmp_path / "state"))
    await store.initialize()
    service = AssetImportService(store)
    preview = await service.preview(
        asset_type=AssetType.PROP,
        filename="道具.txt",
        payload="""## 道具表\n| 道具 | 初始持有人 |\n|---|---|\n| 木尺 | 石九 |\n""".encode(),
        project_id="p1",
        user_id="u1",
    )
    with pytest.raises(ValueError, match="作用域"):
        await service.confirm(preview.import_id, AssetType.PROP, "p1", "u2")
    await store.close()


@pytest.mark.asyncio
async def test_alias_match_updates_canonical_and_exposes_evidence(tmp_path):
    store = SQLiteStore("test/assets", output_dir=str(tmp_path / "out"), state_dir=str(tmp_path / "state"))
    await store.initialize()
    await store.add_character(NovelCharacter(name="谢砚秋", aliases=["谢姑娘"], role="", description="用户描述"))
    service = AssetImportService(store)
    preview = await service.preview(
        asset_type=AssetType.CHARACTER, filename="人物表.md",
        payload="# 人物表\n## 谢姑娘\n- 身份：碑刻修复师。\n- 视觉锚点：青灰衣。\n".encode(),
        project_id="p1", user_id="u1",
    )
    assert preview.diffs[0].canonical_name == "谢砚秋"
    assert preview.diffs[0].changes[0].evidence
    result = await service.confirm(preview.import_id, AssetType.CHARACTER, "p1", "u1")
    assert store.get_character("谢砚秋").role == "碑刻修复师。"
    assert store.get_character("谢姑娘").name == "谢砚秋"
    assert result.supplemented_count == 1
    await store.close()


@pytest.mark.asyncio
async def test_ambiguous_alias_is_conflict(tmp_path):
    store = SQLiteStore("test/assets", output_dir=str(tmp_path / "out"), state_dir=str(tmp_path / "state"))
    await store.initialize()
    await store.add_character(NovelCharacter(name="甲", aliases=["同名"]))
    await store.add_character(NovelCharacter(name="乙", aliases=["同名"]))
    service = AssetImportService(store)
    preview = await service.preview(
        asset_type=AssetType.CHARACTER, filename="人物表.md",
        payload="# 人物表\n## 同名\n- 身份：证人。\n".encode(), project_id="p1", user_id="u1",
    )
    assert preview.diffs[0].disposition == "conflict"
    await store.close()


@pytest.mark.asyncio
async def test_canonical_and_other_asset_alias_collision_is_conflict(tmp_path):
    store = SQLiteStore("test/assets", output_dir=str(tmp_path / "out"), state_dir=str(tmp_path / "state"))
    await store.initialize()
    await store.add_character(NovelCharacter(name="同名", role="已有角色"))
    await store.add_character(NovelCharacter(name="乙", aliases=["同名"]))
    service = AssetImportService(store)
    preview = await service.preview(
        asset_type=AssetType.CHARACTER, filename="人物表.md",
        payload="# 人物表\n## 同名\n- 身份：证人。\n".encode(), project_id="p", user_id="u",
    )
    assert preview.diffs[0].disposition == "conflict"
    await store.close()


@pytest.mark.asyncio
async def test_store_atomically_rejects_second_confirmation(tmp_path):
    store = SQLiteStore("test/assets", output_dir=str(tmp_path / "out"), state_dir=str(tmp_path / "state"))
    await store.initialize()
    service = AssetImportService(store)
    preview = await service.preview(asset_type=AssetType.PROP, filename="道具.md", payload="## 道具表\n| 道具 | 初始持有人 |\n|---|---|\n| 木尺 | 石九 |\n".encode(), project_id="p", user_id="u")
    from novelvideo.asset_imports.models import AssetCandidate
    record = await store.get_asset_import_preview(preview.import_id)
    import json
    candidates = [AssetCandidate.model_validate(x) for x in json.loads(record["candidates_json"])]
    await store.confirm_asset_import(preview.import_id, "prop", candidates, preview.diffs, "p", "u")
    with pytest.raises(ValueError, match="已确认"):
        await store.confirm_asset_import(preview.import_id, "prop", candidates, preview.diffs, "p", "u")
    await store.close()


@pytest.mark.asyncio
async def test_store_claim_enforces_scope_and_text_literal_is_not_empty(tmp_path):
    store = SQLiteStore("test/assets", output_dir=str(tmp_path / "out"), state_dir=str(tmp_path / "state"))
    await store.initialize()
    await store.add_character(NovelCharacter(name="甲", description="{}"))
    service = AssetImportService(store)
    preview = await service.preview(asset_type=AssetType.CHARACTER, filename="人物.md", payload="# 人物表\n## 甲\n- 身份：证人。\n- 视觉锚点：新描述。\n".encode(), project_id="p", user_id="u")
    from novelvideo.asset_imports.models import AssetCandidate
    import json
    record = await store.get_asset_import_preview(preview.import_id)
    candidates = [AssetCandidate.model_validate(x) for x in json.loads(record["candidates_json"])]
    with pytest.raises(ValueError, match="作用域"):
        await store.confirm_asset_import(preview.import_id, "character", candidates, preview.diffs, "wrong", "u")
    result = await service.confirm(preview.import_id, AssetType.CHARACTER, "p", "u")
    assert store.get_character("甲").description == "{}"
    assert next(x for x in result.diffs[0].changes if x.field == "description").disposition == "preserve"
    db = await store._ensure_db()
    async with db.execute("SELECT result_json FROM asset_import_audits WHERE import_id = ?", (preview.import_id,)) as cursor:
        audit = json.loads((await cursor.fetchone())["result_json"])
    assert "diffs" in audit and "preview_summary" in audit
    await store.close()


@pytest.mark.asyncio
async def test_confirm_replaces_technical_scene_default_with_evidenced_value(tmp_path):
    store = SQLiteStore("test/assets", output_dir=str(tmp_path / "out"), state_dir=str(tmp_path / "state"))
    await store.initialize()
    await store.add_scene(NovelScene(name="外景", scene_type="interior"))
    service = AssetImportService(store)
    preview = await service.preview(
        asset_type=AssetType.SCENE,
        filename="场景.md",
        payload="""## 场景表
| 场景名 | 内／外 |
|---|---|
| 外景 | 外 |
""".encode(),
        project_id="p",
        user_id="u",
    )

    change = next(item for item in preview.diffs[0].changes if item.field == "scene_type")
    assert change.disposition == "fill"
    result = await service.confirm(preview.import_id, AssetType.SCENE, "p", "u")

    assert (await store.get_scene("外景")).scene_type == "exterior"
    assert result.supplemented_count == 1
    await store.close()


@pytest.mark.asyncio
async def test_confirm_does_not_turn_post_commit_cache_refresh_into_failure(tmp_path, monkeypatch):
    store = SQLiteStore("test/assets", output_dir=str(tmp_path / "out"), state_dir=str(tmp_path / "state"))
    await store.initialize()
    service = AssetImportService(store)
    preview = await service.preview(
        asset_type=AssetType.PROP,
        filename="道具.md",
        payload="""## 道具表
| 道具 | 初始持有人 |
|---|---|
| 木尺 | 石九 |
""".encode(),
        project_id="p",
        user_id="u",
    )

    async def broken_reload():
        raise RuntimeError("cache reload failed")

    monkeypatch.setattr(store, "load_graph_state", broken_reload)
    result = await service.confirm(preview.import_id, AssetType.PROP, "p", "u")

    assert result.created_count == 1
    assert (await store.get_prop("木尺")) is not None
    await store.close()
