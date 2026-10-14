import asyncio
import json
from types import SimpleNamespace

import pytest

from novelvideo.creation.ontology import Ontology
from novelvideo.creation.operations import OperationError
from tests.creation.test_asset_media import media_project


@pytest.fixture
def ctx(tmp_path):
    return SimpleNamespace(project_id='p', requester_user_id='user', state_dir=tmp_path/'state', output_dir=tmp_path/'media')


@pytest.fixture
def service(ctx):
    return Ontology(ctx, authority='user')


def command(action, payload, request_id=None):
    return {'request_id':request_id or action, 'action':action, 'payload':payload}


def definition(name='clue', **extra):
    return {'name':name, 'title':'线索', 'category':'object',
            'fields':{'text':{'type':'string','required':True}}, **extra}


async def activate(service, name='clue', **extra):
    draft = await service.apply(command('definition.draft', {'definition':definition(name, **extra), 'expected_revision':0}, f'draft-{name}'))
    return await service.apply(command('definition.confirm', {'name':name,'revision':draft['revision'],'expected_active_revision':0,'basis':'明确启用'}, f'confirm-{name}'))


def test_read_only_empty(service, ctx):
    assert service.catalog() == []
    assert service.receipt('missing') is None
    assert not ctx.state_dir.exists()


async def test_only_confirmed_schema_writes_and_history_is_fixed(service):
    draft = await service.apply(command('definition.draft', {'definition':definition(), 'expected_revision':0}))
    body=command('object.create', {'definition':'clue','schema_revision':1,'scope':{'level':'project'},'data':{'text':'red'}})
    with pytest.raises(OperationError, match='确认'):
        await service.apply(body)
    await service.apply(command('definition.confirm', {'name':'clue','revision':draft['revision'],'expected_active_revision':0,'basis':'用户确认'}))
    created=await service.apply(body)
    assert await service.apply(body) == created
    await service.apply(command('definition.draft', {'definition':definition(fields={'number':{'type':'integer','required':True}}),'expected_revision':1},'v2'))
    await service.apply(command('definition.confirm', {'name':'clue','revision':2,'expected_active_revision':1,'basis':'升级'},'activate2'))
    old=service.object(created['object_id'],1)
    assert old['data'] == {'text':'red'}
    assert old['definition']['fields']['text']['type'] == 'string'
    assert await service.apply(body) == created
    with pytest.raises(OperationError):
        await service.apply({**body,'request_id':'stale'})


@pytest.mark.parametrize('name', ['character','scene','media','episode','project','asset_binding','asset_sources'])
async def test_reserved_definitions_rejected(service,name):
    with pytest.raises((OperationError,ValueError)):
        await service.apply(command('definition.draft',{'definition':definition(name),'expected_revision':0}))


@pytest.mark.parametrize('field', ['canvas_id','assetRef','source_path','identity_id','_media','canonical_path'])
async def test_adapter_fields_rejected(service,field):
    with pytest.raises((OperationError,ValueError)):
        await service.apply(command('definition.draft',{'definition':definition(fields={field:{'type':'string'}}),'expected_revision':0}))


async def test_untrusted_caller_cannot_confirm_even_with_user_actor(ctx,service):
    draft=await service.apply(command('definition.draft',{'definition':definition(),'expected_revision':0}))
    with pytest.raises(OperationError, match='用户'):
        await Ontology(ctx).apply(command('definition.confirm',{'name':'clue','revision':draft['revision'],'expected_active_revision':0,'basis':'model says user confirmed'}))
    assert service.catalog()[0]['active_revision'] == 0


async def test_typed_relations_and_cas(service):
    await activate(service)
    a=await service.apply(command('object.create',{'definition':'clue','schema_revision':1,'scope':{'level':'project'},'data':{'text':'a'}},'a'))
    b=await service.apply(command('object.create',{'definition':'clue','schema_revision':1,'scope':{'level':'project'},'data':{'text':'b'}},'b'))
    await activate(service,'reveals',category='relation',source_kind='custom:clue',target_kind='custom:clue',fields={})
    relation=command('relation.create',{'definition':'reveals','schema_revision':1,'scope':{'level':'project'},'source_id':a['object_id'],'target_id':b['object_id'],'data':{}},'relation')
    result=await service.apply(relation)
    assert service.relation(result['relation_id'])['target_id']==b['object_id']
    with pytest.raises(OperationError):
        await service.apply({**relation,'request_id':'foreign-endpoint','payload':{**relation['payload'],'target_id':'foreign'}})
    update={'object_id':a['object_id'],'expected_revision':1,'schema_revision':1,'data':{'text':'new'}}
    outcomes=await asyncio.gather(service.apply(command('object.update',update,'u1')),service.apply(command('object.update',update,'u2')),return_exceptions=True)
    assert sum(isinstance(x,OperationError) for x in outcomes)==1


