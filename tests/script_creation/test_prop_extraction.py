from types import SimpleNamespace

import pytest

from novelvideo.script_creation.store import DocumentConflict, DocumentStore


@pytest.fixture
async def setup(tmp_path):
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.script_creation.prop_extraction import PropExtractionService
    assets = SQLiteStore('demo', str(tmp_path))
    await assets.initialize()
    store = DocumentStore(assets.db_path)
    await store.initialize()
    service = PropExtractionService(store)
    await service.initialize()
    doc = await store.create(kind='props', title='道具表',
        markdown='## 记录本、手电与铅笔\n- 外观材质：记录本是黑色纸封；手电为金属筒。\n- 连续性约束：记录本不可消失。',
        client_mutation_id='doc')
    yield store, assets, service, doc
    await assets.close()


async def preview(service, doc):
    block = doc.revision.blocks[0]
    calls = []
    async def model(**kwargs):
        calls.append(kwargs)
        return {'props': [
            {'name': '记录本', 'source_block_id': block.id, 'evidence': '记录本、手电与铅笔',
             'visual_evidence': [{'block_id': block.id, 'text': '记录本是黑色纸封'}]},
            {'name': '手电', 'source_block_id': block.id, 'evidence': '记录本、手电与铅笔',
             'visual_evidence': [{'block_id': block.id, 'text': '手电为金属筒'}]},
            {'name': '铅笔', 'source_block_id': block.id, 'evidence': '记录本、手电与铅笔'},
        ]}
    run = await service.start(document_id=doc.id, base_revision_id=doc.current_revision_id,
        client_mutation_id='extract')
    result = await service.execute(run['id'], runtime=SimpleNamespace(run_structured=model), task_id='task')
    assert (await service.execute(run['id'], runtime=None, task_id='replay')) == result
    assert len(calls) == 1
    return result


async def test_preview_is_zero_asset_write_and_selected_commit_reuses_without_overwrite(setup):
    from novelvideo.models import NovelProp
    store, assets, service, doc = setup
    await assets.add_prop(NovelProp(name='记录本', description='人工确认描述', visual_prompt='现有外观'))
    run = await preview(service, doc)
    assert run['status'] == 'ready'
    assert len(await assets.list_props()) == 1
    assert run['candidates'][0]['action'] == 'reuse'
    assert run['candidates'][2]['warnings']
    assert run['candidates'][2]['missing_visual_description'] is True
    args = dict(base_revision_id=doc.current_revision_id,
        candidate_ids=[c['id'] for c in run['candidates'][:2]], client_mutation_id='confirm')
    result = await service.confirm(run['id'], **args)
    assert result['status'] == 'committed'
    assert [r['action'] for r in result['result']] == ['reused', 'created']
    assert len(await assets.list_props()) == 2
    assert (await assets.get_prop('记录本')).description == '人工确认描述'
    assert (await assets.get_prop('手电')).visual_prompt == '手电为金属筒'
    assert await assets.get_prop('铅笔') is None
    assert await service.confirm(run['id'], **args) == result
    async with store._db() as db:
        links = await (await db.execute('SELECT * FROM script_prop_import_links')).fetchall()
    assert len(links) == 2
    assert {r['source_block_id'] for r in links} == {doc.revision.blocks[0].id}


async def test_stale_preview_cannot_write_assets(setup):
    store, assets, service, doc = setup
    run = await preview(service, doc)
    await store.save(doc.id, base_revision_id=doc.current_revision_id, markdown='已改道具', client_mutation_id='edit')
    with pytest.raises(DocumentConflict):
        await service.confirm(run['id'], base_revision_id=doc.current_revision_id,
            candidate_ids=[run['candidates'][0]['id']], client_mutation_id='confirm')
    assert await assets.list_props() == []


@pytest.mark.parametrize('name,evidence', [('记录本、手电与铅笔', '记录本、手电与铅笔'), ('手电与铅笔', '手电与铅笔'), ('连续性约束', '连续性约束'), ('钥匙', '记录本')])
async def test_invalid_or_combined_names_fail_without_creating_assets(setup, name, evidence):
    _, assets, service, doc = setup
    run = await service.start(document_id=doc.id, base_revision_id=doc.current_revision_id, client_mutation_id='extract')
    async def model(**kw):
        return {'props': [{'name': name, 'evidence': evidence, 'source_block_id': doc.revision.blocks[0].id}]}
    with pytest.raises(ValueError):
        await service.execute(run['id'], runtime=SimpleNamespace(run_structured=model), task_id='task')
    failed = await service.get(run['id'])
    assert failed['status'] == 'failed'
    assert failed['rejected_candidates'][0]['name'] == name
    assert failed['raw_output']['props'][0]['evidence'] == evidence
    assert await assets.list_props() == []


