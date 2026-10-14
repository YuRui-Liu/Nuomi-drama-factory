"""Synchronous stage dispatcher publishes through the durable scene boundary."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.creation.asset_adapter import AssetAdapter
from novelvideo.creation.store import ObjectStore
from novelvideo.models import NovelScene
from novelvideo.sqlite_store import SQLiteStore


@pytest.fixture
async def project(tmp_path,monkeypatch):
    from novelvideo.task_backend.runners import stage_asset as runner
    from novelvideo.api import deps
    source=SQLiteStore('test',output_dir=str(tmp_path/'output'),state_dir=str(tmp_path/'state'))
    await source.initialize();await source.add_scene(NovelScene(name='Hall'))
    ctx=SimpleNamespace(output_dir=Path(source.project_dir),state_dir=Path(source.state_dir),
        owner_project_label='test',requester_user_id='actor',project_id='test')
    monkeypatch.setattr(runner,'get_task_manager',lambda:SimpleNamespace(update_progress_for_project=lambda *a,**k:None))
    monkeypatch.setattr(runner,'raise_if_envelope_cancel_requested',lambda *a,**k:None)
    monkeypatch.setattr(runner,'remaining_timeout_seconds',lambda *a,**k:k['default_seconds'])
    monkeypatch.setattr(deps,'make_static_url_for_context',lambda ctx,rel,**k:rel)
    yield source,ctx
    await source.close()


@pytest.mark.parametrize('step',['pano_from_text','pano_sharp','single_face_sharp','splat_collision','upload_scene_package'])
async def test_dispatcher_publishes_registered_scene_and_preserves_history(project,monkeypatch,step):
    from novelvideo import stage_asset_tasks as tasks
    from novelvideo.task_backend.runners.stage_asset import run_stage_asset
    source,ctx=project
    incoming=ctx.output_dir/'incoming.sog';incoming.write_bytes(b'package')
    stage=ctx.output_dir/'director_worlds/Hall/v1';stage.mkdir(parents=True)
    (stage/'pano_360.png').write_bytes(b'input')
    from novelvideo.director_world import stage_manifest
    stage_manifest.update_manifest(ctx.output_dir,'Hall',pano_path='pano_360.png',ply_path='model.sog')
    (stage/'model.sog').write_bytes(b'input model')
    master=ctx.output_dir/'assets/scenes/Hall/master.png';master.parent.mkdir(parents=True);master.write_bytes(b'master')
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));ref=await adapter.register_base('scene','Hall')
    before=adapter.objects.read(ref.base_asset_id)
    def generated(root,name,*args,**kwargs):
        out=Path(kwargs.get('artifact_dir') or stage)
        out.mkdir(parents=True,exist_ok=True)
        key='pano_path' if step=='pano_from_text' else ('collision_glb_path' if step=='splat_collision' else 'ply_path')
        dest=out/('pano_360.png' if key=='pano_path' else ('scene.collision.glb' if key=='collision_glb_path' else 'new.sog'))
        dest.write_bytes(b'generated')
        return {'ok':True,key:str(dest),'manifest_patch':{key:dest.name,'source':'generated'}}
    if step!='upload_scene_package':
        monkeypatch.setattr(tasks,{'pano_from_text':'run_scene_360','pano_sharp':'run_pano_sharp',
            'single_face_sharp':'run_single_face_sharp','splat_collision':'run_splat_collision'}[step],generated)
    result=await asyncio.to_thread(run_stage_asset,{'payload':{'step':step,'scene_name':'Hall','params':{'src_asset':str(incoming)}}},ctx)
    assert result['ok']
    current=adapter.objects.read(ref.base_asset_id)
    assert current['revision']==2
    assert adapter.objects.read(ref.base_asset_id,1)==before
    assert all((ctx.output_dir/m['current_blob']['path']).is_file() for m in before['payload']['_media'])


@pytest.mark.parametrize('change',['input','scene','unrelated','cancel'])
async def test_generation_uses_frozen_input_and_rejects_late_changes(project,monkeypatch,change):
    from novelvideo import stage_asset_tasks as tasks
    from novelvideo.task_backend.runners import stage_asset as runner
    from novelvideo.task_backend.cancel import TaskCancelled
    source,ctx=project
    master=ctx.output_dir/'assets/scenes/Hall/master.png';master.parent.mkdir(parents=True);master.write_bytes(b'old master')
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));ref=await adapter.register_base('scene','Hall')
    cancelled=False
    def check(*a,**kw):
        if cancelled:raise TaskCancelled()
    monkeypatch.setattr(runner,'raise_if_envelope_cancel_requested',check)
    def generate(root,name,**kw):
        nonlocal cancelled
        image=Path(kw['image_path'])
        assert image!=master and image.read_bytes()==b'old master'
        if change=='input':master.write_bytes(b'human')
        elif change in {'scene','unrelated'}:
            async def edit():
                db=SQLiteStore('test',output_dir=str(ctx.output_dir),state_dir=str(ctx.state_dir))
                try:
                    if change=='scene':await db.update_scene('Hall',description='human')
                    else:await db.add_scene(NovelScene(name='Other'))
                finally:await db.close()
            asyncio.run(edit())
        else:cancelled=True
        assert image.read_bytes()==b'old master'
        dest=Path(kw['artifact_dir'])/'master_sharp.sog';dest.parent.mkdir(parents=True);dest.write_bytes(b'generated')
        return {'ok':True,'ply_path':str(dest),'manifest_patch':{'ply_path':dest.name,'master_ply_path':dest.name}}
    monkeypatch.setattr(tasks,'run_single_face_sharp',generate)
    call=asyncio.to_thread(runner.run_stage_asset,{'payload':{'scene_name':'Hall','step':'single_face_sharp'}},ctx)
    if change=='unrelated':
        result=await call;assert result['ok'] and adapter.objects.read(ref.base_asset_id)['revision']==2
    else:
        with pytest.raises(TaskCancelled if change=='cancel' else ValueError):await call
        assert not (ctx.output_dir/'director_worlds/Hall/v1/master_sharp.sog').exists()
    assert (ctx.output_dir/adapter.objects.read(ref.base_asset_id,1)['payload']['_media'][0]['current_blob']['path']).read_bytes()==b'old master'


@pytest.mark.parametrize('failure',['manifest','captured'])
async def test_stage_failure_retains_recoverable_evidence(project,monkeypatch,failure):
    from novelvideo import stage_asset_tasks as tasks
    from novelvideo.creation import asset_media_publication as media
    from novelvideo.creation.asset_sync import SourceSyncPending
    from novelvideo.task_backend.runners.stage_asset import run_stage_asset
    from novelvideo.director_world import stage_manifest
    source,ctx=project
    incoming=ctx.output_dir/'source.sog';incoming.write_bytes(b'old')
    tasks.upload_scene_package(ctx.output_dir,'Hall',incoming)
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));ref=await adapter.register_base('scene','Hall')
    old=adapter.objects.read(ref.base_asset_id)
    incoming.write_bytes(b'new')
    original=stage_manifest.update_manifest
    def fail(*a,**kw):raise RuntimeError('manifest failed')
    def checkpoint(phase):
        if phase=='captured':raise RuntimeError('captured failed')
    if failure=='manifest':monkeypatch.setattr(stage_manifest,'update_manifest',fail)
    else:monkeypatch.setattr(media,'_checkpoint',checkpoint)
    with pytest.raises(RuntimeError):await asyncio.to_thread(run_stage_asset,{'payload':{'scene_name':'Hall','step':'upload_scene_package','params':{'src_asset':str(incoming)}}},ctx)
    monkeypatch.setattr(stage_manifest,'update_manifest',original);monkeypatch.setattr(media,'_checkpoint',lambda p:None)
    assert adapter.objects.read(ref.base_asset_id,1)==old
    assert all((ctx.output_dir/m['current_blob']['path']).read_bytes()==b'old' for m in old['payload']['_media'])
    service=media.MediaPublication(media.SourceSync(source,ObjectStore(source.state_dir)))
    if failure=='captured':
        with pytest.raises(SourceSyncPending):adapter.objects.read(ref.base_asset_id)
        await service.reconcile(next(iter(service.entries().values()))['token'])
        assert adapter.objects.read(ref.base_asset_id)['revision']==2
    else:
        assert service.entries()


@pytest.mark.parametrize('step',['pano_from_master','pano_sharp','single_face_sharp','splat_collision'])
async def test_real_generators_return_complete_manifest_for_durable_publish(project,monkeypatch,step):
    from novelvideo import stage_asset_tasks as tasks
    from novelvideo.director_world import stage_manifest
    from novelvideo.task_backend.runners.stage_asset import run_stage_asset
    source,ctx=project
    master=ctx.output_dir/'assets/scenes/Hall/master.png';master.parent.mkdir(parents=True);master.write_bytes(b'master')
    (master.parent/'reverse_master.png').write_bytes(b'reverse')
    stage=stage_manifest.stage_dir(ctx.output_dir,'Hall');stage.mkdir(parents=True)
    (stage/'pano_360.png').write_bytes(b'pano');(stage/'existing.sog').write_bytes(b'model')
    stage_manifest.update_manifest(ctx.output_dir,'Hall',source='uploaded_master',pano_path='pano_360.png',ply_path='existing.sog')
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));ref=await adapter.register_base('scene','Hall')
    monkeypatch.delenv('OPENROUTER_API_KEY',raising=False)
    monkeypatch.setattr(tasks.pano_sharp,'sharp_available',lambda:True)
    monkeypatch.setattr(tasks.pano_sharp,'da2_available',lambda:True)
    monkeypatch.setattr(tasks,'splat_transform_executable',lambda:'fake-cli')
    monkeypatch.setattr(tasks,'_reserve_scene_360_model_call',lambda *a,**k:None)
    monkeypatch.setattr(tasks,'_confirm_scene_360_model_call',lambda *a,**k:None)
    monkeypatch.setattr(tasks,'_refund_scene_360_model_call',lambda *a,**k:None)
    monkeypatch.setattr(tasks,'resolve_scene_360_image_model',lambda **k:'mock-model')
    def compress(src,dest,**kw):Path(dest).write_bytes(b'compressed');return Path(dest)
    monkeypatch.setattr(tasks,'_compress_ply_to_sog',compress)
    def subprocess(cmd,**kw):
        if '-K' in cmd:
            path=Path(cmd[-1]);path.write_text('{}');path.with_name('scene.collision.glb').write_bytes(b'collision')
        else:
            directory=Path(cmd[cmd.index('--output-dir')+1]);directory.mkdir(parents=True,exist_ok=True)
            for filename in ['pano_depth.ply','pano_sharp_merged.ply','scene_panorama_2to1.png']:(directory/filename).write_bytes(b'generated')
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    monkeypatch.setattr(tasks,'run_project_subprocess',subprocess)
    result=await asyncio.to_thread(run_stage_asset,{'payload':{'scene_name':'Hall','step':step}},ctx)
    assert result['ok'] and result['output_url']
    manifest=stage_manifest.load_manifest(ctx.output_dir,'Hall')
    field={'pano_from_master':'scene_360_args','pano_sharp':'pano_sharp_args','single_face_sharp':'single_face_sharp_args','splat_collision':'splat_transform_args'}[step]
    assert manifest[field]
    if step=='pano_from_master':
        assert manifest['pano_correction']['source']=='scene_360_master_reverse_safe_seam'
        assert manifest[field]['master_path'].startswith(str(ctx.output_dir/'stage_candidates'))
        for key in ['spatial_contract_path','overlap_analysis_path']:
            assert not manifest[field][key] or Path(manifest[field][key]).is_file()
    if step=='pano_sharp':assert manifest['source']=='uploaded_master' and manifest[field]['front_yaw_deg']==0
    assert adapter.objects.read(ref.base_asset_id)['revision']==2


@pytest.mark.parametrize('suffix',['.splat','.ksplat'])
async def test_upload_format_switch_avoids_existing_sog_sidecar(project,suffix):
    from novelvideo import stage_asset_tasks as tasks
    from novelvideo.director_world import stage_manifest
    from novelvideo.task_backend.runners.stage_asset import run_stage_asset
    source,ctx=project
    old=ctx.output_dir/'old.sog';old.write_bytes(b'old')
    tasks.upload_scene_package(ctx.output_dir,'Hall',old)
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));ref=await adapter.register_base('scene','Hall')
    frozen=adapter.objects.read(ref.base_asset_id)
    incoming=ctx.output_dir/('new'+suffix);incoming.write_bytes(b'new')
    result=await asyncio.to_thread(run_stage_asset,{'payload':{'scene_name':'Hall','step':'upload_scene_package','params':{'src_asset':str(incoming)}}},ctx)
    assert stage_manifest.resolve_ply_path(ctx.output_dir,'Hall').read_bytes()==b'new'
    assert Path(result['scene_path']).suffix==suffix
    assert (ctx.output_dir/'director_worlds/Hall/v1/custom.sog').read_bytes()==b'old'
    assert all((ctx.output_dir/m['current_blob']['path']).read_bytes()==b'old' for m in frozen['payload']['_media'])


async def test_candidate_flag_cannot_direct_output_to_current_scene(project,monkeypatch):
    from novelvideo import stage_asset_tasks as tasks
    from novelvideo.task_backend.runners.stage_asset import run_stage_asset
    source,ctx=project
    directory=ctx.output_dir/'director_worlds/Hall/v1';directory.mkdir(parents=True)
    current=directory/'pano_360.png';current.write_bytes(b'old')
    from novelvideo.director_world import stage_manifest
    stage_manifest.update_manifest(ctx.output_dir,'Hall',pano_path=current.name)
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));ref=await adapter.register_base('scene','Hall')
    before=adapter.objects.read(ref.base_asset_id)
    manifest_before=stage_manifest.manifest_path(ctx.output_dir,'Hall').read_bytes()
    def generate(root,name,**kw):
        dest=Path(kw['artifact_dir'])/'pano_360.png';dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(b'candidate')
        return {'ok':True,'pano_path':str(dest),'manifest_patch':{'pano_path':dest.name}}
    monkeypatch.setattr(tasks,'run_scene_360',generate)
    result=await asyncio.to_thread(run_stage_asset,{'payload':{'scene_name':'Hall','step':'pano_from_text',
        'params':{'update_manifest':False,'artifact_dir':str(directory)}}},ctx)
    assert current.read_bytes()==b'old'
    assert Path(result['pano_path']).read_bytes()==b'candidate' and Path(result['pano_path'])!=current
    assert result['image_url']==result['output_url']
    assert adapter.objects.read(ref.base_asset_id)==before
    assert stage_manifest.manifest_path(ctx.output_dir,'Hall').read_bytes()==manifest_before


async def test_unbound_canvas_candidate_never_bootstraps_source_or_creation(project,monkeypatch,tmp_path):
    from novelvideo import stage_asset_tasks as tasks
    from novelvideo.task_backend.runners.stage_asset import run_stage_asset
    _,base_ctx=project
    root=tmp_path/'candidate-only';root.mkdir()
    ctx=SimpleNamespace(**vars(base_ctx));ctx.output_dir=root;ctx.state_dir=tmp_path/'candidate-state'
    master=root/'reference.png';master.write_bytes(b'reference')
    def generate(project_dir,name,**kw):
        assert Path(kw['master_path_override']).read_bytes()==b'reference'
        path=Path(kw['artifact_dir'])/'pano_360.png';path.parent.mkdir(parents=True);path.write_bytes(b'candidate')
        return {'pano_path':str(path),'manifest_patch':{'pano_path':path.name}}
    monkeypatch.setattr(tasks,'run_scene_360',generate)
    result=await asyncio.to_thread(run_stage_asset,{'payload':{'scene_name':'Canvas Label','step':'pano_from_master',
        'params':{'update_manifest':False,'master_path':str(master)}}},ctx)
    assert result['ok'] and not result['manifest_updated']
    assert not (ctx.state_dir/'data.db').exists() and not (ctx.state_dir/'creation').exists()


@pytest.mark.parametrize('danger',['target_link','manifest_link','input_link','project_mismatch'])
async def test_stage_rejects_unsafe_paths_without_overwriting_history(project,monkeypatch,danger):
    from novelvideo.task_backend.runners.stage_asset import run_stage_asset
    source,ctx=project
    incoming=ctx.output_dir/'incoming.sog';incoming.write_bytes(b'new')
    protected=ctx.output_dir/'assets/.creation-media/fixed.sog';protected.parent.mkdir(parents=True);protected.write_bytes(b'old')
    directory=ctx.output_dir/'director_worlds/Hall/v1';directory.mkdir(parents=True)
    payload={'scene_name':'Hall','step':'upload_scene_package','params':{'src_asset':str(incoming)}}
    if danger=='target_link':(directory/'custom.sog').symlink_to(protected)
    elif danger=='manifest_link':(directory/'stage_manifest.json').symlink_to(protected)
    elif danger=='input_link':
        incoming.unlink();incoming.symlink_to(protected)
    else:payload['project_dir']=str(ctx.output_dir.parent)
    with pytest.raises(ValueError):await asyncio.to_thread(run_stage_asset,{'payload':payload},ctx)
    assert protected.read_bytes()==b'old'


@pytest.mark.parametrize('change',['none','base','context','rename'])
async def test_variant_alias_rechecks_parent_context_and_locator(project,monkeypatch,change):
    import sqlite3
    from novelvideo import stage_asset_tasks as tasks
    from novelvideo.task_backend.runners.stage_asset import run_stage_asset
    source,ctx=project
    await source.add_scene(NovelScene(name='Night',base_scene_id='Hall',variant_id='night',aliases=['Alias']))
    master=ctx.output_dir/'assets/scenes/Night/master.png';master.parent.mkdir(parents=True);master.write_bytes(b'master')
    with sqlite3.connect(source.db_path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS script_documents(id TEXT PRIMARY KEY,title TEXT,kind TEXT,current_revision_id TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS script_revisions(id TEXT PRIMARY KEY,markdown TEXT,blocks TEXT)')
        db.execute("INSERT INTO script_documents VALUES('outline','Outline','outline','r1')")
        db.execute("INSERT INTO script_revisions VALUES('r1','old context','[]')")
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));base=await adapter.register_base('scene','Hall');child=await adapter.register_variant(base.base_asset_id,'Night')
    def generate(root,name,**kw):
        assert name=='Night'
        async def edit():
            db=SQLiteStore('test',output_dir=str(ctx.output_dir),state_dir=str(ctx.state_dir))
            try:
                if change=='base':await db.update_scene('Hall',description='human')
                elif change=='rename':await db.rename_scene('Night','New')
            finally:await db.close()
        if change in {'base','rename'}:asyncio.run(edit())
        elif change=='context':
            with sqlite3.connect(source.db_path) as db:db.execute("UPDATE script_revisions SET markdown='new context' WHERE id='r1'")
        path=Path(kw['artifact_dir'])/'master_sharp.sog';path.parent.mkdir(parents=True);path.write_bytes(b'model')
        return {'ok':True,'ply_path':str(path),'manifest_patch':{'ply_path':path.name,'master_ply_path':path.name}}
    monkeypatch.setattr(tasks,'run_single_face_sharp',generate)
    call=asyncio.to_thread(run_stage_asset,{'payload':{'scene_name':'Alias','step':'single_face_sharp'}},ctx)
    if change=='none':
        result=await call
        assert 'Night' in Path(result['ply_path']).parts
        assert adapter.objects.read(child.variant_id)['revision']==2
        assert adapter.objects.read(base.base_asset_id)['revision']==1
    else:
        with pytest.raises(ValueError):await call
        assert not (ctx.output_dir/'director_worlds/Night/v1/master_sharp.sog').exists()


async def test_cancellation_during_publish_is_pending_not_late_success(project,monkeypatch):
    from novelvideo import stage_asset_tasks as tasks
    from novelvideo.creation.asset_sync import SourceSyncPending
    from novelvideo.task_backend.cancel import TaskCancelled
    from novelvideo.task_backend.runners.stage_asset import run_stage_asset
    source,ctx=project
    incoming=ctx.output_dir/'source.sog';incoming.write_bytes(b'old')
    target=Path(tasks.upload_scene_package(ctx.output_dir,'Hall',incoming)['scene_path'])
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));ref=await adapter.register_base('scene','Hall')
    old=adapter.objects.read(ref.base_asset_id);incoming.write_bytes(b'new')
    async def cancelled(**kwargs):return target.read_bytes()==b'new'
    monkeypatch.setattr('novelvideo.task_backend.cancel.is_cancel_requested',cancelled)
    with pytest.raises(TaskCancelled):
        await asyncio.to_thread(run_stage_asset,{'__run_task_id':'task','payload':{'scene_name':'Hall','step':'upload_scene_package','params':{'src_asset':str(incoming)}}},ctx)
    with pytest.raises(SourceSyncPending):adapter.objects.read(ref.base_asset_id)
    assert adapter.objects.read(ref.base_asset_id,1)==old
