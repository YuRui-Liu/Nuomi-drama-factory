from types import SimpleNamespace

import pytest

from novelvideo.creation.creative_templates import CreativeTemplates
from novelvideo.creation.personal_library import PersonalLibrary
from novelvideo.creation.ontology import Ontology
from novelvideo.creation.creative_constraints import CreativeConstraints
from novelvideo.creation.operations import OperationError
from .test_ontology import ctx, command, activate, definition
from .test_creative_constraints import create, confirm


@pytest.fixture
def service(ctx,tmp_path):
    return CreativeTemplates(ctx,PersonalLibrary(tmp_path/'personal',ctx.requester_user_id))


def selection(**changes):
    return {'name':'线索模板','description':'可复用约定','definitions':[], 'constraints':[],
            'agents':[],'examples':[],'relations':[],**changes}


async def publish(service,chosen,request_id='publish'):
    preview=service.preview(chosen)
    ref=await service.save(command('save',{'configuration':chosen,'proof':preview['proof'],'expected_revision':0},request_id))
    return ref,preview


def test_empty_preview_and_catalog_are_readonly(service,ctx):
    preview=service.preview(selection())
    assert preview['configuration']['examples']==[]
    assert service.catalog()==[] and service.library.catalog('template')==[]
    assert not ctx.state_dir.exists() and not service.library.root.exists()


async def test_template_fixed_dependencies_examples_and_draft_adoption(service,ctx):
    ontology=Ontology(ctx,authority='user');await activate(ontology)
    a=await ontology.apply(command('object.create',{'definition':'clue','schema_revision':1,'scope':{'level':'project'},'data':{'text':'first'}},'a'))
    b=await ontology.apply(command('object.create',{'definition':'clue','schema_revision':1,'scope':{'level':'project'},'data':{'text':'second'}},'b'))
    await activate(ontology,'leads',category='relation',source_kind='custom:clue',target_kind='custom:clue',fields={})
    rel=await ontology.apply(command('relation.create',{'definition':'leads','schema_revision':1,'scope':{'level':'project'},'source_id':a['object_id'],'target_id':b['object_id'],'data':{}},'relation'))
    constraints=CreativeConstraints(ctx,authority='user')
    hard=await confirm(constraints,await create(constraints))
    chosen=selection(constraints=[{'id':hard['constraint_id'],'revision':hard['revision']}],
        examples=[{'id':a['object_id'],'revision':1},{'id':b['object_id'],'revision':1}],relations=[rel['relation_id']])
    ref,preview=await publish(service,chosen)
    assert {d['definition']['name'] for d in preview['configuration']['definitions']}=={'clue','leads'}
    targetctx=SimpleNamespace(**{**vars(ctx),'project_id':'target','state_dir':ctx.state_dir.parent/'target'})
    target=CreativeTemplates(targetctx,service.library)
    plan=target.adoption_preview(ref)
    adopted=await target.apply(command('adopt',{'ref':ref,'proof':plan['proof']},'adopt'))
    content=target.adoption(adopted['adoption_id'])
    assert all(d['active_revision']==0 for d in Ontology(targetctx).catalog())
    draft=CreativeConstraints(targetctx).catalog()[0]
    assert draft['level']=='draft' and draft['suggested_level']=='hard'
    assert draft['origin']['constraint_id']==hard['constraint_id']
    examples=content['example_drafts']
    assert {e['id'] for e in examples}.isdisjoint({a['object_id'],b['object_id']})
    relation=content['relation_drafts'][0]
    assert {relation['source_id'],relation['target_id']}=={e['id'] for e in examples}
    assert relation['state']=='draft'
    assert content['fixed']['configuration']==preview['configuration']
    assert target.adoption(adopted['adoption_id'])==content
    activation=command('activate_examples',{'adoption_id':adopted['adoption_id'],'expected_revision':1,
        'definitions':list(content['definition_mapping'].values()),'basis':'明确启用所列定义并实例化所选示例',
        'proof':target.activation_preview(adopted['adoption_id'])['proof']},'activate')
    result=await target.apply(activation)
    assert await target.apply(activation)==result
    ids=result['instantiated']['objects']
    actual_relation=Ontology(targetctx).relation(next(iter(result['instantiated']['relations'].values())))
    assert {actual_relation['source_id'],actual_relation['target_id']}==set(ids.values())
    assert set(ids.values()).isdisjoint({a['object_id'],b['object_id']})
    assert CreativeConstraints(targetctx).record(draft['constraint_id'])['level']=='draft'