async def test_concurrent_named_asset_is_reused_and_cross_run_links_are_unique(setup):
    from novelvideo.models import NovelProp
    store, assets, service, doc = setup
    first = await preview(service, doc)
    await assets.add_prop(NovelProp(name='手电', description='预览后人工创建', visual_prompt='人工外观'))
    picked = first['candidates'][1]['id']
    result = await service.confirm(first['id'], base_revision_id=doc.current_revision_id,
        candidate_ids=[picked], client_mutation_id='confirm')
    assert result['result'][0]['action'] == 'reused'
    assert (await assets.get_prop('手电')).visual_prompt == '人工外观'
    second = await service.start(document_id=doc.id, base_revision_id=doc.current_revision_id, client_mutation_id='extract-again')
    async def model(**kw):
        return {'props': [{'name': '手电', 'source_block_id': doc.revision.blocks[0].id, 'evidence': '手电'}]}
    second = await service.execute(second['id'], runtime=SimpleNamespace(run_structured=model), task_id='second')
    await service.confirm(second['id'], base_revision_id=doc.current_revision_id,
        candidate_ids=[second['candidates'][0]['id']], client_mutation_id='confirm-again')
    async with store._db() as db:
        assert (await (await db.execute('SELECT count(*) FROM script_prop_import_links')).fetchone())[0] == 1


async def test_missing_runtime_fails_without_rule_based_fallback(setup):
    _, assets, service, doc = setup
    run = await service.start(document_id=doc.id, base_revision_id=doc.current_revision_id, client_mutation_id='extract')
    with pytest.raises(ValueError, match='运行时不可用'):
        await service.execute(run['id'], runtime=None, task_id='task')
    assert (await service.get(run['id']))['status'] == 'failed'
    assert await assets.list_props() == []


async def test_extraction_checks_revision_before_model_and_start_payload_replay(setup):
    store, _, service, doc = setup
    args = dict(document_id=doc.id, base_revision_id=doc.current_revision_id, client_mutation_id='extract')
    run = await service.start(**args)
    assert await service.start(**args) == run
    changed = await store.save(doc.id, base_revision_id=doc.current_revision_id, markdown='新道具表', client_mutation_id='edit')
    with pytest.raises(DocumentConflict):
        await service.start(**{**args, 'base_revision_id': changed.current_revision_id})
    calls = []
    async def model(**kw):
        calls.append(kw)
    with pytest.raises(DocumentConflict):
        await service.execute(run['id'], runtime=SimpleNamespace(run_structured=model), task_id='task')
    assert not calls
    assert (await service.get(run['id']))['status'] == 'needs_rebase'


async def test_narrative_quote_is_not_accepted_as_visual_prompt(setup):
    _, _, service, doc = setup
    run = await service.start(document_id=doc.id, base_revision_id=doc.current_revision_id, client_mutation_id='extract')
    async def model(**kw):
        return {'props': [{'name': '记录本', 'source_block_id': doc.revision.blocks[0].id, 'evidence': '记录本',
            'visual_evidence': [{'block_id': doc.revision.blocks[0].id, 'text': '记录本不可消失'}]}]}
    with pytest.raises(ValueError, match='视觉字段'):
        await service.execute(run['id'], runtime=SimpleNamespace(run_structured=model), task_id='task')


async def test_unselected_or_foreign_candidate_makes_confirmation_atomic(setup):
    _, assets, service, doc = setup
    run = await preview(service, doc)
    with pytest.raises(ValueError, match='不属于当前预览'):
        await service.confirm(run['id'], base_revision_id=doc.current_revision_id,
            candidate_ids=[run['candidates'][0]['id'], 'other-preview'], client_mutation_id='confirm')
    assert await assets.list_props() == []


