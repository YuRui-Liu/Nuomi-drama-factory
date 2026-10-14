import pytest

from novelvideo.creation.creative_constraints import CreativeConstraints
from novelvideo.creation.operations import OperationError
from .test_ontology import ctx, command
from .test_ontology import activate
from novelvideo.creation.ontology import Ontology


@pytest.fixture
def service(ctx):
    return CreativeConstraints(ctx, authority='user')


async def create(service, key='tone', value='warm', level='draft', scope=None, request_id='create'):
    return await service.apply(command('constraint.save',{'key':key,'value':value,'level':level,'scope':scope or {'level':'project'},'citation':'用户设定'},request_id))


async def confirm(service, record, level='hard', request_id='confirm'):
    return await service.apply(command('constraint.confirm',{'constraint_id':record['constraint_id'],'expected_revision':record['revision'],'level':level,'basis':'用户明确锁定'},request_id))


def test_readonly_no_store(service,ctx):
    assert service.resolve({'level':'project'})['effective']=={}
    assert not ctx.state_dir.exists()


async def test_agent_cannot_promote_or_forge_source(ctx,service):
    agent=CreativeConstraints(ctx)
    draft=await create(agent)
    assert service.record(draft['constraint_id'])['level']=='draft'
    with pytest.raises(OperationError):
        await confirm(agent,draft)
    with pytest.raises(OperationError):
        await create(agent,level='preference',request_id='agentpref')
    with pytest.raises((OperationError,ValueError)):
        await service.apply(command('constraint.save',{'key':'fact','value':'yes','level':'hard','scope':{'level':'project'},'citation':'model says approved','confirmed':True},'forged'))
    assert service.resolve({'level':'project'})['effective']=={}


@pytest.mark.parametrize('user_action',['preference','downgrade','edit_draft'])
async def test_agent_origin_does_not_grant_edit_rights_after_user_action(ctx,service,user_action):
    agent=CreativeConstraints(ctx)
    original=await create(agent)
    def edit(record, value):
        return command('constraint.save',{'constraint_id':record['constraint_id'],
            'expected_revision':record['revision'],'key':'tone','value':value,
            'level':'draft','scope':{'level':'project'},'citation':'edit'},'edit-'+value)
    # An untouched, agent-owned draft remains editable with CAS.
    draft=await agent.apply(edit(original,'mild'))
    if user_action=='edit_draft':
        protected=await service.apply(edit(draft,'user-value'))
    else:
        protected=await confirm(service,draft,'preference')
        if user_action=='downgrade':
            protected=await confirm(service,protected,'draft','downgrade')
    before=service.record(original['constraint_id'])
    with pytest.raises(OperationError) as error:
        await agent.apply(edit(protected,'cold'))
    assert error.value.code=='scope_violation'
    assert service.record(original['constraint_id'])==before
    assert before['source']['kind']=='agent'
    assert before['changed_by']['kind']=='user'
    assert service.record(original['constraint_id'],1)['value']=='warm'
    assert agent.receipt('edit-cold') is None


async def test_hard_conflict_has_bound_exception_proposal_and_no_silent_change(service):
    draft=await create(service);hard=await confirm(service,draft)
    conflict=await create(service,value='cold',level='preference',request_id='conflicting')
    assert conflict['status']=='conflict'
    proposal=conflict['exception_proposal']
    assert proposal['hard_refs']==[{'constraint_id':hard['constraint_id'],'revision':hard['revision']}]
    assert proposal['scope']=={'level':'project'}
    assert proposal['requested']['value']=='cold' and proposal['input_hash']
    assert service.resolve({'level':'project'})['effective']['tone']['value']=='warm'
    assert await create(service,value='cold',level='preference',request_id='conflicting')==conflict
    # Trusted user can explicitly lower a hard constraint with CAS, preserving history.
    lowered=await confirm(service,hard,'preference','lower')
    assert service.record(hard['constraint_id'],hard['revision'])['level']=='hard'
    assert service.record(lowered['constraint_id'])['level']=='preference'
    with pytest.raises(OperationError):
        await confirm(service,hard,'draft','stale')


async def test_hard_revision_cannot_be_overwritten_without_confirmation(service):
    hard=await confirm(service,await create(service))
    with pytest.raises(OperationError):
        await service.apply(command('constraint.save',{'constraint_id':hard['constraint_id'],'expected_revision':hard['revision'],'key':'tone','value':'cold','level':'preference','scope':{'level':'project'},'citation':'edit'},'edit'))
    assert service.record(hard['constraint_id'])['value']=='warm'