async def test_template_conflict_requires_explicit_remap_and_never_changes_active(service,ctx):
    source=Ontology(ctx,authority='user');await activate(source)
    ref,_=await publish(service,selection(definitions=[{'name':'clue','revision':1}]))
    targetctx=SimpleNamespace(**{**vars(ctx),'project_id':'target','state_dir':ctx.state_dir.parent/'target'})
    target=CreativeTemplates(targetctx,service.library);ontology=Ontology(targetctx,authority='user')
    await activate(ontology,fields={'number':{'type':'integer'}})
    preview=target.adoption_preview(ref)
    assert preview['conflicts']
    with pytest.raises(OperationError):
        await target.apply(command('adopt',{'ref':ref,'proof':preview['proof']},'conflict'))
    remap={'clue':'imported_clue'}
    preview=target.adoption_preview(ref,remap)
    assert preview['conflicts']==[]
    await target.apply(command('adopt',{'ref':ref,'proof':preview['proof'],'definition_names':remap},'remapped'))
    assert ontology.definition('clue')['active_revision']==1
    assert ontology.definition('imported_clue')['active_revision']==0


@pytest.mark.parametrize('path',['/private/secret.png','assets/characters/private.png','../outside.json'])
async def test_missing_or_nonportable_explicit_examples_rejected(service,ctx,path):
    with pytest.raises(OperationError):
        service.preview(selection(examples=[{'id':'missing','revision':1}]))
    ontology=Ontology(ctx,authority='user');await activate(ontology)
    item=await ontology.apply(command('object.create',{'definition':'clue','schema_revision':1,'scope':{'level':'project'},'data':{'text':path}},'path'))
    with pytest.raises(OperationError) as error:
        service.preview(selection(examples=[{'id':item['object_id'],'revision':1}]))
    assert error.value.code=='unsupported_reference'
    assert service.library.catalog('template')==[]


async def test_template_upgrade_preview_cas_preserves_project_changes(service,ctx):
    ontology=Ontology(ctx,authority='user');await activate(ontology)
    constraints=CreativeConstraints(ctx,authority='user')
    record=await create(constraints)
    chosen=selection(definitions=[{'name':'clue','revision':1}],constraints=[{'id':record['constraint_id'],'revision':1}])
    ref,_=await publish(service,chosen)
    targetctx=SimpleNamespace(**{**vars(ctx),'project_id':'target','state_dir':ctx.state_dir.parent/'target'})
    target=CreativeTemplates(targetctx,service.library)
    plan=target.adoption_preview(ref)
    adopted=await target.apply(command('adopt',{'ref':ref,'proof':plan['proof']},'adopt'))
    first=target.adoption(adopted['adoption_id']);cid=first['constraint_drafts'][0]
    local=CreativeConstraints(targetctx,authority='user')
    await confirm(local,{'constraint_id':cid,'revision':1},'hard','lock-local')
    new_selection={**chosen,'description':'新版'}
    preview=service.preview(new_selection)
    newref=await service.save(command('save',{'configuration':new_selection,'proof':preview['proof'],
        'id':ref['id'],'expected_revision':1},'new-version'))
    upgrade=target.upgrade_preview(adopted['adoption_id'],newref)
    assert upgrade['existing_constraint_drafts'][0]['level']=='hard'
    body=command('upgrade',{'adoption_id':adopted['adoption_id'],'expected_revision':1,
        'ref':newref,'proof':upgrade['proof'],'resolutions':{}},'upgrade')
    result=await target.apply(body)
    assert local.record(cid)['level']=='hard'
    assert target.adoption(adopted['adoption_id'],1)==first
    assert target.adoption(adopted['adoption_id'])['previous_installations'][0]['constraint_drafts']==[cid]
    assert await target.apply(body)==result


async def test_activation_conflict_rolls_back_all_definitions_and_instances(service,ctx):
    ontology=Ontology(ctx,authority='user');await activate(ontology)
    obj=await ontology.apply(command('object.create',{'definition':'clue','schema_revision':1,
        'scope':{'level':'project'},'data':{'text':'bad'}},'sample'))
    ref,_=await publish(service,selection(examples=[{'id':obj['object_id'],'revision':1}]))
    plan=service.adoption_preview(ref)
    adopted=await service.apply(command('adopt',{'ref':ref,'proof':plan['proof']},'adopt'))
    contents=service.adoption(adopted['adoption_id'])
    constraints=CreativeConstraints(ctx,authority='user')
    record=await constraints.apply(command('constraint.save',{'key':'clue','value':'required','level':'draft',
        'scope':{'level':'project'},'citation':'rule','rule':{'kind':'custom:clue','field':'text','operator':'equals','schema_revision':1}},'rule'))
    await confirm(constraints,record)
    with service.read() as conn:
        before=list(conn.iterdump())
    with pytest.raises(OperationError):
        await service.apply(command('activate_examples',{'adoption_id':adopted['adoption_id'],'expected_revision':1,
            'definitions':list(contents['definition_mapping'].values()),'basis':'activate',
            'proof':service.activation_preview(adopted['adoption_id'])['proof']},'activate'))
    with service.read() as conn:
        assert list(conn.iterdump())==before


