from types import SimpleNamespace

import pytest

from novelvideo.creation.agent_profiles import AgentProfiles
from novelvideo.creation.personal_library import PersonalLibrary
from novelvideo.creation.operations import OperationError
from .test_ontology import ctx, command


@pytest.fixture
def service(ctx,tmp_path):
    return AgentProfiles(ctx,PersonalLibrary(tmp_path/'personal',ctx.requester_user_id))


def configuration(**changes):
    return {'name':'主创','responsibilities':'人物与情节','methods':'先核对事实',
            'references':['示例作品'],'skill_notes':['建议分支而非直接覆盖'],
            'capabilities':['context.read','proposal.create'],'defaults':{},'studio_refs':[],**changes}


async def save(service,config=None,ref=None,request_id='save'):
    config=config or configuration()
    preview=service.preview(config)
    return await service.save(command('save',{'configuration':config,'proof':preview['proof'],
        'expected_revision':ref['revision'] if ref else 0,**({'id':ref['id']} if ref else {})},request_id))


def test_empty_personal_and_project_reads_do_not_initialize(service,ctx):
    assert service.library.catalog('agent')==[]
    assert service.catalog()==[]
    service.preview(configuration())
    assert not service.library.root.exists() and not ctx.state_dir.exists()


async def test_personal_versions_copy_and_cross_user_isolation(service,tmp_path):
    first=await save(service)
    second=await save(service,configuration(methods='不同方法'),first,'save2')
    assert second['revision']==2
    assert service.library.version('agent',first)['configuration']['methods']=='先核对事实'
    copied=await service.save(command('copy',{'ref':first,'name':'副本'},'copy'))
    assert copied['id']!=first['id'] and copied['revision']==1
    assert service.library.version('agent',copied)['configuration']['name']=='副本'
    other=PersonalLibrary(tmp_path/'personal','other')
    assert other.catalog('agent')==[]
    with pytest.raises(OperationError):
        other.version('agent',first)
    assert await service.save(command('copy',{'ref':first,'name':'副本'},'copy'))==copied


async def test_legacy_profile_input_publishes_complete_professional_fields(service):
    preview=service.preview(configuration())
    fields=('required_node_operations','delivery_requirements','examples','collaboration_rules')
    assert all(preview['configuration'][field]==[] for field in fields)
    ref=await service.save(command('save',{'configuration':configuration(),'proof':preview['proof'],
        'expected_revision':0},'legacy-shaped-save'))
    published=service.library.version('agent',ref)['configuration']
    assert all(published[field]==[] for field in fields)


async def test_patching_legacy_profile_publishes_complete_professional_fields(service):
    legacy=configuration()
    ref=service.library.apply('agent',command('save',{
        'configuration':legacy,'proof':'legacy-proof','expected_revision':0,
    },'seed-legacy'),lambda config,proof:config)
    changes={'methods':'旧配置的新方法'}
    preview=service.personal_patch_preview(ref,changes)
    fields=('required_node_operations','delivery_requirements','examples','collaboration_rules')
    assert all(preview['configuration'][field]==[] for field in fields)
    newer=await service.save(command('patch',{'ref':ref,'changes':changes,'proof':preview['proof']},'patch-legacy'))
    assert all(service.library.version('agent',newer)['configuration'][field]==[] for field in fields)
    assert all(field not in service.library.version('agent',ref)['configuration'] for field in fields)


async def test_project_override_accepts_catalogued_unavailable_node_operations(service):
    ref=await save(service)
    adopted=await service.apply(command('adopt',{'ref':ref,'proof':service.library.version('agent',ref)['proof']},'adopt-ops'))
    operations=[{'node_type':'musicDeskNode','operation':'export'}]
    changes={'required_node_operations':operations}
    preview=service.override_preview(adopted['adoption_id'],changes)
    result=await service.apply(command('override',{'adoption_id':adopted['adoption_id'],
        'expected_revision':1,'changes':changes,'proof':preview['proof']},'override-ops'))
    assert result['effective']['required_node_operations']==operations
    assert service.capabilities()['execution_enabled'] is False


