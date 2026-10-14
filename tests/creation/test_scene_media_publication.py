"""Scene current publications keep fixed media and manifest evidence."""
import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import UploadFile, HTTPException
from PIL import Image

from novelvideo.creation.asset_adapter import AssetAdapter
from novelvideo.creation.store import ObjectStore
from novelvideo.models import NovelScene
from novelvideo.sqlite_store import SQLiteStore


def png(color, size=(16,8)):
    stream = io.BytesIO(); Image.new('RGB',size,color).save(stream,format='PNG'); return stream.getvalue()


@pytest.fixture
async def project(tmp_path,monkeypatch):
    from novelvideo.api.routes import scenes
    source = SQLiteStore('test',output_dir=str(tmp_path/'output'),state_dir=str(tmp_path/'state'))
    await source.initialize()
    await source.add_scene(NovelScene(name='Hall'))
    await source.add_scene(NovelScene(name='Night',base_scene_id='Hall',variant_id='night'))
    ctx = SimpleNamespace(output_dir=Path(source.project_dir),state_dir=source.state_dir,requester_user_id='actor',project_id='test')
    async def resolve(*args,**kwargs):return ctx,'actor','test',ctx.output_dir,str(ctx.output_dir),source
    monkeypatch.setattr(scenes,'_resolve_scene_project',resolve)
    monkeypatch.setattr(scenes,'_scene_payload',lambda scene,**kwargs:{'name':scene.name})
    yield source,ctx
    await source.close()


async def upload(kind,data,name='Hall'):
    from novelvideo.api.routes import scenes
    method = getattr(scenes,'upload_scene_'+('custom_package' if kind=='package' else kind))
    return await method('test',name,UploadFile(filename='file.sog' if kind=='package' else 'file.png',file=io.BytesIO(data)),{})


@pytest.mark.parametrize('registered',[True,False])
@pytest.mark.parametrize('name',['Hall','Night'])
@pytest.mark.parametrize('kind',['master','pano','package'])
async def test_real_scene_upload_and_delete_keep_fixed_history(project,registered,name,kind):
    from novelvideo.api.routes import scenes
    source,ctx = project
    if registered:
        adapter = AssetAdapter(source,ObjectStore(source.state_dir)); base = await adapter.register_base('scene','Hall')
        ref = base if name=='Hall' else await adapter.register_variant(base.base_asset_id,'Night')
        oid = ref.variant_id or ref.base_asset_id
    first = b'old-sog' if kind=='package' else png('red')
    second = b'new-sog' if kind=='package' else png('blue')
    assert (await upload(kind,first,name))['ok']
    if registered:
        fixed = adapter.objects.read(oid)
        assert fixed['revision']==2
        blobs = [m['current_blob'] for m in fixed['payload']['_media']]
        assert blobs and all((ctx.output_dir/b['path']).read_bytes()==first for b in blobs)
    assert (await upload(kind,second,name))['ok']
    if registered:
        current = adapter.objects.read(oid)
        assert current['revision']==3
        assert all((ctx.output_dir/m['current_blob']['path']).read_bytes()==second for m in current['payload']['_media'])
        assert adapter.objects.read(oid,2)==fixed
    method=getattr(scenes,'delete_scene_'+('custom_package' if kind=='package' else kind))
    assert (await method('test',name,{}))['ok']
    if registered:
        assert adapter.objects.read(oid)['payload']['_media']==[]
        assert all((ctx.output_dir/b['path']).read_bytes()==first for b in blobs)
    else:
        assert not (Path(ctx.state_dir)/'creation').exists()


