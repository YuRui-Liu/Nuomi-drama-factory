from copy import deepcopy

import pytest

from novelvideo.creation import builtin_agents, professional_agents
from novelvideo.creation.agent_profiles import AgentProfiles
from novelvideo.creation.operations import OperationError
from novelvideo.freezone.orchestration.capabilities import catalog_by_type
from .test_ontology import command, ctx


EXPECTED_ROLES = {
    'builtin:creative-lead': '主创制片统筹',
    'builtin:creative-ideator': '创意师',
    'builtin:text-writer': '主笔',
    'builtin:script-editor': '剧本编辑',
    'builtin:dialogue-writer': '台词专家',
    'builtin:director': '导演',
    'builtin:storyboard-artist': '分镜师',
    'builtin:blocking-designer': '站位师',
    'builtin:cinematographer': '摄影师',
    'builtin:lighting-designer': '灯光师',
    'builtin:action-designer': '动作师',
    'builtin:expression-designer': '表情师',
    'builtin:art-asset-designer': '美术资产师',
    'builtin:continuity-editor': '连续性师',
    'builtin:storyboard-image-operator': '分镜图执行师',
    'builtin:minimax-video-operator': 'MiniMax执行师',
    'builtin:sound-designer': '声音师',
    'builtin:voice-designer': '音色设计师',
    'builtin:dubbing-director': '配音导演',
    'builtin:dubbing-operator': '配音执行师',
    'builtin:editor': '剪辑师',
    'builtin:delivery-reviewer': '交付检查',
}


@pytest.fixture
def service(ctx, tmp_path):
    from novelvideo.creation.personal_library import PersonalLibrary

    return AgentProfiles(ctx, PersonalLibrary(tmp_path/'personal', ctx.requester_user_id))


def test_professional_catalog_exposes_all_configured_roles(service, ctx):
    entries = service.builtin_catalog()
    assert {entry['id']:entry['configuration']['name'] for entry in entries} == EXPECTED_ROLES
    assert len(entries) == 22
    assert all(entry['revision'] == (3 if identifier in {
        'builtin:creative-lead', 'builtin:text-writer', 'builtin:director', 'builtin:storyboard-artist'} else 2 if identifier in {
        'builtin:blocking-designer', 'builtin:cinematographer', 'builtin:lighting-designer',
        'builtin:action-designer', 'builtin:expression-designer', 'builtin:script-editor',
        'builtin:minimax-video-operator', 'builtin:storyboard-image-operator', 'builtin:voice-designer',
        'builtin:dubbing-director', 'builtin:dubbing-operator'} else 1)
        for identifier, entry in ((item['id'], item) for item in entries))

    operation_catalog = catalog_by_type()
    for entry in entries:
        config = entry['configuration']
        assert config['methods'].strip()
        assert config['capabilities']
        assert config['delivery_requirements']
        assert isinstance(config['references'], list)
        assert isinstance(config['examples'], list)
        assert config['collaboration_rules']
        assert config['required_node_operations']
        for required in config['required_node_operations']:
            assert set(required) == {'node_type', 'operation'}
            node = operation_catalog[required['node_type']]
            assert required['operation'] in {op['name'] for op in node['operations']}
            # Availability is deliberately excluded: this list describes a role's needs.
            assert 'available' not in required
        assert entry['configuration']['capability_policy']['execution_enabled'] is False
        assert entry['source']['kind'] == 'builtin'

    assert not ctx.state_dir.exists() and not service.library.root.exists()


def test_voice_roles_separate_design_direction_and_execution_without_adoption_grants(service):
    configs = {item['id']: item['configuration'] for item in service.builtin_catalog()}
    required = {
        'builtin:voice-designer': ('characterVoiceNode', 'prepare_design'),
        'builtin:dubbing-director': ('audioNode', 'prepare_dubbing'),
        'builtin:dubbing-operator': ('audioNode', 'generate_dubbing'),
    }
    for identifier, (node_type, operation) in required.items():
        config = configs[identifier]
        assert {'node_type': node_type, 'operation': operation} in config['required_node_operations']
        assert config['capability_policy']['execution_enabled'] is False
        assert not any('adopt' in item['operation'] for item in config['required_node_operations'])
    assert 'Qwen3-TTS' in configs['builtin:voice-designer']['methods']
    assert 'IndexTTS2' in configs['builtin:dubbing-operator']['methods']


def test_director_specialists_have_versioned_content_operations(service):
    from novelvideo.freezone.orchestration.adapters import available_operations
    assert {('directorWorkspaceNode', operation) for operation in
            ('save_blocking', 'compile_blocking', 'edit_shot')} <= available_operations()
    entries = {item['id']: item for item in service.builtin_catalog()}
    for identifier in ('director', 'storyboard-artist', 'blocking-designer',
                       'cinematographer', 'lighting-designer', 'action-designer', 'expression-designer'):
        entry = entries[f'builtin:{identifier}']
        assert entry['revision'] == (3 if identifier in {'director', 'storyboard-artist'} else 2)
        assert {'node_type': 'directorWorkspaceNode', 'operation': 'edit_shot'} in entry['configuration']['required_node_operations']
        legacy = builtin_agents.resolve({'id': entry['id'], 'revision': 1})
        assert not any(item['operation'] == 'edit_shot' for item in legacy['configuration']['required_node_operations'])
    assert {'node_type': 'directorWorkspaceNode', 'operation': 'compile_blocking'} in entries['builtin:director']['configuration']['required_node_operations']


