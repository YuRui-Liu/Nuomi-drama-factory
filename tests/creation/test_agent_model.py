import json
import sqlite3
from types import SimpleNamespace

import pytest

from novelvideo.creation import agent_model
from novelvideo.creation.agent_model import Decision, ToolDecision
from novelvideo.text_task_runtime.models import AgentTaskRouteSnapshot
from .test_delegations import ctx


@pytest.fixture(autouse=True)
def no_global_provider_patches(monkeypatch):
    from novelvideo import llm_instrumentation
    monkeypatch.setattr(llm_instrumentation,'install_provider_instrumentation',lambda:None)


def test_availability_no_files_secret_hydration_or_network(ctx,tmp_path,monkeypatch):
    from novelvideo import config
    from novelvideo import text_runtime_settings as settings
    from novelvideo import model_gateway_settings as gateway
    monkeypatch.setattr(config,'STATE_DIR',tmp_path/'global')
    monkeypatch.setattr(settings,'is_self_hosted',lambda:True)
    monkeypatch.setattr(gateway,'_hydrate_secrets',lambda *_:pytest.fail('must not hydrate secrets'))
    result=agent_model.availability(ctx)
    assert result['available'] is False and result['code']=='runtime_unconfigured'
    assert not ctx.state_dir.exists() and not config.STATE_DIR.exists()


def test_node_decisions_have_precise_structured_payloads():
    patch = ToolDecision.model_validate({'operation': 'node.patch', 'payload': {
        'node_id': 'frame-node', 'expected_revision': 4,
        'changes': [{'path': ['frames', 0, 'static_panel', 'lighting'], 'value': '冷色侧光'}],
    }})
    run = ToolDecision.model_validate({'operation': 'node.run', 'payload': {
        'node_id': 'image-node', 'operation': 'generate', 'expected_revision': 4,
    }})
    assert patch.payload['changes'][0]['path'][-1] == 'lighting'
    assert run.payload['operation'] == 'generate'
    skill_run = ToolDecision.model_validate({'operation': 'node.run', 'payload': {
        'node_id': 'skill-node', 'operation': 'run', 'expected_revision': 4,
    }})
    assert skill_run.payload['operation'] == 'run'
    with pytest.raises(Exception):
        ToolDecision.model_validate({'operation': 'node.run', 'payload': {
            'node_id': 'image-node', 'operation': 'patch', 'expected_revision': 4,
        }})


def test_asset_design_accepts_empty_server_created_targets_and_ignores_null_optional_source():
    payload = {
        'target_node_ids': [], 'input_indices': [], 'acceptance_indices': [0],
        'kind': 'character', 'name': '阿宁', 'description': '角色设定', 'prompt': '电影感角色肖像',
        'reference_indices': [], 'style_template_id': None,
        'base_asset_input_index': None, 'draft_title': '阿宁形象提案',
        'draft_text': '她是夜班电台的主持人。', 'source_proposal_input_index': None,
    }
    prepared = ToolDecision.model_validate({'operation': 'asset.design.create', 'payload': payload})
    assert prepared.payload['target_node_ids'] == []

    node_scoped = {**payload, 'target_node_ids': ['group-node-2'], 'draft_title': None,
                   'draft_text': None, 'source_proposal_input_index': 1,
                   'input_indices': [1]}
    assert ToolDecision.model_validate({'operation': 'asset.design.create',
                                        'payload': node_scoped}).payload['target_node_ids'] == ['group-node-2']


def test_asset_design_accepts_explicit_usage_and_aspect_ratio_but_rejects_mismatch():
    payload = {
        'target_node_ids': [], 'input_indices': [], 'acceptance_indices': [0],
        'kind': 'character', 'purpose': 'avatar', 'aspect_ratio': '1:1',
        'name': '阿宁', 'description': '角色设定', 'prompt': '头像',
        'reference_indices': [], 'draft_title': '提案', 'draft_text': '人物设定',
    }
    prepared = ToolDecision.model_validate({'operation': 'asset.design.create', 'payload': payload})
    assert prepared.payload['purpose'] == 'avatar'
    assert prepared.payload['aspect_ratio'] == '1:1'

    for purpose in ('half_body', 'full_body', 'body', 'character_sheet'):
        accepted = ToolDecision.model_validate({'operation': 'asset.design.create',
            'payload': {**payload, 'purpose': purpose}})
        assert accepted.payload['purpose'] == purpose

    camera_payload = {**payload, 'camera_selection': {
        'cameraBodyId': 'arri_alexa_65', 'lensId': 'arri_signature_prime',
        'focalLengthMm': 75, 'aperture': 'f/4',
    }}
    assert ToolDecision.model_validate({'operation': 'asset.design.create',
        'payload': camera_payload}).payload['camera_selection']['focalLengthMm'] == 75

    for change in ({'purpose': 'scene'}, {'aspect_ratio': 'auto'},
                   {'camera_selection': {'focalLengthMm': 85}},
                   {'camera_selection': {'cameraBodyId': 'arbitrary'}}):
        with pytest.raises(Exception):
            ToolDecision.model_validate({'operation': 'asset.design.create',
                                         'payload': {**payload, **change}})


