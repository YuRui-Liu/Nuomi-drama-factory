from types import SimpleNamespace

import pytest

from novelvideo.script_creation.store import DocumentStore, DocumentValidation


@pytest.fixture
async def setup(tmp_path):
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.script_creation.asset_extraction import AssetExtractionService
    assets = SQLiteStore('test', str(tmp_path))
    await assets.initialize()
    store = DocumentStore(assets.db_path)
    await store.initialize()
    service = AssetExtractionService(store)
    await service.initialize()
    yield store, assets, service
    await assets.close()


async def test_character_preserves_complete_biography_and_only_explicit_design_fields(setup):
    store, assets, service = setup
    markdown = '## 岑砚\n\n身份：旧城测绘员。\n人物弧光：计划从谨慎走向承担责任。\n\n- 面部特征：短发，窄下颌\n- 服装：灰色工作外套\n\n## 老汀\n\n另一人物的小传'
    doc = await store.create(kind='people', title='人物', markdown=markdown, client_mutation_id='doc')
    visual_block = next(b for b in doc.revision.blocks if '面部特征' in b.markdown)
    async def model(**kw):
        return {'assets': [{'name': '岑砚', 'source_block_id': doc.revision.blocks[0].id, 'evidence': '岑砚',
            'field_evidence': [
                {'field': 'face_prompt', 'block_id': visual_block.id, 'text': '短发，窄下颌'},
                {'field': 'appearance_details', 'block_id': visual_block.id, 'text': '灰色工作外套'}]}]}
    run = await service.start(asset_type='character', document_id=doc.id,
        base_revision_id=doc.current_revision_id, client_mutation_id='extract')
    run = await service.execute(run['id'], runtime=SimpleNamespace(run_structured=model), task_id='task')
    candidate = run['candidates'][0]
    assert candidate['fields']['face_prompt'] == '短发，窄下颌'
    assert '旧城测绘员' in candidate['description'] and '人物弧光' in candidate['description']
    assert '另一人物' not in candidate['description']
    result = await service.confirm(run['id'], base_revision_id=doc.current_revision_id,
        candidate_ids=[candidate['id']], client_mutation_id='confirm')
    assert result['result'][0]['asset_type'] == 'character'
    await assets.load_graph_state()
    character = assets.get_character('岑砚')
    assert character.description == candidate['description']
    assert character.face_prompt == '短发，窄下颌'
    assert character.appearance_details == '灰色工作外套'
    assert character.age_group == '' and character.gender == ''
    assert character.voice_facts_json == '{}'
    from novelvideo.script_creation.asset_context import load_asset_authoring_context
    context = load_asset_authoring_context(assets.db_path, 'character', character.name)
    linked = [c for c in context if c['source_revision_id']]
    assert len(linked) == len(candidate['source_block_ids'])
    assert all(c['source_revision_id'] == doc.current_revision_id for c in linked)
    assert '人物弧光' in '\n'.join(c['text'] for c in linked)
    from novelvideo.character_visual.casting_source import load_sources
    documents, _ = await load_sources(assets.project_dir, assets)
    assert any('人物弧光' in d.text and d.kind == 'authoring_context' for d in documents.values())


async def test_scene_uses_visual_fields_and_unknown_type_does_not_become_interior(setup):
    store, assets, service = setup
    doc = await store.create(kind='scenes', title='场景',
        markdown='## 旧管线观察廊\n\n- 空间布局：北侧隔离网，南侧通道\n- 视觉/光线：冷色应急灯\n- 剧情作用：第十集计划揭露秘密', client_mutation_id='doc')
    async def model(**kw):
        return {'assets': [{'name': '旧管线观察廊', 'source_block_id': doc.revision.blocks[0].id, 'evidence': '旧管线观察廊',
            'visual_evidence': [{'block_id': doc.revision.blocks[1].id, 'text': '北侧隔离网，南侧通道'},
                                {'block_id': doc.revision.blocks[1].id, 'text': '冷色应急灯'}]}]}
    run = await service.start(asset_type='scene', document_id=doc.id, base_revision_id=doc.current_revision_id, client_mutation_id='extract')
    run = await service.execute(run['id'], runtime=SimpleNamespace(run_structured=model), task_id='task')
    candidate = run['candidates'][0]
    assert candidate['fields']['environment_prompt'] == '北侧隔离网，南侧通道；冷色应急灯'
    assert candidate['fields']['scene_type'] == ''
    await service.confirm(run['id'], base_revision_id=doc.current_revision_id,
        candidate_ids=[candidate['id']], client_mutation_id='confirm')
    scene = await assets.get_scene('旧管线观察廊')
    assert scene.environment_prompt == candidate['fields']['environment_prompt']
    assert scene.scene_type == ''
    assert '计划揭露秘密' in scene.description and '秘密' not in scene.environment_prompt


