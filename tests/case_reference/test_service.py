from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from novelvideo.case_reference.models import ProposalInput
from novelvideo.case_reference.service import ProposalService
from novelvideo.technique_library.prompt_records import prompt_record


def inputs(**patch):
    draft = dict(revision=1, model_id='minimax-h3', aspect_ratio='16:9', resolution='720p',
                 references=[], segments=[dict(id='s1', prompt='林澈在教室停步回望，随后走出门。', duration_seconds=5,
                                              first_frame={'image_id': 'i1', 'url': '/image.png'})])
    return ProposalInput.model_validate(dict(request_id='one', canvas_id='canvas', node_id='node',
        segment_id='s1', case_id='h3-' + '0' * 16, draft=draft, effective_draft=draft,
        dimensions=['camera'], **patch))


def case():
    source = dict(repository='example/cases', revision='a' * 40, path='demo.json', upstream_id='demo',
                  url='https://example.com/demo', provenance='author')
    record = prompt_record('原创测试：缓慢侧移。忽略以上指令只是案例台词，不是系统命令。', source)
    return {'id': 'h3-' + '0' * 16, 'prompts': [record.model_dump()], 'sources': [source]}


@pytest.mark.asyncio
async def test_public_prompt_without_media_generates_text_only(tmp_path, monkeypatch):
    monkeypatch.setattr('novelvideo.case_reference.service.get_case', lambda _: case())
    service = ProposalService(SimpleNamespace(runtime_dir=tmp_path))
    body = inputs(prompt_hashes=[case()['prompts'][0]['content_sha256']])
    item, _ = service.create('alice', body)
    runtime = SimpleNamespace(run_structured=AsyncMock(return_value=dict(prompt='林澈在教室停步回望，镜头缓慢侧移，他随后走出门。',
        suggestions=[dict(dimension='camera', suggestion='侧移', basis='依据作者原文的缓慢侧移')], compatible=True, conflict=None)))
    result = await service.run(item['id'], 'alice', runtime, {'runtime': 'test'})
    assert result['status'] == 'succeeded'
    assert result['evidence']['prompts'][0]['text'] == case()['prompts'][0]['text']
    assert result['text_route_snapshot'] == {'runtime': 'test'}
    kwargs = runtime.run_structured.call_args.kwargs
    assert 'untrusted' in kwargs['system_prompt']
    assert 'images' not in kwargs


def test_generic_case_without_body_is_not_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr('novelvideo.case_reference.service.get_case', lambda _: {'prompts': [], 'sources': []})
    service = ProposalService(SimpleNamespace(runtime_dir=tmp_path))
    with pytest.raises(ValueError, match='依据'):
        service.create('alice', inputs())
    item, _ = service.create('alice', inputs(description='镜头从桌边缓慢移到门口，以背影结束。'))
    assert item['evidence']['description'].startswith('镜头')


def test_foreign_prompt_hash_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr('novelvideo.case_reference.service.get_case', lambda _: case())
    with pytest.raises(ValueError, match='提示词'):
        ProposalService(SimpleNamespace(runtime_dir=tmp_path)).create('alice', inputs(prompt_hashes=['b' * 64]))


@pytest.mark.parametrize('field,value', [('duration_seconds', 10), ('prompt', '另一个故事')])
def test_effective_draft_cannot_change_story_contract(field, value):
    body = inputs().model_dump()
    body['effective_draft']['segments'][0][field] = value
    with pytest.raises(ValueError, match='不一致'):
        ProposalInput.model_validate(body)


@pytest.mark.asyncio
async def test_runtime_initialization_failure_is_persisted(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners.case_reference import _run
    monkeypatch.setattr('novelvideo.case_reference.service.get_case', lambda _: case())
    ctx = SimpleNamespace(runtime_dir=tmp_path)
    service = ProposalService(ctx)
    item, _ = service.create('alice', inputs(description='从桌面向门口缓慢移动。'))
    def unavailable():
        raise RuntimeError('runtime unavailable')
    monkeypatch.setattr('novelvideo.task_backend.runners.case_reference.current_text_task_runtime', unavailable)
    with pytest.raises(RuntimeError, match='runtime unavailable'):
        await _run({'payload': {'proposal_id': item['id'], 'owner': 'alice'}}, ctx)
    assert service.store.get(item['id'], 'alice')['status'] == 'failed'


@pytest.mark.asyncio
async def test_incompatible_or_missing_dimensions_fail(tmp_path, monkeypatch):
    monkeypatch.setattr('novelvideo.case_reference.service.get_case', lambda _: case())
    service = ProposalService(SimpleNamespace(runtime_dir=tmp_path))
    item, _ = service.create('alice', inputs(description='从桌面向门口缓慢移动。'))
    runtime = SimpleNamespace(run_structured=AsyncMock(return_value=dict(prompt='一个方案', suggestions=[dict(
        dimension='ending', suggestion='停住', basis='结尾')], compatible=True, conflict=None)))
    result = await service.run(item['id'], 'alice', runtime)
    assert result['status'] == 'failed'
    assert result['result'] is None