@pytest.mark.parametrize(('change', 'reason_code', 'field'), [
    ({'kind': 'location'}, 'asset_design_kind_invalid', None),
    ({'prompt': None}, 'asset_design_prompt_invalid', None),
    ({'style_template_id': []}, 'asset_design_style_template_id_invalid', None),
    ({'base_asset_input_index': 'one'}, 'asset_design_base_asset_input_index_invalid', None),
    ({'reference_indices': [0]}, 'asset_design_references_not_selected', None),
    ({'selected_group_id': 'do-not-authorize-by-payload'},
     'asset_design_payload_field_unknown', 'selected_group_id'),
    ({'draft_text': None}, 'asset_design_draft_source', None),
])
def test_asset_design_validation_uses_safe_reason_codes(change, reason_code, field):
    payload = {
        'target_node_ids': [], 'input_indices': [], 'acceptance_indices': [0],
        'kind': 'character', 'name': '阿宁', 'description': '角色设定', 'prompt': '电影感角色肖像',
        'reference_indices': [], 'draft_title': '阿宁形象提案', 'draft_text': '她是夜班电台的主持人。',
    }
    with pytest.raises(Exception) as failure:
        Decision.model_validate({'message': '候选', 'actions': [{
            'operation': 'asset.design.create', 'payload': {**payload, **change},
        }], 'delegations': [], 'finished': True, 'unmet': []})
    issues = agent_model._safe_validation_issues(failure.value)
    assert issues and issues[0]['loc'] == ['actions', 0]
    assert issues[0]['type'] == reason_code
    if field is not None:
        assert issues[0]['field'] == field
    serialized = json.dumps(issues)
    assert 'do-not-authorize-by-payload' not in serialized


@pytest.mark.parametrize(('payload', 'reason_code', 'field'), [
    ({'node_id': 'frame-1', 'changes': []},
     'node_patch_payload_field_missing', 'expected_revision'),
    ({'node_id': 'frame-1', 'expected_revision': 2, 'changes': [],
      'private_unknown_field': 'never-log-this'},
     'node_patch_payload_field_unknown', 'other'),
    ({'node_id': 'frame-1', 'expected_revision': 2, 'changes': []},
     'node_patch_changes_invalid', None),
    ({'node_id': 'frame-1', 'expected_revision': 2,
      'changes': [{'path': [], 'value': 'value'}]},
     'node_patch_change_path_invalid', None),
])
def test_node_patch_validation_uses_safe_reason_codes(payload, reason_code, field):
    with pytest.raises(Exception) as failure:
        Decision.model_validate({'message': '局部修改候选', 'actions': [{
            'operation': 'node.patch', 'payload': payload,
        }], 'delegations': [], 'finished': True, 'unmet': []})
    issues = agent_model._safe_validation_issues(failure.value)
    assert issues and issues[0]['loc'] == ['actions', 0]
    assert issues[0]['type'] == reason_code
    if field is not None:
        assert issues[0]['field'] == field
    serialized = json.dumps(issues)
    assert 'never-log-this' not in serialized


@pytest.mark.parametrize('change', [
    {'target_node_ids': [3]},
    {'target_node_ids': ['same', 'same']},
    {'draft_text': None},
    {'source_proposal_input_index': 1},
])
def test_asset_design_rejects_invalid_target_or_ambiguous_proposal_source(change):
    payload = {
        'target_node_ids': [], 'input_indices': [], 'acceptance_indices': [0],
        'kind': 'scene', 'name': '旧站台', 'description': '', 'prompt': '雨夜站台',
        'reference_indices': [], 'draft_title': '提案', 'draft_text': '一段真实来源文本',
        'source_proposal_input_index': None,
    }
    with pytest.raises(Exception):
        ToolDecision.model_validate({'operation': 'asset.design.create',
                                     'payload': {**payload, **change}})
    with pytest.raises(Exception):
        ToolDecision.model_validate({'operation': 'node.patch', 'payload': {
            'node_id': 'frame-node', 'expected_revision': 4,
            'changes': [{'path': ['id'], 'value': 'attacker-controlled'}], 'target_node_ids': ['frame-node'],
        }})


