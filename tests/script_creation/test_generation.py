import pytest
from novelvideo.script_creation.generation import GenerationService, GenerationConflict, GenerationValidation
from novelvideo.script_creation.store import DocumentStore

@pytest.fixture
async def store(tmp_path):
    value = DocumentStore(tmp_path / 'data.db')
    await value.initialize()
    return value

class Runtime:
    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at
    async def run_structured(self, *, prompt, output_type, system_prompt, **kwargs):
        self.calls.append(prompt)
        if len(self.calls) == self.fail_at:
            raise RuntimeError('model unavailable')
        return output_type(markdown=f'# 生成 {len(self.calls)}\n\n人物做出选择，冲突随之升级。')

async def test_series_bootstrap_resumes_after_failure_without_redoing_completed_steps(store):
    brief = await store.create(kind='brief', title='简报', markdown='都市悬疑，三集', client_mutation_id='brief')
    service = GenerationService(store)
    run = await service.start(mode='bootstrap', brief_id=brief.id, script_mode='series', episode_count=3, instruction='节奏紧凑', mutation_id='start')
    with pytest.raises(RuntimeError, match='model unavailable'):
        await service.execute(run['id'], runtime=Runtime(fail_at=3), task_id='task-a')
    saved = await service.get(run['id'])
    assert [step['status'] for step in saved['steps']][:3] == ['completed', 'completed', 'failed']
    runtime = Runtime()
    completed = await service.execute(run['id'], runtime=runtime, task_id='task-b')
    assert completed['status'] == 'completed'
    assert len(runtime.calls) == 4
    assert [doc.kind for doc in await store.list()] == ['brief', 'outline', 'episode_synopsis', 'people', 'scenes', 'props', 'episode_script']

async def test_single_bootstrap_has_no_synopsis(store):
    brief = await store.create(kind='brief', title='简报', markdown='独立短片', client_mutation_id='brief')
    service = GenerationService(store)
    run = await service.start(mode='bootstrap', brief_id=brief.id, script_mode='single', episode_count=1, instruction='', mutation_id='single')
    finished = await service.execute(run['id'], runtime=Runtime(), task_id='task')
    assert finished['status'] == 'completed'
    assert 'episode_synopsis' not in [doc.kind for doc in await store.list()]

async def test_changed_brief_prevents_stale_commit(store):
    brief = await store.create(kind='brief', title='简报', markdown='旧设定', client_mutation_id='brief')
    service = GenerationService(store)
    run = await service.start(mode='bootstrap', brief_id=brief.id, script_mode='single', episode_count=1, instruction='', mutation_id='start')
    class EditingRuntime(Runtime):
        async def run_structured(self, **kwargs):
            await store.save(brief.id, base_revision_id=brief.current_revision_id, markdown='新设定', client_mutation_id='edit')
            return await super().run_structured(**kwargs)
    result = await service.execute(run['id'], runtime=EditingRuntime(), task_id='task')
    assert result['status'] == 'needs_rebase'
    assert [doc.kind for doc in await store.list()] == ['brief']

async def test_continue_uses_prior_script_and_existing_target_becomes_candidate(store):
    brief = await store.create(kind='brief', title='简报', markdown='三集短剧', client_mutation_id='brief')
    await store.create(kind='outline', title='大纲', markdown='# 大纲\n\n姐妹在旧城寻找失踪父亲。', client_mutation_id='outline')
    await store.create(kind='episode_synopsis', title='分集梗概', markdown='# 分集梗概\n\n第二集计划：两人前往码头。', client_mutation_id='synopsis')
    await store.create(kind='episode_script', title='第一集', episode_number=1, markdown='# 第一集\n\n主角在码头找到钥匙。', client_mutation_id='first')
    target = await store.create(kind='episode_script', title='第二集', episode_number=2, markdown='# 第二集\n\n作者已写正文。', client_mutation_id='second')
    service = GenerationService(store)
    run = await service.start(mode='continue', brief_id=brief.id, script_mode='series', episode_count=3, episode_number=2, instruction='保留克制风格', mutation_id='continue')
    runtime = Runtime()
    finished = await service.execute(run['id'], runtime=runtime, task_id='task')
    assert '码头找到钥匙' in runtime.calls[0]
    assert finished['steps'][0]['output']['kind'] == 'candidate'
    assert (await store.get(target.id)).revision.markdown == target.revision.markdown

