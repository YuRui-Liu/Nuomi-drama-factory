from types import SimpleNamespace

import pytest

from novelvideo.media_capabilities.video.parameters import (
    VideoWorkflowParameterDefinition, VideoWorkflowParameterOption,
)


def workflow():
    return SimpleNamespace(id='video-a', parameters=(VideoWorkflowParameterDefinition(
        key='resolution', label='Resolution', default='720p', options=(
            VideoWorkflowParameterOption(value='720p', label='720p'),
            VideoWorkflowParameterOption(value='1080p', label='1080p'),
        )),))


def test_parameter_resolution_is_shared_and_retains_user_intent():
    from novelvideo.narrative_groups.execution_contract import resolve_execution_parameters
    values = resolve_execution_parameters(workflow(), {'resolution': '720p'}, {'resolution': '1080p'})
    assert values == {'resolution': '1080p'}
    with pytest.raises(ValueError, match='unknown workflow parameter'):
        resolve_execution_parameters(workflow(), {}, {'old_model_parameter': 'x'})


def test_execution_snapshot_detects_payload_drift_without_using_live_defaults():
    from novelvideo.narrative_groups.execution_contract import freeze_execution_contract, validate_execution_contract
    payload = {'model': 'video-a', 'mode': 'auto', 'aspect_ratio': '16:9',
               'workflow_parameters': {'resolution': '1080p'}, 'settings_revision': 2,
               'plan_revision': 3, 'group_id': 'g1', 'episode': 1, 'revision': 4}
    payload['execution_contract'] = freeze_execution_contract(payload)
    validate_execution_contract(payload)
    payload['workflow_parameters']['resolution'] = '720p'
    with pytest.raises(ValueError, match='execution contract'):
        validate_execution_contract(payload)


def test_legacy_task_without_contract_remains_explicitly_supported():
    from novelvideo.narrative_groups.execution_contract import validate_execution_contract
    assert validate_execution_contract({'model': 'legacy'}) is None


def test_workflow_changes_cannot_silently_change_queued_execution():
    from novelvideo.narrative_groups.execution_contract import validate_workflow_definition, workflow_execution_definition
    data = {'id': 'a', 'provider_workflow_id': '100', 'adapter_key': 'h3'}
    definition = SimpleNamespace(model_dump=lambda **kw: dict(data))
    payload = {'workflow_definition': workflow_execution_definition(definition)}
    validate_workflow_definition(payload, definition)
    data['provider_workflow_id'] = '200'
    with pytest.raises(ValueError, match='workflow changed'):
        validate_workflow_definition(payload, definition)


def test_isolated_segment_output_is_not_a_complete_group_video():
    from novelvideo.narrative_groups.execution_contract import group_output_is_complete
    assert not group_output_is_complete('v1', [{'id': 'v1'}, {'id': 'v2'}])
    assert group_output_is_complete('v1', [{'id': 'v1'}])
    assert group_output_is_complete('', [{'id': 'v1'}, {'id': 'v2'}])