def test_script_document_actions_accept_node_ids_and_keep_input_indexes_numeric():
    draft = {
        'target_node_ids': ['script-ref-node'], 'input_indices': [0], 'acceptance_indices': [0],
        'episode_number': 2, 'title': '第二集', 'markdown': '# 第二集\n正文', 'source_input_index': 0,
    }
    assert ToolDecision.model_validate({'operation': 'script.document.draft', 'payload': draft}).payload == draft

    review = {
        'target_node_ids': [], 'input_indices': [0], 'acceptance_indices': [0],
        'document_input_index': 0,
        'report': {'conclusion': 'passed', 'summary': '结构完整。', 'issues': []},
    }
    assert ToolDecision.model_validate({'operation': 'script.document.review', 'payload': review}).payload == review

    for invalid in ({**draft, 'target_node_ids': [0]},
                    {**draft, 'target_node_ids': ['same', 'same']},
                    {**draft, 'input_indices': ['0']}):
        with pytest.raises(Exception):
            ToolDecision.model_validate({'operation': 'script.document.draft', 'payload': invalid})


def test_agent_decision_supports_bounded_full_crew_roster():
    decision = Decision.model_validate({'message': '分工完成', 'actions': [],
        'delegations': [{'task_key': f'task-{index}', 'adoption_id': str(index), 'target_node_ids': ['node'], 'input_indices': [],
            'allowed_operations': ['context.read'], 'goal': '核对节点', 'acceptance': ['给出结论']}
            for index in range(18)], 'finished': True, 'unmet': []})
    assert len(decision.delegations) == agent_model.MAX_ELIGIBLE_SPECIALISTS
    with pytest.raises(Exception):
        Decision.model_validate({'message': '超过团队规模', 'actions': [],
            'delegations': [{'task_key': f'task-{index}', 'adoption_id': str(index), 'target_node_ids': ['node'], 'input_indices': [],
                'allowed_operations': ['context.read'], 'goal': '核对节点', 'acceptance': ['给出结论']}
                for index in range(19)], 'finished': True, 'unmet': []})


def test_child_decisions_use_stable_task_keys_scoped_dependencies_and_host_output_indexes():
    children = [
        {'task_key': 'writer', 'adoption_id': 'writer-agent', 'target_node_ids': ['node'],
         'input_indices': [], 'allowed_operations': ['proposal.create'], 'goal': '写候选',
         'acceptance': ['提供候选']},
        {'task_key': 'reviewer', 'adoption_id': 'review-agent', 'target_node_ids': ['node'],
         'input_indices': [], 'allowed_operations': ['context.read'], 'goal': '核对候选',
         'acceptance': ['指出问题'], 'depends_on': ['writer'],
         'input_output_selections': [{'source_task_key': 'writer', 'output_index': 0}]},
    ]
    result = Decision.model_validate({'message': '拆成两个任务', 'actions': [],
        'delegations': children, 'finished': True, 'unmet': []})
    assert result.delegations[1].depends_on == ['writer']
    assert result.delegations[1].input_output_selections[0].source_task_key == 'writer'

    for invalid in (
        [dict(children[0], depends_on=['missing']), children[1]],
        [dict(children[0], depends_on=['reviewer']), dict(children[1], depends_on=['writer'])],
        [children[0], dict(children[1], input_output_selections=[
            {'source_task_key': 'unrelated', 'output_index': 0}])],
    ):
        with pytest.raises(Exception):
            Decision.model_validate({'message': '无效依赖', 'actions': [],
                'delegations': invalid, 'finished': True, 'unmet': []})


def test_empty_canvas_child_scope_is_explicit():
    valid = {'task_key': 'designer', 'scope_kind': 'canvas', 'adoption_id': 'designer-agent',
        'target_node_ids': [], 'input_indices': [], 'allowed_operations': ['proposal.create'],
        'goal': '准备空画布方向', 'acceptance': ['给出候选']}
    decision_value = Decision.model_validate({'message': '交给设计专家', 'actions': [],
        'delegations': [valid], 'finished': True, 'unmet': []})
    assert decision_value.delegations[0].scope_kind == 'canvas'
    with pytest.raises(Exception):
        Decision.model_validate({'message': '局部范围不能空', 'actions': [],
            'delegations': [dict(valid, scope_kind='nodes')], 'finished': True, 'unmet': []})


