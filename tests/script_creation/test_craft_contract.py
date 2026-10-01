import pytest

from novelvideo.agent_teams.adapters import craft_method, method_guidance
from novelvideo.agent_teams.models import ExecutionSnapshot, MethodConfig, ResourceRef, ResourceVersion
from novelvideo.agent_teams.runtime import method_scope
from novelvideo.script_creation.prompts import build_prompt, craft_guidance
from novelvideo.text_task_runtime.models import AgentTaskRoute


@pytest.mark.parametrize('episode', [1, 2, 12])
def test_screenplay_contract_uses_target_episode_and_separate_scene_metadata(episode):
    prompt = build_prompt(kind='episode_script', script_mode='series', episode_number=episode,
                          episode_count=12, instruction='', references=[])
    assert f'第{episode}集 {episode}-1\n场景：' in prompt
    assert '人物：' in prompt and '△' in prompt and 'OS' in prompt
    assert '动作与对白可自然交织' in prompt
    assert '场号｜场景名称' not in prompt
    assert '本集标题、本集目标→' not in prompt
    assert '创作分析不混入正文' in prompt


@pytest.mark.parametrize('kind', ['episode_synopsis', 'episode_script'])
def test_default_methods_include_single_episode_rhythm(kind):
    guidance = craft_guidance(kind)
    assert guidance.count('# 短剧节奏\n') == 1
    assert '单纯没有打斗' in guidance
    if kind == 'episode_script':
        assert guidance.count('# 人物与对白\n') == 1


def test_custom_skill_replaces_default_craft_but_preserves_format_contract():
    resource = ResourceVersion(id='quiet', revision=1, kind='skill', owner='user',
                               content='保留长时间安静观察。', content_hash='h')
    snapshot = ExecutionSnapshot(
        id='s', project_id='p', template_id='custom', template_revision=1,
        active_revision=1, role_id='writer', subtask_id='episode_script',
        input_revision='r', input_hash='h', resolved_model=AgentTaskRoute(),
        resolved_method=MethodConfig(skills=(ResourceRef(id='quiet', revision=1),)),
        resource_snapshots=(resource,))
    with method_scope([snapshot], project_id='p'):
        assert craft_method('writer', 'episode_script', craft_guidance('episode_script')) == ''
        prompt = build_prompt(kind='episode_script', script_mode='series', episode_number=2,
                              episode_count=3, instruction='', references=[])
        assert '第2集 2-1\n场景：' in prompt
        assert '保留长时间安静观察' in method_guidance(snapshot)
        assert '# 短剧节奏' not in prompt
