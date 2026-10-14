"""Fixed direction references are authority independent of canvas autosave."""
import json
import asyncio
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from novelvideo.creation.canvas_workspaces import CanvasCreate, CanvasWorkspace
from novelvideo.creation.projection import ProjectionService
from novelvideo.creation.store import ObjectStore
from novelvideo.freezone.canvas_store import canvas_path


@pytest.fixture
def context(tmp_path):
    return SimpleNamespace(state_dir=tmp_path/'state', output_dir=tmp_path/'output',
                           project_id='project', requester_user_id='actor')


@pytest.fixture
def service(context):
    from novelvideo.creation.library_references import DirectionReferences
    return DirectionReferences(context)


@pytest.fixture
def canvas(context):
    objects = ObjectStore(context.state_dir)
    result = CanvasWorkspace(objects, project_id=context.project_id).create('actor',
        CanvasCreate(request_id='canvas', kind='free', title='Free'))
    assert ProjectionService(objects).apply(result.operation_id).status == 'applied'
    cid = result.object_refs[0]['canvas_id']
    path = canvas_path(context.state_dir, cid)
    value = json.loads(path.read_text())
    value['nodes'] = [
        {'id':'group', 'type':'groupNode', 'data':{}},
        {'id':'node', 'type':'videoGenNode', 'parentId':'group', 'data':{}},
        {'id':'other', 'type':'uploadNode', 'data':{}},
    ]
    path.write_text(json.dumps(value))
    return cid


def request(service, scope, entry=None, action='apply', request_id='apply', kind='style'):
    current = service.inspect(scope)
    return {'request_id':request_id, 'scope':scope, 'kind':kind, 'action':action,
            'expected_revision':current['local'][kind]['revision'], 'scope_proof':current['proof'],
            **({'selection':{'id':entry['id'], 'version':entry['version'], 'proof':entry['proof']}}
               if entry else {})}


def test_preview_and_empty_scope_do_not_create_store(service, context):
    assert service.catalog('style')
    assert service.catalog('technique')
    inspected = service.inspect({'level':'project'})
    assert inspected['effective']['style']['state'] == 'unset'
    assert not context.state_dir.exists()


async def test_image_templates_are_trusted_fixed_and_reject_changed_preview(service, context, monkeypatch):
    from novelvideo.freezone import image_style_templates as route_helpers
    original = dict(route_helpers.FREEZONE_IMAGE_STYLE_TEMPLATES[0])
    key = 'image-template:' + original['id']
    entry = next((e for e in service.catalog('style') if e['id'] == key), None)
    assert entry is not None
    assert entry['configuration']['projections']['image'] == original['style_prompt']
    assert entry['metadata']['author'] == original['author']
    assert entry['metadata']['category'] == original['category']
    assert not context.state_dir.exists()
    body = request(service, {'level':'project'}, entry)
    result = await service.apply(body)
    frozen = service.snapshot(result['snapshot_ref']['snapshot_id'])
    monkeypatch.setattr(route_helpers, 'FREEZONE_IMAGE_STYLE_TEMPLATES', [{**original, 'style_prompt':'changed'}])
    with pytest.raises(ValueError, match='目录'):
        await service.apply(request(service, {'level':'project'}, entry, request_id='stale'))
    assert service.inspect({'level':'project'})['local']['style']['revision'] == 1
    monkeypatch.setattr(route_helpers, 'FREEZONE_IMAGE_STYLE_TEMPLATES', [])
    assert service.snapshot(result['snapshot_ref']['snapshot_id']) == frozen
    assert await service.apply(body) == result


@pytest.mark.parametrize('field', ['style_prompt', 'category', 'author'])
async def test_template_evidence_covers_all_trusted_content_before_any_write(service, context, monkeypatch, field):
    from novelvideo.freezone import image_style_templates
    from novelvideo.director_plan.models import StyleSnapshot
    item = dict(image_style_templates.FREEZONE_IMAGE_STYLE_TEMPLATES[0])
    entry = next(e for e in service.catalog('style') if e['id'] == 'image-template:' + item['id'])
    StyleSnapshot.model_validate(entry['configuration'])
    body = request(service, {'level':'project'}, entry)
    monkeypatch.setattr(image_style_templates, 'FREEZONE_IMAGE_STYLE_TEMPLATES', [{**item, field:'new evidence'}])
    changed = next(e for e in service.catalog('style') if e['id'] == entry['id'])
    assert changed['proof'] != entry['proof']
    assert changed['version'] != entry['version']
    with pytest.raises(ValueError, match='目录'):
        await service.apply(body)
    # The existing project writer lock is allowed; no business data is created.
    assert not service.db.exists()
    assert all(p.name == '.production_workflow.lock' and p.stat().st_size == 0
               for p in context.state_dir.rglob('*') if p.is_file())