async def test_authored_zombie_template_preserves_infection_design_without_expanding_crowd(setup):
    store, assets, service = setup
    doc = await store.create(kind='people', title='人物表', markdown=(
        '## 普通尸群模板\n\n用途：重复使用一个代表性个体，不按尸群数量新增人物。\n\n'
        '- 感染面部特征：灰白肤色，浑浊双眼，保留人类面部结构\n'
        '- 服装：褪色旧外套\n- 体型：瘦削人形，双足直立\n\n'
        '状态变体：服装变化仍沿用此模板。\n'), client_mutation_id='zombie-doc')
    visual = next(b for b in doc.revision.blocks if '感染面部特征' in b.markdown)
    async def model(**kw):
        return {'assets': [{'name': '普通尸群模板', 'source_block_id': doc.revision.blocks[0].id,
            'evidence': '普通尸群模板', 'field_evidence': [
                {'field': 'face_prompt', 'block_id': visual.id, 'text': '灰白肤色，浑浊双眼，保留人类面部结构'},
                {'field': 'body_type', 'block_id': visual.id, 'text': '瘦削人形，双足直立'}]}]}
    run = await service.start(asset_type='character', document_id=doc.id,
        base_revision_id=doc.current_revision_id, client_mutation_id='zombie-extract')
    run = await service.execute(run['id'], runtime=SimpleNamespace(run_structured=model), task_id='task')
    assert len(run['candidates']) == 1
    candidate = run['candidates'][0]
    assert candidate['fields']['face_prompt'] == '灰白肤色，浑浊双眼，保留人类面部结构'
    await service.confirm(run['id'], base_revision_id=doc.current_revision_id,
        candidate_ids=[candidate['id']], client_mutation_id='zombie-confirm')
    await assets.load_graph_state()
    character = assets.get_character('普通尸群模板')
    assert character.face_prompt == candidate['fields']['face_prompt']
    assert character.body_type == '瘦削人形，双足直立'
    assert '服装变化仍沿用此模板' in character.description
    assert character.voice_facts_json == '{}'


async def test_type_must_match_document_and_legacy_props_scope_is_preserved(setup):
    from novelvideo.script_creation.prop_extraction import PropExtractionService
    store, _, service = setup
    doc = await store.create(kind='people', title='人物', markdown='## 岑砚', client_mutation_id='doc')
    with pytest.raises(DocumentValidation):
        await service.start(asset_type='scene', document_id=doc.id, base_revision_id=doc.current_revision_id, client_mutation_id='wrong')
    run = await service.start(asset_type='character', document_id=doc.id, base_revision_id=doc.current_revision_id, client_mutation_id='right')
    assert await PropExtractionService(store).list() == []
    assert [r['id'] for r in await service.list(asset_type='character')] == [run['id']]