async def test_stage_manifest_snapshot_prefers_sog_and_freezes_render_relations(project):
    from novelvideo.director_world import stage_manifest
    source,ctx=project
    directory=stage_manifest.stage_dir(ctx.output_dir,'Hall');directory.mkdir(parents=True)
    for name in ['master.ply','master.sog','pano.png','collision.glb','voxel.json']:
        (directory/name).write_bytes(name.encode())
    fields={'pano_path':'pano.png','ply_path':'master.ply','pano_ply_path':'master.ply',
        'pano_depth_ply_path':'master.ply','master_ply_path':'master.ply','reverse_ply_path':'master.ply',
        'custom_scene_path':'master.ply','collision_glb_path':'collision.glb','voxel_json_path':'voxel.json',
        'pano_correction':{'front_yaw_deg':12},'splat_transform_args':{'scale':2},'source':'custom_scene'}
    stage_manifest.update_manifest(ctx.output_dir,'Hall',**fields)
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));ref=await adapter.register_base('scene','Hall')
    fixed=adapter.objects.read(ref.base_asset_id)
    assert fixed['payload']['_scene_manifest']==fields
    media={m['manifest_field']:m for m in fixed['payload']['_media']}
    for field in fields.keys() - {'source','pano_correction','splat_transform_args'}:
        assert field in media
    assert media['ply_path']['current_blob']['path'].endswith('.sog')
    assert (ctx.output_dir/media['ply_path']['current_blob']['path']).read_bytes()==b'master.sog'
    stage_manifest.update_manifest(ctx.output_dir,'Hall',**fields)
    assert (await adapter.refresh_source(ref.base_asset_id,expected_revision=1)).revision==1


def runner_setup(ctx,monkeypatch,provider):
    from novelvideo.task_backend.runners import scene_reference
    ctx.owner_project_label='test';ctx.owner_username='actor';ctx.project_name='test'
    monkeypatch.setattr(scene_reference,'get_task_manager',lambda:SimpleNamespace(update_progress_for_project=lambda *args,**kwargs:None))
    monkeypatch.setattr('novelvideo.generators.scene_reference_images.generate_scene_reference_image',provider)
    monkeypatch.setattr('novelvideo.config.get_style_preset',lambda *args,**kwargs:{})
    monkeypatch.setattr('novelvideo.api.deps.get_media_capability_store',lambda:object())
    monkeypatch.setattr('novelvideo.api.deps.get_media_credential_resolver',lambda:object())
    monkeypatch.setattr('novelvideo.media_capabilities.runtime.configuration.load_grsai_runtime_configuration',lambda *args:SimpleNamespace(model='test'))
    monkeypatch.setattr('novelvideo.media_capabilities.image.catalog.resolve_grsai_image_model',lambda **kwargs:SimpleNamespace(model='test',requested_model='test',resolution_source='explicit'))
    monkeypatch.setattr('novelvideo.project_config.load_project_config_file',lambda *args:{})
    return scene_reference


@pytest.mark.parametrize('name',['Hall','Night'])
@pytest.mark.parametrize('kind',['master','reverse_master','spatial_layout'])
async def test_runner_first_provisional_and_later_candidate_are_distinct(project,monkeypatch,name,kind):
    from novelvideo.creation.asset_media_publication import publication_targets
    source,ctx=project
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));base=await adapter.register_base('scene','Hall')
    ref=base if name=='Hall' else await adapter.register_variant(base.base_asset_id,'Night')
    oid=ref.variant_id or ref.base_asset_id
    calls=[]
    async def provider(**kwargs):
        assert not publication_targets(ctx.state_dir)
        path=kwargs['output_path_override'];path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(png('red' if not calls else 'blue'));calls.append(path);return path
    runner=runner_setup(ctx,monkeypatch,provider)
    request={'task_id':'task','payload':{'scene_name':name,'kind':kind}}
    result=await runner._run_scene_reference_asset(request,ctx)
    assert result['adoption_status']=='provisional'
    fixed=adapter.objects.read(oid)
    assert fixed['revision']==2
    result=await runner._run_scene_reference_asset(request,ctx)
    assert result['adoption_status']!='adopted'
    assert adapter.objects.read(oid)==fixed
    if kind=='spatial_layout':
        assert (await source.get_scene(name)).spatial_layout_image.endswith('spatial_layout.png')


