"""Real canvas push publishes trusted asset media and metadata together."""
import io
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image

from novelvideo.api.schemas import PushRequest
from novelvideo.creation.asset_adapter import AssetAdapter
from novelvideo.creation.store import ObjectStore
from novelvideo.models import NovelCharacter,CharacterIdentity,NovelScene
from novelvideo.sqlite_store import SQLiteStore


def png(color):
    data=io.BytesIO();Image.new('RGB',(16,8),color).save(data,format='PNG');return data.getvalue()


@pytest.fixture
async def project(tmp_path,monkeypatch):
    from novelvideo.api.routes import freezone
    source=SQLiteStore('test',output_dir=str(tmp_path/'output'),state_dir=str(tmp_path/'state'))
    await source.initialize()
    await source.add_character(NovelCharacter(name='Lin'))
    await source.add_character_identity('Lin',CharacterIdentity(character_name='Lin',identity_id='stable-id',identity_name='coat'))
    await source.add_scene(NovelScene(name='Hall'))
    await source.add_scene(NovelScene(name='Night',base_scene_id='Hall',variant_id='night'))
    ctx=SimpleNamespace(output_dir=Path(source.project_dir),state_dir=source.state_dir,requester_user_id='actor',project_id='test')
    async def resolve(*args,**kwargs):return ctx,'actor','test',ctx.output_dir,str(ctx.output_dir)
    monkeypatch.setattr(freezone,'_resolve_freezone_project',resolve)
    monkeypatch.setattr(freezone,'make_static_url_for_context',lambda ctx,rel,**kwargs:rel)
    events=[]
    monkeypatch.setattr(freezone,'_append_canvas_event',lambda **kwargs:events.append(kwargs))
    yield source,ctx,events
    await source.close()


async def push(ctx,kind,content,*,identity='stable-id',scene='Hall',source_path=None,character='Lin'):
    from novelvideo.api.routes import freezone
    target={'kind':kind}
    if kind.startswith('scene_'):target['scene_id']=scene
    else:
        target['character']=character
        if kind!='portrait':target['identity_id']=identity
    if source_path is None:
        suffix='.sog' if '3gs' in kind else '.png'
        source_path=ctx.output_dir/('freezone/_outputs/input'+suffix)
        source_path.parent.mkdir(parents=True,exist_ok=True);source_path.write_bytes(content)
    return await freezone.freezone_push('test',PushRequest(source_url=source_path.relative_to(ctx.output_dir).as_posix(),target=target,mark_stale=False),{'id':'actor'})


@pytest.mark.parametrize('registered',[True,False])
@pytest.mark.parametrize('kind',['portrait','identity','identity_costume','identity_portrait','scene_master','scene_reverse_master',
    'scene_spatial_layout','scene_360','scene_director_pano_360','scene_3gs_custom_scene'])
async def test_real_global_push_keeps_fixed_media_and_source_metadata(project,registered,kind):
    source,ctx,events=project
    oid=None
    if registered:
        adapter=AssetAdapter(source,ObjectStore(source.state_dir))
        if kind.startswith('scene_'):
            base=await adapter.register_base('scene','Hall');ref=await adapter.register_variant(base.base_asset_id,'Night')
        else:
            base=await adapter.register_base('character','Lin')
            ref=base if kind=='portrait' else await adapter.register_variant(base.base_asset_id,'stable-id')
        oid=ref.variant_id or ref.base_asset_id
    red=b'old sog' if '3gs' in kind else png('red');blue=b'new sog' if '3gs' in kind else png('blue')
    result=await push(ctx,kind,red,scene='Night')
    assert result['ok']
    target=Path(result['data']['target_path'])
    if kind.startswith('identity'):assert 'stable-id' not in target.name and 'coat' in target.name
    if registered:
        fixed=adapter.objects.read(oid);assert fixed['revision']==2
        blobs=[m['current_blob'] for m in fixed['payload']['_media']]
        assert blobs and all((ctx.output_dir/b['path']).read_bytes()==red for b in blobs)
    await push(ctx,kind,blue,scene='Night')
    if registered:
        current=adapter.objects.read(oid);assert current['revision']==3
        assert adapter.objects.read(oid,2)==fixed
        assert all((ctx.output_dir/b['path']).read_bytes()==red for b in blobs)
        await push(ctx,kind,None,scene='Night',source_path=target)
        assert adapter.objects.read(oid)==current
    else:assert not (Path(ctx.state_dir)/'creation').exists()
    await source.load_graph_state()
    identity=source.get_character('Lin').identities[0]
    if kind=='identity_costume':assert identity.costume_image==str(target)
    if kind=='identity_portrait':assert identity.portrait_image==str(target)
    assert events