async def test_fixed_adoption_overlay_upgrade_conflict_and_two_projects(service,ctx):
    ref=await save(service)
    body=command('adopt',{'ref':ref,'proof':service.library.version('agent',ref)['proof']},'adopt')
    adopted=await service.apply(body)
    other_ctx=SimpleNamespace(**{**vars(ctx),'project_id':'p2','state_dir':ctx.state_dir.parent/'p2'})
    other=AgentProfiles(other_ctx,service.library)
    second_project=await other.apply(body)
    aid=adopted['adoption_id']
    changes={'methods':'项目局部方法'}
    override_preview=service.override_preview(aid,changes)
    overridden=await service.apply(command('override',{'adoption_id':aid,'expected_revision':1,
        'changes':changes,'proof':override_preview['proof']},'override'))
    newer=await save(service,configuration(methods='新版方法'),ref,'newer')
    assert service.adoption(aid)['effective']['methods']=='项目局部方法'
    assert other.adoption(second_project['adoption_id'])['effective']['methods']=='先核对事实'
    preview=service.upgrade_preview(aid,newer)
    assert preview['conflicts']==['methods']
    with pytest.raises(OperationError):
        await service.apply(command('upgrade',{'adoption_id':aid,'expected_revision':2,'ref':newer,'proof':preview['proof'],'resolutions':{}},'bad'))
    upgrade=command('upgrade',{'adoption_id':aid,'expected_revision':2,'ref':newer,'proof':preview['proof'],'resolutions':{'methods':'keep'}},'upgrade')
    final=await service.apply(upgrade)
    assert final['revision']==3
    assert service.adoption(aid)['source']['ref']==newer
    assert service.adoption(aid,1)['source']['ref']==ref
    assert service.adoption(aid)['effective']['methods']=='项目局部方法'
    assert service.adoption(aid,1)['effective']['methods']=='先核对事实'
    assert await service.apply(upgrade)==final
    assert overridden['revision']==2


async def test_project_override_resolves_full_configuration_and_rejects_stale_preview(service):
    ref=await save(service)
    adopted=await service.apply(command('adopt',{'ref':ref,'proof':service.library.version('agent',ref)['proof']},'adopt'))
    entry=service.references.catalog('style')[0]
    changes={'defaults':{'style':{'id':entry['id'],'proof':entry['proof']}},
             'references':['项目独立参考'],'skill_notes':['只提出建议'],'capabilities':['ontology.draft']}
    preview=service.override_preview(adopted['adoption_id'],changes)
    body=command('override',{'adoption_id':adopted['adoption_id'],'expected_revision':1,'changes':changes,'proof':preview['proof']},'override')
    result=await service.apply(body)
    assert result['effective']['defaults']['style']['configuration']==entry['configuration']
    assert result['effective']['references']==['项目独立参考']
    assert service.library.version('agent',ref)['configuration']['defaults']=={}
    with pytest.raises(OperationError):
        await service.apply({**body,'request_id':'stale','payload':{**body['payload'],'expected_revision':2}})
    with pytest.raises(OperationError):
        service.override_preview(adopted['adoption_id'],{'capabilities':['shell.execute']})


async def test_capabilities_and_default_snapshots_are_server_owned(service):
    with pytest.raises(OperationError):
        service.preview(configuration(capabilities=['shell.execute']))
    with pytest.raises(OperationError):
        service.preview(configuration(defaults={'style':{'configuration':{'prompt':'forged'}}}))
    entry=service.references.catalog('style')[0]
    config=configuration(defaults={'style':{'id':entry['id'],'proof':entry['proof']}})
    ref=await save(service,config)
    frozen=service.library.version('agent',ref)['configuration']['defaults']['style']
    assert frozen['configuration']==entry['configuration']
    assert 'execution_authorized' not in frozen


