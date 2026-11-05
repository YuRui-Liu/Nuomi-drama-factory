from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from novelvideo.creation import builtin_agents, professional_agents
from novelvideo.creation.agent_profiles import AgentProfiles
from novelvideo.creation.personal_library import PersonalLibrary
from novelvideo.creation.operations import OperationError
from novelvideo.creation.professional_roles import role_configuration
from .test_ontology import ctx, command
from .test_agent_profiles import configuration, save


_LEGACY_CAPABILITY_POLICY = {
    'items': [
        {'id':'context.read','title':'读取已授权创作上下文','runtime_permission':'requires_scoped_read'},
        {'id':'proposal.create','title':'提出创作修改建议','runtime_permission':'proposal_only'},
        {'id':'ontology.draft','title':'提出对象定义草案','runtime_permission':'draft_only'},
        {'id':'media.propose','title':'提出媒体生成方案','runtime_permission':'requires_separate_execution_authorization'},
    ],
    'execution_enabled':False,
    'notice':'技能说明不授予工具权限；执行须由后续运行时核验范围和授权',
}


def _legacy_fixed(id_, configuration_, content_hash, proof):
    return {
        'id':id_, 'revision':1,
        'configuration':{**configuration_, 'capability_policy':deepcopy(_LEGACY_CAPABILITY_POLICY)},
        'kind':'agent',
        'source':{'kind':'builtin','ref':{'id':id_,'revision':1},'content_hash':content_hash},
        'content_hash':content_hash, 'proof':proof,
    }


_LEGACY_FIXED_BASELINE = (
    _legacy_fixed('builtin:creative-lead', {
        'name':'通用主创',
        'responsibilities':'理解用户创作目标，协调局部创作建议，维护人物、场景与叙事一致性。',
        'methods':'依据用户题材、已确认约定和当前授权范围组织工作。先明确意图与已有素材，再提出可比较的局部方案；不强制线性制作阶段，不将草案视为已确认事实。',
        'references':[],
        'skill_notes':['把修改建议与事实确认分开；媒体方案须另行授权执行。'],
        'capabilities':['context.read','proposal.create','ontology.draft','media.propose'],
        'defaults':{},'studio_refs':[],
    }, '9be4a11a550be54ea47a37ef9614d6aaa28cfba957d4988d73c7cb543163a2ef',
       'f7f18851f9c5499ee10ed0cc0bca77d2f7fa01f6e48709d4ee171556aac8505c'),
    _legacy_fixed('builtin:text-writer', {
        'name':'文本编剧',
        'responsibilities':'在明确授权的片段中提出情节、人物行动与对白建议。',
        'methods':'遵循用户题材、人物动机和已确认约定，以可修改的文本候选呈现方案。保留未决问题，不擅自扩展范围或改变已确认事实。',
        'references':[],
        'skill_notes':['文本建议不代表用户已接受，不自动生成媒体。'],
        'capabilities':['context.read','proposal.create'],
        'defaults':{},'studio_refs':[],
    }, '41fc1dd0ce70fc50e141012da1d5ab7b3e7e74ca3d2e0697464689622e6b42f2',
       '53f6bd5a0db6fcf013478645a8b69e1dea6c8a928ae6ca2d28da081317ccd808'),
    _legacy_fixed('builtin:ideation', {
        'name':'立意策划',
        'responsibilities':'依据用户给定的题材、受众、角色、时代、看点、画风、篇幅与故事种子，生成三个可比较的原创故事方向。',
        'methods':'整理用户提供的创作要素，围绕不同核心冲突和叙事钩子提出三个有区分度的方向。调用 proposal.create 时，text 必须是无 Markdown 代码围栏的纯 JSON，根对象仅含 schema_version 和 candidates，严格符合 {"schema_version":1,"candidates":[...]}；schema_version 必须是整数 1，candidates 恰好三条。每条候选仅含 id、title、premise、protagonist_goal、conflict、hook、expansion；id 是非空字符串，且在三条候选之间唯一，其余六项均为非空字符串。候选始终作为建议交由用户选择或修改，不自动采纳，也不执行媒体制作。',
        'references':[],
        'skill_notes':['proposal.create 的 text 必须严格遵循 methods 中的 JSON 契约。立意候选只供比较与用户确认，不自动采纳或执行媒体制作。'],
        'capabilities':['context.read','proposal.create'],
        'defaults':{},'studio_refs':[],
    }, '083c6f98ee77a1eba6e2d497edf3aebde34cbe74562cb89793eda4ef5915a23a',
       '8211f43b4ce661c667d33a78eb4e65beb6ea34ce63a9e9b5d6bbe14263c98c7f'),
)