@pytest.mark.parametrize('change',['target','source','base','unrelated'])
async def test_scene_runner_rejects_late_related_changes(project,monkeypatch,change):
    source,ctx=project
    await source.add_scene(NovelScene(name='Other'))
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));base=await adapter.register_base('scene','Hall')
    child=await adapter.register_variant(base.base_asset_id,'Night')
    async def provider(**kwargs):
        if change=='target':await upload('master',png('blue'),'Night')
        else:await source.update_scene('Other' if change=='unrelated' else ('Hall' if change=='base' else 'Night'),description='human')
        path=kwargs['output_path_override'];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(png('red'));return path
    runner=runner_setup(ctx,monkeypatch,provider)
    call=runner._run_scene_reference_asset({'payload':{'scene_name':'Night','kind':'master'}},ctx)
    if change=='unrelated':
        await call
        assert adapter.objects.read(child.variant_id)['revision']==2
    else:
        with pytest.raises(ValueError,match='changed'):
            await call
        path=ctx.output_dir/'assets/scenes/Night/master.png'
        assert path.read_bytes()==png('blue') if change=='target' else not path.exists()


@pytest.mark.parametrize('phase',['published','captured'])
@pytest.mark.parametrize('cancel',[False,True])
async def test_scene_failure_cancel_and_restart_evidence(project,monkeypatch,phase,cancel):
    from novelvideo.creation import asset_media_publication as publication
    from novelvideo.creation.asset_sync import SourceSyncPending
    source,ctx=project
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));ref=await adapter.register_base('scene','Hall')
    def checkpoint(at):
        if at==phase:raise asyncio.CancelledError() if cancel else RuntimeError('interrupted')
    monkeypatch.setattr(publication,'_checkpoint',checkpoint)
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):await upload('pano',png('red'))
    monkeypatch.setattr(publication,'_checkpoint',lambda _:None)
    assert adapter.objects.read(ref.base_asset_id,1)['payload']['_media']==[]
    with pytest.raises(HTTPException) as error:await upload('pano',png('blue'))
    assert error.value.status_code==409 and error.value.detail['code']=='asset_media_pending'
    service=publication.MediaPublication(publication.SourceSync(source,ObjectStore(source.state_dir)))
    token=next(iter(service.entries().values()))['token']
    if phase=='published':
        with pytest.raises(SourceSyncPending):await service.reconcile(token)
    else:
        await service.reconcile(token)
        assert not service.entries()
        assert adapter.objects.read(ref.base_asset_id)['revision']==2


@pytest.mark.parametrize('operation',['upload','delete'])
async def test_scene_rename_during_wait_rejects_old_locator(project,monkeypatch,operation):
    from contextlib import asynccontextmanager
    from novelvideo.creation import asset_media_publication as publication
    from novelvideo.production_workflow.coordination import asset_project_lock
    from novelvideo.api.routes import scenes
    source,ctx=project
    await upload('master',png('red'))
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));ref=await adapter.register_base('scene','Hall')
    fixed=adapter.objects.read(ref.base_asset_id,1)
    waiting=asyncio.Event()
    @asynccontextmanager
    async def observed(state_dir):
        waiting.set()
        async with asset_project_lock(state_dir):yield
    monkeypatch.setattr(publication,'asset_project_lock',observed)
    async with asset_project_lock(ctx.state_dir):
        task=asyncio.create_task(upload('master',png('blue')) if operation=='upload' else scenes.delete_scene_master('test','Hall',{}))
        await waiting.wait()
        await source.rename_scene('Hall','New')
    with pytest.raises(HTTPException) as error:await task
    assert error.value.status_code==409
    assert (ctx.output_dir/'assets/scenes/Hall/master.png').read_bytes()==png('red')
    await upload('master',png('blue'),'New')
    assert adapter.objects.read(ref.base_asset_id,1)==fixed
    assert (ctx.output_dir/fixed['payload']['_media'][0]['current_blob']['path']).read_bytes()==png('red')


