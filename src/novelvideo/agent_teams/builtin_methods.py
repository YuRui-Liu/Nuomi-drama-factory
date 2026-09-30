"""Read-only provenance for the actual baseline prompts; never a mutable library."""
from hashlib import sha256


def builtin_methods():
    from novelvideo.script_creation.prompts import CRAFT_BY_KIND, craft_guidance, build_prompt
    from novelvideo.script_creation.generation import GENERATION_SYSTEM_PROMPT
    from novelvideo.screenplay_semantics.prompts import SYSTEM_PROMPT
    from novelvideo.director_plan.prompts import _EPISODE_AUTHORITY, _REPAIR_AUTHORITY
    from novelvideo.media_capabilities.video.h3_prompt_profile import H3_DIRECTOR_SYSTEM_PROMPT

    result = []

    def add(role, task, name, kind, source, content):
        result.append(dict(id=f'{role}:{task}:{name}', role_id=role, subtask_id=task,
                           name=name, kind=kind, source=source, content=content,
                           content_hash=sha256(content.encode()).hexdigest(),
                           read_only=True, replaceable=kind == 'creative_skill'))

    for task, files in CRAFT_BY_KIND.items():
        add('writer', task, '写作技法', 'creative_skill',
            'script_creation/prompts.py:craft_guidance → nuomi-drama-scripts/references/' + ', '.join(files), craft_guidance(task))
        add('writer', task, '固定写作协议示例（单篇第 1 集、空引用、无用户要求）', 'protocol',
            f'script_creation/generation.py:GENERATION_SYSTEM_PROMPT + script_creation/prompts.py:build_prompt(kind={task}, script_mode=single, episode_number=1, episode_count=1, instruction="", references=[])；含 STAGE_CONTRACTS',
            GENERATION_SYSTEM_PROMPT + '\n\n' + build_prompt(kind=task, script_mode='single',
                episode_number=1, episode_count=1, instruction='', references=[]))
    add('script_parser', 'screenplay_semantics', '剧本解析系统协议', 'protocol',
        'screenplay_semantics/prompts.py:SYSTEM_PROMPT', SYSTEM_PROMPT)
    add('director', 'director_plan', '整集导演系统协议', 'protocol',
        'director_plan/prompts.py:_EPISODE_AUTHORITY', _EPISODE_AUTHORITY)
    add('director', 'director_plan', '失败分组修复协议', 'protocol',
        'director_plan/prompts.py:_REPAIR_AUTHORITY', _REPAIR_AUTHORITY)
    add('video_director', 'h3_episode_pack', 'H3 导演提示配置', 'protocol',
        'media_capabilities/video/h3_prompt_profile.py:H3_DIRECTOR_SYSTEM_PROMPT', H3_DIRECTOR_SYSTEM_PROMPT)
    return result
