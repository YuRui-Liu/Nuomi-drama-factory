from pathlib import Path
from types import SimpleNamespace

from novelvideo.script_creation.store import DocumentStore
from novelvideo.task_backend.registry import get_project_task_runner_registration


async def test_registered_runner_uses_frozen_runtime_and_persists_output(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import script_creation
    from novelvideo.script_creation.generation import GenerationService

    store = DocumentStore(tmp_path / 'data.db')
    await store.initialize()
    brief = await store.create(kind='brief', title='简报', markdown='一个完整短片', client_mutation_id='brief')
    run = await GenerationService(store).start(mode='bootstrap', brief_id=brief.id, script_mode='single',
                                               episode_count=1, instruction='', mutation_id='run')
    called = []
    class Runtime:
        snapshot = SimpleNamespace(runtime='model_api', model='configured-model')
        async def run_structured(self, *, prompt, output_type, **kwargs):
            called.append(prompt)
            return output_type(markdown='# 剧本\n\n主角在雨夜做出决定，真相终于揭开。')
    monkeypatch.setattr(script_creation, 'current_text_task_runtime', lambda: Runtime())
    monkeypatch.setattr(script_creation, 'is_cancel_requested', lambda **kwargs: __import__('asyncio').sleep(0, result=False))
    monkeypatch.setattr(script_creation, 'get_task_manager', lambda: SimpleNamespace(
        get_task_for_project=lambda *args, **kwargs: SimpleNamespace(task_id='task', status='running'),
        update_progress_for_project=lambda *args, **kwargs: None))
    ctx = SimpleNamespace(project_id='project', state_dir=tmp_path)
    envelope = {'project_id': 'project', 'task_type': 'script_creation_generation', 'episode': 0,
                'scope': f'run:{run["id"]}', '__run_task_id': 'task',
                'payload': {'project_id': 'project', 'run_id': run['id']}}
    result = await script_creation._run_script_creation(envelope, ctx)
    assert result['status'] == 'completed'
    assert len(called) == 5
    assert get_project_task_runner_registration('script_creation_generation').text_task_role == 'script_creation'
    assert len([doc for doc in await store.list() if doc.kind == 'episode_script']) == 1

async def test_missing_runtime_persists_actionable_failure_before_model_call(tmp_path, monkeypatch):
    import pytest
    from novelvideo.task_backend.runners import script_creation
    from novelvideo.script_creation.generation import GenerationService
    store = DocumentStore(tmp_path / 'data.db')
    await store.initialize()
    brief = await store.create(kind='brief', title='简报', markdown='独立短片', client_mutation_id='brief')
    run = await GenerationService(store).start(mode='bootstrap', brief_id=brief.id, script_mode='single',
                                               episode_count=1, instruction='', mutation_id='run')
    monkeypatch.setattr(script_creation, 'current_text_task_runtime', lambda: None)
    ctx = SimpleNamespace(project_id='project', state_dir=tmp_path)
    envelope = {'project_id': 'project', 'scope': f'run:{run["id"]}', '__run_task_id': 'task',
                'payload': {'project_id': 'project', 'run_id': run['id']}}
    with pytest.raises(RuntimeError, match='模型路由'):
        await script_creation._run_script_creation(envelope, ctx)
    saved = await GenerationService(store).get(run['id'])
    assert saved['status'] == 'failed'
    assert '模型路由' in saved['error']