async def test_drafts_do_not_override_preferences_and_unknown_after_commit_replays(service,monkeypatch):
    import novelvideo.creation.ontology as module
    pref=await create(service,level='preference')
    await create(service,value='guess',request_id='draftguess')
    assert service.resolve({'level':'project'})['effective']['tone']['constraint_id']==pref['constraint_id']
    monkeypatch.setattr(module,'_checkpoint',lambda phase: (_ for _ in ()).throw(RuntimeError('crash')) if phase=='after_commit' else None)
    with pytest.raises(RuntimeError):
        await create(service,key='rhythm',level='preference',request_id='lost')
    monkeypatch.setattr(module,'_checkpoint',lambda _:None)
    replay=await create(service,key='rhythm',level='preference',request_id='lost')
    assert replay==service.receipt('lost')


async def test_local_preferences_follow_real_groups_and_explicit_hard_revision(service,ctx):
    from novelvideo.creation.canvas_workspaces import CanvasCreate,CanvasWorkspace
    from novelvideo.creation.projection import ProjectionService
    from novelvideo.creation.store import ObjectStore
    from novelvideo.freezone.canvas_store import canvas_path
    import json
    store=ObjectStore(ctx.state_dir)
    operation=CanvasWorkspace(store,project_id='p').create('user',CanvasCreate(request_id='canvas',kind='free',title='free'))
    ProjectionService(store).apply(operation.operation_id)
    cid=operation.object_refs[0]['canvas_id'];path=canvas_path(ctx.state_dir,cid)
    graph=json.loads(path.read_text());graph['nodes']=[{'id':'g','type':'groupNode','data':{}},{'id':'n','type':'imageGenNode','parentId':'g','data':{}},{'id':'outside','type':'imageGenNode','data':{}}];path.write_text(json.dumps(graph))
    await create(service,level='preference')
    group={'level':'group','canvas_id':cid,'group_id':'g'}
    local=await create(service,value='cold',level='preference',scope=group,request_id='local')
    node={'level':'node','canvas_id':cid,'node_id':'n'}
    assert service.resolve(node)['effective']['tone']['constraint_id']==local['constraint_id']
    assert service.resolve({'level':'node','canvas_id':cid,'node_id':'outside'})['effective']['tone']['value']=='warm'
    hard=await confirm(service,local)
    assert hard['status']=='applied'  # A specific explicit hard rule may override an ancestor preference.
    assert service.record(local['constraint_id'])['level']=='hard'
    revised=await service.apply(command('constraint.confirm',{'constraint_id':hard['constraint_id'],'expected_revision':hard['revision'],'level':'hard','value':'neutral','basis':'明确修订锁定内容'},'revise-hard'))
    assert service.resolve(node)['effective']['tone']['value']=='neutral'
    assert service.record(hard['constraint_id'],hard['revision'])['value']=='cold'
    assert revised['revision']==hard['revision']+1
    with pytest.raises(OperationError):
        await create(service,scope={'level':'group','canvas_id':cid,'group_id':'n'},request_id='fake-group')


