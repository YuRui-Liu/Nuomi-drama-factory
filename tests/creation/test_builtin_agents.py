from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from novelvideo.creation import builtin_agents
from novelvideo.creation.agent_profiles import AgentProfiles
from novelvideo.creation.personal_library import PersonalLibrary
from novelvideo.creation.operations import OperationError
from .test_ontology import ctx, command
from .test_agent_profiles import configuration, save


@pytest.fixture
def service(ctx, tmp_path):
    return AgentProfiles(ctx, PersonalLibrary(tmp_path/'personal', ctx.requester_user_id))


def first(service):
    entry = service.builtin_catalog()[0]
    return {'id':entry['id'],'revision':entry['revision']}, entry


async def adopt(service, request_id='adopt'):
    ref, entry = first(service)
    body = command('adopt', {'source':'builtin','ref':ref,'proof':entry['proof']}, request_id)
    return await service.apply(body), body


def add_version(monkeypatch):
    newer = deepcopy(builtin_agents.BUILTIN_AGENTS[0])
    newer['revision'] += 1
    newer['configuration']['methods'] = '新版：根据用户目标比较两个可行方案'
    monkeypatch.setattr(builtin_agents, 'BUILTIN_AGENTS', (*builtin_agents.BUILTIN_AGENTS, newer))
    return {'id':newer['id'],'revision':newer['revision']}


def test_catalog_and_preview_do_not_initialize_personal_or_project(service, ctx):
    entries = service.builtin_catalog()
    assert len(entries) == 2
    assert len({entry['id'] for entry in entries}) == 2
    for entry in entries:
        assert entry['id'].startswith('builtin:')
        assert entry['source']['kind'] == 'builtin'
        assert entry['configuration']['defaults'] == {}
        assert entry['configuration']['studio_refs'] == []
        assert set(entry['configuration']['capabilities']) <= {c['id'] for c in service.capabilities()['items']}
        assert service.adoption_preview({'id':entry['id'],'revision':entry['revision']}, source='builtin') == entry
    assert not ctx.state_dir.exists() and not service.library.root.exists()


async def test_explicit_adoption_freezes_builtin_source_without_personal_seed(service, monkeypatch):
    result, body = await adopt(service)
    original = service.adoption(result['adoption_id'])
    assert original['source']['kind'] == 'builtin'
    assert original['source']['ref'] == body['payload']['ref']
    assert original['source']['content_hash'] == original['fixed']['content_hash']
    assert 'user_id' not in original['source']
    assert original['effective']['name'] == original['fixed']['configuration']['name']
    monkeypatch.setattr(builtin_agents, 'BUILTIN_AGENTS', ())
    assert await service.apply(body) == result
    assert service.adoption(result['adoption_id']) == original
    assert not service.library.root.exists()


async def test_stale_builtin_selection_cannot_copy_changed_catalog(service, monkeypatch):
    ref, entry = first(service)
    changed = deepcopy(builtin_agents.BUILTIN_AGENTS[0])
    changed['configuration']['methods'] = '目录内容已变化'
    monkeypatch.setattr(builtin_agents, 'BUILTIN_AGENTS', (changed, *builtin_agents.BUILTIN_AGENTS[1:]))
    with pytest.raises(OperationError) as error:
        await service.apply(command('adopt', {'source':'builtin','ref':ref,'proof':entry['proof']}, 'stale'))
    assert error.value.code == 'source_conflict'
    assert service.catalog() == [] and service.receipt('stale') is None


@pytest.mark.parametrize('resolution', ['keep', 'drop'])
async def test_builtin_upgrade_cas_conflicts_and_fixed_history(service, monkeypatch, resolution):
    adopted, _ = await adopt(service)
    identifier = adopted['adoption_id']
    changes = {'methods':'本项目的方法'}
    preview = service.override_preview(identifier, changes)
    await service.apply(command('override', {'adoption_id':identifier,'expected_revision':1,
        'changes':changes,'proof':preview['proof']}, 'override'))
    old = service.adoption(identifier)
    ref = add_version(monkeypatch)
    assert service.adoption(identifier) == old
    preview = service.upgrade_preview(identifier, ref, source='builtin')
    assert preview['conflicts'] == ['methods']
    payload = {'source':'builtin','adoption_id':identifier,'expected_revision':2,
               'ref':ref,'proof':preview['proof'],'resolutions':{'methods':resolution}}
    with pytest.raises(OperationError):
        await service.apply(command('upgrade', {**payload,'resolutions':{}}, 'missing-choice'))
    result = await service.apply(command('upgrade', payload, 'upgrade'))
    current = service.adoption(identifier)
    assert result['revision'] == 3
    assert current['source']['ref'] == ref and current['fixed']['revision'] == 2
    assert current['effective']['methods'] == ('本项目的方法' if resolution == 'keep' else preview['after']['methods'])
    assert service.adoption(identifier, 2) == old
    assert service.adoption(identifier, 1)['fixed']['revision'] == 1
    assert await service.apply(command('upgrade', payload, 'upgrade')) == result
    with pytest.raises(OperationError):
        await service.apply(command('upgrade', payload, 'stale-cas'))


