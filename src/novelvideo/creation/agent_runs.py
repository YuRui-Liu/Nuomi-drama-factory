"""Durable bounded Decision runs. Unknown provider submissions are never resent."""
import asyncio
from copy import deepcopy
import json
import time
from uuid import uuid4

from novelvideo.production_workflow.coordination import asset_project_lock
from . import agent_context, agent_model
from .agent_tools import AgentTools
from .asset_adapter import registration_io
from .asset_reference import table
from .delegations import Delegations
from .library_references import digest, packed
from .ontology import DomainCommand, ProjectDomain, exact, integer, require, text
from .operations import OperationError
from .store import ObjectStore
from novelvideo.freezone.orchestration.models import OrchestrationError


LEASE_SECONDS = 30
HEARTBEAT_SECONDS = 5
MODEL_TIMEOUT = 120
TERMINAL = {'delivered', 'partial', 'stopped', 'blocked', 'submission_unknown'}
_SCHEMA = ('''CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, owner_id TEXT NOT NULL,
    username TEXT NOT NULL, canvas_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
    delegation_id TEXT NOT NULL UNIQUE, revision INTEGER NOT NULL, content TEXT NOT NULL)''',)


def checkpoint(phase):
    """Failure-injection boundary; never a recovery heuristic."""


class AgentRuns(ProjectDomain):
    domain = 'agent_runs'
    schema = _SCHEMA

    def __init__(self, ctx, *, username, canvas_id, conversation_id, resolve_context):
        super().__init__(ctx, authority='user')
        self.username = text(username, 200)
        self.canvas_id = text(canvas_id, 64)
        self.conversation_id = text(conversation_id, 64)
        self.resolve_context = resolve_context
        self.delegations = Delegations(ctx, username=username, authority='user')

    async def _fresh(self, role):
        current = await self.resolve_context(role)
        require(current.project_id == self.ctx.project_id and current.requester_user_id == self.ctx.requester_user_id
                and current.state_dir.resolve() == self.ctx.state_dir.resolve()
                and current.output_dir.resolve() == self.ctx.output_dir.resolve(), '运行身份已变化', 'scope_violation')

    def _contract(self, conn, identifier):
        row = self.delegations._get(conn, identifier)
        contract = row['contract']
        require(contract['canvas_id'] == self.canvas_id and contract['conversation_id'] == self.conversation_id,
                '运行不属于当前画布会话', 'scope_violation')
        return contract

    def _tools(self, identifier):
        return AgentTools(self.ctx, username=self.username, canvas_id=self.canvas_id,
                          conversation_id=self.conversation_id, delegation_id=identifier, resolve_context=self.resolve_context,
                          authorize_projection=self._authorize_node_projection_sync)

    def _validate_current(self, conn, identifier):
        contract = self._contract(conn, identifier)
        require(bool(contract['allowed_operations']), '委托没有可用准备能力', 'capability_denied')
        return self._tools(identifier)._current(conn, contract['allowed_operations'][0])

    def _get(self, conn, identifier):
        text(identifier, 128)
        row = conn.execute('SELECT * FROM agent_runs WHERE id=? AND project_id=?',
                           (identifier, self.ctx.project_id)).fetchone() if conn and table(conn, 'agent_runs') else None
        require(row is not None and row['owner_id'] == self.ctx.requester_user_id
                and row['username'] == self.username and row['canvas_id'] == self.canvas_id
                and row['conversation_id'] == self.conversation_id, '运行不存在或不属于当前会话', 'scope_violation')
        return json.loads(row['content'])

    @staticmethod
    def _action_id(identifier, index, action_index):
        return f'run:{identifier}:{index}:action:{action_index}'

    def _outputs(self, conn, value):
        outputs = []
        if not table(conn, 'creative_domain_receipts'):
            return outputs
        for index, step in enumerate(value['rounds']):
            for action_index, action in enumerate(step.get('decision', {}).get('actions', [])):
                if action['operation'] == 'context.read':
                    continue
                row = conn.execute('''SELECT r.result FROM creative_domain_receipts r JOIN operations o
                    ON o.request_id=r.request_id WHERE r.request_id=? AND r.domain='agent_tools'
                    AND r.project_id=? AND o.actor_id=?''',
                    (self._action_id(value['id'], index, action_index), self.ctx.project_id, self.ctx.requester_user_id)).fetchone()
                if row:
                    result = json.loads(row[0])
                    require(result['delegation_id'] == step['delegation_id'], '输出归属损坏', 'scope_violation')
                    output = result['output']
                    if 'object_ref' in output:
                        ref = output['object_ref']
                        obj = ObjectStore._read(conn, ref['id'], ref['revision'])
                        require(obj and obj['kind'] == 'story_fragment' and obj['payload']['canvas_id'] == self.canvas_id,
                                '输出历史归属损坏', 'scope_violation')
                        output = {**output, 'object': obj}
                    outputs.append(output)
        return outputs

    def _has_verified_output(self, conn, value):
        for output in self._outputs(conn, value):
            if output.get('operation') == 'node.patch':
                if output.get('state') == 'patched' and isinstance(output.get('accepted_after'), dict):
                    return True
                continue
            if output.get('operation') != 'node.run':
                return True
            if (output.get('status') == 'succeeded'
                    and output.get('projection', {}).get('status') == 'verified_projected'):
                return True
        return False

    def _round_node_action_unmet(self, conn, value, round_index):
        """Keep failed, stale, or unknown node receipts visible as unmet work."""
        if not table(conn, 'creative_domain_receipts'):
            return []
        step = value['rounds'][round_index]
        unmet = []
        for action_index, action in enumerate(step.get('decision', {}).get('actions', [])):
            operation = action.get('operation')
            if operation not in {'node.patch', 'node.run'}:
                continue
            request_id = self._action_id(value['id'], round_index, action_index)
            row = conn.execute('''SELECT r.result FROM creative_domain_receipts r JOIN operations o
                ON o.request_id=r.request_id WHERE r.request_id=? AND r.domain='agent_tools'
                AND r.project_id=? AND o.actor_id=?''',
                (request_id, self.ctx.project_id, self.ctx.requester_user_id)).fetchone()
            if row is None:
                unmet.append('节点动作尚无可恢复回执，未计为完成')
                continue
            output = json.loads(row[0]).get('output', {})
            node_id = output.get('node_id', action.get('payload', {}).get('node_id', 'unknown'))
            if operation == 'node.patch':
                if output.get('state') != 'patched' or not output.get('accepted_after'):
                    unmet.append(f'节点 {node_id} 修改未能确认仍投影在当前画布')
                continue
            verified = (output.get('status') == 'succeeded'
                        and output.get('projection', {}).get('status') == 'verified_projected')
            if not verified:
                status = output.get('status') or 'unknown'
                unmet.append(f'节点 {node_id} 运行未验证完成（{status}），请核对原运行记录')
        return unmet

    def _public(self, conn, value):
        result = deepcopy(value)
        result.pop('lease', None)
        for step in result['rounds']:
            step.pop('prompt', None)
        result['outputs'] = self._outputs(conn, value)
        result['remaining_model_calls'] = value['max_model_calls'] - len(value['rounds'])
        result['evaluation'] = 'not_evaluated'
        return result

    def _read_value(self, identifier, *, raw=False):
        with self.read() as conn:
            value = self._get(conn, identifier)
            return value if raw else self._public(conn, value)

    async def get(self, identifier):
        await self._fresh('viewer')
        return await registration_io(self._read_value, identifier)

    async def catalog(self, delegation_id):
        await self._fresh('viewer')
        def read():
            with self.read() as conn:
                self._contract(conn, delegation_id)
                if not table(conn, 'agent_runs'):
                    return []
                return [self._public(conn, self._get(conn, row[0])) for row in conn.execute(
                    'SELECT id FROM agent_runs WHERE delegation_id=?', (delegation_id,))]
        return await registration_io(read)

    async def receipt(self, request_id):
        text(request_id, 128)
        await self._fresh('viewer')
        def read():
            with self.read() as conn:
                if conn is None or not table(conn, 'creative_domain_receipts'):
                    return None
                row = conn.execute('''SELECT r.result FROM creative_domain_receipts r JOIN operations o
                    ON o.request_id=r.request_id WHERE r.request_id=? AND r.domain=? AND r.project_id=? AND o.actor_id=?''',
                    (request_id, self.domain, self.ctx.project_id, self.ctx.requester_user_id)).fetchone()
                result = json.loads(row[0]) if row else None
                if result:
                    self._get(conn, result['run']['id'])
                return result
        return await registration_io(read)

    def _replay(self, conn, request, request_hash):
        result = super()._replay(conn, request, request_hash)
        if result:
            self._get(conn, result['run']['id'])
        return result

    async def apply(self, body):
        raise OperationError('capability_denied', '运行只能通过明确的启动、停止或恢复入口修改')

    async def _send(self, action, payload, request_id=None):
        request = DomainCommand(request_id=request_id or uuid4().hex, action=action, payload=payload)
        await self._fresh('editor')
        async with asset_project_lock(self.ctx.state_dir):
            await self._fresh('editor')
            return await registration_io(self._apply, request)

    async def start(self, body):
        exact(body, {'request_id', 'delegation_id', 'contract_hash', 'max_model_calls', 'specialist_ids'})
        return await self._send('start', {k: v for k, v in body.items() if k != 'request_id'}, body['request_id'])

    async def control(self, identifier, action, body):
        exact(body, {'request_id', 'expected_revision'})
        require(action in {'stop', 'resume'}, '未知运行控制操作')
        result = await self._send(action, {'id': identifier, 'expected_revision': body['expected_revision']}, body['request_id'])
        if action == 'stop':
            await self._stop_accepted_node_runs(result['run'])
        return result

    async def _stop_accepted_node_runs(self, value):
        """Propagate an outer stop to any durable inner node run, outside SQL locks."""
        from pathlib import Path
        from .node_execution import NodeExecutionService
        actor = {'id': self.ctx.requester_user_id, 'username': self.username}
        for round_index, step in enumerate(value.get('rounds', [])):
            for action_index in step.get('actions_admitted', []):
                actions = step.get('decision', {}).get('actions', [])
                if (type(action_index) is not int or not 0 <= action_index < len(actions)
                        or action_index in step.get('actions_done', [])):
                    continue
                action = actions[action_index]
                if action.get('operation') != 'node.run':
                    continue
                contract = await registration_io(lambda d=step['delegation_id']: self._contract_read(d))
                if not contract.get('execution_authorization') or not contract.get('authorization_id'):
                    continue
                contract = {**contract, 'delegation_id': step['delegation_id']}
                service = NodeExecutionService(Path(self.ctx.state_dir), project_id=self.ctx.project_id,
                    canvas_id=self.canvas_id, actor=actor, authorize_submission=self._authorize_node_submission)
                request_id = self._action_id(value['id'], round_index, action_index)
                receipt = None
                try:
                    receipt = await registration_io(service.find_request_run, request_id,
                        contract=contract, authorization_id=contract['authorization_id'])
                    if receipt is None:
                        continue
                    await service.stop_run(receipt['run_id'], contract=contract,
                                           authorization_id=contract['authorization_id'])
                except Exception as exc:
                    raise OperationError('node_stop_pending',
                        '主运行已停止，但节点运行停止回执尚未确认；请重试停止并检查原运行记录',
                        node_run_id=(receipt.get('node_run_id', receipt.get('run_id'))
                                     if isinstance(receipt, dict) else None),
                        status=(receipt.get('status') if isinstance(receipt, dict) else None)) from exc

    def _contract_read(self, identifier):
        with self.read() as conn:
            return deepcopy(self._contract(conn, identifier))

    def _fence(self, conn, identifier, owner):
        value = self._get(conn, identifier)
        require(value['state'] not in TERMINAL and value.get('lease')
                and value['lease']['owner'] == owner and value['lease']['expires'] > time.time(),
                '运行已停止或执行租约已失效', 'run_fenced')
        return value

    def _save(self, conn, value):
        conn.execute('UPDATE agent_runs SET revision=?,content=? WHERE id=?',
                     (value['revision'], packed(value), value['id']))
        return {'run': self._public(conn, value)}

    def _validate_resume_node_action(self, conn, value):
        """Validate authority while letting the durable node request resolve first."""
        if not value.get('rounds'):
            return False
        step = value['rounds'][-1]
        if step.get('phase') != 'decision_saved':
            return False
        for index in step.get('actions_admitted', []):
            if index in step.get('actions_done', []):
                continue
            actions = step.get('decision', {}).get('actions', [])
            if type(index) is not int or not 0 <= index < len(actions):
                raise OperationError('run_not_resumable', '持久节点动作索引无效')
            action = actions[index]
            operation = action.get('operation')
            if operation not in {'node.patch', 'node.run'}:
                continue
            payload = action.get('payload') or {}
            row = self.delegations._get(conn, step['delegation_id'])
            contract = row['contract']
            grant = contract.get('execution_authorization')
            node_id = payload.get('node_id')
            node_operation = 'patch' if operation == 'node.patch' else payload.get('operation')
            root = self._contract(conn, value['delegation_id'])
            root_id = root.get('root_delegation_id') or value['delegation_id']
            if (row['state'] == 'revoked' or not self.delegations._active(conn, row)
                    or contract.get('session_fence') != agent_context.conversation(
                        self.ctx, self.username, self.canvas_id, self.conversation_id)
                    or contract.get('canvas_id') != self.canvas_id
                    or contract.get('conversation_id') != self.conversation_id
                    or (contract.get('root_delegation_id') or step['delegation_id']) != root_id
                    or not grant or node_id not in grant.get('allowed_node_ids', [])
                    or not any(item == {'node_id': node_id, 'operation': node_operation}
                               for item in grant.get('allowed_node_operations', []))):
                raise OperationError('authorization_revoked', '持久节点动作授权或会话已失效')
            if operation == 'node.run' and grant.get('max_node_runs', 0) < 1:
                raise OperationError('authorization_revoked', '节点运行次数授权已失效')
            if not any(node.get('node_id') == node_id
                       for node in contract.get('expanded_scope', {}).get('nodes', [])):
                raise OperationError('authorization_revoked', '节点已超出冻结委托范围')
            return True
        return False

    def dispatch(self, conn, request, operation_id):
        p, action = request.payload, request.action
        if action == 'start':
            contract = self._validate_current(conn, p['delegation_id'])
            require(digest(contract) == text(p['contract_hash'], 128), '委托快照不匹配', 'stale_input')
            limit = integer(p['max_model_calls'], 1)
            require(limit <= agent_model.MAX_MODEL_CALLS,
                    f'最多允许 {agent_model.MAX_MODEL_CALLS} 次明确授权的模型调用')
            ids = p['specialist_ids']
            require(isinstance(ids, list) and len(ids) <= agent_model.MAX_ELIGIBLE_SPECIALISTS
                    and all(isinstance(i, str) for i in ids)
                    and len(ids) == len(set(ids)), '专业Agent选择无效')
            specialists = [self.delegations._agent(conn, i) for i in ids]
            available = agent_model.availability(self.ctx)
            require(available['available'], '当前未配置可用的受限模型API', available['code'])
            existing = conn.execute('SELECT id FROM agent_runs WHERE delegation_id=?', (p['delegation_id'],)).fetchone()
            require(existing is None, '此委托已有运行，请恢复原运行', 'run_exists', run_id=existing[0] if existing else None)
            value = {'id': uuid4().hex, 'revision': 1, 'state': 'queued', 'delegation_id': p['delegation_id'],
                     'contract_hash': p['contract_hash'], 'route': available['route'],
                     'configuration_fingerprint': available['configuration_fingerprint'], 'max_model_calls': limit,
                     'specialists': specialists, 'pending': [p['delegation_id']], 'rounds': [], 'lease': None,
                     'unmet': [], 'cost': {'status': 'unknown', 'amount': None}}
            conn.execute('INSERT INTO agent_runs VALUES (?,?,?,?,?,?,?,?,?)',
                (value['id'], self.ctx.project_id, self.ctx.requester_user_id, self.username, self.canvas_id,
                 self.conversation_id, p['delegation_id'], 1, packed(value)))
            return {'run': self._public(conn, value)}

        value = self._get(conn, p['id'])
        if action in {'stop', 'resume'}:
            require(value['revision'] == integer(p['expected_revision'], 1), '运行状态已变化', 'revision_conflict')
            if action == 'stop':
                if value['state'] in {'delivered', 'partial', 'stopped'}:
                    return {'run': self._public(conn, value)}
                if value['rounds'] and value['rounds'][-1]['phase'] == 'model_inflight':
                    value['rounds'][-1]['phase'] = 'submission_unknown'
                value.update(state='stopped', lease=None, provider_cancelled=False)
            else:
                require(value['state'] in {'stopped', 'blocked', 'paused', 'queued', 'applying', 'model_inflight'}, '此状态不能安全继续', 'run_not_resumable')
                require(not value.get('lease') or value['lease']['expires'] <= time.time(), '运行正在执行', 'run_busy')
                if value['rounds'] and value['rounds'][-1]['phase'] == 'model_inflight':
                    value['rounds'][-1]['phase'] = 'submission_unknown'
                    value.update(state='submission_unknown', lease=None)
                    value['revision'] += 1
                    return self._save(conn, value)
                require(not any(s['phase'] in {'model_inflight', 'submission_unknown'} for s in value['rounds']),
                        '模型提交结果未知，不能重发付费请求', 'model_submission_unknown')
                require(bool(value['rounds'] and value['rounds'][-1]['phase'] == 'decision_saved')
                        or len(value['rounds']) < value['max_model_calls'], '已达到本次调用上限', 'call_limit_reached')
                has_pending_node_action = self._validate_resume_node_action(conn, value)
                if not has_pending_node_action:
                    self._validate_current(conn, value['pending'][0] if value['pending'] else value['delegation_id'])
                value.update(state='queued', lease=None)
                value.pop('error', None)
        elif action == 'claim':
            if value['state'] in TERMINAL:
                return {'run': self._public(conn, value)}
            require(not value.get('lease') or value['lease']['expires'] <= time.time(), '运行正在执行', 'run_busy')
            if value['rounds'] and value['rounds'][-1]['phase'] == 'model_inflight':
                value['rounds'][-1]['phase'] = 'submission_unknown'
                value.update(state='submission_unknown', lease=None)
            else:
                value['lease'] = {'owner': p['owner'], 'expires': time.time() + LEASE_SECONDS}
                value['state'] = 'applying' if value['rounds'] and value['rounds'][-1]['phase'] == 'decision_saved' else 'queued'
        else:
            value = self._fence(conn, p['id'], p['owner'])
            if action == 'heartbeat':
                value['lease']['expires'] = time.time() + LEASE_SECONDS
            elif action == 'admit_model':
                step = value['rounds'][-1]
                require(value['state'] == 'model_inflight' and not step.get('send_admitted'),
                        '模型发送已被认领', 'run_fenced')
                self._validate_current(conn, step['delegation_id'])
                step['send_admitted'] = True
            elif action == 'prepare':
                require(len(value['rounds']) < value['max_model_calls'] and bool(value['pending']), '模型调用额度已用完', 'call_limit_reached')
                tools = self._tools(value['pending'][0])
                contract = self._validate_current(conn, value['pending'][0])
                node_context = tools.node_execution_context(contract)
                metadata = agent_model.availability(self.ctx)
                require(metadata['available'] and metadata['route'] == value['route']
                        and metadata['configuration_fingerprint'] == value['configuration_fingerprint'],
                        '模型配置已变化，请重新发起任务', 'runtime_configuration_changed')
                nodes = deepcopy(contract['expanded_scope']['nodes'])
                for node in nodes:
                    current = node_context.get(node['node_id'])
                    if current:
                        for key in ('input_fingerprint', 'result_fingerprint', 'editable_fields', 'media_evidence'):
                            node[key] = deepcopy(current[key])
                payload_contract = {'common': ['target_node_ids', 'input_indices', 'acceptance_indices'],
                    'proposal.create': ['title', 'text'], 'media.propose': ['title', 'intent', 'modality'],
                    'ontology.definition.draft': ['definition']}
                authorization = contract.get('execution_authorization')
                if authorization is not None:
                    payload_contract.update({
                        'node.patch': {'node_id': 'one authorized node id', 'expected_revision': 'frozen canvas revision',
                                       'changes': [{'path': ['frames', 0, 'static_panel', 'lighting'], 'value': '冷色侧光'}]},
                        'node.run': {'node_id': 'one authorized node id', 'operation': 'one exact registered operation',
                                     'expected_revision': 'frozen canvas revision'},
                    })
                prompt = packed({'goal': contract['goal'], 'agent': contract['agent']['effective'],
                    'nodes': nodes, 'inputs': contract['inputs'],
                    'context': contract['context'], 'acceptance': contract['acceptance'],
                    'allowed_operations': contract['allowed_operations'], 'specialists': value['specialists'],
                    'node_execution': {'authorization': authorization,
                        'root_max_node_runs': contract.get('root_max_node_runs', 0),
                        'budget_chain': contract.get('node_run_budget_chain', []),
                        'read_node_ids': contract.get('read_node_ids', []), 'targets': node_context},
                    'outputs': self._outputs(conn, value), 'remaining_model_calls': value['max_model_calls'] - len(value['rounds']),
                    'payload_contract': payload_contract})
                require(len(prompt.encode()) <= 250_000, '冻结上下文过大，请缩小范围', 'context_too_large')
                value['rounds'].append({'delegation_id': value['pending'][0], 'attempt_id': uuid4().hex,
                    'phase': 'model_inflight', 'prompt': prompt, 'node_context': node_context,
                    'actions_done': [], 'actions_admitted': [], 'children_done': []})
                value['state'] = 'model_inflight'
            elif action == 'decision':
                decision = agent_model.Decision.model_validate(p['result']['decision']).model_dump(mode='json')
                require(len(packed(decision).encode()) <= 250_000, '模型输出过大')
                step = value['rounds'][-1]
                require(step['phase'] == 'model_inflight', '模型回合已完成', 'run_fenced')
                step.update(phase='decision_saved', decision=decision, usage=p['result'].get('usage', {}))
                value['state'] = 'applying'
            elif action == 'action_done':
                step = value['rounds'][-1]
                index = integer(p['index'])
                require(index < len(step['decision']['actions']), '动作索引无效', 'run_fenced')
                if step['decision']['actions'][index]['operation'] in {'node.patch', 'node.run'}:
                    require(index in step.get('actions_admitted', []), '节点动作尚未持久接纳', 'run_fenced')
                if index not in step['actions_done']:
                    step['actions_done'].append(index)
            elif action == 'admit_action':
                step = value['rounds'][-1]
                index = integer(p['index'])
                require(value['state'] == 'applying' and step['phase'] == 'decision_saved'
                        and index < len(step['decision']['actions']), '动作不能接纳', 'run_fenced')
                model_action = step['decision']['actions'][index]
                require(model_action['operation'] in {'node.patch', 'node.run'}
                        and p['action_id'] == self._action_id(value['id'], len(value['rounds']) - 1, index),
                        '节点动作身份无效', 'run_fenced')
                if index not in step.setdefault('actions_admitted', []):
                    # This durable host admission is the node-action
                    # linearization point. Replays of an already admitted
                    # request retain the same idempotency key and frozen
                    # context, even if the canvas later changes.
                    contract = self._validate_current(conn, step['delegation_id'])
                    if model_action['operation'] in {'node.patch', 'node.run'}:
                        node_id = model_action['payload'].get('node_id')
                        original = step.get('node_context', {}).get(node_id)
                        require(original is not None
                                and model_action['payload'].get('expected_revision') == original['expected_revision'],
                                '节点动作版本必须匹配模型接收的冻结版本', 'stale_revision')
                        current = self._tools(step['delegation_id']).node_execution_context(contract).get(node_id)
                        require(current is not None, '节点不在持久执行上下文中', 'scope_violation')
                        current['model_expected_revision'] = model_action['payload']['expected_revision']
                        step.setdefault('action_node_context', {})[str(index)] = current
                    step['actions_admitted'].append(index)
            elif action == 'child_done':
                value['rounds'][-1]['children_done'].append(p['child_id'])
                value['pending'].append(p['child_id'])
            elif action == 'children_limited':
                value['rounds'][-1]['children_limited'] = True
            elif action == 'finish_round':
                step = value['rounds'][-1]
                step['phase'] = 'applied'
                decision = step['decision']
                # A child cannot resolve its parent's obligations. Rebuild from
                # persisted rounds so recovery also supports earlier run rows.
                by_delegation = {}
                node_unmet_by_delegation = {}
                for completed_index, completed in enumerate(value['rounds']):
                    if completed['phase'] != 'applied':
                        continue
                    remaining = list(completed['decision']['unmet'])
                    if completed.get('children_limited'):
                        remaining.append('专业委派超出本次模型调用上限，未执行剩余委派')
                    by_delegation[completed['delegation_id']] = remaining
                    node_unmet_by_delegation.setdefault(completed['delegation_id'], []).extend(
                        self._round_node_action_unmet(conn, value, completed_index))
                value['unmet_by_delegation'] = {
                    delegation_id: model_unmet + node_unmet_by_delegation.get(delegation_id, [])
                    for delegation_id, model_unmet in by_delegation.items()}
                # Node execution obligations survive later model rounds until
                # the same durable request has a verified result. A later
                # decision's `finished` flag cannot erase them.
                value['unmet'] = [item for remaining in value['unmet_by_delegation'].values()
                                  for item in remaining]
                value['pending'].pop(0)
                if not decision['finished'] and decision['actions']:
                    value['pending'].append(step['delegation_id'])
                if not value['pending'] or len(value['rounds']) >= value['max_model_calls']:
                    value['state'] = 'partial' if value['pending'] or value['unmet'] or not self._has_verified_output(conn, value) else 'delivered'
                    if value['pending'] and not value['unmet']:
                        value['unmet'] = ['本次模型调用次数已用完，仍有待处理事项']
                    value['lease'] = None
                else:
                    value['state'] = 'queued'
            elif action == 'failure':
                value['error'] = p['error']
                if value['rounds'] and value['rounds'][-1]['phase'] == 'model_inflight':
                    received = p['error'].get('response_received', False)
                    value['rounds'][-1].update(phase='invalid_response' if received else 'submission_unknown',
                                                usage=p['error'].get('usage', {}))
                    value['state'] = 'blocked' if received else 'submission_unknown'
                else:
                    value['state'] = 'blocked'
                value['lease'] = None
            else:
                require(False, '未知可信宿主状态转换')
        value['revision'] += 1
        return self._save(conn, value)

    async def _model(self, value, owner):
        from novelvideo.text_task_runtime.models import AgentTaskRouteSnapshot
        step = value['rounds'][-1]
        # Durable send admission is the stop/send linearization point. No network
        # runs under this lock; a later stop can only fence the admitted request.
        await self._send('admit_model', {'id': value['id'], 'owner': owner})
        task = asyncio.create_task(agent_model.run_decision(self.ctx,
            AgentTaskRouteSnapshot.model_validate(value['route']), step['prompt'],
            run_id=value['id'], attempt_id=step['attempt_id']))
        deadline, heartbeat = time.monotonic() + MODEL_TIMEOUT, time.monotonic()
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=.2)
                current = await registration_io(self._read_value, value['id'], raw=True)
                if current['state'] in TERMINAL or not current.get('lease') or current['lease']['owner'] != owner:
                    raise OperationError('run_fenced', '运行已停止，迟到结果不会执行')
                if time.monotonic() >= deadline:
                    raise TimeoutError()
                if time.monotonic() - heartbeat >= HEARTBEAT_SECONDS:
                    await self._send('heartbeat', {'id': value['id'], 'owner': owner})
                    heartbeat = time.monotonic()
            return task.result()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.wait({task}, timeout=1)
                task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    async def _authorize_node_action(self, transition, *, delegation_id, operation, payload=None):
        """Recheck a persisted AgentRuns action before handing it to a node service."""
        try:
            await self._fresh('editor')
            return await registration_io(self._authorize_node_action_sync, transition,
                delegation_id=delegation_id, operation=operation, payload=deepcopy(payload))
        except OrchestrationError:
            raise
        except OperationError as exc:
            code = 'authorization_revoked' if exc.code in {
                'run_fenced', 'delegation_inactive', 'scope_violation', 'stale_input'
            } else 'authorization_unavailable'
            raise OrchestrationError(code, 'Agent node action authorization is no longer valid') from exc
        except Exception as exc:
            raise OrchestrationError('authorization_unavailable',
                                     'Could not recheck Agent node action authorization') from exc

    def _authorize_node_action_sync(self, transition, *, delegation_id, operation, payload):
        required = {'agent_run_id', 'step_id', 'action_id', 'action_index'}
        if not isinstance(transition, dict) or set(transition) != required:
            raise OrchestrationError('authorization_revoked', 'Accepted node action identity is malformed')
        try:
            with self.read() as conn:
                value = self._get(conn, transition['agent_run_id'])
                if (value['state'] != 'applying' or not value.get('lease')
                        or value['lease'].get('expires', 0) <= time.time()):
                    raise OrchestrationError('authorization_revoked', 'Agent run is stopped or its lease expired')
                if not value['rounds']:
                    raise OrchestrationError('authorization_revoked', 'Agent run has no accepted action')
                step = value['rounds'][-1]
                index = transition['action_index']
                if (type(index) is not int or index < 0 or index >= len(step.get('decision', {}).get('actions', []))
                        or step.get('attempt_id') != transition['step_id']
                        or step.get('delegation_id') != delegation_id
                        or index not in step.get('actions_admitted', [])
                        or index in step.get('actions_done', [])):
                    raise OrchestrationError('authorization_revoked', 'Agent action is no longer admitted')
                action = step['decision']['actions'][index]
                if (action.get('operation') != operation
                        or transition['action_id'] != self._action_id(value['id'], len(value['rounds']) - 1, index)):
                    raise OrchestrationError('authorization_revoked', 'Accepted Agent action identity changed')
                action_payload = action.get('payload')
                if payload is not None and action_payload != payload:
                    raise OrchestrationError('authorization_revoked', 'Accepted Agent action payload changed')
                payload = action_payload
                node_id = payload.get('node_id') if isinstance(payload, dict) else None
                original_node_context = step.get('node_context', {}).get(node_id)
                action_node_context = step.get('action_node_context', {}).get(str(index), original_node_context)
                if (not original_node_context or not action_node_context
                        or payload.get('expected_revision') != original_node_context.get('expected_revision')):
                    raise OrchestrationError('authorization_revoked', 'Node action lost its frozen input context')

                row = self.delegations._get(conn, delegation_id)
                contract = row['contract']
                root_contract = self._contract(conn, value['delegation_id'])
                root_id = root_contract.get('root_delegation_id') or value['delegation_id']
                if (row['state'] == 'revoked' or not self.delegations._active(conn, row)
                        or str(row['owner_id']) != str(self.ctx.requester_user_id)
                        or row['owner_username'] != self.username
                        or contract.get('canvas_id') != self.canvas_id
                        or contract.get('conversation_id') != self.conversation_id
                        or contract.get('project_id', self.ctx.project_id) != self.ctx.project_id
                        or str(contract.get('owner_id', self.ctx.requester_user_id)) != str(self.ctx.requester_user_id)
                        or (contract.get('root_delegation_id') or delegation_id) != root_id
                        or contract.get('session_fence') != agent_context.conversation(
                            self.ctx, self.username, self.canvas_id, self.conversation_id)):
                    raise OrchestrationError('authorization_revoked', 'Delegation ownership or live session changed')
                chain_rows = []
                cursor = row
                while cursor is not None:
                    ancestor_contract = cursor['contract']
                    ancestor_grant = ancestor_contract.get('execution_authorization')
                    if not ancestor_grant:
                        raise OrchestrationError('authorization_revoked', 'An ancestor node grant was revoked')
                    chain_rows.append({'delegation_id': cursor['id'],
                                       'max_node_runs': ancestor_grant['max_node_runs']})
                    cursor = self.delegations._get(conn, cursor['parent_id']) if cursor['parent_id'] else None
                chain_rows.reverse()
                grant = contract.get('execution_authorization')
                if not grant or not contract.get('authorization_id'):
                    raise OrchestrationError('authorization_revoked', 'Frozen node execution grant is missing')
                node_id = payload.get('node_id')
                node_operation = 'patch' if operation == 'node.patch' else payload.get('operation')
                if (node_id not in grant.get('allowed_node_ids', [])
                        or not any(item == {'node_id': node_id, 'operation': node_operation}
                                   for item in grant.get('allowed_node_operations', []))):
                    raise OrchestrationError('authorization_revoked', 'Frozen grant does not allow this node operation')
                if operation == 'node.run' and grant.get('max_node_runs', 0) < 1:
                    raise OrchestrationError('authorization_revoked', 'Frozen grant has no node run budget')
                expected_chain = contract.get('node_run_budget_chain', [])
                if (chain_rows != expected_chain
                        or contract.get('root_max_node_runs', 0) != (expected_chain[0]['max_node_runs'] if expected_chain else 0)
                        or not expected_chain or expected_chain[0]['delegation_id'] != root_id
                        or expected_chain[-1]['delegation_id'] != delegation_id
                        or expected_chain[-1]['max_node_runs'] != grant.get('max_node_runs')
                        or not isinstance(payload, dict)
                        or payload.get('node_id') not in grant.get('allowed_node_ids', [])):
                    raise OrchestrationError('authorization_revoked', 'Frozen node grant or budget chain changed')
                # _get() already checks the trusted SQL row scope (project,
                # owner, username, canvas and conversation). The JSON content
                # intentionally contains no duplicate copy of those columns.
                return {'contract': contract, 'action': action, 'step': step,
                        'node_context': action_node_context}
        except OrchestrationError:
            raise
        except OperationError as exc:
            raise OrchestrationError('authorization_revoked', 'Agent node action authorization was revoked') from exc

    async def _authorize_node_submission(self, run, attempt):
        """Scheduler edge callback for every external node-run attempt."""
        from novelvideo.freezone.orchestration.models import OrchestrationError
        marker = run.get('agent_execution') if isinstance(run, dict) else None
        transition = marker.get('accepted_transition') if isinstance(marker, dict) else None
        if not isinstance(marker, dict):
            raise OrchestrationError('authorization_revoked', 'Node run has no AgentRuns authorization marker')
        if (run.get('project_id') != self.ctx.project_id or run.get('canvas_id') != self.canvas_id
                or str((run.get('actor') or {}).get('id')) != str(self.ctx.requester_user_id)
                or marker.get('project_id') != self.ctx.project_id or marker.get('canvas_id') != self.canvas_id
                or str(marker.get('actor_id')) != str(self.ctx.requester_user_id)
                or marker.get('request_id') != (transition or {}).get('action_id')
                or marker.get('target_node_id') != (attempt or {}).get('node_id')):
            raise OrchestrationError('authorization_revoked', 'Node run marker no longer matches the accepted action')
        try:
            await self._fresh('editor')
            result = await registration_io(self._authorize_node_action_sync, transition,
                delegation_id=marker.get('delegation_id'), operation='node.run', payload=None)
        except OrchestrationError:
            raise
        except OperationError as exc:
            code = 'authorization_revoked' if exc.code in {
                'run_fenced', 'delegation_inactive', 'scope_violation', 'stale_input'
            } else 'authorization_unavailable'
            raise OrchestrationError(code, 'Agent node run authorization is no longer valid') from exc
        except Exception as exc:
            raise OrchestrationError('authorization_unavailable',
                                     'Could not recheck Agent node run authorization') from exc
        action = result['action']
        payload = action.get('payload') or {}
        grant = result['contract'].get('execution_authorization') or {}
        frozen_node = result.get('node_context')
        if (not frozen_node or marker.get('expected_revision') != frozen_node.get('expected_revision')
                or marker.get('target_input_fingerprint') != frozen_node.get('input_fingerprint')):
            raise OrchestrationError('authorization_revoked', 'Node run no longer matches its frozen Agent input')
        expected_pairs = sorted(grant.get('allowed_node_operations', []),
                                 key=lambda item: (item['node_id'], item['operation']))
        marker_pairs = sorted(marker.get('allowed_node_operations', []),
                              key=lambda item: (item.get('node_id', ''), item.get('operation', '')))
        chain = result['contract'].get('node_run_budget_chain', [])
        if (action.get('operation') != 'node.run'
                or marker.get('operation') != payload.get('operation')
                or marker.get('target_node_id') != payload.get('node_id')
                or marker.get('authorization_id') != result['contract'].get('authorization_id')
                or marker.get('root_delegation_id') != (result['contract'].get('root_delegation_id')
                                                         or marker.get('delegation_id'))
                or marker.get('allowed_node_ids') != grant.get('allowed_node_ids')
                or marker_pairs != expected_pairs
                or marker.get('read_node_ids') != result['contract'].get('read_node_ids', [])
                or marker.get('node_run_budget_chain') != chain
                or marker.get('max_node_runs') != grant.get('max_node_runs')
                or marker.get('root_max_node_runs') != result['contract'].get('root_max_node_runs')):
            raise OrchestrationError('authorization_revoked', 'Node run marker does not match the frozen grant')

    def _authorize_node_projection_sync(self, run, attempt):
        """Fence canvas adoption against stop/revoke while the canvas CAS lock is held."""
        marker = run.get('agent_execution') if isinstance(run, dict) else None
        transition = marker.get('accepted_transition') if isinstance(marker, dict) else None
        if (not isinstance(marker, dict) or run.get('project_id') != self.ctx.project_id
                or run.get('canvas_id') != self.canvas_id
                or str((run.get('actor') or {}).get('id')) != str(self.ctx.requester_user_id)
                or marker.get('project_id') != self.ctx.project_id
                or marker.get('canvas_id') != self.canvas_id
                or str(marker.get('actor_id')) != str(self.ctx.requester_user_id)
                or marker.get('request_id') != (transition or {}).get('action_id')
                or marker.get('target_node_id') != (attempt or {}).get('node_id')):
            raise OrchestrationError('authorization_revoked', 'Node projection marker no longer matches the accepted action')
        result = self._authorize_node_action_sync(transition,
            delegation_id=marker.get('delegation_id'), operation='node.run', payload=None)
        action = result['action']
        payload = action.get('payload') or {}
        contract = result['contract']
        grant = contract.get('execution_authorization') or {}
        frozen_node = result.get('node_context')
        if (not frozen_node or marker.get('expected_revision') != frozen_node.get('expected_revision')
                or marker.get('target_input_fingerprint') != frozen_node.get('input_fingerprint')):
            raise OrchestrationError('authorization_revoked', 'Node projection no longer matches its frozen Agent input')
        expected_pairs = sorted(grant.get('allowed_node_operations', []),
                                 key=lambda item: (item['node_id'], item['operation']))
        marker_pairs = sorted(marker.get('allowed_node_operations', []),
                              key=lambda item: (item.get('node_id', ''), item.get('operation', '')))
        chain = contract.get('node_run_budget_chain', [])
        if (action.get('operation') != 'node.run'
                or marker.get('operation') != payload.get('operation')
                or marker.get('target_node_id') != payload.get('node_id')
                or marker.get('authorization_id') != contract.get('authorization_id')
                or marker.get('root_delegation_id') != (contract.get('root_delegation_id')
                                                         or marker.get('delegation_id'))
                or marker.get('allowed_node_ids') != grant.get('allowed_node_ids')
                or marker_pairs != expected_pairs
                or marker.get('read_node_ids') != contract.get('read_node_ids', [])
                or marker.get('node_run_budget_chain') != chain
                or marker.get('max_node_runs') != grant.get('max_node_runs')
                or marker.get('root_max_node_runs') != contract.get('root_max_node_runs')):
            raise OrchestrationError('authorization_revoked', 'Node projection marker does not match the frozen grant')

    async def drive(self, identifier):
        owner = uuid4().hex
        try:
            claimed = await self._send('claim', {'id': identifier, 'owner': owner})
            if claimed['run']['state'] in TERMINAL:
                return
            while True:
                value = await registration_io(self._read_value, identifier, raw=True)
                if value['state'] in TERMINAL:
                    return
                if value['state'] == 'queued':
                    await self._send('prepare', {'id': identifier, 'owner': owner})
                    value = await registration_io(self._read_value, identifier, raw=True)
                    checkpoint('before_model')
                    result = await self._model(value, owner)
                    checkpoint('after_model')
                    await self._send('decision', {'id': identifier, 'owner': owner, 'result': result})
                    checkpoint('after_decision')
                value = await registration_io(self._read_value, identifier, raw=True)
                step = value['rounds'][-1]
                index = len(value['rounds']) - 1
                tools = _RunTools(self, identifier, owner, step['delegation_id'], step=step,
                                  round_index=index)
                for i, action in enumerate(step['decision']['actions']):
                    if i not in step['actions_done']:
                        action_id = self._action_id(identifier, index, i)
                        transition = {'agent_run_id': identifier, 'step_id': step['attempt_id'],
                                      'action_id': action_id, 'action_index': i}
                        await tools.execute({'request_id': action_id, **action},
                                             accepted_transition=transition)
                        checkpoint('after_action')
                        await self._send('action_done', {'id': identifier, 'owner': owner, 'index': i})
                for i, child in enumerate(step['decision']['delegations']):
                    if i < len(step['children_done']):
                        continue
                    value = await registration_io(self._read_value, identifier, raw=True)
                    if len(value['rounds']) + len(value['pending']) > value['max_model_calls']:
                        await self._send('children_limited', {'id': identifier, 'owner': owner})
                        break
                    specialist = next((a for a in value['specialists'] if a['adoption_id'] == child['adoption_id']), None)
                    require(specialist is not None, '专业Agent不在本次明确选择中', 'capability_denied')
                    parent = self.delegations.get(step['delegation_id'])['contract']
                    AgentTools._indices(child['input_indices'], len(parent['inputs']))
                    body = {'canvas_id': self.canvas_id, 'conversation_id': self.conversation_id,
                        'parent_id': step['delegation_id'], 'scope': {'kind': 'nodes', 'node_ids': child['target_node_ids']},
                        'agent': {'kind': 'adoption', 'adoption_id': specialist['adoption_id'], 'revision': specialist['revision']},
                        'input_refs': [parent['inputs'][j]['ref'] for j in child['input_indices']],
                        'allowed_operations': child['allowed_operations'], 'goal': child['goal'], 'acceptance': child['acceptance']}
                    if child.get('execution_authorization') is not None:
                        body['execution_authorization'] = deepcopy(child['execution_authorization'])
                    children = _RunDelegations(self, identifier, owner, step['delegation_id'])
                    child_request = f'run:{identifier}:{index}:child:{i}'
                    previous = children.receipt(child_request)
                    if previous is None:
                        preview = await registration_io(children.preview, body)
                        previous = await children.apply({'request_id': child_request, 'action': 'delegation.create',
                            'payload': {**body, 'preview_proof': preview['proof']}})
                    await self._send('child_done', {'id': identifier, 'owner': owner, 'child_id': previous['delegation_id']})
                await self._send('finish_round', {'id': identifier, 'owner': owner})
        except asyncio.CancelledError:
            # Durable in-flight evidence is deliberately left unknown for recovery.
            raise
        except Exception as exc:
            if isinstance(exc, OperationError) and exc.code in {'run_busy', 'run_fenced'}:
                return
            error = ({'code': exc.code, 'message': str(exc), **exc.details} if isinstance(exc, OperationError)
                     else {'code': 'model_submission_unknown', 'message': '运行中断，未自动重试模型请求'})
            try:
                await self._send('failure', {'id': identifier, 'owner': owner, 'error': error})
            except Exception:
                # Current ACL/lease may have been revoked. No stale worker writes.
                return


