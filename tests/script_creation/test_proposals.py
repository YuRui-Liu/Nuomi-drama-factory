import pytest

from novelvideo.script_creation.store import DocumentStore, DocumentConflict
from novelvideo.script_creation.proposals import ProposalService


@pytest.fixture
async def pair(tmp_path):
    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    doc = await store.create(kind="episode_script", title="第一集", markdown="同句😀\n\n同句😀", client_mutation_id="create")
    return store, doc, ProposalService(store)


async def test_exact_repeat_and_unicode_codepoint(pair):
    store, doc, service = pair
    second = doc.revision.blocks[1]
    proposal = await service.create(document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=second.id, start=0, end=3, before="同句😀", after="新句✨", reason="测试",
        round_id="r1", client_mutation_id="p1")
    changed = await service.accept([proposal["id"]], base_revision_id=doc.current_revision_id,
                                   client_mutation_id="accept")
    assert changed.revision.markdown == "同句😀\n\n新句✨"
    replay = await service.accept([proposal["id"]], base_revision_id=doc.current_revision_id,
                                  client_mutation_id="accept")
    assert replay.revision.id == changed.revision.id


async def test_changed_baseline_and_batch_conflict_leave_all_pending(pair):
    store, doc, service = pair
    a, b = doc.revision.blocks
    p1 = await service.create(document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=a.id, start=0, end=3, before="同句😀", after="甲", reason="测试",
        round_id="r1", client_mutation_id="p1")
    p2 = await service.create(document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=b.id, start=0, end=3, before="同句😀", after="乙", reason="测试",
        round_id="r1", client_mutation_id="p2")
    await store.save(doc.id, base_revision_id=doc.current_revision_id, markdown="变了\n\n同句😀", client_mutation_id="manual")
    with pytest.raises(DocumentConflict):
        await service.accept([p1["id"], p2["id"]], base_revision_id=doc.current_revision_id,
                             client_mutation_id="accept")
    assert [p["status"] for p in await service.list(doc.id)] == ["pending", "pending"]
    assert len(await store.revisions(doc.id)) == 2


async def test_overlapping_batch_rejected_atomically(pair):
    store, doc, service = pair
    block = doc.revision.blocks[0]
    p1 = await service.create(document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=block.id, start=0, end=2, before="同句", after="甲", reason="测试",
        round_id="r1", client_mutation_id="p1")
    p2 = await service.create(document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=block.id, start=1, end=3, before="句😀", after="乙", reason="测试",
        round_id="r1", client_mutation_id="p2")
    with pytest.raises(DocumentConflict):
        await service.accept([p1["id"], p2["id"]], base_revision_id=doc.current_revision_id,
                             client_mutation_id="accept")
    assert [p["status"] for p in await service.list(doc.id)] == ["pending", "pending"]
    assert len(await store.revisions(doc.id)) == 1


async def test_sequential_independent_proposals_follow_own_adoptions(pair):
    store, doc, service = pair
    a, b = doc.revision.blocks
    p1 = await service.create(document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=a.id, start=0, end=3, before="同句😀", after="甲", reason="测试",
        round_id="r1", client_mutation_id="p1")
    p2 = await service.create(document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=b.id, start=0, end=3, before="同句😀", after="乙", reason="测试",
        round_id="r1", client_mutation_id="p2")
    first = await service.accept([p1["id"]], base_revision_id=doc.current_revision_id, client_mutation_id="a1")
    second = await service.accept([p2["id"]], base_revision_id=first.current_revision_id, client_mutation_id="a2")
    assert second.revision.markdown == "甲\n\n乙"


async def test_single_overlapping_proposal_cannot_split_review_unit(pair):
    store, doc, service = pair
    block = doc.revision.blocks[0]
    p1 = await service.create(document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=block.id, start=0, end=2, before="同句", after="甲", reason="测试",
        round_id="r1", client_mutation_id="p1")
    await service.create(document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=block.id, start=1, end=3, before="句😀", after="乙", reason="测试",
        round_id="r1", client_mutation_id="p2")
    with pytest.raises(DocumentConflict):
        await service.accept([p1["id"]], base_revision_id=doc.current_revision_id,
                             client_mutation_id="a1")
    assert len(await store.revisions(doc.id)) == 1


async def test_sequential_same_block_offsets_shift_without_guessing(tmp_path):
    store = DocumentStore(tmp_path / "data.db")
    await store.initialize()
    doc = await store.create(kind="episode_script", title="一", markdown="甲乙丙丁", client_mutation_id="create")
    service = ProposalService(store)
    block = doc.revision.blocks[0]
    first = await service.create(document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=block.id, start=0, end=1, before="甲", after="长长", reason="测试",
        round_id="r", client_mutation_id="p1")
    second = await service.create(document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=block.id, start=2, end=3, before="丙", after="新", reason="测试",
        round_id="r", client_mutation_id="p2")
    changed = await service.accept([first["id"]], base_revision_id=doc.current_revision_id,
                                   client_mutation_id="a1")
    changed = await service.accept([second["id"]], base_revision_id=changed.current_revision_id,
                                   client_mutation_id="a2")
    assert changed.revision.markdown == "长长乙新丁"


async def test_adopted_pointer_survives_restoring_older_revision(pair):
    store, doc, service = pair
    block = doc.revision.blocks[0]
    proposal = await service.create(document_id=doc.id, base_revision_id=doc.current_revision_id,
        block_id=block.id, start=0, end=3, before="同句😀", after="新句", reason="测试",
        round_id="r1", client_mutation_id="p1")
    adopted = await service.accept([proposal["id"]], base_revision_id=doc.current_revision_id,
                                   client_mutation_id="accept")
    assert adopted.adopted_revision_id == adopted.current_revision_id
    restored = await store.restore(doc.id, revision_id=doc.current_revision_id,
        base_revision_id=adopted.current_revision_id, client_mutation_id="restore")
    assert restored.adopted_revision_id == adopted.current_revision_id
    assert restored.current_revision_id != adopted.current_revision_id
