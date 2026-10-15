"""Read-only, server-owned inputs for a bounded Agent delegation.

Snapshots are context, not tool authorization. In particular text containing a
URL is never resolved or opened by this module.
"""
from types import SimpleNamespace

from fastapi import HTTPException

from novelvideo.chat.conversation_binding import canvas_evidence, validate_conversation
from novelvideo.chat.session_store import conversation_scope, for_scope
from novelvideo.freezone.canvas_store import read_canvas
from novelvideo.freezone.orchestration.models import RESULT_FIELDS, input_fingerprint
from .asset_reference import AssetReferences
from .creative_constraints import CreativeConstraints
from .library_references import DirectionReferences, DirectionScope, digest
from .ontology import Ontology, exact, integer, require, text
from .operations import OperationError


TEXT_FIELDS = frozenset({'text', 'content', 'prompt', 'title', 'label', 'displayName',
                        'description', 'negativePrompt', 'script', 'instructions'})


def required_canvases(ctx, conn, body):
    """Find only actual graph dependencies; this is not a grant or snapshot."""
    ids = {body['canvas_id']} if isinstance(body.get('canvas_id'), str) else set()
    refs = body.get('input_refs', [])
    require(isinstance(refs, list), '输入引用列表无效')
    for ref in refs:
        require(isinstance(ref, dict), '输入引用无效')
        if ref.get('kind') == 'canvas_node' and isinstance(ref.get('canvas_id'), str):
            ids.add(ref['canvas_id'])
        elif ref.get('kind') == 'object':
            owner = {'level': 'object', 'object_id': ref.get('object_id')}
            seen = set()
            while owner.get('level') == 'object':
                identifier = owner.get('object_id')
                text(identifier, 256)
                require(identifier not in seen, '对象归属链循环', 'scope_violation')
                seen.add(identifier)
                owner = Ontology(ctx)._object(conn, identifier)['payload']['scope']
            if owner.get('canvas_id'):
                ids.add(owner['canvas_id'])
    return ids


def conversation(ctx, username, canvas_id, conversation_id):
    """Reuse transport ownership checks before any session read (never create)."""
    text(canvas_id, 64)
    text(conversation_id, 64)
    require(conversation_id != 'default', '委托需要已登记的独立画布会话', 'scope_violation')
    try:
        with canvas_evidence(ctx.project_id, canvas_id), conversation_scope(conversation_id):
            validate_conversation({'username': username},
                                  SimpleNamespace(kind='project', id=ctx.project_id), ctx)
        state, _ = for_scope(username, 'project', ctx.project_id,
                             conversation_id=conversation_id).peek()
    except (HTTPException, ValueError) as exc:
        raise OperationError('scope_violation', '会话不属于当前用户和画布或尚未准备好') from exc
    require(state is not None, '会话尚未准备好，请恢复原创建请求', 'scope_violation')
    return {'session_id': state.session_id, 'generation': state.generation}


def node_snapshot(node):
    data = node.get('data', {})
    media_keys = set(RESULT_FIELDS.get(node['type'], set())) | {
        'imageUrl', 'previewImageUrl', 'panoUrl', 'plyUrl', 'videoUrl', 'audioUrl',
        'sources', 'assetRef', 'assetMediaSelection', 'scene', 'scenesBySourceId', 'activeSourceId'}
    media = {key: data[key] for key in sorted(media_keys) if key in data}
    return {'node_id': node['id'], 'node_type': node['type'],
            'parent_id': node.get('parentId'), 'input_fingerprint': input_fingerprint(node),
            'media_evidence': {'status': 'not_evaluated', 'byte_stability': 'not_archived',
                               'fields': sorted(media), 'locator_fingerprint': digest(media),
                               'notice': '仅固定节点媒体字段证据；读取字节前须核验受控来源，不授予任意 URL 或文件访问'},
            'content': {k: v for k, v in node.get('data', {}).items()
                        if k in TEXT_FIELDS and isinstance(v, str)}}


