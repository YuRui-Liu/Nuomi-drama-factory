import json
import pytest
from novelvideo.script_creation.craft_pipeline import run_craft
from tests.script_creation.test_craft_pipeline import Runtime, TEXT, review


async def test_author_receives_failed_strategy_but_qc_does_not():
    runtime = Runtime({'markdown': TEXT}, review(True), {'markdown': TEXT}, review(True),
                      {'markdown': TEXT}, review(), review())
    result = await run_craft(runtime=runtime, prompt='人物计划', system_prompt='方法', kind='episode_script')
    repairs = [json.loads(c['prompt']) for c in runtime.calls if 'structure_repair' in c['prompt'] and json.loads(c['prompt'])['stage'].startswith('structure_repair')]
    assert repairs[1]['repair_history']
    assert repairs[1]['author_history'][0]['output']['markdown'] == TEXT
    assert repairs[0]['author_session_id'] == repairs[1]['author_session_id']
    for call in runtime.calls:
        request = json.loads(call['prompt'])
        if 'review' in request['stage']:
            assert 'author_history' not in request
            assert 'repair_history' not in request
    assert result.records[0].author_session_id


async def test_missing_repair_notes_cannot_be_treated_as_response_to_qc():
    from novelvideo.script_creation.craft_models import CraftDraft
    with pytest.raises(ValueError, match='repair_notes'):
        CraftDraft.model_validate({'markdown': TEXT}, context={'repair_issues': [{'id': 'one', 'severity': 'blocker'}]})


async def test_qc_never_reads_author_explanations_from_project_memory():
    memory = [{'id':'old','history':[{'author_attempt':{'strategy':'我这样改一定对'}},{'qc_resolution':'one'}]}]
    runtime = Runtime({'markdown':TEXT},review())
    await run_craft(runtime=runtime,prompt='计划',system_prompt='方法',kind='outline',memory=memory)
    assert '我这样改一定对' in runtime.calls[0]['prompt']
    assert '我这样改一定对' not in runtime.calls[1]['prompt']
    assert 'qc_resolution' in runtime.calls[1]['prompt']


async def test_historical_ids_cannot_be_confused_with_live_qc_dispositions():
    old_id = 'issue_9426400a64e6a0930fc8'
    memory = [{'id':'memory-key', 'source':'rewrite:old:rewrite', 'status':'resolved',
               'issue':{'id':old_id,'prior_issue_id':old_id,'reason':f'延续历史问题{old_id}的判断',
                        'quote':'旧稿原文','repair_goal':'核对人物去向'},
               'history':[{'qc_resolution':old_id},{'qc_issue':{'id':old_id,'quote':'旧稿原文'}}]}]
    runtime = Runtime({'markdown':TEXT},review(True),{'markdown':TEXT},review())
    await run_craft(runtime=runtime,prompt='计划',system_prompt='方法',kind='outline',memory=memory)
    assert old_id in runtime.calls[0]['prompt']
    for call in runtime.calls:
        request = json.loads(call['prompt'])
        if 'review' in request['stage']:
            assert old_id not in call['prompt']
            assert '旧稿原文' in call['prompt']
            assert '核对人物去向' in call['prompt']
    current = json.loads(runtime.calls[-1]['prompt'])['issue_history']
    assert current and current[0]['id'].startswith('issue_')
    assert memory[0]['issue']['id'] == old_id
