import importlib.util
import sqlite3

import pytest

from novelvideo.script_creation.store import DocumentConflict, DocumentNotFound, DocumentValidation, DocumentStore


def test_service_exists():
    assert importlib.util.find_spec("novelvideo.script_creation.entities") is not None


@pytest.fixture
async def setup(tmp_path):
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.script_creation.entities import EntityService
    assets = SQLiteStore("demo", str(tmp_path / "project"))
    await assets.initialize()
    store = DocumentStore(assets.db_path)
    await store.initialize()
    service = EntityService(store)
    await service.initialize()
    yield store, service, assets
    await assets.close()


async def entity(store, service, kind="people", name="A", mutation="a"):
    doc = await store.create(kind=kind, title=name, markdown=f"## {name}", client_mutation_id=mutation)
    result = await service.put(document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=doc.revision.blocks[0].id, name=name, client_mutation_id=mutation)
    return doc, result


async def test_explicit_link_rename_delete_recreate_and_replay(setup):
    store, service, assets = setup
    doc, ent = await entity(store, service)
    with sqlite3.connect(assets.db_path) as db:
        db.execute("INSERT INTO characters(name) VALUES ('A')")
    assert (await service.list(doc.id))[0]["asset_id"] is None
    choice = (await service.assets("character"))[0]
    args = dict(entity_id=ent["entity_id"], document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=doc.revision.blocks[0].id, name="A", asset_id=choice["asset_id"], client_mutation_id="link")
    linked = await service.put(**args)
    assert linked["selected_revision"] == doc.current_revision_id
    assert await service.put(**args) == linked
    with pytest.raises(DocumentConflict):
        await service.put(**{**args, "name": "other"})
    with sqlite3.connect(assets.db_path) as db:
        db.execute("UPDATE characters SET name='renamed' WHERE name='A'")
    assert (await service.list(doc.id))[0]["asset_name"] == "renamed"
    with sqlite3.connect(assets.db_path) as db:
        db.execute("DELETE FROM characters")
        db.execute("INSERT INTO characters(name) VALUES ('renamed')")
    missing = (await service.list(doc.id))[0]
    assert missing["asset_missing"]
    assert missing["asset_id"] == choice["asset_id"]
    with pytest.raises(DocumentValidation):
        await service.put(**{**args, "asset_id": "foreign-project-uuid", "client_mutation_id": "foreign"})


async def test_stale_entity_requires_explicit_confirmation_and_relations_are_stable(setup):
    store, service, _ = setup
    doc, ent = await entity(store, service)
    prop_doc, prop = await entity(store, service, "props", "key", "prop")
    episode = await store.create(kind="episode_script", title="E1", episode_number=1, markdown="A拿起钥匙", client_mutation_id="episode")
    args = dict(entity_id=ent["entity_id"], document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=doc.revision.blocks[0].id, name="A", relations=[{"kind": "holding", "entity_id": prop["entity_id"]}],
        appearances=[{"kind": "first_appearance", "status": "planned", "episode_number": 1},
            {"kind": "critical_scene", "status": "written", "document_id": episode.id, "revision_id": episode.current_revision_id}], client_mutation_id="relations")
    linked = await service.put(**args)
    assert linked["relations"][0]["entity_id"] == prop["entity_id"]
    saved = await store.save(doc.id, base_revision_id=doc.current_revision_id, markdown="## renamed", client_mutation_id="rename")
    assert (await service.list(doc.id))[0]["stale"]
    with pytest.raises(DocumentConflict):
        await service.put(**{**args, "client_mutation_id": "stale"})
    confirmed = await service.put(**{**args, "base_revision_id": saved.current_revision_id, "name": "renamed", "client_mutation_id": "confirm"})
    assert confirmed["entity_id"] == ent["entity_id"]
    assert not confirmed["stale"]
    with pytest.raises(DocumentValidation):
        await service.put(**{**args, "base_revision_id": saved.current_revision_id, "relations": [{"kind": "holding", "entity_id": "foreign"}], "client_mutation_id": "badrel"})
    with pytest.raises(DocumentValidation):
        await service.put(**{**args, "base_revision_id": saved.current_revision_id, "appearances": [{"kind": "first_appearance", "status": "written", "document_id": doc.id, "revision_id": saved.current_revision_id}], "client_mutation_id": "badwritten"})