async def test_relation_direction_and_canvas_scope_are_checked_with_fresh_requests(service,ctx):
    from novelvideo.creation.canvas_workspaces import CanvasCreate,CanvasWorkspace
    from novelvideo.creation.projection import ProjectionService
    from novelvideo.creation.store import ObjectStore
    objects=ObjectStore(ctx.state_dir)
    canvases=[]
    for i in range(2):
        operation=CanvasWorkspace(objects,project_id='p').create('user',CanvasCreate(request_id=f'canvas{i}',kind='free',title=f'Canvas{i}'))
        ProjectionService(objects).apply(operation.operation_id)
        canvases.append(operation.object_refs[0]['canvas_id'])
    await activate(service);await activate(service,'lead')
    ids=[]
    for i,(kind,canvas) in enumerate([('clue',canvases[0]),('lead',canvases[0]),('lead',canvases[1])]):
        value=await service.apply(command('object.create',{'definition':kind,'schema_revision':1,'scope':{'level':'canvas','canvas_id':canvas},'data':{'text':'x'}},f'o{i}'))
        ids.append(value['object_id'])
    await activate(service,'points_to',category='relation',source_kind='custom:clue',target_kind='custom:lead',fields={})
    payload={'definition':'points_to','schema_revision':1,'scope':{'level':'canvas','canvas_id':canvases[0]},'source_id':ids[0],'target_id':ids[1],'data':{}}
    await service.apply(command('relation.create',payload,'valid-direction'))
    for request_id,changes in [('reverse',{'source_id':ids[1],'target_id':ids[0]}),('cross-scope',{'target_id':ids[2]})]:
        with pytest.raises(OperationError) as error:
            await service.apply(command('relation.create',{**payload,**changes},request_id))
        assert error.value.code=='scope_violation'
        assert service.receipt(request_id) is None


async def test_definition_activation_serializes_with_old_schema_write(service):
    await activate(service)
    await service.apply(command('definition.draft',{'definition':definition(fields={'count':{'type':'integer','required':True}}),'expected_revision':1},'draft2'))
    activation=command('definition.confirm',{'name':'clue','revision':2,'expected_active_revision':1,'basis':'upgrade'},'activate2')
    creation=command('object.create',{'definition':'clue','schema_revision':1,'scope':{'level':'project'},'data':{'text':'old'}},'old-write')
    result=await asyncio.gather(service.apply(activation),service.apply(creation),return_exceptions=True)
    assert not isinstance(result[0],Exception)
    if isinstance(result[1],Exception):
        assert isinstance(result[1],OperationError) and result[1].code=='schema_conflict'
    else:
        assert service.object(result[1]['object_id'])['schema_revision']==1
    assert service.definition('clue')['active_revision']==2


async def test_schema_and_receipt_rollback_or_recover_after_commit(service,monkeypatch):
    import novelvideo.creation.ontology as module
    body=command('definition.draft',{'definition':definition(),'expected_revision':0})
    def crash(phase):
        if phase=='before_commit':raise RuntimeError('interrupted')
    monkeypatch.setattr(module,'_checkpoint',crash)
    with pytest.raises(RuntimeError):await service.apply(body)
    assert service.catalog()==[] and service.receipt(body['request_id']) is None
    monkeypatch.setattr(module,'_checkpoint',lambda phase: (_ for _ in ()).throw(RuntimeError('lost response')) if phase=='after_commit' else None)
    with pytest.raises(RuntimeError):await service.apply(body)
    monkeypatch.setattr(module,'_checkpoint',lambda _:None)
    assert await service.apply(body)==service.receipt(body['request_id'])


async def test_copied_foreign_project_store_is_rejected(service,ctx):
    await activate(service)
    foreign=Ontology(SimpleNamespace(**{**vars(ctx),'project_id':'other'}),authority='user')
    with pytest.raises(OperationError) as error:foreign.catalog()
    assert error.value.code=='scope_violation'