def test_template_namespace_cannot_be_shadowed_by_project_custom_style(service, context):
    from novelvideo.freezone.image_style_templates import FREEZONE_IMAGE_STYLE_TEMPLATES
    item = FREEZONE_IMAGE_STYLE_TEMPLATES[0]
    context.state_dir.mkdir()
    (context.state_dir/'project_config.json').write_text(json.dumps({'custom_styles':{
        'image-template:' + item['id']:{'label':'forged', 'style_tag':'injected'}}}))
    entries = [e for e in service.catalog('style') if e['id'] == 'image-template:' + item['id']]
    assert len(entries) == 1
    assert entries[0]['configuration']['projections']['image'] == item['style_prompt']


async def test_four_levels_unset_disable_and_fixed_history(service, canvas, monkeypatch):
    scopes = [{'level':'project'}, {'level':'canvas','canvas_id':canvas},
              {'level':'group','canvas_id':canvas,'group_id':'group'},
              {'level':'node','canvas_id':canvas,'node_id':'node'}]
    styles = service.catalog('style')[:2]
    refs = []
    for index, scope in enumerate(scopes):
        body = request(service, scope, styles[index % 2], request_id=f'apply-{index}')
        result = await service.apply(body)
        refs.append(result['snapshot_ref'])
        effective = service.inspect(scopes[-1])['effective']['style']
        assert effective['origin_scope'] == scope
        assert effective['snapshot_ref'] == result['snapshot_ref']
        assert await service.apply(body) == result
    await service.apply(request(service, scopes[-1], action='unset', request_id='unset'))
    assert service.inspect(scopes[-1])['effective']['style']['origin_scope'] == scopes[-2]
    await service.apply(request(service, scopes[-2], action='disable', request_id='disable'))
    assert service.inspect(scopes[-1])['effective']['style']['state'] == 'disabled'
    fixed = service.snapshot(refs[0]['snapshot_id'])
    monkeypatch.setattr(service, 'catalog', lambda *_: [])
    assert service.snapshot(refs[0]['snapshot_id']) == fixed
    assert service.inspect(scopes[0])['effective']['style']['configuration'] == fixed['configuration']


async def test_stale_catalog_and_scope_cas_do_not_mutate(service, canvas):
    scope = {'level':'canvas','canvas_id':canvas}
    entry = service.catalog('style')[0]
    body = request(service, scope, entry)
    bad = {**body, 'selection':{**body['selection'], 'proof':'stale'}}
    with pytest.raises(ValueError, match='目录'):
        await service.apply(bad)
    assert service.inspect(scope)['local']['style']['revision'] == 0
    await service.apply(body)
    with pytest.raises(ValueError):
        await service.apply({**body, 'request_id':'other'})
    with pytest.raises(ValueError):
        await service.apply({**body, 'action':'disable', 'selection':None})
    assert service.inspect(scope)['local']['style']['revision'] == 1


@pytest.mark.parametrize('scope', [
    {'level':'canvas','canvas_id':'foreign'},
    {'level':'group','group_id':'node'},
    {'level':'node','node_id':'missing'},
])
def test_scope_requires_applied_workspace_and_actual_group(service, canvas, scope):
    scope = {'canvas_id':canvas, **scope}
    with pytest.raises(ValueError):
        service.inspect(scope)


async def test_technique_freezes_applicability_without_claiming_unknown_context(service):
    entry = next(e for e in service.catalog('technique') if e['available'])
    result = await service.apply(request(service, {'level':'project'}, entry, kind='technique'))
    effect = service.inspect({'level':'project'})['effective']['technique']
    assert effect['applicability']['status'] == 'not_evaluated'
    assert effect['applicability']['compatible'] is None
    fixed = service.snapshot(result['snapshot_ref']['snapshot_id'])
    assert fixed['metadata']['card']['applicability']
    assert fixed['metadata']['card']['title']
    effect = service.inspect({'level':'project'}, mode='invalid', duration_seconds=1)['effective']['technique']
    assert effect['applicability']['status'] == 'incompatible'
    assert effect['applicability']['reasons']