async def test_real_api_personal_save_fixed_adoption_and_permission(ctx,tmp_path,monkeypatch):
    from fastapi import FastAPI,HTTPException
    from httpx import ASGITransport,AsyncClient
    from novelvideo.api.routes import creation_library as routes
    from novelvideo.api.auth import get_api_user
    import novelvideo.config as config
    monkeypatch.setattr(config,'STATE_DIR',tmp_path/'libraries')
    roles=[]
    async def context(project,user,role):
        roles.append(role)
        if user['id']=='viewer' and role=='editor':
            raise HTTPException(403)
        return SimpleNamespace(**{**vars(ctx),'requester_user_id':user['id']})
    monkeypatch.setattr(routes,'context_for',context)
    app=FastAPI();app.include_router(routes.router,prefix='/api/v1')
    app.dependency_overrides[get_api_user]=lambda:{'id':'user'}
    prefix='/api/v1/projects/p/creation/reusable/agent'
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get(prefix+'/personal')).json()['data']==[]
        preview=(await client.post(prefix+'/preview',json={'configuration':configuration()})).json()['data']
        assert not (tmp_path/'libraries').exists()
        response=await client.post(prefix+'/personal/operations',json=command('save',{'configuration':configuration(),
            'proof':preview['proof'],'expected_revision':0},'save-http'))
        assert response.status_code==200,response.text
        ref=response.json()['data']
        fixed=(await client.get(prefix+f'/personal/versions/{ref["id"]}/{ref["revision"]}')).json()['data']
        adopted=await client.post(prefix+'/operations',json=command('adopt',{'ref':ref,'proof':fixed['proof']},'adopt-http'))
        assert adopted.status_code==200,adopted.text
        assert (await client.get(prefix+'/requests/adopt-http')).json()['data']==adopted.json()['data']
        app.dependency_overrides[get_api_user]=lambda:{'id':'viewer'}
        assert (await client.get(prefix+'/personal')).json()['data']==[]
        assert (await client.post(prefix+'/operations',json=command('adopt',{'ref':ref,'proof':fixed['proof']},'forbidden'))).status_code==403
    assert 'viewer' in roles and 'editor' in roles


@pytest.mark.parametrize('phase',['before_commit','after_commit'])
async def test_personal_crash_recovery_preserves_request_identity(service,monkeypatch,phase):
    import novelvideo.creation.personal_library as module
    preview=service.preview(configuration())
    body=command('save',{'configuration':configuration(),'proof':preview['proof'],'expected_revision':0},'crash')
    monkeypatch.setattr(module,'checkpoint',lambda at: (_ for _ in ()).throw(RuntimeError('crash')) if at==phase else None)
    with pytest.raises(RuntimeError):
        await service.save(body)
    monkeypatch.setattr(module,'checkpoint',lambda _:None)
    recovered=await service.save(body)
    assert recovered==await service.save(body)
    assert len(service.library.catalog('agent'))==1
    with pytest.raises(OperationError) as exc:
        await service.save({**body,'payload':{**body['payload'],'proof':'different'}})
    assert exc.value.code=='idempotency_conflict'


async def test_adoption_replay_and_fixed_reads_do_not_depend_on_personal_latest(service,monkeypatch):
    import novelvideo.creation.ontology as module
    ref=await save(service)
    body=command('adopt',{'ref':ref,'proof':service.library.version('agent',ref)['proof']},'lost')
    monkeypatch.setattr(module,'_checkpoint',lambda phase: (_ for _ in ()).throw(RuntimeError('lost')) if phase=='after_commit' else None)
    with pytest.raises(RuntimeError):
        await service.apply(body)
    monkeypatch.setattr(module,'_checkpoint',lambda _:None)
    service.library.db.unlink()
    result=await service.apply(body)
    assert result==service.receipt('lost')
    assert service.adoption(result['adoption_id'])['effective']['name']=='主创'


async def test_concurrent_personal_cas_and_project_cas(service):
    import asyncio
    ref=await save(service)
    results=await asyncio.gather(save(service,configuration(name='one'),ref,'one'),
        save(service,configuration(name='two'),ref,'two'),return_exceptions=True)
    assert sum(isinstance(r,OperationError) for r in results)==1
    adopted=await service.apply(command('adopt',{'ref':ref,'proof':service.library.version('agent',ref)['proof']},'adopt'))
    async def override(value):
        changes={'methods':value};preview=service.override_preview(adopted['adoption_id'],changes)
        return await service.apply(command('override',{'adoption_id':adopted['adoption_id'],
            'expected_revision':1,'changes':changes,'proof':preview['proof']},value))
    results=await asyncio.gather(override('one'),override('two'),return_exceptions=True)
    assert sum(isinstance(r,OperationError) for r in results)==1


async def test_symlink_and_copied_personal_identity_rejected(service,tmp_path):
    import shutil
    ref=await save(service)
    other=PersonalLibrary(service.library.root,'other')
    other.directory.mkdir()
    shutil.copyfile(service.library.db,other.db)
    with pytest.raises(OperationError):
        other.version('agent',ref)
    other.db.unlink();other.directory.rmdir()
    other.directory.symlink_to(service.library.directory,target_is_directory=True)
    with pytest.raises(OperationError):
        other.catalog('agent')


