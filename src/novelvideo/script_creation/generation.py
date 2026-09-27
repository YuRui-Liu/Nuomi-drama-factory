"""Durable, revision-bound creative generation runs."""
from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field, field_validator

from .prompts import STEP_LABELS, build_prompt, craft_guidance, target_synopsis
from .store import DocumentConflict, DocumentNotFound, DocumentStore, DocumentValidation


class GenerationConflict(DocumentConflict):
    pass


class GenerationValidation(DocumentValidation):
    pass


class GenerationOutput(BaseModel):
    markdown: str = Field(min_length=20)

    @field_validator('markdown')
    @classmethod
    def substantive(cls, value: str) -> str:
        lines = [line.strip() for line in value.splitlines() if line.strip() and not line.lstrip().startswith('#')]
        if not lines or len(''.join(lines)) < 12:
            raise ValueError('model returned headings or placeholders instead of content')
        return value.strip()


TEMPLATE_LINES = {
    'outline': ['# 故事大纲', '## Logline、核心看点与情绪曲线', '### Logline', '### 核心看点',
                '### 情绪曲线', '## 故事简述', '## 背景设定与世界规则', '## 全剧分段',
                '## 为什么能共情', '## 种子情节与人物变化', '## 爽感桥段', '## 反转桥段', '## 创作禁区'],
    'people': ['# 人物小传', '## 新人物（请命名）', '### 类型', '### 人物设定', '### 在本剧的作用',
               '### 性格', '### 语言风格', '### 说话的破绽', '### 设定记忆点', '### 人物弧光',
               '起点：', '触发：', '考验：', '关键选择：', '终点：', '计划集数：', '已写集数：',
               '### 被逼急时怎么做', '### 称呼规则', '### 首次出场', '### 关键场次'],
    'scenes': ['# 场景设计', '## 新场景（请命名）', '### 场景类型', '### 剧情作用', '### 空间布局',
               '### 视觉设计', '### 光线与氛围', '### 关键物件', '### 连续性约束',
               '### 首次出场（计划）', '### 首次出场（已写）', '### 关键场次（计划）', '### 关键场次（已写）'],
    'props': ['# 道具设计', '## 新道具（请命名）', '### 剧情作用', '### 外观与材质',
              '### 持有与流转', '### 使用动作', '### 状态变化', '### 连续性约束',
              '### 首次出场（计划）', '### 首次出场（已写）', '### 关键场次（计划）', '### 关键场次（已写）'],
    'episode_synopsis': ['# 分集梗概', '## 第 1 集', '### 本集目标', '### 冲突推进', '### 结尾钩子'],
}


def _blank_scaffold(markdown: str, kind: str = '', episode_number: int | None = None) -> bool:
    if not markdown.strip():
        return True
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    if kind == 'episode_script' and episode_number is not None:
        title = '# 剧本正文' if episode_number == 1 else f'# 第 {episode_number} 集'
        expected = [title, '本集目标：', f'## {episode_number}-1｜场景名称 · 日/夜 · 内/外',
                    '出场人物：', '动作描述：', '人物对白：', '必要语气提示（如需）：', '结尾钩子：']
        return lines == expected or (episode_number == 1 and lines == [
            '# 第 1 集', *expected[1:]])
    return lines == TEMPLATE_LINES.get(kind)


def _slot(docs, kind, episode=None):
    return next((doc for doc in docs if doc.kind == kind and doc.episode_number == episode), None)


