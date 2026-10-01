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

async def test_continue_requires_substantive_target_episode_synopsis(store):
    brief = await store.create(kind='brief', title='简报', markdown='三集短剧', client_mutation_id='brief')
    await store.create(kind='outline', title='大纲', markdown='姐妹在旧城寻人。', client_mutation_id='outline')
    await store.create(kind='episode_synopsis', title='分集梗概',
                       markdown='# 分集梗概\n\n## 第 1 集\n\n两人发现钥匙。\n\n## 第 2 集\n\n### 本集目标',
                       client_mutation_id='synopsis')
    await store.create(kind='episode_script', title='第一集', episode_number=1,
                       markdown='# 第一集\n\n两人在码头发现钥匙。', client_mutation_id='first')
    service = GenerationService(store)
    with pytest.raises(GenerationValidation, match='target episode synopsis'):
        await service.start(mode='continue', brief_id=brief.id, script_mode='series', episode_count=3,
                            episode_number=2, instruction='', mutation_id='continue')

async def test_new_dependency_during_model_call_requires_rebase(store):
    brief = await store.create(kind='brief', title='简报', markdown='三集短剧', client_mutation_id='brief')
    await store.create(kind='outline', title='大纲', markdown='姐妹在旧城寻人。', client_mutation_id='outline')
    await store.create(kind='episode_synopsis', title='分集梗概',
                       markdown='第一集：找钥匙。\n\n第二集：姐妹在码头寻找证人。', client_mutation_id='synopsis')
    await store.create(kind='episode_script', title='第一集', episode_number=1,
                       markdown='# 第一集\n\n两人在码头发现钥匙。', client_mutation_id='first')
    service = GenerationService(store)
    run = await service.start(mode='continue', brief_id=brief.id, script_mode='series', episode_count=3,
                              episode_number=2, instruction='', mutation_id='continue')
    class NewDependency(Runtime):
        async def run_structured(self, **kwargs):
            await store.create(kind='people', title='人物小传', markdown='姐姐很谨慎。',
                               client_mutation_id='new-people')
            return await super().run_structured(**kwargs)
    result = await service.execute(run['id'], runtime=NewDependency(), task_id='task')
    assert result['status'] == 'needs_rebase'
    assert not any(doc.kind == 'episode_script' and doc.episode_number == 2 for doc in await store.list())

async def test_design_prompt_distinguishes_plans_from_written_facts(store):
    from novelvideo.script_creation.prompts import build_prompt
    people = await store.create(kind='people', title='人物小传', markdown='甲第十集首次登场。', client_mutation_id='people')
    scenes = await store.create(kind='scenes', title='场景设计', markdown='旧宅预计第十集使用。', client_mutation_id='scenes')
    props = await store.create(kind='props', title='道具设计', markdown='钥匙预计第十集出现。', client_mutation_id='props')
    script = await store.create(kind='episode_script', title='第一集', episode_number=1, markdown='第一集里甲尚未登场。', client_mutation_id='script')
    prompt = build_prompt(kind='episode_script', script_mode='series', episode_number=2, episode_count=10,
                          instruction='', references=[people, scenes, props, script])
    assert '设计建议与已写正文事实' in prompt
    assert '未来集' in prompt and '计划' in prompt
    assert '已有正文依据' in prompt

@pytest.mark.parametrize('leak', ['## 第 1 集剧本正文', '## 第1集｜完整剧本正文', '## 1-1｜旧城 · 夜 · 外', '### 首集交接状态', '| craft_status | passed |'])
async def test_outline_rejects_embedded_episode_script_and_delivery_claim(store, leak):
    brief = await store.create(kind='brief', title='简报', markdown='都市悬疑连续剧', client_mutation_id='brief')
    service = GenerationService(store)
    run = await service.start(mode='bootstrap', brief_id=brief.id, script_mode='series', episode_count=3,
                              instruction='', mutation_id='run')
    class LeakyRuntime(Runtime):
        async def run_structured(self, *, output_type, **kwargs):
            return output_type(markdown=f'# 故事大纲\n\n姐妹寻找失踪父亲。\n\n{leak}\n\n甲：父亲在哪里？')
    with pytest.raises(GenerationValidation, match='当前文档类型'):
        await service.execute(run['id'], runtime=LeakyRuntime(), task_id='task')
    saved = await service.get(run['id'])
    assert saved['status'] == 'failed'
    assert [doc.kind for doc in await store.list()] == ['brief']