async def test_studio_fixed_safe_fields_and_stale_default_proof(service,ctx,monkeypatch):
    from novelvideo.creative_studios.store import StudioStore
    studio=StudioStore(ctx.state_dir/'creative-studios.db')
    studio.save('director','method','方法',{'method':'先表演再镜头','api_key':'NEVER_EXPORT','local_path':'/secret'},0)
    config=configuration(studio_refs=[{'kind':'director','id':'method','revision':1}])
    ref=await save(service,config)
    fixed=service.library.version('agent',ref)
    assert 'NEVER_EXPORT' not in str(fixed) and '/secret' not in str(fixed)
    assert fixed['configuration']['studio_refs'][0]['configuration']=={'method':'先表演再镜头'}
    entry=service.references.catalog('style')[0]
    config=configuration(defaults={'style':{'id':entry['id'],'proof':entry['proof']}})
    preview=service.preview(config)
    monkeypatch.setattr(service.references,'catalog',lambda _: [{**entry,'proof':'changed'}])
    with pytest.raises(OperationError) as error:
        await service.save(command('save',{'configuration':config,'proof':preview['proof'],'expected_revision':0},'stale'))
    assert error.value.code=='catalog_conflict'


async def test_cancel_waits_for_worker_before_releasing_project_lock(service,monkeypatch):
    import asyncio
    import threading
    from novelvideo.production_workflow.coordination import asset_project_lock
    entered=threading.Event();release=threading.Event();acquired=asyncio.Event()
    original=service.library.apply
    def blocked(*args):
        entered.set();assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(service.library,'apply',blocked)
    task=asyncio.create_task(save(service))
    await asyncio.to_thread(entered.wait,2)
    task.cancel()
    async def waiter():
        async with asset_project_lock(service.ctx.state_dir):
            acquired.set()
    contender=asyncio.create_task(waiter())
    await asyncio.sleep(.02)
    assert not acquired.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await contender
    assert service.library.receipt('save') is not None


async def test_edit_method_inherits_fixed_defaults_after_catalog_removal(service,monkeypatch):
    entry=service.references.catalog('style')[0]
    ref=await save(service,configuration(defaults={'style':{'id':entry['id'],'proof':entry['proof']}}))
    frozen=service.library.version('agent',ref)['configuration']['defaults']
    monkeypatch.setattr(service.references,'catalog',lambda _: [])
    changes={'methods':'仅修改方法'}
    preview=service.personal_patch_preview(ref,changes)
    newer=await service.save(command('patch',{'ref':ref,'changes':changes,'proof':preview['proof']},'patch'))
    saved=service.library.version('agent',newer)['configuration']
    assert saved['methods']=='仅修改方法' and saved['defaults']==frozen
    copied=await service.save(command('copy',{'ref':newer,'name':'固定副本'},'copy-fixed'))
    assert service.library.version('agent',copied)['configuration']['defaults']==frozen


def test_studio_symlink_cannot_read_other_project(service,ctx,tmp_path):
    from novelvideo.creative_studios.store import StudioStore
    other=tmp_path/'foreign'/'creative-studios.db'
    StudioStore(other).save('director','private','Private',{'method':'private method'},0)
    ctx.state_dir.mkdir()
    (ctx.state_dir/'creative-studios.db').symlink_to(other)
    with pytest.raises(OperationError):
        service.preview(configuration(studio_refs=[{'kind':'director','id':'private','revision':1}]))


def test_personal_root_symlink_is_rejected(service,tmp_path):
    outside=tmp_path/'outside';outside.mkdir()
    service.library.root.symlink_to(outside,target_is_directory=True)
    with pytest.raises(OperationError):
        service.library.catalog('agent')