LEGACY_V1_PROOFS = {
    'builtin:creative-lead': 'f7f18851f9c5499ee10ed0cc0bca77d2f7fa01f6e48709d4ee171556aac8505c',
    'builtin:text-writer': '53f6bd5a0db6fcf013478645a8b69e1dea6c8a928ae6ca2d28da081317ccd808',
}


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
    newer = deepcopy(max((entry for entry in builtin_agents.BUILTIN_AGENTS
                         if entry['id'] == 'builtin:creative-lead'), key=lambda entry: entry['revision']))
    newer['revision'] += 1
    newer['configuration']['methods'] = '新版：根据用户目标比较两个可行方案'
    monkeypatch.setattr(builtin_agents, 'BUILTIN_AGENTS', (*builtin_agents.BUILTIN_AGENTS, newer))
    return {'id':newer['id'],'revision':newer['revision']}


def test_catalog_and_preview_do_not_initialize_personal_or_project(service, ctx):
    entries = service.builtin_catalog()
    assert len(entries) == 22
    assert len({entry['id'] for entry in entries}) == 22
    by_id = {entry['id']:entry for entry in entries}
    assert set(by_id) == {entry['id'] for entry in professional_agents.PROFESSIONAL_AGENTS}
    assert {'builtin:creative-lead','builtin:text-writer'} <= by_id.keys()
    assert 'builtin:ideation' not in by_id
    ideation = builtin_agents.resolve({'id':'builtin:ideation','revision':1})
    assert ideation['configuration']['capabilities'] == ['context.read','proposal.create']
    methods = ideation['configuration']['methods']
    assert 'proposal.create' in methods and 'text' in methods
    assert '无 Markdown 代码围栏的纯 JSON' in methods
    assert 'schema_version' in methods and '整数 1' in methods
    assert '根对象仅含 schema_version 和 candidates' in methods
    assert '恰好三条' in methods
    assert 'id 是非空字符串，且在三条候选之间唯一' in methods
    assert '每条候选仅含 id、title、premise、protagonist_goal、conflict、hook、expansion' in methods
    for entry in entries:
        assert entry['id'].startswith('builtin:')
        assert entry['source']['kind'] == 'builtin'
        assert entry['configuration']['defaults'] == {}
        assert entry['configuration']['studio_refs'] == []
        assert set(entry['configuration']['capabilities']) <= {c['id'] for c in service.capabilities()['items']}
        assert entry['configuration']['capability_policy']['execution_enabled'] is False
        assert service.adoption_preview({'id':entry['id'],'revision':entry['revision']}, source='builtin') == entry
    assert not ctx.state_dir.exists() and not service.library.root.exists()


def test_legacy_builtin_revision_1_objects_match_full_baseline():
    resolved = tuple(
        builtin_agents.resolve({'id':entry['id'],'revision':entry['revision']})
        for entry in _LEGACY_FIXED_BASELINE
    )
    assert resolved == _LEGACY_FIXED_BASELINE
    listed = {entry['id']:entry for entry in builtin_agents.catalog()}
    assert listed['builtin:creative-lead']['revision'] > 1
    assert listed['builtin:text-writer']['revision'] > 1
    assert 'builtin:ideation' not in listed


async def test_professional_adoption_is_frozen(service, monkeypatch):
    entry = next(x for x in service.builtin_catalog()
                 if x['id'] == 'builtin:lighting-designer')
    ref = {'id':entry['id'],'revision':entry['revision']}
    result = await service.apply(command('adopt', {
        'source':'builtin','ref':ref,'proof':entry['proof']
    }, 'adopt-lighting'))
    snapshot = service.adoption(result['adoption_id'])
    assert snapshot['effective']['capabilities'] == entry['configuration']['capabilities']
    assert snapshot['effective']['skill_notes'] == entry['configuration']['skill_notes']
    monkeypatch.setattr(builtin_agents, 'BUILTIN_AGENTS', ())
    assert service.adoption(result['adoption_id']) == snapshot


@pytest.mark.parametrize(('identifier', 'proof'), LEGACY_V1_PROOFS.items())
def test_legacy_builtin_v1_proofs_and_content_remain_fixed(identifier, proof):
    fixed = builtin_agents.resolve({'id':identifier,'revision':1})
    assert fixed['proof'] == proof
    assert fixed['configuration']['defaults'] == {}
    assert fixed['configuration']['studio_refs'] == []
    assert not {'delivery_requirements','collaboration_rules','required_node_operations','examples'} & fixed['configuration'].keys()


def test_hidden_legacy_role_references_remain_resolvable_without_entering_default_catalog(service):
    assert 'builtin:producer' not in {entry['id'] for entry in service.builtin_catalog()}
    fixed = builtin_agents.resolve({'id':'builtin:producer','revision':1})
    assert fixed['configuration'] == {
        **role_configuration('producer'),
        'capability_policy':AgentProfiles.capabilities(),
    }


