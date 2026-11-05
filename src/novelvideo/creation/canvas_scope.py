"""Resolve trusted managed-canvas scope from the creation registry."""

from __future__ import annotations

from novelvideo.freezone.orchestration.models import OrchestrationError

from .canvas_workspaces import list_canvases


EPISODE_BOUND_NODE_TYPES = frozenset({
    'screenplayNode',
    'directorWorkspaceNode',
    'narrativeGroupStoryboardNode',
    'narrativeGroupVideoNode',
    'episodeComposeNode',
})


def resolve_canvas_scope(state_dir, canvas_id: str) -> dict | None:
    """Return a registry-backed scope, or None for an unregistered canvas.

    Episode ordinals are copied from ``list_episodes`` into the registry DTO by
    ``list_canvases``. Missing or ambiguous mappings remain ``None``.
    """
    matches = [item for item in list_canvases(state_dir) if item.get('canvas_id') == canvas_id]
    if len(matches) != 1:
        return None
    item = matches[0]
    return {
        'canvas_id': item['canvas_id'],
        'kind': item['kind'],
        'episode_id': item.get('episode_id'),
        'episode_number': item.get('episode_number'),
        'status': item.get('status'),
    }


def require_episode_canvas_scope(state_dir, canvas_id: str, episode_number: int) -> dict:
    """Require a registered episode canvas with the requested canonical number."""
    scope = resolve_canvas_scope(state_dir, canvas_id)
    if (scope is None or scope.get('kind') != 'episode'
            or not isinstance(scope.get('episode_id'), str) or not scope['episode_id']
            or type(scope.get('episode_number')) is not int
            or scope['episode_number'] != episode_number
            or scope.get('status') != 'applied'):
        raise OrchestrationError(
            'episode_scope_mismatch',
            'The requested episode does not match this registered canvas',
            canvas_id=canvas_id,
            episode_number=episode_number,
        )
    return scope


def validate_snapshot_workspace_scope(canvas: dict, workspace_scope: dict | None,
                                      node_ids: list[str] | None = None) -> None:
    """Reject executed episode-bound nodes that disagree with trusted scope."""
    canvas_id = canvas.get('canvas_id')
    if workspace_scope is not None and (
        not isinstance(workspace_scope, dict)
        or workspace_scope.get('canvas_id') != canvas_id
        or workspace_scope.get('kind') not in {'concept', 'episode', 'free'}
    ):
        raise OrchestrationError('episode_scope_mismatch', 'The registered workspace scope does not match this canvas',
                                 canvas_id=canvas_id)
    if workspace_scope is not None and workspace_scope.get('status') != 'applied':
        raise OrchestrationError('episode_scope_mismatch', 'The registered workspace is not ready for execution',
                                 canvas_id=canvas_id)

    selected_ids = set(node_ids) if node_ids is not None else None
    nodes = [node for node in canvas.get('nodes', [])
             if isinstance(node, dict) and node.get('type') in EPISODE_BOUND_NODE_TYPES
             and (selected_ids is None or node.get('id') in selected_ids)]
    if not nodes:
        return
    if (workspace_scope is None or workspace_scope.get('kind') != 'episode'
            or type(workspace_scope.get('episode_number')) is not int
            or workspace_scope['episode_number'] < 1
            or not isinstance(workspace_scope.get('episode_id'), str)
            or not workspace_scope['episode_id']):
        raise OrchestrationError('episode_scope_mismatch', 'Episode-bound nodes require a registered episode canvas',
                                 canvas_id=canvas_id)

    canonical_number = workspace_scope['episode_number']
    for node in nodes:
        data = node.get('data')
        if not isinstance(data, dict) or type(data.get('episodeNumber')) is not int \
                or data['episodeNumber'] != canonical_number:
            raise OrchestrationError(
                'episode_scope_mismatch',
                'An episode-bound node does not match the registered canvas episode',
                canvas_id=canvas_id,
                node_id=node.get('id'),
                episode_number=canonical_number,
            )
