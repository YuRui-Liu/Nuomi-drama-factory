from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest

from novelvideo.narrative_groups.character_reference_repair import RepairStore, apply_repairs, assert_source, select_repair, bind_repair
from novelvideo.narrative_groups.planned_bindings import PlannedReferenceBinding


def record(**kw):
    return dict(request_id='request', repair_id='repair', project_id='p', episode_number=1,
        source_plan_revision_id='v1', source_reference_revision='ref', binding_id='b',
        entity_id='两名侍卫', character_name='侍卫甲乙', identity_id='guards', status='bound',
        error='', candidate_id='', reuse_identity=False, **kw)


def test_concurrent_idempotent_create_and_recovery(tmp_path):
    RepairStore(tmp_path, 'p')
    def insert(index):
        item = record()
        item['repair_id'] = f'repair-{index}'
        return RepairStore(tmp_path, 'p').insert(item)
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(insert, range(20)))
    assert len({v['repair_id'] for v in values}) == 1
    assert len(RepairStore(tmp_path, 'p').all()) == 1
    assert RepairStore(tmp_path, 'other').all() == []


def test_explicit_mapping_keeps_similar_groups_and_other_episode(tmp_path):
    store = RepairStore(tmp_path, 'p')
    store.save(record())
    def binding(entity, episode=1):
        return PlannedReferenceBinding.create(project_id='p', episode_number=episode,
            source_plan_revision_id='v1', asset_kind='character_identity', entity_id=entity,
            asset_slot_id='', status='missing_asset', resolution='auto_matched', display_label=entity)
    result = apply_repairs(store.path, [binding('两名侍卫'), binding('侍卫'), binding('三名侍卫'), binding('两名侍卫', 2)])
    assert result[0].asset_slot_id == 'character:侍卫甲乙:identity:guards:portrait'
    assert result[0].entity_id == '两名侍卫'
    assert all(not b.asset_slot_id for b in result[1:])


def test_revision_rejected_before_side_effects():
    with pytest.raises(ValueError, match='REFERENCE_REVISION_STALE'):
        assert_source(SimpleNamespace(), record(), 'wrong')


