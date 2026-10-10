import json

import pytest

from novelvideo.script_creation.generation import GenerationService
from novelvideo.script_creation.store import DocumentStore, DocumentConflict
from novelvideo.script_creation.proposals import ProposalService


class CraftRuntime:
    def __init__(self, blocked=False, fail_stage=None):
        self.calls = []
        self.blocked, self.fail_stage = blocked, fail_stage

    async def run_structured(self, *, prompt, output_type, **kwargs):
        try:
            request = json.loads(prompt)
        except ValueError:
            request = {'stage': 'draft', 'task': prompt}
        self.calls.append(request)
        if request['stage'] == self.fail_stage:
            raise RuntimeError('offline')
        if 'review' in request['stage']:
            draft = request['draft']
            quote = draft.strip()
            return output_type(summary='逐场检查完成', coverage=['因果、选择、对白、连续性'],
                strengths=[dict(quote=quote, reason='保留已有行动')],
                issues=[dict(quote=quote, severity='blocker', dimension='因果', reason='人物无依据知道秘密',
                             repair_goal='补充信息获取路径')] if self.blocked else [])
        text = '# 新设计\n\n阿宁拒绝交出钥匙，承担失去工作的代价，迫使对方先释放人质。'
        return output_type(markdown=text,repair_notes=[dict(issue_id=i['id'],diagnosis=i['reason'],
            strategy='调整信息来源',changed_quote=text) for i in (request.get('review') or {}).get('issues',[]) if i['severity']=='blocker'])


@pytest.fixture
async def setup(tmp_path):
    store = DocumentStore(tmp_path / 'data.db')
    await store.initialize()
    brief = await store.create(kind='brief', title='简报', markdown='单篇短片，人质营救', client_mutation_id='b')
    service = GenerationService(store)
    return store, brief, service


async def start(brief, service):
    return await service.start(mode='bootstrap', brief_id=brief.id, script_mode='single',
                               episode_count=1, instruction='', mutation_id='run')


async def test_blocked_planning_is_retained_without_publishing_or_downstream(setup):
    store, brief, service = setup
    run = await start(brief, service)
    runtime = CraftRuntime(blocked=True)
    result = await service.execute(run['id'], runtime=runtime, task_id='t')
    assert result['status'] == 'needs_review'
    assert [d.kind for d in await store.list()] == ['brief']
    assert len(runtime.calls) == 4
    from novelvideo.script_creation.craft_store import craft_details
    details = await craft_details(store, 'generation', run['id'])
    assert details['records'][0]['output']['markdown'].startswith('# 新设计')
    assert details['steps'][0]['quality']['status'] == 'needs_revision'


async def test_resume_after_reviewer_failure_keeps_draft(setup):
    store, brief, service = setup
    run = await start(brief, service)
    with pytest.raises(RuntimeError, match='offline'):
        await service.execute(run['id'], runtime=CraftRuntime(fail_stage='structure_review'), task_id='t')
    next_runtime = CraftRuntime()
    result = await service.execute(run['id'], runtime=next_runtime, task_id='t2')
    assert result['status'] == 'completed'
    assert next_runtime.calls[0]['stage'] == 'structure_review'


async def test_candidate_design_flows_downstream_and_requires_adoption(setup):
    store, brief, service = setup
    outline = await store.create(kind='outline', title='旧大纲', markdown='旧大纲：主角从来不曾接触钥匙。', client_mutation_id='o')
    run = await start(brief, service)
    runtime = CraftRuntime()
    result = await service.execute(run['id'], runtime=runtime, task_id='t')
    people_call = [c for c in runtime.calls if c['stage'] == 'draft'][1]
    assert '阿宁拒绝交出钥匙' in people_call['task']
    assert '候选计划' in people_call['task']
    candidate = result['steps'][1]['output']
    assert candidate['kind'] == 'candidate'
    with pytest.raises(DocumentConflict, match='上游'):
        await ProposalService(store).from_candidate(candidate['candidate_id'])
    first = await ProposalService(store).from_candidate(result['steps'][0]['output']['candidate_id'])
    await ProposalService(store).accept([first['id']], base_revision_id=outline.current_revision_id, client_mutation_id='accept-o')
    second = await ProposalService(store).from_candidate(candidate['candidate_id'])
    target = await store.get(second['document_id'])
    accepted = await ProposalService(store).accept([second['id']], base_revision_id=target.current_revision_id, client_mutation_id='accept-p')
    assert '阿宁' in accepted.revision.markdown


