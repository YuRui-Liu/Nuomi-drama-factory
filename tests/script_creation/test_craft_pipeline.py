import asyncio
import json

import pytest

from novelvideo.script_creation.craft_pipeline import run_craft


TEXT = '# 第一集\n\n小林决定交出钥匙，承担失去住所的风险，换取邻居的信任。'


def test_bad_evidence_error_identifies_the_quote_for_model_repair():
    from novelvideo.script_creation.craft_models import locate_evidence
    with pytest.raises(ValueError, match='不存在的原句'):
        locate_evidence(TEXT, '不存在的原句', None, None)


def review(block=False):
    return dict(summary='检查完成', coverage=['全文因果与人物选择'],
                strengths=[dict(quote='交出钥匙', reason='选择带来代价')],
                issues=[dict(severity='blocker', dimension='因果', quote='邻居的信任',
                             reason='缺少回应', repair_goal='补足邻居回应', repair_scope='dialogue')] if block else [])


class Runtime:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def run_structured(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        if 'markdown' in output and kwargs.get('validation_context', {}).get('repair_issues'):
            output = dict(output)
            output['repair_notes'] = [dict(issue_id=i['id'], diagnosis=i['reason'],
                strategy='补充具体回应', changed_quote=output['markdown'])
                for i in kwargs['validation_context']['repair_issues'] if i['severity'] == 'blocker']
        if 'issues' in output:
            output = dict(output)
            active = {(i['dimension'], i.get('basis', 'local'), i['quote']) for i in output['issues']}
            output.setdefault('resolved_issue_ids', [i['id'] for i in kwargs.get('validation_context', {}).get('issue_history', [])
                                                     if i['severity'] == 'blocker' and (i['dimension'], i['basis'], i['quote']) not in active])
        return output


async def test_planning_skips_unnecessary_repair():
    runtime = Runtime({'markdown': TEXT}, review())
    result = await run_craft(runtime=runtime, prompt='计划', system_prompt='方法', kind='outline')
    assert result.status == 'passed'
    assert len(runtime.calls) == 2
    assert len(result.records) == 2


async def test_context_budget_fails_before_call_without_truncation(monkeypatch):
    monkeypatch.setenv('SCRIPT_CRAFT_MAX_INPUT_CHARS', '10')
    runtime = Runtime()
    with pytest.raises(ValueError, match='上下文超过'):
        await run_craft(runtime=runtime, prompt='完整输入', system_prompt='方法', kind='outline')
    assert not runtime.calls


async def test_changed_model_cannot_reuse_checkpoint():
    runtime = Runtime({'markdown': TEXT}, review())
    runtime.snapshot = 'model-a'
    result = await run_craft(runtime=runtime, prompt='计划', system_prompt='方法', kind='outline')
    changed = Runtime()
    changed.snapshot = 'model-b'
    with pytest.raises(ValueError, match='input mismatch'):
        await run_craft(runtime=changed, prompt='计划', system_prompt='方法', kind='outline', records=result.records)
    assert not changed.calls


async def test_planning_repairs_blocker_and_rechecks():
    runtime = Runtime({'markdown': TEXT}, review(True), {'markdown': TEXT}, review())
    result = await run_craft(runtime=runtime, prompt='计划', system_prompt='方法', kind='outline')
    assert result.status == 'passed'
    assert len(runtime.calls) == 4


async def test_episode_exhaustion_preserves_failure():
    runtime = Runtime({'markdown': TEXT}, review(True), {'markdown': TEXT}, review(True),
                      {'markdown': TEXT}, review(True))
    result = await run_craft(runtime=runtime, prompt='计划', system_prompt='方法', kind='episode_script')
    assert result.status == 'needs_revision'
    assert len(runtime.calls) == 6


async def test_episode_maximum_nine_calls_includes_final_review():
    runtime = Runtime({'markdown': TEXT}, review(True), {'markdown': TEXT}, review(True),
                      {'markdown': TEXT}, review(), review(True), {'markdown': TEXT + '邻居点头。'}, review())
    result = await run_craft(runtime=runtime, prompt='计划', system_prompt='方法', kind='episode_script')
    assert result.status == 'passed'
    assert len(runtime.calls) == 9
    assert result.records[-1].stage == 'final_review'
    assert json.loads(runtime.calls[-1]['prompt'])['review']['issues']
    assert json.loads(runtime.calls[-1]['prompt'])['previous_draft'] == TEXT
    assert TEXT in runtime.calls[-1]['validation_context']['reference']


async def test_dialogue_discovered_story_problem_stops_without_changing_events():
    problem = review(True)
    problem['issues'][0]['repair_scope'] = 'story'
    runtime = Runtime({'markdown': TEXT}, review(), problem)
    result = await run_craft(runtime=runtime, prompt='计划', system_prompt='方法', kind='episode_script')
    assert result.status == 'needs_revision'
    assert result.markdown == TEXT
    assert len(runtime.calls) == 3


async def test_invalid_review_evidence_fails_closed():
    invalid = review(True)
    invalid['issues'][0]['quote'] = '并不存在的证据'
    records = []

    async def checkpoint(record):
        records.append(record)

    with pytest.raises(ValueError, match='quote'):
        await run_craft(runtime=Runtime({'markdown': TEXT}, invalid, invalid), prompt='计划', system_prompt='方法', kind='outline', checkpoint=checkpoint)
    assert [record['stage'] for record in records] == ['draft']


async def test_cancel_after_call_checkpoints_and_resume_reuses_output():
    records = []
    runtime = Runtime({'markdown': TEXT})

    async def checkpoint(record):
        records.append(record)

    async def cancel():
        if records:
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await run_craft(runtime=runtime, prompt='计划', system_prompt='方法', kind='outline',
                        checkpoint=checkpoint, cancel_check=cancel)
    resumed = Runtime(review())
    result = await run_craft(runtime=resumed, prompt='计划', system_prompt='方法', kind='outline', records=records)
    assert result.status == 'passed'
    assert len(resumed.calls) == 1
    with pytest.raises(ValueError, match='input'):
        await run_craft(runtime=Runtime(), prompt='改变计划', system_prompt='方法', kind='outline', records=records)


async def test_each_revision_is_validated_before_review():
    checked = []
    runtime = Runtime({'markdown': TEXT}, review(True), {'markdown': TEXT + '修订'}, review())
    await run_craft(runtime=runtime, prompt='计划', system_prompt='方法', kind='rewrite', validate_text=checked.append)
    assert checked == [TEXT, TEXT + '修订']
    assert len(runtime.calls) == 4


async def test_blocking_review_need_not_invent_strengths():
    bad = review(True)
    bad['strengths'] = []
    result = await run_craft(runtime=Runtime({'markdown': TEXT}, bad), prompt='计划', system_prompt='方法', kind='outline', max_repairs=0)
    assert result.status == 'needs_revision'


def test_packaged_resources_have_provenance_and_stage_specific_guidance():
    from novelvideo.script_creation.craft_resources import craft_guidance, stage_guidance, provenance
    assert 'Want' in craft_guidance('people')
    assert '潜台词' in stage_guidance('dialogue_review', 'episode_script')
    info = provenance()
    assert len(info['sources']) == 7
    assert all(len(item['sha256']) == 64 for item in info['sources'])
    assert info['resource_hashes']


async def test_suggestions_do_not_trigger_dialogue_integration():
    suggestion = review(True)
    suggestion['issues'][0]['severity'] = 'suggestion'
    runtime = Runtime({'markdown': TEXT}, review(), suggestion)
    result = await run_craft(runtime=runtime, prompt='计划', system_prompt='自定义方法', kind='episode_script')
    assert result.status == 'passed'
    assert len(runtime.calls) == 3
    assert result.review.issues[0].severity == 'suggestion'


@pytest.mark.parametrize('evidence', [
    dict(quote='不存在的原句', start=0, end=4),
    dict(quote='交出钥匙', reference_quote='不存在的输入'),
])
async def test_invalid_offsets_and_reference_evidence_are_rejected(evidence):
    bad = review(True)
    bad['issues'][0].update(evidence)
    with pytest.raises(ValueError, match='quote'):
        await run_craft(runtime=Runtime({'markdown': TEXT}, bad, bad), prompt='计划', system_prompt='方法', kind='outline')


@pytest.mark.parametrize('start,end', [(0, 4), (8, None), (None, 9), (0, 999)])
def test_unique_verbatim_evidence_recovers_inaccurate_model_offsets(start, end):
    from novelvideo.script_creation.craft_models import CraftReview
    bad = review(True)
    bad['issues'][0].update(start=start, end=end)
    bad['strengths'][0].update(start=start, end=end)
    bad['issues'][0].update(basis='context', reference_quote='原文要求', reference_start=0, reference_end=1)
    result = CraftReview.model_validate(bad, context={'draft': TEXT, 'reference': '背景：原文要求必须保留'})
    issue = result.issues[0]
    assert TEXT[issue.start:issue.end] == issue.quote
    assert TEXT[result.strengths[0].start:result.strengths[0].end] == '交出钥匙'
    assert (issue.reference_start, issue.reference_end) == (3, 7)
    assert result.status == 'needs_revision'


def test_inaccurate_offsets_do_not_guess_between_repeated_quotes():
    from novelvideo.script_creation.craft_models import locate_evidence
    with pytest.raises(ValueError, match='ambiguous'):
        locate_evidence('原句。原句。', '原句', 1, 3)
    assert locate_evidence('原句。原句。', '原句', 3, 5) == (3, 5)


async def test_completed_review_can_resume_without_any_model_calls():
    original = await run_craft(runtime=Runtime({'markdown': TEXT}, review()), prompt='计划', system_prompt='方法', kind='outline')
    runtime = Runtime()
    resumed = await run_craft(runtime=runtime, prompt='计划', system_prompt='方法', kind='outline',
                              records=[r.model_dump(mode='json') for r in original.records])
    assert resumed.markdown == original.markdown
    assert resumed.review.strengths[0].start == TEXT.index('交出钥匙')
    assert not runtime.calls


def test_missing_packaged_resource_raises(monkeypatch, tmp_path):
    from novelvideo.script_creation import craft_resources
    monkeypatch.setattr(craft_resources, 'files', lambda _: tmp_path)
    with pytest.raises(FileNotFoundError):
        craft_resources.craft_guidance('outline')


def test_context_contradiction_requires_both_sources():
    from novelvideo.script_creation.craft_models import CraftReview
    bad = review(True)
    bad['issues'][0]['basis'] = 'context'
    with pytest.raises(ValueError, match='reference_quote'):
        CraftReview.model_validate(bad)


async def test_invalid_evidence_retries_once_with_context_and_attempt_count():
    bad = review(True)
    bad['issues'][0]['quote'] = '不存在'
    runtime = Runtime({'markdown': TEXT}, bad, review())
    result = await run_craft(runtime=runtime, prompt='计划', system_prompt='方法', kind='outline')
    assert result.status == 'passed'
    assert len(runtime.calls) == 3
    assert len(result.records) == 2
    assert result.records[-1].attempts == 2
    assert runtime.calls[-1]['validation_context']['draft'] == TEXT


def test_review_model_validates_evidence_inside_runtime():
    from novelvideo.script_creation.craft_models import CraftReview
    bad = review(True)
    bad['issues'][0]['quote'] = '不存在'
    with pytest.raises(ValueError, match='quote'):
        CraftReview.model_validate(bad, context={'draft': TEXT, 'reference': '计划'})


async def test_final_review_receives_all_prior_issues_and_stable_ids():
    structural = review(True)
    dialogue = review(True)
    dialogue['issues'][0].update(dimension='对白', quote='交出钥匙')
    runtime = Runtime({'markdown': TEXT}, structural, {'markdown': TEXT}, review(),
                      dialogue, {'markdown': TEXT}, review())
    result = await run_craft(runtime=runtime, prompt='计划', system_prompt='方法', kind='episode_script')
    history = json.loads(runtime.calls[-1]['prompt'])['issue_history']
    assert {item['dimension'] for item in history} == {'因果', '对白'}
    ids = {item['id'] for item in history}
    assert len(ids) == 2
    assert set(result.review.history_issue_ids) == ids
    assert result.records[1].output['issues'][0]['id'] in ids


async def test_local_rewrite_allows_short_selected_dialogue():
    checked = []
    report = dict(summary='范围检查', coverage=['选中台词'], issues=[],
                  strengths=[dict(quote='新台词✨', reason='保留改写目的')])
    result = await run_craft(runtime=Runtime({'markdown': '新台词✨'}, report), prompt='替换选中台词',
                             system_prompt='方法', kind='rewrite', validate_text=checked.append)
    assert result.markdown == '新台词✨'
    assert checked == ['新台词✨']


def test_passing_review_cannot_forget_unresolved_blocker():
    from novelvideo.script_creation.craft_models import CraftReview
    first = CraftReview.model_validate(review(True), context={'draft': TEXT})
    context = {'draft': TEXT, 'reference': '计划', 'issue_history': [first.issues[0].model_dump(mode='json')]}
    with pytest.raises(ValueError, match='disposition'):
        CraftReview.model_validate(review(), context=context)
    fixed = review()
    fixed['resolved_issue_ids'] = [first.issues[0].id]
    assert CraftReview.model_validate(fixed, context=context).status == 'passed'


def test_repeated_quotes_at_distinct_offsets_have_distinct_issue_ids():
    from novelvideo.script_creation.craft_models import CraftReview
    text = TEXT + TEXT
    bad = review(True)
    issue = bad['issues'][0]
    pos = TEXT.index(issue['quote'])
    bad['issues'] = [dict(issue, start=pos, end=pos + len(issue['quote'])),
                     dict(issue, start=pos + len(TEXT), end=pos + len(TEXT) + len(issue['quote']))]
    bad['strengths'] = []
    result = CraftReview.model_validate(bad, context={'draft': text})
    assert len({i.id for i in result.issues}) == 2


def test_missing_disposition_identifies_exact_unresolved_history_for_retry():
    from novelvideo.script_creation.craft_models import CraftReview
    first = CraftReview.model_validate(review(True), context={'draft': TEXT})
    revised = review(True)
    revised['issues'][0].update(quote='交出钥匙', prior_issue_id=first.issues[0].id)
    second = CraftReview.model_validate(revised, context={
        'draft': TEXT, 'issue_history': [first.issues[0].model_dump()]})
    final = review()
    final['resolved_issue_ids'] = [first.issues[0].id]
    with pytest.raises(ValueError) as error:
        CraftReview.model_validate(final, context={
            'draft': TEXT,
            'issue_history': [first.issues[0].model_dump(), second.issues[0].model_dump()]})
    assert 'missing explicit disposition: ' + second.issues[0].id in str(error.value)


def test_changed_quote_can_carry_unresolved_prior_issue():
    from novelvideo.script_creation.craft_models import CraftReview
    previous = CraftReview.model_validate(review(True), context={'draft': TEXT})
    bad = review(True)
    bad['issues'][0].update(quote='交出钥匙', prior_issue_id=previous.issues[0].id)
    result = CraftReview.model_validate(bad, context={'draft': TEXT, 'issue_history': [previous.issues[0].model_dump()]})
    assert result.status == 'needs_revision'
    assert result.issues[0].id != previous.issues[0].id
    assert not result.resolved_issue_ids


@pytest.mark.parametrize('duplicate', [False, True])
def test_unknown_or_duplicate_prior_issue_links_fail(duplicate):
    from novelvideo.script_creation.craft_models import CraftReview
    previous = CraftReview.model_validate(review(True), context={'draft': TEXT})
    bad = review(True)
    bad['issues'][0].update(quote='交出钥匙', prior_issue_id=previous.issues[0].id if duplicate else 'fabricated')
    if duplicate:
        bad['issues'].append(dict(bad['issues'][0], quote='失去住所'))
    with pytest.raises(ValueError, match='prior_issue_id'):
        CraftReview.model_validate(bad, context={'draft': TEXT, 'issue_history': [previous.issues[0].model_dump()]})


def test_prior_link_chain_keeps_partial_repairs_active():
    from novelvideo.script_creation.craft_models import CraftReview
    first = CraftReview.model_validate(review(True), context={'draft': TEXT})
    second_data = review(True)
    second_data['issues'][0].update(quote='交出钥匙', prior_issue_id=first.issues[0].id)
    second = CraftReview.model_validate(second_data, context={'draft': TEXT, 'issue_history': [first.issues[0].model_dump()]})
    third_data = review(True)
    third_data['issues'][0].update(quote='失去住所', prior_issue_id=second.issues[0].id)
    third = CraftReview.model_validate(third_data, context={'draft': TEXT, 'issue_history': [first.issues[0].model_dump(), second.issues[0].model_dump()]})
    assert third.status == 'needs_revision'
    assert not third.resolved_issue_ids


@pytest.mark.parametrize('explicit', [False, True])
def test_unchanged_linked_issue_inherits_full_ancestry(explicit):
    from novelvideo.script_creation.craft_models import CraftReview
    first = CraftReview.model_validate(review(True), context={'draft': TEXT})
    changed = review(True)
    changed['issues'][0].update(quote='交出钥匙', prior_issue_id=first.issues[0].id)
    second = CraftReview.model_validate(changed, context={'draft': TEXT, 'issue_history': [first.issues[0].model_dump()]})
    unchanged = review(True)
    unchanged['issues'][0].update(quote='交出钥匙')
    if explicit:
        unchanged['issues'][0]['prior_issue_id'] = second.issues[0].id
    result = CraftReview.model_validate(unchanged, context={'draft': TEXT, 'issue_history': [first.issues[0].model_dump(), second.issues[0].model_dump()]})
    assert result.status == 'needs_revision'
    assert result.issues[0].prior_issue_id == first.issues[0].id


@pytest.mark.parametrize('code', ['WORKBUDDY_OUTPUT_INVALID', 'DSH_OUTPUT_INVALID'])
async def test_runtime_wrapped_invalid_output_is_retried_once(code):
    from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError
    class WrappedRuntime(Runtime):
        async def run_structured(self, **kwargs):
            if len(self.calls) == 1:
                self.calls.append(kwargs)
                raise KnowledgeRuntimeError('invalid evidence', code=code)
            return await super().run_structured(**kwargs)
    runtime = WrappedRuntime({'markdown': TEXT}, review())
    result = await run_craft(runtime=runtime, prompt='计划', system_prompt='方法', kind='outline')
    assert result.status == 'passed'
    assert result.records[-1].attempts == 2


async def test_runtime_transport_failure_is_not_retried():
    from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError
    class BrokenRuntime:
        calls = 0
        async def run_structured(self, **kwargs):
            self.calls += 1
            raise KnowledgeRuntimeError('transport failed', code='NETWORK_ERROR')
    runtime = BrokenRuntime()
    with pytest.raises(KnowledgeRuntimeError):
        await run_craft(runtime=runtime, prompt='计划', system_prompt='方法', kind='outline')
    assert runtime.calls == 1


def test_evidence_end_cannot_exceed_source_length():
    from novelvideo.script_creation.craft_models import locate_evidence
    assert locate_evidence('原文', '原文', 0, 999) == (0, 2)