@pytest.mark.parametrize('phase',['published','captured','committed'])
@pytest.mark.parametrize('cancel',[False,True])
async def test_failed_push_retains_evidence_without_success_event(project,monkeypatch,phase,cancel):
    from novelvideo.creation import asset_media_publication as publication
    from novelvideo.creation.asset_sync import SourceSyncPending
    source,ctx,events=project
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));ref=await adapter.register_base('character','Lin')
    def checkpoint(at):
        if at==phase:raise asyncio.CancelledError() if cancel else RuntimeError('interrupted')
    monkeypatch.setattr(publication,'_checkpoint',checkpoint)
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):await push(ctx,'portrait',png('red'))
    assert not events
    monkeypatch.setattr(publication,'_checkpoint',lambda _:None)
    assert adapter.objects.read(ref.base_asset_id,1)['payload']['_media']==[]
    service=publication.MediaPublication(publication.SourceSync(source,ObjectStore(source.state_dir)))
    if phase=='committed':
        assert adapter.objects.read(ref.base_asset_id)['revision']==2
        return
    with pytest.raises(HTTPException) as error:await push(ctx,'portrait',png('blue'))
    assert error.value.status_code==409 and error.value.detail['code']=='asset_media_pending'
    token=next(iter(service.entries().values()))['token']
    if phase=='published':
        with pytest.raises(SourceSyncPending):await service.reconcile(token)
    else:
        await service.reconcile(token)
        assert not service.entries()
        assert adapter.objects.read(ref.base_asset_id)['revision']==2


@pytest.mark.parametrize('kind',['portrait','identity_costume','scene_director_pano_360','scene_3gs_custom_scene'])
@pytest.mark.parametrize('location',['target','history'])
@pytest.mark.parametrize('exists',[True,False])
async def test_push_rejects_target_and_backup_links_before_write(project,kind,location,exists):
    from novelvideo.freezone.slots import slot_target_path
    source,ctx,events=project
    target_spec={'kind':kind,'scene_id':'Hall'} if kind.startswith('scene_') else {'kind':kind,'character':'Lin','identity_id':'coat'}
    spec=PushRequest(source_url='unused',target=target_spec).target
    target=slot_target_path(ctx.output_dir,spec)
    other=target.parent/('another.sog' if '3gs' in kind else 'another.png')
    target.parent.mkdir(parents=True,exist_ok=True)
    if exists:other.write_bytes(b'protected')
    if location=='target':target.symlink_to(other)
    else:
        target.write_bytes(png('red'))
        outside=ctx.output_dir/'assets/.creation-media/protected'
        outside.mkdir(parents=True)
        (target.parent/'_history').symlink_to(outside,target_is_directory=True)
    with pytest.raises(HTTPException) as error:await push(ctx,kind,b'new sog' if '3gs' in kind else png('blue'))
    assert error.value.status_code==409
    assert other.read_bytes()==b'protected' if exists else not other.exists()
    assert not events


async def test_archive_is_readonly_push_source(project):
    source,ctx,events=project
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));ref=await adapter.register_base('character','Lin')
    await push(ctx,'portrait',png('red'))
    fixed=adapter.objects.read(ref.base_asset_id)
    archive=ctx.output_dir/fixed['payload']['_media'][0]['current_blob']['path']
    await push(ctx,'portrait',png('blue'))
    result=await push(ctx,'portrait',None,source_path=archive)
    assert Path(result['data']['target_path']).read_bytes()==png('red')
    assert archive.read_bytes()==png('red') and adapter.objects.read(ref.base_asset_id,2)==fixed


@pytest.mark.parametrize('kind',['identity','identity_costume','identity_portrait'])
async def test_trusted_legacy_identity_name_resolves_to_stable_id(project,kind):
    source,ctx,events=project
    result=await push(ctx,kind,png('red'),identity='coat')
    assert 'coat' in Path(result['data']['target_path']).name
    assert events[-1]['payload']['target']['identity_id']=='stable-id'


