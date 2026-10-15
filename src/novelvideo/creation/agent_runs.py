"""Durable bounded Decision runs. Unknown provider submissions are never resent."""
import asyncio
from copy import deepcopy
import json
import time
from uuid import uuid4

from novelvideo.production_workflow.coordination import asset_project_lock
from . import agent_model
from .agent_tools import AgentTools
from .asset_adapter import registration_io
from .asset_reference import table
from .delegations import Delegations
from .library_references import digest, packed
from .ontology import DomainCommand, ProjectDomain, exact, integer, require, text
from .operations import OperationError
from .store import ObjectStore


LEASE_SECONDS = 30
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
                          conversation_id=self.conversation_id, delegation_id=identifier, resolve_context=self.resolve_context)

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
        return await self._send(action, {'id': identifier, 'expected_revision': body['expected_revision']}, body['request_id'])

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

    def dispatch(self, conn, request, operation_id):
        p, action = request.payload, request.action
        if action == 'start':
            contract = self._validate_current(conn, p['delegation_id'])
            require(digest(contract) == text(p['contract_hash'], 128), '委托快照不匹配', 'stale_input')
            limit = integer(p['max_model_calls'], 1)
            require(limit <= 3, '最多允许三次明确授权的模型调用')
            ids = p['specialist_ids']
            require(isinstance(ids, list) and len(ids) <= 10 and all(isinstance(i, str) for i in ids)
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
                contract = self._validate_current(conn, value['pending'][0])
                metadata = agent_model.availability(self.ctx)
                require(metadata['available'] and metadata['route'] == value['route']
                        and metadata['configuration_fingerprint'] == value['configuration_fingerprint'],
                        '模型配置已变化，请重新发起任务', 'runtime_configuration_changed')
                prompt = packed({'goal': contract['goal'], 'agent': contract['agent']['effective'],
                    'nodes': contract['expanded_scope']['nodes'], 'inputs': contract['inputs'],
                    'context': contract['context'], 'acceptance': contract['acceptance'],
                    'allowed_operations': contract['allowed_operations'], 'specialists': value['specialists'],
                    'outputs': self._outputs(conn, value), 'remaining_model_calls': value['max_model_calls'] - len(value['rounds']),
                    'payload_contract': {'common': ['target_node_ids', 'input_indices', 'acceptance_indices'],
                        'proposal.create': ['title', 'text'], 'media.propose': ['title', 'intent', 'modality'],
                        'ontology.definition.draft': ['definition']}})
                require(len(prompt.encode()) <= 250_000, '冻结上下文过大，请缩小范围', 'context_too_large')
                value['rounds'].append({'delegation_id': value['pending'][0], 'attempt_id': uuid4().hex,
                    'phase': 'model_inflight', 'prompt': prompt, 'actions_done': [], 'children_done': []})
                value['state'] = 'model_inflight'
            elif action == 'decision':
                decision = agent_model.Decision.model_validate(p['result']['decision']).model_dump(mode='json')
                require(len(packed(decision).encode()) <= 250_000, '模型输出过大')
                step = value['rounds'][-1]
                require(step['phase'] == 'model_inflight', '模型回合已完成', 'run_fenced')
                step.update(phase='decision_saved', decision=decision, usage=p['result'].get('usage', {}))
                value['state'] = 'applying'
            elif action == 'action_done':
                value['rounds'][-1]['actions_done'].append(p['index'])
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
                for completed in value['rounds']:
                    if completed['phase'] != 'applied':
                        continue
                    remaining = list(completed['decision']['unmet'])
                    if completed.get('children_limited'):
                        remaining.append('专业委派超出本次模型调用上限，未执行剩余委派')
                    by_delegation[completed['delegation_id']] = remaining
                value['unmet_by_delegation'] = by_delegation
                value['unmet'] = [item for remaining in by_delegation.values() for item in remaining]
                value['pending'].pop(0)
                if not decision['finished'] and decision['actions']:
                    value['pending'].append(step['delegation_id'])
                if not value['pending'] or len(value['rounds']) >= value['max_model_calls']:
                    value['state'] = 'partial' if value['pending'] or value['unmet'] or not self._outputs(conn, value) else 'delivered'
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
                if time.monotonic() - heartbeat >= 5:
                    await self._send('heartbeat', {'id': value['id'], 'owner': owner})
                    heartbeat = time.monotonic()
            return task.result()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.wait({task}, timeout=1)
                task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

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
                tools = _RunTools(self, identifier, owner, step['delegation_id'])
                for i, action in enumerate(step['decision']['actions']):
                    if i not in step['actions_done']:
                        await tools.execute({'request_id': self._action_id(identifier, index, i), **action})
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
    def __init__(self, runs, identifier, owner, delegation_id):
        super().__init__(runs.ctx, username=runs.username, canvas_id=runs.canvas_id,
                         conversation_id=runs.conversation_id, delegation_id=delegation_id, resolve_context=runs.resolve_context)
        self.runs, self.identifier, self.owner = runs, identifier, owner

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