async def test_atomic_explicit_multiselect_and_stale_member(service, canvas):
    scopes = [{'level':'node','canvas_id':canvas,'node_id':node} for node in ('node','other')]
    entry = service.catalog('style')[0]
    targets = [{key:value for key,value in request(service, scope, entry).items()
                if key in {'scope','expected_revision','scope_proof'}} for scope in scopes]
    body = {'request_id':'batch', 'kind':'style', 'action':'apply', 'targets':targets,
            'selection':{'id':entry['id'],'version':entry['version'],'proof':entry['proof']}}
    bad = {**body,'targets':[targets[0], {**targets[1],'scope_proof':'stale'}]}
    with pytest.raises(ValueError):
        await service.apply(bad)
    assert all(service.inspect(scope)['local']['style']['revision'] == 0 for scope in scopes)
    result = await service.apply(body)
    assert len(result['bindings']) == 2
    assert [entry['scope'] for entry in result['effective']] == scopes
    assert all(service.inspect(scope)['effective']['style']['origin_scope'] == scope for scope in scopes)
    for action in ('disable','unset'):
        body = {'request_id':action,'kind':'style','action':action,'targets':[
            {key:value for key,value in request(service, scope, action=action).items()
             if key in {'scope','expected_revision','scope_proof'}} for scope in scopes]}
        await service.apply(body)
        assert all(service.inspect(scope)['effective']['style']['state'] ==
                   ('disabled' if action == 'disable' else 'unset') for scope in scopes)


@pytest.mark.parametrize('phase',['before_commit','committed'])
async def test_crash_receipt_is_atomic_and_replay_ignores_later_scope_and_catalog(service, canvas, monkeypatch, phase):
    from novelvideo.creation import library_references
    scope = {'level':'node','canvas_id':canvas,'node_id':'node'}
    body = request(service, scope, service.catalog('style')[0])
    def crash(at):
        if at == phase:
            raise RuntimeError('lost response')
    monkeypatch.setattr(library_references, '_checkpoint', crash)
    with pytest.raises(RuntimeError, match='lost response'):
        await service.apply(body)
    monkeypatch.setattr(library_references, '_checkpoint', lambda _:None)
    if phase == 'before_commit':
        assert service.receipt(body['request_id']) is None
        assert service.inspect(scope)['local']['style']['revision'] == 0
    result = await service.apply(body)
    path = canvas_path(service.state_dir, canvas)
    graph = json.loads(path.read_text())
    graph['nodes'] = []
    path.write_text(json.dumps(graph))
    monkeypatch.setattr(library_references.DirectionReferences, 'catalog', lambda *_: [])
    restarted = library_references.DirectionReferences(service.ctx)
    assert await restarted.apply(body) == result


async def test_concurrent_writers_have_one_winner(service):
    body = request(service, {'level':'project'}, service.catalog('style')[0])
    results = await asyncio.gather(service.apply(body), service.apply({**body,'request_id':'second'}), return_exceptions=True)
    assert sum(isinstance(result, ValueError) for result in results) == 1
    assert service.inspect({'level':'project'})['local']['style']['revision'] == 1


@pytest.mark.parametrize('change',['move','revision','cycle','missing','not_group'])
async def test_membership_evidence_is_rechecked(service, canvas, change):
    scope = {'level':'node','canvas_id':canvas,'node_id':'node'}
    body = request(service, scope, service.catalog('style')[0])
    path = canvas_path(service.state_dir, canvas)
    graph = json.loads(path.read_text())
    if change == 'move':
        graph['nodes'][1].pop('parentId')
    elif change == 'revision':
        graph['revision'] += 1
    elif change == 'cycle':
        graph['nodes'][0]['parentId'] = 'group'
    elif change == 'missing':
        graph['nodes'][1]['parentId'] = 'foreign-group'
    else:
        graph['nodes'][1]['parentId'] = 'other'
    path.write_text(json.dumps(graph))
    with pytest.raises(ValueError):
        await service.apply(body)
    assert service.receipt(body['request_id']) is None


async def test_project_identity_cannot_reuse_another_project_database(service, canvas):
    from novelvideo.creation.library_references import DirectionReferences
    impostor = DirectionReferences(SimpleNamespace(**{**vars(service.ctx),'project_id':'foreign'}))
    with pytest.raises(ValueError):
        impostor.inspect({'level':'project'})
    with pytest.raises(ValueError):
        impostor.inspect({'level':'canvas','canvas_id':canvas})


async def test_autosave_fields_are_not_binding_authority(service, canvas):
    from novelvideo.freezone.canvas_store import save_canvas
    scope = {'level':'node','canvas_id':canvas,'node_id':'node'}
    body = request(service, scope, service.catalog('style')[0])
    result = await service.apply(body)
    path = canvas_path(service.state_dir, canvas)
    graph = json.loads(path.read_text())
    graph['nodes'][1]['data'].update(styleRef={'snapshot_id':'forged'}, techniqueRef={'id':'fake'}, style='other')
    graph.setdefault('metadata', {})['directionBindings'] = []
    save_canvas(service.state_dir, canvas, base_revision=graph['revision'], build_payload=lambda _:graph)
    assert service.inspect(scope)['effective']['style']['snapshot_ref'] == result['snapshot_ref']
    assert service.inspect(scope)['effective']['technique']['state'] == 'unset'