async def test_fixed_resource_provenance_survives_other_project_copy_and_patch(service,ctx):
    from novelvideo.creative_studios.store import StudioStore
    StudioStore(ctx.state_dir/'creative-studios.db').save('director','same','方法A',{'method':'A的文本'},0)
    otherctx=SimpleNamespace(**{**vars(ctx),'project_id':'B','state_dir':ctx.state_dir.parent/'B'})
    StudioStore(otherctx.state_dir/'creative-studios.db').save('director','same','方法B',{'method':'B的文本'},0)
    config=configuration(studio_refs=[{'kind':'director','id':'same','revision':1}])
    ref=await save(service,config)
    other=AgentProfiles(otherctx,service.library)
    fixed=service.library.version('agent',ref)
    resource=fixed['configuration']['studio_refs'][0]
    assert resource['source']=={'project_id':ctx.project_id,'kind':'project_studio'}
    adopted=await other.apply(command('adopt',{'ref':ref,'proof':fixed['proof']},'adopt-from-A'))
    assert adopted['effective']['studio_refs'][0]==resource
    assert resource['configuration']['method']=='A的文本'
    patch=other.personal_patch_preview(ref,{'methods':'改方法'})
    edited=await other.save(command('patch',{'ref':ref,'changes':{'methods':'改方法'},'proof':patch['proof']},'edit-in-B'))
    copied=await other.save(command('copy',{'ref':edited,'name':'复制'},'copy-in-B'))
    assert other.library.version('agent',copied)['configuration']['studio_refs'][0]==resource


def test_default_resource_provenance_distinguishes_custom_and_global(service,monkeypatch):
    base=service.references.catalog('style')[0]
    for library_type,expected in [('custom',{'kind':'project_custom_style','project_id':service.ctx.project_id}),
                                  ('preset',{'kind':'global_catalog'})]:
        entry={**base,'metadata':{'library_type':library_type}}
        monkeypatch.setattr(service.references,'catalog',lambda _,entry=entry:[entry])
        preview=service.preview(configuration(defaults={'style':{'id':entry['id'],'proof':entry['proof']}}))
        assert preview['configuration']['defaults']['style']['source']==expected


async def test_old_snapshot_without_provenance_is_not_invented_on_read_or_patch(service,ctx):
    import json,sqlite3
    from novelvideo.creative_studios.store import StudioStore
    StudioStore(ctx.state_dir/'creative-studios.db').save('director','old','旧方法',{'method':'旧内容'},0)
    ref=await save(service,configuration(studio_refs=[{'kind':'director','id':'old','revision':1}]))
    with sqlite3.connect(service.library.db) as conn:
        content=json.loads(conn.execute('SELECT content FROM library_versions').fetchone()[0])
        content['configuration']['studio_refs'][0].pop('source')
        conn.execute('UPDATE library_versions SET content=?',(json.dumps(content),))
    before=service.library.db.read_bytes()
    old=service.library.version('agent',ref)
    assert 'source' not in old['configuration']['studio_refs'][0]
    assert service.library.db.read_bytes()==before
    preview=service.personal_patch_preview(ref,{'methods':'只改方法'})
    newer=await service.save(command('patch',{'ref':ref,'changes':{'methods':'只改方法'},'proof':preview['proof']},'patch-old'))
    assert 'source' not in service.library.version('agent',newer)['configuration']['studio_refs'][0]


def test_studio_catalog_reads_versions_without_initializing(service,ctx):
    assert service.studio_resources()==[]
    assert not ctx.state_dir.exists()
    from novelvideo.creative_studios.store import StudioStore
    path=ctx.state_dir/'creative-studios.db'
    studio=StudioStore(path)
    studio.save('director','one','方法',{'method':'文本','api_key':'hidden'},0)
    studio.save('director','one','方法新版',{'method':'新文本'},1)
    studio.save('intro','unsupported','仅素材',{'video_path':'private'},0)
    before=path.read_bytes()
    resources=service.studio_resources()
    assert len(resources)==3
    assert {r['revision'] for r in resources if r['id']=='one'}=={1,2}
    assert next(r for r in resources if r['id']=='unsupported')['portable'] is False
    assert 'hidden' not in str(resources) and 'private' not in str(resources)
    assert path.read_bytes()==before


@pytest.mark.parametrize('notes',['','   ','x'*12001,{'unsupported':'value'}])
def test_studio_catalog_portability_matches_selected_preview(service,ctx,notes):
    from novelvideo.creative_studios.store import StudioStore
    path=ctx.state_dir/'creative-studios.db'
    StudioStore(path).save('character','portable','人物资料',
                          {'description':'有效人物说明','notes':notes},0)
    before=path.read_bytes()
    resource=service.studio_resources()[0]
    assert resource['portable'] is True
    assert resource['excluded_fields']==['notes']
    preview=service.preview(configuration(studio_refs=[
        {key:resource[key] for key in ('kind','id','revision')}]))
    frozen=preview['configuration']['studio_refs'][0]
    assert frozen['configuration']=={'description':'有效人物说明'}
    assert frozen['excluded_fields']==resource['excluded_fields']
    assert path.read_bytes()==before