def test_same_plan_source_mapping_change_rejected_but_own_refresh_allowed(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setattr('novelvideo.narrative_groups.character_reference_repair.DirectorPlanStore', lambda p: SimpleNamespace(load_active=lambda e: SimpleNamespace(revision_id='v1')))
    ctx = SimpleNamespace(state_dir=tmp_path, output_dir=tmp_path, project_id='p')
    item = record()
    item.update(source_persisted=True, source_binding={'group_ids':['g'], 'shot_ids':['s'], 'asset_slot_id':''})
    with sqlite3.connect(tmp_path / 'data.db') as db:
        db.execute('CREATE TABLE planned_reference_bindings (project_id,episode_number,source_plan_revision_id,asset_kind,entity_id,asset_slot_id,group_ids_json,shot_ids_json)')
        db.execute('INSERT INTO planned_reference_bindings VALUES (?,?,?,?,?,?,?,?)', ('p',1,'v1','character_identity','两名侍卫','','["g"]','["s"]'))
    assert_source(ctx, item, 'ref')
    with sqlite3.connect(tmp_path / 'data.db') as db:
        db.execute("UPDATE planned_reference_bindings SET asset_slot_id='character:陌生人:portrait'")
    with pytest.raises(ValueError, match='SOURCE_CHANGED'):
        assert_source(ctx, item, 'ref')
    with sqlite3.connect(tmp_path / 'data.db') as db:
        db.execute("UPDATE planned_reference_bindings SET entity_id='guards',asset_slot_id='character:侍卫甲乙:state:guards'")
    item['owned_created_identity'] = ['侍卫甲乙', 'guards']
    assert_source(ctx, item, 'ref')
    with sqlite3.connect(tmp_path / 'data.db') as db:
        db.execute('DELETE FROM planned_reference_bindings')
    with pytest.raises(ValueError, match='SOURCE_CHANGED'):
        assert_source(ctx, item, 'ref')


@pytest.mark.asyncio
async def test_missing_image_cannot_create_duplicate(monkeypatch):
    monkeypatch.setattr('novelvideo.narrative_groups.character_reference_repair.assert_source', lambda *a: None)
    item = record()
    item.update(status='selected', reuse_identity=True)
    body = SimpleNamespace(expected_reference_revision='ref', character_name='新侍卫', identity_id='new', mode='create')
    sql = SimpleNamespace(get_character=lambda name: None)
    with pytest.raises(ValueError, match='MISSING_IMAGE_REUSES_IDENTITY'):
        await select_repair(SimpleNamespace(), sql, item, body)


@pytest.mark.asyncio
async def test_selected_existing_identity_never_becomes_repair_owned(monkeypatch):
    monkeypatch.setattr('novelvideo.narrative_groups.character_reference_repair.assert_source', lambda *a: None)
    item = record()
    item.update(status='selected', owned_created_identity=['original-created', 'original-id'])
    body = SimpleNamespace(expected_reference_revision='ref', character_name='侍卫甲乙', identity_id='guards', mode='create')
    sql = SimpleNamespace(get_character=lambda name: SimpleNamespace(name=name))
    with pytest.raises(ValueError, match='CHARACTER_EXISTS_SELECT_EXPLICITLY'):
        await select_repair(SimpleNamespace(), sql, item, body)


@pytest.mark.asyncio
async def test_unadopted_candidate_rejected_and_retry_recovers(tmp_path, monkeypatch):
    import novelvideo.api.routes.character_casting as casting
    monkeypatch.setattr('novelvideo.narrative_groups.character_reference_repair.assert_source', lambda *a: None)
    monkeypatch.setattr(casting, 'candidate_store', lambda ctx: SimpleNamespace(get=lambda key: SimpleNamespace(character_id='侍卫甲乙', identity_id='guards')))
    current = {'candidate_id': 'candidate', 'adoption_status': 'provisional'}
    monkeypatch.setattr(casting, 'current_version', lambda *a: current)
    ctx = SimpleNamespace(state_dir=tmp_path, project_id='p')
    changes = []
    async def update(*args, **kw):
        changes.append(kw)
    sql = SimpleNamespace(get_episode=lambda n: SimpleNamespace(identity_ids=['keep']), update_episode=update)
    item = record()
    item['status'] = 'selected'
    failed = await bind_repair(ctx, sql, item, 'candidate')
    assert failed['error'] == 'CANDIDATE_NOT_ADOPTED'
    assert changes == []
    current['adoption_status'] = 'adopted'
    bound = await bind_repair(ctx, sql, RepairStore(tmp_path, 'p').get('repair'), 'candidate')
    assert bound['status'] == 'bound'
    assert changes == [{'identity_ids': ['keep', 'guards']}]
    rejected = await bind_repair(ctx, sql, bound, 'wrong-candidate')
    assert rejected['status'] == 'bound'
    assert RepairStore(tmp_path, 'p').get('repair')['status'] == 'bound'


@pytest.mark.asyncio
async def test_cross_identity_candidate_rejected(tmp_path, monkeypatch):
    import novelvideo.api.routes.character_casting as casting
    monkeypatch.setattr('novelvideo.narrative_groups.character_reference_repair.assert_source', lambda *a: None)
    monkeypatch.setattr(casting, 'candidate_store', lambda ctx: SimpleNamespace(get=lambda key: SimpleNamespace(character_id='other', identity_id='other')))
    result = await bind_repair(SimpleNamespace(state_dir=tmp_path, project_id='p'), None, record(), 'candidate')
    assert result['error'] == 'CANDIDATE_SCOPE_MISMATCH'


@pytest.mark.asyncio
async def test_recovery_does_not_bind_foreign_candidate_or_write_for_viewer(tmp_path, monkeypatch):
    import novelvideo.api.routes.character_casting as casting
    from novelvideo.narrative_groups.character_reference_repair import recover_adopted_repair
    monkeypatch.setattr('novelvideo.narrative_groups.character_reference_repair.assert_source', lambda *a: None)
    monkeypatch.setattr(casting, 'current_version', lambda *a: {'candidate_id':'adopted', 'adoption_status':'adopted'})
    owner = SimpleNamespace(character_id='foreign', identity_id='foreign')
    monkeypatch.setattr(casting, 'candidate_store', lambda ctx: SimpleNamespace(get=lambda key: owner))
    item = record()
    item.update(status='selected')
    ctx = SimpleNamespace(state_dir=tmp_path, project_id='p')
    assert await recover_adopted_repair(ctx, None, item, can_bind=True) == item
    owner.character_id, owner.identity_id = item['character_name'], item['identity_id']
    exposed = await recover_adopted_repair(ctx, None, item, can_bind=False)
    assert exposed['candidate_id'] == 'adopted'
    assert exposed['status'] == 'binding_failed'
    assert item['candidate_id'] == ''
    assert not (tmp_path / 'data.db').exists()


@pytest.mark.asyncio
async def test_routes_enforce_scope_and_schema(tmp_path, monkeypatch):
    import httpx
    from fastapi import FastAPI, HTTPException
    from novelvideo.api.routes import character_reference_repair as api
    from novelvideo.api.auth import get_api_user
    ctx = SimpleNamespace(state_dir=tmp_path, project_id='p')
    calls = []
    async def resolve(project, user, required_role):
        calls.append(required_role)
        if project != 'p':
            raise HTTPException(404)
        if user['role'] == 'viewer' and required_role == 'editor':
            raise HTTPException(403)
        return SimpleNamespace(ctx=ctx)
    async def close(): pass
    async def make(ctx):
        return SimpleNamespace(close=close, get_all_characters=lambda: [])
    monkeypatch.setattr(api, 'resolve_project_scope', resolve)
    monkeypatch.setattr(api, 'make_sqlite_store_for_context', make)
    monkeypatch.setattr(api, 'assert_source', lambda *a: None)
    RepairStore(tmp_path, 'p').save(record())
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_api_user] = lambda: {'role': 'viewer'}
    base = '/projects/p/episodes/1/director-plans/v1/character-reference-repairs'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get(base + '/repair')
        assert response.status_code == 200
        assert response.json()['data']['existing_choices'] == []
        assert (await client.get(base.replace('/p/', '/other/') + '/repair')).status_code == 404
        assert (await client.get(base.replace('/1/', '/2/') + '/repair')).status_code == 404
        assert (await client.post(base, json={'binding_id':'b', 'request_id':'r', 'expected_reference_revision':'ref'})).status_code == 403
        assert (await client.post(base, json={'binding_id':'b'})).status_code == 422
    assert 'editor' in calls and 'viewer' in calls


