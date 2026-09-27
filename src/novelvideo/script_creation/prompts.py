"""Project document context for structured screenplay generation."""
from __future__ import annotations

from pathlib import Path

from .models import Document


STEP_LABELS = {
    'outline': '故事大纲', 'episode_synopsis': '分集梗概', 'people': '人物小传',
    'scenes': '场景设计', 'props': '道具设计', 'episode_script': '剧本正文',
}

CRAFT_FILES = ('story-development.md', 'character-and-dialogue.md', 'screenplay-craft.md',
               'language-editing.md', 'continuity-contract.md', 'review-rubric.md')


def craft_guidance() -> str:
    root = Path(__file__).resolve().parents[3] / 'nuomi-drama-scripts' / 'references'
    return '\n\n'.join((root / name).read_text(encoding='utf-8') for name in CRAFT_FILES if (root / name).is_file())


def build_prompt(*, kind: str, script_mode: str, episode_number: int, episode_count: int,
                 instruction: str, references: list[Document]) -> str:
    heading = STEP_LABELS[kind]
    parts = [f'任务：创作{heading}。形式：{"单篇完整短片" if script_mode == "single" else f"共 {episode_count} 集连续短剧"}。']
    if kind == 'episode_script':
        parts.append(f'仅写第 {episode_number} 集完整正文。使用可拍摄的场次、动作和对白，保持前文事实。不要写其他集正文。')
    elif kind == 'episode_synopsis':
        parts.append('规划全剧各集梗概；未来集只记作计划，不当作已发生事实。')
    if instruction.strip():
        parts.append('用户本次要求：' + instruction.strip())
    for doc in references:
        label = f'{doc.title}（{doc.kind}；版本 {doc.current_revision_id}）'
        if doc.kind == 'episode_synopsis':
            label += '；以下为计划，非已写正文事实'
        if doc.kind == 'episode_script':
            label += '；以下为已保存正文事实与风格参考'
        parts.append(f'### {label}\n{doc.revision.markdown}')
    parts.append('输出连贯的 Markdown 正文，内容须具体、完整、可继续编辑；不要只返回标题或占位提示。')
    return '\n\n'.join(parts)
