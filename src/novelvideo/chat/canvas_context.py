"""Versioned canvas references and non-destructive chat edits."""
from copy import deepcopy
from pathlib import Path

from novelvideo.freezone.canvas_store import read_canvas, save_canvas, utc_now_iso
from novelvideo.freezone.orchestration.models import OrchestrationError, fingerprint, PRESENTATION_FIELDS, RESULT_FIELDS
from novelvideo.freezone.orchestration.adapters import available_operations

EDITABLE = frozenset({'prompt', 'text', 'content', 'sourceText', 'displayName', 'model',
    'aspectRatio', 'durationSec', 'resolution', 'imageSize', 'quality', 'generateAudio'})


def node_fingerprint(node):
    # Generated media is part of a reference even when it is not a generator input.
    return fingerprint({'type': node['type'], 'data': {k: v for k, v in node.get('data', {}).items()
                        if k not in PRESENTATION_FIELDS}})


def reference_snapshot(root: Path, canvas_id: str, node_ids: list[str]) -> dict:
    if not 1 <= len(node_ids) <= 20 or len(set(node_ids)) != len(node_ids):
        raise ValueError('每次引用 1–20 个不同节点')
    canvas = read_canvas(root, canvas_id)
    if canvas is None:
        raise OrchestrationError('canvas_not_found', '来源画布不存在')
    found = {n['id']: n for n in canvas['nodes']}
    nodes = []
    for node_id in node_ids:
        if node_id not in found:
            raise OrchestrationError('node_not_found', '引用节点已删除')
        node = found[node_id]
        nodes.append({'id': node_id, 'type': node['type'], 'fingerprint': node_fingerprint(node),
                      'data': deepcopy(node.get('data', {})),
                      'editable_fields': sorted(EDITABLE),
                      'operations': sorted(op for kind, op in available_operations() if kind == node['type'])})
    return {'canvas_id': canvas_id, 'revision': canvas['revision'], 'nodes': nodes}


def apply_context_edit(root: Path, canvas_id: str, *, node_id: str, expected_fingerprint: str,
                       request_id: str, action: str, changes: dict, operation: str | None = None,
                       reference_media: list[dict] | None = None) -> dict:
    if action not in {'update', 'generate'} or not request_id or len(request_id) > 128:
        raise ValueError('无效的画布操作')
    if not changes or set(changes) - EDITABLE:
        raise ValueError('只能修改节点公开的创作字段，不能改写产物或身份')
    for key, value in changes.items():
        if key == 'generateAudio':
            if not isinstance(value, bool):
                raise ValueError('generateAudio 必须是布尔值')
        elif key == 'durationSec':
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 300:
                raise ValueError('时长必须在 0–300 秒之间')
        elif not isinstance(value, str) or len(value) > 20000:
            raise ValueError('创作字段必须是长度不超过 20000 的文本')
    target_id = 'chat-' + fingerprint([canvas_id, request_id])[:24] if action == 'generate' else node_id

    def build(existing):
        if existing is None:
            raise OrchestrationError('canvas_not_found', '来源画布不存在')
        result = deepcopy(existing)
        source = next((n for n in result['nodes'] if n['id'] == node_id), None)
        if source is None or node_fingerprint(source) != expected_fingerprint:
            raise OrchestrationError('input_conflict', '来源已变化，请重新引用后再修改')
        if action == 'update':
            source.setdefault('data', {}).update(changes)
        else:
            kind = source['type']
            if (kind, operation) not in available_operations():
                raise ValueError('该节点未注册所选生成操作')
            data = deepcopy(source.get('data', {}))
            provider = (data.get('request') or {}).get('provider')
            media = {k: data.get(k) for k in ('imageUrl', 'videoUrl', 'audioUrl')}
            for key in RESULT_FIELDS.get(kind, set()) | PRESENTATION_FIELDS | {'referenceOnly', 'request'}:
                data.pop(key, None)
            data.update(changes)
            data.update(operation=operation, displayName=f"对话候选 · {changes.get('displayName') or source.get('data', {}).get('displayName') or node_id}",
                        chatOrigin={'canvas_id': canvas_id, 'node_id': node_id, 'fingerprint': expected_fingerprint, 'request_id': request_id})
            if operation == 'edit' and kind in {'imageNode', 'imageGenNode'}:
                if not media['imageUrl']:
                    raise ValueError('来源图片尚未生成')
                data['request'] = {'base_url': media['imageUrl']}
            if kind == 'videoNode' and media['videoUrl']:
                data['request'] = {'video_url': media['videoUrl']}
            references = list({(item['type'], item['url']): item for item in reference_media or []}.values())
            images = [item['url'] for item in references if item['type'] == 'image']
            request = data.setdefault('request', {})
            if provider:
                request['provider'] = provider
            if kind in {'imageNode', 'imageGenNode', 'storyboardGenNode'}:
                request['extra_reference_urls' if operation == 'edit' else 'reference_urls'] = images
            elif kind == 'videoNode':
                if operation == 'reference':
                    request['references'] = references
                elif operation == 'image_to_video':
                    request['image_urls'] = images
                elif operation == 'keyframes' and images:
                    request.update(first_frame_url=images[0], last_frame_url=images[1] if len(images) > 1 else None)
            result['nodes'].append({'id': target_id, 'type': kind, 'data': data,
                'position': {'x': source.get('position', {}).get('x', 0) + 460,
                             'y': source.get('position', {}).get('y', 0)}})
            # A provenance link is visible but must not regenerate its source.
            result['edges'].append({'id': 'origin-' + target_id, 'source': node_id, 'target': target_id,
                'sourceHandle': 'source', 'targetHandle': 'target', 'data': {'contextLink': True}})
        result['revision'] += 1
        result['updated_at'] = utc_now_iso()
        result['save_source'] = 'chat'
        return result

    save_canvas(root, canvas_id, base_revision=None, enforce_revision=False, build_payload=build,
                client_save_id='chat-' + request_id,
                request_hash=fingerprint([node_id, expected_fingerprint, action, changes, operation, reference_media or []]), save_source='chat')
    return {'canvas_id': canvas_id, 'node_id': target_id, 'action': action}


