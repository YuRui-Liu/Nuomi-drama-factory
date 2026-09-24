import importlib.util
from types import SimpleNamespace
import pytest


@pytest.mark.asyncio
async def test_lost_enqueue_response_never_enqueues_twice(tmp_path):
    assert importlib.util.find_spec('novelvideo.character_visual.casting_submission'), 'submission journal missing'
    from novelvideo.character_visual.casting_submission import SubmissionJournal, submit_once
    ctx = SimpleNamespace(state_dir=tmp_path, project_id='p')
    journal = SubmissionJournal(ctx)
    calls = []
    async def enqueue(*a, **kw):
        calls.append(kw)
        raise TimeoutError('response lost after dispatch')
    manager = SimpleNamespace(get_task_for_project=lambda *a, **kw: SimpleNamespace(task_id='actual', status='running'))
    args = dict(ctx=ctx, journal=journal, operation='recast', character_id='甲', identity_id=None,
        idempotency_key='key', fingerprint='same', task_type='character_casting_proposals',
        payload={}, backend=SimpleNamespace(enqueue_project_task=enqueue), manager=manager)
    first = await submit_once(**args)
    second = await submit_once(**args)
    assert first['task_id'] == second['task_id'] == 'actual'
    assert len(calls) == 1
    with pytest.raises(ValueError, match='idempotency'):
        await submit_once(**{**args, 'fingerprint': 'different'})


@pytest.mark.asyncio
async def test_unresolved_submission_is_never_blindly_retried(tmp_path):
    assert importlib.util.find_spec('novelvideo.character_visual.casting_submission'), 'submission journal missing'
    from novelvideo.character_visual.casting_submission import SubmissionJournal, submit_once
    ctx = SimpleNamespace(state_dir=tmp_path, project_id='p')
    calls = []
    async def enqueue(*a, **kw):
        calls.append(kw)
        raise TimeoutError('unknown')
    args = dict(ctx=ctx, journal=SubmissionJournal(ctx), operation='generate', character_id='甲', identity_id=None,
        idempotency_key='key', fingerprint='same', task_type='character_portrait', payload={},
        backend=SimpleNamespace(enqueue_project_task=enqueue),
        manager=SimpleNamespace(get_task_for_project=lambda *a, **kw: None))
    assert (await submit_once(**args))['status'] == 'submission_unknown'
    assert (await submit_once(**args))['status'] == 'submission_unknown'
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_recast_execution_is_claimed_once_before_any_model_call(tmp_path):
    from novelvideo.character_visual.casting_submission import SubmissionJournal, submit_once
    ctx = SimpleNamespace(state_dir=tmp_path, project_id='p')
    journal = SubmissionJournal(ctx)
    dispatched = []
    async def enqueue(*args, **kwargs):
        dispatched.append(kwargs)
        return SimpleNamespace(task_state=SimpleNamespace(task_id='actual', status='queued'))
    await submit_once(ctx=ctx, journal=journal, operation='recast', character_id='甲', identity_id=None,
        idempotency_key='k', fingerprint='f', task_type='character_casting_proposals', payload={'character_id': '甲'},
        backend=SimpleNamespace(enqueue_project_task=enqueue), manager=SimpleNamespace(get_task_for_project=lambda *a, **kw: None))
    row = journal.list('甲', None)[0]
    assert hasattr(journal, 'claim_execution'), 'recast must claim before model calls'
    kwargs = dict(request_id=row['request_id'], submission_token=row['submission_token'], task_id='actual',
        task_type=row['task_type'], scope=row['scope'], payload=dispatched[0]['payload'])
    assert journal.claim_execution(**kwargs)['execution_status'] == 'running'
    with pytest.raises(ValueError, match='uncertain'):
        journal.claim_execution(**kwargs)
    journal.finish_execution(row['request_id'], task_id='actual', result={'revision_id': 'v'})
    assert journal.claim_execution(**kwargs)['result'] == {'revision_id': 'v'}
    with pytest.raises(ValueError, match='task'):
        journal.claim_execution(**{**kwargs, 'task_id': 'other'})