@pytest.mark.parametrize('bad',['../../assets/.creation-media/history.sog','../other.sog','symlink'])
async def test_manifest_boundary_rejected_before_upload_or_delete(project,bad):
    from novelvideo.api.routes import scenes
    from novelvideo.director_world import stage_manifest
    source,ctx=project
    directory=stage_manifest.stage_dir(ctx.output_dir,'Hall');directory.mkdir(parents=True)
    outside=ctx.output_dir/'other.sog';outside.write_bytes(b'untouched')
    if bad=='symlink':
        (directory/'linked.sog').symlink_to(outside);bad='linked.sog'
    stage_manifest.update_manifest(ctx.output_dir,'Hall',custom_scene_path=bad,ply_path=bad)
    for call in [scenes.delete_scene_custom_package('test','Hall',{}),upload('package',b'new')]:
        with pytest.raises(HTTPException) as error:await call
        assert error.value.status_code==409
    assert outside.read_bytes()==b'untouched'


@pytest.mark.parametrize('suffix',['.sog','.ply','.splat','.ksplat','.glb','.json'])
def test_model_archive_keeps_loader_suffix_and_serves_exact_file(tmp_path,monkeypatch,suffix):
    from novelvideo.creation.asset_media import capture_blob
    from novelvideo.api.routes.files import _serve_or_redirect_to_oss
    monkeypatch.setenv('OSS_ENABLED','false')
    source=tmp_path/('model'+suffix);source.write_bytes(b'model')
    blob=capture_blob(tmp_path,source,archive=True)
    assert blob['path'].endswith(suffix)
    response=_serve_or_redirect_to_oss(tmp_path/blob['path'],as_download=False)
    assert Path(response.path).read_bytes()==b'model'


@pytest.mark.parametrize('role',['master','pano','custom_scene','forged'])
def test_scene_role_compatibility_requires_known_exact_derived_value(role):
    from novelvideo.creation.asset_sync import fingerprint
    item={'slot_id':'scene:Hall:base:'+role,'blob':{'path':'p','sha256':'s'}}
    previous={'_media':[item]}
    actual={'_media':[{**item,'media_role':role}]}
    assert (fingerprint(previous)==fingerprint(actual))==(role!='forged')
    assert fingerprint(previous)!=fingerprint({'_media':[{**item,'media_role':'contradiction'}]})


async def test_actual_master_overrides_workflow_metadata_and_delete_does_not_revive_old_file(project):
    from datetime import datetime,timezone
    from novelvideo.production_workflow import ProductionWorkflowStore
    from novelvideo.api.routes import scenes
    source,ctx=project
    legacy=ctx.output_dir/'director_worlds/Hall/v1/master.png';legacy.parent.mkdir(parents=True);legacy.write_bytes(png('red'))
    version=ctx.output_dir/'assets/version.png';version.parent.mkdir(parents=True);version.write_bytes(png('red'))
    workflow=ProductionWorkflowStore(Path(ctx.state_dir)/'production_workflow.json')
    workflow.register_candidate_version(slot_id='scene:Hall:base:master',asset_kind='scene_base',version_id='v1',
        asset_path='assets/version.png',source_attempt_id='original',qc_passed=True,
        generation_metadata={'canonical_path':'director_worlds/Hall/v1/master.png'},actor='test',at=datetime.now(timezone.utc))
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));ref=await adapter.register_base('scene','Hall')
    fixed=adapter.objects.read(ref.base_asset_id,1)
    await upload('master',png('blue'))
    item=adapter.objects.read(ref.base_asset_id)['payload']['_media'][0]
    assert (ctx.output_dir/item['current_blob']['path']).read_bytes()==png('blue')
    assert (ctx.output_dir/item['blob']['path']).read_bytes()==png('red')
    assert item['source_attempt_id']=='original' and item['current_source']['source_attempt_id'] is None
    await scenes.delete_scene_master('test','Hall',{})
    assert adapter.objects.read(ref.base_asset_id)['payload']['_media']==[]
    assert legacy.read_bytes()==png('red')
    assert adapter.objects.read(ref.base_asset_id,1)==fixed


