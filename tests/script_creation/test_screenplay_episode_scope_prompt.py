from tests.craft_fakes import craft_reply
from novelvideo.script_creation.generation import GenerationService
from novelvideo.script_creation.store import DocumentStore


class CapturingRuntime:
    def __init__(self):
        self.prompts = []

    async def run_structured(self, *, prompt, output_type, **kwargs):
        self.prompts.append(prompt)
        return output_type.model_validate(
            craft_reply(prompt, '# 第一集\n\n林岚关掉灯，听见脚步停在门外。')
        )


async def test_confirmed_episode_synopsis_bounds_e01_despite_series_final_choice(tmp_path):
    store = DocumentStore(tmp_path / 'generation.db')
    await store.initialize()
    brief = await store.create(
        kind='brief', title='山城悬疑', markdown='八集连续剧，围绕旧档案展开。',
        client_mutation_id='brief',
    )
    outline = await store.create(
        kind='outline', title='全剧大纲', markdown='主角逐步揭开档案被篡改的原因。',
        client_mutation_id='outline',
    )
    synopsis = await store.create(
        kind='episode_synopsis', title='分集梗概',
        markdown=(
            '# 分集梗概\n\n## 第1集：夜痕\n'
            '林岚发现档案页有新刻痕，听见脚步后藏起笔记。\n\n'
            '## 第2集：来客\n林岚找到刻痕的来源。'
        ),
        client_mutation_id='synopsis',
    )
    idea = {
        'candidate_id': 'idea-candidate-1',
        'revision_id': 'idea-revision-4',
        'one_line': '旧档案揭开山城的秘密。',
        'protagonist_conflict': '林岚必须决定是否公开证据。',
        'highlights': '线索逐集展开。',
        'final_choice': '第八集公开全部证据并承担后果。',
    }
    confirmed = {
        brief.id: brief.current_revision_id,
        idea['candidate_id']: idea['revision_id'],
        outline.id: outline.current_revision_id,
        synopsis.id: synopsis.current_revision_id,
    }
    service = GenerationService(store)
    run = await service.start(
        mode='screenplay', brief_id=brief.id, script_mode='series', episode_count=8,
        episode_number=1, instruction='', mutation_id='write-e01', source_idea=idea,
        source_ideation_id='ideation-run-1', confirmed_revisions=confirmed,
        plan_run_id='confirmed-plan-run-1',
    )
    runtime = CapturingRuntime()

    result = await service.execute(run['id'], runtime=runtime, task_id='task-e01')

    assert result['status'] == 'completed'
    prompt = runtime.prompts[0]
    assert '第八集公开全部证据并承担后果。' in prompt
    assert '林岚发现档案页有新刻痕，听见脚步后藏起笔记。' in prompt
    assert '本轮只创作和审查第1集。' in prompt
    assert '已确认的分集梗概决定本集的情节范围和结尾' in prompt
    assert '全剧立意、最终抉择及后续集情节是长线背景' in prompt
    assert '审查不得把已分配给后续集的事件当成本集遗漏' in prompt
    assert '不得为满足全剧结局改写已确认的本集范围' in prompt
