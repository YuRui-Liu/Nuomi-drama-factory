import asyncio
import importlib.util

import pytest


def test_document_store_available():
    assert importlib.util.find_spec("novelvideo.script_creation") is not None


@pytest.fixture
async def store(tmp_path):
    from novelvideo.script_creation.store import DocumentStore
    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    return store


async def test_immutable_revision_restore_and_idempotency(store):
    doc = await store.create(kind="brief", title="创作简报", markdown="甲\n\n乙", client_mutation_id="create")
    again = await store.create(kind="brief", title="创作简报", markdown="甲\n\n乙", client_mutation_id="create")
    assert again == doc
    saved = await store.save(doc.id, base_revision_id=doc.current_revision_id, markdown="甲\n\n丙", client_mutation_id="save")
    replay = await store.save(doc.id, base_revision_id=doc.current_revision_id, markdown="甲\n\n丙", client_mutation_id="save")
    assert replay == saved
    assert saved.revision.blocks[0].id == doc.revision.blocks[0].id
    assert saved.revision.blocks[1].id == doc.revision.blocks[1].id
    restored = await store.restore(doc.id, revision_id=doc.current_revision_id, base_revision_id=saved.current_revision_id, client_mutation_id="restore")
    assert restored.revision.markdown == doc.revision.markdown
    assert restored.current_revision_id != doc.current_revision_id
    assert restored.adopted_revision_id is None
    history = await store.revisions(doc.id)
    assert len(history) == 3
    assert history[0] == doc.revision


async def test_concurrent_saves_have_exactly_one_winner(store):
    from novelvideo.script_creation.store import DocumentConflict
    doc = await store.create(kind="outline", title="大纲", client_mutation_id="create")
    results = await asyncio.gather(*(store.save(doc.id, base_revision_id=doc.current_revision_id,
        markdown=str(i), client_mutation_id=f"save-{i}") for i in range(2)), return_exceptions=True)
    assert sum(isinstance(item, DocumentConflict) for item in results) == 1
    assert len(await store.revisions(doc.id)) == 2


async def test_mutation_key_cannot_silently_accept_different_payload(store):
    from novelvideo.script_creation.store import DocumentConflict
    doc = await store.create(kind="outline", title="大纲", client_mutation_id="create")
    await store.save(doc.id, base_revision_id=doc.current_revision_id, markdown="A", client_mutation_id="save")
    with pytest.raises(DocumentConflict):
        await store.save(doc.id, base_revision_id=doc.current_revision_id, markdown="B", client_mutation_id="save")


async def test_import_copies_source_and_never_overwrites_it(tmp_path, store):
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.episode_sources import build_episode_candidate
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.script_creation.documents import import_episode_source
    sqlite = SQLiteStore("demo", str(tmp_path / "project"), str(tmp_path / "state"))
    await sqlite.initialize()
    try:
        sources = EpisodeSourceStore(sqlite)
        original = "第1集\n原始内容"
        await sources.upsert_sources([build_episode_candidate("E01.md", original)], expected_revision=0, canonical_novel=original)
        source = (await sources.list_sources())[0]
        doc = await import_episode_source(store, sources, 1)
        assert doc.revision.markdown == original
        assert doc.source_origin["content_hash"] == source.content_hash
        assert doc.source_origin["source_revision"] == source.source_revision
        assert (await import_episode_source(store, sources, 1)).id == doc.id
        await store.save(doc.id, base_revision_id=doc.current_revision_id, markdown="自由编辑", client_mutation_id="edit")
        assert (await import_episode_source(store, sources, 1)).revision.markdown == "自由编辑"
        assert (await sources.list_sources())[0] == source
        assert (tmp_path / "project" / "novel.txt").read_text() == original
    finally:
        await sqlite.close()


async def test_inserting_paragraph_preserves_unmodified_block_ids(store):
    doc = await store.create(kind="outline", title="大纲", markdown="甲\n\n乙", client_mutation_id="create")
    first, second = doc.revision.blocks
    inserted = await store.save(
        doc.id, base_revision_id=doc.current_revision_id,
        markdown="甲\n\n新增\n\n乙", client_mutation_id="insert",
    )
    assert "".join(block.markdown for block in inserted.revision.blocks) == inserted.revision.markdown
    assert inserted.revision.blocks[0].id == first.id
    assert inserted.revision.blocks[2].id == second.id
    assert inserted.revision.blocks[1].id not in {first.id, second.id}


async def test_idempotent_replay_returns_original_revision_after_later_save(store):
    doc = await store.create(kind="brief", title="简报", markdown="初版", client_mutation_id="create")
    saved = await store.save(
        doc.id, base_revision_id=doc.current_revision_id, markdown="二版", client_mutation_id="save",
    )
    await store.save(
        doc.id, base_revision_id=saved.current_revision_id, markdown="三版", client_mutation_id="later",
    )
    replay = await store.save(
        doc.id, base_revision_id=doc.current_revision_id, markdown="二版", client_mutation_id="save",
    )
    assert replay == saved


async def test_explicit_block_ids_validate_duplicates_and_foreign_ids(store):
    from novelvideo.script_creation.store import DocumentValidation
    doc = await store.create(kind="outline", title="大纲", markdown="A", client_mutation_id="create")
    block_id = doc.revision.blocks[0].id
    with pytest.raises(DocumentValidation):
        await store.save(doc.id, base_revision_id=doc.current_revision_id, markdown="AB",
            client_mutation_id="duplicate", blocks=[
                {"id": block_id, "markdown": "A"}, {"id": block_id, "markdown": "B"}])
    with pytest.raises(DocumentValidation):
        await store.save(doc.id, base_revision_id=doc.current_revision_id, markdown="A",
            client_mutation_id="foreign", blocks=[{"id": "other-document-block", "markdown": "A"}])
    saved = await store.save(doc.id, base_revision_id=doc.current_revision_id, markdown="AB",
        client_mutation_id="new", blocks=[
            {"id": block_id, "markdown": "A"}, {"id": "", "markdown": "B"}])
    assert saved.revision.blocks[0].id == block_id
    assert saved.revision.blocks[1].id != block_id


