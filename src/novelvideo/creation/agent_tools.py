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
from .asset_generation import AssetDesignPrepare, AssetGeneration, AssetDesignProposal, AssetDesignProposalRef
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
                 node_execution_factory=None, run_output_resolver=None):
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
        self.run_output_resolver = run_output_resolver
        self._pending_node_transition = None
        self._pending_node_result = None
        self._pending_asset_design = None
        self._pending_script_document = None
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
        if body['operation'] == 'asset.design.create':
            try:
                return await self._execute_asset_design(request)
            except OrchestrationError as exc:
                raise OperationError(exc.code, str(exc), **exc.details) from exc
        if body['operation'] in {'script.document.draft', 'script.document.review'}:
            try:
                return await self._execute_script_document(request)
            except OrchestrationError as exc:
                raise OperationError(exc.code, str(exc), **exc.details) from exc
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

    def _script_document_plan(self, payload, contract):
        from .canvas_workspaces import list_canvases

        selection = self._selection(payload, contract)
        workspaces = [item for item in list_canvases(self.ctx.state_dir)
            if item['canvas_id'] == self.canvas_id and item['status'] == 'applied']
        require(len(workspaces) == 1 and workspaces[0]['kind'] == 'concept'
                and workspaces[0]['episode_id'] is None,
                '正式剧本草稿和独立审阅只能从已登记的构思画布发起', 'scope_violation')
        if payload.get('source_input_index') is not None:
            index = payload['source_input_index']
            require(index in selection['input_indices'], '剧本来源必须是本动作明确选择的固定输入', 'scope_violation')
            source = contract['inputs'][index]
            snapshot = source.get('snapshot', {})
            provenance = snapshot.get('provenance')
            ref = source.get('ref', {})
            require(ref.get('kind') == 'run_output'
                    and snapshot.get('kind') == 'story_fragment'
                    and isinstance(snapshot.get('payload'), dict)
                    and snapshot['payload'].get('canvas_id') == self.canvas_id
                    and isinstance(provenance, dict)
                    and isinstance(provenance.get('source_run_id'), str)
                    and isinstance(provenance.get('source_delegation_id'), str)
                    and isinstance(provenance.get('source_output_id'), str)
                    and isinstance(provenance.get('source_action_request_id'), str)
                    and isinstance(provenance.get('source_operation_id'), str),
                    '既有剧本来源必须是当前画布上经过验证的主创任务产物', 'scope_violation')
            source_ref = {'kind': 'run_output', 'output_id': provenance['source_output_id'],
                          'source_run_id': provenance['source_run_id'],
                          'source_task_key': ref.get('source_task_key'),
                          'source_delegation_id': provenance['source_delegation_id'],
                          'source_action_request_id': provenance['source_action_request_id'],
                          'source_operation_id': provenance['source_operation_id']}
        else:
            source_ref = None
        if 'document_input_index' in payload:
            index = payload['document_input_index']
            require(index in selection['input_indices'], '审阅剧本必须是本动作明确选择的固定输入', 'scope_violation')
            document_input = contract['inputs'][index]
            ref = document_input.get('ref', {})
            snapshot = document_input.get('snapshot', {})
            require(ref.get('kind') == 'script_document'
                    and ref.get('canvas_id') == self.canvas_id
                    and snapshot.get('kind') == 'episode_script'
                    and snapshot.get('current_revision_id') == ref.get('revision_id'),
                    '审阅目标不是当前画布中已固定的正式剧本草稿', 'scope_violation')
            document_ref = {'document_id': ref['document_id'], 'revision_id': ref['revision_id'],
                            'kind': 'episode_script', 'episode_number': snapshot['episode_number']}
        else:
            document_ref = None
        return {'selection': selection, 'source_ref': source_ref, 'document_ref': document_ref}

    async def _execute_script_document(self, request):
        from pathlib import Path

        from novelvideo.script_creation.store import DocumentStore
        from .agent_script_documents import AgentScriptDocuments

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
        self._pending_script_document = None
        try:
            await self._fresh('editor')
            async with asset_project_lock(self.ctx.state_dir):
                await self._fresh('editor')

                def preflight():
                    with self.read() as conn:
                        contract = self._current(conn, operation)
                        return contract, self._script_document_plan(payload, contract)

                contract, plan = await registration_io(preflight)
                documents = AgentScriptDocuments(DocumentStore(Path(self.ctx.state_dir) / 'data.db'))
                member_id = contract['agent'].get('adoption_id')
                member = {'adoption_id': member_id, 'revision': contract['agent'].get('revision'),
                          'name': contract['agent']['effective']['name']}
                if operation == 'script.document.draft':
                    result = await documents.create_draft(
                        request_id=request.request_id, project_id=self.ctx.project_id,
                        canvas_id=self.canvas_id, delegation_id=self.delegation_id,
                        writer=member, episode_number=payload['episode_number'],
                        title=payload['title'], markdown=payload['markdown'],
                        source_ref=plan['source_ref'],
                    )
                    projection = await registration_io(documents.project_draft,
                        state_dir=self.ctx.state_dir, project_id=self.ctx.project_id,
                        canvas_id=self.canvas_id, actor=self.ctx.requester_user_id,
                        document_ref=result['document_ref'], title=result['title'],
                        markdown=result['markdown'], request_id=request.request_id)
                    result = {**result, 'projection': projection}
                else:
                    document_ref = plan['document_ref']
                    result = await documents.record_review(
                        request_id=request.request_id, project_id=self.ctx.project_id,
                        canvas_id=self.canvas_id, delegation_id=self.delegation_id,
                        reviewer=member, document_id=document_ref['document_id'],
                        revision_id=document_ref['revision_id'], report=payload['report'],
                    )
                self._pending_script_document = {'contract': contract, 'result': result,
                                                 'plan': plan}
                return await registration_io(self._apply, request)
        except CanvasLockBusy as exc:
            raise OperationError('canvas_busy', '画布正在保存，请使用原请求重试') from exc
        finally:
            self._pending_script_document = None

    def _asset_design_plan(self, payload, contract):
        selection = self._selection(payload, contract)
        scope = contract['expanded_scope']['scope']
        scope_kind = scope['kind']
        targets = selection['target_node_ids']
        expanded_scope = contract['expanded_scope']
        nodes = {node['node_id']: node for node in expanded_scope['nodes']}
        input_indices = set(selection['input_indices'])
        reference_indices = self._indices(payload['reference_indices'], len(contract['inputs']))
        require(set(reference_indices) <= input_indices,
                '资产参考必须来自本动作明确选择的冻结输入', 'scope_violation')
        if scope_kind == 'canvas':
            require(not targets, '画布级委托的资产设计不能伪造局部目标', 'scope_violation')
            selected_group_id = None
        elif scope_kind == 'group':
            selected_group_id = scope['group_id']
            require(targets == [selected_group_id], '分组委托只能在冻结的目标内容组内创建设计', 'scope_violation')
        else:
            require(len(targets) == 1 and targets[0] in nodes,
                    '节点委托只能在单一冻结目标内创建设计', 'scope_violation')
            target_id = targets[0]
            target_node = nodes[target_id]
            if target_node['node_type'] == 'groupNode':
                selected_group_id = target_id
            else:
                require(target_node['node_type'] in {'imageGenNode', 'imageNode', 'uploadNode'},
                        '节点委托只允许内容组或单一图像节点作为资产设计范围', 'scope_violation')
                media_fields = set(target_node.get('media_evidence', {}).get('fields', ()))
                require(bool(media_fields & {'imageUrl', 'previewImageUrl'}),
                        '选中的图像节点尚无固定图像结果', 'scope_violation')
                if target_node['node_type'] == 'uploadNode':
                    require(not media_fields & {'videoUrl', 'audioUrl', 'panoUrl', 'plyUrl'},
                            '上传节点必须是单一图像素材', 'scope_violation')

                membership = [item for item in expanded_scope.get('membership', [])
                              if item.get('node_id') == target_id]
                require(len(membership) == 1 and isinstance(membership[0].get('members'), list),
                        '选中图像缺少冻结的分组成员关系', 'scope_violation')
                chain = membership[0]['members']
                chain_ids = [item.get('id') for item in chain if isinstance(item, dict)]
                require(len(chain) == len(chain_ids) and chain_ids
                        and chain_ids[0] == target_id and len(chain_ids) == len(set(chain_ids)),
                        '选中图像的冻结分组成员关系无效', 'scope_violation')
                require(all(item.get('parentId') == (chain_ids[index + 1]
                                if index + 1 < len(chain_ids) else None)
                            for index, item in enumerate(chain)),
                        '选中图像的冻结分组链不连续', 'scope_violation')
                require(chain[0].get('type') == target_node['node_type']
                        and chain[0].get('parentId') == target_node.get('parent_id'),
                        '选中图像的冻结父分组与成员关系不一致', 'scope_violation')
                parent_id = target_node.get('parent_id')
                if parent_id is None:
                    require(len(chain) == 1,
                            '顶层图像不能带有未声明的冻结父分组', 'scope_violation')
                    selected_group_id = None
                else:
                    require(len(chain) > 1 and chain[1].get('id') == parent_id
                            and chain[1].get('type') == 'groupNode',
                            '输出容器必须是选中图像的冻结直接父分组', 'scope_violation')
                    require(all(item.get('type') == 'groupNode' for item in chain[1:]),
                            '选中图像的冻结祖先必须全部是内容组', 'scope_violation')
                    selected_group_id = parent_id

                # A selected image is an authorized write target only for
                # add-only design creation. Its bytes are referenced through
                # the fixed input snapshot; the output container is derived
                # from frozen ancestry and never accepted from model payload.
                frozen_input_indices = [index for index in input_indices
                    if contract['inputs'][index].get('ref') == {
                        'kind': 'canvas_node', 'canvas_id': self.canvas_id, 'node_id': target_id}
                    and contract['inputs'][index].get('snapshot') == target_node
                    and contract['inputs'][index].get('membership') == membership]
                require(any(index in reference_indices for index in frozen_input_indices),
                        '选中图像必须作为本次资产设计的固定图像参考', 'scope_violation')

        references = []
        for index in reference_indices:
            item = contract['inputs'][index]
            ref = item['ref']
            if ref.get('kind') == 'canvas_node':
                require(ref.get('canvas_id') == self.canvas_id,
                        '资产参考节点必须来自当前画布', 'scope_violation')
                references.append({'kind': 'canvas_node', 'canvas_id': self.canvas_id,
                                   'node_id': ref['node_id']})
            elif ref.get('kind') == 'asset':
                references.append({'kind': 'asset', 'asset_ref': deepcopy(ref['asset_ref']),
                                   'slot_id': ref['slot_id'], 'role': ref['role']})
            else:
                require(False, '资产参考只支持当前画布节点或固定资产输入', 'scope_violation')

        base_ref = None
        base_index = payload.get('base_asset_input_index')
        if base_index is not None:
            require(base_index in input_indices, '基础资产必须来自本动作明确选择的输入', 'scope_violation')
            base_input = contract['inputs'][base_index]
            require(base_input['ref'].get('kind') == 'asset',
                    '基础版本必须引用固定资产输入', 'scope_violation')
            base_ref = deepcopy(base_input['ref']['asset_ref'])

        source_ref = None
        source_provenance = None
        source_index = payload.get('source_proposal_input_index')
        if source_index is not None:
            require(source_index in input_indices, '既有提案必须来自本动作明确选择的输入', 'scope_violation')
            source = contract['inputs'][source_index]
            snapshot = source.get('snapshot', {})
            require(source['ref'].get('kind') == 'run_output'
                    and snapshot.get('kind') == 'story_fragment'
                    and isinstance(snapshot.get('payload'), dict)
                    and isinstance(snapshot.get('provenance'), dict)
                    and snapshot.get('payload', {}).get('canvas_id') == self.canvas_id,
                    '既有提案必须是受信的、属于当前画布的已验证故事草案输出', 'scope_violation')
            source_ref = AssetDesignProposalRef(id=snapshot['id'], revision=snapshot['revision'],
                                                kind='story_fragment')
            source_provenance = deepcopy(snapshot['provenance'])
        else:
            require('draft_title' in payload and 'draft_text' in payload,
                    '请提供新提案草稿正文，或引用已验证的提案输入')

        return {'selection': selection, 'selected_group_id': selected_group_id,
                'references': references, 'base_asset_ref': base_ref,
                'source_proposal_ref': source_ref,
                'source_proposal_provenance': source_provenance}

    async def _execute_asset_design(self, request):
        request_hash = digest([self.domain, self.ctx.project_id, self.ctx.requester_user_id,
                               self.authority, request.model_dump(mode='json')])

        def replay():
            with self.read() as conn:
                return self._replay(conn, request, request_hash)

        previous = await registration_io(replay)
        if previous is not None:
            return previous
        self._pending_asset_design = None
        try:
            await self._fresh('editor')
            async with asset_project_lock(self.ctx.state_dir):
                await self._fresh('editor')

                def preflight():
                    with self.read() as conn:
                        contract = self._current(conn, 'asset.design.create')
                        plan = self._asset_design_plan(request.payload['payload'], contract)
                        return contract, plan

                contract, plan = await registration_io(preflight)
                canvas = await registration_io(read_canvas, self.ctx.state_dir, self.canvas_id)
                require(canvas is not None and canvas.get('project_id') == self.ctx.project_id,
                        '当前概念画布不可用', 'scope_violation')
                payload = request.payload['payload']
                design_request_id = 'asset-design:' + digest(
                    [self.ctx.project_id, self.delegation_id, request.request_id])[:48]
                body = AssetDesignPrepare(
                    request_id=design_request_id, canvas_id=self.canvas_id,
                    expected_canvas_revision=canvas['revision'], kind=payload['kind'],
                    purpose=(payload['purpose'] if payload.get('purpose') is not None else
                             'character_sheet' if payload['kind'] == 'character' else 'scene'),
                    aspect_ratio=payload.get('aspect_ratio'),
                    camera_selection=payload.get('camera_selection'),
                    name=payload['name'], description=payload['description'], prompt=payload['prompt'],
                    source_proposal=(AssetDesignProposal(title=payload['draft_title'], text=payload['draft_text'])
                                     if plan['source_proposal_ref'] is None else None),
                    source_proposal_ref=plan['source_proposal_ref'],
                    style_template_id=payload.get('style_template_id'),
                    base_asset_ref=plan['base_asset_ref'], references=plan['references'],
                    group_id=plan['selected_group_id'])
                service = AssetGeneration(self.ctx)
                result = await registration_io(service.prepare, self.ctx.requester_user_id, body,
                    selected_group_id=plan['selected_group_id'],
                    source_proposal_provenance=plan['source_proposal_provenance'])
                self._pending_asset_design = {'contract': contract, 'plan': plan, 'body': body,
                                              'result': result}
                return await registration_io(self._apply, request)
        except CanvasLockBusy as exc:
            raise OperationError('canvas_busy', '画布正在保存，请使用原请求重试') from exc
        finally:
            self._pending_asset_design = None

    def _node_service(self, contract):
        if self.node_execution_factory is not None:
            return self.node_execution_factory(contract)
        from pathlib import Path
        from .node_execution import NodeExecutionService
        return NodeExecutionService(Path(self.ctx.state_dir), project_id=self.ctx.project_id,
            canvas_id=self.canvas_id, actor={'id': self.ctx.requester_user_id, 'username': self.username},
            authorize_submission=self.authorize_submission,
            authorize_projection=self.authorize_projection,
            require_workspace_scope=True)

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

    def _verified_asset_designs(self, conn, contract):
        """Read only applied asset-design receipts under this authorization root."""
        root_id = contract.get('root_delegation_id') or self.delegation_id
        if not table(conn, 'agent_delegations') or not table(conn, 'delegation_outputs'):
            return []
        delegation_ids = []
        for row in conn.execute('''SELECT id,contract FROM agent_delegations
                WHERE project_id=? AND owner_id=? AND owner_username=? AND canvas_id=? AND conversation_id=?''',
                (self.ctx.project_id, self.ctx.requester_user_id, self.username,
                 self.canvas_id, self.conversation_id)):
            value = json.loads(row['contract'])
            if (value.get('root_delegation_id') == root_id
                    and value.get('authorization_id') == contract.get('authorization_id')):
                delegation_ids.append(row['id'])
        if not delegation_ids or not table(conn, 'creative_domain_receipts'):
            return []
        placeholders = ','.join('?' for _ in delegation_ids)
        rows = conn.execute(f'''SELECT d.rowid AS sequence,d.delegation_id,d.content,o.request_id,o.actor_id,
                r.result FROM delegation_outputs d JOIN operations o ON o.id=d.operation_id
            JOIN creative_domain_receipts r ON r.request_id=o.request_id AND r.project_id=d.project_id
                AND r.domain='agent_tools'
            WHERE d.project_id=? AND d.owner_id=? AND d.canvas_id=? AND d.conversation_id=?
              AND d.delegation_id IN ({placeholders}) AND o.actor_id=? ORDER BY d.rowid''',
            (self.ctx.project_id, self.ctx.requester_user_id, self.canvas_id, self.conversation_id,
             *delegation_ids, self.ctx.requester_user_id))
        from .asset_generation import AssetGeneration
        designs = []
        for row in rows:
            output = json.loads(row['content'])
            if (output.get('operation') != 'asset.design.create'
                    or output.get('delegation_id') != row['delegation_id']
                    or output.get('operation_id') is None):
                continue
            receipt = json.loads(row['result'])
            candidate = output.get('prepared_execution')
            if (receipt.get('delegation_id') != row['delegation_id']
                    or receipt.get('output') != output
                    or not isinstance(candidate, dict)
                    or output.get('asset_design', {}).get('prepared_execution') != candidate):
                continue
            verified = AssetGeneration.verified_design_nodes(self.ctx, candidate)
            if verified is None:
                continue
            if (output.get('group_id') != verified['group_id']
                    or output.get('text_node_id') != verified['text_node_id']
                    or output.get('image_node_id') != verified['image_node_id']
                    or candidate.get('node_id') != verified['image_node_id']):
                continue
            designs.append({**verified, 'sequence': row['sequence'],
                            'delegation_id': row['delegation_id']})
        return designs

    def _verified_own_script_projections(self, conn, contract):
        """Return only applied screenplay refs recorded by this delegation's run."""
        if 'script.document.draft' not in contract.get('allowed_operations', []):
            return set()
        required_tables = {'agent_runs', 'creative_domain_receipts', 'operations', 'delegation_outputs'}
        if not conn or not all(table(conn, name) for name in required_tables):
            return set()
        run_row = conn.execute('''SELECT content FROM agent_runs
            WHERE project_id=? AND owner_id=? AND username=? AND canvas_id=?
              AND conversation_id=? AND delegation_id=?''',
            (self.ctx.project_id, self.ctx.requester_user_id, self.username,
             self.canvas_id, self.conversation_id, self.delegation_id)).fetchone()
        if run_row is None:
            return set()
        run = json.loads(run_row['content'])
        root_id = contract.get('root_delegation_id') or self.delegation_id
        if (run.get('delegation_id') != self.delegation_id
                or run.get('root_delegation_id', self.delegation_id) != root_id
                or run.get('state') not in {
                    'queued', 'applying', 'model_inflight', 'waiting_children', 'stopped', 'blocked'}):
            return set()

        agent = contract.get('agent', {})
        projections = set()
        for round_index, step in enumerate(run.get('rounds', [])):
            if step.get('delegation_id') != self.delegation_id:
                continue
            actions = step.get('decision', {}).get('actions', [])
            completed = set(step.get('actions_done', []))
            for action_index, action in enumerate(actions):
                if (action_index not in completed
                        or action.get('operation') != 'script.document.draft'):
                    continue
                request_id = f"run:{run['id']}:{round_index}:action:{action_index}"
                persisted = conn.execute('''SELECT d.content,r.result,o.id AS operation_id
                    FROM creative_domain_receipts r
                    JOIN operations o ON o.request_id=r.request_id
                    JOIN delegation_outputs d ON d.operation_id=o.id
                    WHERE r.request_id=? AND r.domain='agent_tools' AND r.project_id=?
                      AND o.actor_id=? AND d.project_id=? AND d.owner_id=?
                      AND d.delegation_id=? AND d.canvas_id=? AND d.conversation_id=?''',
                    (request_id, self.ctx.project_id, self.ctx.requester_user_id,
                     self.ctx.project_id, self.ctx.requester_user_id, self.delegation_id,
                     self.canvas_id, self.conversation_id)).fetchone()
                if persisted is None:
                    continue
                output = json.loads(persisted['content'])
                receipt = json.loads(persisted['result'])
                origin = output.get('source_origin')
                document_ref = output.get('document_ref')
                projection = output.get('projection')
                if (receipt.get('delegation_id') != self.delegation_id
                        or receipt.get('output') != output
                        or output.get('id') != receipt.get('output', {}).get('id')
                        or output.get('operation_id') != persisted['operation_id']
                        or output.get('operation') != 'script.document.draft'
                        or output.get('state') != 'draft'
                        or not isinstance(origin, dict)
                        or origin.get('kind') != 'agent_script_draft'
                        or origin.get('project_id') != self.ctx.project_id
                        or origin.get('canvas_id') != self.canvas_id
                        or origin.get('delegation_id') != self.delegation_id
                        or origin.get('member_id') != agent.get('adoption_id')
                        or origin.get('member_revision') != agent.get('revision')
                        or not isinstance(document_ref, dict)
                        or document_ref.get('kind') != 'episode_script'
                        or not isinstance(document_ref.get('document_id'), str)
                        or not isinstance(document_ref.get('revision_id'), str)
                        or not isinstance(projection, dict)
                        or projection.get('status') != 'applied'
                        or projection.get('canvas_id') != self.canvas_id
                        or not isinstance(projection.get('node_id'), str)):
                    continue

                from novelvideo.freezone.canvas_store import read_canvas
                canvas = read_canvas(self.ctx.state_dir, self.canvas_id)
                node = next((item for item in (canvas or {}).get('nodes', [])
                             if item.get('id') == projection['node_id']), None)
                binding = (node or {}).get('data', {}).get('formalScriptRef', {})
                if (node is not None and node.get('type') == 'textAnnotationNode'
                        and binding.get('kind') == 'episode_script'
                        and binding.get('project_id') == self.ctx.project_id
                        and binding.get('canvas_id') == self.canvas_id
                        and binding.get('document_id') == document_ref['document_id']
                        and binding.get('revision_id') == document_ref['revision_id']
                        and binding.get('episode_number') == document_ref.get('episode_number')):
                    projections.add(projection['node_id'])
        return projections

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
        require(current['scope'] == original['scope'], '目标范围已变化', 'stale_input')
        designs = self._verified_asset_designs(conn, contract)
        expected_added = set()
        visible_designs = []
        if scope['kind'] == 'canvas':
            visible_designs = designs
        elif scope['kind'] == 'group':
            visible_designs = [item for item in designs if item['parent_group_id'] == scope['group_id']]
        expected_added = {identifier for item in visible_designs for identifier in
                          (item['group_id'], item['text_node_id'], item['image_node_id'])}
        script_projection_ids = set()
        parent_id = contract.get('parent_id')
        if scope['kind'] == 'canvas' and parent_id:
            parent_contract = self.delegations._get(conn, parent_id)['contract']
            inherited_inputs = {digest(item['ref']) for item in parent_contract.get('inputs', [])}
            for item in contract.get('inputs', []):
                ref = item.get('ref', {})
                if ref.get('kind') != 'script_document' or digest(ref) in inherited_inputs:
                    continue
                evidence = self.delegations._script_document_transition_evidence(
                    conn, item, parent_id, parent_contract)
                projection_node_id = evidence.get('projection_node_id')
                require(isinstance(projection_node_id, str) and projection_node_id,
                        '正式剧本来源缺少固定投影节点', 'scope_violation')
                script_projection_ids.add(projection_node_id)
        if scope['kind'] == 'canvas':
            script_projection_ids.update(self._verified_own_script_projections(conn, contract))
        original_ids = set(original['node_ids'])
        # Existing frozen nodes remain part of the original scope. Only newly
        # projected, receipt-verified references can be removed from its delta.
        script_projection_ids.difference_update(original_ids)
        expected_added.update(script_projection_ids)
        current_ids = set(current['node_ids'])
        require(current_ids - original_ids == expected_added
                and original_ids <= current_ids,
                '目标范围只能增加已验证的资产设计或固定剧本引用节点', 'stale_input')
        if script_projection_ids:
            # The exact script version is already present as a fixed input.
            # Keep its canvas projection out of the live graph read set; this
            # bridge tolerates the node's existence without exposing its
            # content or granting access to any other added node.
            current['node_ids'] = [node_id for node_id in current['node_ids']
                                   if node_id not in script_projection_ids]
            current['nodes'] = [node for node in current['nodes']
                                if node['node_id'] not in script_projection_ids]
            current['membership'] = [item for item in current['membership']
                                     if item['node_id'] not in script_projection_ids]
        original_membership = {item['node_id']: item for item in original['membership']}
        current_membership = {item['node_id']: item for item in current['membership']}
        require(all(current_membership.get(node_id) == frozen
                    for node_id, frozen in original_membership.items()),
                '目标范围原成员归属已变化', 'stale_input')
        # A nodes-scope group intentionally freezes only the selected parent
        # node. Keep its child range add-only by comparing the last trusted
        # design's pre-add membership to the live group descendants.
        if scope['kind'] == 'nodes':
            current_canvas = read_canvas(self.ctx.state_dir, self.canvas_id)
            canvas_nodes = {node['id']: node for node in current_canvas['nodes']}
            original_nodes = {node['node_id']: node for node in original['nodes']}
            selected_groups = {node_id for node_id, node in original_nodes.items()
                               if node['node_type'] == 'groupNode'}
            for group_id in selected_groups:
                group_designs = [item for item in designs if item['parent_group_id'] == group_id]
                if not group_designs:
                    continue
                latest = max(group_designs, key=lambda item: item['sequence'])
                expected_descendants = set(latest['prior_descendant_ids']) | {
                    latest['group_id'], latest['text_node_id'], latest['image_node_id']}
                descendants = {group_id}
                changed = True
                while changed:
                    changed = False
                    for node in current_canvas['nodes']:
                        if node.get('parentId') in descendants and node['id'] not in descendants:
                            descendants.add(node['id'])
                            changed = True
                require(descendants == expected_descendants,
                        '局部内容组只能包含原成员与回执中的资产设计节点', 'stale_input',
                        group_id=group_id)
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
        inputs = agent_context.inputs(self.ctx, conn, [item['ref'] for item in contract['inputs']],
                                      username=self.username,
                                      current_canvas_id=self.canvas_id,
                                      run_output_resolver=self.run_output_resolver)
        for current_input, frozen_input in zip(inputs, contract['inputs']):
            ref = current_input['ref']
            input_node_id = ref.get('node_id') if ref.get('kind') == 'canvas_node' and ref.get('canvas_id') == self.canvas_id else None
            state = accepted.get(input_node_id) if input_node_id else None
            if state:
                snapshot = current_input['snapshot']
                require(current_input['ref'] == frozen_input['ref']
                        and current_input.get('membership') == frozen_input.get('membership')
                        and snapshot['input_fingerprint'] == state['input_fingerprint']
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
        elif operation == 'asset.design.create' and self._pending_asset_design is not None:
            contract = self._pending_asset_design['contract']
        elif operation in {'script.document.draft', 'script.document.review'} and self._pending_script_document is not None:
            contract = self._pending_script_document['contract']
        else:
            contract = self._current(conn, operation,
                allow_inactive=bool(self._direct_node_acceptance and operation == 'node.patch'))
        member_id = contract['agent'].get('adoption_id')
        member = {'adoption_id': member_id, 'revision': contract['agent'].get('revision'),
                  'origin': 'main' if contract.get('main_binding') else 'adoption'}
        common = {'target_node_ids', 'input_indices', 'acceptance_indices'}
        fields = {'context.read': set(), 'proposal.create': {'title', 'text'},
                  'ontology.definition.draft': {'definition'}, 'media.propose': {'title', 'intent', 'modality'},
                  'asset.design.create': {'kind', 'name', 'description', 'prompt', 'reference_indices',
                      'style_template_id', 'base_asset_input_index', 'draft_title', 'draft_text',
                      'source_proposal_input_index', 'purpose', 'aspect_ratio', 'camera_selection'},
                  'script.document.draft': {'episode_number', 'title', 'markdown', 'source_input_index'},
                  'script.document.review': {'document_input_index', 'report'},
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

        if operation == 'asset.design.create':
            exact(payload, common | {'kind', 'name', 'description', 'prompt', 'reference_indices'},
                  {'style_template_id', 'base_asset_input_index', 'draft_title', 'draft_text',
                   'source_proposal_input_index', 'purpose', 'aspect_ratio', 'camera_selection'})
        elif operation == 'script.document.draft':
            exact(payload, common | {'episode_number', 'title', 'markdown'}, {'source_input_index'})
        else:
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
        elif operation == 'asset.design.create':
            pending = self._pending_asset_design
            require(isinstance(pending, dict) and pending.get('result'),
                    '资产设计服务尚未返回可记录的持久回执', 'asset_design_result_missing')
            result = deepcopy(pending['result'])
            candidate = result.get('prepared_execution')
            if candidate is not None:
                candidate = AssetGeneration.verified_prepared_execution(self.ctx, candidate)
                require(candidate is not None,
                        '资产设计节点未能通过持久回执验证', 'asset_design_projection_unverified')
            result['prepared_execution'] = candidate
            output.update(state=result['projection_status'], asset_design=result,
                request_id=result['request_id'], design_ref=result['design_ref'],
                source_proposal_ref=result['source_proposal_ref'], group_id=result['group_id'],
                text_node_id=result['text_node_id'], image_node_id=result['image_node_id'],
                canvas_revision=result['canvas_revision'],
                graph_operation_id=result['operation_id'], projection_status=result['projection_status'],
                prepared_execution=candidate)
        elif operation in {'script.document.draft', 'script.document.review'}:
            pending = self._pending_script_document
            require(isinstance(pending, dict) and isinstance(pending.get('result'), dict),
                    '正式剧本服务尚未返回可记录的持久回执', 'script_document_result_missing')
            result = deepcopy(pending['result'])
            if operation == 'script.document.draft':
                output.update(state='draft', document_ref=result['document_ref'],
                    title=result['title'], markdown=result['markdown'],
                    source_origin=result['source_origin'], adopted=result['adopted'],
                    projection=result.get('projection'))
            else:
                output.update(state='reviewed', script_review=result,
                    document_ref=result['document_ref'], candidate_hash=result['candidate_hash'],
                    conclusion=result['conclusion'], summary=result['summary'], issues=result['issues'],
                    reviewer=result['reviewer'])
        else:
            text(payload['title'], 200); text(payload['intent'], 12000)
            require(isinstance(payload['modality'], str) and payload['modality'] in {'image', 'video', 'audio', '3d'},
                    '未知媒体方案类型')
            output.update(state='proposed', plan={k: payload[k] for k in ('title', 'intent', 'modality')}, execution_enabled=False)
        conn.execute('INSERT INTO delegation_outputs VALUES (?,?,?,?,?,?,?,?)',
            (output['id'], self.ctx.project_id, self.delegation_id, self.ctx.requester_user_id,
             self.canvas_id, self.conversation_id, operation_id, packed(output)))
        return {'delegation_id': self.delegation_id, 'output': output}