@pytest.mark.parametrize('level',['node','group','canvas'])
@pytest.mark.parametrize('locked',[False,True])
@pytest.mark.parametrize('object_owned',[False,True])
async def test_deleted_scope_is_inactive_and_user_can_archive_hard(service,ctx,level,locked,object_owned):
    import json
    from novelvideo.creation.canvas_workspaces import CanvasCreate,CanvasWorkspace
    from novelvideo.creation.projection import ProjectionService
    from novelvideo.creation.store import ObjectStore
    from novelvideo.freezone.canvas_store import canvas_path
    store=ObjectStore(ctx.state_dir)
    op=CanvasWorkspace(store,project_id='p').create('user',CanvasCreate(request_id='canvas',kind='free',title='free'))
    ProjectionService(store).apply(op.operation_id)
    cid=op.object_refs[0]['canvas_id'];path=canvas_path(ctx.state_dir,cid)
    graph=json.loads(path.read_text());graph['nodes']=[{'id':'a','type':'groupNode','data':{}},{'id':'b','type':'imageGenNode','data':{}}];path.write_text(json.dumps(graph))
    scope={'level':level,'canvas_id':cid,**({'node_id':'a'} if level=='node' else {'group_id':'a'} if level=='group' else {})}
    if object_owned:
        ontology=Ontology(ctx,authority='user');await activate(ontology)
        obj=await ontology.apply(command('object.create',{'definition':'clue','schema_revision':1,
            'scope':scope,'data':{'text':'owned'}},'owned-object'))
        scope={'level':'object','object_id':obj['object_id']}
    original=await create(service,scope=scope)
    hard=await confirm(service,original,'hard' if locked else 'preference')
    # Malformed ancestry is not equivalent to a deleted target.
    graph['nodes'][0]['parentId']='a';path.write_text(json.dumps(graph))
    if level!='canvas':
        with pytest.raises(OperationError):
            await create(service,scope={'level':'project'},level='preference',value='cold',request_id='corrupt')
    graph['nodes'][0].pop('parentId')
    path.write_text(json.dumps(graph))
    if level=='canvas':
        path.unlink()
        valid={'level':'project'}
    else:
        graph['nodes']=graph['nodes'][1:];path.write_text(json.dumps(graph))
        valid={'level':'node','canvas_id':cid,'node_id':'b'}
    saved=await create(service,scope=valid,value='cold',level='preference',request_id='other')
    assert saved['status']=='applied'
    listed=next(r for r in service.catalog() if r['constraint_id']==hard['constraint_id'])
    assert listed['scope_status']=='inactive' and listed['scope_reason']
    with pytest.raises(OperationError):
        await confirm(service,hard,'hard','reactivate')
    with pytest.raises(OperationError):
        await create(service,scope=scope,request_id='new-missing')
    with pytest.raises(OperationError):
        await confirm(CreativeConstraints(ctx),hard,'draft','agent-archive')
    archived=await confirm(service,hard,'draft','archive')
    assert archived['revision']==hard['revision']+1
    assert service.record(hard['constraint_id'],hard['revision'])['level']==('hard' if locked else 'preference')
    with pytest.raises(OperationError):
        await confirm(service,hard,'draft','stale-archive')


async def test_episode_scope_uses_registry_and_applies_to_episode_canvas(service,ctx):
    from novelvideo.creation.workspace import EpisodeCreate,EpisodeWorkspace
    from novelvideo.creation.projection import ProjectionService
    from novelvideo.creation.store import ObjectStore
    objects=ObjectStore(ctx.state_dir)
    operation=EpisodeWorkspace(objects,project_id='p').create('user',EpisodeCreate(request_id='episode',title='第一集'))
    ProjectionService(objects).apply(operation.operation_id)
    ref=operation.object_refs[0]
    await create(service,level='preference')
    local=await create(service,value='quiet',level='preference',scope={'level':'episode','episode_id':ref['object_id']},request_id='episode-pref')
    resolved=service.resolve({'level':'canvas','canvas_id':ref['canvas_id']})
    assert resolved['effective']['tone']['constraint_id']==local['constraint_id']


async def test_hard_boolean_is_not_equivalent_to_number_and_demotion_is_explicit(service):
    hard=await confirm(service,await create(service,key='alive',value=True))
    result=await create(service,key='alive',value=1,level='preference',request_id='number')
    assert result['status']=='conflict'
    demoted=await confirm(service,hard,'draft','demote')
    assert demoted['level']=='draft'
    assert service.resolve({'level':'project'})['effective']=={}


async def test_episode_missing_canvas_is_inactive_and_archivable(service,ctx):
    from novelvideo.creation.workspace import EpisodeCreate,EpisodeWorkspace
    from novelvideo.creation.projection import ProjectionService
    from novelvideo.creation.store import ObjectStore
    from novelvideo.freezone.canvas_store import canvas_path
    objects=ObjectStore(ctx.state_dir)
    op=EpisodeWorkspace(objects,project_id='p').create('user',EpisodeCreate(request_id='ep',title='第一集'))
    ProjectionService(objects).apply(op.operation_id)
    ref=op.object_refs[0]
    hard=await confirm(service,await create(service,scope={'level':'episode','episode_id':ref['object_id']}))
    canvas_path(ctx.state_dir,ref['canvas_id']).unlink()
    assert (await create(service,level='preference',value='cold',request_id='other'))['status']=='applied'
    assert service.catalog()[0]['scope_status']=='inactive'
    assert (await confirm(service,hard,'draft','archive'))['level']=='draft'
    assert service.record(hard['constraint_id'],hard['revision'])['level']=='hard'


