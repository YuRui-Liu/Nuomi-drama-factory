"""Bounded, resumable craft orchestration, independent of document persistence."""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import time
import uuid

from pydantic import ValidationError

from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError

from .craft_models import CraftDraft, CraftRewriteDraft, CraftResult, CraftReview, CraftStageRecord


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def _instruction(stage):
    common = ('仅执行当前阶段。创作方法和风格以提供的 system_prompt 与用户简报为准。'
              '正文与候选计划严格区分，不把计划当作已发生事实。不要在正文输出审查或交付状态。')
    if 'review' in stage:
        return common + (
            '独立审阅当前完整草稿，不接受作者自评。逐项给出 coverage 审阅范围和 strengths 应保留设计的原文证据。'
            '每个 issue 必须包含 severity(blocker/suggestion)、dimension、quote、start/end、reason、repair_goal。'
            'repair_scope 标明修复范围：仅措辞为 dialogue，必要相邻表演为 local_action，涉及新事件、角色调度或情节结果为 story。'
            'start/end 为 Python Unicode 字符偏移，区间左闭右开；不确定时都填 null，仅允许唯一原句。'
            '跨输入或跨文档矛盾必须标记 basis=context，并同时给出 reference_quote 及其在原始任务中的 reference_start/reference_end；正文内部问题标记 basis=local。'
            'issue_history 包含全部历史问题，逐项复核；确已解决的 ID 写入 resolved_issue_ids，仍存在的问题须再次报告。ID 由程序生成，不自行编造。'
            'resolved_issue_ids 和 prior_issue_id 只能使用本轮 issue_history 中的 ID；project_memory 是其他任务的历史提醒，不可关闭或关联其中的问题。issue_history 为空时 resolved_issue_ids 必须为 []，prior_issue_id 必须为 null。'
            '同一缺陷经过部分修改而引用或位置变化时，以 prior_issue_id 关联历史 ID，不得谎报已解决；每个历史问题只关联一个当前问题。'
            '缺失问题应引用受影响的完整场景。偏好只能作为 suggestion，不得编造证据。'
            '不输出 passed 字段，由程序依据 blocker 计算结论。'
            + ('本轮检查对白的言说目的、角色信息边界及实际采用方法；覆盖全部场景。' if stage == 'dialogue_review'
               else '检查上下文约束、因果、人物动机及实际采用方法；复查前轮问题并检查改动影响。'))
    if stage == 'dialogue_integrate':
        return common + '仅整合有证据的阻断对白问题及必要相邻动作，保留场次、事实、目标与情节结果。返回完整正文 markdown。'
    if stage.startswith('structure_repair'):
        return common + '仅修复报告中的阻断问题与必要关联影响，保留有效设计，不扩大用户指定改写范围。返回完整正文 markdown。'
    return common + '生成当前文档的完整干净 markdown。正文场景意图可独立写入 scene_intents，禁止混入正文。'