async def test_same_mutation_replays_and_new_payload_conflicts(store):
    brief = await store.create(kind='brief', title='简报', markdown='A', client_mutation_id='brief')
    service = GenerationService(store)
    args = dict(mode='bootstrap', brief_id=brief.id, script_mode='single', episode_count=1, instruction='A', mutation_id='key')
    first = await service.start(**args)
    assert (await service.start(**args))['id'] == first['id']
    with pytest.raises(GenerationConflict):
        await service.start(**{**args, 'instruction': 'B'})

async def test_continue_requires_saved_outline_and_synopsis(store):
    brief = await store.create(kind='brief', title='简报', markdown='短剧', client_mutation_id='brief')
    await store.create(kind='episode_script', title='第一集', episode_number=1,
                       markdown='# 第一集\n\n主角找到了钥匙。', client_mutation_id='first')
    service = GenerationService(store)
    with pytest.raises(GenerationValidation, match='outline and target episode synopsis'):
        await service.start(mode='continue', brief_id=brief.id, script_mode='series', episode_count=3,
                            episode_number=2, instruction='', mutation_id='continue')

async def test_heading_only_author_draft_is_candidate_not_overwritten(store):
    brief = await store.create(kind='brief', title='简报', markdown='独立短片', client_mutation_id='brief')
    author = await store.create(kind='outline', title='自拟标题', markdown='# 我的私人故事结构', client_mutation_id='author')
    service = GenerationService(store)
    run = await service.start(mode='bootstrap', brief_id=brief.id, script_mode='single', episode_count=1,
                              instruction='', mutation_id='start')
    result = await service.execute(run['id'], runtime=Runtime(), task_id='task')
    assert result['steps'][0]['output']['kind'] == 'candidate'
    assert (await store.get(author.id)).current_revision_id == author.current_revision_id

async def test_same_run_cannot_be_executed_by_two_workers_at_once(store):
    import asyncio
    brief = await store.create(kind='brief', title='简报', markdown='独立短片', client_mutation_id='brief')
    service = GenerationService(store)
    run = await service.start(mode='bootstrap', brief_id=brief.id, script_mode='single', episode_count=1,
                              instruction='', mutation_id='start')
    entered, release = asyncio.Event(), asyncio.Event()
    class WaitingRuntime(Runtime):
        async def run_structured(self, **kwargs):
            entered.set()
            await release.wait()
            return await super().run_structured(**kwargs)
    task = asyncio.create_task(service.execute(run['id'], runtime=WaitingRuntime(), task_id='first'))
    await asyncio.wait_for(entered.wait(), 2)
    try:
        with pytest.raises(GenerationConflict):
            await service.execute(run['id'], runtime=Runtime(), task_id='second')
    finally:
        release.set()
    await task
    assert (await service.get(run['id']))['status'] == 'completed'

async def test_bootstrap_can_fill_existing_blank_template_and_continue(store):
    brief = await store.create(kind='brief', title='简报', markdown='独立短片', client_mutation_id='brief')
    template = '\n\n'.join([
        '# 故事大纲', '## Logline、核心看点与情绪曲线', '### Logline', '### 核心看点',
        '### 情绪曲线', '## 故事简述', '## 背景设定与世界规则', '## 全剧分段',
        '## 为什么能共情', '## 种子情节与人物变化', '## 爽感桥段', '## 反转桥段', '## 创作禁区'])
    outline = await store.create(kind='outline', title='故事框架', markdown=template, client_mutation_id='template')
    service = GenerationService(store)
    run = await service.start(mode='bootstrap', brief_id=brief.id, script_mode='single', episode_count=1,
                              instruction='', mutation_id='start')
    finished = await service.execute(run['id'], runtime=Runtime(), task_id='task')
    assert finished['status'] == 'completed'
    assert finished['steps'][0]['output']['document_id'] == outline.id
    assert (await store.get(outline.id)).revision.markdown != template

async def test_cancel_preflight_does_not_wait_on_generation_write_lock(store):
    import asyncio
    import aiosqlite
    brief = await store.create(kind='brief', title='简报', markdown='独立短片', client_mutation_id='brief')
    service = GenerationService(store)
    run = await service.start(mode='bootstrap', brief_id=brief.id, script_mode='single', episode_count=1,
                              instruction='', mutation_id='start')
    checks = 0
    async def cancel_check():
        nonlocal checks
        checks += 1
        if checks == 2:
            async with aiosqlite.connect(store.db_path, timeout=0.2) as db:
                await db.execute('PRAGMA user_version=1')
                await db.commit()
    result = await asyncio.wait_for(service.execute(run['id'], runtime=Runtime(), task_id='task',
                                                     cancel_check=cancel_check), timeout=2)
    assert result['status'] == 'completed'
    assert checks >= 2