async def test_catalog_replacement_cannot_change_historical_rules(service, monkeypatch):
    from novelvideo.freezone.video_director import techniques
    entry = next(e for e in service.catalog('technique') if e['available'])
    body = request(service, {'level':'project'}, entry, kind='technique')
    result = await service.apply(body)
    card = service.snapshot(result['snapshot_ref']['snapshot_id'])['metadata']['card']
    mode = card['applicability']['modes'][0]
    duration = card['applicability']['min_duration_seconds'] + .1
    before = service.inspect({'level':'project'}, mode=mode, duration_seconds=duration)
    monkeypatch.setattr(techniques, '_CARDS', ())
    after = service.inspect({'level':'project'}, mode=mode, duration_seconds=duration)
    assert before == after
    assert await service.apply(body) == result


async def test_real_api_read_permissions_and_editor_apply(context, monkeypatch):
    from novelvideo.api.routes import creation
    calls = []
    async def authorized(project,user,role):
        calls.append(role)
        return context
    monkeypatch.setattr(creation, 'context_for', authorized)
    catalog = (await creation.browse_direction_catalog('project','style',{}))['data']
    scope = {'level':'project'}
    inspected = (await creation.inspect_direction_bindings('project', level='project', user={}))['data']
    assert not context.state_dir.exists()
    entry = catalog[0]
    body = {'request_id':'api','kind':'style','action':'apply','scope':scope,
            'expected_revision':0,'scope_proof':inspected['proof'],
            'selection':{'id':entry['id'],'version':entry['version'],'proof':entry['proof']}}
    result = (await creation.apply_direction_bindings('project',body,{}))['data']
    fixed = (await creation.read_direction_snapshot('project',result['snapshot_ref']['snapshot_id'],{}))['data']
    assert fixed['configuration'] == entry['configuration']
    receipt = (await creation.read_direction_request('project','api',{}))['data']
    assert receipt == result
    assert calls == ['viewer','viewer','editor','viewer','viewer']


async def test_cancelled_worker_drains_before_project_unlock_and_receipt_recovers(service, monkeypatch):
    from novelvideo.creation import library_references
    from novelvideo.production_workflow.coordination import asset_project_lock
    entered, release = threading.Event(), threading.Event()
    def pause(phase):
        if phase == 'before_commit':
            entered.set()
            assert release.wait(5)
    monkeypatch.setattr(library_references, '_checkpoint', pause)
    body = request(service, {'level':'project'}, service.catalog('style')[0])
    task = asyncio.create_task(service.apply(body))
    try:
        for _ in range(200):
            if entered.is_set():
                break
            await asyncio.sleep(.01)
        assert entered.is_set()  # event loop stayed live during synchronous work
        task.cancel()
        acquired = asyncio.Event()
        async def competing():
            async with asset_project_lock(service.state_dir):
                acquired.set()
        competitor = asyncio.create_task(competing())
        await asyncio.sleep(.05)
        assert not acquired.is_set()
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await competitor
    assert acquired.is_set()
    result = await service.apply(body)
    assert result['status'] == 'applied'
    assert service.inspect({'level':'project'})['local']['style']['revision'] == 1


def db_dump(path):
    with sqlite3.connect(path) as conn:
        return list(conn.iterdump())


async def test_reads_and_rejected_write_preserve_existing_business_content(service, canvas):
    scope = {'level':'node','canvas_id':canvas,'node_id':'node'}
    entry = service.catalog('style')[0]
    result = await service.apply(request(service, scope, entry))
    before = db_dump(service.db)
    files = {str(p):p.read_bytes() for p in service.state_dir.rglob('*.json')}
    for _ in range(2):
        service.inspect(scope)
        service.catalog('style')
        service.snapshot(result['snapshot_ref']['snapshot_id'])
        service.receipt('apply')
    assert db_dump(service.db) == before
    assert {str(p):p.read_bytes() for p in service.state_dir.rglob('*.json')} == files
    stale = request(service, scope, entry, request_id='rejected')
    stale['selection']['proof'] = 'changed'
    with pytest.raises(ValueError):
        await service.apply(stale)
    assert db_dump(service.db) == before