async def test_mixed_visual_quote_warns_without_discarding_extracted_props(setup):
    _, _, service, doc = setup
    run = await service.start(document_id=doc.id, base_revision_id=doc.current_revision_id, client_mutation_id='extract')
    block_id = doc.revision.blocks[0].id
    async def model(**kw):
        return {'props': [
            {'name': '记录本', 'source_block_id': block_id, 'evidence': '记录本',
             'visual_evidence': [{'block_id': block_id, 'text': '记录本是黑色纸封；手电为金属筒。'}]},
            {'name': '手电', 'source_block_id': block_id, 'evidence': '手电'},
        ]}
    run = await service.execute(run['id'], runtime=SimpleNamespace(run_structured=model), task_id='task')
    assert run['status'] == 'ready'
    assert [c['name'] for c in run['candidates']] == ['记录本', '手电']
    assert run['candidates'][0]['visual_prompt'] == ''
    assert run['candidates'][0]['missing_visual_description']
    assert any('其他道具' in warning for warning in run['candidates'][0]['warnings'])


def test_actual_project_medical_and_lighting_visual_quotes_keep_component_references():
    from novelvideo.script_creation.prop_extraction import PropExtractionOutput, _candidates
    # Verbatim fields read from ccbdf66f1dc1433bab42bb7f4e25a951,
    # revision 48ac9516d32b416bb0cf2e114f672f35, sections 7 and 9.
    medical = '旧药箱有磨损扣件和潮湿污迹，内部药瓶规格与剩余数量可识别；空水桶为不同来源的旧容器，有凹痕、污垢和可见容量差异。'
    lighting = '检修灯为旧式工作灯，灯罩有划痕和积灰，光线间歇闪烁；备用电池外壳有旧标签、腐蚀或磨损。'
    rows = [
        ('药箱', 'medical', '旧药箱有磨损扣件和潮湿污迹，内部药瓶规格与剩余数量可识别'),
        ('药瓶', 'medical', '内部药瓶规格与剩余数量可识别'),
        ('空水桶', 'medical', '空水桶为不同来源的旧容器，有凹痕、污垢和可见容量差异'),
        ('检修灯', 'lighting', '检修灯为旧式工作灯，灯罩有划痕和积灰，光线间歇闪烁'),
        ('备用电池', 'lighting', '备用电池外壳有旧标签、腐蚀或磨损'),
    ]
    document = SimpleNamespace(revision=SimpleNamespace(blocks=[
        SimpleNamespace(id='medical', markdown='- **外观材质：**' + medical),
        SimpleNamespace(id='lighting', markdown='- **外观材质：**' + lighting),
    ]))
    output = PropExtractionOutput(props=[dict(name=name, source_block_id=block_id, evidence=quote,
        visual_evidence=[dict(block_id=block_id, text=quote)]) for name, block_id, quote in rows])
    result = _candidates(output, document)
    assert [c['visual_prompt'] for c in result] == [row[2] for row in rows]
    assert all(not c['missing_visual_description'] for c in result)


def test_component_mention_is_not_a_second_independent_prop_description():
    from novelvideo.script_creation.prop_extraction import PropExtractionOutput, _candidates
    text = '- 外观材质：手电的电池仓盖有划痕，电池装在底部；绳索有磨损，绳端打结。'
    document = SimpleNamespace(revision=SimpleNamespace(blocks=[SimpleNamespace(id='b', markdown=text)]))
    rows = [('手电', '手电的电池仓盖有划痕，电池装在底部'), ('电池', ''),
            ('绳索', '绳索有磨损，绳端打结'), ('绳端', '')]
    output = PropExtractionOutput(props=[dict(name=name, source_block_id='b', evidence=name,
        visual_evidence=[dict(block_id='b', text=quote)] if quote else []) for name, quote in rows])
    result = _candidates(output, document)
    assert result[0]['visual_prompt'] == rows[0][1]
    assert result[2]['visual_prompt'] == rows[2][1]


def test_actual_combination_title_does_not_become_single_prop():
    from novelvideo.script_creation.prop_extraction import PropExtractionOutput, _candidates
    document = SimpleNamespace(revision=SimpleNamespace(blocks=[
        SimpleNamespace(id='b', markdown='## 8. 闸门、阀轮、压力表、配重板与锁链')]))
    with pytest.raises(ValueError, match='组合'):
        _candidates(PropExtractionOutput(props=[dict(name='配重板与锁链', source_block_id='b', evidence='配重板与锁链')]), document)


def test_prop_task_uses_existing_creative_runtime_registration():
    import novelvideo.task_backend.runners.script_creation  # noqa: F401
    from novelvideo.task_backend.registry import get_project_task_runner_registration
    assert get_project_task_runner_registration('script_creation_prop_extraction').text_task_role == 'script_creation'
