import pytest

from novelvideo.script_creation.store import DocumentStore, DocumentValidation


@pytest.fixture
async def store(tmp_path):
    result = DocumentStore(tmp_path / 'scene-identities.db')
    await result.initialize()
    return result


def identity(identifier, ordinal, heading, parents=()):
    return {'id': identifier, 'ordinal': ordinal, 'heading': heading, 'parent_ids': list(parents)}


async def test_scene_reorder_and_merge_preserve_historical_identity(store):
    doc = await store.create(kind='episode_script', episode_number=1, title='第一集',
        markdown='1-1 门厅 夜\n甲：第一场。\n\n1-2 楼道 夜\n乙：第二场。', client_mutation_id='create')
    first, second = doc.revision.scene_identities
    reordered = await store.save(doc.id, base_revision_id=doc.current_revision_id,
        markdown='1-1 楼道 夜\n乙：第二场。\n\n1-2 门厅 夜\n甲：第一场。', client_mutation_id='reorder',
        scene_identity_update=[identity(second.id, 1, '1-1 楼道 夜'), identity(first.id, 2, '1-2 门厅 夜')])
    assert [item.id for item in (await store.get(doc.id)).revision.scene_identities] == [second.id, first.id]
    merged = await store.save(doc.id, base_revision_id=reordered.current_revision_id,
        markdown='1-1 楼道 夜\n乙：第二场。\n\n甲：第一场。', client_mutation_id='merge',
        scene_identity_update=[identity('scene-merged', 1, '1-1 楼道 夜', (second.id, first.id))])
    assert merged.revision.scene_identities[0].parent_ids == (second.id, first.id)
    original = await store.get_revision(doc.id, doc.current_revision_id)
    assert [item.id for item in original.revision.scene_identities] == [first.id, second.id]
    restored = await store.restore(doc.id, revision_id=doc.current_revision_id,
        base_revision_id=merged.current_revision_id, client_mutation_id='restore')
    assert restored.revision.scene_identities == doc.revision.scene_identities


@pytest.mark.parametrize('parent_kind', ['self', 'foreign'])
async def test_invalid_lineage_cannot_change_saved_script(store, parent_kind):
    doc = await store.create(kind='episode_script', episode_number=1, title='第一集',
        markdown='1-1 门厅 夜\n甲：原文。', client_mutation_id='create')
    original = doc.revision.scene_identities[0]
    parent = original.id if parent_kind == 'self' else 'unknown-scene'
    with pytest.raises(DocumentValidation):
        await store.save(doc.id, base_revision_id=doc.current_revision_id,
            markdown='1-1 门厅 夜\n甲：错误覆盖。', client_mutation_id='invalid',
            scene_identity_update=[identity(original.id, 1, '1-1 门厅 夜', (parent,))])
    assert (await store.get(doc.id)).current_revision_id == doc.current_revision_id
