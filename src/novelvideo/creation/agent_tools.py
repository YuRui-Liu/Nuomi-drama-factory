"""Host-invoked, scoped preparation tools. No provider, filesystem or HTTP tool."""
from copy import deepcopy
import asyncio
import json
import time
from uuid import uuid4

from novelvideo.freezone.canvas_lock import CanvasLockBusy
from novelvideo.freezone.canvas_store import read_canvas
from novelvideo.freezone.orchestration.models import OrchestrationError, input_fingerprint
from novelvideo.production_workflow.coordination import asset_project_lock
from . import agent_context
from .asset_adapter import registration_io
from .asset_reference import table
from .delegations import Delegations, OPERATIONS
from .library_references import digest, packed
from .ontology import DomainCommand, Ontology, ProjectDomain, exact, integer, require, text
from .operations import OperationError, create_text_object
from .store import ObjectStore


_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS delegation_outputs (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, delegation_id TEXT NOT NULL,
        owner_id TEXT NOT NULL, canvas_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
        operation_id TEXT NOT NULL UNIQUE, content TEXT NOT NULL)''',
)


class AgentTools(ProjectDomain):
    domain = 'agent_tools'
    schema = _SCHEMA

    def __init__(self, ctx, *, username, canvas_id, conversation_id, delegation_id, resolve_context,
                 accepted_transition=None, authorize_submission=None, authorize_projection=None, authorize_action=None,
                 heartbeat=None, node_context=None,
                 node_execution_factory=None):
        super().__init__(ctx, authority='agent')
        self.username = text(username, 200)
        self.canvas_id = text(canvas_id, 64)
        self.conversation_id = text(conversation_id, 64)
        self.delegation_id = text(delegation_id, 128)
        require(callable(resolve_context), '缺少可信权限解析器', 'scope_violation')
        self.resolve_context = resolve_context
        self.delegations = Delegations(ctx, username=username)
        self.accepted_transition = accepted_transition
        self.authorize_submission = authorize_submission
        self.authorize_projection = authorize_projection
        self.authorize_action = authorize_action
        self.heartbeat = heartbeat
        self.node_context = deepcopy(node_context or {})
        self.node_execution_factory = node_execution_factory
        self._pending_node_transition = None
        self._pending_node_result = None
        self._direct_node_acceptance = None
        self._agent_run_transition = None

    async def _fresh(self, role):
        current = await self.resolve_context(role)
        require(current.project_id == self.ctx.project_id
                and current.requester_user_id == self.ctx.requester_user_id
                and current.state_dir.resolve() == self.ctx.state_dir.resolve()
                and current.output_dir.resolve() == self.ctx.output_dir.resolve(),
                '项目身份或存储归属已变化', 'scope_violation')

    async def apply(self, body):
        # Do not accidentally expose ProjectDomain's unscoped public shortcut.
        raise OperationError('capability_denied', '请使用固定委托的受限动作入口')

    async def execute(self, body, *, accepted_transition=None):
        exact(body, {'request_id', 'operation', 'payload'})
        text(body['operation'], 80)
        request = DomainCommand(request_id=body['request_id'], action='execute', payload={
            **deepcopy(body), 'delegation_id': self.delegation_id,
            'canvas_id': self.canvas_id, 'conversation_id': self.conversation_id})
        await self._fresh('editor')
        if body['operation'] in {'node.patch', 'node.run'}:
            try:
                return await self._execute_node(request, accepted_transition=accepted_transition)
            except OrchestrationError as exc:
                # Preserve known scheduler/auth/version failures at the domain
                # boundary. Arbitrary exceptions still propagate unchanged.
                raise OperationError(exc.code, str(exc), **exc.details) from exc
        async with asset_project_lock(self.ctx.state_dir):
            await self._fresh('editor')
            try:
                return await registration_io(self._apply, request)
            except CanvasLockBusy as exc:
                raise OperationError('canvas_busy', '画布正在保存，请使用原请求重试') from exc

    def _node_service(self, contract):
        if self.node_execution_factory is not None:
            return self.node_execution_factory(contract)
        from pathlib import Path
        from .node_execution import NodeExecutionService
        return NodeExecutionService(Path(self.ctx.state_dir), project_id=self.ctx.project_id,
            canvas_id=self.canvas_id, actor={'id': self.ctx.requester_user_id, 'username': self.username},
            authorize_submission=self.authorize_submission,
            authorize_projection=self.authorize_projection)

    def _node_preflight(self, operation, payload, *, accepted_transition, connection):
        if operation == 'node.patch':
            exact(payload, {'node_id', 'expected_revision', 'changes'})
            node_id = text(payload['node_id'], 128)
            revision = integer(payload['expected_revision'], 1)
            changes = payload['changes']
            require(isinstance(changes, list) and 0 < len(changes) <= 64,
                    '节点字段修改必须是有限的结构化字段路径', 'invalid_patch')
            for change in changes:
                exact(change, {'path', 'value'})
                require(isinstance(change['path'], list) and 0 < len(change['path']) <= 16
                        and all(type(part) in {str, int} for part in change['path']),
                        '节点字段路径无效', 'invalid_patch')
        else:
            exact(payload, {'node_id', 'operation', 'expected_revision'})
            node_id = text(payload['node_id'], 128)
            revision = integer(payload['expected_revision'], 1)
            node_operation = text(payload['operation'], 100)
            require(node_operation != 'patch', '请指定已注册的节点操作', 'capability_denied')
            require(accepted_transition is not None and callable(self.authorize_submission),
                    '节点运行必须来自可恢复的 AgentRuns 任务', 'run_required')
            changes = None

        if accepted_transition is None:
            contract = self._current(connection, operation)
        else:
            # AgentRuns durably validates and admits this exact action before
            # entering the node service. Replaying that accepted request must
            # reach the service's idempotency record even if the canvas has
            # since changed; new requests still receive service-level semantic
            # input validation before any mutation or external submission.
            contract = self._owned(connection)['contract']
            require(operation in contract['allowed_operations']
                    and operation in OPERATIONS
                    and OPERATIONS[operation] in contract['agent']['effective']['capabilities'],
                    '操作超出固定委托能力', 'capability_denied')
        authorization = contract.get('execution_authorization')
        require(authorization is not None and contract.get('authorization_id'),
                '此委托仅允许准备，不包含节点执行授权', 'capability_denied')
        require(node_id in authorization['allowed_node_ids'], '节点不在明确授权目标中', 'scope_violation')
        pair_operation = 'patch' if operation == 'node.patch' else node_operation
        require(any(item == {'node_id': node_id, 'operation': pair_operation}
                    for item in authorization['allowed_node_operations']),
                '节点的精确操作不在明确授权中', 'capability_denied')
        if operation == 'node.run':
            require(authorization['max_node_runs'] > 0, '委托没有节点运行次数授权', 'capability_denied')
        context = self.node_context.get(node_id)
        if context:
            require(revision == context.get('model_expected_revision', context['expected_revision']),
                    '节点版本不是模型接收的冻结版本', 'stale_revision')
            revision = context['expected_revision']
            expected_input_fingerprint = context['input_fingerprint']
        else:
            canvas = read_canvas(self.ctx.state_dir, self.canvas_id)
            current = next((node for node in canvas['nodes'] if node.get('id') == node_id), None)
            require(current is not None, '授权节点已不存在', 'scope_violation')
            require(revision <= canvas['revision'], '节点版本新于当前画布', 'stale_revision')
            expected_input_fingerprint = input_fingerprint(current)
            context = agent_context.node_snapshot(current)
            context['expected_revision'] = revision
        self.node_context.setdefault(node_id, context)
        require(node_id in {node['node_id'] for node in contract['expanded_scope']['nodes']},
                '节点超出冻结委托范围', 'scope_violation')
        return contract, node_id, revision, expected_input_fingerprint, changes, pair_operation, context

    @staticmethod
    def _accepted_after(snapshot):
        return {'node_id': snapshot['node_id'], 'input_fingerprint': snapshot['input_fingerprint'],
                'result_fingerprint': snapshot['result_fingerprint'],
                'media_fingerprint': snapshot['media_evidence']['locator_fingerprint']}

    async def _execute_node(self, request, *, accepted_transition=None):
        operation = request.payload['operation']
        payload = request.payload['payload']
        request_hash = digest([self.domain, self.ctx.project_id, self.ctx.requester_user_id,
                               self.authority, request.model_dump(mode='json')])

        def replay():
            with self.read() as conn:
                return self._replay(conn, request, request_hash)

        previous = await registration_io(replay)
        if previous is not None:
            return previous
        self._pending_node_transition = None
        self._pending_node_result = None
        self._direct_node_acceptance = None
        try:
            if accepted_transition is None:
                require(operation == 'node.patch', '节点运行必须来自可恢复的 AgentRuns 任务', 'run_required')
                accepted_request = DomainCommand(
                    request_id='node-accept:' + digest([self.delegation_id, request.request_id])[:64],
                    action='node.accept', payload={'source_request_id': request.request_id,
                        'operation': operation, 'payload': deepcopy(payload)})
                await self._fresh('editor')
                async with asset_project_lock(self.ctx.state_dir):
                    await self._fresh('editor')
                    accepted = await registration_io(self._apply, accepted_request)
                self._direct_node_acceptance = accepted
                with self.read() as conn:
                    row = self._owned(conn)
                    contract = row['contract']
                    require(self.delegations._active(conn, row), '委托已停止后续操作', 'delegation_inactive')
                    require(agent_context.conversation(self.ctx, self.username, self.canvas_id,
                                self.conversation_id) == contract['session_fence'],
                            '会话已清理，原委托不能跨代继续', 'stale_input')
                require(accepted.get('contract_hash') == digest(contract),
                        '已接纳动作的委托合同已变化', 'stale_input')
                node_id = accepted['node_id']
                revision = accepted['expected_revision']
                expected_fingerprint = accepted['expected_input_fingerprint']
                changes = deepcopy(payload['changes'])
                pair_operation = 'patch'
                context = accepted['node_context']
                transition = {'agent_run_id': 'direct:' + digest([self.delegation_id, request.request_id])[:32],
                    'step_id': request.request_id, 'action_id': request.request_id, 'action_index': 0}
            else:
                transition = deepcopy(accepted_transition)
                require(callable(self.authorize_action), '节点动作缺少可信宿主接纳记录', 'run_required')
                await self.authorize_action(transition, operation=operation, payload=payload)
                self._agent_run_transition = transition
                def preflight():
                    with self.read() as conn:
                        return self._node_preflight(operation, payload, accepted_transition=accepted_transition,
                                                   connection=conn)
                contract, node_id, revision, expected_fingerprint, changes, pair_operation, context = await registration_io(preflight)
            service = self._node_service(contract)
            # Delegation identity is attached by the trusted host. It is not
            # part of the caller/model grant envelope, but NodeExecution uses
            # it to scope the durable RunStore marker and budget chain.
            execution_contract = {**contract, 'delegation_id': self.delegation_id}
            if operation == 'node.patch':
                if accepted_transition is not None:
                    # Recheck the persisted lease/grant immediately before a
                    # canvas mutation. AgentRuns admission remains stable for
                    # idempotent recovery; stop/revocation fences new writes.
                    await self.authorize_action(transition, operation=operation, payload=payload)
                receipt = await registration_io(service.patch_node, request_id=request.request_id,
                    node_id=node_id, expected_revision=revision,
                    expected_input_fingerprint=expected_fingerprint, changes=changes,
                    contract=execution_contract, authorization_id=contract['authorization_id'],
                    accepted_transition=transition)
                current = await registration_io(read_canvas, self.ctx.state_dir, self.canvas_id)
                actual = next((node for node in current['nodes'] if node.get('id') == node_id), None)
                require(actual is not None, '授权节点已不存在', 'scope_violation')
                after_snapshot = agent_context.node_snapshot(actual)
                current_matches = (after_snapshot['input_fingerprint'] == receipt['after']['input_fingerprint']
                    and after_snapshot['result_fingerprint'] == context['result_fingerprint']
                    and after_snapshot['media_evidence']['locator_fingerprint']
                        == context['media_evidence']['locator_fingerprint'])
                if current_matches:
                    self._pending_node_transition = self._accepted_after(after_snapshot)
                self._pending_node_result = {'service_receipt': receipt, 'before': context,
                                             'after': after_snapshot, 'current_matches': current_matches}
            else:
                authorization_id = contract['authorization_id']
                # An explicit AgentRuns resume may continue the exact accepted
                # request after its local run was cancelled by an outer stop.
                # Unknown submissions are only reconciled by execute/read;
                # they are never resumed into a fresh provider submission.
                existing = await registration_io(service.find_request_run, request.request_id,
                    contract=execution_contract, authorization_id=authorization_id)
                if existing and existing.get('cancel_reason') in {
                        'adoption_stopped', 'cancelled_before_submit'}:
                    receipt = await service.resume_run(existing['run_id'], contract=execution_contract,
                                                       authorization_id=authorization_id)
                else:
                    receipt = await service.execute_node(request_id=request.request_id, node_id=node_id,
                        operation=pair_operation, expected_revision=revision,
                        expected_input_fingerprint=expected_fingerprint, contract=execution_contract,
                        authorization_id=authorization_id, accepted_transition=transition)
                deadline = time.monotonic() + 900
                heartbeat_at = time.monotonic()
                while receipt['status'] not in {'succeeded', 'failed', 'cancelled', 'blocked', 'submission_unknown'}:
                    if time.monotonic() >= deadline:
                        raise OperationError('node_run_pending', '节点运行仍在执行；保留原运行编号，不会创建替代运行',
                                             node_run_id=receipt['node_run_id'], status=receipt['status'])
                    if self.heartbeat and time.monotonic() - heartbeat_at >= 5:
                        await self.heartbeat()
                        heartbeat_at = time.monotonic()
                    await asyncio.sleep(.2)
                    receipt = await service.read_run(receipt['run_id'], contract=execution_contract,
                                                     authorization_id=contract['authorization_id'])
                if receipt['status'] in {'submission_unknown', 'blocked', 'cancelled'}:
                    # These statuses are mutable or resumable RunStore state,
                    # not an immutable AgentTools result. Leave the accepted
                    # AgentRuns action pending so an explicit resume can
                    # reconcile the same inner request without a new ID.
                    code = ('node_submission_unknown' if receipt['status'] == 'submission_unknown'
                            else 'node_run_pending')
                    raise OperationError(code,
                        '节点运行尚未形成可接受的终态回执；请恢复原 Agent 运行并查询同一节点运行',
                        node_run_id=receipt['node_run_id'], status=receipt['status'])
                if receipt['status'] == 'succeeded' and receipt.get('projection', {}).get('status') == 'verified_projected':
                    projection = receipt['projection']
                    require(projection['node_id'] == node_id
                            and projection['input_fingerprint'] == expected_fingerprint,
                            '节点输出投影不匹配当前授权输入', 'stale_input')
                    self._pending_node_transition = {
                        'node_id': node_id, 'input_fingerprint': projection['input_fingerprint'],
                        'result_fingerprint': projection['result_fingerprint'],
                        'media_fingerprint': projection['projected_media_fingerprint'],
                    }
                self._pending_node_result = {'service_receipt': receipt, 'before': context,
                                             'after': receipt.get('projection')}
            # The service call is outside both the creation DB transaction and
            # the asset project lock. Persist only its verified receipt here.
            result = await registration_io(self._apply, request)
            return result
        finally:
            self._pending_node_transition = None
            self._pending_node_result = None
            self._direct_node_acceptance = None
            self._agent_run_transition = None

    def _owned(self, conn):
        row = self.delegations._get(conn, self.delegation_id)
        contract = row['contract']
        require(contract['canvas_id'] == self.canvas_id and contract['conversation_id'] == self.conversation_id,
                '动作不属于当前画布会话', 'scope_violation')
        # Historical reads/replays authorize against the fixed server contract.
        # Only new actions revalidate the live canvas/session in _current.
        return row

    def _replay(self, conn, request, request_hash):
        self._owned(conn)
        result = super()._replay(conn, request, request_hash)
        if result:
            require(result['delegation_id'] == self.delegation_id, '回执不属于当前委托', 'scope_violation')
        return result

    async def receipt(self, request_id):
        text(request_id, 128)
        await self._fresh('viewer')
        return await registration_io(self._receipt, request_id)

    def _receipt(self, request_id):
        with self.read() as conn:
            self._owned(conn)
            if not table(conn, 'creative_domain_receipts'):
                return None
            row = conn.execute('''SELECT r.result FROM creative_domain_receipts r
                JOIN operations o ON o.request_id=r.request_id
                WHERE r.request_id=? AND r.project_id=? AND r.domain=? AND o.actor_id=?''',
                (request_id, self.ctx.project_id, self.domain, self.ctx.requester_user_id)).fetchone()
            result = json.loads(row[0]) if row else None
            return result if result and result['delegation_id'] == self.delegation_id else None

    async def outputs(self):
        await self._fresh('viewer')
        return await registration_io(self._outputs)

    def _outputs(self):
        with self.read() as conn:
            self._owned(conn)
            if not table(conn, 'delegation_outputs'):
                return []
            outputs = [json.loads(row[0]) for row in conn.execute('''SELECT content FROM delegation_outputs
                WHERE project_id=? AND delegation_id=? AND owner_id=? AND canvas_id=? AND conversation_id=? ORDER BY rowid''',
                (self.ctx.project_id, self.delegation_id, self.ctx.requester_user_id, self.canvas_id, self.conversation_id))]
            for output in outputs:
                if 'object_ref' in output:
                    ref = output['object_ref']
                    obj = ObjectStore._read(conn, ref['id'], ref['revision'])
                    require(obj is not None and obj['kind'] == 'story_fragment'
                            and obj['payload'].get('canvas_id') == self.canvas_id,
                            '候选历史或归属无效', 'scope_violation')
                    output['object'] = obj
            return outputs

    @staticmethod
    def _hard(context):
        return [{'scope': entry['scope'], 'hard': {key: value for key, value in entry['agreements']['effective'].items()
                                                  if value['level'] == 'hard'},
                 'conflicts': entry['agreements']['conflicts']} for entry in context
                if entry['agreements']['conflicts'] or any(v['level'] == 'hard' for v in entry['agreements']['effective'].values())]

    def _accepted_node_transitions(self, conn, contract):
        root_id = contract.get('root_delegation_id') or self.delegation_id
        if not table(conn, 'agent_delegations') or not table(conn, 'delegation_outputs'):
            return {}
        contract_by_id = {}
        for row in conn.execute('''SELECT id,contract FROM agent_delegations
                WHERE project_id=? AND owner_id=? AND owner_username=? AND canvas_id=? AND conversation_id=?''',
                (self.ctx.project_id, self.ctx.requester_user_id, self.username,
                 self.canvas_id, self.conversation_id)):
            value = json.loads(row['contract'])
            if (value.get('root_delegation_id') == root_id
                    and value.get('authorization_id') == contract.get('authorization_id')):
                contract_by_id[row['id']] = value
        transitions = {}
        if contract_by_id:
            placeholders = ','.join('?' for _ in contract_by_id)
            query = f'''SELECT content FROM delegation_outputs WHERE project_id=? AND canvas_id=?
                AND conversation_id=? AND delegation_id IN ({placeholders}) ORDER BY rowid'''
            args = (self.ctx.project_id, self.canvas_id, self.conversation_id, *contract_by_id.keys())
            for row in conn.execute(query, args):
                output = json.loads(row['content'])
                node_id = output.get('node_id')
                accepted = output.get('accepted_after')
                origin = contract_by_id.get(output.get('delegation_id'), {})
                grant = origin.get('execution_authorization') or {}
                pair = ('patch' if output.get('operation') == 'node.patch'
                        else output.get('node_operation'))
                authorized = any(item == {'node_id': node_id, 'operation': pair}
                                 for item in grant.get('allowed_node_operations', []))
                if not (node_id and isinstance(accepted, dict) and accepted.get('node_id') == node_id
                        and authorized):
                    continue
                if output.get('operation') == 'node.patch':
                    if output.get('state') != 'patched':
                        continue
                elif (output.get('operation') == 'node.run'
                      and not (output.get('status') == 'succeeded'
                               and output.get('projection', {}).get('status') == 'verified_projected')):
                    continue
                transitions[node_id] = accepted
        pending = self._pending_node_transition
        if isinstance(pending, dict) and pending.get('node_id'):
            transitions[pending['node_id']] = pending
        return transitions

    def _current(self, conn, operation, *, allow_inactive=False):
        row = self._owned(conn)
        contract = row['contract']
        if not allow_inactive:
            require(self.delegations._active(conn, row), '委托已停止后续操作', 'delegation_inactive')
        require(operation in OPERATIONS and operation in contract['allowed_operations']
                and OPERATIONS[operation] in contract['agent']['effective']['capabilities'],
                '操作超出固定委托能力', 'capability_denied')
        require(agent_context.conversation(self.ctx, self.username, self.canvas_id, self.conversation_id)
                == contract['session_fence'], '会话已清理，原委托不能跨代继续', 'stale_input')
        original = contract['expanded_scope']
        scope = dict(original['scope'])
        require(scope.pop('canvas_id') == self.canvas_id, '委托画布归属损坏', 'scope_violation')
        current = agent_context.graph_scope(self.ctx, self.canvas_id, scope)
        require(current['scope'] == original['scope'] and current['node_ids'] == original['node_ids']
                and current['membership'] == original['membership'],
                '目标范围或成员归属已变化', 'stale_input')
        accepted = self._accepted_node_transitions(conn, contract)
        original_nodes = {node['node_id']: node for node in original['nodes']}
        current_nodes = {node['node_id']: node for node in current['nodes']}
        for node_id, frozen in original_nodes.items():
            now_node = current_nodes[node_id]
            require(now_node['node_type'] == frozen['node_type'] and now_node['parent_id'] == frozen['parent_id'],
                    '目标节点类型或归属已变化', 'stale_input', node_id=node_id)
            state = accepted.get(node_id, frozen)
            require(now_node['input_fingerprint'] == state.get('input_fingerprint')
                    and now_node['result_fingerprint'] == state.get('result_fingerprint')
                    and now_node['media_evidence']['locator_fingerprint'] == state.get('media_fingerprint',
                        frozen['media_evidence']['locator_fingerprint']),
                    '目标内容、结果或媒体投影已变化', 'stale_input', node_id=node_id)
        inputs = agent_context.inputs(self.ctx, conn, [item['ref'] for item in contract['inputs']])
        for current_input, frozen_input in zip(inputs, contract['inputs']):
            ref = current_input['ref']
            input_node_id = ref.get('node_id') if ref.get('kind') == 'canvas_node' and ref.get('canvas_id') == self.canvas_id else None
            state = accepted.get(input_node_id) if input_node_id else None
            if state:
                snapshot = current_input['snapshot']
                require(snapshot['input_fingerprint'] == state['input_fingerprint']
                        and snapshot['result_fingerprint'] == state['result_fingerprint']
                        and snapshot['media_evidence']['locator_fingerprint'] == state['media_fingerprint'],
                        '固定节点输入与已接受转换不一致', 'stale_input', node_id=input_node_id)
            else:
                require(current_input == frozen_input, '委托引用输入已变化', 'stale_input')
        now = agent_context.context(self.ctx, conn, self.canvas_id, current, inputs)
        before, after = self._hard(contract['context']), self._hard(now)
        require(before == after, '当前硬约束已变化，请重新比较委托', 'hard_constraints_changed',
                before=before, after=after, evaluation='not_evaluated')
        require(not any(entry['conflicts'] for entry in after), '当前硬约束存在冲突',
                'hard_constraints_conflict', conflicts=after, evaluation='not_evaluated')
        return contract

    def node_execution_context(self, contract):
        grant = contract.get('execution_authorization')
        if not grant:
            return {}
        canvas = read_canvas(self.ctx.state_dir, self.canvas_id)
        nodes = {node['id']: node for node in canvas['nodes']}
        result = {}
        for node_id in grant['allowed_node_ids']:
            node = nodes.get(node_id)
            require(node is not None, '授权节点已不存在', 'scope_violation', node_id=node_id)
            snapshot = agent_context.node_snapshot(node)
            snapshot.update(expected_revision=canvas['revision'], allowed_operations=[
                item['operation'] for item in grant['allowed_node_operations'] if item['node_id'] == node_id])
            result[node_id] = snapshot
        return result

    @staticmethod
    def _indices(values, count):
        require(isinstance(values, list) and all(type(v) is int and 0 <= v < count for v in values)
                and len(values) == len(set(values)), '引用索引不属于原委托')
        return values

    def _selection(self, payload, contract):
        targets = payload['target_node_ids']
        require(isinstance(targets, list) and all(isinstance(v, str) for v in targets)
                and len(targets) == len(set(targets))
                and set(targets) <= set(contract['expanded_scope']['node_ids']), '目标超出原委托', 'scope_violation')
        require(bool(targets) or contract['expanded_scope']['scope']['kind'] == 'canvas',
                '局部委托不能以空目标扩大范围', 'scope_violation')
        self._indices(payload['input_indices'], len(contract['inputs']))
        self._indices(payload['acceptance_indices'], len(contract['acceptance']))
        require(bool(payload['acceptance_indices']), '请关联明确交付标准')
        return {'target_node_ids': targets, 'input_indices': payload['input_indices'],
                'acceptance_indices': payload['acceptance_indices']}

    def _context_result(self, contract, selection):
        inputs = [contract['inputs'][i] for i in selection['input_indices']]
        objects = set()
        for item in inputs:
            if item['ref']['kind'] == 'object':
                objects.add(item['ref']['object_id'])
            elif item['ref']['kind'] == 'asset':
                objects.update(v for v in (item['snapshot']['mapping']['object_id'],
                                          item['snapshot']['mapping']['parent_id']) if v)
        return {'nodes': [n for n in contract['expanded_scope']['nodes'] if n['node_id'] in selection['target_node_ids']],
                'inputs': inputs, 'agent': contract['agent'],
                'acceptance': [contract['acceptance'][i] for i in selection['acceptance_indices']],
                'context': [e for e in contract['context'] if e['scope'].get('node_id') in selection['target_node_ids']
                            or e['scope'].get('object_id') in objects
                            or e['scope']['level'] == 'canvas']}

    def dispatch(self, conn, request, operation_id):
        if request.action == 'node.accept':
            payload = request.payload
            exact(payload, {'source_request_id', 'operation', 'payload'})
            require(payload['operation'] == 'node.patch', '直接节点运行必须来自 AgentRuns', 'run_required')
            text(payload['source_request_id'], 128)
            contract, node_id, revision, expected_fingerprint, _, _, context = self._node_preflight(
                payload['operation'], payload['payload'], accepted_transition=None, connection=conn)
            return {'delegation_id': self.delegation_id, 'accepted': True,
                    'source_request_id': payload['source_request_id'], 'contract_hash': digest(contract),
                    'node_id': node_id, 'expected_revision': revision,
                    'expected_input_fingerprint': expected_fingerprint, 'node_context': context}
        operation, payload = request.payload['operation'], request.payload['payload']
        if (operation in {'node.patch', 'node.run'} and self._pending_node_result is not None
                and (self._agent_run_transition is not None or self._direct_node_acceptance is not None)):
            contract = self._owned(conn)['contract']
        else:
            contract = self._current(conn, operation,
                allow_inactive=bool(self._direct_node_acceptance and operation == 'node.patch'))
        member_id = contract['agent'].get('adoption_id')
        member = {'adoption_id': member_id, 'revision': contract['agent'].get('revision'),
                  'origin': 'main' if contract.get('main_binding') else 'adoption'}
        common = {'target_node_ids', 'input_indices', 'acceptance_indices'}
        fields = {'context.read': set(), 'proposal.create': {'title', 'text'},
                  'ontology.definition.draft': {'definition'}, 'media.propose': {'title', 'intent', 'modality'},
                  'node.patch': {'node_id', 'expected_revision', 'changes'},
                  'node.run': {'node_id', 'operation', 'expected_revision'}}
        if operation in {'node.patch', 'node.run'}:
            exact(payload, fields[operation])
            pending = self._pending_node_result
            require(isinstance(pending, dict), '节点服务尚未返回可记录的持久回执', 'node_result_missing')
            receipt = pending['service_receipt']
            node_id = payload['node_id']
            require(receipt['node_id'] == node_id, '节点服务回执目标不一致', 'scope_violation')
            source = {'kind': 'agent', 'owner_id': self.ctx.requester_user_id,
                      'delegation_id': self.delegation_id, 'member_id': member_id}
            output = {'id': uuid4().hex, 'delegation_id': self.delegation_id, 'operation_id': operation_id,
                      'operation': operation, 'source': source, 'member_id': member_id, 'member': member,
                      'delegation_origin': {'parent_id': contract.get('parent_id'),
                                            'root_delegation_id': contract.get('root_delegation_id')},
                      'node_id': node_id,
                      'evaluation': {'status': 'not_evaluated',
                                     'notice': '节点修改或运行回执不代表后续质量评估或整项任务完成'}}
            if operation == 'node.patch':
                before, after = pending['before'], pending['after']
                output.update(state='patched' if pending.get('current_matches') else 'stale',
                    revision=receipt['revision'], changes=receipt['changes'],
                    before={'input_fingerprint': before['input_fingerprint'],
                            'result_fingerprint': before['result_fingerprint'],
                            'media_fingerprint': before['media_evidence']['locator_fingerprint'],
                            'fields': before['editable_fields']},
                    after={'input_fingerprint': after['input_fingerprint'],
                           'result_fingerprint': after['result_fingerprint'],
                           'media_fingerprint': after['media_evidence']['locator_fingerprint'],
                           'fields': after['editable_fields']},
                    accepted_after=self._pending_node_transition)
            else:
                output.update(state='node_run', node_operation=payload['operation'],
                    node_run_id=receipt['node_run_id'], run_id=receipt['run_id'], status=receipt['status'],
                    attempt_status=receipt.get('attempt_status'), artifacts=receipt.get('artifacts', []),
                    result=receipt.get('result', {}), error=receipt.get('error'),
                    projection=receipt.get('projection'), previous_media=receipt.get('previous_media', []),
                    usage=receipt.get('usage'))
                if (receipt['status'] == 'succeeded'
                        and receipt.get('projection', {}).get('status') == 'verified_projected'):
                    output['accepted_after'] = self._pending_node_transition
            conn.execute('INSERT INTO delegation_outputs VALUES (?,?,?,?,?,?,?,?)',
                (output['id'], self.ctx.project_id, self.delegation_id, self.ctx.requester_user_id,
                 self.canvas_id, self.conversation_id, operation_id, packed(output)))
            return {'delegation_id': self.delegation_id, 'output': output}

        exact(payload, common | fields[operation])
        selection = self._selection(payload, contract)
        output = {'id': uuid4().hex, 'delegation_id': self.delegation_id, 'operation_id': operation_id,
                  'operation': operation, 'source': {'kind': 'agent', 'owner_id': self.ctx.requester_user_id,
                    'delegation_id': self.delegation_id, 'member_id': member_id}, 'member_id': member_id,
                  'member': member, 'delegation_origin': {'parent_id': contract.get('parent_id'),
                    'root_delegation_id': contract.get('root_delegation_id')}, **selection,
                  'evaluation': {'status': 'not_evaluated', 'notice': '工具成功不表示文本满足所有自然语言约定或整个委托已完成'}}
        if operation == 'context.read':
            output.update(state='read', context=self._context_result(contract, selection))
        elif operation == 'proposal.create':
            text(payload['title'], 200); text(payload['text'], 100000)
            # _current already verifies the applied registry and actual canvas.
            canvas = conn.execute('SELECT episode_id FROM canvas_workspaces WHERE canvas_id=?',
                                  (self.canvas_id,)).fetchone()
            require(canvas is not None, '画布尚未完成登记', 'scope_violation')
            ref = create_text_object(conn, self.ctx.requester_user_id, kind='story_fragment',
                data={'title': payload['title'], 'text': payload['text']}, canvas_id=self.canvas_id, episode_id=canvas[0])
            output.update(state='candidate', object_ref=ref)
        elif operation == 'ontology.definition.draft':
            domain = Ontology(self.ctx, authority='agent')
            result = domain.dispatch(conn, DomainCommand(request_id=request.request_id, action='definition.draft',
                payload={'definition': payload['definition'], 'expected_revision': 0}), operation_id)
            output.update(state='draft', definition_ref={'name': result['name'], 'revision': result['revision']})
        else:
            text(payload['title'], 200); text(payload['intent'], 12000)
            require(isinstance(payload['modality'], str) and payload['modality'] in {'image', 'video', 'audio', '3d'},
                    '未知媒体方案类型')
            output.update(state='proposed', plan={k: payload[k] for k in ('title', 'intent', 'modality')}, execution_enabled=False)
        conn.execute('INSERT INTO delegation_outputs VALUES (?,?,?,?,?,?,?,?)',
            (output['id'], self.ctx.project_id, self.delegation_id, self.ctx.requester_user_id,
             self.canvas_id, self.conversation_id, operation_id, packed(output)))
        return {'delegation_id': self.delegation_id, 'output': output}