def test_codex_decision_wire_schema_is_strict_and_keeps_arbitrary_payload_as_json_text():
    from novelvideo.knowledge_runtime.codex import normalize_codex_output_schema

    schema = normalize_codex_output_schema(agent_model.CodexDecision.model_json_schema())

    def assert_objects_are_closed(value):
        if isinstance(value, dict):
            if value.get('type') == 'object' or 'properties' in value:
                assert value.get('additionalProperties') is False
            for child in value.values():
                assert_objects_are_closed(child)
        elif isinstance(value, list):
            for child in value:
                assert_objects_are_closed(child)

    assert_objects_are_closed(schema)
    assert schema['$defs']['CodexToolDecision']['properties']['payload_json']['type'] == 'string'


def test_codex_decision_decodes_payload_then_applies_original_decision_validation():
    valid = agent_model.CodexDecision.model_validate({
        'message': '已完成准备',
        'actions': [{
            'operation': 'node.patch',
            'payload_json': json.dumps({
                'node_id': 'frame-node', 'expected_revision': 4,
                'changes': [{'path': ['frames', 0, 'lighting'], 'value': '冷色侧光'}],
            }),
        }],
        'delegations': [], 'finished': False, 'unmet': ['等待用户确认'],
    })
    decoded = agent_model.decode_codex_decision(valid)
    assert isinstance(decoded, Decision)
    assert decoded.actions[0].payload['changes'][0]['value'] == '冷色侧光'

    invalid = agent_model.CodexDecision.model_validate({
        'message': '无效动作',
        'actions': [{
            'operation': 'node.patch',
            'payload_json': json.dumps({
                'node_id': 'frame-node', 'expected_revision': 4,
                'changes': [{'path': ['lighting'], 'value': '冷色'}],
                'target_node_ids': ['unauthorized-node'],
            }),
        }],
        'delegations': [], 'finished': False, 'unmet': [],
    })
    with pytest.raises(ValueError):
        agent_model.decode_codex_decision(invalid)


def test_codex_decision_rejects_malformed_and_duplicate_payload_json_keys():
    for payload_json in ('not json', '{"operation":"generate","operation":"patch"}'):
        wire = agent_model.CodexDecision.model_validate({
            'message': '不应执行',
            'actions': [{'operation': 'node.run', 'payload_json': payload_json}],
            'delegations': [], 'finished': False, 'unmet': [],
        })
        with pytest.raises(ValueError):
            agent_model.decode_codex_decision(wire)


async def test_codex_invalid_wire_response_records_only_safe_schema_diagnostics(ctx, monkeypatch):
    from novelvideo import ports
    from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError
    from novelvideo.text_task_runtime import runtime as runtime_module
    from novelvideo.creation.operations import OperationError
    from pydantic import ValidationError

    class Meter:
        def set_llm_usage_context(self, *args, **kwargs): pass
        def clear_llm_usage_context(self): pass

    class InvalidWireRuntime:
        def __init__(self, snapshot, **kwargs):
            self.result_observer = kwargs['result_observer']
        async def run_structured(self, **kwargs):
            self.result_observer(SimpleNamespace(usage=None, response='private raw CLI output'))
            with pytest.raises(ValidationError) as cause:
                agent_model.CodexDecision.model_validate({'private_output': 'do-not-log'})
            raise KnowledgeRuntimeError('private raw CLI output', code='CODEX_STRUCTURED_OUTPUT_INVALID') from cause.value

    monkeypatch.setattr(ports, 'get_usage_meter', lambda: Meter())
    monkeypatch.setattr(runtime_module, 'CodexStructuredRuntime', InvalidWireRuntime)
    route = AgentTaskRouteSnapshot(task_role=agent_model.ROLE, source='global',
                                   runtime='codex', model='gpt-6-luna')

    with pytest.raises(OperationError) as failure:
        await agent_model.run_decision(ctx, route, 'private prompt text', run_id='run', attempt_id='attempt')

    diagnostic = failure.value.details['diagnostic']
    assert diagnostic['stage'] == 'codex_wire_output'
    assert diagnostic['code'] == 'codex_output_invalid'
    assert diagnostic['exception_type'] == 'KnowledgeRuntimeError'
    assert diagnostic['issues']
    assert all(set(issue) == {'loc', 'type'} for issue in diagnostic['issues'])
    assert failure.value.code == 'model_response_invalid'
    assert failure.value.details['response_received'] is True
    serialized = json.dumps(failure.value.as_dict())
    assert all(secret not in serialized for secret in ('private raw CLI output', 'do-not-log', 'private prompt text'))