async def test_identity_ambiguity_is_rejected_before_touch(project):
    source,ctx,events=project
    await source.add_character_identity('Lin',CharacterIdentity(character_name='Lin',identity_id='second',identity_name='stable-id'))
    with pytest.raises(HTTPException) as error:await push(ctx,'identity',png('red'))
    assert error.value.status_code==409 and 'ambiguous' in str(error.value.detail)
    assert not events


@pytest.mark.parametrize('kind',['identity_costume','scene_master'])
@pytest.mark.parametrize('change',['rename','source_bytes','target_bytes','metadata','unrelated'])
async def test_push_revalidates_after_waiting_for_lock(project,monkeypatch,kind,change):
    from novelvideo.creation import asset_media_publication as publication
    from novelvideo.production_workflow.coordination import asset_project_lock
    source,ctx,events=project
    first=await push(ctx,kind,png('red'))
    target=Path(first['data']['target_path']);events.clear()
    waiting=asyncio.Event()
    @asynccontextmanager
    async def observed(state):
        waiting.set()
        async with asset_project_lock(state):yield
    monkeypatch.setattr(publication,'asset_project_lock',observed)
    async with asset_project_lock(ctx.state_dir):
        task=asyncio.create_task(push(ctx,kind,png('blue')))
        await waiting.wait()
        if change=='rename':
            if kind.startswith('scene_'):await source.rename_scene('Hall','New')
            else:await source.update_character_identity('Lin','stable-id',identity_name='newcoat')
        elif change=='source_bytes':(ctx.output_dir/'freezone/_outputs/input.png').write_bytes(png('green'))
        elif change=='target_bytes':target.write_bytes(png('green'))
        elif change=='metadata':
            if kind.startswith('scene_'):await source.update_scene('Hall',description='human')
            else:await source.update_character_identity('Lin','stable-id',appearance_details='human')
        else:await source.add_character(NovelCharacter(name='Unrelated'))
    if change=='unrelated':
        assert (await task)['ok'] and target.read_bytes()==png('blue')
    else:
        with pytest.raises(HTTPException) as error:await task
        assert error.value.status_code==409
        assert target.read_bytes()==png('green' if change=='target_bytes' else 'red')
        assert not events


async def test_costume_sql_failure_cannot_report_success(project,monkeypatch):
    from novelvideo.creation import asset_media_publication as publication
    source,ctx,events=project
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));base=await adapter.register_base('character','Lin')
    child=await adapter.register_variant(base.base_asset_id,'stable-id')
    async def fail(*args,**kwargs):raise RuntimeError('SQL unavailable')
    monkeypatch.setattr(SQLiteStore,'update_character_identity',fail)
    with pytest.raises(RuntimeError,match='SQL unavailable'):await push(ctx,'identity_costume',png('red'))
    assert not events and adapter.objects.read(child.variant_id,1)['revision']==1
    from novelvideo.creation.asset_sync import SourceSyncPending
    with pytest.raises(SourceSyncPending):adapter.objects.read(child.variant_id)
    service=publication.MediaPublication(publication.SourceSync(source,ObjectStore(source.state_dir)))
    assert service.entries()


@pytest.mark.parametrize('kind',['scene_3gs_active_ply','scene_3gs_master_ply','scene_3gs_reverse_ply','scene_3gs_pano_ply','scene_3gs_collision_glb'])
async def test_remaining_scene_roles_publish_manifest_and_fixed_bytes(project,kind):
    source,ctx,events=project
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));ref=await adapter.register_base('scene','Hall')
    path=ctx.output_dir/('freezone/_outputs/model.glb' if kind.endswith('glb') else 'freezone/_outputs/model.sog')
    path.parent.mkdir(parents=True);path.write_bytes(b'old model')
    await push(ctx,kind,None,source_path=path)
    old=adapter.objects.read(ref.base_asset_id)
    assert old['payload']['_scene_manifest']
    blobs=[ctx.output_dir/m['current_blob']['path'] for m in old['payload']['_media']]
    assert blobs and all(p.read_bytes()==b'old model' for p in blobs)
    path.write_bytes(b'new model');await push(ctx,kind,None,source_path=path)
    current=adapter.objects.read(ref.base_asset_id)
    assert current['revision']==3 and all(p.read_bytes()==b'old model' for p in blobs)
    assert all((ctx.output_dir/m['current_blob']['path']).read_bytes()==b'new model' for m in current['payload']['_media'])