def place_result(root: Path, canvas_id: str, *, node_id: str, expected_fingerprint: str,
                 source_node_id: str, expected_source_fingerprint: str, request_id: str) -> dict:
    """Explicitly reattach a preserved candidate without generating new media."""
    if node_id == source_node_id:
        raise ValueError('候选结果不能关联到自己')

    def build(existing):
        if existing is None:
            raise OrchestrationError('canvas_not_found', '画布不存在')
        result = deepcopy(existing)
        by_id = {item['id']: item for item in result['nodes']}
        candidate, source = by_id.get(node_id), by_id.get(source_node_id)
        if candidate is None or source is None:
            raise OrchestrationError('node_not_found', '候选或目标节点已删除，请重新选择')
        if node_fingerprint(candidate) != expected_fingerprint or node_fingerprint(source) != expected_source_fingerprint:
            raise OrchestrationError('input_conflict', '候选或目标节点已变化，请重新引用')
        data = candidate.setdefault('data', {})
        status = data.get('orchestration') or {}
        if status.get('status') != 'source_changed' or not isinstance(data.get('chatOrigin'), dict):
            raise ValueError('仅来源已变化的对话候选需要重新放置')
        data['chatOrigin'] = {**data['chatOrigin'], 'canvas_id': canvas_id,
                              'node_id': source_node_id, 'fingerprint': expected_source_fingerprint}
        data['orchestration'] = {'attempt_id': status.get('attempt_id'), 'status': 'succeeded',
                                 'source_node_id': source_node_id, 'placement_request_id': request_id}
        data['displayName'] = str(data.get('displayName') or node_id).removesuffix(' · 来源已变化，待选择放置位置')
        candidate['position'] = {'x': source.get('position', {}).get('x', 0) + 460,
                                 'y': source.get('position', {}).get('y', 0)}
        result['edges'] = [edge for edge in result['edges'] if not (
            (edge.get('data') or {}).get('contextLink') and edge.get('target') == node_id)]
        result['edges'].append({'id': 'origin-' + node_id, 'source': source_node_id, 'target': node_id,
                               'sourceHandle': 'source', 'targetHandle': 'target', 'data': {'contextLink': True}})
        result['revision'] += 1
        result['updated_at'] = utc_now_iso()
        result['save_source'] = 'chat'
        return result

    save_canvas(root, canvas_id, base_revision=None, enforce_revision=False, build_payload=build,
                client_save_id='chat-place-' + request_id,
                request_hash=fingerprint([node_id, expected_fingerprint, source_node_id, expected_source_fingerprint]),
                save_source='chat')
    return {'canvas_id': canvas_id, 'node_id': node_id, 'source_node_id': source_node_id, 'status': 'succeeded'}