async def test_codex_invalid_action_payload_has_distinct_safe_diagnostic(ctx, monkeypatch):
    from novelvideo import ports
    from novelvideo.text_task_runtime import runtime as runtime_module
    from novelvideo.creation.operations import OperationError

    class Meter:
        def set_llm_usage_context(self, *args, **kwargs): pass
        def clear_llm_usage_context(self): pass

    class InvalidPayloadRuntime:
        def __init__(self, snapshot, **kwargs): pass
        async def run_structured(self, **kwargs):
            return agent_model.CodexDecision.model_validate({
                'message': '已完成准备',
                'actions': [{'operation': 'node.patch', 'payload_json': json.dumps({
                    'node_id': 'frame-node', 'expected_revision': 4,
                    'changes': [{'path': ['lighting'], 'value': '冷色'}],
                    'private_unknown_field': 'do-not-log',
                })}],
                'delegations': [], 'finished': False, 'unmet': [],
            })

    monkeypatch.setattr(ports, 'get_usage_meter', lambda: Meter())
    monkeypatch.setattr(runtime_module, 'CodexStructuredRuntime', InvalidPayloadRuntime)
    route = AgentTaskRouteSnapshot(task_role=agent_model.ROLE, source='global',
                                   runtime='codex', model='gpt-6-luna')

    with pytest.raises(OperationError) as failure:
        await agent_model.run_decision(ctx, route, 'private prompt text', run_id='run', attempt_id='attempt')

    diagnostic = failure.value.details['diagnostic']
    assert diagnostic['stage'] == 'decision_payload'
    assert diagnostic['code'] == 'decision_contract_invalid'
    assert diagnostic['issues']
    assert all(set(issue) in ({'loc', 'type'}, {'loc', 'type', 'field'})
               for issue in diagnostic['issues'])
    assert diagnostic['issues'][0]['type'] == 'node_patch_payload_field_unknown'
    assert diagnostic['issues'][0]['field'] == 'other'
    serialized = json.dumps(failure.value.as_dict())
    assert all(secret not in serialized for secret in ('private_unknown_field', 'do-not-log', 'private prompt text'))


async def test_codex_malformed_action_payload_reports_decode_stage(ctx, monkeypatch):
    from novelvideo import ports
    from novelvideo.text_task_runtime import runtime as runtime_module
    from novelvideo.creation.operations import OperationError

    class Meter:
        def set_llm_usage_context(self, *args, **kwargs): pass
        def clear_llm_usage_context(self): pass

    class MalformedPayloadRuntime:
        def __init__(self, snapshot, **kwargs): pass
        async def run_structured(self, **kwargs):
            return agent_model.CodexDecision.model_validate({
                'message': '已完成准备',
                'actions': [{'operation': 'node.run', 'payload_json': 'private malformed input'}],
                'delegations': [], 'finished': False, 'unmet': [],
            })

    monkeypatch.setattr(ports, 'get_usage_meter', lambda: Meter())
    monkeypatch.setattr(runtime_module, 'CodexStructuredRuntime', MalformedPayloadRuntime)
    route = AgentTaskRouteSnapshot(task_role=agent_model.ROLE, source='global',
                                   runtime='codex', model='gpt-6-luna')

    with pytest.raises(OperationError) as failure:
        await agent_model.run_decision(ctx, route, 'private prompt text', run_id='run', attempt_id='attempt')

    assert failure.value.details['diagnostic'] == {
        'stage': 'decision_payload', 'code': 'action_payload_json_invalid',
        'exception_type': 'JSONDecodeError', 'position': 0,
    }
    serialized = json.dumps(failure.value.as_dict())
    assert all(secret not in serialized for secret in ('private malformed input', 'private prompt text'))