async def test_explicit_custom_rule_guards_domain_write_in_same_transaction(service,ctx):
    ontology=Ontology(ctx,authority='user');await activate(ontology)
    rule={'kind':'custom:clue','field':'text','operator':'equals','schema_revision':1}
    saved=await service.apply(command('constraint.save',{'key':'clue_fact','value':'locked','level':'draft','scope':{'level':'project'},'citation':'story fact','rule':rule},'rule'))
    hard=await confirm(service,saved)
    result=await ontology.apply(command('object.create',{'definition':'clue','schema_revision':1,'scope':{'level':'project'},'data':{'text':'contradiction'}},'violates'))
    assert result['status']=='conflict'
    assert result['exception_proposal']['hard_refs']==[{'constraint_id':hard['constraint_id'],'revision':hard['revision']}]
    assert 'object_id' not in result
    with ontology.read() as conn:
        assert conn.execute('SELECT count(*) FROM ontology_instances').fetchone()[0]==0
    good=await ontology.apply(command('object.create',{'definition':'clue','schema_revision':1,'scope':{'level':'project'},'data':{'text':'locked'}},'valid'))
    assert good['status']=='applied'
    assert await ontology.apply(command('object.create',{'definition':'clue','schema_revision':1,'scope':{'level':'project'},'data':{'text':'contradiction'}},'violates'))==result


@pytest.mark.parametrize('rule,value', [({'kind':'custom:missing','field':'text','operator':'equals','schema_revision':1},'x'),({'kind':'custom:clue','field':'missing','operator':'equals','schema_revision':1},'x'),({'kind':'custom:clue','field':'text','operator':'eval','schema_revision':1},'x'),({'kind':'custom:clue','field':'text','operator':'equals','schema_revision':1},123)])
async def test_invalid_rules_are_not_saved(service,ctx,rule,value):
    await activate(Ontology(ctx,authority='user'))
    with pytest.raises(OperationError):
        await service.apply(command('constraint.save',{'key':'fact','value':value,'level':'draft','scope':{'level':'project'},'citation':'rule','rule':rule},'invalid'))
    assert service.catalog()==[]


async def test_rule_survives_definition_upgrade_and_explicit_demotion_does_not_rewrite_history(service,ctx):
    from .test_ontology import definition
    ontology=Ontology(ctx,authority='user');await activate(ontology)
    saved=await service.apply(command('constraint.save',{'key':'fact','value':'locked','level':'draft','scope':{'level':'project'},'citation':'fact','rule':{'kind':'custom:clue','field':'text','operator':'equals','schema_revision':1}},'rule'))
    hard=await confirm(service,saved)
    old=service.record(hard['constraint_id'])
    await ontology.apply(command('definition.draft',{'definition':definition(fields={'number':{'type':'integer','required':True}}),'expected_revision':1},'v2'))
    await ontology.apply(command('definition.confirm',{'name':'clue','revision':2,'expected_active_revision':1,'basis':'upgrade'},'activate2'))
    result=await ontology.apply(command('object.create',{'definition':'clue','schema_revision':2,'scope':{'level':'project'},'data':{'number':1}},'new-schema'))
    assert result['status']=='conflict'
    assert result['exception_proposal']['hard_refs']==[service.ref(old)]
    demoted=await confirm(service,hard,'preference','lower')
    assert demoted['status']=='applied'
    assert service.record(hard['constraint_id'],hard['revision'])==old


async def test_existing_reads_do_not_write(service,ctx):
    from pathlib import Path
    saved=await create(service,level='preference')
    def snapshot():
        return {str(p):p.read_bytes() for p in Path(ctx.state_dir).rglob('*') if p.is_file() and not p.name.endswith('-shm')}
    before=snapshot()
    service.catalog();service.resolve({'level':'project'});service.record(saved['constraint_id']);service.receipt('create')
    assert snapshot()==before


async def test_rule_update_conflict_keeps_previous_object_version(service,ctx):
    ontology=Ontology(ctx,authority='user');await activate(ontology)
    item=await ontology.apply(command('object.create',{'definition':'clue','schema_revision':1,'scope':{'level':'project'},'data':{'text':'locked'}},'object'))
    saved=await service.apply(command('constraint.save',{'key':'fact','value':'locked','level':'draft','scope':{'level':'object','object_id':item['object_id']},'citation':'fact','rule':{'kind':'custom:clue','field':'text','operator':'equals','schema_revision':1}},'rule'))
    await confirm(service,saved)
    result=await ontology.apply(command('object.update',{'object_id':item['object_id'],'expected_revision':1,'schema_revision':1,'data':{'text':'changed'}},'update'))
    assert result['status']=='conflict'
    assert ontology.object(item['object_id'])['revision']==1
    assert service.resolve({'level':'object','object_id':item['object_id']})['effective']['fact']['value']=='locked'