@pytest.mark.parametrize('drift',[None,'role','manifest','bytes'])
async def test_scene_old_after_proof_and_actual_drift_are_distinguished(project,monkeypatch,drift):
    from novelvideo.creation import asset_media_publication as publication
    from novelvideo.creation.asset_sync import SourceSyncPending
    from novelvideo.director_world import stage_manifest
    source,ctx=project
    await upload('master',png('red'))
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));ref=await adapter.register_base('scene','Hall')
    def fail(phase):
        if phase=='captured':raise RuntimeError('lost acknowledgement')
    monkeypatch.setattr(publication,'_checkpoint',fail)
    with pytest.raises(RuntimeError):await upload('master',png('blue'))
    monkeypatch.setattr(publication,'_checkpoint',lambda _:None)
    with adapter.objects.transaction() as conn:
        row=conn.execute('SELECT * FROM asset_media_publications').fetchone()
        proof=json.loads(row['source_after'])
        for item in proof['_media']:item.pop('media_role',None)
        if drift=='role':proof['_media'][0]['media_role']='forged'
        conn.execute('UPDATE asset_media_publications SET source_after=?',(json.dumps(proof),))
    if drift=='manifest':stage_manifest.update_manifest(ctx.output_dir,'Hall',pano_correction={'front_yaw_deg':90})
    if drift=='bytes':(ctx.output_dir/'assets/scenes/Hall/master.png').write_bytes(png('green'))
    service=publication.MediaPublication(publication.SourceSync(source,ObjectStore(source.state_dir)))
    if drift:
        with pytest.raises(SourceSyncPending):await service.reconcile(row['token'])
    else:
        await service.reconcile(row['token'])
        assert adapter.objects.read(ref.base_asset_id)['revision']==2
    old=adapter.objects.read(ref.base_asset_id,1)['payload']['_media'][0]
    assert (ctx.output_dir/old['current_blob']['path']).read_bytes()==png('red')


@pytest.mark.parametrize('changed_name',['Hall','Other'])
async def test_runner_first_preflight_matches_its_actual_input(project,monkeypatch,changed_name):
    from contextlib import asynccontextmanager
    from novelvideo.creation import asset_media_publication as publication
    from novelvideo.production_workflow.coordination import asset_project_lock
    source,ctx=project
    await source.add_scene(NovelScene(name='Other'))
    calls=[]
    async def provider(**kwargs):
        calls.append(kwargs);path=kwargs['output_path_override'];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(png('red'));return path
    runner=runner_setup(ctx,monkeypatch,provider)
    waiting=asyncio.Event()
    @asynccontextmanager
    async def observed(state_dir):
        waiting.set()
        async with asset_project_lock(state_dir):yield
    monkeypatch.setattr(publication,'asset_project_lock',observed)
    async with asset_project_lock(ctx.state_dir):
        task=asyncio.create_task(runner._run_scene_reference_asset({'payload':{'scene_name':'Hall','kind':'master'}},ctx))
        await waiting.wait()
        await source.update_scene(changed_name,description='edited during wait')
    if changed_name=='Hall':
        with pytest.raises(ValueError,match='input changed'):await task
        assert calls==[]
    else:
        await task
        assert len(calls)==1
    assert not (Path(ctx.state_dir)/'creation').exists()


@pytest.mark.parametrize('change',['source','target'])
async def test_upload_read_rechecks_source_and_existing_target(project,change):
    from novelvideo.api.routes import scenes
    source,ctx=project
    await upload('master',png('red'))
    class DelayedUpload(UploadFile):
        async def read(self,size=-1):
            if change=='source':await source.update_scene('Hall',description='new source')
            else:await upload('master',png('green'))
            return png('blue')
    with pytest.raises(HTTPException) as error:
        await scenes.upload_scene_master('test','Hall',DelayedUpload(filename='image.png',file=io.BytesIO()),{})
    assert error.value.status_code==409
    assert (ctx.output_dir/'assets/scenes/Hall/master.png').read_bytes()==png('red' if change=='source' else 'green')