async def test_stage_prompt_names_required_document_structure_and_fact_source(store):
    from novelvideo.script_creation.prompts import build_prompt, craft_guidance
    brief = await store.create(kind='brief', title='简报', markdown='都市悬疑短剧', client_mutation_id='brief')
    outline = build_prompt(kind='outline', script_mode='series', episode_number=1, episode_count=3,
                           instruction='', references=[brief])
    people = build_prompt(kind='people', script_mode='series', episode_number=1, episode_count=3,
                          instruction='', references=[brief])
    scenes = build_prompt(kind='scenes', script_mode='series', episode_number=1, episode_count=3,
                          instruction='', references=[brief])
    script = build_prompt(kind='episode_script', script_mode='series', episode_number=1, episode_count=3,
                          instruction='', references=[brief])
    assert 'Logline' in outline and '情绪曲线' in outline and '全剧分段' in outline
    assert '题材' in outline and '目标观众' in outline and '目标体量' in outline
    assert '人物弧光' in people and '首次出场' in people and '已写正文' in people
    assert '身份与人物设定' in people and '被逼急时怎么做' in people and '极端行为' in people
    assert people.index('记忆点') < people.index('人物弧光') < people.index('被逼急时怎么做') < people.index('称呼规则')
    assert '可复用' in scenes and '场景类型' in scenes and '账房' in scenes and '码头' in scenes
    assert '寻找单据' in scenes and '剧本正文' in scenes
    assert script.index('本集标题') < script.index('集数与场号') < script.index('场景和时间')
    assert script.index('人物列表') < script.index('△开头的可拍摄动作') < script.index('人物对白')
    assert '动作与对白可自然交织' in script
    assert '必要语气' in script
    assert '只能依据引用中的 episode_script 文档' in people
    assert 'craft_status' not in craft_guidance('outline')

@pytest.mark.parametrize(('number', 'chinese'), [(11, '十一'), (20, '二十'), (21, '二十一'), (100, '一百')])
def test_target_synopsis_accepts_chinese_episode_numbers_through_100(number, chinese):
    from novelvideo.script_creation.prompts import target_synopsis
    markdown = f'# 分集梗概\n\n## 第{chinese}集\n姐妹在码头找到父亲留下的证人。\n\n## 第{number + 1}集\n后续剧情仍待规划。'
    result = target_synopsis(markdown, number)
    assert result is not None
    assert '姐妹在码头找到父亲留下的证人' in result
    assert '后续剧情' not in result


async def test_generated_candidate_enters_review_and_adopts_with_cas(store):
    from novelvideo.script_creation.proposals import ProposalService
    brief = await store.create(kind='brief', title='简报', markdown='独立短片', client_mutation_id='brief')
    target = await store.create(kind='outline', title='作者大纲', markdown='# 作者大纲\n\n已有内容。', client_mutation_id='target')
    service = GenerationService(store)
    run = await service.start(mode='bootstrap', brief_id=brief.id, script_mode='single', episode_count=1,
                              instruction='', mutation_id='start')
    result = await service.execute(run['id'], runtime=Runtime(), task_id='task')
    candidate_id = result['steps'][0]['output']['candidate_id']
    review = ProposalService(store)
    proposal = await review.from_candidate(candidate_id)
    assert proposal['source_candidate_id'] == candidate_id
    assert proposal['before'] == target.revision.markdown
    assert (await store.get(target.id)).current_revision_id == target.current_revision_id
    adopted = await review.accept([proposal['id']], base_revision_id=target.current_revision_id,
                                  client_mutation_id='accept')
    assert adopted.revision.markdown == (await store.generation_candidate(candidate_id))['markdown']
    assert len(await store.revisions(target.id)) == 2


def test_episode_script_prompt_requires_episode_scoped_scene_numbers():
    from novelvideo.script_creation.prompts import build_prompt
    prompt = build_prompt(kind="episode_script", script_mode="series", episode_number=3,
                          episode_count=8, instruction="", references=[])
    assert "3-1" in prompt and "3-2" in prompt
