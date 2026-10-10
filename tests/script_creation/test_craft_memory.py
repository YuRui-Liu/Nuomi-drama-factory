import pytest
from novelvideo.script_creation.store import DocumentStore


async def test_memory_is_project_scoped_kind_scoped_and_revocable(tmp_path):
    from novelvideo.script_creation.craft_memory import CraftMemory
    one, two = DocumentStore(tmp_path/'one.db'), DocumentStore(tmp_path/'two.db')
    await one.initialize(); await two.initialize()
    memory = CraftMemory(one)
    await memory.remember(kind='people', source='generation:g:people', records=[
        {'stage':'structure_review','output':{'summary':'缺少依据','issues':[{'id':'i','quote':'她知道答案','reason':'没有获知过程',
            'repair_goal':'补充信息来源','severity':'blocker','dimension':'信息边界'}]}}], references={})
    entries = await memory.list(kind='people')
    assert len(entries) == 1
    assert not await CraftMemory(two).list(kind='people')
    assert not await memory.list(kind='scenes')
    await memory.dismiss(entries[0]['id'], '审稿忽略了前文，撤销这条意见')
    assert not await memory.snapshot('people')
    assert (await memory.list(kind='people'))[0]['dismissal_reason'].startswith('审稿')


async def test_memory_with_changed_reference_is_excluded(tmp_path):
    from novelvideo.script_creation.craft_memory import CraftMemory
    store = DocumentStore(tmp_path/'one.db'); await store.initialize()
    doc = await store.create(kind='people',title='人物',markdown='不知道答案',client_mutation_id='d')
    memory = CraftMemory(store)
    await memory.remember(kind='episode_script',source='generation:g:e',references={doc.id:doc.current_revision_id},
        records=[{'stage':'structure_review','output':{'summary':'信息越界','issues':[{'id':'i','reason':'提前知道','quote':'知道答案','severity':'blocker'}]}}])
    assert await memory.snapshot('episode_script')
    await store.save(doc.id,base_revision_id=doc.current_revision_id,markdown='已收到答案',client_mutation_id='edit')
    assert not await memory.snapshot('episode_script')


async def test_task_freezes_memory_and_revocation_invalidates_frozen_context(tmp_path):
    from novelvideo.script_creation.craft_memory import CraftMemory
    from novelvideo.script_creation.craft_store import CraftJournal
    from novelvideo.script_creation.store import DocumentConflict
    store = DocumentStore(tmp_path/'one.db'); await store.initialize()
    async def owner(mutation):
        run = await store.generation_start({'mode':'bootstrap','brief_id':'b','script_mode':'single',
            'episode_count':1,'episode_number':1,'instruction':'','status':'pending',
            'steps':[{'key':'people:0','status':'pending'}]},mutation)
        await store.generation_claim(run['id'],'task')
        return CraftJournal(store,'generation',run['id'],'people:0','task')
    first = await owner('first')
    assert (await first.context('people'))['memory'] == []
    memory = CraftMemory(store)
    records = [{'stage':'structure_review','output':{'summary':'旧方法失败','issues':[{'id':'i','quote':'她知道','reason':'无依据','severity':'blocker'}]}}]
    await memory.remember(kind='people',source='source',records=records,references={})
    assert (await first.context('people'))['memory'] == []
    second = await owner('second')
    frozen = await second.context('people')
    assert len(frozen['memory']) == 1
    assert (await second.context('people'))['author_session']['id'] == frozen['author_session']['id']
    await memory.dismiss(frozen['memory'][0]['id'],'原审稿忽略前文')
    with pytest.raises(DocumentConflict,match='撤销'):
        await second.validate_memory()
    await memory.remember(kind='people',source='source',records=records,references={})
    assert not await memory.snapshot('people')  # another checkpoint cannot resurrect dismissed advice