async def test_linked_context_changes_voice_snapshot_and_reaches_identity_design(setup):
    from novelvideo.models import NovelCharacter
    from novelvideo.media_capabilities.tts.character_voice import prepare_character_voice_request, character_voice_snapshot, voice_snapshot_digest
    from novelvideo.agents.identity_planner import IdentityPlanner
    from novelvideo.script_creation.asset_context import load_asset_authoring_context
    store, assets, service = setup
    character = NovelCharacter(name='岑砚', description='已有小传，不覆盖',
        voice_facts={'vocalization_mode': 'dialogue', 'provenance': 'human'})
    await assets.add_character(character)
    doc = await store.create(kind='people', title='人物表', markdown='## 岑砚\n\n身份：旧城测绘员。未来计划：成为领队。', client_mutation_id='doc')
    async def model(**kw):
        return {'assets': [dict(name='岑砚', source_block_id=doc.revision.blocks[0].id, evidence='岑砚')]}
    run = await service.start(asset_type='character', document_id=doc.id, base_revision_id=doc.current_revision_id, client_mutation_id='extract')
    run = await service.execute(run['id'], runtime=SimpleNamespace(run_structured=model), task_id='task')
    result = await service.confirm(run['id'], base_revision_id=doc.current_revision_id,
        candidate_ids=[run['candidates'][0]['id']], client_mutation_id='confirm')
    assert result['result'][0]['action'] == 'reused'
    await assets.load_graph_state()
    role = assets.get_character('岑砚')
    assert role.description == '已有小传，不覆盖'
    payload = prepare_character_voice_request(role, slot='default', db_path=assets.db_path)
    assert '旧城测绘员' in payload['voice_description']
    assert payload['profile_digest'] == voice_snapshot_digest(character_voice_snapshot(role, db_path=assets.db_path))
    planner = IdentityPlanner.__new__(IdentityPlanner)
    planner.cognee_store = assets
    assert '旧城测绘员' in planner._character_authoring_context(role)
    assert '未来计划不代表已发生剧情' in planner._build_character_info(['岑砚'])
    await store.save(doc.id, base_revision_id=doc.current_revision_id,
        markdown='## 岑砚\n\n新版小传', client_mutation_id='edit')
    assert payload['profile_digest'] != voice_snapshot_digest(character_voice_snapshot(role, db_path=assets.db_path))
    assert not any(c['source_revision_id'] for c in load_asset_authoring_context(assets.db_path, 'character', '岑砚'))


async def test_fullwidth_scene_visual_label_preserves_exact_quote(setup):
    from novelvideo.script_creation.prop_extraction import AssetExtractionOutput, _candidates
    store, _, _ = setup
    doc = await store.create(kind='scenes', title='场景', markdown='## 折湾站\n\n- **视觉／光线：**冷灰混凝土、褪色的站内导向标记与手绘路线并置。\n- **剧情作用：**建立断水压力', client_mutation_id='doc')
    output = AssetExtractionOutput(assets=[dict(name='折湾站', source_block_id=doc.revision.blocks[0].id,
        evidence='折湾站', visual_evidence=[dict(block_id=doc.revision.blocks[1].id, text='冷灰混凝土、褪色的站内导向标记与手绘路线并置。')])])
    assert _candidates(output, doc, 'scene')[0]['fields']['environment_prompt'] == '冷灰混凝土、褪色的站内导向标记与手绘路线并置。'


@pytest.mark.parametrize('prefix', ['视觉／光线：', '**视觉／光线：**', '- **视觉／光线：**'])
async def test_labeled_visual_quotes_only_normalize_label_format(setup, prefix):
    from novelvideo.script_creation.prop_extraction import AssetExtractionOutput, _candidates
    store, _, _ = setup
    doc = await store.create(kind='scenes', title='场景', markdown='## 折湾站\n\n- **视觉／光线：**冷灰混凝土、褪色标记。\n- **剧情作用：**未来揭露秘密', client_mutation_id='doc')
    item = dict(name='折湾站', source_block_id=doc.revision.blocks[0].id, evidence='折湾站',
        visual_evidence=[dict(block_id=doc.revision.blocks[1].id, text=prefix+'冷灰混凝土、褪色标记。')])
    assert _candidates(AssetExtractionOutput(assets=[item]), doc, 'scene')[0]['fields']['environment_prompt'] == '冷灰混凝土、褪色标记。'
    for false_quote in [prefix+'冷灰混凝土，褪色标记。', prefix+'未来揭露秘密', '空间布局：冷灰混凝土、褪色标记。']:
        item['visual_evidence'][0]['text'] = false_quote
        with pytest.raises(DocumentValidation):
            _candidates(AssetExtractionOutput(assets=[item]), doc, 'scene')