async def test_package_conversion_is_isolated_and_late_source_edit_rejects_publication(project,monkeypatch):
    from novelvideo import stage_asset_tasks
    from novelvideo.api.routes import scenes
    from novelvideo.production_workflow.coordination import asset_project_lock
    source,ctx=project
    loop=asyncio.get_running_loop()
    def convert(src,dest):
        async def edit():
            async with asset_project_lock(ctx.state_dir):
                await source.update_scene('Hall',description='edited during conversion')
        asyncio.run_coroutine_threadsafe(edit(),loop).result(timeout=3)
        assert not Path(dest).is_relative_to(ctx.output_dir)
        Path(dest).write_bytes(b'converted sog')
    monkeypatch.setattr(stage_asset_tasks,'_compress_ply_to_sog',convert)
    with pytest.raises(HTTPException) as error:
        await scenes.upload_scene_custom_package('test','Hall',UploadFile(filename='model.ply',file=io.BytesIO(b'ply')), {})
    assert error.value.status_code==409
    assert not (ctx.output_dir/'director_worlds/Hall/v1/custom.sog').exists()


@pytest.mark.parametrize('kind',['package','pano'])
@pytest.mark.parametrize('has_manifest',[False,True])
@pytest.mark.parametrize('existing',[False,True])
@pytest.mark.parametrize('destination',['history','other_scene','same_scene','external'])
async def test_scene_upload_rejects_exact_target_symlinks_before_writing(project,kind,has_manifest,existing,destination):
    from novelvideo.director_world import stage_manifest
    source,ctx=project
    directory=stage_manifest.stage_dir(ctx.output_dir,'Hall');directory.mkdir(parents=True)
    destinations={'history':ctx.output_dir/'assets/.creation-media/sha256/aa/fixed.sog',
        'other_scene':ctx.output_dir/'director_worlds/Night/v1/other.sog',
        'same_scene':directory/'other-role.png','external':ctx.output_dir.parent/'external.sog'}
    protected=destinations[destination];protected.parent.mkdir(parents=True,exist_ok=True)
    if existing:protected.write_bytes(b'fixed history')
    target=directory/('custom.sog' if kind=='package' else 'pano_360.png')
    target.symlink_to(protected)
    if has_manifest:
        stage_manifest.update_manifest(ctx.output_dir,'Hall',**({'custom_scene_path':target.name,'ply_path':target.name}
            if kind=='package' else {'pano_path':target.name}))
    with pytest.raises(HTTPException) as error:await upload(kind,b'new sog' if kind=='package' else png('blue'))
    assert error.value.status_code==409
    assert target.is_symlink()
    assert protected.read_bytes()==b'fixed history' if existing else not protected.exists()


@pytest.mark.parametrize('target',['custom.ply','custom.sog'])
def test_shared_package_writer_validates_all_conversion_targets_before_copy(tmp_path,monkeypatch,target):
    from novelvideo import stage_asset_tasks
    from novelvideo.director_world import stage_manifest
    directory=stage_manifest.stage_dir(tmp_path,'Hall');directory.mkdir(parents=True)
    protected=tmp_path/'assets/.creation-media/fixed.sog';protected.parent.mkdir(parents=True);protected.write_bytes(b'old')
    (directory/target).symlink_to(protected)
    source=tmp_path/'input.ply';source.write_bytes(b'new')
    monkeypatch.setattr(stage_asset_tasks,'_compress_ply_to_sog',lambda *args:pytest.fail('must reject before conversion'))
    with pytest.raises(ValueError,match='Symlink'):stage_asset_tasks.upload_scene_package(tmp_path,'Hall',source)
    assert protected.read_bytes()==b'old'
    if target=='custom.sog':assert not (directory/'custom.ply').exists()