async def test_nested_groups_resolve_nearest_explicit_without_spatial_inference(service, canvas):
    path = canvas_path(service.state_dir, canvas)
    graph = json.loads(path.read_text())
    graph['nodes'].append({'id':'outer','type':'groupNode','data':{}})
    graph['nodes'][0]['parentId'] = 'outer'
    # Merely overlapping a group is not membership.
    graph['nodes'][2]['position'] = {'x':0,'y':0}
    path.write_text(json.dumps(graph))
    entry = service.catalog('style')[0]
    outer = {'level':'group','canvas_id':canvas,'group_id':'outer'}
    inner = {'level':'group','canvas_id':canvas,'group_id':'group'}
    node = {'level':'node','canvas_id':canvas,'node_id':'node'}
    await service.apply(request(service, outer, entry))
    assert service.inspect(node)['effective']['style']['origin_scope'] == outer
    await service.apply(request(service, inner, entry, request_id='inner'))
    assert service.inspect(node)['effective']['style']['origin_scope'] == inner
    assert service.inspect({'level':'node','canvas_id':canvas,'node_id':'other'})['effective']['style']['state'] == 'unset'


@pytest.mark.parametrize('legacy,denied', [(True,False),(False,True)])
async def test_real_context_guard_precedes_any_read_or_write(context, monkeypatch, legacy, denied):
    from fastapi import HTTPException
    from novelvideo.api.routes import creation
    context.state_dir.mkdir()
    (context.state_dir/'project_config.json').write_text(json.dumps(
        {} if legacy else {'creation_mode':'agent_canvas_v1'}))
    context.is_home_node = True
    async def resolve(**kwargs):
        if denied:
            raise HTTPException(403, 'denied')
        return context
    monkeypatch.setattr(creation, 'resolve_project_context', resolve)
    before = {str(p):p.read_bytes() for p in context.state_dir.rglob('*') if p.is_file()}
    for operation in [creation.browse_direction_catalog('project','style',{}),
                      creation.inspect_direction_bindings('project',user={}),
                      creation.apply_direction_bindings('project',{}, {})]:
        with pytest.raises(HTTPException) as error:
            await operation
        assert error.value.status_code == (403 if denied else 409)
    assert {str(p):p.read_bytes() for p in context.state_dir.rglob('*') if p.is_file()} == before


async def test_style_custom_source_is_state_root_and_stale_preview_rejected(service, context):
    from novelvideo.services.style_service import StyleService
    example = StyleService.get_preset(StyleService.list_preset_styles()[0]['id']).model_dump(mode='json')
    example.update(id='project-only', name='Project style', label='Project style', style_instructions='ink')
    context.state_dir.mkdir()
    config = context.state_dir/'project_config.json'
    config.write_text(json.dumps({'custom_styles':{'project-only':example}}))
    entry = next(e for e in service.catalog('style') if e['id'] == 'project-only')
    body = request(service, {'level':'project'}, entry)
    example['style_instructions'] = 'watercolor'
    config.write_text(json.dumps({'custom_styles':{'project-only':example}}))
    with pytest.raises(ValueError, match='目录'):
        await service.apply(body)
    assert not service.db.exists()
    updated = next(e for e in service.catalog('style') if e['id'] == 'project-only')
    result = await service.apply(request(service, {'level':'project'}, updated))
    config.write_text('{}')
    assert service.snapshot(result['snapshot_ref']['snapshot_id'])['configuration'] == updated['configuration']


async def test_forged_prompt_and_wrong_actor_replay_rejected(service):
    from novelvideo.creation.library_references import DirectionReferences
    body = request(service, {'level':'project'}, service.catalog('style')[0])
    with pytest.raises(ValueError):
        await service.apply({**body,'prompt':'untrusted'})
    result = await service.apply(body)
    other = DirectionReferences(SimpleNamespace(**{**vars(service.ctx),'requester_user_id':'other'}))
    with pytest.raises(ValueError):
        await other.apply(body)
    assert await service.apply(body) == result


async def test_canvas_lock_contention_is_typed_and_does_not_change_binding(service, canvas, monkeypatch):
    from contextlib import contextmanager
    from novelvideo.creation import library_references
    from novelvideo.creation.operations import OperationError
    from novelvideo.freezone.canvas_lock import CanvasLockBusy
    scope = {'level':'canvas','canvas_id':canvas}
    body = request(service, scope, service.catalog('style')[0])
    @contextmanager
    def busy(*args):
        raise CanvasLockBusy(canvas)
        yield
    monkeypatch.setattr(library_references, 'canvas_write_lock', busy)
    with pytest.raises(OperationError) as error:
        await service.apply(body)
    assert error.value.code == 'canvas_busy'
    assert service.inspect(scope)['local']['style']['revision'] == 0