async def test_create_with_generated_explicit_block_id_replays_same_payload(store):
    doc = await store.create(kind="brief", title="简报", markdown="甲",
        client_mutation_id="create", blocks=[{"id": "", "markdown": "甲"}])
    replay = await store.create(kind="brief", title="简报", markdown="甲",
        client_mutation_id="create", blocks=[{"id": "", "markdown": "甲"}])
    assert replay == doc
    assert replay.revision.blocks[0].id


async def test_empty_document_rejects_block_id_from_another_document(store):
    from novelvideo.script_creation.store import DocumentValidation
    owner = await store.create(kind="brief", title="Owner", markdown="owner", client_mutation_id="owner")
    empty = await store.create(kind="brief", title="Empty", client_mutation_id="empty")
    with pytest.raises(DocumentValidation):
        await store.save(empty.id, base_revision_id=empty.current_revision_id,
            markdown="borrowed", client_mutation_id="borrow",
            blocks=[{"id": owner.revision.blocks[0].id, "markdown": "borrowed"}])


async def test_restore_recovers_historical_block_ids_after_deletion(store):
    doc = await store.create(kind="outline", title="Outline", markdown="甲\n\n乙",
        client_mutation_id="create")
    shortened = await store.save(doc.id, base_revision_id=doc.current_revision_id,
        markdown="甲", client_mutation_id="shorten")
    restored = await store.restore(doc.id, revision_id=doc.current_revision_id,
        base_revision_id=shortened.current_revision_id, client_mutation_id="restore")
    assert restored.revision.blocks == doc.revision.blocks
    assert restored.revision.restored_from_revision_id == doc.current_revision_id


async def test_mutation_payload_conflict_identifies_current_head(store):
    from novelvideo.script_creation.store import DocumentConflict
    doc = await store.create(kind="brief", title="Brief", client_mutation_id="create")
    first = await store.save(doc.id, base_revision_id=doc.current_revision_id,
        markdown="A", client_mutation_id="save")
    latest = await store.save(doc.id, base_revision_id=first.current_revision_id,
        markdown="B", client_mutation_id="later")
    with pytest.raises(DocumentConflict) as save_error:
        await store.save(doc.id, base_revision_id=doc.current_revision_id,
            markdown="different", client_mutation_id="save")
    assert save_error.value.current_revision_id == latest.current_revision_id
    with pytest.raises(DocumentConflict) as create_error:
        await store.create(kind="outline", title="Different", client_mutation_id="create")
    assert create_error.value.current_revision_id == latest.current_revision_id


async def test_create_response_stays_at_its_committed_revision_when_next_writer_advances(store, monkeypatch):
    import aiosqlite
    from novelvideo.script_creation.store import DocumentStore

    other = DocumentStore(store.db_path)
    await other.initialize()
    committed = asyncio.Event()
    resume = asyncio.Event()
    original_commit = aiosqlite.Connection.commit
    first_commit = True

    async def pause_after_first_commit(connection):
        nonlocal first_commit
        await original_commit(connection)
        if first_commit:
            first_commit = False
            committed.set()
            await resume.wait()

    monkeypatch.setattr(aiosqlite.Connection, "commit", pause_after_first_commit)
    task = asyncio.create_task(store.create(kind="brief", title="Brief",
        markdown="A", client_mutation_id="create"))
    try:
        await asyncio.wait_for(committed.wait(), 2)
        visible = (await other.list())[0]
        advanced = await other.save(visible.id, base_revision_id=visible.current_revision_id,
            markdown="B", client_mutation_id="next")
    finally:
        resume.set()
    created = await task
    assert created.current_revision_id == created.revision.id == visible.current_revision_id
    assert created.revision.markdown == "A"
    assert advanced.current_revision_id != created.current_revision_id


async def test_save_response_stays_at_its_committed_revision_when_next_writer_advances(store, monkeypatch):
    import aiosqlite
    from novelvideo.script_creation.store import DocumentStore

    doc = await store.create(kind="brief", title="Brief", markdown="A", client_mutation_id="create")
    other = DocumentStore(store.db_path)
    await other.initialize()
    committed = asyncio.Event()
    resume = asyncio.Event()
    original_commit = aiosqlite.Connection.commit
    first_commit = True

    async def pause_after_first_commit(connection):
        nonlocal first_commit
        await original_commit(connection)
        if first_commit:
            first_commit = False
            committed.set()
            await resume.wait()

    monkeypatch.setattr(aiosqlite.Connection, "commit", pause_after_first_commit)
    task = asyncio.create_task(store.save(doc.id, base_revision_id=doc.current_revision_id,
        markdown="B", client_mutation_id="save"))
    try:
        await asyncio.wait_for(committed.wait(), 2)
        visible = await other.get(doc.id)
        advanced = await other.save(doc.id, base_revision_id=visible.current_revision_id,
            markdown="C", client_mutation_id="next")
    finally:
        resume.set()
    saved = await task
    assert saved.current_revision_id == saved.revision.id == visible.current_revision_id
    assert saved.revision.markdown == "B"
    assert advanced.current_revision_id != saved.current_revision_id
