import pytest

from novelvideo.script_creation.store import DocumentStore, DocumentConflict


@pytest.fixture
async def running(tmp_path):
    store = DocumentStore(tmp_path / 'data.db')
    await store.initialize()
    run = await store.generation_start(dict(mode='bootstrap', brief_id='b', script_mode='single',
        episode_count=1, episode_number=1, instruction='', steps=[{'key': 'outline:0', 'status': 'running'}],
        status='pending'), 'm')
    await store.generation_claim(run['id'], 'task')
    return store, run['id']


async def test_craft_checkpoint_survives_new_store_instance(running):
    from novelvideo.script_creation.craft_store import CraftJournal
    store, run_id = running
    journal = CraftJournal(store, 'generation', run_id, 'outline:0', 'task')
    record = dict(stage='draft', input_hash='in', output={'markdown': '候选正文'},
                  output_hash='out', elapsed_seconds=1.0)
    await journal.append(record)
    reopened = CraftJournal(DocumentStore(store.db_path), 'generation', run_id, 'outline:0', 'task')
    assert await reopened.records() == [record]


async def test_old_worker_cannot_checkpoint_after_task_replaced(running):
    from novelvideo.script_creation.craft_store import CraftJournal
    store, run_id = running
    journal = CraftJournal(store, 'generation', run_id, 'outline:0', 'old-task')
    with pytest.raises(DocumentConflict, match='superseded'):
        await journal.append(dict(stage='draft', input_hash='x', output={}, output_hash='y', elapsed_seconds=0))


async def test_checkpoint_cannot_overwrite_completed_call(running):
    from novelvideo.script_creation.craft_store import CraftJournal
    store, run_id = running
    journal = CraftJournal(store, 'generation', run_id, 'outline:0', 'task')
    record = dict(stage='draft', input_hash='in', output={'markdown': '初稿'}, output_hash='out', elapsed_seconds=1)
    await journal.append(record)
    await journal.append(record)
    with pytest.raises(DocumentConflict, match='checkpoint'):
        await journal.append({**record, 'output': {'markdown': '偷偷覆盖'}})
    assert await journal.records() == [record]


async def test_generation_orders_people_before_synopsis(tmp_path):
    from novelvideo.script_creation.generation import GenerationService
    store = DocumentStore(tmp_path / 'data.db')
    await store.initialize()
    brief = await store.create(kind='brief', title='简报', markdown='三集悬疑', client_mutation_id='brief')
    run = await GenerationService(store).start(mode='bootstrap', brief_id=brief.id,
        script_mode='series', episode_count=3, instruction='', mutation_id='start')
    kinds = [s['kind'] for s in run['steps']]
    assert kinds.index('people') < kinds.index('episode_synopsis')
