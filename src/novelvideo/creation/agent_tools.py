"""Host-invoked, scoped preparation tools. No provider, filesystem or HTTP tool."""
from copy import deepcopy
import json
from uuid import uuid4

from novelvideo.freezone.canvas_lock import CanvasLockBusy
from novelvideo.production_workflow.coordination import asset_project_lock
from . import agent_context
from .asset_adapter import registration_io
from .asset_reference import table
from .delegations import Delegations, OPERATIONS
from .library_references import packed
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

    def __init__(self, ctx, *, username, canvas_id, conversation_id, delegation_id, resolve_context):
        super().__init__(ctx, authority='agent')
        self.username = text(username, 200)
        self.canvas_id = text(canvas_id, 64)
        self.conversation_id = text(conversation_id, 64)
        self.delegation_id = text(delegation_id, 128)
        require(callable(resolve_context), '缺少可信权限解析器', 'scope_violation')
        self.resolve_context = resolve_context
        self.delegations = Delegations(ctx, username=username)

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

    async def execute(self, body):
        exact(body, {'request_id', 'operation', 'payload'})
        text(body['operation'], 80)
        request = DomainCommand(request_id=body['request_id'], action='execute', payload={
            **deepcopy(body), 'delegation_id': self.delegation_id,
            'canvas_id': self.canvas_id, 'conversation_id': self.conversation_id})
        await self._fresh('editor')
        async with asset_project_lock(self.ctx.state_dir):
            await self._fresh('editor')
            try:
                return await registration_io(self._apply, request)
            except CanvasLockBusy as exc:
                raise OperationError('canvas_busy', '画布正在保存，请使用原请求重试') from exc

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

    def _current(self, conn, operation):
        row = self._owned(conn)
        contract = row['contract']
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
        require({k: v for k, v in current.items() if k != 'observed_canvas_revision'}
                == {k: v for k, v in original.items() if k != 'observed_canvas_revision'},
                '目标内容、结果或成员归属已变化', 'stale_input')
        inputs = agent_context.inputs(self.ctx, conn, [item['ref'] for item in contract['inputs']])
        require(inputs == contract['inputs'], '委托引用输入已变化', 'stale_input')
        now = agent_context.context(self.ctx, conn, self.canvas_id, current, inputs)
        before, after = self._hard(contract['context']), self._hard(now)
        require(before == after, '当前硬约束已变化，请重新比较委托', 'hard_constraints_changed',
                before=before, after=after, evaluation='not_evaluated')
        require(not any(entry['conflicts'] for entry in after), '当前硬约束存在冲突',
                'hard_constraints_conflict', conflicts=after, evaluation='not_evaluated')
        return contract

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
        operation, payload = request.payload['operation'], request.payload['payload']
        contract = self._current(conn, operation)
        common = {'target_node_ids', 'input_indices', 'acceptance_indices'}
        fields = {'context.read': set(), 'proposal.create': {'title', 'text'},
                  'ontology.definition.draft': {'definition'}, 'media.propose': {'title', 'intent', 'modality'}}
        exact(payload, common | fields[operation])
        selection = self._selection(payload, contract)
        output = {'id': uuid4().hex, 'delegation_id': self.delegation_id, 'operation_id': operation_id,
                  'operation': operation, 'source': {'kind': 'agent', 'owner_id': self.ctx.requester_user_id,
                                                    'delegation_id': self.delegation_id}, **selection,
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