async def test_template_preview_fixed_history_and_target_schema_change_rejects(service,ctx):
    ontology=Ontology(ctx,authority='user');await activate(ontology)
    selected=selection(definitions=[{'name':'clue','revision':1}])
    preview=service.preview(selected)
    await ontology.apply(command('definition.draft',{'definition':definition(fields={'text':{'type':'string'},'other':{'type':'integer'}}),'expected_revision':1},'newdef'))
    assert service.preview(selected)['proof']==preview['proof']
    ref,_=await publish(service,selected)
    targetctx=SimpleNamespace(**{**vars(ctx),'project_id':'target','state_dir':ctx.state_dir.parent/'target'})
    target=CreativeTemplates(targetctx,service.library)
    plan=target.adoption_preview(ref)
    await activate(Ontology(targetctx,authority='user'))
    with pytest.raises(OperationError) as exc:
        await target.apply(command('adopt',{'ref':ref,'proof':plan['proof']},'stale'))
    assert exc.value.code=='source_conflict'
    assert target.catalog()==[]


async def test_template_activation_requires_all_explicit_dependencies(service,ctx):
    ontology=Ontology(ctx,authority='user');await activate(ontology)
    ref,_=await publish(service,selection(definitions=[{'name':'clue','revision':1}]))
    plan=service.adoption_preview(ref)
    adopted=await service.apply(command('adopt',{'ref':ref,'proof':plan['proof']},'adopt'))
    with pytest.raises(OperationError):
        await service.apply(command('activate_examples',{'adoption_id':adopted['adoption_id'],'expected_revision':1,
            'definitions':[],'basis':'missing selection',
            'proof':service.activation_preview(adopted['adoption_id'])['proof']},'activate'))
    assert service.adoption(adopted['adoption_id'])['revision']==1


async def test_activation_preview_detects_changed_active_schema(service,ctx):
    ontology=Ontology(ctx,authority='user');await activate(ontology)
    ref,_=await publish(service,selection(definitions=[{'name':'clue','revision':1}]))
    plan=service.adoption_preview(ref)
    adopted=await service.apply(command('adopt',{'ref':ref,'proof':plan['proof']},'adopt'))
    preview=service.activation_preview(adopted['adoption_id'])
    await ontology.apply(command('definition.draft',{'definition':definition(fields={'other':{'type':'string'}}),'expected_revision':1},'v2'))
    with pytest.raises(OperationError):
        await service.apply(command('activate_examples',{'adoption_id':adopted['adoption_id'],'expected_revision':1,
            'definitions':[{'name':'clue','revision':1}],'proof':preview['proof'],'basis':'explicit'},'stale'))


async def test_real_template_http_publish_adopt_and_activate(ctx,tmp_path,monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport,AsyncClient
    from novelvideo.api.routes import creation_library as routes
    from novelvideo.api.auth import get_api_user
    import novelvideo.config as config
    monkeypatch.setattr(config,'STATE_DIR',tmp_path/'libraries')
    async def context(project,user,role):
        return ctx
    monkeypatch.setattr(routes,'context_for',context)
    await activate(Ontology(ctx,authority='user'))
    app=FastAPI();app.include_router(routes.router,prefix='/api/v1')
    app.dependency_overrides[get_api_user]=lambda:{'id':'user'}
    prefix='/api/v1/projects/p/creation/reusable/template'
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        selected=selection(definitions=[{'name':'clue','revision':1}])
        preview=(await client.post(prefix+'/preview',json={'configuration':selected})).json()['data']
        ref=(await client.post(prefix+'/personal/operations',json=command('save',{
            'configuration':selected,'proof':preview['proof'],'expected_revision':0},'publish'))).json()['data']
        plan=(await client.post(prefix+'/adoption-preview',json={'ref':ref})).json()['data']
        response=await client.post(prefix+'/operations',json=command('adopt',{'ref':ref,'proof':plan['proof']},'adopt'))
        assert response.status_code==200,response.text
        adopted=response.json()['data']
        preview=(await client.get(prefix+'/activation-preview/'+adopted['adoption_id'])).json()['data']
        body=command('activate_examples',{'adoption_id':adopted['adoption_id'],'expected_revision':1,
            'definitions':[{'name':'clue','revision':1}],'proof':preview['proof'],'basis':'explicit'},'activate')
        response=await client.post(prefix+'/operations',json=body)
        assert response.status_code==200,response.text
        assert (await client.post(prefix+'/operations',json=body)).json()==response.json()


async def test_existing_gets_leave_project_and_personal_business_bytes_unchanged(service,ctx):
    ref,_=await publish(service,selection())
    plan=service.adoption_preview(ref)
    adopted=await service.apply(command('adopt',{'ref':ref,'proof':plan['proof']},'adopt'))
    def snapshot():
        return {str(p):p.read_bytes() for root in (ctx.state_dir,service.library.root)
                for p in root.rglob('*') if p.is_file() and not p.name.endswith('-shm')}
    before=snapshot()
    service.catalog();service.library.catalog('template');service.adoption(adopted['adoption_id'])
    service.activation_preview(adopted['adoption_id']);service.receipt('adopt');service.library.receipt('publish')
    assert snapshot()==before