async def test_text_creation_is_insert_only_atomic_and_no_media(setup):
    store, service, assets = setup
    doc, ent = await entity(store, service)
    args = dict(entity_id=ent["entity_id"], document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=doc.revision.blocks[0].id, name="A", create_text={"name": "text", "description": "Only text"}, client_mutation_id="text")
    created = await service.put(**args)
    assert created["asset_id"]
    assert await service.put(**args) == created
    with pytest.raises(DocumentConflict):
        await service.put(**{**args, "client_mutation_id": "duplicate"})
    with sqlite3.connect(assets.db_path) as db:
        row = db.execute("SELECT description,face_prompt,reference_audio_path FROM characters WHERE name='text'").fetchone()
        assert row == ("Only text", "", "")


async def test_confirming_entity_does_not_silently_confirm_old_asset_selection(setup):
    store, service, _ = setup
    doc, ent = await entity(store, service)
    args = dict(entity_id=ent["entity_id"], document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=doc.revision.blocks[0].id, name="A", client_mutation_id="text", create_text={"name": "A"})
    linked = await service.put(**args)
    saved = await store.save(doc.id, base_revision_id=doc.current_revision_id, markdown="## renamed", client_mutation_id="save")
    confirmed = await service.put(**{**args, "base_revision_id": saved.current_revision_id, "create_text": None, "client_mutation_id": "confirm"})
    assert confirmed["selected_revision"] == doc.current_revision_id
    assert confirmed["stale"]
    selected = await service.put(**{**args, "base_revision_id": saved.current_revision_id, "create_text": None, "asset_id": linked["asset_id"], "client_mutation_id": "select"})
    assert not selected["stale"]


async def test_scene_relations_and_deleted_design_are_visible(setup):
    store, service, _ = setup
    people_doc, person = await entity(store, service)
    prop_doc, prop = await entity(store, service, "props", "key", "prop")
    scene_doc, scene = await entity(store, service, "scenes", "room", "scene")
    args = dict(entity_id=scene["entity_id"], document_id=scene_doc.id, base_revision_id=scene_doc.current_revision_id,
        block_id=scene_doc.revision.blocks[0].id, name="room", client_mutation_id="relations",
        relations=[{"kind": "key_prop", "entity_id": prop["entity_id"]}, {"kind": "entry", "entity_id": person["entity_id"]}])
    linked = await service.put(**args)
    assert len(linked["relations"]) == 2
    saved = await store.save(prop_doc.id, base_revision_id=prop_doc.current_revision_id, markdown="", client_mutation_id="delete")
    listed = (await service.list(scene_doc.id))[0]
    assert listed["relations"][0]["missing"]
    assert (await service.list(prop_doc.id))[0]["entry_missing"]
    recreated = await store.save(prop_doc.id, base_revision_id=saved.current_revision_id, markdown="## key", client_mutation_id="recreate")
    assert recreated.revision.blocks[0].id != prop_doc.revision.blocks[0].id
    assert (await service.list(prop_doc.id))[0]["entry_missing"]
    with pytest.raises(DocumentValidation):
        await service.put(document_id=prop_doc.id, entity_id=prop["entity_id"], base_revision_id=recreated.current_revision_id,
            block_id=recreated.revision.blocks[0].id, name="key", client_mutation_id="reassign")


@pytest.mark.parametrize("kind", ["people", "scenes", "props"])
async def test_text_record_creation_for_each_type_and_cross_project_uuid_rejected(setup, kind, tmp_path):
    from novelvideo.sqlite_store import SQLiteStore
    store, service, _ = setup
    doc, ent = await entity(store, service, kind)
    linked = await service.put(entity_id=ent["entity_id"], document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=doc.revision.blocks[0].id, name="A", create_text={"name": "A"}, client_mutation_id="text")
    other = SQLiteStore("other", str(tmp_path / "other"))
    await other.initialize()
    try:
        other_store = DocumentStore(other.db_path)
        await other_store.initialize()
        other_service = type(service)(other_store)
        await other_service.initialize()
        other_doc, other_entity = await entity(other_store, other_service, kind)
        with pytest.raises(DocumentValidation):
            await other_service.put(entity_id=other_entity["entity_id"], document_id=other_doc.id, base_revision_id=other_doc.current_revision_id,
                block_id=other_doc.revision.blocks[0].id, name="A", asset_id=linked["asset_id"], client_mutation_id="foreign")
        assert not (await other_service.list())[0]["asset_missing"]
    finally:
        await other.close()


