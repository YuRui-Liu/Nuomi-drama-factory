"""Fixed task contracts only: no provider, token minting or tool execution."""
from copy import deepcopy
from contextlib import ExitStack
import json
from types import SimpleNamespace
from uuid import uuid4

import portalocker

from novelvideo.freezone.canvas_lock import canvas_lock_path
from . import agent_context
from .asset_reference import table
from .canvas_workspaces import list_canvases
from .library_references import digest, packed
from .ontology import ProjectDomain, exact, integer, require, text
from .operations import OperationError
from .personal_library import ProjectLibrary


OPERATIONS = {'context.read': 'context.read', 'proposal.create': 'proposal.create',
              'ontology.definition.draft': 'ontology.draft', 'media.propose': 'media.propose',
              'node.patch': 'node.patch', 'node.run': 'node.run'}
MAX_NODE_RUNS = 24
_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS project_main_agent_versions (
        project_id TEXT NOT NULL, revision INTEGER NOT NULL, content TEXT NOT NULL,
        PRIMARY KEY(project_id,revision))''',
    '''CREATE TABLE IF NOT EXISTS agent_delegations (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, owner_id TEXT NOT NULL,
        owner_username TEXT NOT NULL, canvas_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
        parent_id TEXT, revision INTEGER NOT NULL, state TEXT NOT NULL, contract TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS agent_delegation_events (
        delegation_id TEXT NOT NULL, revision INTEGER NOT NULL, operation_id TEXT NOT NULL,
        content TEXT NOT NULL, PRIMARY KEY(delegation_id,revision))''',
)


class Delegations(ProjectDomain):
    domain = 'delegations'
    schema = _SCHEMA

    def __init__(self, ctx, *, username, authority='agent', parent_id=None):
        super().__init__(ctx, authority=authority)
        self.username = text(username, 200)
        # A future broker supplies this trusted identity, never a JSON authority flag.
        if parent_id is not None:
            text(parent_id, 128)
        self.parent_id = parent_id

    def _agent(self, conn, identifier, revision=None):
        text(identifier, 128)
        # Reuse project ownership/version resolution without opening a personal library.
        reader = SimpleNamespace(ctx=self.ctx, kind='agent')
        return ProjectLibrary._adoption(reader, conn, identifier, revision)

    def _main(self, conn):
        row = conn.execute('SELECT content FROM project_main_agent_versions WHERE project_id=? ORDER BY revision DESC LIMIT 1',
                           (self.ctx.project_id,)).fetchone() if conn and table(conn, 'project_main_agent_versions') else None
        return json.loads(row[0]) if row else {'binding_revision': 0, 'adoption_id': None}

    def main_agent(self):
        with self.read() as conn:
            main = self._main(conn)
            return {**main, 'current_agent': self._agent(conn, main['adoption_id']) if main['adoption_id'] else None}

    def receipt(self, request_id):
        text(request_id, 128)
        with self.read() as conn:
            if not conn or not table(conn, 'operations'):
                return None
            row = conn.execute('SELECT actor_id FROM operations WHERE request_id=?', (request_id,)).fetchone()
            if not row or row[0] != self.ctx.requester_user_id:
                return None
        result = super().receipt(request_id)
        if result and 'contract' in result:
            self._owner(result['contract'])
        elif result and result.get('delegation_id'):
            self.get(result['delegation_id'])
        return result

    def _replay(self, conn, request, request_hash):
        result = super()._replay(conn, request, request_hash)
        if result and 'contract' in result:
            self._owner(result['contract'])
        elif result and result.get('delegation_id'):
            self._get(conn, result['delegation_id'])
        return result

    def _owner(self, contract):
        require(contract['owner_id'] == self.ctx.requester_user_id and contract['owner_username'] == self.username,
                '委托属于其他会话用户', 'scope_violation')

    @staticmethod
    def _execution_authorization(value, expanded, allowed_operations):
        if value is None:
            return None
        exact(value, {'allowed_node_ids', 'allowed_node_operations', 'max_node_runs'})
        node_ids = value['allowed_node_ids']
        require(isinstance(node_ids, list) and 0 < len(node_ids) <= 1000
                and all(isinstance(node_id, str) and node_id for node_id in node_ids)
                and len(node_ids) == len(set(node_ids)), '节点执行授权范围无效', 'capability_denied')
        node_set = set(node_ids)
        require(node_set <= set(expanded['node_ids']), '节点执行授权超出委托目标', 'scope_violation')
        snapshots = {node['node_id']: node for node in expanded['nodes']}
        operations = value['allowed_node_operations']
        require(isinstance(operations, list) and 0 < len(operations) <= 1000,
                '节点操作授权无效', 'capability_denied')
        pairs = []
        for entry in operations:
            exact(entry, {'node_id', 'operation'})
            node_id = text(entry['node_id'], 128)
            operation = text(entry['operation'], 100)
            require(node_id in node_set and node_id in snapshots, '节点操作授权超出目标范围', 'scope_violation')
            if operation == 'patch':
                require('node.patch' in allowed_operations, '委托没有节点编辑能力', 'capability_denied')
            else:
                require('node.run' in allowed_operations, '委托没有节点运行能力', 'capability_denied')
                from novelvideo.freezone.orchestration.capabilities import catalog_by_type
                catalog = catalog_by_type().get(snapshots[node_id]['node_type'])
                require(catalog is not None and operation in {item['name'] for item in catalog['operations']},
                        '节点操作未注册', 'capability_denied')
            pairs.append({'node_id': node_id, 'operation': operation})
        pair_keys = [(item['node_id'], item['operation']) for item in pairs]
        require(len(pair_keys) == len(set(pair_keys)), '节点操作授权不能重复', 'capability_denied')
        require({node_id for node_id, _ in pair_keys} == node_set,
                '节点列表必须与授权操作覆盖范围一致', 'capability_denied')
        run_granted = any(operation != 'patch' for _, operation in pair_keys)
        max_node_runs = integer(value['max_node_runs'], 0)
        require(max_node_runs <= MAX_NODE_RUNS, f'本次最多授权 {MAX_NODE_RUNS} 次节点运行', 'capability_denied')
        require(max_node_runs > 0 if run_granted else max_node_runs == 0,
                '节点运行次数必须与具体运行授权一致', 'capability_denied')
        return {'allowed_node_ids': sorted(node_set),
                'allowed_node_operations': sorted(pairs, key=lambda item: (item['node_id'], item['operation'])),
                'max_node_runs': max_node_runs}

    def _get(self, conn, identifier):
        text(identifier, 128)
        row = conn.execute('SELECT * FROM agent_delegations WHERE id=? AND project_id=?',
                           (identifier, self.ctx.project_id)).fetchone() if conn and table(conn, 'agent_delegations') else None
        require(row is not None, '委托不存在或不属于当前项目', 'scope_violation')
        value = dict(row)
        value['contract'] = json.loads(value['contract'])
        self._owner(value['contract'])
        return value

    def _active(self, conn, row):
        seen = set()
        while row:
            require(row['id'] not in seen, '委托父链损坏', 'scope_violation')
            seen.add(row['id'])
            if row['state'] == 'revoked':
                return False
            row = self._get(conn, row['parent_id']) if row['parent_id'] else None
        return True

    def _accepted_node_transitions(self, conn, contract):
        """Return only durable node changes accepted under this authorization root."""
        root_id = contract.get('root_delegation_id')
        authorization_id = contract.get('authorization_id')
        if (not root_id or not authorization_id or not table(conn, 'agent_delegations')
                or not table(conn, 'delegation_outputs')):
            return {}
        contracts = {}
        for row in conn.execute('''SELECT id,contract FROM agent_delegations
                WHERE project_id=? AND owner_id=? AND owner_username=? AND canvas_id=? AND conversation_id=?''',
                (self.ctx.project_id, self.ctx.requester_user_id, self.username,
                 contract['canvas_id'], contract['conversation_id'])):
            value = json.loads(row['contract'])
            if (value.get('root_delegation_id') == root_id
                    and value.get('authorization_id') == authorization_id):
                contracts[row['id']] = value
        if not contracts:
            return {}
        placeholders = ','.join('?' for _ in contracts)
        rows = conn.execute(f'''SELECT content FROM delegation_outputs WHERE project_id=? AND canvas_id=?
            AND conversation_id=? AND delegation_id IN ({placeholders}) ORDER BY rowid''',
            (self.ctx.project_id, contract['canvas_id'], contract['conversation_id'], *contracts.keys()))
        accepted = {}
        for row in rows:
            output = json.loads(row['content'])
            node_id = output.get('node_id')
            state = output.get('accepted_after')
            origin = contracts.get(output.get('delegation_id'), {})
            grant = origin.get('execution_authorization') or {}
            operation = ('patch' if output.get('operation') == 'node.patch'
                         else output.get('node_operation'))
            authorized = any(item == {'node_id': node_id, 'operation': operation}
                             for item in grant.get('allowed_node_operations', []))
            valid_receipt = (output.get('operation') == 'node.patch' and output.get('state') == 'patched'
                             or output.get('operation') == 'node.run'
                             and output.get('status') == 'succeeded'
                             and output.get('projection', {}).get('status') == 'verified_projected')
            if (not node_id or not isinstance(state, dict) or state.get('node_id') != node_id
                    or not authorized or not valid_receipt):
                continue
            accepted[node_id] = {
                'input_fingerprint': state.get('input_fingerprint'),
                'result_fingerprint': state.get('result_fingerprint'),
                'media_fingerprint': state.get('media_fingerprint'),
                'evidence': {'delegation_id': output['delegation_id'], 'output_id': output.get('id'),
                    'operation_id': output.get('operation_id'), 'operation': output.get('operation'),
                    'node_run_id': output.get('node_run_id')},
            }
        return accepted

    def get(self, identifier):
        with self.read() as conn:
            row = self._get(conn, identifier)
            return {**row, 'delegation_id': row['id'],
                    'effective_state': row['state'] if self._active(conn, row) else 'revoked'}

    def catalog(self, canvas_id, conversation_id):
        agent_context.conversation(self.ctx, self.username, canvas_id, conversation_id)
        with self.read() as conn:
            if not conn or not table(conn, 'agent_delegations'):
                return []
            ids = [r[0] for r in conn.execute('''SELECT id FROM agent_delegations
                WHERE project_id=? AND owner_id=? AND owner_username=? AND canvas_id=? AND conversation_id=? ORDER BY rowid''',
                (self.ctx.project_id, self.ctx.requester_user_id, self.username, canvas_id, conversation_id))]
            return [{**self._get(conn, identifier), 'effective_state': 'prepared' if self._active(conn, self._get(conn, identifier)) else 'revoked'}
                    for identifier in ids]

    def preview(self, body):
        # GET-like computation cannot create a .lock file. Applied workspaces
        # already have one from projection; missing locks require write recovery.
        with self.read() as conn:
            needed = agent_context.required_canvases(self.ctx, conn, body)
        registered = {c['canvas_id'] for c in list_canvases(self.ctx.state_dir) if c['status'] == 'applied'}
        require(needed <= registered, '输入画布尚未登记或不属于当前项目', 'scope_violation')
        with ExitStack() as locks:
            for canvas_id in sorted(needed):
                try:
                    handle = locks.enter_context(canvas_lock_path(self.ctx.state_dir, canvas_id).open('rb'))
                    portalocker.lock(handle, portalocker.LOCK_SH | portalocker.LOCK_NB)
                    locks.callback(portalocker.unlock, handle)
                except (FileNotFoundError, portalocker.exceptions.AlreadyLocked) as exc:
                    raise OperationError('canvas_busy', '画布正在保存或需要恢复，请稍后重新比较') from exc
            with self.read() as conn:
                # ProjectDomain.read already opens a readonly SQL transaction.
                require(agent_context.required_canvases(self.ctx, conn, body) == needed,
                        '输入对象归属已变化，请重新比较', 'stale_input')
                return self._preview(conn, body)

    def _preview(self, conn, body):
        exact(body, {'canvas_id', 'conversation_id', 'scope', 'agent', 'input_refs', 'goal',
                     'allowed_operations', 'acceptance'}, {'parent_id', 'execution_authorization'})
        parent_id = text(body['parent_id'], 128) if 'parent_id' in body else None
        if self.authority != 'user':
            require(self.parent_id is not None and parent_id == self.parent_id,
                    'Agent 只能在可信父委托中缩小范围', 'scope_violation')
        fence = agent_context.conversation(self.ctx, self.username, body['canvas_id'], body['conversation_id'])
        expanded = agent_context.graph_scope(self.ctx, body['canvas_id'], body['scope'])
        selected_agent = body['agent']
        require(isinstance(selected_agent, dict), '请选择项目采用的 Agent')
        if selected_agent.get('kind') == 'main':
            exact(selected_agent, {'kind'})
            main = self._main(conn)
            require(main['adoption_id'], '请先明确选择项目主创', 'scope_violation')
            agent = self._agent(conn, main['adoption_id'])
        else:
            exact(selected_agent, {'kind', 'adoption_id', 'revision'})
            require(selected_agent['kind'] == 'adoption', 'Agent 选择无效')
            integer(selected_agent['revision'], 1)
            agent = self._agent(conn, selected_agent['adoption_id'], selected_agent['revision'])
            main = None
        allowed = body['allowed_operations']
        require(isinstance(allowed, list) and all(isinstance(x, str) for x in allowed)
                and len(allowed) == len(set(allowed)) and set(allowed) <= OPERATIONS.keys(), '未知操作权限', 'capability_denied')
        require(all(OPERATIONS[x] in agent['effective']['capabilities'] for x in allowed),
                '操作超出所选 Agent 的可信能力', 'capability_denied')
        text(body['goal'], 12000)
        acceptance = body['acceptance']
        require(isinstance(acceptance, list) and 0 < len(acceptance) <= 30, '请提供明确交付标准')
        for criterion in acceptance:
            text(criterion, 2000)
        fixed_inputs = agent_context.inputs(self.ctx, conn, body['input_refs'])
        # Read access is frozen only for explicitly selected scope nodes and
        # explicit same-canvas node inputs. Never discover and grant graph
        # ancestors from the live canvas after the preview was accepted.
        read_node_ids = set(expanded['node_ids'])
        read_node_ids.update(item['ref']['node_id'] for item in fixed_inputs
                             if item['ref']['kind'] == 'canvas_node'
                             and item['ref']['canvas_id'] == body['canvas_id'])
        execution_authorization = self._execution_authorization(
            body.get('execution_authorization'), expanded, allowed)
        parent_hash = None
        root_delegation_id = None
        authorization_id = None
        root_max_node_runs = 0
        node_run_budget_chain = []
        parent_transition_evidence = []
        if parent_id:
            parent = self._get(conn, parent_id)
            require(self._active(conn, parent), '父委托已停止后续操作', 'parent_delegation_inactive')
            original = parent['contract']
            require(fence == original['session_fence'], '父委托会话已清理，不能扩大到新会话代次', 'stale_input')
            require(original['canvas_id'] == body['canvas_id'] and original['conversation_id'] == body['conversation_id'],
                    '子委托不能改变会话或写入画布', 'scope_violation')
            require(set(expanded['node_ids']) <= set(original['expanded_scope']['node_ids'])
                    and set(allowed) <= set(original['allowed_operations']), '子委托不能扩大范围或操作权限', 'scope_violation')
            require(body['scope']['kind'] != 'canvas' or original['expanded_scope']['scope']['kind'] == 'canvas',
                    '子委托不能将局部目标扩大为整张画布准备空间', 'scope_violation')
            old_nodes = {n['node_id']: n for n in original['expanded_scope']['nodes']}
            accepted = self._accepted_node_transitions(conn, original)
            for node in expanded['nodes']:
                node_id = node['node_id']
                frozen = old_nodes[node_id]
                require(node['node_type'] == frozen['node_type'] and node['parent_id'] == frozen['parent_id'],
                        '父委托节点类型或归属已变化', 'stale_input', node_id=node_id)
                transition = accepted.get(node_id)
                if transition:
                    require(node['input_fingerprint'] == transition.get('input_fingerprint')
                            and node['result_fingerprint'] == transition.get('result_fingerprint')
                            and node['media_evidence']['locator_fingerprint'] == transition.get('media_fingerprint'),
                            '父委托节点与已接受结果不一致', 'stale_input', node_id=node_id)
                    parent_transition_evidence.append(deepcopy(transition['evidence']))
                else:
                    require(node == frozen, '父委托输入已变化', 'stale_input', node_id=node_id)
            old_membership = original['expanded_scope']['membership']
            require(all(m in old_membership for m in expanded['membership']), '父委托成员归属已变化', 'stale_input')
            old_inputs = {digest(item['ref']): item for item in original['inputs']}
            for item in fixed_inputs:
                frozen_input = old_inputs.get(digest(item['ref']))
                require(frozen_input is not None, '子委托不能增加或替换固定输入', 'scope_violation')
                ref = item['ref']
                input_node_id = (ref.get('node_id') if ref.get('kind') == 'canvas_node'
                                 and ref.get('canvas_id') == body['canvas_id'] else None)
                transition = accepted.get(input_node_id) if input_node_id else None
                if transition:
                    snapshot = item['snapshot']
                    require(item.get('membership') == frozen_input.get('membership')
                            and snapshot['input_fingerprint'] == transition.get('input_fingerprint')
                            and snapshot['result_fingerprint'] == transition.get('result_fingerprint')
                            and snapshot['media_evidence']['locator_fingerprint'] == transition.get('media_fingerprint'),
                            '固定节点输入与已接受结果不一致', 'stale_input', node_id=input_node_id)
                    parent_transition_evidence.append(deepcopy(transition['evidence']))
                else:
                    require(item == frozen_input, '父委托引用输入已变化', 'stale_input')
            require(read_node_ids <= set(original.get('read_node_ids', original['expanded_scope']['node_ids'])),
                    '子委托不能扩大冻结节点读取范围', 'scope_violation')
            parent_authorization = original.get('execution_authorization')
            if execution_authorization is not None:
                require(parent_authorization is not None, '父委托没有显式节点执行授权', 'capability_denied')
                require(set(execution_authorization['allowed_node_ids'])
                        <= set(parent_authorization['allowed_node_ids'])
                        and {(item['node_id'], item['operation'])
                             for item in execution_authorization['allowed_node_operations']}
                        <= {(item['node_id'], item['operation'])
                            for item in parent_authorization['allowed_node_operations']}
                        and execution_authorization['max_node_runs'] <= parent_authorization['max_node_runs'],
                        '子委托不能扩大节点、操作或运行次数授权', 'scope_violation')
                authorization_id = original.get('authorization_id')
                require(bool(authorization_id), '父委托授权身份无效', 'scope_violation')
            root_delegation_id = original.get('root_delegation_id') or parent_id
            root_max_node_runs = original.get('root_max_node_runs',
                (original.get('execution_authorization') or {}).get('max_node_runs', 0))
            node_run_budget_chain = deepcopy(original.get('node_run_budget_chain', []))
            parent_hash = digest(original)
        elif execution_authorization is not None:
            root_max_node_runs = execution_authorization['max_node_runs']
        contract = {'project_id': self.ctx.project_id, 'owner_id': self.ctx.requester_user_id,
                    'owner_username': self.username, 'canvas_id': body['canvas_id'], 'conversation_id': body['conversation_id'],
                    'session_fence': fence, 'parent_id': parent_id, 'parent_contract_hash': parent_hash,
                    'root_delegation_id': root_delegation_id, 'authorization_id': authorization_id,
                    'parent_transition_evidence': sorted(
                        {digest(item): item for item in parent_transition_evidence}.values(),
                        key=lambda item: (item.get('delegation_id', ''), item.get('output_id', ''))),
                    'root_max_node_runs': root_max_node_runs,
                    'node_run_budget_chain': node_run_budget_chain,
                    'agent': agent, 'main_binding': main, 'expanded_scope': expanded,
                    'inputs': fixed_inputs, 'read_node_ids': sorted(read_node_ids),
                    'goal': body['goal'], 'acceptance': acceptance,
                    'allowed_operations': sorted(allowed),
                    'execution_authorization': execution_authorization,
                    'context': agent_context.context(self.ctx, conn, body['canvas_id'], expanded, fixed_inputs),
                    'authority': {'grant': 'user' if self.authority == 'user' else 'parent_delegation',
                                  'facts': 'confirmed_sources_only', 'text_is_permission': False},
                    'execution_enabled': execution_authorization is not None}
        proof_content = deepcopy(contract)
        proof_content['expanded_scope'].pop('observed_canvas_revision')
        return {'proof': digest(proof_content), 'contract': contract}

    def dispatch(self, conn, request, operation_id):
        payload = request.payload
        if request.action in {'main.select', 'main.clear'}:
            self.user_only()
            required = {'expected_binding_revision'}
            if request.action == 'main.select':
                required |= {'adoption_id', 'expected_adoption_revision'}
            exact(payload, required)
            main = self._main(conn)
            require(integer(payload['expected_binding_revision']) == main['binding_revision'],
                    '项目主创指派已变化', 'revision_conflict')
            if request.action == 'main.select':
                agent = self._agent(conn, payload['adoption_id'])
                require(integer(payload['expected_adoption_revision'], 1) == agent['revision'],
                        '项目 Agent 配置已变化', 'revision_conflict')
            value = {'binding_revision': main['binding_revision'] + 1, 'adoption_id': payload.get('adoption_id'),
                     'actor_id': self.ctx.requester_user_id, 'operation_id': operation_id}
            conn.execute('INSERT INTO project_main_agent_versions VALUES (?,?,?)',
                         (self.ctx.project_id, value['binding_revision'], packed(value)))
            return value
        if request.action == 'delegation.create':
            require('preview_proof' in payload, '缺少冻结输入证明')
            body = {k: v for k, v in payload.items() if k != 'preview_proof'}
            preview = self._preview(conn, body)
            require(preview['proof'] == payload['preview_proof'], '输入、范围或配置已变化，请重新比较', 'stale_input')
            identifier = uuid4().hex
            contract = preview['contract']
            contract['root_delegation_id'] = contract.get('root_delegation_id') or identifier
            if contract.get('execution_authorization') is not None:
                chain = deepcopy(contract.get('node_run_budget_chain', []))
                if chain:
                    require(body.get('parent_id') and chain[-1]['delegation_id'] == body['parent_id'],
                            '节点运行预算父链无效', 'scope_violation')
                else:
                    require(body.get('parent_id') is None, '缺少冻结的节点运行预算父链', 'scope_violation')
                chain.append({'delegation_id': identifier,
                              'max_node_runs': contract['execution_authorization']['max_node_runs']})
                contract['node_run_budget_chain'] = chain
            else:
                contract['node_run_budget_chain'] = []
            if contract.get('execution_authorization') is not None and contract.get('authorization_id') is None:
                contract['authorization_id'] = digest({'root_delegation_id': identifier,
                    'contract_hash': digest(contract)})
            conn.execute('INSERT INTO agent_delegations VALUES (?,?,?,?,?,?,?,?,?,?)',
                         (identifier, self.ctx.project_id, self.ctx.requester_user_id, self.username,
                          body['canvas_id'], body['conversation_id'], body.get('parent_id'), 1, 'prepared', packed(contract)))
            conn.execute('INSERT INTO agent_delegation_events VALUES (?,?,?,?)',
                         (identifier, 1, operation_id, packed({'state': 'prepared', 'contract_hash': digest(contract)})))
            return {'status': 'prepared', 'delegation_id': identifier, 'revision': 1,
                    'contract_hash': digest(contract), 'contract': contract}
        if request.action == 'delegation.revoke':
            self.user_only()
            exact(payload, {'delegation_id', 'expected_revision', 'reason'})
            row = self._get(conn, payload['delegation_id'])
            require(integer(payload['expected_revision'], 1) == row['revision'], '委托版本已变化', 'revision_conflict')
            text(payload['reason'], 2000)
            revision = row['revision'] + 1
            conn.execute('UPDATE agent_delegations SET state=?,revision=? WHERE id=?', ('revoked', revision, row['id']))
            conn.execute('INSERT INTO agent_delegation_events VALUES (?,?,?,?)',
                         (row['id'], revision, operation_id, packed({'state': 'revoked', 'reason': payload['reason'],
                                                                  'actor_id': self.ctx.requester_user_id})))
            return {'delegation_id': row['id'], 'revision': revision, 'state': 'revoked',
                    'provider_cancelled': False}
        require(False, '未知委托领域操作')