async def test_old_task_cannot_mark_new_task_needs_rebase(setup):
    store, brief, service = setup
    run = await start(brief, service)
    await store.generation_claim(run['id'], 'new')
    with pytest.raises(DocumentConflict):
        await service.execute(run['id'], runtime=CraftRuntime(), task_id='old')
    assert (await store.generation_get(run['id']))['task_id'] == 'new'


async def test_local_rewrite_is_reviewed_without_publishing_failed_proposal(setup):
    from novelvideo.script_creation.rewrite import RewriteService
    store, _, _ = setup
    doc = await store.create(kind='episode_script', title='原稿', markdown='甲：你早知道了？', client_mutation_id='s')
    service = RewriteService(store)
    job = await service.start(document_id=doc.id, base_revision_id=doc.current_revision_id,
        start=0, end=len(doc.revision.markdown), scope='selection', mode='dialogue',
        instruction='自然一点', preserve='', client_mutation_id='r')
    result = await service.execute(job['id'], runtime=CraftRuntime(blocked=True), task_id='r')
    assert result['status'] == 'needs_review'
    assert not await service.proposals.list(doc.id)
    from novelvideo.script_creation.craft_memory import CraftMemory
    memory = CraftMemory(store)
    assert await memory.snapshot('episode_script')
    await store.save(doc.id,base_revision_id=doc.current_revision_id,markdown='甲已经收到答案。',client_mutation_id='edit-memory-source')
    assert not await memory.snapshot('episode_script')


async def test_rewrite_resume_preserves_draft_and_full_context(setup):
    from novelvideo.script_creation.rewrite import RewriteService
    from novelvideo.script_creation.craft_store import craft_details
    store, _, _ = setup
    text = '甲：不知道。\n\n第二场：甲刚从乙口中得知钥匙在井底。'
    doc = await store.create(kind='episode_script', title='原稿', markdown=text, client_mutation_id='s')
    service = RewriteService(store)
    job = await service.start(document_id=doc.id, base_revision_id=doc.current_revision_id,
        start=0, end=6, scope='selection', mode='dialogue', instruction='', preserve='', client_mutation_id='r')
    first = CraftRuntime(fail_stage='structure_review')
    with pytest.raises(RuntimeError):
        await service.execute(job['id'], runtime=first, task_id='r')
    assert text in first.calls[0]['task']
    await service.retry(job['id'])
    second = CraftRuntime()
    await service.execute(job['id'], runtime=second, task_id='r2')
    assert second.calls[0]['stage'] == 'structure_review'
    assert not (await craft_details(store, 'rewrite', job['id']))['stale']
    await store.save(doc.id, base_revision_id=doc.current_revision_id, markdown=text+'已改', client_mutation_id='edit')
    assert (await craft_details(store, 'rewrite', job['id']))['stale']


async def test_cancelled_rewrite_is_paused_and_resumes_checkpoint(setup):
    from novelvideo.script_creation.rewrite import RewriteService
    from novelvideo.script_creation.craft_store import CraftJournal
    from novelvideo.task_backend.cancel import TaskCancelled
    store, _, _ = setup
    doc = await store.create(kind='episode_script', title='原稿', markdown='甲：留下。', client_mutation_id='s')
    service = RewriteService(store)
    job = await service.start(document_id=doc.id, base_revision_id=doc.current_revision_id,
        start=0,end=5,scope='selection',mode='dialogue',instruction='',preserve='',client_mutation_id='r')
    async def cancel_after_draft():
        if await CraftJournal(store, 'rewrite', job['id'], 'rewrite', 't').records():
            raise TaskCancelled()
    with pytest.raises(TaskCancelled):
        await service.execute(job['id'], runtime=CraftRuntime(), task_id='t', cancel_check=cancel_after_draft)
    assert (await service.get(job['id']))['status'] == 'paused'
    await service.retry(job['id'])
    runtime = CraftRuntime()
    assert (await service.execute(job['id'], runtime=runtime, task_id='t2'))['status'] == 'completed'
    assert runtime.calls[0]['stage'] == 'structure_review'
