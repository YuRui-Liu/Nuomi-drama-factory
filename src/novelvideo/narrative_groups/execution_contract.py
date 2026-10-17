"""Shared parameter resolution and immutable, auditable queued video inputs."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Mapping

from novelvideo.media_capabilities.video.parameters import resolve_workflow_parameters


def resolve_execution_parameters(workflow, project_defaults, overrides):
    """Used by settings writes and enqueue; unknown model parameters fail closed."""
    return resolve_workflow_parameters(workflow, {**project_defaults, **overrides})


_FIELDS = (
    'episode', 'group_id', 'revision', 'plan_revision', 'settings_revision',
    'model', 'mode', 'aspect_ratio', 'workflow_parameters', 'segment_id',
    'reference_snapshot_id', 'reference_snapshot_digest', 'reference_revision',
    'provider_workflow_id', 'reference_limit', 'storyboard_source_id',
    'requested_parameters', 'workflow_definition',
    'cinematography_review_required', 'retry_planning',
    'storyboard_contract_version', 'storyboard_binding', 'reference_contract_version',
)


def _digest(value: Mapping[str, Any]) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def freeze_execution_contract(payload: Mapping[str, Any]) -> dict[str, Any]:
    values = deepcopy({key: payload[key] for key in _FIELDS if key in payload})
    return {'schema_version': 1, 'effective': values, 'digest': _digest(values)}


def validate_execution_contract(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    if 'execution_contract' not in payload:
        return None  # Explicit compatibility for historical queued tasks.
    contract = payload['execution_contract']
    if not isinstance(contract, dict) or contract != freeze_execution_contract(payload):
        raise ValueError('execution contract differs from queued inputs; resubmit the current plan')
    return deepcopy(contract)


def workflow_execution_definition(workflow) -> dict[str, Any]:
    """Only execution-relevant public configuration; never credentials."""
    data = workflow.model_dump(mode='json')
    result = {key: data.get(key) for key in (
        'id', 'provider', 'adapter_key', 'provider_workflow_id',
        'workflow_settings_key', 'supported_modes', 'default_mode',
        'reference_policy', 'parameters',
    )}
    if data.get('workflow_profile') is not None:
        result['workflow_profile'] = data['workflow_profile']
    return result


def validate_workflow_definition(payload, workflow):
    expected = payload.get('workflow_definition')
    if expected is not None and expected != workflow_execution_definition(workflow):
        raise ValueError('video workflow changed after submission; review settings and resubmit')


def group_output_is_complete(requested_segment_id, segments):
    """An isolated retry must never be published as a multi-segment group film."""
    return not requested_segment_id or (
        len(segments) == 1 and str(segments[0].get('id')) == requested_segment_id
    )