class GenerationService:
    def __init__(self, store: DocumentStore):
        self.store = store

    async def get(self, run_id: str) -> dict[str, Any]:
        return await self.store.generation_get(run_id)

    async def list(self) -> list[dict[str, Any]]:
        return await self.store.generation_list()

    async def start(self, *, mode: str, brief_id: str, script_mode: str, episode_count: int,
                    instruction: str, mutation_id: str, episode_number: int = 1) -> dict[str, Any]:
        if mode not in {'bootstrap', 'continue'} or script_mode not in {'series', 'single'}:
            raise GenerationValidation('invalid generation mode')
        if not mutation_id.strip() or episode_count < 1 or episode_count > 100 or episode_number < 1 or episode_number > episode_count:
            raise GenerationValidation('invalid episode count or mutation id')
        if (script_mode == 'single' and episode_count != 1) or (mode == 'bootstrap' and episode_number != 1):
            raise GenerationValidation('episode count does not match generation mode')
        if len(instruction) > 8000:
            raise GenerationValidation('instruction too long')
        brief = await self.store.get(brief_id)
        if brief.kind != 'brief' or not brief.revision.markdown.strip():
            raise GenerationValidation('saved creative brief required')
        docs = await self.store.list()
        if mode == 'continue':
            if script_mode != 'series' or episode_number < 2:
                raise GenerationValidation('continuation requires a later series episode')
            synopsis = _slot(docs, 'episode_synopsis')
            if not _slot(docs, 'outline') or synopsis is None:
                raise GenerationValidation('saved outline and target episode synopsis required')
            if not target_synopsis(synopsis.revision.markdown, episode_number):
                raise GenerationValidation('substantive target episode synopsis required')
            if not any(doc.kind == 'episode_script' and doc.episode_number < episode_number
                       and not _blank_scaffold(doc.revision.markdown, doc.kind, doc.episode_number) for doc in docs):
                raise GenerationValidation('saved previous episode script required')
        kinds = (['outline'] + (['episode_synopsis'] if script_mode == 'series' else []) +
                 ['people', 'scenes', 'props', 'episode_script']) if mode == 'bootstrap' else ['episode_script']
        steps = [{'key': f'{kind}:{episode_number if kind == "episode_script" and mode == "continue" else 1 if kind == "episode_script" else 0}',
                  'kind': kind, 'title': (f'第 {episode_number if mode == "continue" else 1} 集' if kind == 'episode_script' and script_mode == 'series'
                                             else STEP_LABELS[kind]),
                  'episode_number': (episode_number if mode == 'continue' else 1) if kind == 'episode_script' else None,
                  'status': 'pending', 'context_revisions': {}, 'context_slots': [],
                  'task_id': None, 'output': None, 'error': None}
                 for kind in kinds]
        payload = {'mode': mode, 'brief_id': brief_id, 'brief_revision_id': brief.current_revision_id,
                   'script_mode': script_mode, 'episode_count': episode_count, 'episode_number': episode_number,
                   'instruction': instruction, 'baseline_revisions': {doc.id: doc.current_revision_id for doc in docs},
                   'status': 'pending', 'task_id': None, 'steps': steps, 'error': None}
        try:
            return await self.store.generation_start(payload, mutation_id)
        except DocumentConflict as exc:
            raise GenerationConflict(str(exc)) from exc

    async def execute(self, run_id: str, *, runtime: Any, task_id: str,
                      cancel_check: Callable[[], Awaitable[None]] | None = None,
                      commit_guard: Callable[[], None] | None = None,
                      progress: Callable[[int, int, str], None] | None = None) -> dict[str, Any]:
        data = await self.get(run_id)
        if data['status'] == 'completed':
            return data
        try:
            data = await self.store.generation_claim(run_id, task_id)
        except DocumentConflict as exc:
            raise GenerationConflict(str(exc)) from exc
        for index in range(len(data['steps'])):
            step = data['steps'][index]
            if step['status'] == 'completed':
                continue
            docs = await self.store.list()
            by_id = {doc.id: doc for doc in docs}
            stale = any(by_id.get(doc_id) is None or by_id[doc_id].current_revision_id != revision_id
                        for doc_id, revision_id in data['baseline_revisions'].items())
            stale = stale or any(prior['status'] == 'completed' and prior['output']['kind'] == 'document'
                                 and (by_id.get(prior['output']['document_id']) is None or
                                      by_id[prior['output']['document_id']].current_revision_id != prior['output']['revision_id'])
                                 for prior in data['steps'][:index])
            if stale:
                data['status'], data['error'] = 'needs_rebase', '参考文档已变化，请基于当前版本重新生成'
                return await self.store.generation_update(run_id, data, expected_task_id=task_id)
            kind, number = step['kind'], step['episode_number']
            references = [by_id[data['brief_id']]]
            for ref_kind in ('outline', 'episode_synopsis', 'people', 'scenes', 'props'):
                if ref_kind == kind:
                    continue
                ref = _slot(docs, ref_kind)
                if ref is not None:
                    references.append(ref)
            if kind == 'episode_script' and data['mode'] == 'continue':
                references += sorted((doc for doc in docs if doc.kind == 'episode_script' and
                                       doc.episode_number is not None and doc.episode_number < number),
                                     key=lambda doc: doc.episode_number)
            revisions = {doc.id: doc.current_revision_id for doc in references}
            slots = [{'kind': ref_kind, 'episode_number': None,
                      'document_ids': [doc.id for doc in docs if doc.kind == ref_kind and doc.episode_number is None]}
                     for ref_kind in ('outline', 'episode_synopsis', 'people', 'scenes', 'props') if ref_kind != kind]
            if kind == 'episode_script' and data['mode'] == 'continue':
                slots += [{'kind': 'episode_script', 'episode_number': prior_number,
                           'document_ids': [doc.id for doc in docs if doc.kind == 'episode_script'
                                            and doc.episode_number == prior_number]}
                          for prior_number in range(1, number)]
            target = _slot(docs, kind, number)
            target_ids = [doc.id for doc in docs if doc.kind == kind and doc.episode_number == number]
            step.update(status='running', task_id=task_id, context_revisions=revisions,
                        context_slots=slots, error=None)
            data = await self.store.generation_update(run_id, data, expected_task_id=task_id)
            if progress:
                progress(index, len(data['steps']), step['title'])
            try:
                if cancel_check:
                    await cancel_check()
                result = await runtime.run_structured(
                    prompt=build_prompt(kind=kind, script_mode=data['script_mode'],
                                        episode_number=number or 1, episode_count=data['episode_count'],
                                        instruction=data['instruction'], references=references),
                    output_type=GenerationOutput,
                    system_prompt='你是严谨的中文短剧编剧。根据所给参考文档创作，不虚构已写正文事实。\n' + craft_guidance(),
                )
                output = GenerationOutput.model_validate(result).markdown
                if cancel_check:
                    await cancel_check()
                data = await self.store.generation_commit_step(
                    run_id, step_index=index, task_id=task_id, markdown=output, references=revisions,
                    target_kind=kind, target_episode=number,
                    target_id=target.id if target else None, target_ids=target_ids,
                    target_revision=target.current_revision_id if target else None,
                    context_slots=slots,
                    allow_fill=target is None or _blank_scaffold(target.revision.markdown, kind, number),
                    commit_guard=commit_guard)
            except DocumentConflict:
                data = await self.get(run_id)
                data['status'], data['error'] = 'needs_rebase', '参考文档或目标文档已变化，请基于当前版本重新生成'
                return await self.store.generation_update(run_id, data, expected_task_id=task_id)
            except BaseException as exc:
                data = await self.get(run_id)
                paused = isinstance(exc, __import__('asyncio').CancelledError) or type(exc).__name__ == 'TaskCancelled'
                data['steps'][index].update(status='pending' if paused else 'failed', error=str(exc))
                data['status'], data['error'] = ('paused' if paused else 'failed'), str(exc)
                await self.store.generation_update(run_id, data, expected_task_id=task_id)
                raise
        return data