def test_readonly_metadata_freezes_route_without_exposing_endpoint(ctx,tmp_path,monkeypatch):
    from novelvideo import config
    from novelvideo import text_runtime_settings as settings
    monkeypatch.setattr(config,'STATE_DIR',tmp_path/'global')
    monkeypatch.setattr(settings,'is_self_hosted',lambda:True)
    db=config.STATE_DIR/'local/settings.db';db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE runtime_settings(key TEXT PRIMARY KEY,value TEXT,updated_at TEXT)')
        conn.execute('INSERT INTO runtime_settings VALUES (?,?,?)',('text_runtime_config',json.dumps({
            'provider':'openai_compatible','base_url':'https://provider.invalid/private','model':'test-model',
            'api_key_ref':'secret-reference'}),'v1'))
        conn.execute('INSERT INTO runtime_settings VALUES (?,?,?)',('text_task_routing_v1',json.dumps({
            'routes':{'creation_agent_decision':{'runtime':'model_api','model':'test-model'}}}),'v1'))
    before=db.read_bytes()
    result=agent_model.availability(ctx)
    assert result['available'] and result['route']['model']=='test-model'
    assert 'secret-reference' not in json.dumps(result) and 'provider.invalid' not in json.dumps(result)
    assert db.read_bytes()==before
    with sqlite3.connect(db) as conn:
        conn.execute('UPDATE runtime_settings SET value=? WHERE key=?',(json.dumps({
            'routes':{'creation_agent_decision':{'runtime':'codex','model':'test-model'}}}),'text_task_routing_v1'))
    from novelvideo.knowledge_runtime import codex
    monkeypatch.setattr(codex, 'resolve_codex_executable', lambda: '/private/codex')
    codex_result = agent_model.availability(ctx)
    assert codex_result['available'] is True
    assert codex_result['code'] == 'configured_not_verified'
    assert codex_result['route']['runtime'] == 'codex'
    assert '/private/codex' not in json.dumps(codex_result)


def test_codex_availability_checks_cli_path_without_model_api_settings(ctx, monkeypatch):
    from novelvideo.text_task_runtime import settings as task_settings
    from novelvideo import text_runtime_settings
    from novelvideo.knowledge_runtime import codex

    monkeypatch.setattr(task_settings, 'resolve_configured_agent_task_route', lambda **kwargs:
        AgentTaskRouteSnapshot(task_role=agent_model.ROLE, source='global',
                               runtime='codex', model='gpt-6-luna'))
    monkeypatch.setattr(codex, 'resolve_codex_executable', lambda: '/private/codex')
    monkeypatch.setattr(text_runtime_settings, 'readonly_text_runtime_metadata',
                        lambda: pytest.fail('Codex availability must not inspect model API settings'))

    result = agent_model.availability(ctx)

    assert result['available'] is True
    assert result['code'] == 'configured_not_verified'
    assert result['route']['runtime'] == 'codex'
    assert result['max_model_calls'] == agent_model.MAX_MODEL_CALLS
    assert '/private/codex' not in json.dumps(result)


def test_codex_availability_reports_missing_cli_without_probing(ctx, monkeypatch):
    from novelvideo.text_task_runtime import settings as task_settings
    from novelvideo.knowledge_runtime import codex
    monkeypatch.setattr(task_settings, 'resolve_configured_agent_task_route', lambda **kwargs:
        AgentTaskRouteSnapshot(task_role=agent_model.ROLE, source='global',
                               runtime='codex', model='gpt-6-luna'))
    monkeypatch.setattr(codex, 'resolve_codex_executable', lambda: '')

    result = agent_model.availability(ctx)

    assert result['available'] is False
    assert result['code'] == 'runtime_unavailable'


async def test_codex_decision_uses_one_strict_tool_disabled_runtime(ctx, monkeypatch):
    from novelvideo import ports
    from novelvideo.text_task_runtime import runtime as runtime_module

    calls = []
    contexts = []
    class Meter:
        def set_llm_usage_context(self, *args, **kwargs):
            contexts.append(('set', args, kwargs))
        def clear_llm_usage_context(self):
            contexts.append(('clear',))

    class Runtime:
        def __init__(self, snapshot, **kwargs):
            calls.append(('runtime', snapshot, kwargs))
            self.kwargs = kwargs
        async def run_structured(self, **kwargs):
            calls.append(('run', kwargs))
            self.kwargs['request_started_observer']()
            self.kwargs['result_observer'](SimpleNamespace(usage=None, response=None))
            return agent_model.CodexDecision(message='完成', actions=[], delegations=[], finished=True, unmet=[])

    monkeypatch.setattr(ports, 'get_usage_meter', lambda: Meter())
    monkeypatch.setattr(runtime_module, 'CodexStructuredRuntime', Runtime)
    route = AgentTaskRouteSnapshot(task_role=agent_model.ROLE, source='global',
                                   runtime='codex', model='gpt-6-luna')

    result = await agent_model.run_decision(ctx, route, 'fixed prompt', run_id='run', attempt_id='attempt')

    runtime_call = calls[0]
    assert runtime_call[0] == 'runtime'
    assert runtime_call[2]['strict_single_request'] is True
    assert runtime_call[2]['disable_execution_tools'] is True
    assert len([item for item in calls if item[0] == 'run']) == 1
    assert calls[1][1]['output_type'] is agent_model.CodexDecision
    assert 'payload_json' in calls[1][1]['system_prompt']
    assert result['decision']['finished'] is True
    assert result['usage']['response_received'] is True
    assert contexts[0][2]['billing_metadata']['billing_task_type'] == agent_model.ROLE
    assert contexts[-1] == ('clear',)


