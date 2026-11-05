"""Fixed task contracts only: no provider, token minting or tool execution."""
from copy import deepcopy
from contextlib import ExitStack
import hashlib
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
              'asset.design.create': 'media.propose',
              # Script drafts and fixed-version review receipts are limited,
              # reversible preparation under the existing proposal capability.
              'script.document.draft': 'proposal.create',
              'script.document.review': 'proposal.create',
              'node.patch': 'node.patch', 'node.run': 'node.run'}
MAX_NODE_RUNS = 24
FORMAL_SCRIPT_DELEGABLE_OPERATIONS = ['script.document.draft', 'script.document.review']
EMPTY_CANVAS_PREPARATION_OPERATIONS = frozenset({
    'context.read', 'proposal.create', 'ontology.definition.draft', 'media.propose',
    'asset.design.create', 'script.document.draft', 'script.document.review',
})
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

    def __init__(self, ctx, *, username, authority='agent', parent_id=None,
                 trusted_preparation_kind=None):
        super().__init__(ctx, authority=authority)
        self.username = text(username, 200)
        # A future broker supplies this trusted identity, never a JSON authority flag.
        if parent_id is not None:
            text(parent_id, 128)
        self.parent_id = parent_id
        if trusted_preparation_kind is not None:
            require(trusted_preparation_kind == 'formal_episode_script'
                    and authority == 'user' and parent_id is None,
                    '正式剧本准备身份只能由可信用户根委托提供', 'scope_violation')
        self.trusted_preparation_kind = trusted_preparation_kind

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

    def _script_document_transition_evidence(self, conn, item, parent_id, parent_contract):
        """Verify a child script input is a delivered draft from this run root."""
        ref = item['ref']
        origin = item['snapshot'].get('source_origin')
        require(ref.get('kind') == 'script_document' and isinstance(origin, dict)
                and origin.get('kind') == 'agent_script_draft'
                and ref.get('canvas_id') == parent_contract['canvas_id'],
                '新增正式剧本输入缺少受信运行来源', 'scope_violation')
        source_id = text(origin.get('delegation_id'), 128)
        source = self._get(conn, source_id)
        source_contract = source['contract']
        agent = source_contract.get('agent', {})
        expected_root = parent_contract.get('root_delegation_id') or parent_id
        source_root = source_contract.get('root_delegation_id') or source_id
        require(self._active(conn, source)
                and (source_contract.get('project_id'), source_contract.get('owner_id'),
                     source_contract.get('owner_username'), source_contract.get('canvas_id'),
                     source_contract.get('conversation_id')) ==
                    (self.ctx.project_id, self.ctx.requester_user_id, self.username,
                     parent_contract['canvas_id'], parent_contract['conversation_id'])
                and source_root == expected_root
                and 'script.document.draft' in source_contract.get('allowed_operations', [])
                and agent.get('adoption_id') == origin.get('member_id')
                and agent.get('revision') == origin.get('member_revision'),
                '正式剧本来源不属于当前父授权链或身份快照', 'scope_violation')
        run_row = conn.execute('''SELECT content FROM agent_runs WHERE project_id=? AND owner_id=?
            AND username=? AND canvas_id=? AND conversation_id=? AND delegation_id=?''',
            (self.ctx.project_id, self.ctx.requester_user_id, self.username,
             parent_contract['canvas_id'], parent_contract['conversation_id'], source_id)).fetchone()
        require(run_row is not None, '正式剧本来源没有持久Agent运行', 'scope_violation')
        run = json.loads(run_row['content'])
        require(run.get('state') == 'delivered'
                and run.get('root_delegation_id', source_id) == expected_root,
                '正式剧本来源运行尚未交付或不属于当前父任务', 'dependency_unavailable')
        matched_request_id = None
        for round_index, step in enumerate(run.get('rounds', [])):
            for action_index, action in enumerate(step.get('decision', {}).get('actions', [])):
                if (step.get('delegation_id') == source_id
                        and action.get('operation') == 'script.document.draft'
                        and action_index in step.get('actions_done', [])):
                    request_id = f"run:{run['id']}:{round_index}:action:{action_index}"
                    receipt_row = conn.execute('''SELECT r.result FROM creative_domain_receipts r
                        JOIN operations o ON o.request_id=r.request_id WHERE r.request_id=?
                          AND r.domain='agent_tools' AND r.project_id=? AND o.actor_id=?''',
                        (request_id, self.ctx.project_id, self.ctx.requester_user_id)).fetchone()
                    if receipt_row is None:
                        continue
                    receipt = json.loads(receipt_row['result'])
                    output = receipt.get('output')
                    document_ref = output.get('document_ref', {}) if isinstance(output, dict) else {}
                    projection = output.get('projection') if isinstance(output, dict) else None
                    if (isinstance(output, dict) and receipt.get('delegation_id') == source_id
                            and output.get('operation') == 'script.document.draft'
                            and output.get('state') == 'draft'
                            and document_ref.get('document_id') == ref.get('document_id')
                            and document_ref.get('revision_id') == ref.get('revision_id')
                            and output.get('source_origin') == origin
                            and isinstance(projection, dict) and projection.get('status') == 'applied'):
                        matched_request_id = request_id
                        break
            if matched_request_id:
                break
        require(matched_request_id is not None, '正式剧本没有来源运行的已完成动作回执', 'scope_violation')
        receipt = conn.execute('''SELECT d.content,o.request_id,r.result FROM delegation_outputs d
            JOIN operations o ON o.id=d.operation_id JOIN creative_domain_receipts r ON r.request_id=o.request_id
            WHERE d.project_id=? AND d.owner_id=? AND d.canvas_id=? AND d.conversation_id=?
              AND d.delegation_id=? AND o.request_id=? AND o.actor_id=? AND r.domain='agent_tools'
              AND r.project_id=?''',
            (self.ctx.project_id, self.ctx.requester_user_id, parent_contract['canvas_id'],
             parent_contract['conversation_id'], source_id, matched_request_id,
             self.ctx.requester_user_id, self.ctx.project_id)).fetchone()
        require(receipt is not None, '正式剧本没有AgentTools输出记录', 'scope_violation')
        output = json.loads(receipt['content'])
        result = json.loads(receipt['result'])
        require(output.get('id') == result.get('output', {}).get('id')
                and output == result.get('output'),
                '正式剧本输出和持久回执不匹配', 'scope_violation')
        projection = output.get('projection')
        projection_node_id = projection.get('node_id') if isinstance(projection, dict) else None
        require(isinstance(projection_node_id, str) and projection.get('status') == 'applied',
                '正式剧本投影回执缺少固定节点', 'scope_violation')
        from novelvideo.freezone.canvas_store import read_canvas
        canvas = read_canvas(self.ctx.state_dir, parent_contract['canvas_id'])
        node = next((item for item in (canvas or {}).get('nodes', [])
                     if item.get('id') == projection_node_id), None)
        binding = (node or {}).get('data', {}).get('formalScriptRef', {})
        require(binding.get('document_id') == ref.get('document_id')
                and binding.get('revision_id') == ref.get('revision_id'),
                '正式剧本固定节点绑定已变化', 'stale_input')
        return {'kind': 'agent_script_draft', 'run_id': run['id'], 'delegation_id': source_id,
                'output_id': output['id'], 'operation_id': output.get('operation_id'),
                'projection_node_id': projection_node_id}

    def _own_script_projection_evidence(self, conn, parent, parent_contract):
        """Verify only this active root run's exact, read-only script projection."""
        parent_id = parent['id']
        if (parent.get('parent_id') is not None or parent_contract.get('parent_id') is not None
                or parent_contract.get('root_delegation_id') != parent_id
                or 'script.document.draft' not in parent_contract.get('allowed_operations', [])):
            return {}
        required_tables = {'agent_runs', 'creative_domain_receipts', 'operations',
                           'delegation_outputs', 'agent_script_draft_projections', 'projection_outbox'}
        if not conn or not all(table(conn, name) for name in required_tables):
            return {}
        run_row = conn.execute('''SELECT content FROM agent_runs WHERE project_id=? AND owner_id=?
            AND username=? AND canvas_id=? AND conversation_id=? AND delegation_id=?''',
            (self.ctx.project_id, self.ctx.requester_user_id, self.username,
             parent_contract['canvas_id'], parent_contract['conversation_id'], parent_id)).fetchone()
        if run_row is None:
            return {}
        try:
            run = json.loads(run_row['content'])
        except (TypeError, json.JSONDecodeError):
            return {}
        root_id = parent_contract.get('root_delegation_id') or parent_id
        if (run.get('delegation_id') != parent_id or run.get('root_delegation_id', parent_id) != root_id
                or run.get('state') not in {'applying', 'waiting_children'}):
            return {}

        from novelvideo.freezone.canvas_store import read_canvas
        canvas = read_canvas(self.ctx.state_dir, parent_contract['canvas_id'])
        current_nodes = {node.get('id'): node for node in (canvas or {}).get('nodes', [])}
        agent = parent_contract.get('agent', {})
        evidence = {}
        for round_index, step in enumerate(run.get('rounds', [])):
            if step.get('delegation_id') != parent_id:
                continue
            actions = step.get('decision', {}).get('actions', [])
            completed = set(step.get('actions_done', []))
            for action_index, action in enumerate(actions):
                if (action_index not in completed
                        or action.get('operation') != 'script.document.draft'):
                    continue
                request_id = f"run:{run['id']}:{round_index}:action:{action_index}"
                persisted = conn.execute('''SELECT d.content AS output,d.operation_id AS action_operation_id,
                        r.result AS receipt,o.state AS action_state,o.actor_id AS action_actor
                    FROM delegation_outputs d JOIN operations o ON o.id=d.operation_id
                    JOIN creative_domain_receipts r ON r.request_id=o.request_id
                    WHERE o.request_id=? AND r.domain='agent_tools' AND r.project_id=?
                      AND o.actor_id=? AND d.project_id=? AND d.owner_id=? AND d.delegation_id=?
                      AND d.canvas_id=? AND d.conversation_id=?''',
                    (request_id, self.ctx.project_id, self.ctx.requester_user_id,
                     self.ctx.project_id, self.ctx.requester_user_id, parent_id,
                     parent_contract['canvas_id'], parent_contract['conversation_id'])).fetchone()
                if persisted is None or persisted['action_state'] != 'applied':
                    continue
                try:
                    output = json.loads(persisted['output'])
                    receipt = json.loads(persisted['receipt'])
                except (TypeError, json.JSONDecodeError):
                    continue
                origin = output.get('source_origin')
                document_ref = output.get('document_ref')
                projection = output.get('projection')
                if (receipt.get('delegation_id') != parent_id or receipt.get('output') != output
                        or output.get('operation_id') != persisted['action_operation_id']
                        or output.get('operation') != 'script.document.draft'
                        or output.get('state') != 'draft'
                        or not isinstance(origin, dict)
                        or origin.get('kind') != 'agent_script_draft'
                        or origin.get('project_id') != self.ctx.project_id
                        or origin.get('canvas_id') != parent_contract['canvas_id']
                        or origin.get('delegation_id') != parent_id
                        or origin.get('member_id') != agent.get('adoption_id')
                        or origin.get('member_revision') != agent.get('revision')
                        or not isinstance(document_ref, dict)
                        or document_ref.get('kind') != 'episode_script'
                        or not isinstance(document_ref.get('document_id'), str)
                        or not isinstance(document_ref.get('revision_id'), str)
                        or not isinstance(projection, dict)
                        or projection.get('status') != 'applied'
                        or projection.get('canvas_id') != parent_contract['canvas_id']
                        or not isinstance(projection.get('node_id'), str)
                        or not isinstance(projection.get('operation_id'), str)):
                    continue

                mapping = conn.execute('''SELECT node_id,story_fragment_id,operation_id
                    FROM agent_script_draft_projections WHERE project_id=? AND canvas_id=?
                      AND document_id=? AND revision_id=?''',
                    (self.ctx.project_id, parent_contract['canvas_id'],
                     document_ref['document_id'], document_ref['revision_id'])).fetchone()
                if (mapping is None or mapping['node_id'] != projection['node_id']
                        or mapping['operation_id'] != projection['operation_id']):
                    continue
                projected = conn.execute('''SELECT o.state,o.actor_id,p.canvas_id,p.patch,p.applied_revision
                    FROM operations o JOIN projection_outbox p ON p.operation_id=o.id
                    WHERE o.id=?''', (projection['operation_id'],)).fetchone()
                if (projected is None or projected['state'] != 'applied'
                        or projected['actor_id'] != self.ctx.requester_user_id
                        or projected['canvas_id'] != parent_contract['canvas_id']
                        or projected['applied_revision'] is None):
                    continue
                try:
                    patch = json.loads(projected['patch'])
                except (TypeError, json.JSONDecodeError):
                    continue
                operations = patch.get('operations') if isinstance(patch, dict) else None
                if (not isinstance(operations, list) or len(operations) != 1
                        or operations[0].get('op') != 'add_node'):
                    continue
                projected_node = operations[0].get('node')
                node_id = projection['node_id']
                node = current_nodes.get(node_id)
                if not isinstance(projected_node, dict) or node != projected_node:
                    continue
                data = node.get('data', {})
                binding = data.get('formalScriptRef', {})
                semantic = data.get('semanticRef', {})
                snapshot = data.get('sourceSnapshot', {})
                expected_hash = hashlib.sha256(output.get('markdown', '').encode('utf-8')).hexdigest()
                if (node.get('type') != 'textAnnotationNode'
                        or data.get('referenceOnly') is not True
                        or data.get('content') != output.get('markdown')
                        or binding.get('kind') != 'episode_script'
                        or binding.get('project_id') != self.ctx.project_id
                        or binding.get('canvas_id') != parent_contract['canvas_id']
                        or binding.get('document_id') != document_ref['document_id']
                        or binding.get('revision_id') != document_ref['revision_id']
                        or binding.get('episode_number') != document_ref.get('episode_number')
                        or semantic.get('object_id') != mapping['story_fragment_id']
                        or snapshot != {'kind': 'agent_script_draft', 'content_hash': expected_hash}):
                    continue
                evidence[node_id] = {'kind': 'agent_script_draft_projection',
                    'delegation_id': parent_id, 'run_id': run['id'], 'request_id': request_id,
                    'output_id': output['id'], 'operation_id': persisted['action_operation_id'],
                    'projection_operation_id': projection['operation_id'],
                    'projection_node_id': node_id, 'document_id': document_ref['document_id'],
                    'revision_id': document_ref['revision_id']}
        return evidence

    def _run_output_input(self, conn, ref, *, parent_id):
        require(False, '运行输出引用只能由可信运行宿主解析', 'scope_violation')

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

    def _child_operation_ceiling(self, conn, parent, parent_contract):
        """Resolve formal screenplay delegation-only grants without propagating them."""
        allowed = set(parent_contract.get('allowed_operations', []))
        if parent_contract.get('preparation_kind') == 'formal_episode_script':
            require(parent.get('parent_id') is None
                    and parent_contract.get('parent_id') is None
                    and parent_contract.get('root_delegation_id') == parent['id']
                    and parent_contract.get('allowed_operations') == ['context.read']
                    and parent_contract.get('delegable_operations') == FORMAL_SCRIPT_DELEGABLE_OPERATIONS,
                    '正式剧本根委托的固定权限合同无效', 'scope_violation')
            return allowed | set(FORMAL_SCRIPT_DELEGABLE_OPERATIONS)
        if parent.get('parent_id'):
            root = self._get(conn, parent['parent_id'])
            root_contract = root['contract']
            if root_contract.get('preparation_kind') == 'formal_episode_script':
                require(root.get('parent_id') is None
                        and root_contract.get('root_delegation_id') == root['id']
                        and root_contract.get('allowed_operations') == ['context.read']
                        and root_contract.get('delegable_operations') == FORMAL_SCRIPT_DELEGABLE_OPERATIONS,
                        '正式剧本根委托的固定权限合同无效', 'scope_violation')
                # Writer/reviewer grants are single-hop. A specialist cannot
                # delegate its role-specific operation to a grandchild.
                return allowed - set(FORMAL_SCRIPT_DELEGABLE_OPERATIONS)
        return allowed

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
                     'allowed_operations', 'acceptance'},
              {'parent_id', 'execution_authorization', 'task_brief'})
        task_brief = text(body['task_brief'], 12000) if 'task_brief' in body else None
        parent_id = text(body['parent_id'], 128) if 'parent_id' in body else None
        if self.trusted_preparation_kind == 'formal_episode_script':
            require(self.authority == 'user' and self.parent_id is None and parent_id is None,
                    '正式剧本准备身份只能用于可信根委托', 'scope_violation')
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
        if self.trusted_preparation_kind == 'formal_episode_script':
            require(allowed == ['context.read'],
                    '正式剧本主创只允许读取固定上下文，写作和审阅必须委派给专业成员',
                    'capability_denied')
        require(all(OPERATIONS[x] in agent['effective']['capabilities'] for x in allowed),
                '操作超出所选 Agent 的可信能力', 'capability_denied')
        text(body['goal'], 12000)
        acceptance = body['acceptance']
        require(isinstance(acceptance, list) and 0 < len(acceptance) <= 30, '请提供明确交付标准')
        for criterion in acceptance:
            text(criterion, 2000)
        fixed_inputs = agent_context.inputs(self.ctx, conn, body['input_refs'],
                                            username=self.username,
                                            current_canvas_id=body['canvas_id'],
                                            run_output_resolver=lambda connection, ref: self._run_output_input(
                                                connection, ref, parent_id=parent_id))
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
        parent_self_projection_evidence = {}
        if parent_id:
            parent = self._get(conn, parent_id)
            require(self._active(conn, parent), '父委托已停止后续操作', 'parent_delegation_inactive')
            original = parent['contract']
            frozen_task_brief = original.get('task_brief')
            require(task_brief == frozen_task_brief,
                    '子委托不能替换或省略冻结的原始用户设定', 'scope_violation')
            require(fence == original['session_fence'], '父委托会话已清理，不能扩大到新会话代次', 'stale_input')
            require(original['canvas_id'] == body['canvas_id'] and original['conversation_id'] == body['conversation_id'],
                    '子委托不能改变会话或写入画布', 'scope_violation')
            old_inputs = {digest(item['ref']): item for item in original['inputs']}
            script_input_evidence = {}
            for item in fixed_inputs:
                if (item['ref'].get('kind') == 'script_document'
                        and digest(item['ref']) not in old_inputs):
                    script_input_evidence[digest(item['ref'])] = self._script_document_transition_evidence(
                        conn, item, parent_id, original)
            frozen_node_ids = set(original['expanded_scope']['node_ids'])
            added_nodes = set(expanded['node_ids']) - frozen_node_ids
            if body['scope']['kind'] == 'canvas' and parent.get('parent_id') is None:
                parent_self_projection_evidence = self._own_script_projection_evidence(
                    conn, parent, original)
            script_projection_ids = {
                evidence['projection_node_id'] for evidence in script_input_evidence.values()
                if isinstance(evidence.get('projection_node_id'), str)
            }
            permitted_added_nodes = ((script_projection_ids | set(parent_self_projection_evidence))
                if body['scope']['kind'] == 'canvas' else set())
            require(added_nodes <= permitted_added_nodes
                    and set(allowed) <= self._child_operation_ceiling(conn, parent, original),
                    '子委托不能扩大范围或操作权限', 'scope_violation')
            # A canvas-scope reviewer may carry the exact projected node for a
            # fixed script_document input. Keep that node out of the dynamic
            # canvas read set; the document itself remains available only via
            # its version-pinned input snapshot.
            if added_nodes:
                read_node_ids.difference_update(added_nodes)
                expanded['node_ids'] = [identifier for identifier in expanded['node_ids']
                                        if identifier not in added_nodes]
                expanded['nodes'] = [node for node in expanded['nodes']
                                     if node['node_id'] not in added_nodes]
                expanded['membership'] = [item for item in expanded['membership']
                                          if item['node_id'] not in added_nodes]
                parent_transition_evidence.extend(deepcopy(parent_self_projection_evidence[node_id])
                    for node_id in sorted(added_nodes & set(parent_self_projection_evidence)))
            require(body['scope']['kind'] != 'canvas' or original['expanded_scope']['scope']['kind'] == 'canvas',
                    '子委托不能将局部目标扩大为整张画布准备空间', 'scope_violation')
            if body['scope']['kind'] == 'canvas' and not expanded['node_ids']:
                require(original['expanded_scope']['scope']['kind'] == 'canvas'
                        and not original['expanded_scope']['node_ids'],
                        '只有空画布父委托可以委派空画布准备工作', 'scope_violation')
                require(set(allowed) <= EMPTY_CANVAS_PREPARATION_OPERATIONS,
                        '空画布委派只允许可逆准备能力', 'capability_denied')
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
            for item in fixed_inputs:
                if item['ref'].get('kind') == 'run_output':
                    provenance = item['snapshot'].get('provenance')
                    require(isinstance(provenance, dict), '运行输出缺少冻结回执来源', 'scope_violation')
                    parent_transition_evidence.append(deepcopy(provenance))
                    continue
                frozen_input = old_inputs.get(digest(item['ref']))
                if frozen_input is None and item['ref'].get('kind') == 'script_document':
                    evidence = script_input_evidence.get(digest(item['ref']))
                    require(evidence is not None, '正式剧本来源缺少固定运行回执', 'scope_violation')
                    parent_transition_evidence.append(evidence)
                    continue
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
        if self.trusted_preparation_kind == 'formal_episode_script':
            contract['preparation_kind'] = self.trusted_preparation_kind
            contract['delegable_operations'] = list(FORMAL_SCRIPT_DELEGABLE_OPERATIONS)
        if task_brief is not None:
            contract['task_brief'] = task_brief
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
