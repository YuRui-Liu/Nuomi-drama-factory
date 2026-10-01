from types import SimpleNamespace

import pytest

from tests.script_creation.test_asset_extraction import setup  # noqa: F401
from novelvideo.script_creation.context_links import SceneContextLinks
from novelvideo.script_creation.asset_context import load_asset_authoring_context
from novelvideo.script_creation.store import DocumentConflict, DocumentNotFound
from novelvideo.script_creation.entities import EntityService
from novelvideo.models import NovelScene


async def test_explicit_scene_context_links_are_scoped_atomic_and_revision_bound(setup):
    documents, assets, extraction = setup
    doc = await documents.create(kind='scenes', title='场景表',
        markdown='## 折湾站\n\n- 视觉／光线：冷灰混凝土\n- 连续性约束：储水减少不可复原\n\n## 其他站\n\n绝不串入', client_mutation_id='doc')
    async def model(**kw):
        return {'assets': [dict(name='折湾站', source_block_id=doc.revision.blocks[0].id, evidence='折湾站')]}
    run = await extraction.start(asset_type='scene', document_id=doc.id, base_revision_id=doc.current_revision_id, client_mutation_id='run')
    run = await extraction.execute(run['id'], runtime=SimpleNamespace(run_structured=model), task_id='task')
    parent = await extraction.confirm(run['id'], base_revision_id=doc.current_revision_id, candidate_ids=[run['candidates'][0]['id']], client_mutation_id='confirm')
    parent_id = parent['result'][0]['asset_id']
    await assets.add_scene(NovelScene(name='折湾站水箱区', description='子场景自身描述'))
    await assets.add_scene(NovelScene(name='折湾站未关联区域'))
    registry = await EntityService(documents).assets('scene')
    child_id = next(a['asset_id'] for a in registry if a['name'] == '折湾站水箱区')
    service = SceneContextLinks(documents)
    await service.initialize()
    body = dict(source_asset_id=parent_id,target_asset_ids=[child_id],document_id=doc.id,
        base_revision_id=doc.current_revision_id,client_mutation_id='link')
    with pytest.raises(DocumentNotFound):
        await service.associate(**(body | {'target_asset_ids': [child_id, 'foreign-id']}))
    assert await service.list(child_id) == []
    result = await service.associate(**body)
    assert await service.associate(**body) == result
    await service.associate(**(body | {'client_mutation_id': 'replay-other-id'}))
    assert len(await service.list(child_id)) == 2
    context = load_asset_authoring_context(assets.db_path, 'scene', '折湾站水箱区')
    text = '\n'.join(c['text'] for c in context)
    assert '储水减少不可复原' in text and '绝不串入' not in text
    assert all(c['source_revision_id'] == doc.current_revision_id for c in context if c['source_block_id'])
    assert not any(c['source_block_id'] for c in load_asset_authoring_context(assets.db_path, 'scene', '折湾站未关联区域'))
    assert (await assets.get_scene('折湾站水箱区')).base_scene_id == ''
    await documents.save(doc.id, base_revision_id=doc.current_revision_id, markdown='新稿', client_mutation_id='edit')
    assert all(c['stale'] for c in await service.list(child_id))
    assert not any(c['source_block_id'] for c in load_asset_authoring_context(assets.db_path, 'scene', '折湾站水箱区'))
    with pytest.raises(DocumentConflict):
        await service.associate(**(body | {'client_mutation_id': 'stale'}))
