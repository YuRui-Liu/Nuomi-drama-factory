"""Explicit shared saves use a durable request and immutable semantic receipt."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from novelvideo.api.routes import creation as creation_routes

from novelvideo.creation.asset_adapter import AssetAdapter
from novelvideo.creation.asset_reference import AssetReferences
from novelvideo.creation.canvas_workspaces import CanvasCreate, CanvasWorkspace
from novelvideo.creation.store import ObjectStore
from novelvideo.creation.projection import ProjectionService
from novelvideo.freezone.canvas_store import canvas_path
from novelvideo.models import NovelCharacter, NovelScene
from novelvideo.sqlite_store import SQLiteStore


@pytest.fixture
async def project(tmp_path):
    source = SQLiteStore('test', output_dir=str(tmp_path/'output'), state_dir=str(tmp_path/'state'))
    await source.initialize()
    await source.add_character(NovelCharacter(name='Lin'))
    await source.add_scene(NovelScene(name='Hall'))
    ctx = SimpleNamespace(project_id='project', state_dir=Path(source.state_dir),
                          output_dir=Path(source.project_dir), requester_user_id='actor')
    objects = ObjectStore(ctx.state_dir)
    result = CanvasWorkspace(objects, project_id=ctx.project_id).create('actor',
        CanvasCreate(request_id='canvas', kind='free', title='Free'))
    canvas_id = result.object_refs[0]['canvas_id']
    assert ProjectionService(objects).apply(result.operation_id).status == 'applied'
    path = ctx.output_dir/'freezone/_outputs/input.png'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'first image')
    canvas = json.loads(canvas_path(ctx.state_dir, canvas_id).read_text())
    canvas.update(canvas_id=canvas_id, nodes=[{'id':'node', 'type':'uploadNode',
        'data':{'imageUrl':f'/api/v1/projects/{ctx.project_id}/media/freezone/_outputs/input.png'}}])
    file = canvas_path(ctx.state_dir, canvas_id)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(canvas))
    yield source, ctx, canvas_id
    await source.close()


async def request_for(project, mode='existing_version', kind='character'):
    from novelvideo.creation.asset_publication import SharedAssetPublication
    source, ctx, canvas_id = project
    service = SharedAssetPublication(ctx)
    body = {'request_id':'save', 'mode':mode, 'kind':kind,
            'role':'portrait' if kind == 'character' else 'scene_master',
            'source':{'canvas_id':canvas_id, 'node_id':'node',
                      'proof':service.inspect_source(canvas_id, 'node')['proof']}}
    if mode == 'new_base':
        body['name'] = 'New'
    else:
        ref = await AssetAdapter(source, ObjectStore(ctx.state_dir)).register_base(kind, 'Lin' if kind == 'character' else 'Hall')
        entry = next(e for e in AssetReferences(ctx.state_dir, ctx.output_dir, ctx.project_id).catalog() if e['kind'] == kind and not e['parent_source_name'])
        body.update(selection=entry['selection'], proof=entry['proof'], expected_ref=ref.model_dump())
        if mode == 'new_variant':
            body.update(name='Rain', role='identity' if kind == 'character' else 'scene_master')
    return service, body


@pytest.mark.parametrize('kind', ['character','scene'])
@pytest.mark.parametrize('mode', ['new_base','new_variant','existing_version'])
async def test_three_modes_are_explicit_and_replay_completed_receipt(project, mode, kind):
    service, body = await request_for(project, mode, kind)
    result = await service.publish(body)
    assert result['status'] == 'applied'
    ref = result['asset_ref']
    fixed = AssetReferences(service.ctx.state_dir, service.ctx.output_dir, service.ctx.project_id).resolve(ref)
    assert fixed['object']['payload']['_media']
    (service.ctx.output_dir/'freezone/_outputs/input.png').write_bytes(b'later image')
    assert await service.publish(body) == result
    with pytest.raises(ValueError):
        await service.publish({**body, 'name':'different'})


@pytest.mark.parametrize('phase', ['prepared','source_created','registered','semantic_committed','applied'])
@pytest.mark.parametrize('mode', ['new_base','new_variant'])
async def test_restart_recovers_durable_creation_and_final_receipt(project, monkeypatch, phase, mode):
    from novelvideo.creation import asset_publication
    service, body = await request_for(project, mode)
    def crash(at):
        if at == phase:
            raise RuntimeError('restart')
    monkeypatch.setattr(asset_publication, '_checkpoint', crash)
    with pytest.raises(RuntimeError, match='restart'):
        await service.publish(body)
    monkeypatch.setattr(asset_publication, '_checkpoint', lambda _: None)
    service = asset_publication.SharedAssetPublication(service.ctx)
    result = await service.publish(body)
    assert result['status'] == 'applied'
    assert await service.publish(body) == result
    records = service.references.catalog()
    assert len([r for r in records if r['title'] in {'New','Rain'}]) == 1


@pytest.mark.parametrize('phase', ['published','captured','committed'])
async def test_media_recovery_never_guesses_missing_after(project, monkeypatch, phase):
    from novelvideo.creation import asset_media_publication
    from novelvideo.creation.asset_sync import SourceSyncPending
    service, body = await request_for(project)
    def crash(at):
        if at == phase:
            raise RuntimeError('interrupted media')
    monkeypatch.setattr(asset_media_publication, '_checkpoint', crash)
    with pytest.raises(RuntimeError, match='interrupted media'):
        await service.publish(body)
    monkeypatch.setattr(asset_media_publication, '_checkpoint', lambda _: None)
    if phase == 'published':
        with pytest.raises(SourceSyncPending):
            await service.publish(body)
        assert service.status('save')['status'] == 'reconciliation_required'
    else:
        result = await service.publish(body)
        assert result['asset_ref']['revision'] == 2
        assert await service.publish(body) == result


@pytest.mark.parametrize('phase', ['prepared','source_created'])
async def test_resumed_new_variant_rejects_changed_original_base(project, monkeypatch, phase):
    from novelvideo.creation import asset_publication
    service, body = await request_for(project, 'new_variant')
    def crash(at):
        if at == phase:
            raise RuntimeError('restart')
    monkeypatch.setattr(asset_publication, '_checkpoint', crash)
    with pytest.raises(RuntimeError):
        await service.publish(body)
    monkeypatch.setattr(asset_publication, '_checkpoint', lambda _: None)
    await project[0].update_character('Lin', description='human edit')
    with pytest.raises(ValueError, match='变化'):
        await service.publish(body)
    assert not (service.root/'assets/characters/Lin/identities/Rain.png').exists()


async def test_source_change_after_inspection_is_not_recaptured_as_authorized(project, monkeypatch):
    from novelvideo.creation import asset_publication
    service, body = await request_for(project)
    def change(at):
        if at == 'registered':
            (service.root/'freezone/_outputs/input.png').write_bytes(b'changed after proof')
    monkeypatch.setattr(asset_publication, '_checkpoint', change)
    with pytest.raises(ValueError, match='变化'):
        await service.publish(body)
    assert not (service.root/'assets/characters/Lin/portrait.png').exists()
    assert ObjectStore(service.state_dir).read(body['expected_ref']['base_asset_id'])['revision'] == 1


async def test_same_name_source_is_never_upserted_or_claimed(project):
    service, body = await request_for(project, 'new_base')
    body['name'] = 'Lin'
    with pytest.raises(ValueError, match='名称已存在'):
        await service.publish(body)
    assert (await project[0].get_character_from_graph('Lin')).description == ''
    assert not (service.root/'assets/characters/Lin/portrait.png').exists()


@pytest.mark.parametrize('kind',['character','scene'])
@pytest.mark.parametrize('collision',['name','alias','retired'])
async def test_new_base_name_conflict_repeatedly_refuses_before_any_business_write(project,kind,collision):
    import sqlite3
    from contextlib import closing
    source,ctx,_=project
    service,body=await request_for(project,'new_base',kind)
    name='Lin' if kind=='character' else 'Hall'
    body['name']=name
    if collision=='alias':
        if kind=='character':await source.add_character(NovelCharacter(name='Other',aliases=['Alias']))
        else:await source.add_scene(NovelScene(name='Other',aliases=['Alias']))
        body['name']='Alias'
    elif collision=='retired':
        if kind=='character':await source.rename_character(name,'Renamed')
        else:await source.rename_scene(name,'Renamed')
    def databases():
        result=[]
        for path in (Path(source.db_path),service.db_path):
            with closing(sqlite3.connect(path)) as conn:result.append(list(conn.iterdump()))
        return result
    before=databases();media={str(p):p.read_bytes() for p in ctx.output_dir.rglob('*') if p.is_file()}
    for _ in range(2):
        with pytest.raises(ValueError):await service.publish(body)
        assert service._row(body['request_id']) is None
        assert databases()==before
        assert {str(p):p.read_bytes() for p in ctx.output_dir.rglob('*') if p.is_file()}==media


@pytest.mark.parametrize('retired',[False,True])
async def test_new_scene_variant_locator_conflict_has_no_prepared_request(project,retired):
    import sqlite3
    from contextlib import closing
    from novelvideo.utils.derived_scenes import compose_derived_scene_name
    source,ctx,_=project
    service,body=await request_for(project,'new_variant','scene')
    locator=compose_derived_scene_name('Hall','Rain')
    await source.add_scene(NovelScene(name=locator))
    if retired:await source.rename_scene(locator,'RenamedVariant')
    def databases():
        result=[]
        for path in (Path(source.db_path),service.db_path):
            with closing(sqlite3.connect(path)) as conn:result.append(list(conn.iterdump()))
        return result
    before=databases();media={str(p):p.read_bytes() for p in ctx.output_dir.rglob('*') if p.is_file()}
    for _ in range(2):
        with pytest.raises(ValueError):await service.publish(body)
        assert service._row(body['request_id']) is None
        assert databases()==before
        assert {str(p):p.read_bytes() for p in ctx.output_dir.rglob('*') if p.is_file()}==media


async def test_character_variant_display_name_is_not_a_unique_locator(project):
    from novelvideo.models import CharacterIdentity
    source,_,_=project
    await source.add_character_identity('Lin',CharacterIdentity(identity_id='existing-identity',character_name='Lin',identity_name='Rain'))
    service,body=await request_for(project,'new_variant','character')
    result=await service.publish(body)
    assert result['status']=='applied'
    identities=(await source.get_character_from_graph('Lin')).identities
    assert len([i for i in identities if i.identity_name=='Rain'])==2
    assert len({i.identity_id for i in identities})==2


@pytest.mark.parametrize('tamper', ['url_project','canvas_project','canvas_registry','source_bytes','selection'])
async def test_selection_and_source_proofs_are_not_silently_refreshed(project, tamper):
    service, body = await request_for(project)
    path = canvas_path(service.state_dir, body['source']['canvas_id'])
    canvas = json.loads(path.read_text())
    if tamper == 'url_project':
        canvas['nodes'][0]['data']['imageUrl'] = '/api/v1/projects/other/media/freezone/_outputs/input.png'
        path.write_text(json.dumps(canvas))
    elif tamper == 'canvas_project':
        canvas['project_id'] = 'other'
        path.write_text(json.dumps(canvas))
    elif tamper == 'canvas_registry':
        body['source']['canvas_id'] = 'unknown'
    elif tamper == 'selection':
        await project[0].update_character('Lin', description='new selected version')
    else:
        (service.root/'freezone/_outputs/input.png').write_bytes(b'new bytes')
    with pytest.raises(ValueError):
        await service.publish(body)
    assert not (service.root/'assets/characters/Lin/portrait.png').exists()


async def test_source_inspection_is_read_only_and_does_not_register(project):
    from novelvideo.creation.asset_publication import SharedAssetPublication
    source, ctx, canvas_id = project
    service = SharedAssetPublication(ctx)
    before = Path(source.db_path).read_bytes()
    evidence = service.inspect_source(canvas_id, 'node')
    assert evidence['files']
    assert not (ctx.output_dir/'assets/.creation-media').exists()
    assert not service.references.catalog()[0]['asset_ref']
    assert Path(source.db_path).read_bytes() == before


async def test_source_uses_projected_state_canvas_not_stale_output_copy(project):
    service, body = await request_for(project)
    stale = canvas_path(service.root, body['source']['canvas_id'])
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text(json.dumps({'project_id':'project','nodes':[]}))
    assert service.inspect_source(body['source']['canvas_id'],'node')['proof'] == body['source']['proof']
    assert (await service.publish(body))['status'] == 'applied'


@pytest.mark.parametrize('mode', ['existing_version','new_variant'])
@pytest.mark.parametrize('mismatch', ['registered_target','unregistered_target','stale_proof','stale_ref'])
async def test_target_proof_and_current_ref_must_agree_before_any_write(project,mode,mismatch):
    import sqlite3
    from contextlib import closing
    source,ctx,_ = project
    service,body = await request_for(project,mode)
    if mismatch in {'registered_target','unregistered_target'}:
        await source.add_character(NovelCharacter(name='Other'))
        if mismatch == 'registered_target':
            await AssetAdapter(source,ObjectStore(ctx.state_dir)).register_base('character','Other')
        entry = next(e for e in service.references.catalog() if e['source_name'] == 'Other')
        body.update(selection=entry['selection'],proof=entry['proof'])
    else:
        await source.update_character('Lin',description='new current version')
        entry = next(e for e in service.references.catalog() if e['source_name'] == 'Lin')
        if mismatch == 'stale_ref':
            body['proof'] = entry['proof']
        else:
            body['expected_ref'] = entry['asset_ref']
    def databases():
        result=[]
        for path in (Path(source.db_path),service.db_path):
            with closing(sqlite3.connect(path)) as conn:
                result.append(list(conn.iterdump()))
        return result
    before = databases()
    media_before = {str(p):p.read_bytes() for p in service.root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):
        await service.publish(body)
    assert databases() == before
    assert service._row(body['request_id']) is None
    assert {str(p):p.read_bytes() for p in service.root.rglob('*') if p.is_file()} == media_before


@pytest.mark.parametrize('bad', ['variant_portrait','missing_media','duplicate_source','invalid_bundle'])
async def test_invalid_request_has_no_source_registration_or_journal_effects(project,bad):
    import sqlite3
    from contextlib import closing
    service, body = await request_for(project, 'new_variant' if bad == 'variant_portrait' else 'new_base',
                                      'character' if bad == 'variant_portrait' else 'scene')
    if bad == 'variant_portrait':
        body['role'] = 'portrait'
    elif bad == 'missing_media':
        body['role'] = 'scene_360'
    else:
        path = canvas_path(service.state_dir,body['source']['canvas_id'])
        canvas = json.loads(path.read_text())
        url = canvas['nodes'][0]['data']['imageUrl']
        canvas['nodes'][0]['data'].update(activeSourceId='one', sources=[
            {'id':'one','source_type':'pano360','pano_url':url},
            {'id':'one' if bad == 'duplicate_source' else 'two','source_type':'wrong' if bad == 'invalid_bundle' else 'pano360','pano_url':url}])
        path.write_text(json.dumps(canvas))
        body.update(scene_bundle=True,role='scene_360')
        body['source']['proof'] = service.inspect_source(body['source']['canvas_id'],'node')['proof']
    def business_rows():
        dumps = []
        for database in (Path(project[0].db_path), service.db_path):
            with closing(sqlite3.connect(database)) as conn:
                dumps.append(list(conn.iterdump()))
        return dumps
    rows_before = business_rows()
    before = {str(p):p.read_bytes() for directory in (service.root,service.state_dir) for p in directory.rglob('*')
              if p.is_file() and not p.name.endswith(('-wal','-shm'))}
    with pytest.raises(ValueError):
        await service.publish(body)
    assert service._row(body['request_id']) is None
    assert business_rows() == rows_before
    after = {str(p):p.read_bytes() for directory in (service.root,service.state_dir) for p in directory.rglob('*')
             if p.is_file() and not p.name.endswith(('-wal','-shm'))}
    assert after == before


@pytest.mark.parametrize('role,media_role', [('scene_360','panorama_360'),('scene_director_pano_360','pano')])
async def test_panorama_bundle_keeps_real_primary_slot_and_later_current(project,role,media_role):
    from novelvideo.creation.asset_media_publication import scene_media_publication
    from novelvideo.utils.path_resolver import canonical_scene_360_path
    from novelvideo.director_world import stage_manifest
    service, body = await bundle_request(project)
    path = canvas_path(service.state_dir,body['source']['canvas_id'])
    canvas = json.loads(path.read_text())
    data = canvas['nodes'][0]['data']
    for item in data['sources']:
        item['source_type'] = 'pano360'
        item.pop('ply_url')
    path.write_text(json.dumps(canvas))
    body.update(role=role)
    body['source']['proof'] = service.inspect_source(body['source']['canvas_id'],'node')['proof']
    result = await service.publish(body)
    assert result['media_selection']['role'] == media_role
    old = service.references.materialize(result['asset_ref'],result['media_selection'],'threeDWorldNode')['data']
    scene = await project[0].get_scene('Hall')
    async with scene_media_publication(service.root,service.state_dir,scene):
        target = canonical_scene_360_path(service.root,'Hall') if role == 'scene_360' else stage_manifest.stage_dir(service.root,'Hall')/'pano_360.png'
        target.write_bytes(b'new panorama')
        if role != 'scene_360':
            stage_manifest.update_manifest(service.root,'Hall',pano_path=target.name)
    ref = {**result['asset_ref'],'revision':result['asset_ref']['revision']+1}
    new = service.references.materialize(ref,result['media_selection'],'threeDWorldNode')['data']
    assert service._url_path(new['panoUrl']).read_bytes() == b'new panorama'
    assert service._url_path(new['sources'][0]['pano_url']).read_bytes() == b'first pano'
    assert service._url_path(old['panoUrl']).read_bytes() == b'second pano'
    assert service.references.materialize(result['asset_ref'],result['media_selection'],'threeDWorldNode')['data'] == old


async def bundle_request(project):
    from novelvideo.director_world import stage_manifest
    source, ctx, canvas_id = project
    directory = stage_manifest.stage_dir(ctx.output_dir, 'Hall')
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'master_sharp.sog').write_bytes(b'old master')
    stage_manifest.update_manifest(ctx.output_dir, 'Hall', master_ply_path='master_sharp.sog')
    service, body = await request_for(project, kind='scene')
    body.update(scene_bundle=True, role='scene_3gs_custom_scene')
    urls = {}
    for name, content in [('one.sog',b'first model'),('two.sog',b'second model'),('one.png',b'first pano'),
                          ('two.png',b'second pano'),('collision.glb',b'collision'),('voxel.json',b'{"voxels":[] }')]:
        (ctx.output_dir/'freezone/_outputs'/name).write_bytes(content)
        urls[name] = f'/static/projects/{ctx.project_id}/freezone/_outputs/{name}'
    path = canvas_path(ctx.state_dir, canvas_id)
    canvas = json.loads(path.read_text())
    canvas['nodes'][0] = {'id':'node', 'type':'threeDWorldNode', 'data':{
        'plyUrl':urls['two.sog'], 'panoUrl':urls['two.png'], 'activeSourceId':'second',
        'sources':[
            {'id':'first', 'source_type':'sog','source_kind':'custom','ply_url':urls['one.sog'],
             'pano_url':urls['one.png'],'collision_glb_url':urls['collision.glb']},
            {'id':'second','source_type':'sog','source_kind':'custom','ply_url':urls['two.sog'],
             'pano_url':urls['two.png'],'voxel_json_url':urls['voxel.json'],'transform':{'yawDeg':17}}],
        'scenesBySourceId':{'first':{'camera':{'x':1}}, 'second':{'camera':{'x':2}}},
        'scene':{'camera':{'x':3}},
    }}
    path.write_text(json.dumps(canvas))
    body['source']['proof'] = service.inspect_source(canvas_id,'node')['proof']
    return service, body


async def test_complete_multi_source_bundle_has_fixed_bytes_layouts_and_prune(project):
    from novelvideo.director_world import stage_manifest
    service, body = await bundle_request(project)
    before = service.references.resolve(body['expected_ref'])
    result = await service.publish(body)
    assert result['asset_ref']['revision'] == body['expected_ref']['revision'] + 1
    assert service.references.resolve(body['expected_ref']) == before
    manifest = stage_manifest.load_manifest(service.root,'Hall')
    assert len(manifest['bundle_sources']) == 2
    assert manifest['scenes_by_source_id']['first']['camera']['x'] == 1
    assert manifest['scenes_by_source_id']['second']['camera']['x'] == 3
    assert stage_manifest.resolve_ply_path(service.root,'Hall',ply_kind='master') is None
    assert (stage_manifest.stage_dir(service.root,'Hall')/'master_sharp.sog').read_bytes() == b'old master'
    media = next(m for m in result['media'] if m['role'] == 'custom_scene')
    fixed = service.references.materialize(result['asset_ref'], media,'threeDWorldNode')['data']
    assert fixed['activeSourceId'] == 'second'
    assert [s['id'] for s in fixed['sources']] == ['first','second']
    assert fixed['scenesBySourceId']['first']['camera']['x'] == 1
    assert fixed['sources'][1]['transform'] == {'yawDeg':17}
    assert service._url_path(fixed['sources'][0]['ply_url']).read_bytes() == b'first model'
    assert service._url_path(fixed['sources'][1]['pano_url']).read_bytes() == b'second pano'
    assert service._url_path(fixed['sources'][0]['collision_glb_url']).read_bytes() == b'collision'
    assert service._url_path(fixed['sources'][1]['voxel_json_url']).read_bytes() == b'{"voxels":[] }'
    for name in ('one.sog','two.sog','one.png','two.png'):
        (service.root/'freezone/_outputs'/name).write_bytes(b'later overwritten')
    assert service.references.materialize(result['asset_ref'],media,'threeDWorldNode')['data'] == fixed
    from novelvideo.creation.asset_reference import protect_bindings
    node = {'id':'bound','type':'threeDWorldNode','data':fixed}
    protect_bindings({'nodes':[node]},{'nodes':[node]})
    stage_manifest.update_manifest(service.root,'Hall', master_ply_path='master_sharp.sog')
    assert stage_manifest.resolve_ply_path(service.root,'Hall',ply_kind='master').read_bytes() == b'old master'


@pytest.mark.parametrize('switch_view', [False, True])
async def test_later_primary_writer_updates_current_bundle_without_changing_history(project, switch_view):
    from novelvideo.director_world import stage_manifest
    from novelvideo.creation.asset_media_publication import scene_media_publication
    service, body = await bundle_request(project)
    result = await service.publish(body)
    old = service.references.materialize(result['asset_ref'],result['media_selection'],'threeDWorldNode')['data']
    scene = await project[0].get_scene('Hall')
    async with scene_media_publication(service.root,service.state_dir,scene):
        primary = stage_manifest.resolve_ply_path(service.root,'Hall',ply_kind='custom')
        primary.write_bytes(b'new primary from another writer')
        stage_manifest.update_manifest(service.root,'Hall',custom_scene_path=primary.name,source='custom_scene')
        if switch_view:
            stage_manifest.save_scene_director_world(service.root,'Hall',active_source_id='first',snapshot={'camera':{'x':99}})
    ref = {**result['asset_ref'], 'revision':result['asset_ref']['revision'] + 1}
    new = service.references.materialize(ref,result['media_selection'],'threeDWorldNode')['data']
    assert service._url_path(new['plyUrl']).read_bytes() == (b'first model' if switch_view else b'new primary from another writer')
    assert service._url_path(new['sources'][1]['ply_url']).read_bytes() == b'new primary from another writer'
    assert service._url_path(new['sources'][0]['ply_url']).read_bytes() == b'first model'
    assert service._url_path(old['plyUrl']).read_bytes() == b'second model'
    assert service.references.materialize(result['asset_ref'],result['media_selection'],'threeDWorldNode')['data'] == old


@pytest.mark.parametrize('failure', ['auxiliary_change','duplicate_id','missing_active','cross_project'])
async def test_bundle_validation_never_writes_a_partial_scene(project, monkeypatch, failure):
    from novelvideo.creation import asset_publication
    from novelvideo.director_world import stage_manifest
    service, body = await bundle_request(project)
    before = stage_manifest.manifest_path(service.root,'Hall').read_bytes()
    if failure == 'auxiliary_change':
        def change(at):
            if at == 'registered':
                (service.root/'freezone/_outputs/one.png').write_bytes(b'changed after inspection')
        monkeypatch.setattr(asset_publication,'_checkpoint',change)
    else:
        path = canvas_path(service.state_dir,body['source']['canvas_id'])
        canvas = json.loads(path.read_text()); data = canvas['nodes'][0]['data']
        if failure == 'duplicate_id':data['sources'][1]['id'] = 'first'
        elif failure == 'missing_active':data['activeSourceId'] = 'missing'
        else:data['sources'][0]['collision_glb_url'] = '/static/projects/other/freezone/_outputs/collision.glb'
        path.write_text(json.dumps(canvas))
        if failure != 'cross_project':
            body['source']['proof'] = service.inspect_source(body['source']['canvas_id'],'node')['proof']
    with pytest.raises(ValueError):
        await service.publish(body)
    assert stage_manifest.manifest_path(service.root,'Hall').read_bytes() == before
    assert ObjectStore(service.state_dir).read(body['expected_ref']['base_asset_id'])['revision'] == body['expected_ref']['revision']


@pytest.mark.parametrize('bad', [{'sources':{}}, {'sources':[5]}, {'imageUrl':{}}, {'panoUrl':[]}, {'sources':[{'url':[]}]}])
async def test_inspect_rejects_malformed_media_with_a_typed_error(project,bad):
    from novelvideo.creation.asset_reference import AssetReferenceError
    from novelvideo.creation.asset_publication import SharedAssetPublication
    _,ctx,canvas_id = project
    path = canvas_path(ctx.state_dir,canvas_id); canvas=json.loads(path.read_text())
    canvas['nodes'][0]['data'].update(bad); path.write_text(json.dumps(canvas))
    with pytest.raises(AssetReferenceError) as error:
        SharedAssetPublication(ctx).inspect_source(canvas_id,'node')
    assert error.value.code == 'asset_source_invalid'


async def test_completed_receipt_precedes_current_target_checks(project):
    service, body = await request_for(project)
    result = await service.publish(body)
    await project[0].update_character('Lin', description='later version')
    assert ObjectStore(service.state_dir).read(result['asset_ref']['base_asset_id'])['revision'] > result['asset_ref']['revision']
    assert await service.publish(body) == result


async def test_source_owner_marker_does_not_claim_modified_interrupted_creation(project,monkeypatch):
    from novelvideo.creation import asset_publication
    service, body = await request_for(project,'new_base')
    def crash(at):
        if at == 'source_created':raise RuntimeError('restart')
    monkeypatch.setattr(asset_publication,'_checkpoint',crash)
    with pytest.raises(RuntimeError):await service.publish(body)
    monkeypatch.setattr(asset_publication,'_checkpoint',lambda _:None)
    await project[0].update_character('New',description='another editor')
    with pytest.raises(ValueError,match='已被修改'):
        await service.publish(body)
    assert not (service.root/'assets/characters/New/portrait.png').exists()


@pytest.mark.parametrize('kind',['character','scene'])
@pytest.mark.parametrize('external_change',[False,True])
async def test_variant_sql_created_before_registration_has_exact_ownership_proof(project,monkeypatch,kind,external_change):
    service, body = await request_for(project,'new_variant',kind)
    original = AssetAdapter._register
    async def interrupted(self,kind,source_name,parent_id=''):
        if parent_id:raise RuntimeError('before variant registration')
        return await original(self,kind,source_name,parent_id)
    monkeypatch.setattr(AssetAdapter,'_register',interrupted)
    with pytest.raises(RuntimeError,match='before variant registration'):
        await service.publish(body)
    monkeypatch.setattr(AssetAdapter,'_register',original)
    if external_change:
        if kind == 'character':
            from novelvideo.models import CharacterIdentity
            await project[0].add_character_identity('Lin',CharacterIdentity(character_name='Lin',identity_id='other',identity_name='Other'))
        else:
            child = next(e for e in service.references.catalog() if e['parent_source_name'] == 'Hall')
            await project[0].update_scene(child['source_name'],description='external child edit')
        with pytest.raises(ValueError):await service.publish(body)
    else:
        assert (await service.publish(body))['status'] == 'applied'


async def test_cancelled_copy_keeps_original_task_lock_until_worker_finishes(project,monkeypatch):
    import asyncio
    import threading
    from novelvideo.creation import asset_canvas_publication
    from novelvideo.production_workflow.coordination import asset_project_lock
    from novelvideo.creation.asset_sync import SourceSyncPending
    service,body = await request_for(project)
    started,release = threading.Event(),threading.Event()
    original = asset_canvas_publication.shutil.copy2
    def copy(source,target,*args,**kwargs):
        if str(target).endswith('Lin/portrait.png'):
            started.set()
            assert release.wait(5)
        return original(source,target,*args,**kwargs)
    monkeypatch.setattr(asset_canvas_publication.shutil,'copy2',copy)
    running=asyncio.create_task(service.publish(body))
    assert await asyncio.to_thread(started.wait,5)
    running.cancel()
    acquired=asyncio.Event()
    async def competitor():
        async with asset_project_lock(service.state_dir):acquired.set()
    waiting=asyncio.create_task(competitor())
    await asyncio.sleep(.05)
    assert not acquired.is_set() and not running.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):await running
    await waiting
    assert acquired.is_set()
    assert service.status('save')['status'] == 'reconciliation_required'
    with pytest.raises(SourceSyncPending):await service.publish(body)


async def test_real_routes_use_resolved_actor_and_editor_role(project,monkeypatch):
    _,ctx,canvas_id = project
    ctx.is_home_node = True
    (ctx.state_dir/'project_config.json').write_text('{"creation_mode":"agent_canvas_v1"}')
    roles=[]
    async def resolve(**kwargs):
        roles.append(kwargs['required_role']); return ctx
    monkeypatch.setattr(creation_routes,'resolve_project_context',resolve)
    service,body = await request_for(project)
    preview = await creation_routes.inspect_publication_source('project',canvas_id,'node',{'id':'forged'})
    assert preview['data']['proof'] == body['source']['proof']
    result = (await creation_routes.publish_shared_asset('project',body,{'id':'forged'}))['data']
    assert (await creation_routes.shared_asset_publication_status('project','save',{}))['data'] == result
    assert roles == ['viewer','editor','viewer']
    assert service.references.resolve(result['asset_ref'])['object']['actor_id'] == 'actor'


@pytest.mark.parametrize('reason',['legacy','permission'])
async def test_route_denial_precedes_any_source_or_journal_write(project,monkeypatch,reason):
    from fastapi import HTTPException
    _,ctx,canvas_id = project
    ctx.is_home_node = True
    (ctx.state_dir/'project_config.json').write_text('{}')
    service,body = await request_for(project)
    before = {str(p):p.read_bytes() for p in ctx.output_dir.rglob('*') if p.is_file()}
    async def resolve(**kwargs):
        assert kwargs['required_role'] == 'editor'
        if reason == 'permission':raise HTTPException(403,'editor required')
        return ctx
    monkeypatch.setattr(creation_routes,'resolve_project_context',resolve)
    with pytest.raises(HTTPException) as error:
        await creation_routes.publish_shared_asset('project',body,{})
    assert error.value.status_code == (409 if reason == 'legacy' else 403)
    assert service._row('save') is None
    assert {str(p):p.read_bytes() for p in ctx.output_dir.rglob('*') if p.is_file()} == before