async def test_actual_model_api_path_has_no_tools_no_retries_and_trusted_usage(ctx,monkeypatch):
    from novelvideo.text_task_runtime import runtime
    from novelvideo import config
    from novelvideo import ports
    calls=[];contexts=[]
    class Agent:
        def __init__(self,**kwargs):
            calls.append(kwargs)
        async def run(self,prompt,**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(output={'message':'两个方案','actions':[], 'delegations':[],
                'finished':True,'unmet':['尚未输出候选']},usage=lambda:SimpleNamespace(input_tokens=31,output_tokens=14),
                response=SimpleNamespace(provider_response_id='response-1'))
    class Meter:
        def set_llm_usage_context(self,*args,**kwargs):contexts.append((args,kwargs))
        def clear_llm_usage_context(self):contexts.append('clear')
    monkeypatch.setattr(ports,'get_usage_meter',lambda:Meter())
    monkeypatch.setattr(runtime,'Agent',Agent)
    monkeypatch.setattr(config,'get_newapi_text_pydantic_model',lambda *a,**k:calls.append(k) or object())
    route=AgentTaskRouteSnapshot(task_role='creation_agent_decision',source='global',runtime='model_api',model='test')
    result=await agent_model.run_decision(ctx,route,'fixed prompt',run_id='run',attempt_id='attempt')
    assert calls[0]['max_retries_override']==0
    assert calls[1]['retries']==0 and calls[1]['output_retries']==0
    assert not {'tools','toolsets','mcp_servers'} & calls[1].keys()
    assert calls[2]['usage_limits'].request_limit==1
    assert result['usage']['input_tokens']==31 and result['usage']['output_tokens']==14
    assert contexts[0][0][0]=='alice' and contexts[0][1]['project_id']=='p'
    assert contexts[-1]=='clear'


async def test_invalid_decision_never_repairs_or_falls_back(ctx,monkeypatch):
    from novelvideo.text_task_runtime import runtime
    from novelvideo import config,ports
    calls=[]
    class Agent:
        def __init__(self,**kwargs):pass
        async def run(self,*a,**k):calls.append(1);return SimpleNamespace(output={'fake':'invalid'})
    monkeypatch.setattr(runtime,'Agent',Agent)
    monkeypatch.setattr(config,'get_newapi_text_pydantic_model',lambda *a,**k:object())
    monkeypatch.setattr(ports,'get_usage_meter',lambda:SimpleNamespace(set_llm_usage_context=lambda *a,**k:None,clear_llm_usage_context=lambda:None))
    route=AgentTaskRouteSnapshot(task_role='creation_agent_decision',source='global',model='test')
    with pytest.raises(Exception):
        await agent_model.run_decision(ctx,route,'fixed',run_id='run',attempt_id='attempt')
    assert calls==[1]


@pytest.mark.parametrize('valid',[True,False])
async def test_real_pydantic_agent_one_model_request_without_tools(ctx,monkeypatch,valid):
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse,TextPart
    from pydantic_ai.usage import RequestUsage
    from novelvideo import config,ports
    requests=[]
    async def respond(messages,info):
        requests.append(info)
        payload={'message':'已准备','actions':[],'delegations':[],'finished':True,'unmet':[]} if valid else {'invalid':True}
        return ModelResponse(parts=[TextPart(json.dumps(payload))],usage=RequestUsage(input_tokens=25,output_tokens=11))
    monkeypatch.setattr(config,'get_newapi_text_pydantic_model',lambda *a,**k:FunctionModel(respond))
    monkeypatch.setattr(agent_model,'availability',lambda _:None)
    monkeypatch.setattr(ports,'get_usage_meter',lambda:SimpleNamespace(set_llm_usage_context=lambda *a,**k:None,clear_llm_usage_context=lambda:None))
    # Keep the real Agent and validation loop; only its network model is replaced.
    from novelvideo import llm_instrumentation
    monkeypatch.setattr(llm_instrumentation,'install_provider_instrumentation',lambda:None)
    route=AgentTaskRouteSnapshot(task_role='creation_agent_decision',source='global',model='test')
    if valid:
        assert (await agent_model.run_decision(ctx,route,'fixed',run_id='r',attempt_id='a'))['decision']['finished']
    else:
        with pytest.raises(Exception) as failure:await agent_model.run_decision(ctx,route,'fixed',run_id='r',attempt_id='a')
        assert failure.value.code=='model_response_invalid'
        assert failure.value.details['usage']['input_tokens']==25
        assert failure.value.details['usage']['output_tokens']==11
    assert len(requests)==1
    assert not requests[0].function_tools and not requests[0].output_tools


async def test_real_openai_client_retry_override_is_zero(monkeypatch):
    from novelvideo import config
    monkeypatch.setattr(config,'load_text_runtime_settings',lambda:SimpleNamespace(
        model='test',api_key='test-key-not-a-credential',base_url='https://invalid.example/v1'))
    model=config.get_newapi_text_pydantic_model('TEXT_TASK_RUNTIME_MODEL','test',
        model_name_override='selected-model',max_retries_override=0)
    assert model.model_name=='selected-model'
    assert model.client.max_retries==0
    await model.client.close()


@pytest.mark.parametrize('failure',[None,'reserve','usage','tokens','invalid','refund'])
async def test_real_instrumentation_lifecycle_is_reported(ctx,monkeypatch,failure):
    from pydantic_ai import Agent
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse,TextPart
    from pydantic_ai.usage import RequestUsage
    from novelvideo import config,ports,llm_instrumentation as instrumentation
    monkeypatch.setattr(Agent,'run',Agent.run)
    monkeypatch.setattr(instrumentation,'_agent_run_patched',False)
    instrumentation._install_agent_run_patch()
    calls=[]
    class Meter:
        set_llm_usage_context=staticmethod(instrumentation.set_llm_usage_context)
        clear_llm_usage_context=staticmethod(instrumentation.clear_llm_usage_context)
        async def reserve_current_model_call_credit(self,**kwargs):
            calls.append('reserve')
            if failure=='reserve':raise RuntimeError('private billing detail')
            return 'reservation'
        async def refund_model_call_credit_reservation(self,identifier):
            calls.append('refund')
            if failure=='refund':raise RuntimeError('private refund detail')
        async def bump_model_call(self,**kwargs):
            calls.append('usage')
            if failure=='usage':raise RuntimeError('private usage detail')
        async def record_llm_tokens(self,**kwargs):
            calls.append('tokens')
            if failure=='tokens':raise RuntimeError('private token detail')
    async def respond(messages,info):
        calls.append('model')
        payload={'message':'候选','actions':[],'delegations':[],'finished':True,'unmet':[]}
        if failure in {'invalid','refund'}:payload={}
        return ModelResponse(parts=[TextPart(json.dumps(payload))],usage=RequestUsage(input_tokens=17,output_tokens=9))
    monkeypatch.setattr(ports,'get_usage_meter',lambda:Meter())
    monkeypatch.setattr(config,'get_newapi_text_pydantic_model',lambda *a,**k:FunctionModel(respond))
    route=AgentTaskRouteSnapshot(task_role='creation_agent_decision',source='global',model='test')
    if failure in {'reserve','invalid','refund'}:
        with pytest.raises(Exception) as error:
            await agent_model.run_decision(ctx,route,'fixed',run_id='r',attempt_id='a')
        usage=error.value.details['usage']
    else:
        usage=(await agent_model.run_decision(ctx,route,'fixed',run_id='r',attempt_id='a'))['usage']
    assert usage['accounting']['status']==('recorded' if failure is None else 'unknown')
    assert 'private' not in json.dumps(usage)
    assert calls.count('model')==(0 if failure=='reserve' else 1)
    if failure in {'invalid','refund'}:
        assert 'refund' in calls and usage['input_tokens']==17
    if failure in {'reserve','usage','tokens','refund'}:
        assert {'stage':failure,'status':'failed'} in usage['accounting']['events']