@pytest.mark.asyncio
@pytest.mark.parametrize('published', [False, True])
@pytest.mark.parametrize('same_name_scene', [False, True])
async def test_real_preview_create_adopt_bind_recovers_unpublished_and_missing_media(tmp_path, monkeypatch, published, same_name_scene):
    from datetime import datetime, UTC
    from PIL import Image
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.models import NovelEpisode
    from novelvideo.production_workflow import ProductionWorkflowStore
    from novelvideo.narrative_groups.character_reference_repair import create_repair
    from novelvideo.narrative_groups.planned_binding_service import resolve_planned_reference_preview
    import novelvideo.api.routes.character_casting as casting
    from novelvideo.api.routes.character_reference_repair import CreateRequest
    state = tmp_path / 'state'
    sql = SQLiteStore('test/repair', output_dir=str(tmp_path), state_dir=str(state))
    await sql.initialize()
    await sql.add_episode(NovelEpisode(number=1, title='repair'))
    shot = SimpleNamespace(id='s', subject='两名侍卫', action='站在门口', asset_requirements=[{'kind':'character_identity','entity_key':'两名侍卫'}])
    plan = SimpleNamespace(revision_id='v1', groups=[SimpleNamespace(id='g', shots=[shot])])
    monkeypatch.setattr('novelvideo.narrative_groups.character_reference_repair.DirectorPlanStore', lambda p: SimpleNamespace(load_active=lambda e: plan))
    if published:
        original = PlannedReferenceBinding.create(project_id='p', episode_number=1, source_plan_revision_id='v1',
            asset_kind='character_identity', entity_id='两名侍卫', asset_slot_id='character:两名侍卫:state:两名侍卫',
            status='ready', resolution='auto_matched', display_label='两名侍卫', group_ids=('g',), shot_ids=('s',))
        await sql.replace_planned_reference_bindings_atomic(1, ['character_identity'], [original])
    if same_name_scene:
        scene = PlannedReferenceBinding.create(project_id='p', episode_number=1, source_plan_revision_id='v1',
            asset_kind='scene_base', entity_id='两名侍卫', asset_slot_id='', status='missing_asset',
            resolution='auto_matched', display_label='同名场景', group_ids=('g',))
        await sql.replace_planned_reference_bindings_atomic(1, ['scene_base'], [scene])
    workflow = ProductionWorkflowStore(state / 'production_workflow.json')
    if published:
        workflow.register_candidate_version(slot_id=original.asset_slot_id, asset_kind='character_state', version_id='missing',
            asset_path='assets/characters/两名侍卫/deleted.png', source_attempt_id='old', qc_passed=True,
            generation_metadata=None, actor='test', at=datetime.now(UTC))
    args = dict(project_id='p', episode_number=1, group_id='g', project_dir=tmp_path,
        active_plan_revision_id='v1', required_binding_keys=frozenset({('character_identity','两名侍卫','')}))
    ctx = SimpleNamespace(project_id='p', output_dir=tmp_path, state_dir=state, effective_role='editor')
    try:
        before = await resolve_planned_reference_preview(sql, workflow, **args)
        missing = next(b for b in before.bindings if b.asset_kind == 'character_identity')
        assert missing.status != 'ready'
        if published:
            assert missing.status == 'missing_image'
        repair = await create_repair(ctx, sql, 1, 'v1', CreateRequest(binding_id=missing.binding_id,
            expected_reference_revision=before.reference_revision, request_id='request', group_id='g'))
        repair.update(character_name='侍卫甲乙', identity_id='guards', status='selected')
        RepairStore(state, 'p').save(repair)
        portrait = tmp_path / 'assets' / 'characters' / '侍卫甲乙' / 'portrait_versions' / 'p.png'
        portrait.parent.mkdir(parents=True)
        Image.new('RGB', (8,8), 'red').save(portrait)
        slot_id = 'character:侍卫甲乙:identity:guards:portrait'
        workflow.register_candidate_version(slot_id=slot_id, asset_kind='character_portrait', version_id='adopted',
            asset_path=portrait.relative_to(tmp_path).as_posix(), source_attempt_id='task', qc_passed=True,
            generation_metadata={'candidate_id':'candidate', 'casting_adoption':{'actor':'test','candidate_id':'candidate','snapshot':{'id':'frozen'},'command_hash':'command'}}, actor='test', at=datetime.now(UTC))
        workflow.adopt_casting_version(slot_id=slot_id, version_id='adopted', actor='test', at=datetime.now(UTC))
        monkeypatch.setattr(casting, 'candidate_store', lambda ctx: SimpleNamespace(get=lambda key: SimpleNamespace(character_id='侍卫甲乙', identity_id='guards')))
        monkeypatch.setattr(casting, 'current_version', lambda *a: {'candidate_id':'candidate','adoption_status':'adopted'})
        # Simulate process exit after adoption persisted but before its callback.
        # Reopening the repair must discover the actual adopted scoped candidate.
        from novelvideo.api.routes import character_reference_repair as api
        async def resolve(*args, **kwargs): return SimpleNamespace(ctx=ctx)
        async def make(*args): return sql
        monkeypatch.setattr(api, 'resolve_project_scope', resolve)
        monkeypatch.setattr(api, 'make_sqlite_store_for_context', make)
        result = (await api.execute('p', 1, 'v1', {}, 'get', repair['repair_id']))['data']
        assert result['status'] == 'bound', result
        assert result['candidate_id'] == 'candidate'
        from novelvideo.narrative_groups.planned_binding_service import ReadOnlyPlannedBindingStore
        reopened = ReadOnlyPlannedBindingStore(state / 'data.db')
        after = await resolve_planned_reference_preview(reopened, workflow, **args)
        repaired = [b for b in after.bindings if b.asset_kind == 'character_identity']
        assert len(repaired) == 1
        assert repaired[0].status == 'ready'
        assert repaired[0].version_id == 'adopted'
        assert len(after.bindings) == 1 + int(same_name_scene)
        assert after.warnings == ()
        other = await resolve_planned_reference_preview(reopened, workflow, **{**args, 'group_id':'other', 'required_binding_keys':frozenset()})
        assert other.bindings == ()
    finally:
        await sql.close()