def test_professional_source_entries_keep_legacy_v1_and_add_latest_versions():
    by_ref = {(item['id'], item['revision']):item for item in professional_agents.PROFESSIONAL_AGENTS}
    assert ('builtin:creative-lead', 2) in by_ref
    assert ('builtin:text-writer', 2) in by_ref
    for identifier in EXPECTED_ROLES.keys() - {'builtin:creative-lead', 'builtin:text-writer'}:
        assert (identifier, 1) in by_ref
    for identifier in ('builtin:creative-lead', 'builtin:text-writer'):
        legacy = builtin_agents.resolve({'id':identifier, 'revision':1})
        assert legacy['revision'] == 1
        assert not {'delivery_requirements','collaboration_rules','required_node_operations','examples'} & legacy['configuration'].keys()


def test_professional_role_capabilities_are_requirements_not_execution_grants(service):
    policy = {item['id']:item['runtime_permission'] for item in service.capabilities()['items']}
    assert policy['node.patch'] == 'requires_scoped_edit_authorization'
    assert policy['node.run'] == 'requires_scoped_execution_authorization'
    assert service.capabilities()['execution_enabled'] is False
    entry = next(item for item in service.builtin_catalog() if item['id'] == 'builtin:storyboard-image-operator')
    config = entry['configuration']
    assert 'node.run' in config['capabilities']
    assert not any('execution_authorized' in value for value in config.values() if isinstance(value, dict))


def test_professional_roles_declare_direct_node_run_needs_without_granting_authority(service):
    entries = {item['id']:item for item in service.builtin_catalog()}
    assert all('node.run' in entries[identifier]['configuration']['capabilities']
               for identifier in EXPECTED_ROLES)
    assert all(entries[identifier]['configuration']['capability_policy']['execution_enabled'] is False
               for identifier in EXPECTED_ROLES)

    lighting = entries['builtin:lighting-designer']['configuration']
    assert {'node_type':'storyboardGenNode', 'operation':'generate'} in lighting['required_node_operations']
    assert any('可直接调用' in rule and '非强制中转' in rule
               for rule in lighting['collaboration_rules'])


def test_latest_main_role_preserves_every_legacy_capability(service):
    legacy = builtin_agents.resolve({'id':'builtin:creative-lead','revision':1})
    latest = next(item for item in service.builtin_catalog() if item['id']=='builtin:creative-lead')
    assert set(legacy['configuration']['capabilities']) <= set(latest['configuration']['capabilities'])
    assert 'ontology.draft' in latest['configuration']['capabilities']


def test_professional_catalog_does_not_claim_unsourced_public_examples():
    assert all(entry['configuration']['examples'] == [] for entry in professional_agents.PROFESSIONAL_AGENTS)


async def test_new_profile_fields_survive_publication_adoption_and_project_override(service):
    source = next(item for item in professional_agents.PROFESSIONAL_AGENTS
                  if item['id'] == 'builtin:lighting-designer')
    config = deepcopy(source['configuration'])
    config['examples'] = [{
        'source_url': 'https://example.org/source',
        'prompt_public': False,
        'inputs_public': False,
        'workflow_public': False,
    }]
    preview = service.preview(config)
    assert preview['configuration']['required_node_operations'] == config['required_node_operations']
    assert preview['execution_enabled'] is False
    saved = await service.save(command('save', {
        'expected_revision':0, 'configuration':config, 'proof':preview['proof'],
    }, 'publish'))
    fixed = service.library.version('agent', saved)
    assert fixed['configuration']['examples'] == config['examples']
    assert fixed['configuration']['collaboration_rules'] == config['collaboration_rules']

    adopted = await service.apply(command('adopt', {
        'ref':saved, 'proof':fixed['proof'],
    }, 'adopt-profile'))
    adoption_id = adopted['adoption_id']
    changes = {'delivery_requirements':['交付包含光源、方向、色温和镜头版本']}
    override = service.override_preview(adoption_id, changes)
    await service.apply(command('override', {
        'adoption_id':adoption_id, 'expected_revision':1,
        'changes':changes, 'proof':override['proof'],
    }, 'override-profile'))
    assert service.adoption(adoption_id)['effective']['delivery_requirements'] == changes['delivery_requirements']
    assert service.adoption(adoption_id)['fixed']['configuration']['delivery_requirements'] == config['delivery_requirements']


@pytest.mark.parametrize('invalid', [
    [{'node_type':'videoNode','operation':'fictional'}],
    [{'node_type':'missingNode','operation':'generate'}],
    [{'node_type':'videoNode','operation':'generate','available':True}],
    [{'node_type':'videoNode','operation':'generate'}, {'node_type':'videoNode','operation':'generate'}],
])
def test_required_node_operations_reject_unknown_or_forged_catalog_entries(service, invalid):
    config = deepcopy(professional_agents.PROFESSIONAL_AGENTS[0]['configuration'])
    config['required_node_operations'] = invalid
    with pytest.raises(OperationError):
        service.preview(config)


def test_examples_require_source_and_explicit_publicness_flags(service):
    config = deepcopy(professional_agents.PROFESSIONAL_AGENTS[0]['configuration'])
    config['examples'] = [{'source_url':'https://example.org/source','prompt_public':False,
                           'inputs_public':False,'workflow_public':False,'prompt':'copied prompt'}]
    with pytest.raises(OperationError):
        service.preview(config)
    config['examples'][0]['prompt_public'] = 'false'
    config['examples'][0].pop('prompt')
    with pytest.raises(OperationError):
        service.preview(config)

