"""Read-only resolution of an explicitly adopted ideation candidate."""

import json

from novelvideo.freezone.canvas_store import read_canvas

from .asset_reference import table
from .ideation import validate_candidates
from .ontology import require
from .operations import OperationError
from .store import ObjectStore


_REF_FIELDS = frozenset({
    'kind', 'request_id', 'canvas_id', 'conversation_id', 'node_id',
    'object_id', 'revision', 'candidate_id',
})
_REQUEST_FIELDS = frozenset({
    'object_id', 'canvas_id', 'conversation_id', 'node_id',
    'expected_revision', 'candidate_id', 'proof',
})
_RESULT_FIELDS = frozenset({
    'request_id', 'status', 'operation_id', 'object_ref', 'candidate_id',
    'origin', 'selection',
})
_SELECTION_FIELDS = frozenset({
    'object_id', 'revision', 'candidate_id', 'node_id', 'run_id', 'source',
})


def _scope(condition, message):
    require(condition, message, 'scope_violation')


def _json(raw, label):
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise OperationError('scope_violation', f'{label}记录损坏') from exc
    _scope(isinstance(value, dict), f'{label}记录损坏')
    return value


def resolve(ctx, conn, ref, *, username, current_canvas_id):
    """Resolve one candidate using only an applied adoption receipt and caller DB connection."""
    _scope(isinstance(ref, dict) and set(ref) == _REF_FIELDS, '采纳引用字段无效')
    _scope(ref.get('kind') == 'ideation_selection', '输入不是固定采纳引用')
    for field in ('request_id', 'canvas_id', 'conversation_id', 'node_id', 'object_id', 'candidate_id'):
        _scope(isinstance(ref.get(field), str) and 0 < len(ref[field].strip()) <= 256,
               '采纳引用字段无效')
    revision = ref.get('revision')
    _scope(type(revision) is int and revision >= 1, '采纳引用版本无效')
    _scope(ref['canvas_id'] == current_canvas_id, '采纳引用必须来自当前画布')
    _scope(conn is not None and all(table(conn, name) for name in (
        'creative_domain_receipts', 'operations', 'objects', 'object_versions')),
        '采纳引用没有可验证的持久记录')

    receipt = conn.execute('SELECT * FROM creative_domain_receipts WHERE request_id=?',
                           (ref['request_id'],)).fetchone()
    operation = conn.execute('SELECT * FROM operations WHERE request_id=?',
                              (ref['request_id'],)).fetchone()
    _scope(receipt is not None and operation is not None, '采纳请求不存在')
    _scope(receipt['project_id'] == ctx.project_id and receipt['domain'] == 'ideation',
           '采纳回执不属于当前项目')
    _scope(operation['actor_id'] == ctx.requester_user_id and operation['state'] == 'applied',
           '采纳操作未由当前用户应用')
    _scope(operation['request_id'] == receipt['request_id'] == ref['request_id']
           and operation['request_hash'] == receipt['request_hash'],
           '采纳请求与回执不一致')

    stored = _json(operation['payload'], '采纳操作')
    result = _json(receipt['result'], '采纳回执')
    operation_result = _json(operation['result'], '采纳操作结果')
    request = stored.get('request')
    payload = request.get('payload') if isinstance(request, dict) else None
    _scope(stored.get('kind') == 'ideation.adopt'
           and stored.get('scope') == {'project_id': ctx.project_id}
           and stored.get('source') == {'kind': 'user', 'actor_id': ctx.requester_user_id},
           '请求不是当前用户在本项目执行的采纳操作')
    _scope(isinstance(request, dict) and request.get('action') == 'adopt'
           and request.get('request_id') == ref['request_id']
           and isinstance(payload, dict) and set(payload) == _REQUEST_FIELDS
           and isinstance(payload.get('proof'), str) and payload['proof'],
           '采纳操作请求字段不一致')
    _scope(payload.get('canvas_id') == ref['canvas_id']
           and payload.get('conversation_id') == ref['conversation_id']
           and payload.get('node_id') == ref['node_id']
           and payload.get('object_id') == ref['object_id']
           and payload.get('expected_revision') == revision
           and payload.get('candidate_id') == ref['candidate_id'],
           '采纳引用与原始请求范围不一致')

    selection = result.get('selection')
    object_ref = result.get('object_ref')
    _scope(result.keys() == _RESULT_FIELDS and result.get('request_id') == ref['request_id']
           and result.get('operation_id') == operation['id']
           and result.get('status') == 'applied'
           and result.get('candidate_id') == ref['candidate_id']
           and object_ref == {'object_id': ref['object_id'], 'revision': revision},
           '采纳回执结果与原始请求不一致')
    _scope(isinstance(selection, dict) and selection.keys() == _SELECTION_FIELDS
           and selection.get('object_id') == ref['object_id']
           and selection.get('revision') == revision
           and selection.get('candidate_id') == ref['candidate_id']
           and selection.get('node_id') == ref['node_id']
           and isinstance(selection.get('source'), dict),
           '固定采纳选择与回执不一致')
    refs = operation_result.get('object_refs')
    _scope(operation_result.get('operation_id') == operation['id']
           and operation_result.get('status') == 'applied'
           and refs == [result], '采纳操作结果与领域回执不一致')

    # The historical conversation is independently checked against the trusted
    # user and canvas. It may differ from the current delegation's conversation.
    from .agent_context import conversation
    conversation(ctx, username, ref['canvas_id'], ref['conversation_id'])

    try:
        canvas = read_canvas(ctx.state_dir, ref['canvas_id'])
    except (OSError, ValueError) as exc:
        raise OperationError('scope_violation', '采纳引用的画布无法读取') from exc
    _scope(canvas is not None and canvas.get('project_id') == ctx.project_id,
           '采纳引用的画布不属于当前项目')
    nodes = [node for node in canvas.get('nodes', [])
             if isinstance(node, dict) and node.get('id') == ref['node_id']]
    _scope(len(nodes) == 1,
           '采纳引用的 Agent 节点已删除或不属于原画布')

    obj = ObjectStore._read(conn, ref['object_id'], revision)
    _scope(obj is not None and obj['revision'] == revision and obj['kind'] == 'story_fragment'
           and obj['payload'].get('canvas_id') == ref['canvas_id'],
           '采纳候选对象版本不存在或不属于原画布')
    try:
        candidates = validate_candidates(json.loads(obj['payload']['text']))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise OperationError('scope_violation', '采纳候选版本内容损坏') from exc
    candidate = next((value for value in candidates if value['id'] == ref['candidate_id']), None)
    _scope(candidate is not None, '采纳候选在固定版本中不存在')

    return {
        'ref': dict(ref),
        'snapshot': {
            'candidate': candidate,
            'provenance': {
                'request_id': ref['request_id'],
                'operation_id': result['operation_id'],
                'canvas_id': ref['canvas_id'],
                'conversation_id': ref['conversation_id'],
                'node_id': ref['node_id'],
                'object_ref': object_ref,
                'candidate_id': ref['candidate_id'],
                'origin': result.get('origin'),
                'source': selection['source'],
            },
        },
    }