@pytest.mark.parametrize("before,after", [
    ("## Alice\n\n## Alice", "## Alice"),
    ("## Alice\n\n## Bob", "## Charlie"),
    ("## Alice\n\n## Bob", "## Charlie\n\n## David"),
    ("## Alice\n\n## Bob", "## Alice\n\n## Alice"),
])
async def test_ambiguous_block_edits_cannot_transfer_existing_entity(setup, before, after):
    store, service, _ = setup
    doc = await store.create(kind="people", title="people", markdown=before, client_mutation_id="document")
    block = doc.revision.blocks[0]
    original = await service.put(document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=block.id, name="Alice", client_mutation_id="original", create_text={"name": "Alice"})
    saved = await store.save(doc.id, base_revision_id=doc.current_revision_id, markdown=after, client_mutation_id="edit")
    assert (await service.list(doc.id))[0]["entry_missing"]
    with pytest.raises(DocumentValidation):
        await service.put(entity_id=original["entity_id"], document_id=doc.id, base_revision_id=saved.current_revision_id,
            block_id=block.id, name="Alice", client_mutation_id="confirm")
    with pytest.raises(DocumentValidation):
        await service.put(entity_id=original["entity_id"], document_id=doc.id, base_revision_id=saved.current_revision_id,
            block_id=saved.revision.blocks[0].id, name="Alice", client_mutation_id="reassign")
    new = await service.put(document_id=doc.id, base_revision_id=saved.current_revision_id,
        block_id=saved.revision.blocks[0].id, name="new entry", asset_id=original["asset_id"], client_mutation_id="explicit-new")
    assert new["entity_id"] != original["entity_id"]
    assert new["asset_id"] == original["asset_id"]


async def test_unambiguous_heading_rename_preserves_identity(setup):
    store, service, _ = setup
    doc = await store.create(kind="people", title="people", markdown="## Alice\n\n## Bob", client_mutation_id="document")
    original = await service.put(document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=doc.revision.blocks[0].id, name="Alice", client_mutation_id="original")
    saved = await store.save(doc.id, base_revision_id=doc.current_revision_id, markdown="## Alicia\n\n## Bob", client_mutation_id="edit")
    assert saved.revision.blocks[0].id == original["block_id"]
    assert not (await service.list(doc.id))[0]["entry_missing"]
    confirmed = await service.put(entity_id=original["entity_id"], document_id=doc.id, base_revision_id=saved.current_revision_id,
        block_id=original["block_id"], name="Alicia", client_mutation_id="confirm")
    assert confirmed["entity_id"] == original["entity_id"]


@pytest.mark.parametrize("description", ["年龄未知", "年迈的老人"])
async def test_text_character_does_not_invent_youth_age(setup, description):
    store, service, assets = setup
    doc, ent = await entity(store, service)
    result = await service.put(entity_id=ent["entity_id"], document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=ent["block_id"], name="A", create_text={"name": "A", "description": description}, client_mutation_id="text")
    assert result["asset_record"]["age_group"] == ""
    await assets.load_graph_state()
    assert assets.get_character("A").age_group == ""


async def test_text_character_uses_existing_name_normalization_without_overwriting(setup):
    store, service, assets = setup
    doc, ent = await entity(store, service)
    args = dict(entity_id=ent["entity_id"], document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=ent["block_id"], name="A", client_mutation_id="first", create_text={"name": "A/B", "description": "original"})
    result = await service.put(**args)
    assert result["asset_name"] == "A_B"
    await assets.load_graph_state()
    assert assets.get_character("A_B").name == result["asset_name"]
    with pytest.raises(DocumentConflict):
        await service.put(**{**args, "client_mutation_id": "collision", "create_text": {"name": "A:B", "description": "must not overwrite"}})
    assert (await service.assets("character")) == [{"asset_id": result["asset_id"], "asset_type": "character", "name": "A_B", "description": "original"}]
    await assets.delete_character("A_B")
    assert (await service.list(doc.id))[0]["asset_missing"]