class _RunTools(AgentTools):
    def __init__(self, runs, identifier, owner, delegation_id, *, step, round_index):
        super().__init__(runs.ctx, username=runs.username, canvas_id=runs.canvas_id,
                         conversation_id=runs.conversation_id, delegation_id=delegation_id,
                         resolve_context=runs.resolve_context,
                         authorize_submission=runs._authorize_node_submission,
                         authorize_projection=runs._authorize_node_projection_sync,
                         authorize_action=lambda transition, *, operation, payload: runs._authorize_node_action(
                             transition, delegation_id=delegation_id, operation=operation, payload=payload),
                         node_context=step.get('node_context', {}))
        self.runs, self.identifier, self.owner = runs, identifier, owner
        self.step_id, self.round_index = step['attempt_id'], round_index

    async def execute(self, body, *, accepted_transition=None):
        operation = body.get('operation') if isinstance(body, dict) else None
        heartbeat_task = None
        if operation in {'node.patch', 'node.run'}:
            require(isinstance(accepted_transition, dict)
                    and accepted_transition.get('agent_run_id') == self.identifier
                    and accepted_transition.get('step_id') == self.step_id
                    and accepted_transition.get('action_id') == body.get('request_id'),
                    '节点动作缺少稳定 AgentRuns 接纳身份', 'run_required')
            admitted = await self.runs._send('admit_action', {'id': self.identifier, 'owner': self.owner,
                'index': accepted_transition['action_index'], 'action_id': accepted_transition['action_id']})
            persisted_step = admitted['run']['rounds'][self.round_index]
            action_context = persisted_step.get('action_node_context', {}).get(
                str(accepted_transition['action_index']))
            require(action_context is not None, '节点动作没有冻结的逐步输入上下文', 'run_fenced')
            self.node_context = {action_context['node_id']: action_context}
            heartbeat_task = asyncio.create_task(self._renew_node_action_lease())
        try:
            return await super().execute(body, accepted_transition=accepted_transition)
        finally:
            if heartbeat_task is not None:
                if not heartbeat_task.done():
                    heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

    async def _renew_node_action_lease(self):
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                await self.runs._send('heartbeat', {'id': self.identifier, 'owner': self.owner})
            except OperationError as exc:
                if exc.code == 'run_fenced':
                    # Stop/lease loss is terminal for renewal. The in-flight
                    # node call continues reconciling its durable request, but
                    # it cannot restore the outer AgentRuns lease.
                    return
                raise

    def dispatch(self, conn, request, operation_id):
        value = self.runs._fence(conn, self.identifier, self.owner)
        require(value['state'] == 'applying', '当前回合不允许写入', 'run_fenced')
        return super().dispatch(conn, request, operation_id)


class _RunDelegations(Delegations):
    def __init__(self, runs, identifier, owner, parent_id):
        super().__init__(runs.ctx, username=runs.username, authority='agent', parent_id=parent_id)
        self.runs, self.identifier, self.owner = runs, identifier, owner

    async def apply(self, body):
        async with asset_project_lock(self.ctx.state_dir):
            await self.runs._fresh('editor')
            return await super().apply(body)

    def dispatch(self, conn, request, operation_id):
        self.runs._fence(conn, self.identifier, self.owner)
        return super().dispatch(conn, request, operation_id)
