"""Project document context for structured screenplay generation."""
from __future__ import annotations

from pathlib import Path
import re

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


def target_synopsis(markdown: str, episode_number: int) -> str | None:
    chinese = ('', '一', '二', '三', '四', '五', '六', '七', '八', '九', '十')
    names = [str(episode_number)]
    if 1 <= episode_number <= 10:
        names.append(chinese[episode_number])
    marker = re.compile(r'第\s*(?:' + '|'.join(names) + r')\s*集')
    match = marker.search(markdown)
    if match is None:
        return None
    rest = markdown[match.end():]
    next_episode = re.search(r'第\s*(?:\d+|[一二三四五六七八九十]+)\s*集', rest)
    section = rest[:next_episode.start()] if next_episode else rest
    body = '\n'.join(line.strip() for line in section.splitlines()
                     if line.strip() and not line.lstrip().startswith('#'))
    if len(re.sub(r'[\W_]+', '', body)) < 8:
        return None
    return f'第 {episode_number} 集：{body.strip()}'


def build_prompt(*, kind: str, script_mode: str, episode_number: int, episode_count: int,
                 instruction: str, references: list[Document]) -> str:
    heading = STEP_LABELS[kind]
    parts = [f'任务：创作{heading}。形式：{"单篇完整短片" if script_mode == "single" else f"共 {episode_count} 集连续短剧"}。']
    if kind == 'episode_script':
        parts.append(f'仅写第 {episode_number} 集完整正文。使用可拍摄的场次、动作和对白，保持前文事实。不要写其他集正文。')
    elif kind == 'episode_synopsis':
        parts.append('规划全剧各集梗概；未来集只记作计划，不当作已发生事实。')
    parts.append('区分人物、场景、道具的设计建议与已写正文事实：设定中的首次出场、关键场次或道具使用，若指向未来集，一律标为计划；引用已写集、已写场或已发生事件时，必须有已有正文依据。不要把设计预期写成已发生事实，也不要假造已写证据。')
    if instruction.strip():
        parts.append('用户本次要求：' + instruction.strip())
    for doc in references:
        label = f'{doc.title}（{doc.kind}；版本 {doc.current_revision_id}）'
        if doc.kind == 'episode_synopsis':
            label += '；以下为计划，非已写正文事实'
            if kind == 'episode_script':
                planned = target_synopsis(doc.revision.markdown, episode_number)
                if planned:
                    parts.append('目标集梗概（计划）：' + planned)
        if doc.kind in {'people', 'scenes', 'props'}:
            label += '；以下包含设计建议与可能的未来计划，只有已写正文能够证明已发生事实'
        if doc.kind == 'episode_script':
            label += '；以下为已保存正文事实与风格参考'
        parts.append(f'### {label}\n{doc.revision.markdown}')
    parts.append('输出连贯的 Markdown 正文，内容须具体、完整、可继续编辑；不要只返回标题或占位提示。')
    return '\n\n'.join(parts)