async def test_manifest_cannot_turn_history_into_writable_target(project):
    from novelvideo.director_world import stage_manifest
    source,ctx,events=project
    archive=ctx.output_dir/'assets/.creation-media/sha256/fixed.sog'
    archive.parent.mkdir(parents=True);archive.write_bytes(b'history')
    stage_manifest.update_manifest(ctx.output_dir,'Hall',custom_scene_path=str(archive))
    with pytest.raises(HTTPException) as error:await push(ctx,'scene_3gs_custom_scene',b'new')
    assert error.value.status_code==409 and archive.read_bytes()==b'history' and not events


async def test_latest_identity_locator_publishes_and_retired_name_rejects(project):
    source,ctx,events=project
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));base=await adapter.register_base('character','Lin')
    child=await adapter.register_variant(base.base_asset_id,'stable-id')
    await push(ctx,'identity',png('red'))
    fixed=adapter.objects.read(child.variant_id)
    await source.update_character_identity('Lin','stable-id',identity_name='newcoat')
    result=await push(ctx,'identity',png('blue'))
    assert 'newcoat' in Path(result['data']['target_path']).name
    assert adapter.objects.read(child.variant_id,2)==fixed
    with pytest.raises(HTTPException):await push(ctx,'identity',png('green'),identity='coat')
    assert Path(result['data']['target_path']).read_bytes()==png('blue')


@pytest.mark.parametrize('suffix',['.ply','.splat','.ksplat'])
async def test_push_keeps_3d_format_in_target_and_fixed_reference(project,suffix):
    source,ctx,events=project
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));ref=await adapter.register_base('scene','Hall')
    path=ctx.output_dir/('freezone/_outputs/model'+suffix)
    path.parent.mkdir(parents=True);path.write_bytes(b'model format')
    result=await push(ctx,'scene_3gs_master_ply',None,source_path=path)
    assert Path(result['data']['target_path']).suffix==suffix
    for item in adapter.objects.read(ref.base_asset_id)['payload']['_media']:
        assert Path(item['current_blob']['path']).suffix==suffix


async def test_format_switch_does_not_reuse_old_sog_sidecar(project):
    from novelvideo.director_world import stage_manifest
    source,ctx,events=project
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));ref=await adapter.register_base('scene','Hall')
    first=await push(ctx,'scene_3gs_master_ply',b'old sog')
    old=adapter.objects.read(ref.base_asset_id)
    path=ctx.output_dir/'freezone/_outputs/new.ply';path.write_bytes(b'new ply')
    result=await push(ctx,'scene_3gs_master_ply',None,source_path=path)
    current=adapter.objects.read(ref.base_asset_id)
    resolved=stage_manifest.resolve_ply_path(ctx.output_dir,'Hall',ply_kind='master')
    assert resolved==Path(result['data']['target_path']) and resolved.read_bytes()==b'new ply'
    assert Path(first['data']['target_path']).read_bytes()==b'old sog'
    assert all((ctx.output_dir/m['current_blob']['path']).read_bytes()==b'new ply' for m in current['payload']['_media'])
    assert all((ctx.output_dir/m['current_blob']['path']).read_bytes()==b'old sog' for m in old['payload']['_media'])
    await push(ctx,'scene_3gs_master_ply',None,source_path=resolved)
    assert adapter.objects.read(ref.base_asset_id)==current


@pytest.mark.parametrize('kind',['portrait','identity','identity_costume','identity_portrait'])
async def test_character_alias_uses_one_canonical_asset_everywhere(project,kind):
    source,ctx,events=project
    await source.update_character('Lin',aliases=['Alias'])
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));base=await adapter.register_base('character','Lin')
    ref=base if kind=='portrait' else await adapter.register_variant(base.base_asset_id,'stable-id')
    result=await push(ctx,kind,png('blue'),character='Alias')
    target=Path(result['data']['target_path'])
    assert target.is_relative_to(ctx.output_dir/'assets/characters/Lin')
    assert not (ctx.output_dir/'assets/characters/Alias').exists()
    current=adapter.objects.read(ref.variant_id or ref.base_asset_id)
    assert current['revision']==2 and current['payload']['_media']
    assert all((ctx.output_dir/m['current_blob']['path']).read_bytes()==png('blue') for m in current['payload']['_media'])
    assert events[-1]['payload']['target']['character']=='Lin'


