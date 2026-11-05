"""Verified, read-only execution inputs for asset-design image nodes."""

from __future__ import annotations

from copy import deepcopy

from .models import (OrchestrationError, dependency_edge_fingerprint, fingerprint,
                     frozen_node_result, input_fingerprint)


def prepare_asset_design_execution(ctx, canvas: dict, target: str,
                                   operation: str | None) -> dict | None:
    """Build a run-local frozen-input marker from an applied asset-design receipt.

    This is intentionally limited to generated imageGen nodes with a durable,
    verified asset-design receipt. Ordinary canvas image nodes continue to use
    the full live dependency scheduler.
    """
    node = next((item for item in canvas.get('nodes', []) if item.get('id') == target), None)
    if (node is None or node.get('type') != 'imageGenNode' or operation != 'generate'
            or not isinstance((node.get('data') or {}).get('designBinding'), dict)):
        return None

    from novelvideo.creation.asset_generation import AssetGeneration

    candidate = AssetGeneration.verified_candidate_for_node(ctx, canvas.get('canvas_id'), node)
    if candidate is None:
        raise OrchestrationError('asset_design_binding_unverified',
                                 '资产节点的设计来源无法复核，已停止提交', node_id=target)

    references = node['data']['designBinding'].get('references')
    if not isinstance(references, list):
        raise OrchestrationError('asset_design_binding_unverified',
                                 '资产节点的冻结参考格式无法复核', node_id=target)
    canvas_references = [item for item in references
                         if isinstance(item, dict) and item.get('kind') == 'canvas_node']
    if not canvas_references:
        return None

    nodes = {item.get('id'): item for item in canvas.get('nodes', [])}
    direct_edges = {edge.get('source') for edge in canvas.get('edges', [])
                    if edge.get('target') == target
                    and not (edge.get('data') or {}).get('contextLink')}
    frozen_inputs = []
    reference_input_fingerprints = {}
    source_output_fingerprints = {}
    for reference in canvas_references:
        source_id = reference.get('node_id')
        source = nodes.get(source_id)
        result = frozen_node_result(source) if isinstance(source, dict) else {}
        if (not isinstance(source_id, str) or source_id in reference_input_fingerprints
                or source_id not in direct_edges or not isinstance(source, dict)
                or source.get('type') != reference.get('node_type')
                or not isinstance(reference.get('input_fingerprint'), str)
                or not isinstance(result.get('imageUrl'), str)):
            raise OrchestrationError('asset_design_binding_unverified',
                                     '资产节点的冻结图片参考无法复核', node_id=target)
        reference_input_fingerprints[source_id] = reference['input_fingerprint']
        source_output_fingerprints[source_id] = fingerprint(result)
        frozen_inputs.append({'node_id': source_id, 'status': 'succeeded', 'result': deepcopy(result)})

    return {
        'candidate': deepcopy(candidate),
        'reference_ids': [item['node_id'] for item in frozen_inputs],
        'reference_input_fingerprints': reference_input_fingerprints,
        'source_output_fingerprints': source_output_fingerprints,
        'frozen_inputs': frozen_inputs,
        'dependency_edge_fingerprint': dependency_edge_fingerprint(canvas, target),
    }


def verify_asset_design_execution(ctx, run: dict, current_canvas: dict) -> None:
    """Revalidate the durable receipt and frozen source fingerprints at a run fence."""
    marker = run.get('asset_design_execution')
    if not isinstance(marker, dict):
        raise OrchestrationError('asset_design_binding_unverified',
                                 '缺少资产设计节点的冻结来源回执')
    target_id = run.get('target')
    snapshot_node = next((item for item in run.get('snapshot', {}).get('nodes', [])
                          if item.get('id') == target_id), None)
    current_node = next((item for item in current_canvas.get('nodes', [])
                         if item.get('id') == target_id), None)
    if (snapshot_node is None or current_node is None
            or input_fingerprint(current_node) != run.get('input_fingerprints', {}).get(target_id)
            or dependency_edge_fingerprint(current_canvas, target_id)
            != marker.get('dependency_edge_fingerprint')):
        raise OrchestrationError('input_conflict',
                                 '资产设计节点或其冻结参考边已变化，请刷新后重新准备', node_id=target_id)

    from novelvideo.creation.asset_generation import AssetGeneration

    candidate = AssetGeneration.verified_candidate_for_node(ctx, run.get('canvas_id'), snapshot_node)
    if candidate is None or candidate != marker.get('candidate'):
        raise OrchestrationError('asset_design_binding_unverified',
                                 '资产节点的设计来源已变化或无法复核，已停止提交', node_id=target_id)

    references = (current_node.get('data') or {}).get('designBinding', {}).get('references')
    if not isinstance(references, list):
        raise OrchestrationError('asset_design_binding_unverified',
                                 '资产节点的冻结参考格式无法复核', node_id=target_id)
    canvas_references = [item for item in references
                         if isinstance(item, dict) and item.get('kind') == 'canvas_node']
    expected_ids = [item.get('node_id') for item in canvas_references]
    if (expected_ids != marker.get('reference_ids')
            or {item.get('node_id'): item.get('input_fingerprint') for item in canvas_references}
            != marker.get('reference_input_fingerprints')):
        raise OrchestrationError('asset_design_binding_unverified',
                                 '资产节点的冻结参考范围与准备回执不一致', node_id=target_id)

    nodes = {item.get('id'): item for item in current_canvas.get('nodes', [])}
    frozen_inputs = {item.get('node_id'): item for item in marker.get('frozen_inputs', [])
                     if isinstance(item, dict)}
    if set(frozen_inputs) != set(expected_ids):
        raise OrchestrationError('asset_design_binding_unverified',
                                 '资产节点的冻结输入回执不完整', node_id=target_id)
    for source_id in expected_ids:
        source = nodes.get(source_id)
        result = frozen_node_result(source) if isinstance(source, dict) else {}
        frozen_input = frozen_inputs[source_id]
        if (not isinstance(source, dict) or frozen_input.get('status') != 'succeeded'
                or frozen_input.get('result') != result
                or marker.get('source_output_fingerprints', {}).get(source_id) != fingerprint(result)):
            raise OrchestrationError('input_conflict',
                                     '冻结的图片参考在生成期间发生变化，已停止提交', node_id=source_id)
