"""Authorized semantic operations and their durable, pending canvas projections."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from uuid import uuid4

from pydantic import ValidationError

from novelvideo.freezone.canvas_store import read_canvas
from novelvideo.freezone.orchestration.graph import validate_graph
from novelvideo.freezone.orchestration.models import fingerprint, input_fingerprint
from .models import AssetRef, OperationRequest, OperationResult
from .store import ObjectStore


class OperationError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details

    def as_dict(self):
        return {'code': self.code, 'message': str(self), **self.details}


def _contract(condition, message='操作字段不符合约定'):
    if not condition:
        raise OperationError('invalid_contract', message)


def _scope(condition, **details):
    if not condition:
        raise OperationError('scope_violation', '操作超出授权范围', **details)


# Explicit editable inputs: output URLs, execution state and asset references are
# intentionally absent. Asset references have a separate validated operation.
DATA_FIELDS = frozenset({'prompt', 'negativePrompt', 'displayName', 'text', 'content',
    'model', 'aspectRatio', 'duration', 'operation', 'seed', 'temperature',
    'description', 'title', 'width', 'height'})
LAYOUT_FIELDS = frozenset({'position', 'parentId', 'style', 'width', 'height'})
TEXT_FIELDS = DATA_FIELDS - {'duration', 'seed', 'temperature', 'width', 'height'}


def _number(value, *, minimum, maximum, integer=False):
    # bool is an int subclass but is never a canvas coordinate or parameter.
    _contract(type(value) in ({int} if integer else {int, float}))
    _contract(minimum <= value <= maximum and math.isfinite(value))


def _layout(value):
    if 'position' in value:
        position = value['position']
        _contract(isinstance(position, dict) and set(position) == {'x', 'y'})
        for coordinate in position.values():
            _number(coordinate, minimum=-10_000_000, maximum=10_000_000)
    if 'parentId' in value:
        parent = value['parentId']
        # null is the explicit detach operation; empty string is the existing
        # canvas convention for a top-level node and remains accepted.
        _contract(parent is None or isinstance(parent, str) and len(parent) <= 256)
    for field in ('width', 'height'):
        if field in value:
            _number(value[field], minimum=0.01, maximum=65536)
    if 'style' in value:
        style = value['style']
        _contract(isinstance(style, dict) and set(style) <= {'width', 'height'})
        for size in style.values():
            _number(size, minimum=0.01, maximum=65536)


class OperationService:
    def __init__(self, objects: ObjectStore, *, project_id: str, authorize):
        self.objects, self.project_id, self.authorize = objects, project_id, authorize
        self.state_dir = objects.db_path.parent.parent

    def _authorized(self, actor, project_id):
        _scope(project_id == self.project_id and self.authorize(actor, project_id), project_id=project_id)

    def get(self, actor, operation_id):
        self._authorized(actor, self.project_id)
        with self.objects.transaction() as conn:
            row = conn.execute('SELECT result FROM operations WHERE id=?', (operation_id,)).fetchone()
            _scope(row is not None, operation_id=operation_id)
            return OperationResult.model_validate_json(row['result'])

    def asset_uses(self, actor, base_id):
        self._authorized(actor, self.project_id)
        with self.objects.transaction() as conn:
            _scope(ObjectStore._read(conn, base_id, None) is not None, base_asset_id=base_id)
            rows = conn.execute("SELECT payload FROM relations WHERE kind='asset_binding'").fetchall()
            return [value for row in rows if (value := json.loads(row['payload']))['asset_ref']['base_asset_id'] == base_id]

    def apply(self, actor, request: OperationRequest) -> OperationResult:
        self._authorized(actor, request.scope.project_id)
        scope = request.scope
        try:
            request_hash = fingerprint(request.model_dump(mode='json'))
        except (ValueError, TypeError) as exc:
            raise OperationError('invalid_contract', '操作字段必须是有限 JSON 值') from exc
        with self.objects.transaction() as conn:
            mapping = conn.execute('SELECT canvas_id FROM episode_canvases WHERE episode_id=?', (scope.episode_id,)).fetchone()
            _scope(mapping is not None and mapping['canvas_id'] == scope.canvas_id, episode_id=scope.episode_id, canvas_id=scope.canvas_id)
            # Replays precede mutable node/revision checks, but never permission
            # checks. Deleting a node must not invalidate an acknowledged retry.
            previous = conn.execute('SELECT * FROM operations WHERE request_id=?', (request.request_id,)).fetchone()
            if previous:
                if previous['request_hash'] != request_hash or previous['actor_id'] != actor:
                    raise OperationError('idempotency_conflict', '请求编号已用于不同操作', request_id=request.request_id)
                return OperationResult.model_validate_json(previous['result'])
            try:
                canvas = read_canvas(self.state_dir, scope.canvas_id)
            except ValueError as exc:
                raise OperationError('invalid_contract', '画布编号无效', canvas_id=scope.canvas_id) from exc
            _scope(canvas is not None and canvas.get('project_id') == self.project_id, canvas_id=scope.canvas_id)
            nodes = {node['id']: node for node in canvas['nodes']}
            if request.proposal_id:
                raise OperationError('approval_stale', '确认授权尚未验证', proposal_id=request.proposal_id)
            _contract(len(scope.node_ids) == len(set(scope.node_ids)))
            if request.kind != 'graph.patch':
                _scope(set(scope.node_ids) <= nodes.keys(), canvas_id=scope.canvas_id)
            operation_id = uuid4().hex
            relation_changes = []
            if request.kind == 'graph.patch':
                operations = self._graph_patch(request, canvas)
            elif request.kind == 'asset.bind':
                operations, relation_changes = self._asset_bind(conn, request, nodes, operation_id)
            elif request.kind in {'object.create', 'object.update'}:
                operations = None
                self._validate_object(conn, request)
            elif request.kind == 'node.run':
                _contract(set(request.payload) <= {'node_id', 'operation'} and isinstance(request.payload.get('node_id'), str))
                _scope(request.payload['node_id'] in nodes and request.payload['node_id'] in scope.node_ids)
                raise OperationError('capability_unavailable', '节点执行服务尚未接入', node_id=request.payload['node_id'])
            else:
                raise OperationError('capability_unavailable', '此操作服务尚未接入', kind=request.kind)
            expected = request.expected_revisions.get(scope.canvas_id)
            _contract(type(expected) is int and expected >= 1, '必须提供画布版本')
            for object_id, revision in request.expected_revisions.items():
                actual = canvas['revision'] if object_id == scope.canvas_id else (ObjectStore._read(conn, object_id, None) or {}).get('revision')
                _scope(actual is not None, object_id=object_id)
                if revision != actual:
                    raise OperationError('revision_conflict', '版本已变化', object_id=object_id, expected_revision=revision, current_revision=actual)
            if operations is None:
                ref = self._write_object(conn, actor, request)
                result = OperationResult(operation_id=operation_id, status='applied', object_refs=[ref], canvas_revision=expected)
                conn.execute('INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?)',
                             (operation_id, request.request_id, request_hash, 'applied', request.model_dump_json(), result.model_dump_json(), actor))
                return result
            before_nodes = {key: deepcopy(nodes[key]) for key in scope.node_ids if key in nodes}
            patch = {'schema_version': 1, 'operation_id': operation_id, 'scope': scope.model_dump(),
                     'base_revision': expected, 'operations': operations,
                     'before': {'nodes': before_nodes, 'edges': deepcopy(canvas['edges'])},
                     'relation_changes': relation_changes,
                     'input_guards': {key: input_fingerprint(node) for key, node in before_nodes.items()},
                     'touched_fields': [{'node_id': op.get('node_id', (op.get('node') or {}).get('id')),
                                         'fields': sorted(op.get('data', {})) + sorted(set(op) & LAYOUT_FIELDS),
                                         'op': op['op']} for op in operations]}
            result = OperationResult(operation_id=operation_id, status='running', canvas_revision=expected)
            conn.execute('INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?)',
                         (operation_id, request.request_id, request_hash, 'pending_projection', request.model_dump_json(), result.model_dump_json(), actor))
            conn.execute('INSERT INTO projection_outbox VALUES (?, ?, ?, NULL)',
                         (operation_id, scope.canvas_id, json.dumps(patch, ensure_ascii=False)))
            return result

    def _validate_object(self, conn, request):
        payload = request.payload
        creating = request.kind == 'object.create'
        _contract(set(payload) == ({'kind', 'data'} if creating else {'object_id', 'data'}))
        if creating:
            kind = payload['kind']
        else:
            _contract(isinstance(payload['object_id'], str))
            obj = ObjectStore._read(conn, payload['object_id'], None)
            _scope(obj is not None, object_id=payload['object_id'])
            kind = obj['kind']
            if kind in {'story_fragment', 'storyboard'}:
                _scope(obj['payload'].get('episode_id') == request.scope.episode_id, object_id=obj['id'])
                _contract(type(request.expected_revisions.get(obj['id'])) is int, '必须提供对象版本')
        if kind not in {'story_fragment', 'storyboard'}:
            raise OperationError('capability_unavailable', '此类对象须使用专用领域服务', kind=kind)
        data = payload['data']
        _contract(isinstance(data, dict) and bool(data) and set(data) <= {'title', 'text', 'description', 'notes', 'prompt'})
        _contract(all(isinstance(value, str) and len(value) <= 100000 for value in data.values()), '对象文本字段无效')

    def _write_object(self, conn, actor, request):
        if request.kind == 'object.create':
            object_id, revision = uuid4().hex, 1
            payload = {**request.payload['data'], 'episode_id': request.scope.episode_id}
            conn.execute('INSERT INTO objects (id, kind, current_revision) VALUES (?, ?, ?)',
                         (object_id, request.payload['kind'], revision))
        else:
            object_id = request.payload['object_id']
            old = ObjectStore._read(conn, object_id, None)
            revision = old['revision'] + 1
            payload = {**old['payload'], **request.payload['data']}
            conn.execute('UPDATE objects SET current_revision=? WHERE id=?', (revision, object_id))
        conn.execute('INSERT INTO object_versions VALUES (?, ?, ?, ?, ?)',
                     (object_id, revision, json.dumps(payload, ensure_ascii=False), actor, datetime.now(timezone.utc).isoformat()))
        return {'id': object_id, 'revision': revision}

    def _asset_bind(self, conn, request, nodes, operation_id):
        payload, scope = request.payload, request.scope
        _contract(set(payload) == {'node_id', 'asset_ref'})
        node_id = payload['node_id']
        _contract(isinstance(node_id, str))
        _scope(node_id in nodes and node_id in scope.node_ids, node_id=node_id)
        try:
            ref = AssetRef.model_validate(payload['asset_ref'])
        except ValidationError as exc:
            raise OperationError('invalid_contract', '资产引用字段无效') from exc
        base = ObjectStore._read(conn, ref.base_asset_id, None)
        target = ObjectStore._read(conn, ref.variant_id or ref.base_asset_id, ref.revision)
        _scope(base is not None and base['kind'] in {'character', 'scene'} and target is not None,
               base_asset_id=ref.base_asset_id)
        _scope(not base['variant_id'] and (not ref.variant_id or target['base_asset_id'] == ref.base_asset_id), base_asset_id=ref.base_asset_id)
        episode = ObjectStore._read(conn, scope.episode_id, None)
        _scope(episode is not None and episode['kind'] == 'episode', episode_id=scope.episode_id)
        relation_id = fingerprint(['asset_binding', scope.episode_id, scope.canvas_id, node_id])
        previous = conn.execute('SELECT * FROM relations WHERE id=?', (relation_id,)).fetchone()
        value = {**scope.model_dump(exclude={'node_ids'}), 'node_id': node_id,
                 'asset_ref': ref.model_dump(), 'operation_id': operation_id}
        serialized = json.dumps(value, ensure_ascii=False)
        conn.execute('INSERT INTO relations VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET target_id=excluded.target_id, payload=excluded.payload',
                     (relation_id, scope.episode_id, target['id'], 'asset_binding', serialized))
        # Keep exact persisted JSON, including ownership, as the compensation
        # CAS token. Never restore/delete a relation now owned by a later op.
        change = {'before': dict(previous) if previous else None,
                  'after': {'id': relation_id, 'source_id': scope.episode_id,
                            'target_id': target['id'], 'kind': 'asset_binding', 'payload': serialized}}
        return ([{'op': 'update_node', 'node_id': node_id, 'data': {'assetRef': ref.model_dump()}}], [change])

    def _graph_patch(self, request, canvas):
        _contract(set(request.payload) == {'operations'})
        operations = request.payload['operations']
        _contract(isinstance(operations, list) and 0 < len(operations) <= 2000)
        draft = deepcopy(canvas)
        scoped = set(request.scope.node_ids)
        for op in operations:
            _contract(isinstance(op, dict))
            kind = op.get('op')
            nodes = {node['id']: node for node in draft['nodes']}
            if kind == 'add_node':
                _contract(set(op) == {'op', 'node'} and isinstance(op['node'], dict))
                node = op['node']
                _contract(set(node) <= {'id', 'type', 'data'} | LAYOUT_FIELDS and isinstance(node.get('id'), str))
                _scope(node['id'] in scoped and node['id'] not in nodes, node_id=node['id'])
                self._data(node.get('data', {}))
                _layout(node)
                draft['nodes'].append(deepcopy(node))
            elif kind in {'update_node', 'remove_node'}:
                _contract(set(op) <= ({'op', 'node_id', 'data'} | LAYOUT_FIELDS if kind == 'update_node' else {'op', 'node_id'}))
                node_id = op.get('node_id')
                _contract(isinstance(node_id, str))
                _scope(node_id in scoped and node_id in nodes, node_id=node_id)
                if kind == 'remove_node':
                    # Deletion cannot silently affect unscoped adjacent nodes.
                    _scope(all({e['source'], e['target']} <= scoped for e in draft['edges'] if node_id in {e['source'], e['target']}))
                    draft['nodes'].remove(nodes[node_id])
                    draft['edges'] = [e for e in draft['edges'] if node_id not in {e['source'], e['target']}]
                else:
                    self._data(op.get('data', {}))
                    _layout(op)
                    nodes[node_id].setdefault('data', {}).update(op.get('data', {}))
                    for field in LAYOUT_FIELDS & op.keys():
                        nodes[node_id][field] = deepcopy(op[field])
            elif kind == 'add_edge':
                _contract(set(op) == {'op', 'edge'} and isinstance(op['edge'], dict))
                edge = op['edge']
                _contract(set(edge) <= {'id', 'source', 'target', 'sourceHandle', 'targetHandle', 'data'})
                _contract(isinstance(edge.get('data', {}), dict) and set(edge.get('data', {})) <= {'sourcePort', 'targetPort'})
                _scope(edge.get('source') in scoped and edge.get('target') in scoped)
                draft['edges'].append(deepcopy(edge))
            elif kind == 'remove_edge':
                _contract(set(op) == {'op', 'edge_id'})
                edge = next((e for e in draft['edges'] if e['id'] == op['edge_id']), None)
                _scope(edge is not None and edge['source'] in scoped and edge['target'] in scoped)
                draft['edges'].remove(edge)
            else:
                _contract(False)
        _scope(scoped <= {n['id'] for n in canvas['nodes']} | {op['node']['id'] for op in operations if op['op'] == 'add_node'})
        _contract(validate_graph(draft['nodes'], draft['edges'])['valid'], '画布结构无效')
        return deepcopy(operations)

    @staticmethod
    def _data(data):
        _contract(isinstance(data, dict) and set(data) <= DATA_FIELDS)
        for field, value in data.items():
            if field in TEXT_FIELDS:
                _contract(isinstance(value, str) and len(value) <= 100000)
            elif field == 'seed':
                _number(value, minimum=-1, maximum=2**53 - 1, integer=True)
            elif field == 'temperature':
                _number(value, minimum=0, maximum=2)
            elif field == 'duration':
                _number(value, minimum=0.001, maximum=86400)
            else:
                _number(value, minimum=1, maximum=65536, integer=True)