async def test_new_relations_cannot_target_deleted_entries_but_old_ones_can_be_removed(setup):
    store, service, _ = setup
    doc, ent = await entity(store, service)
    prop_doc, prop = await entity(store, service, "props", "key", "prop")
    args = dict(entity_id=ent["entity_id"], document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=ent["block_id"], name="A", relations=[{"kind": "holding", "entity_id": prop["entity_id"]}])
    await service.put(**args, client_mutation_id="relation")
    await store.save(prop_doc.id, base_revision_id=prop_doc.current_revision_id, markdown="", client_mutation_id="delete")
    removed = await service.put(**{**args, "relations": []}, client_mutation_id="remove")
    assert removed["relations"] == []
    with pytest.raises(DocumentValidation):
        await service.put(**args, client_mutation_id="readd")


@pytest.mark.parametrize("kind,field,design,expected", [
    ("scenes", "environment_prompt", "- **空间布局：**北侧木柜，南侧窄门\n- **视觉/光线：**冷色侧光\n- **关键物件：**墙面旧钟", "空间布局：北侧木柜，南侧窄门\n视觉/光线：冷色侧光\n关键物件：墙面旧钟"),
    ("props", "visual_prompt", "- **外观材质：**铜制方形印章，边缘磨损", "外观材质：铜制方形印章，边缘磨损"),
])
async def test_text_design_creates_renderable_fields_without_narrative(setup, kind, field, design, expected):
    store, service, assets = setup
    doc, ent = await entity(store, service, kind)
    description = f"## A\n- **剧情作用：**揭露秘密\n{design}\n- **首次出场：**计划第六集\n- **连续性约束：**第七集被烧毁"
    result = await service.put(entity_id=ent["entity_id"], document_id=doc.id,
        base_revision_id=doc.current_revision_id, block_id=ent["block_id"], name="A",
        create_text={"name": "A", "description": description}, client_mutation_id="design")
    assert result["asset_record"][field] == expected
    model = await (assets.get_scene("A") if kind == "scenes" else assets.get_prop("A"))
    assert getattr(model, field) == expected
    assert model.description == description
    if kind == "scenes":
        from novelvideo.generators.scene_reference_images import _scene_context
        prompt = _scene_context(model)
    else:
        from novelvideo.task_backend.runners.prop_reference import _prop_reference_prompt
        prompt = _prop_reference_prompt(style="ink", visual_prompt=model.visual_prompt or model.description)
    assert expected in prompt
    assert "揭露秘密" not in getattr(model, field)
    if kind == 'scenes':
        assert '揭露秘密' in prompt  # Full business context is a separate prompt section.
    assert "第六集" not in getattr(model, field)
    assert "第七集" not in getattr(model, field)


@pytest.mark.parametrize("kind,field", [("scenes", "environment_prompt"), ("props", "visual_prompt")])
async def test_unlabeled_text_does_not_invent_visual_contract(setup, kind, field):
    store, service, _ = setup
    doc, ent = await entity(store, service, kind)
    description = "剧情作用：寻找旧物；外观待定。\n- **未知字段：**不要推断"
    result = await service.put(entity_id=ent["entity_id"], document_id=doc.id,
        base_revision_id=doc.current_revision_id, block_id=ent["block_id"], name="A",
        create_text={"name": "A", "description": description}, client_mutation_id="unknown")
    assert result["asset_record"][field] == ""
    assert result["asset_record"]["description"] == description


def test_design_prompt_preserves_field_values_and_accepts_label_markup():
    from novelvideo.script_creation.entities import _design_prompt
    assert _design_prompt("prop", "- **外观材质**：表盘刻有 12:30\n剧情作用：揭密") == "外观材质：表盘刻有 12:30"
    assert _design_prompt("scene", "空间布局: 北窗南门\n  后续尚未确定\n未知：保留原文") == "空间布局：北窗南门"