async def test_partial_invalid_candidate_is_excluded_and_raw_can_revalidate_free(setup, monkeypatch):
    from novelvideo.script_creation import prop_extraction as module
    store, _, service = setup
    doc = await store.create(kind='props', title='道具', markdown='## 手电\n\n- 外观材质：金属筒', client_mutation_id='doc')
    async def model(**kw):
        return {'props': [dict(name='手电', evidence='手电', source_block_id=doc.revision.blocks[0].id),
            dict(name='不存在的钥匙', evidence='改写证据', source_block_id=doc.revision.blocks[0].id)]}
    run = await service.start(asset_type='prop', document_id=doc.id, base_revision_id=doc.current_revision_id, client_mutation_id='extract')
    validator = module._preview_candidates
    def fail(*args):
        raise DocumentValidation('旧版本校验失败')
    monkeypatch.setattr(module, '_preview_candidates', fail)
    with pytest.raises(DocumentValidation):
        await service.execute(run['id'], runtime=SimpleNamespace(run_structured=model), task_id='task')
    failed = await service.get(run['id'])
    assert failed['raw_output']['props'][1]['name'] == '不存在的钥匙'
    monkeypatch.setattr(module, '_preview_candidates', validator)
    ready = await service.revalidate(run['id'])
    assert ready['status'] == 'ready'
    assert [c['name'] for c in ready['candidates']] == ['手电']
    assert ready['rejected_candidates'][0]['evidence'] == '改写证据'
    assert '已排除' in ready['candidates'][0]['warnings'][-1]
    await store.save(doc.id, base_revision_id=doc.current_revision_id, markdown='新版', client_mutation_id='edit')
    from novelvideo.script_creation.store import DocumentConflict
    with pytest.raises(DocumentConflict):
        await service.revalidate(run['id'])


async def test_unique_quote_recovery_and_state_labels_are_not_assets(setup):
    from novelvideo.script_creation.prop_extraction import PropExtractionOutput, _preview_candidates
    store, _, _ = setup
    doc = await store.create(kind='props', title='道具', markdown='## 旧城地图\n\n- 外观材质：旧纸地图。\n\n## 自动播报机\n\n- 外观材质：自动播报机可有“回放”和“实时警报”两类状态指示；老式指示灯为金属外壳。', client_mutation_id='doc')
    quote = '自动播报机可有“回放”和“实时警报”两类状态指示'
    output = PropExtractionOutput(props=[
        dict(name='旧城地图', source_block_id='wrong', evidence='## 旧城地图\n\n- 外观材质：旧纸地图。',
            visual_evidence=[dict(block_id='wrong', text='旧纸地图。')], source_block_ids=['wrong']),
        dict(name='“回放”', source_block_id=doc.revision.blocks[-1].id, evidence=quote),
        dict(name='“实时警报”', source_block_id=doc.revision.blocks[-1].id, evidence=quote),
        dict(name='自动播报机', source_block_id='wrong', evidence='## 自动播报机'),
        dict(name='老式指示灯', source_block_id='wrong', evidence='老式指示灯为金属外壳。'),
        dict(name='不存在的道具', source_block_id='wrong', evidence='不存在的道具')])
    candidates, rejected = _preview_candidates(output, doc, 'prop')
    assert [c['name'] for c in candidates] == ['旧城地图', '自动播报机']
    assert candidates[0]['fields']['visual_prompt'] == '旧纸地图。'
    assert '正式道具条目' in rejected[0]['reason']
    assert len(rejected) == 4
    assert output.props[0].source_block_id == 'wrong'  # Raw result remains immutable.


async def test_props_only_formal_table_rows_or_combined_headings(setup):
    from novelvideo.script_creation.prop_extraction import PropExtractionOutput, _preview_candidates
    store, _, _ = setup
    doc = await store.create(kind='props', title='道具表', markdown='## 医疗用品\n\n| 道具名称 | 外观材质 |\n| --- | --- |\n| 药箱 | 铁壳、磨损扣件 |\n| 药瓶 | 玻璃瓶 |\n\n## 空水桶、检修灯与备用电池\n\n- 外观材质：空水桶有提手；检修灯灯罩有划痕。', client_mutation_id='doc')
    def candidate(name):
        block = next(b for b in doc.revision.blocks if name in b.markdown)
        return dict(name=name, source_block_id=block.id, evidence=name)
    output = PropExtractionOutput(props=[candidate(n) for n in ['药箱', '药瓶', '空水桶', '检修灯', '备用电池', '磨损扣件', '提手']])
    from novelvideo.script_creation.prop_extraction import Quote
    output.props[0].visual_evidence = [Quote(block_id=output.props[0].source_block_id, text='铁壳、磨损扣件')]
    accepted, rejected = _preview_candidates(output, doc, 'prop')
    assert [c['name'] for c in accepted] == ['药箱', '药瓶', '空水桶', '检修灯', '备用电池']
    assert [r['name'] for r in rejected] == ['磨损扣件', '提手']
    assert accepted[0]['visual_prompt'] == '铁壳、磨损扣件'