async def test_conflicting_legacy_role_proof_cannot_adopt_new_professional_revision(service):
    ref = {'id':'builtin:lighting-designer','revision':1}
    legacy_fixed = builtin_agents._fixed({
        'id':ref['id'], 'revision':ref['revision'],
        'configuration':role_configuration('lighting-designer'),
    })
    current_fixed = builtin_agents.resolve(ref)
    assert legacy_fixed['source']['content_hash'] != current_fixed['source']['content_hash']
    with pytest.raises(OperationError) as error:
        await service.apply(command('adopt', {
            'source':'builtin','ref':ref,'proof':legacy_fixed['proof'],
        }, 'legacy-role-conflict'))
    assert error.value.code == 'source_conflict'
    assert service.catalog() == [] and service.receipt('legacy-role-conflict') is None


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
    changed = deepcopy(next(item for item in builtin_agents.BUILTIN_AGENTS
                            if item['id'] == ref['id'] and item['revision'] == ref['revision']))
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
    assert current['source']['ref'] == ref and current['fixed']['revision'] == ref['revision']
    assert current['effective']['methods'] == ('本项目的方法' if resolution == 'keep' else preview['after']['methods'])
    assert service.adoption(identifier, 2) == old
    assert service.adoption(identifier, 1)['fixed']['revision'] == old['fixed']['revision']
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


async def test_formal_script_builtin_upgrade_is_explicit_and_preserves_old_snapshot(service, monkeypatch):
    identifier = 'builtin:text-writer'
    old_ref = {'id': identifier, 'revision': 3}
    latest_ref = {'id': identifier, 'revision': 4}
    old_catalog = []
    for entry in builtin_agents.BUILTIN_AGENTS:
        fixed = deepcopy(entry)
        if fixed['id'] == identifier and fixed['revision'] == old_ref['revision']:
            fixed['configuration']['capabilities'] = [
                capability for capability in fixed['configuration']['capabilities']
                if capability != 'script.document.draft'
            ]
        old_catalog.append(fixed)
    monkeypatch.setattr(builtin_agents, 'BUILTIN_AGENTS', tuple(old_catalog))

    old_fixed = builtin_agents.resolve(old_ref)
    assert 'script.document.draft' not in old_fixed['configuration']['capabilities']
    adopted = await service.apply(command('adopt', {
        'source': 'builtin', 'ref': old_ref, 'proof': old_fixed['proof'],
    }, 'adopt-old-writer'))
    pinned = service.adoption(adopted['adoption_id'])

    latest = builtin_agents.resolve(latest_ref)
    assert 'script.document.draft' in latest['configuration']['capabilities']
    preview = service.upgrade_preview(adopted['adoption_id'], latest_ref, source='builtin')
    await service.apply(command('upgrade', {
        'source': 'builtin',
        'adoption_id': adopted['adoption_id'],
        'expected_revision': pinned['revision'],
        'ref': latest_ref,
        'proof': preview['proof'],
        'resolutions': {},
    }, 'upgrade-writer-script-capability'))

    current = service.adoption(adopted['adoption_id'])
    assert current['source']['ref'] == latest_ref
    assert 'script.document.draft' in current['effective']['capabilities']
    assert service.adoption(adopted['adoption_id'], pinned['revision']) == pinned


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
    task_id = task['delegation_id']
    fixed_task = delegations.get(task_id)
    newer_ref = add_version(monkeypatch)
    upgrade = service.upgrade_preview(adopted['adoption_id'], newer_ref, source='builtin')
    await service.apply(command('upgrade', {'source':'builtin','adoption_id':adopted['adoption_id'],
        'expected_revision':1,'ref':newer_ref,'proof':upgrade['proof'],'resolutions':{}}, 'upgrade-main'))
    current_task = delegations.get(task_id)
    assert current_task['contract']['allowed_operations'] == fixed_task['contract']['allowed_operations']
    assert current_task['contract']['agent'] == fixed_task['contract']['agent']
    assert current_task['contract']['agent']['source']['ref'] == fixed_task['contract']['agent']['source']['ref']
    assert service.adoption(adopted['adoption_id'])['fixed']['revision'] == newer_ref['revision']
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
        entries = catalog.json()['data']
        assert len(entries) == 22
        entry = next(item for item in entries if item['id'] == 'builtin:lighting-designer')
        ref = {'id':entry['id'],'revision':entry['revision']}
        fixed = await client.get(prefix+f"/builtins/{ref['id']}/revisions/{ref['revision']}")
        assert fixed.status_code == 200, fixed.text
        assert fixed.json()['data'] == entry
        preview = await client.post(prefix+'/adoption-preview', json={'source':'builtin','ref':ref})
        assert preview.json()['data'] == entry
        legacy = await client.get(prefix+'/builtins/builtin:ideation/revisions/1')
        assert legacy.status_code == 200, legacy.text
        assert legacy.json()['data']['source']['ref'] == {'id':'builtin:ideation','revision':1}
        missing = await client.get(prefix+'/builtins/builtin:missing/revisions/1')
        assert missing.status_code == 409
        assert missing.json()['detail']['code'] == 'source_conflict'
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