async def test_no_implicit_builtin_fallback_or_cross_source_upgrade(service, monkeypatch):
    ref, entry = first(service)
    with pytest.raises(OperationError):
        await service.apply(command('adopt', {'ref':ref,'proof':entry['proof']}, 'no-fallback'))
    adopted, _ = await adopt(service)
    def unexpected(*args):
        pytest.fail('cross-source must be rejected before personal lookup')
    monkeypatch.setattr(service.library, 'version', unexpected)
    with pytest.raises(OperationError):
        service.upgrade_preview(adopted['adoption_id'], ref)
    with pytest.raises(OperationError):
        await service.apply(command('upgrade', {'adoption_id':adopted['adoption_id'],'expected_revision':1,
            'ref':ref,'proof':'forged','resolutions':{}}, 'cross-source'))


async def test_personal_source_shape_unchanged_and_cannot_upgrade_to_builtin(service):
    ref = await save(service, configuration())
    personal = await service.apply(command('adopt', {'ref':ref,'proof':service.library.version('agent',ref)['proof']}, 'personal'))
    original = service.adoption(personal['adoption_id'])
    assert original['source'] == {'user_id':service.ctx.requester_user_id,'ref':ref}
    builtin, _ = first(service)
    with pytest.raises(OperationError):
        service.upgrade_preview(personal['adoption_id'], builtin, source='builtin')
    assert service.adoption(personal['adoption_id']) == original


async def test_builtin_first_use_reaches_real_main_and_delegation(service, ctx, monkeypatch, tmp_path):
    from novelvideo.creation.delegations import Delegations
    from novelvideo.creation.store import ObjectStore
    from novelvideo.creation.canvas_workspaces import CanvasWorkspace, CanvasCreate
    from novelvideo.creation.projection import ProjectionService
    from novelvideo.chat.conversations import ConversationStore
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(tmp_path/'sessions'))
    ctx.is_home_node = True
    ctx.state_dir.mkdir()
    (ctx.state_dir/'project_config.json').write_text(json.dumps({'creation_mode':'agent_canvas_v1'}))
    store = ObjectStore(ctx.state_dir)
    canvas = CanvasWorkspace(store, project_id=ctx.project_id).create('user', CanvasCreate(request_id='canvas', kind='free', title='开始创作'))
    ProjectionService(store).apply(canvas.operation_id)
    cid = canvas.object_refs[0]['canvas_id']
    chat = ConversationStore('user','project',ctx.project_id).create('创作', canvas_id=cid, request_id='chat')
    adopted, _ = await adopt(service)
    delegations = Delegations(ctx, username='user', authority='user')
    await delegations.apply(command('main.select', {'adoption_id':adopted['adoption_id'],
        'expected_adoption_revision':1,'expected_binding_revision':0}, 'main'))
    body = {'canvas_id':cid,'conversation_id':chat['id'],'scope':{'kind':'canvas'},'agent':{'kind':'main'},
            'input_refs':[],'goal':'开始一个故事','allowed_operations':['proposal.create'],'acceptance':['一段文本建议']}
    preview = delegations.preview(body)
    task = await delegations.apply(command('delegation.create', {**body,'preview_proof':preview['proof']}, 'task'))
    assert task['contract']['agent']['source']['kind'] == 'builtin'
    assert task['contract']['agent']['effective'] == service.adoption(adopted['adoption_id'])['effective']
    assert not service.library.root.exists()


async def test_real_builtin_api_and_agent_token_rejection(ctx, tmp_path, monkeypatch):
    from fastapi import FastAPI, HTTPException
    from httpx import ASGITransport, AsyncClient
    from novelvideo.api.routes import creation_library as routes
    from novelvideo.api.auth import get_api_user
    from novelvideo import config
    monkeypatch.setattr(config, 'STATE_DIR', tmp_path/'libraries')
    user = {'id':'user','username':'user'}
    async def context(project, user, role):
        if role == 'editor' and user['id'] == 'viewer':
            raise HTTPException(403)
        return SimpleNamespace(**{**vars(ctx),'requester_user_id':user['id']})
    monkeypatch.setattr(routes, 'context_for', context)
    app = FastAPI(); app.include_router(routes.router)
    app.dependency_overrides[get_api_user] = lambda:dict(user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        prefix = '/projects/p/creation/reusable/agent'
        catalog = await client.get(prefix+'/builtins')
        assert catalog.status_code == 200, catalog.text
        entry = catalog.json()['data'][0]
        ref = {'id':entry['id'],'revision':entry['revision']}
        preview = await client.post(prefix+'/adoption-preview', json={'source':'builtin','ref':ref})
        assert preview.json()['data'] == entry
        assert not ctx.state_dir.exists() and not config.STATE_DIR.exists()
        request = command('adopt', {'source':'builtin','ref':ref,'proof':entry['proof']}, 'http-adopt')
        user['credential_kind'] = 'agent_session'
        assert (await client.post(prefix+'/operations', json=request)).status_code == 403
        user.pop('credential_kind'); user['id'] = 'viewer'
        assert (await client.post(prefix+'/operations', json=request)).status_code == 403
        user['id'] = 'user'
        response = await client.post(prefix+'/operations', json=request)
        assert response.status_code == 200, response.text
        assert (await client.post(prefix+'/operations', json=request)).json() == response.json()
        identifier = response.json()['data']['adoption_id']
        detail = await client.get(prefix+'/adoptions/'+identifier)
        assert detail.json()['data']['source']['kind'] == 'builtin'
        assert not config.STATE_DIR.exists()