async def run_craft(*, runtime, prompt: str, system_prompt: str, kind: str,
                    records=(), checkpoint=None, cancel_check=None,
                    validate_text=None, max_repairs=None, stage_started=None,
                    author_session=None, session_checkpoint=None, memory=(), session_dir=None) -> CraftResult:
    limit = 2 if kind == 'episode_script' else 1
    if max_repairs is not None:
        if not isinstance(max_repairs, int) or not 0 <= max_repairs <= limit:
            raise ValueError('max_repairs exceeds bounded craft limit')
        limit = max_repairs
    saved = [r if isinstance(r, CraftStageRecord) else CraftStageRecord.model_validate(r) for r in records]
    from novelvideo.text_task_runtime.craft_session import author_runtime
    from .craft_feedback import author_history, repair_history, qc_memory
    session = dict(author_session or {})
    route = getattr(runtime,'snapshot',None)
    route = route.model_dump(mode='json') if hasattr(route,'model_dump') else str(route)
    binding = _hash({'task':prompt,'system':system_prompt,'kind':kind,'route':route,'memory':list(memory)})
    if session.get('binding') and session['binding'] != binding:
        raise ValueError('saved craft session input mismatch; rebase required')
    session_key = session.get('id') or next((r.author_session_id for r in saved if r.author_session_id), None) or str(uuid.uuid4())
    native_id = session.get('provider_session_id') or next((r.provider_session_id for r in reversed(saved) if r.provider_session_id), None)
    async def save_native_session(provider_id):
        if session_checkpoint:
            await session_checkpoint({'id': session_key, 'provider_session_id': provider_id, 'mode': 'native','binding':binding})
    author = author_runtime(runtime, session_id=native_id, on_session=save_native_session, session_dir=session_dir)
    if session_checkpoint:
        await session_checkpoint({'id':session_key,'provider_session_id':native_id,'mode':author.mode,'binding':binding})
    completed = []
    issue_history = {}

    async def check():
        if cancel_check:
            await cancel_check()

    async def call(stage, model, markdown='', review=None, previous_draft=None):
        is_author = 'review' not in stage
        request = {'task': prompt, 'kind': kind, 'stage': stage,
                   'draft': markdown, 'review': review.model_dump(mode='json') if review else None,
                   'issue_history': list(issue_history.values()), 'project_memory': list(memory) if is_author else qc_memory(memory)}
        context = {'draft': markdown, 'reference': prompt, 'issue_history': list(issue_history.values())}
        stage_system = system_prompt + '\n\n' + _instruction(stage)
        stage_system += '\nproject_memory 是带来源的历史审稿提醒，可能过期或误判，不能作为事实或新的强制风格规则；当前用户要求与正式参考优先。'
        if is_author:
            request.update(author_session_id=session_key, author_history=author_history(completed),
                           repair_history=repair_history(completed))
            stage_system += ('\n延续同一作者修订链。repair_history 包含每次尝试和后续QC结果。'
                             '同一问题再次出现时先诊断上次修法为什么未奏效，再更换处理策略；不得只换措辞掩盖同一缺陷。'
                             '修订返回 repair_notes，逐个 blocker 写 issue_id、diagnosis、strategy、changed_quote（新版中的原句）；'
                             '这只是作者处理说明，不代表问题已解决，须由独立QC确认。')
            if review:
                context['repair_issues'] = [i.model_dump(mode='json') for i in review.issues]
        if previous_draft is not None:
            request['previous_draft'] = previous_draft
            context['reference'] += '\n\n整合前正文：\n' + previous_draft
            stage_system += ('\n逐项对比 previous_draft 与当前正文：对白整合只能改措辞和必要相邻表演，'
                             '不得新增事故、危险行为、道具规则、角色进出或改变情节结果。'
                             '发现越界改动必须作为上下文阻断问题，提供当前及整合前双方原文证据。'
                             '既有结构问题若不能在此范围内修复，应保留需要修改结论，不降低标准。')
        route = getattr(runtime, 'snapshot', None)
        route = route.model_dump(mode='json') if hasattr(route, 'model_dump') else str(route)
        fingerprint = _hash({'request': request, 'system': stage_system, 'max_repairs': limit, 'route': route})
        await check()
        index = len(completed)
        if index < len(saved):
            record = saved[index]
            if record.stage != stage or record.input_hash != fingerprint:
                raise ValueError('saved craft input mismatch; rebase required')
            if record.output_hash != _hash(record.output):
                raise ValueError('saved craft output hash mismatch')
            output = model.model_validate(record.output, context=context)
        else:
            # Explicit application budget, never silently truncate source evidence.
            budget = int(os.getenv('SCRIPT_CRAFT_MAX_INPUT_CHARS', '200000'))
            if len(json.dumps(request, ensure_ascii=False)) + len(stage_system) > budget:
                raise ValueError(f'创作上下文超过配置预算（{budget} 字符），请缩小引用范围后重新生成')
            if stage_started:
                await stage_started(stage)
            started = time.monotonic()
            kwargs = dict(prompt=json.dumps(request, ensure_ascii=False), system_prompt=stage_system, output_type=model)
            selected_runtime = author if is_author else runtime
            parameters = inspect.signature(selected_runtime.run_structured).parameters
            if 'validation_context' in parameters or any(p.kind == p.VAR_KEYWORD for p in parameters.values()):
                kwargs['validation_context'] = context
            # Logical stage count stays bounded (4/9); malformed-output attempts
            # are recorded separately. Provider-internal retries remain provider
            # accounting; attempts here counts facade invocations only.
            for attempt in range(1, 3):
                await check()
                try:
                    raw = await selected_runtime.run_structured(**kwargs)
                    output = model.model_validate(raw.model_dump(mode='json') if hasattr(raw, 'model_dump') else raw,
                                                  context=context)
                    break
                except (ValidationError, KnowledgeRuntimeError) as exc:
                    if isinstance(exc, KnowledgeRuntimeError) and exc.code not in {
                        'WORKBUDDY_OUTPUT_INVALID', 'DSH_OUTPUT_INVALID', 'CODEX_STRUCTURED_OUTPUT_INVALID',
                    }:
                        raise
                    await check()
                    if attempt == 2:
                        raise
                    kwargs['prompt'] = json.dumps(request, ensure_ascii=False) + '\n结构或证据校验失败，请修正：' + str(exc)
        if isinstance(output, CraftDraft):
            if not output.markdown.strip():
                raise ValueError('empty craft draft')
            if validate_text:
                validate_text(output.markdown)
        if index >= len(saved):
            payload = output.model_dump(mode='json')
            record = CraftStageRecord(stage=stage, input_hash=fingerprint, output=payload,
                                      output_hash=_hash(payload), elapsed_seconds=time.monotonic() - started,
                                      attempts=attempt, author_session_id=session_key if is_author else None,
                                      session_mode=author.mode if is_author else None,
                                      provider_session_id=author.session_id if is_author else None)
            if checkpoint:
                await checkpoint(record.model_dump(mode='json'))
        completed.append(record)
        if isinstance(output, CraftReview):
            for issue in output.issues:
                issue_history[issue.id] = issue.model_dump(mode='json')
        await check()
        return output

    def result(draft, review):
        if len(completed) < len(saved):
            raise ValueError('saved craft input contains unexpected stages')
        return CraftResult(markdown=draft.markdown, status=review.status, review=review, records=completed)

    draft_model = CraftRewriteDraft if kind == 'rewrite' else CraftDraft
    draft = await call('draft', draft_model)
    review = await call('structure_review', CraftReview, draft.markdown)
    for attempt in range(1, limit + 1):
        if review.status == 'passed':
            break
        draft = await call(f'structure_repair_{attempt}', draft_model, draft.markdown, review)
        review = await call(f'structure_review_{attempt}', CraftReview, draft.markdown, review)
    if review.status != 'passed' or kind != 'episode_script':
        return result(draft, review)
    review = await call('dialogue_review', CraftReview, draft.markdown)
    if review.status != 'passed':
        if any(i.severity == 'blocker' and i.repair_scope == 'story' for i in review.issues):
            # A dialogue pass cannot reopen the accepted story or silently spend
            # another structural repair after its bounded phase has ended.
            return result(draft, review)
        # Suggestions remain visible but never cause unsolicited aesthetic rewrites.
        blockers = review.model_copy(update={'issues': [i for i in review.issues if i.severity == 'blocker']})
        previous_draft = draft.markdown
        draft = await call('dialogue_integrate', CraftDraft, draft.markdown, blockers)
        review = await call('final_review', CraftReview, draft.markdown, review, previous_draft=previous_draft)
    return result(draft, review)