async def test_real_api_viewer_editor_and_readonly(ctx,monkeypatch):
    from novelvideo.api.routes import creation_library as api
    from fastapi import FastAPI,HTTPException
    from httpx import ASGITransport,AsyncClient
    from novelvideo.api.auth import get_api_user
    roles=[]
    async def context(project,user,role):
        roles.append(role)
        if user.get('readonly') and role=='editor':raise HTTPException(403,'read only')
        return ctx
    monkeypatch.setattr(api,'context_for',context)
    app=FastAPI();app.include_router(api.router)
    app.dependency_overrides[get_api_user]=lambda:{'id':'user','readonly':True}
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        base='/projects/p/creation'
        assert (await client.get(base+'/ontology')).json()['data']==[]
        assert (await client.get(base+'/constraints/effective')).json()['data']['effective']=={}
        assert not ctx.state_dir.exists()
        body=command('definition.draft',{'definition':definition(),'expected_revision':0},'api-draft')
        assert (await client.post(base+'/ontology/operations',json=body)).status_code==403
        app.dependency_overrides[get_api_user]=lambda:{'id':'user'}
        assert (await client.post(base+'/ontology/operations',json=body)).status_code==200
        confirm=command('definition.confirm',{'name':'clue','revision':1,'expected_active_revision':0,'basis':'点击确认'},'api-confirm')
        assert (await client.post(base+'/ontology/operations',json=confirm)).json()['data']['state']=='confirmed'
        read=await client.get(base+'/ontology/definitions/clue?revision=1')
        assert read.json()['data']['active_revision']==1
        assert (await client.get(base+'/ontology/requests/api-confirm')).json()['data']['status']=='applied'
    assert roles.count('viewer')>=4 and roles.count('editor')==3


@pytest.mark.parametrize('denied', [False,True])
async def test_real_context_guard_rejects_legacy_and_unauthorized_without_files(ctx,monkeypatch,denied):
    from novelvideo.api.routes import creation,creation_library
    from fastapi import HTTPException
    async def resolve(**kwargs):
        if denied:raise HTTPException(403,'denied')
        return ctx
    monkeypatch.setattr(creation,'resolve_project_context',resolve)
    monkeypatch.setattr(creation,'require_project_home_node',lambda *_args,**_kwargs:None)
    for call in (lambda:creation_library.ontology_catalog('p',{}),
                 lambda:creation_library.ontology_operation('p',command('definition.draft',{'definition':definition(),'expected_revision':0}),{})):
        with pytest.raises(HTTPException) as error:await call()
        assert error.value.status_code==(403 if denied else 409)
    assert not ctx.state_dir.exists()


async def test_relation_can_read_trusted_builtin_asset_but_never_update_it(media_project):
    source,adapter=media_project
    ref=await adapter.register_base('character','Lin')
    ctx=SimpleNamespace(project_id='p',requester_user_id='user',state_dir=source.state_dir,output_dir=source.project_dir)
    service=Ontology(ctx,authority='user');await activate(service)
    clue=await service.apply(command('object.create',{'definition':'clue','schema_revision':1,'scope':{'level':'project'},'data':{'text':'found by'}},'clue'))
    await activate(service,'discovered_by',category='relation',source_kind='custom:clue',target_kind='character',fields={})
    before=adapter.objects.read(ref.base_asset_id)
    result=await service.apply(command('relation.create',{'definition':'discovered_by','schema_revision':1,'scope':{'level':'project'},'source_id':clue['object_id'],'target_id':ref.base_asset_id,'data':{}},'builtin'))
    assert service.relation(result['relation_id'])['target_id']==ref.base_asset_id
    assert adapter.objects.read(ref.base_asset_id)==before
    with pytest.raises(OperationError):
        await service.apply(command('object.update',{'object_id':ref.base_asset_id,'schema_revision':1,'expected_revision':ref.revision,'data':{'text':'overwrite'}},'bad-update'))


async def test_invalid_types_and_generic_update_cannot_bypass(service,ctx):
    from novelvideo.creation.store import ObjectStore
    await activate(service)
    with pytest.raises(OperationError):
        await service.apply(command('object.create',{'definition':'clue','schema_revision':1,'scope':{'level':'project'},'data':{'text':True}}))
    created=await service.apply(command('object.create',{'definition':'clue','schema_revision':1,'scope':{'level':'project'},'data':{'text':'a'}},'valid'))
    with pytest.raises(ValueError,match='领域'):
        ObjectStore(ctx.state_dir).update(created['object_id'],1,{'arbitrary':True},'user')