def graph_scope(ctx, canvas_id, scope):
    require(isinstance(scope, dict), '委托范围无效')
    kind = scope.get('kind')
    required = {'canvas': {'kind'}, 'nodes': {'kind', 'node_ids'}, 'group': {'kind', 'group_id'}}
    require(kind in required, '请明确画布、分组或节点范围')
    exact(scope, required[kind])
    refs = DirectionReferences(ctx)
    refs._chain(DirectionScope(level='canvas', canvas_id=canvas_id))
    canvas = read_canvas(ctx.state_dir, canvas_id)
    nodes = {n['id']: n for n in canvas['nodes']}
    require(len(nodes) == len(canvas['nodes']), '画布包含重复节点', 'scope_violation')
    if kind == 'nodes':
        selected = scope['node_ids']
        require(isinstance(selected, list) and 0 < len(selected) <= 1000
                and all(isinstance(x, str) for x in selected)
                and len(set(selected)) == len(selected), '请选择明确且不重复的节点')
    elif kind == 'group':
        group = scope['group_id']
        require(isinstance(group, str) and nodes.get(group, {}).get('type') == 'groupNode',
                '目标不是当前画布的真实分组', 'scope_violation')
        selected = [group]
        # Validate actual ancestry, including disconnected/cyclic graph corruption.
        for node_id in nodes:
            chain, _ = refs._chain(DirectionScope(level='node', canvas_id=canvas_id, node_id=node_id))
            if node_id != group and any(x.get('group_id') == group for x in chain):
                selected.append(node_id)
    else:
        selected = list(nodes)
    snapshots, chains = [], []
    for node_id in sorted(selected):
        require(node_id in nodes, '目标节点不存在', 'scope_violation')
        _, evidence = refs._chain(DirectionScope(level='node', canvas_id=canvas_id, node_id=node_id))
        chains.append({'node_id': node_id, 'members': evidence['members']})
        snapshots.append(node_snapshot(nodes[node_id]))
    return {'scope': {**scope, 'canvas_id': canvas_id}, 'node_ids': sorted(selected),
            'nodes': snapshots, 'membership': chains, 'observed_canvas_revision': canvas['revision']}


def inputs(ctx, conn, values):
    require(isinstance(values, list) and len(values) <= 100, '输入引用数量无效')
    result = []
    for ref in values:
        require(isinstance(ref, dict), '输入引用无效')
        kind = ref.get('kind')
        if kind == 'canvas_node':
            exact(ref, {'kind', 'canvas_id', 'node_id'})
            snapshot = graph_scope(ctx, ref['canvas_id'], {'kind': 'nodes', 'node_ids': [ref['node_id']]})
            value = {'ref': ref, 'snapshot': snapshot['nodes'][0], 'membership': snapshot['membership']}
        elif kind == 'object':
            exact(ref, {'kind', 'object_id', 'revision'})
            integer(ref['revision'], 1)
            obj = Ontology(ctx)._object(conn, ref['object_id'], ref['revision'])
            payload = obj['payload']
            definition = Ontology(ctx).definition(payload['definition_name'], payload['schema_revision'], conn)
            value = {'ref': ref, 'snapshot': obj, 'definition': definition}
        elif kind == 'asset':
            exact(ref, {'kind', 'asset_ref', 'slot_id', 'role'})
            asset = AssetReferences(ctx.state_dir, ctx.output_dir, ctx.project_id).resolve(ref['asset_ref'])
            media = next((m for m in asset['object']['payload'].get('_media', [])
                          if m['slot_id'] == ref['slot_id']
                          and m.get('media_role', m['slot_id'].rsplit(':', 1)[-1]) == ref['role']), None)
            require(media is not None and media.get('current_blob'), '固定资产缺少指定媒体', 'scope_violation')
            # Paths remain opaque fixed evidence, never a generic file-read permission.
            value = {'ref': ref, 'snapshot': {'kind': asset['object']['kind'], 'media': media,
                                            'mapping': asset['mapping']}}
        else:
            require(False, '仅支持明确的节点、固定自定义对象或固定资产引用')
        require(not any(digest(item['ref']) == digest(ref) for item in result), '输入引用重复')
        result.append(value)
    return result


def context(ctx, conn, canvas_id, expanded, fixed_inputs):
    constraints = CreativeConstraints(ctx)
    references = DirectionReferences(ctx)
    targets = [{'level': 'node', 'canvas_id': canvas_id, 'node_id': n} for n in expanded['node_ids']]
    if not targets:
        targets = [{'level': 'canvas', 'canvas_id': canvas_id}]
    targets += [{'level': 'object', 'object_id': item['ref']['object_id']}
                for item in fixed_inputs if item['ref']['kind'] == 'object']
    for item in fixed_inputs:
        if item['ref']['kind'] == 'asset':
            # AssetReferences already validated this mapping and fixed version.
            # Resolve base and variant independently under the existing scope
            # rules; including a base does not invent inheritance for variants.
            mapping = item['snapshot']['mapping']
            targets += [{'level': 'object', 'object_id': identifier}
                        for identifier in (mapping['parent_id'], mapping['object_id']) if identifier]
    targets = list({digest(target): target for target in targets}.values())
    records = constraints._all(conn)
    result = []
    for target in targets:
        normalized, chain, evidence = constraints.scope(target)
        agreements = constraints._resolve(records, normalized, chain, evidence)
        # Revision-only layout changes must not stale a semantic selection.
        agreements.pop('scope_proof', None)
        entry = {'scope': target, 'agreements': agreements}
        if target['level'] != 'object':
            direction = references.inspect(target, conn=conn)
            direction.pop('proof', None)
            entry['direction'] = direction
        result.append(entry)
    return result