@pytest.mark.parametrize('collision',['shared_alias','canonical_alias','retired_alias'])
@pytest.mark.parametrize('kind',['portrait','identity','identity_costume','identity_portrait'])
async def test_ambiguous_or_retired_character_alias_cannot_write(project,kind,collision):
    source,ctx,events=project
    await source.update_character('Lin',aliases=['Alias'])
    if collision=='shared_alias':await source.add_character(NovelCharacter(name='Other',aliases=['Alias']))
    elif collision=='canonical_alias':await source.add_character(NovelCharacter(name='Alias'))
    else:
        await source.rename_character('Lin','New')
        await source.update_character('New',aliases=['Lin'])
    name='New' if collision=='retired_alias' else 'Lin'
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));ref=await adapter.register_base('character',name)
    before=adapter.objects.read(ref.base_asset_id)
    with pytest.raises(HTTPException) as error:
        await push(ctx,kind,png('blue'),character='Lin' if collision=='retired_alias' else 'Alias')
    assert error.value.status_code==409 and not events
    assert adapter.objects.read(ref.base_asset_id)==before
    assert not list((ctx.output_dir/'assets/characters').rglob('*.png'))


@pytest.mark.parametrize('kind',['scene_master','scene_director_pano_360','scene_3gs_custom_scene'])
@pytest.mark.parametrize('collision',[None,'shared_alias','canonical_alias','retired_alias'])
async def test_scene_alias_is_unique_canonical_and_not_retired(project,kind,collision):
    source,ctx,events=project
    await source.update_scene('Hall',aliases=['Alias'])
    if collision=='shared_alias':await source.add_scene(NovelScene(name='Other',aliases=['Alias']))
    elif collision=='canonical_alias':await source.add_scene(NovelScene(name='Alias'))
    elif collision=='retired_alias':
        await source.rename_scene('Hall','New')
        await source.update_scene('New',aliases=['Hall'])
    name='New' if collision=='retired_alias' else 'Hall'
    adapter=AssetAdapter(source,ObjectStore(source.state_dir));ref=await adapter.register_base('scene',name)
    before=adapter.objects.read(ref.base_asset_id)
    content=b'model' if '3gs' in kind else png('blue')
    call=push(ctx,kind,content,scene='Hall' if collision=='retired_alias' else 'Alias')
    if collision:
        with pytest.raises(HTTPException) as error:await call
        assert error.value.status_code==409 and not events
        assert adapter.objects.read(ref.base_asset_id)==before
    else:
        result=await call
        assert 'Hall' in Path(result['data']['target_path']).parts
        assert events[-1]['payload']['target']['scene_id']=='Hall'
        current=adapter.objects.read(ref.base_asset_id)
        assert current['revision']==2
        assert all((ctx.output_dir/m['current_blob']['path']).read_bytes()==content for m in current['payload']['_media'])
    assert not (ctx.output_dir/'assets/scenes/Alias').exists()
    assert not (ctx.output_dir/'director_worlds/Alias').exists()


@pytest.mark.parametrize('kind',['portrait','scene_master'])
async def test_alias_ambiguity_added_while_waiting_is_rejected(project,monkeypatch,kind):
    from novelvideo.creation import asset_media_publication as publication
    from novelvideo.production_workflow.coordination import asset_project_lock
    source,ctx,events=project
    if kind=='portrait':await source.update_character('Lin',aliases=['Alias'])
    else:await source.update_scene('Hall',aliases=['Alias'])
    waiting=asyncio.Event()
    @asynccontextmanager
    async def observed(state):
        waiting.set()
        async with asset_project_lock(state):yield
    monkeypatch.setattr(publication,'asset_project_lock',observed)
    async with asset_project_lock(ctx.state_dir):
        task=asyncio.create_task(push(ctx,kind,png('blue'),character='Alias',scene='Alias'))
        await waiting.wait()
        if kind=='portrait':await source.add_character(NovelCharacter(name='Other',aliases=['Alias']))
        else:await source.add_scene(NovelScene(name='Other',aliases=['Alias']))
    with pytest.raises(HTTPException) as error:await task
    assert error.value.status_code==409 and 'ambiguous' in str(error.value.detail)
    assert not events and not list((ctx.output_dir/'assets').rglob('*.png'))
