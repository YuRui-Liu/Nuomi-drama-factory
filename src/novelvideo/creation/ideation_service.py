"""Scoped editing and explicit adoption for agent-produced idea candidates."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json

import portalocker

from novelvideo.freezone.canvas_lock import CanvasLockBusy, canvas_lock_path
from novelvideo.freezone.canvas_store import read_canvas
from novelvideo.production_workflow.coordination import asset_project_lock
from . import agent_context
from .agent_runs import AgentRuns
from .asset_adapter import registration_io
from .asset_reference import table
from .canvas_workspaces import list_canvases
from .delegations import Delegations
from .ideation import validate_candidates
from .library_references import digest, packed
from .ontology import ProjectDomain, DomainCommand, exact, integer, require, text
from .operations import OperationError
from .store import ObjectStore


_BODY_FIELDS = {'title', 'premise', 'protagonist_goal', 'conflict', 'hook', 'expansion'}
_LAYOUT_FIELDS = {'position', 'positionAbsolute', 'width', 'height', 'x', 'y',
                  'measured', 'selected', 'dragging'}


def _object_ref(value):
    return {'object_id': value['id'], 'revision': value['revision']}


class IdeationService(ProjectDomain):
    """Treat one verified proposal output as the root of all edited versions."""

    domain = 'ideation'

    def __init__(self, ctx, *, username, canvas_id, conversation_id, resolve_context):
        super().__init__(ctx, authority='user')
        self.username = text(username, 200)
        self.canvas_id = text(canvas_id, 64)
        self.conversation_id = text(conversation_id, 64)
        require(callable(resolve_context), '缺少可信权限解析器', 'scope_violation')
        self.resolve_context = resolve_context
        self.delegations = Delegations(ctx, username=self.username, authority='user')

    async def _fresh(self, role):
        current = await self.resolve_context(role)
        require(current.project_id == self.ctx.project_id
                and current.requester_user_id == self.ctx.requester_user_id
                and current.state_dir.resolve() == self.ctx.state_dir.resolve()
                and current.output_dir.resolve() == self.ctx.output_dir.resolve(),
                '项目身份或存储归属已变化', 'scope_violation')

    def _scope(self, canvas_id=None, conversation_id=None):
        if canvas_id is not None:
            require(canvas_id == self.canvas_id, '画布不属于当前服务范围', 'scope_violation')
        if conversation_id is not None:
            require(conversation_id == self.conversation_id, '会话不属于当前服务范围', 'scope_violation')
        agent_context.conversation(self.ctx, self.username, self.canvas_id, self.conversation_id)
        registered = next((value for value in list_canvases(self.ctx.state_dir)
                           if value['canvas_id'] == self.canvas_id and value['status'] == 'applied'), None)
        require(registered is not None, '画布尚未登记或不属于当前项目', 'scope_violation')

    @staticmethod
    def _current_revision(conn, object_id):
        row = conn.execute('SELECT current_revision FROM objects WHERE id=?', (object_id,)).fetchone()
        require(row is not None, '候选对象不存在或不属于当前委托', 'scope_violation')
        return row['current_revision']

    def _source(self, conn, object_id, node_id):
        """Resolve an object only through its durable AgentTools output and receipt."""
        require(conn is not None and table(conn, 'delegation_outputs')
                and table(conn, 'creative_domain_receipts') and table(conn, 'operations'),
                '候选对象没有可验证的委托来源', 'scope_violation')
        rows = conn.execute('''SELECT * FROM delegation_outputs WHERE project_id=? AND owner_id=?
            AND canvas_id=? AND conversation_id=? ORDER BY rowid''',
            (self.ctx.project_id, self.ctx.requester_user_id, self.canvas_id, self.conversation_id)).fetchall()
        matches = []
        for row in rows:
            output = json.loads(row['content'])
            ref = output.get('object_ref')
            if isinstance(ref, dict) and ref.get('id') == object_id:
                matches.append((row, output))
        require(len(matches) == 1, '候选对象没有唯一的可信委托输出', 'scope_violation')
        output_row, output = matches[0]
        delegation = self.delegations._get(conn, output_row['delegation_id'])
        contract = delegation['contract']
        require(delegation['project_id'] == self.ctx.project_id
                and delegation['owner_id'] == self.ctx.requester_user_id
                and contract['owner_username'] == self.username
                and delegation['canvas_id'] == self.canvas_id
                and delegation['conversation_id'] == self.conversation_id
                and output_row['owner_id'] == self.ctx.requester_user_id
                and output_row['canvas_id'] == self.canvas_id
                and output_row['conversation_id'] == self.conversation_id
                and output_row['delegation_id'] == output.get('delegation_id')
                and output_row['operation_id'] == output.get('operation_id'),
                '候选来源不属于当前用户、项目或画布会话', 'scope_violation')
        legacy_source = {'kind': 'agent', 'owner_id': self.ctx.requester_user_id,
                         'delegation_id': output_row['delegation_id']}
        member_source = {**legacy_source, 'member_id': contract['agent'].get('adoption_id')}
        require(output.get('id') == output_row['id']
                and output.get('operation') == 'proposal.create' and output.get('state') == 'candidate'
                and output.get('source') in (legacy_source, member_source),
                '对象不是有效的提案候选输出', 'scope_violation')
        target_ids = output.get('target_node_ids')
        original_ids = contract.get('expanded_scope', {}).get('node_ids')
        require(isinstance(target_ids, list) and target_ids and all(isinstance(value, str) for value in target_ids)
                and len(target_ids) == len(set(target_ids)) and isinstance(original_ids, list)
                and set(target_ids) <= set(original_ids) and node_id in target_ids,
                '目标节点不在原委托范围内', 'scope_violation')
        require(contract.get('canvas_id') == self.canvas_id
                and contract.get('conversation_id') == self.conversation_id,
                '原委托不属于当前画布会话', 'scope_violation')

        operation = conn.execute('SELECT * FROM operations WHERE id=?',
                                 (output_row['operation_id'],)).fetchone()
        require(operation is not None and operation['actor_id'] == self.ctx.requester_user_id
                and operation['state'] == 'applied', '提案操作记录缺失或归属无效', 'scope_violation')
        operation_payload = json.loads(operation['payload'])
        request = operation_payload.get('request', {})
        action_payload = request.get('payload', {})
        arguments = action_payload.get('payload', {})
        require(operation_payload.get('kind') == 'agent_tools.execute'
                and operation_payload.get('source') == {'kind': 'agent', 'actor_id': self.ctx.requester_user_id}
                and request.get('action') == 'execute'
                and action_payload.get('operation') == 'proposal.create'
                and action_payload.get('delegation_id') == output_row['delegation_id']
                and action_payload.get('canvas_id') == self.canvas_id
                and action_payload.get('conversation_id') == self.conversation_id
                and arguments.get('target_node_ids') == target_ids
                and arguments.get('input_indices') == output.get('input_indices')
                and arguments.get('acceptance_indices') == output.get('acceptance_indices'),
                '提案输出与原始可信操作不一致', 'scope_violation')
        receipt = conn.execute('''SELECT result FROM creative_domain_receipts
            WHERE request_id=? AND project_id=? AND domain='agent_tools' ''',
            (operation['request_id'], self.ctx.project_id)).fetchone()
        require(receipt is not None, '提案动作缺少持久回执', 'scope_violation')
        action_result = json.loads(receipt['result'])
        require(action_result.get('delegation_id') == output_row['delegation_id']
                and action_result.get('output') == output
                and action_result.get('operation_id') == output_row['operation_id'],
                '提案动作回执与输出不一致', 'scope_violation')
        original_ref = output.get('object_ref')
        require(original_ref == {'id': object_id, 'revision': 1}, '原始提案对象引用无效', 'scope_violation')
        original = ObjectStore._read(conn, object_id, original_ref['revision'])
        require(original is not None and original['kind'] == 'story_fragment'
                and original['payload'].get('canvas_id') == self.canvas_id
                and original['payload'].get('title') == arguments.get('title')
                and original['payload'].get('text') == arguments.get('text'),
                '提案对象不属于当前画布', 'scope_violation')

        indices = output.get('input_indices')
        contract_inputs = contract.get('inputs')
        require(isinstance(indices, list) and all(type(value) is int and 0 <= value < len(contract_inputs)
                for value in indices) and len(indices) == len(set(indices)),
                '原始输入引用损坏', 'scope_violation')
        source = {'output_id': output_row['id'], 'operation_id': output_row['operation_id'],
                  'delegation_id': output_row['delegation_id'], 'run_id': self._run_id(
                      conn, output_row['delegation_id'], operation['request_id']),
                  'target_node_ids': target_ids,
                  'inputs': [contract_inputs[index] for index in indices]}
        return {'origin': _object_ref(original), 'source': source, 'output': output,
                'contract': contract, 'original': original}

    def _run_id(self, conn, delegation_id, request_id):
        if not table(conn, 'agent_runs'):
            return None
        matches = []
        rows = conn.execute('''SELECT * FROM agent_runs WHERE project_id=? AND owner_id=?
            AND username=? AND canvas_id=? AND conversation_id=? ORDER BY rowid''',
            (self.ctx.project_id, self.ctx.requester_user_id, self.username,
             self.canvas_id, self.conversation_id)).fetchall()
        for row in rows:
            run = json.loads(row['content'])
            for round_index, step in enumerate(run.get('rounds', [])):
                if step.get('delegation_id') != delegation_id:
                    continue
                actions = step.get('decision', {}).get('actions', [])
                for action_index, action in enumerate(actions):
                    if action.get('operation') == 'proposal.create' and AgentRuns._action_id(
                            run['id'], round_index, action_index) == request_id:
                        matches.append(run['id'])
        require(len(matches) <= 1, '候选运行来源不唯一', 'scope_violation')
        return matches[0] if matches else None

    @staticmethod
    def _candidates(obj):
        payload = obj['payload']
        require(isinstance(payload.get('text'), str), '候选内容无效', 'invalid_contract')
        try:
            candidates = validate_candidates(json.loads(payload['text']))
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise OperationError('invalid_contract', '提案内容不符合候选格式') from exc
        return candidates

    def _node_evidence(self, node_id):
        try:
            canvas = read_canvas(self.ctx.state_dir, self.canvas_id)
        except (OSError, ValueError) as exc:
            raise OperationError('scope_violation', '画布不存在或无法读取') from exc
        require(canvas is not None and canvas.get('project_id') == self.ctx.project_id,
                '画布不存在或不属于当前项目', 'scope_violation')
        nodes = [node for node in canvas.get('nodes', []) if node.get('id') == node_id]
        require(len(nodes) == 1, '目标节点已删除或不属于当前画布', 'scope_violation')
        return {key: value for key, value in nodes[0].items() if key not in _LAYOUT_FIELDS}

    def _missing_canvas_node(self, ref):
        if not isinstance(ref, dict) or ref.get('kind') != 'canvas_node':
            return False
        try:
            canvas = read_canvas(self.ctx.state_dir, ref['canvas_id'])
        except (OSError, ValueError):
            return False
        if canvas is None:
            return True
        return (canvas.get('project_id') == self.ctx.project_id
                and not any(node.get('id') == ref['node_id'] for node in canvas.get('nodes', [])))

    def _current_input_evidence(self, conn, source):
        """Capture today's input state separately from the immutable source snapshot."""
        evidence = []
        for historical in source['inputs']:
            ref = historical['ref']
            try:
                current = agent_context.inputs(self.ctx, conn, [ref],
                                               username=self.username,
                                               current_canvas_id=self.canvas_id)
            except OperationError:
                # A removed canvas input remains reviewable from its historical source.
                # Recording the missing state lets an explicit preview proceed, while
                # restoring/changing that input before the write changes the proof.
                if not self._missing_canvas_node(ref):
                    raise
                evidence.append({'ref': ref, 'status': 'missing'})
            else:
                evidence.extend(current)
        return evidence

    def _selection(self, conn, object_id):
        if conn is None or not table(conn, 'creative_domain_receipts') or not table(conn, 'operations'):
            return None
        rows = conn.execute('''SELECT r.result,o.payload FROM creative_domain_receipts r JOIN operations o
            ON o.request_id=r.request_id WHERE r.project_id=? AND r.domain='ideation'
            AND o.actor_id=? ORDER BY o.rowid''',
            (self.ctx.project_id, self.ctx.requester_user_id)).fetchall()
        selected = None
        for row in rows:
            result, operation = json.loads(row['result']), json.loads(row['payload'])
            req = operation.get('request', {})
            p = req.get('payload', {})
            if req.get('action') != 'adopt' or p.get('object_id') != object_id:
                continue
            require(result.get('selection') is not None and result.get('origin', {}).get('object_id') == object_id,
                    '采纳记录损坏', 'scope_violation')
            selected = result['selection']
        return selected

    def _verify_lineage(self, conn, object_id, origin, source, requested_revision):
        source_proof = digest(source)
        for revision in range(origin['revision'] + 1, requested_revision + 1):
            row = conn.execute('SELECT payload FROM object_versions WHERE object_id=? AND revision=?',
                               (object_id, revision)).fetchone()
            require(row is not None, '候选版本历史不连续', 'scope_violation')
            payload = json.loads(row['payload'])
            marker = payload.get('_ideation_lineage')
            require(isinstance(marker, dict) and marker.get('origin') == origin
                    and marker.get('source_proof') == source_proof
                    and marker.get('revision') == revision
                    and marker.get('previous_revision') == revision - 1,
                    '新版本未保留可验证的原始来源', 'scope_violation')
            operation = conn.execute('SELECT * FROM operations WHERE id=?',
                                     (marker.get('operation_id'),)).fetchone()
            require(operation is not None and operation['actor_id'] == self.ctx.requester_user_id
                    and operation['state'] == 'applied', '修改版本缺少可信操作记录', 'scope_violation')
            stored = json.loads(operation['payload'])
            request = stored.get('request', {})
            request_payload = request.get('payload', {})
            require(stored.get('kind') == 'ideation.edit' and request.get('action') == 'edit'
                    and request.get('request_id') == marker.get('request_id')
                    and request_payload.get('object_id') == object_id
                    and request_payload.get('expected_revision') == revision - 1
                    and request_payload.get('canvas_id') == self.canvas_id
                    and request_payload.get('conversation_id') == self.conversation_id,
                    '修改版本操作与对象历史不一致', 'scope_violation')

    def _view(self, conn, object_id, node_id, revision=None, *, expected_latest=False,
              action=None, candidate_id=None, candidate=None):
        self._scope()
        source = self._source(conn, object_id, node_id)
        current_revision = self._current_revision(conn, object_id)
        selected_revision = current_revision if revision is None else integer(revision, 1)
        require(selected_revision <= current_revision, '候选版本不存在', 'scope_violation')
        if expected_latest:
            require(selected_revision == current_revision, '候选版本已变化，请重新预览', 'source_conflict')
        obj = ObjectStore._read(conn, object_id, selected_revision)
        require(obj is not None and obj['kind'] == 'story_fragment'
                and obj['payload'].get('canvas_id') == self.canvas_id,
                '候选版本不存在或不属于当前画布', 'scope_violation')
        require(source['origin']['object_id'] == object_id
                and source['origin']['revision'] <= selected_revision,
                '候选版本早于可信原始来源', 'scope_violation')
        self._verify_lineage(conn, object_id, source['origin'], source['source'], selected_revision)
        entries = self._candidates(obj)
        entry = next((item for item in entries if item['id'] == candidate_id), None)
        if action is not None:
            require(entry is not None, '候选编号不存在', 'invalid_contract')
        if action == 'edit':
            require(isinstance(candidate, dict) and set(candidate) == _BODY_FIELDS,
                    '编辑必须提供候选的六个正文段落', 'invalid_contract')
            changed = {**entry, **candidate}
            entries = [changed if item['id'] == candidate_id else item for item in entries]
            try:
                entries = validate_candidates({'schema_version': 1, 'candidates': entries})
            except (ValueError, TypeError) as exc:
                raise OperationError('invalid_contract', str(exc)) from exc
        node = self._node_evidence(node_id)
        selection = self._selection(conn, object_id)
        if selection is not None:
            require(selection.get('object_id') == object_id
                    and type(selection.get('revision')) is int
                    and source['origin']['revision'] <= selection['revision'] <= current_revision
                    and selection.get('node_id') in source['source']['target_node_ids']
                    and selection.get('source') == source['source']
                    and selection.get('run_id') == source['source']['run_id'],
                    '固定采纳记录与候选来源不一致', 'scope_violation')
            selected_obj = ObjectStore._read(conn, object_id, selection['revision'])
            require(selected_obj is not None and selection.get('candidate_id') in {
                item['id'] for item in self._candidates(selected_obj)},
                '固定采纳候选版本不存在', 'scope_violation')
        evidence = {'project_id': self.ctx.project_id, 'owner_id': self.ctx.requester_user_id,
                    'canvas_id': self.canvas_id, 'conversation_id': self.conversation_id,
                    'node_id': node_id, 'node_content': node,
                    'object_ref': _object_ref(obj), 'origin': source['origin'],
                    'source': source['source'], 'contract_hash': digest(source['contract']),
                    'selection': selection,
                    'action': action, 'candidate_id': candidate_id,
                    'candidate': candidate if action == 'edit' else None}
        if action is not None:
            evidence['current_inputs'] = self._current_input_evidence(conn, source['source'])
        return {'object': obj, 'candidates': self._candidates(obj), 'updated_candidates': entries,
                'entry': entry, 'origin': source['origin'], 'source': source['source'],
                'selection': selection, 'evidence': evidence,
                'proof': digest(evidence), 'node_id': node_id,
                'current_revision': current_revision, 'revision': selected_revision}

    @staticmethod
    @contextmanager
    def _shared_canvas_lock(state_dir, canvas_id):
        """Existing applied canvases have a lock file; reads never create one."""
        handle = None
        try:
            handle = canvas_lock_path(state_dir, canvas_id).open('rb')
            portalocker.lock(handle, portalocker.LOCK_SH | portalocker.LOCK_NB)
        except (OSError, portalocker.exceptions.AlreadyLocked) as exc:
            if handle is not None:
                handle.close()
            raise CanvasLockBusy(canvas_id) from exc
        try:
            yield
        finally:
            portalocker.unlock(handle)
            handle.close()

    def _locked_read(self, function, *args):
        try:
            with self._shared_canvas_lock(self.ctx.state_dir, self.canvas_id):
                return function(*args)
        except CanvasLockBusy as exc:
            raise OperationError('canvas_busy', '画布正在保存，请稍后重试') from exc

    def _read_view(self, object_id, node_id, revision=None):
        with self.read() as conn:
            value = self._view(conn, object_id, node_id, revision)
            return {'object_id': object_id, 'revision': value['revision'],
                    'origin': value['origin'], 'source': value['source'],
                    'candidates': value['candidates'], 'selection': value['selection']}

    async def get(self, object_id, *, node_id, revision=None):
        text(object_id, 128)
        text(node_id, 256)
        if revision is not None:
            integer(revision, 1)
        await self._fresh('viewer')
        return await registration_io(self._locked_read, self._read_view, object_id, node_id, revision)

    async def preview(self, body):
        require(isinstance(body, dict), '操作字段不符合约定')
        action = body.get('action')
        require(action in {'edit', 'adopt'}, '未知候选操作', 'invalid_contract')
        required = {'object_id', 'action', 'node_id', 'expected_revision', 'candidate_id'}
        optional = {'canvas_id', 'conversation_id'} | ({'candidate'} if action == 'edit' else set())
        exact(body, required, optional)
        text(body['object_id'], 128)
        text(body['node_id'], 256)
        text(body['candidate_id'], 128)
        integer(body['expected_revision'], 1)
        if 'canvas_id' in body or 'conversation_id' in body:
            self._scope(body.get('canvas_id'), body.get('conversation_id'))
        await self._fresh('viewer')
        def compute():
            with self.read() as conn:
                value = self._view(conn, body['object_id'], body['node_id'], body['expected_revision'],
                    expected_latest=True, action=action, candidate_id=body['candidate_id'],
                    candidate=body.get('candidate'))
                return {'proof': value['proof'], 'object_ref': _object_ref(value['object']),
                        'candidate_id': body['candidate_id'], 'action': action}
        return await registration_io(self._locked_read, compute)

    def _request(self, object_id, body, action):
        text(object_id, 128)
        require(isinstance(body, dict), '操作字段不符合约定')
        required = {'request_id', 'node_id', 'expected_revision', 'candidate_id', 'proof'}
        optional = {'canvas_id', 'conversation_id'} | ({'candidate'} if action == 'edit' else set())
        exact(body, required, optional)
        request_id = text(body['request_id'], 128)
        node_id = text(body['node_id'], 256)
        candidate_id = text(body['candidate_id'], 128)
        integer(body['expected_revision'], 1)
        text(body['proof'], 128)
        if 'canvas_id' in body or 'conversation_id' in body:
            self._scope(body.get('canvas_id'), body.get('conversation_id'))
        payload = {'object_id': object_id, 'canvas_id': self.canvas_id,
                   'conversation_id': self.conversation_id, 'node_id': node_id,
                   'expected_revision': body['expected_revision'], 'candidate_id': candidate_id,
                   'proof': body['proof']}
        if action == 'edit':
            payload['candidate'] = body['candidate']
        return DomainCommand(request_id=request_id, action=action, payload=payload)

    async def _send(self, object_id, body, action):
        request = self._request(object_id, body, action)
        await self._fresh('editor')
        async with asset_project_lock(self.ctx.state_dir):
            await self._fresh('editor')
            try:
                return await registration_io(self._apply, request)
            except CanvasLockBusy as exc:
                raise OperationError('canvas_busy', '画布正在保存，请使用原请求重试') from exc

    async def edit(self, object_id, body):
        return await self._send(object_id, body, 'edit')

    async def adopt(self, object_id, body):
        return await self._send(object_id, body, 'adopt')

    def _replay(self, conn, request, request_hash):
        require(request.payload.get('canvas_id') == self.canvas_id
                and request.payload.get('conversation_id') == self.conversation_id,
                '回执不属于当前画布会话', 'scope_violation')
        return super()._replay(conn, request, request_hash)

    def dispatch(self, conn, request, operation_id):
        action, payload = request.action, request.payload
        self._scope(payload.get('canvas_id'), payload.get('conversation_id'))
        candidate = payload.get('candidate') if action == 'edit' else None
        value = self._view(conn, payload['object_id'], payload['node_id'], payload['expected_revision'],
            expected_latest=True, action=action, candidate_id=payload['candidate_id'], candidate=candidate)
        require(value['proof'] == payload['proof'], '候选预览已变化，请重新比较', 'source_conflict')

        if action == 'edit':
            revision = value['current_revision'] + 1
            object_payload = dict(value['object']['payload'])
            object_payload['text'] = json.dumps({'schema_version': 1,
                'candidates': value['updated_candidates']}, ensure_ascii=False, separators=(',', ':'))
            object_payload['_ideation_lineage'] = {'origin': value['origin'],
                'source_proof': digest(value['source']), 'revision': revision,
                'previous_revision': value['current_revision'], 'operation_id': operation_id,
                'request_id': request.request_id}
            conn.execute('INSERT INTO object_versions VALUES (?,?,?,?,?)',
                (payload['object_id'], revision, packed(object_payload), self.ctx.requester_user_id,
                 datetime.now(timezone.utc).isoformat()))
            changed = conn.execute('UPDATE objects SET current_revision=? WHERE id=? AND current_revision=?',
                (revision, payload['object_id'], value['current_revision']))
            require(changed.rowcount == 1, '候选版本已变化，请重新预览', 'source_conflict')
            result = {'request_id': request.request_id, 'status': 'applied',
                'operation_id': operation_id, 'object_ref': {'object_id': payload['object_id'], 'revision': revision},
                'candidate_id': payload['candidate_id'], 'origin': value['origin'],
                'selection': value['selection']}
            return result

        require(action == 'adopt', '未知候选操作', 'invalid_contract')
        selection = {'object_id': payload['object_id'], 'revision': value['revision'],
            'candidate_id': payload['candidate_id'], 'node_id': payload['node_id'],
            'run_id': value['source']['run_id'], 'source': value['source']}
        return {'request_id': request.request_id, 'status': 'applied',
            'operation_id': operation_id, 'object_ref': _object_ref(value['object']),
            'candidate_id': payload['candidate_id'], 'origin': value['origin'],
            'selection': selection}

    async def receipt(self, request_id, *, node_id):
        text(request_id, 128)
        text(node_id, 256)
        await self._fresh('viewer')

        def read_receipt():
            with self.read() as conn:
                self._scope()
                self._node_evidence(node_id)
                if conn is None or not table(conn, 'operations') or not table(conn, 'creative_domain_receipts'):
                    return None
                row = conn.execute('''SELECT r.result,o.payload FROM creative_domain_receipts r JOIN operations o
                    ON o.request_id=r.request_id WHERE r.request_id=? AND r.project_id=? AND r.domain='ideation'
                    AND o.actor_id=?''', (request_id, self.ctx.project_id,
                    self.ctx.requester_user_id)).fetchone()
                if row is None:
                    return None
                stored = json.loads(row['payload'])
                request = stored.get('request', {})
                payload = request.get('payload', {})
                if (payload.get('canvas_id') != self.canvas_id
                        or payload.get('conversation_id') != self.conversation_id
                        or payload.get('node_id') != node_id):
                    return None
                return json.loads(row['result'])
        return await registration_io(self._locked_read, read_receipt)
