"""Project document context for structured screenplay generation."""
from __future__ import annotations

from pathlib import Path
import re

from .models import Document


STEP_LABELS = {
    'brief': '创作简报',
    'outline': '故事大纲', 'episode_synopsis': '分集梗概', 'people': '人物小传',
    'scenes': '场景设计', 'props': '道具设计', 'episode_script': '剧本正文',
}

CRAFT_BY_KIND = {
    'brief': ('story-development.md',),
    'outline': ('story-development.md',),
    'episode_synopsis': ('story-development.md', 'short-drama-rhythm.md'),
    'people': ('character-and-dialogue.md',),
    'scenes': ('story-development.md',),
    'props': ('story-development.md',),
    'episode_script': ('screenplay-craft.md', 'character-and-dialogue.md',
                       'short-drama-rhythm.md', 'language-editing.md'),
}

STAGE_CONTRACTS = {
    'brief': '只写创作简报，说明题材、目标观众、核心冲突、主角目标、篇幅及创作约束；不写整集剧本或交付自评。',
    'outline': '只写故事大纲，不写任何一集剧本正文、逐场动作对白、人物详细小传或交付自评。大纲开头明确题材、目标观众、目标体量（集数与单集时长或短片时长）；随后依次写 Logline、核心看点与情绪曲线、故事简述、背景设定与世界规则、全剧分段、共情来源、种子情节与人物变化、爽感与反转桥段、创作禁区。全剧分段只概括剧情，不扩写成完整场次。',
    'episode_synopsis': '只写分集梗概，每集概括目标、冲突推进与结尾钩子；不写逐场剧本正文或对白。未来集是计划，不是已写事实。',
    'people': '只写人物小传，每位主要人物说明类型与作用、身份与人物设定、性格、语言风格与破绽、记忆点、人物弧光的起点/触发/考验/关键选择/终点、被逼急时怎么做及极端行为、称呼规则、首次出场与关键场次。严格保持记忆点→人物弧光→被逼急时怎么做/极端行为→称呼规则的字段顺序。未有正文依据的首次出场和关键场次标计划；不写整集正文。',
    'scenes': '只写场景设计。以可复用的物理空间为条目，例如账房、码头，不按寻找单据等具体发生事件拆成分镜场次；具体行动归入 episode_script 剧本正文。每处空间说明场景类型、剧情作用、空间布局、视觉/光线、关键物件和连续性约束；首次出场与关键场次区分计划和已写依据。不写逐场动作或逐句对白。',
    'props': '只写道具设计，说明剧情作用、外观材质、持有流转、使用动作、状态变化和连续性约束；首次出场与关键场次区分计划和已写依据。不写剧本正文或逐句对白。',
    'episode_script': '只写目标集完整剧本正文，采用小云雀示例结构：本集标题、集数与场号、场景和时间、人物列表、△开头的可拍摄动作、人物对白与必要语气。每场先列场景和出场人物，随后动作与对白可自然交织；内心独白标记 OS，广播明确标记声源，不把未出镜说话者列为出场人物。创作分析不混入正文，不输出本集目标、结尾钩子等分析标题；不写其他集正文、审查结论或交付状态。',
}


def craft_guidance(kind: str = 'episode_script') -> str:
    root = Path(__file__).resolve().parents[3] / 'nuomi-drama-scripts' / 'references'
    return '\n\n'.join((root / name).read_text(encoding='utf-8') for name in CRAFT_BY_KIND[kind] if (root / name).is_file())


def _chinese_episode_number(number: int) -> str:
    digits = ('', '一', '二', '三', '四', '五', '六', '七', '八', '九')
    if number == 100:
        return '一百'
    tens, ones = divmod(number, 10)
    if tens == 0:
        return digits[ones]
    return (digits[tens] if tens > 1 else '') + '十' + digits[ones]


def target_synopsis(markdown: str, episode_number: int) -> str | None:
    names = [str(episode_number)]
    if 1 <= episode_number <= 100:
        names.append(_chinese_episode_number(episode_number))
    marker = re.compile(r'第\s*(?:' + '|'.join(names) + r')\s*集')
    match = marker.search(markdown)
    if match is None:
        return None
    rest = markdown[match.end():]
    next_episode = re.search(r'第\s*(?:\d+|[一二三四五六七八九十百]+)\s*集', rest)
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
    parts.append('当前文档类型约束：' + STAGE_CONTRACTS[kind])
    parts.append('只返回当前文档的创作内容。禁止输出 craft_status、continuity_status、format_status、delivery_status、passed、自评、交付/交接状态或工作流程备注。写作参考资料仅是方法，不是本次要执行的交付流程。')
    parts.append('已写正文事实只能依据引用中的 episode_script 文档；brief、outline、episode_synopsis、people、scenes、props 即使提及集数或场次，也只是设定或计划，不能充当已写正文证据。')
    if kind == 'episode_script':
        parts.append(f'仅写第 {episode_number} 集完整正文。场号使用 {episode_number}-1、{episode_number}-2 依次编号。分行格式示例（替换占位内容，不照抄示例剧情）：\n'
                     f'第{episode_number}集 {episode_number}-1\n场景：地点名称 日/夜 内/外\n人物：实际出场人物\n△可拍摄的动作。\n人物名（必要语气）：对白。\n'
                     '内心独白格式为人物名（OS）：内容。使用可拍摄的场次、动作和对白，保持前文事实。不要写其他集正文。')
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
