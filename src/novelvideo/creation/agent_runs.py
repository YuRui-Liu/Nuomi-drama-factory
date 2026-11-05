"""Durable bounded Decision runs. Unknown provider submissions are never resent."""
import asyncio
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4

from novelvideo.production_workflow.coordination import asset_project_lock
from . import agent_context, agent_model
from .agent_skill_context import task_skill_context
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
FORMAL_SCRIPT_DELEGABLE_OPERATIONS = ['script.document.draft', 'script.document.review']
MAX_PROMPT_SKILL_CONTEXT_BYTES = 120_000
_SCRIPT_WRITER_SECTIONS = {
    'skills/script-master/references/writing.md': (
        '核心原则速记（开头冗余 1/3）',
        '第三部分 写作模式 + 分集开关',
        '第四部分 B 编剧内核六维',
        '第五部分 模式 × 节奏 × 分集开关 推荐组合矩阵',
        '第七部分 输出格式与创作笔记',
        '附录 C：中文短剧拍摄稿格式（推荐给短剧 / 短视频 / 微电影）',
        '附录 D：AI 视频配音友好的标点与发声设计',
        '附录 E：剧本转视频提示词的前置接口规范',
    ),
}
_SCRIPT_REVIEWER_SECTIONS = {
    'skills/script-master/references/doctor.md': (
        '核心原则速记（开头冗余 1/3）',
        '1.3i AI味识别与消除会诊（writing-craft-library 区12）',
        '2.1 工作流总览',
        '2.4 主治医生归纳（步骤 5 详解）',
        '2.5 编剧内核诊断',
        '第三部分 防"和稀泥"硬规则',
        '第七部分 输出格式与视觉规范',
    ),
}
_SCRIPT_COORDINATOR_SECTIONS = {
    'skills/script-master/SKILL.md': (
        '第一部分 总体原则', 'S4 剧本初稿', 'S5 圆桌会诊',
        '第四部分 专家调用协议', '第五部分 统一交接摘要',
    ),
}
_SCRIPT_ROOT_SECTIONS = {
    'skills/script-master/references/framework.md': (
        '第一部分 总体原则', 'S0 灵感输入', 'S1 核心信息收集',
        'S2 项目档案生成', 'S3 大纲锁定',
    ),
    'skills/script-master/references/writing.md': (
        '第一部分 总体原则', 'S4 剧本初稿',
    ),
    'skills/script-master/references/doctor.md': (
        '第一部分 总体原则', 'S5 圆桌会诊',
    ),
    'skills/script-master/references/dialogue.md': (
        '第一部分 总体原则', 'S7 台词诊断',
    ),
    'skills/script-master/references/visual-director.md': (
        '第一部分 总体原则', 'S4 剧本初稿', '第十八部分：导演画面版输出模式',
    ),
}


def _normalized_markdown_heading(value):
    return ' '.join(value.split())


def _markdown_section_blocks(content, section_ids):
    """Return exact source slices for a fixed allowlist of Markdown headings."""
    headings = []
    offset = 0
    in_fence = False
    for line in content.splitlines(keepends=True):
        if line.lstrip().startswith(('```', '~~~')):
            in_fence = not in_fence
        if not in_fence:
            match = re.match(r'^(#{1,6})[ \t]+(.+?)[ \t]*(?:\r?\n)?$', line)
            if match:
                headings.append((offset, len(match.group(1)),
                                 _normalized_markdown_heading(match.group(2))))
        offset += len(line)

    blocks = []
    for section_id in section_ids:
        normalized = _normalized_markdown_heading(section_id)
        matches = [(index, item) for index, item in enumerate(headings)
                   if item[2] == normalized]
        if len(matches) != 1:
            raise OperationError('skill_context_unavailable', '所需的固定项目技能文件无法读取')
        index, (start, level, _) = matches[0]
        end = next((item[0] for item in headings[index + 1:]
                    if item[1] <= level), len(content))
        body = content[start:end]
        blocks.append({
            'section_id': section_id,
            'content': body,
            'content_sha256': sha256(body.encode('utf-8')).hexdigest(),
        })
    return blocks


def _script_skill_selection(contract, skill):
    """Select the source sections needed by this frozen screenplay role."""
    agent = contract.get('agent') or {}
    fixed = agent.get('fixed') or {}
    agent_id = fixed.get('id')
    delegated = set(contract.get('delegable_operations', []))
    is_coordinator = (agent_id == 'builtin:creative-lead'
                      and delegated == set(FORMAL_SCRIPT_DELEGABLE_OPERATIONS))
    loaded = {document['path']: document for document in skill.get('documents', [])}
    if not loaded:
        return None, None

    selections = {}
    skipped_paths = set()
    if is_coordinator:
        selections.update(_SCRIPT_COORDINATOR_SECTIONS)
        skipped_paths.update({
            'skills/script-master/references/writing.md',
            'skills/script-master/references/doctor.md',
        })
    else:
        root_refs = [path for path in (
            'skills/script-master/references/framework.md',
            'skills/script-master/references/writing.md',
            'skills/script-master/references/doctor.md',
            'skills/script-master/references/dialogue.md',
            'skills/script-master/references/visual-director.md',
        ) if path in loaded]
        if root_refs:
            root_sections = ['第一部分 总体原则']
            for reference in root_refs:
                root_sections.extend(_SCRIPT_ROOT_SECTIONS[reference][1:])
            selections['skills/script-master/SKILL.md'] = tuple(dict.fromkeys(root_sections))
        if 'skills/script-master/references/writing.md' in loaded:
            selections.update(_SCRIPT_WRITER_SECTIONS)
        if 'skills/script-master/references/doctor.md' in loaded:
            selections.update(_SCRIPT_REVIEWER_SECTIONS)
    if not selections:
        return None, None

    prompt_documents = []
    receipt_documents = []
    for path, document in loaded.items():
        if path in skipped_paths:
            continue
        section_ids = selections.get(path)
        if section_ids is None:
            content = document['content']
            selection = {
                'mode': 'full', 'section_ids': [],
                'content_sha256': sha256(content.encode('utf-8')).hexdigest(),
            }
            prompt_documents.append({
                'path': path, 'sha256': document['sha256'],
                'selection': selection, 'content': content,
            })
        else:
            blocks = _markdown_section_blocks(document['content'], section_ids)
            excerpt = '\n\n'.join(block['content'] for block in blocks)
            selection = {
                'mode': 'sections',
                'section_ids': list(section_ids),
                'content_sha256': sha256(excerpt.encode('utf-8')).hexdigest(),
            }
            prompt_documents.append({
                'path': path, 'sha256': document['sha256'],
                'selection': selection, 'excerpt_blocks': blocks,
            })
        receipt_documents.append({
            'path': path, 'sha256': document['sha256'], 'selection': selection,
        })

    references_not_loaded = list(skill.get('references_not_loaded', []))
    for path in skipped_paths:
        if path not in references_not_loaded:
            references_not_loaded.append(path)
    prompt_skill = {
        'id': skill['id'],
        'documents': prompt_documents,
        'references_not_loaded': references_not_loaded,
        'scope_note': ('Each document selection marks full source text or the listed section excerpts; '
                       'other sections and references are unavailable to this role.'),
    }
    receipt = {'id': skill['id'], 'documents': receipt_documents}
    return prompt_skill, receipt


def _task_skill_prompt_context(contract, frozen_skill_context):
    """Build a budgeted prompt context and receipt from full trusted source docs."""
    prompt_skills = []
    receipt = []
    for skill in frozen_skill_context.get('skills', []):
        selected, selected_receipt = (None, None)
        if skill.get('id') == 'script-master':
            selected, selected_receipt = _script_skill_selection(contract, skill)
        if selected is None:
            prompt_documents = []
            receipt_documents = []
            for document in skill.get('documents', []):
                content_hash = sha256(document['content'].encode('utf-8')).hexdigest()
                selection = {'mode': 'full', 'section_ids': [], 'content_sha256': content_hash}
                prompt_documents.append({
                    'path': document['path'],
                    'sha256': document['sha256'],
                    'selection': selection,
                    'content': document['content'],
                })
                receipt_documents.append({
                    'path': document['path'],
                    'sha256': document['sha256'],
                    'selection': selection,
                })
            selected = {
                'id': skill['id'],
                'documents': prompt_documents,
                'references_not_loaded': list(skill.get('references_not_loaded', [])),
                'scope_note': skill.get('scope_note'),
            }
            selected_receipt = {'id': skill['id'], 'documents': receipt_documents}
        prompt_skills.append(selected)
        receipt.append(selected_receipt)

    value = {'precedence': frozen_skill_context['precedence'], 'skills': prompt_skills}
    if len(packed(value).encode('utf-8')) > MAX_PROMPT_SKILL_CONTEXT_BYTES:
        raise OperationError('context_too_large', '任务技能指导超过安全上下文预算，请缩小任务范围')
    return value, receipt
_SCHEMA = ('''CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, owner_id TEXT NOT NULL,
    username TEXT NOT NULL, canvas_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
    delegation_id TEXT NOT NULL UNIQUE, revision INTEGER NOT NULL, content TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS agent_run_model_budgets (
        project_id TEXT NOT NULL, owner_id TEXT NOT NULL, root_delegation_id TEXT NOT NULL,
        max_model_calls INTEGER NOT NULL CHECK(max_model_calls >= 1),
        used_model_calls INTEGER NOT NULL CHECK(used_model_calls >= 0),
        legacy_used_calls INTEGER NOT NULL CHECK(legacy_used_calls >= 0),
        PRIMARY KEY(project_id, root_delegation_id))''',
    '''CREATE TABLE IF NOT EXISTS agent_run_model_attempts (
        attempt_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, owner_id TEXT NOT NULL,
        root_delegation_id TEXT NOT NULL, run_id TEXT NOT NULL, delegation_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('admitted','unknown','response_received')),
        created_at REAL NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS agent_run_children (
        parent_run_id TEXT NOT NULL, round_index INTEGER NOT NULL CHECK(round_index >= 0),
        task_key TEXT NOT NULL, adoption_id TEXT NOT NULL, state TEXT NOT NULL,
        create_request TEXT NOT NULL, start_request TEXT NOT NULL,
        delegation_id TEXT, child_run_id TEXT, content TEXT NOT NULL,
        PRIMARY KEY(parent_run_id,round_index,task_key),
        UNIQUE(parent_run_id,round_index,adoption_id,task_key),
        UNIQUE(delegation_id), UNIQUE(child_run_id))''')


def _payload_contract(authorization=None):
    """Describe exact payload fields per operation; preparation fields are not node fields."""
    selection = {
        'target_node_ids': 'unique node IDs within the frozen delegation scope',
        'input_indices': 'unique zero-based indexes into frozen inputs',
        'acceptance_indices': 'one or more unique zero-based indexes into frozen acceptance criteria',
    }
    contract = {
        'context.read': deepcopy(selection),
        'proposal.create': {**deepcopy(selection), 'title': 'candidate title', 'text': 'candidate text'},
        'ontology.definition.draft': {**deepcopy(selection), 'definition': 'unconfirmed definition draft'},
        'media.propose': {**deepcopy(selection), 'title': 'media proposal title',
                          'intent': 'description of proposed media',
                          'modality': 'one of image, video, audio, or 3d'},
        # This is a trusted, reversible operation carried by the existing
        # media.propose capability; it creates a design draft and one image
        # node candidate, never permission to run that node.
        'asset.design.create': {
            **deepcopy(selection),
            'target_node_ids': ('[] on canvas scope; [group_id] on group scope. On nodes scope choose exactly one selected groupNode ID [group_node_id], '
                                'or exactly one selected image source node ID, which must be an imageGenNode, imageNode, or image-only uploadNode. '
                                'For a groupNode target, use its selected ID directly and no image input reference is required. For an image source target, '
                                'keep its ID as the target and include its frozen image input in input_indices and reference_indices. The service derives '
                                'the image source output parent from frozen parentId/ancestry and membership; a null parent means top-level add-only. '
                                'Do not provide an output container ID.'),
            'kind': 'character or scene',
            'purpose': ('optional: avatar, half_body, full_body, body, or character_sheet for character; scene for scene. '
                        'Default new character designs to character_sheet unless the user asks for a different framing.'),
            'aspect_ratio': ('optional: one of 1:1, 9:16, 16:9, 3:4, 4:3, 3:2, 2:3, 4:5, 5:4, 21:9; never auto. '
                             'Pass the exact allowed ratio when explicitly requested; otherwise omit to use the purpose default.'),
            'camera_selection': ('optional partial object with only these fields: cameraBodyId must be one of '
                'panavision_dxl2, arri_alexa_65, red_vraptor_xl, sony_venice_2; lensId must be one of '
                'arri_signature_prime, cooke_s4i, zeiss_supreme_prime, panavision_primo_70; focalLengthMm must be '
                'one of 8, 14, 24, 35, 50, 75, 125; aperture must be one of f/1.4, f/2, f/2.8, f/4, f/5.6, '
                'f/8. Preserve explicitly requested allowed settings; omit unspecified fields to use purpose defaults. '
                'Do not send display labels or free text.'),
            'name': 'design name',
            'description': 'design description',
            'prompt': 'image-generation prompt draft',
            'reference_indices': ('unique indexes into input_indices for fixed references; only when the nodes target is an image source, include its frozen '
                                  'selected source image input index. A groupNode target does not require a canvas_node image reference.'),
            'draft_title': 'with draft_text, creates new source text in this operation; omit both in reference mode',
            'draft_text': 'with draft_title, creates new source text in this operation; omit both in reference mode',
            'source_proposal_input_index': ('required only when reusing a frozen, verified run_output story_fragment; '
                                            'must be one of input_indices'),
            'style_template_id': 'optional fixed style template ID or null',
            'base_asset_input_index': 'optional input index for a fixed base asset, or null',
        },
        'script.document.draft': {
            **deepcopy(selection),
            'episode_number': 'positive episode number',
            'title': 'draft title, 1 to 200 characters',
            'markdown': 'draft screenplay markdown, 1 to 100000 characters',
            'source_input_index': 'optional frozen run_output story_fragment input index, or null',
        },
        'script.document.review': {
            **deepcopy(selection),
            'document_input_index': 'index of a frozen script_document input',
            'report': {
                'conclusion': 'passed or needs_revision',
                'summary': 'review summary, 1 to 12000 characters',
                'issues': [{
                    'severity': 'blocking, major, minor, or note',
                    'issue': 'specific issue, 1 to 4000 characters',
                    'evidence': 'evidence from the fixed document, 1 to 4000 characters',
                    'suggestion': 'suggested change, up to 4000 characters',
                }],
            },
        },
    }
    if authorization is not None:
        contract.update({
            'node.patch': {
                'node_id': 'one node ID in the explicit execution authorization',
                'expected_revision': 'the frozen canvas revision shown in node context',
                'changes': [{'path': 'editable field path', 'value': 'JSON value'}],
            },
            'node.run': {
                'node_id': 'one node ID in the explicit execution authorization',
                'operation': 'one exact registered operation authorized for that node',
                'expected_revision': 'the frozen canvas revision shown in node context',
            },
        })
    return contract


def _pending_admitted_node_run(run):
    """Whether recovery can reconcile an already accepted, stable node.run action."""
    rounds = run.get('rounds') if isinstance(run, dict) else None
    if not isinstance(rounds, list) or not rounds:
        return False
    step = rounds[-1]
    if not isinstance(step, dict) or step.get('phase') != 'decision_saved':
        return False
    admitted = step.get('actions_admitted')
    done = step.get('actions_done')
    decision = step.get('decision')
    actions = decision.get('actions') if isinstance(decision, dict) else None
    if not isinstance(admitted, list) or not isinstance(done, list) or not isinstance(actions, list):
        return False
    completed = {index for index in done if type(index) is int}
    return any(type(index) is int and 0 <= index < len(actions)
               and index not in completed and isinstance(actions[index], dict)
               and actions[index].get('operation') == 'node.run'
               for index in admitted)


def _drive_failure_error(exc, run, *, response_received=False):
    """Classify failures by the durable phase that actually failed."""
    rounds = run.get('rounds') if isinstance(run, dict) else None
    step = rounds[-1] if isinstance(rounds, list) and rounds else {}
    phase = step.get('phase')
    pending_node_run = _pending_admitted_node_run(run)
    response_received = (phase == 'model_inflight' and
                         (response_received or bool(step.get('response_received'))))
    if isinstance(exc, OperationError):
        error = {'code': exc.code, 'message': str(exc), **exc.details}
        if response_received:
            error['response_received'] = True
            error.setdefault('usage', step.get('usage', {}))
        return error
    if pending_node_run:
        return {'code': 'node_execution_failed',
                'message': '节点执行失败；恢复时只对账原节点请求，不会创建替代运行'}
    if response_received:
        return {'code': 'model_response_processing_failed',
                'message': '模型已返回，但运行校验未完成；未自动重发',
                'response_received': True, 'usage': step.get('usage', {})}
    if phase == 'model_inflight':
        return {'code': 'model_submission_unknown', 'message': '运行中断，未自动重试模型请求'}
    if phase == 'decision_saved':
        return {'code': 'run_action_failed', 'message': '已保存的运行动作未完成；请检查任务状态'}
    return {'code': 'run_failed', 'message': '运行未能完成'}


def _decision_index_issues(decision, contract):
    """Validate model indexes against the exact frozen contract before saving.

    The Decision schema can validate shape and relationships within a response,
    but only the persisted delegation contract supplies the upper bounds for
    input and acceptance indexes.
    """
    input_count = len(contract.get('inputs', []))
    acceptance_count = len(contract.get('acceptance', []))
    issues = []

    def check_index_list(values, count, location, *, selected=None):
        if not isinstance(values, list):
            return
        for position, index in enumerate(values):
            if type(index) is not int or not 0 <= index < count:
                issues.append({'loc': [*location, position], 'type': 'index_out_of_range'})
            elif selected is not None and index not in selected:
                issues.append({'loc': [*location, position], 'type': 'index_not_selected'})

    def check_scalar_index(index, count, location, *, selected):
        if index is None:
            return
        if type(index) is not int or not 0 <= index < count:
            issues.append({'loc': location, 'type': 'index_out_of_range'})
        elif selected is not None and index not in selected:
            issues.append({'loc': location, 'type': 'index_not_selected'})

    for action_index, action in enumerate(decision.get('actions', [])):
        payload = action.get('payload', {})
        if not isinstance(payload, dict):
            continue
        input_indices = payload.get('input_indices')
        selected_inputs = set(input_indices) if isinstance(input_indices, list) else None
        action_root = ['actions', action_index, 'payload']
        check_index_list(input_indices, input_count, [*action_root, 'input_indices'])
        check_index_list(payload.get('acceptance_indices'), acceptance_count,
                         [*action_root, 'acceptance_indices'])

        if action.get('operation') == 'asset.design.create':
            check_index_list(payload.get('reference_indices'), input_count,
                             [*action_root, 'reference_indices'], selected=selected_inputs)
            check_scalar_index(payload.get('base_asset_input_index'), input_count,
                               [*action_root, 'base_asset_input_index'], selected=selected_inputs)
            check_scalar_index(payload.get('source_proposal_input_index'), input_count,
                               [*action_root, 'source_proposal_input_index'], selected=selected_inputs)
        elif action.get('operation') == 'script.document.draft':
            check_scalar_index(payload.get('source_input_index'), input_count,
                               [*action_root, 'source_input_index'], selected=selected_inputs)
        elif action.get('operation') == 'script.document.review':
            check_scalar_index(payload.get('document_input_index'), input_count,
                               [*action_root, 'document_input_index'], selected=selected_inputs)

    for child_index, child in enumerate(decision.get('delegations', [])):
        if isinstance(child, dict):
            check_index_list(child.get('input_indices'), input_count,
                             ['delegations', child_index, 'input_indices'])

    return issues[:64]


def checkpoint(phase):
    """Failure-injection boundary; never a recovery heuristic."""


def recover_expired_model_submissions(state_dir, *, now=None):
    """Classify interrupted model sends as unknown without ever resubmitting them.

    This startup sweep only writes a terminal recovery state for an expired
    model-inflight lease. A live lease, saved decision, or unknown attempt is
    left untouched. The SQL transaction makes concurrent API startups safely
    idempotent.
    """
    root = Path(state_dir).resolve()
    if not root.is_dir():
        return 0
    current_time = time.time() if now is None else float(now)
    recovered = 0
    for path in root.glob('**/creation/objects.sqlite3'):
        if path.is_symlink():
            continue
        try:
            resolved = path.resolve()
            if not resolved.is_relative_to(root) or not resolved.is_file():
                continue
            conn = sqlite3.connect(resolved, timeout=2, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA busy_timeout = 2000')
            try:
                conn.execute('BEGIN IMMEDIATE')
                tables = {row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if 'agent_runs' not in tables:
                    conn.rollback()
                    continue
                rows = conn.execute('SELECT id,project_id,owner_id,revision,content FROM agent_runs').fetchall()
                for row in rows:
                    try:
                        value = json.loads(row['content'])
                    except (TypeError, json.JSONDecodeError):
                        continue
                    rounds = value.get('rounds')
                    step = rounds[-1] if isinstance(rounds, list) and rounds else None
                    if (value.get('state') != 'model_inflight' or not isinstance(step, dict)
                            or step.get('phase') != 'model_inflight'):
                        continue
                    lease = value.get('lease')
                    expires = lease.get('expires') if isinstance(lease, dict) else None
                    if isinstance(expires, (int, float)) and expires > current_time:
                        continue
                    response_received = bool(step.get('response_received'))
                    if response_received:
                        step['phase'] = 'invalid_response'
                        error = {'code': 'model_response_processing_interrupted',
                                 'message': '模型已返回，但运行处理响应时中断；未自动重发'}
                    else:
                        step['phase'] = 'submission_unknown'
                        error = {'code': 'model_submission_unknown',
                                 'message': '服务恢复时模型提交结果未知，未自动重发'}
                    value.update(state='blocked' if response_received else 'submission_unknown',
                                 lease=None, revision=row['revision'] + 1, error=error)
                    updated = conn.execute('''UPDATE agent_runs SET revision=?,content=?
                        WHERE id=? AND revision=?''',
                        (value['revision'], json.dumps(value, ensure_ascii=False, sort_keys=True,
                                                       separators=(',', ':')),
                         row['id'], row['revision']))
                    if updated.rowcount != 1:
                        continue
                    attempt_id = step.get('attempt_id')
                    if attempt_id and 'agent_run_model_attempts' in tables:
                        conn.execute('''UPDATE agent_run_model_attempts SET state=?
                            WHERE attempt_id=? AND project_id=? AND owner_id=?''',
                            ('response_received' if response_received else 'unknown',
                             attempt_id, row['project_id'], row['owner_id']))
                    recovered += 1
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()
        except sqlite3.Error:
            # A busy or damaged project store must not prevent the API from
            # starting; a later explicit project read/control can recover it.
            continue
    return recovered


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
                          authorize_projection=self._authorize_node_projection_sync,
                          run_output_resolver=lambda conn, ref: self._run_output_input_for_consumer(
                              conn, identifier, ref))

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

    def _root_delegation_id(self, conn, delegation_id):
        """Resolve the shared model-budget root from trusted links, never caller input."""
        row = self.delegations._get(conn, delegation_id)
        expected_session = (self.ctx.project_id, self.ctx.requester_user_id, self.username,
                            self.canvas_id, self.conversation_id)
        seen, claims, root_id = set(), [], None
        while row is not None:
            require(row['id'] not in seen, '委托父链损坏', 'scope_violation')
            seen.add(row['id'])
            require((row['project_id'], row['owner_id'], row['owner_username'], row['canvas_id'],
                     row['conversation_id']) == expected_session,
                    '委托父链不属于当前运行会话', 'scope_violation')
            claim = row['contract'].get('root_delegation_id')
            if claim:
                claims.append(claim)
            parent_id = row['parent_id']
            if not parent_id:
                root_id = row['id']
            row = self.delegations._get(conn, parent_id) if parent_id else None
        require(root_id is not None and all(claim == root_id for claim in claims),
                '委托根身份与可信父链不一致', 'scope_violation')
        # A task-card continuation is its own node-authorization root, so the
        # original preparation contract and zero-node quota remain immutable.
        # Only the server-persisted task/candidate link may anchor its model
        # ledger back to that original task root.
        if table(conn, 'creation_collaboration_task_continuations'):
            continuation = conn.execute('''SELECT c.*,t.owner_id AS task_owner_id,
                    t.project_id AS task_project_id,t.canvas_id AS task_canvas_id,
                    t.conversation_id AS task_conversation_id,t.run_id AS task_run_id,
                    t.delegation_id AS task_delegation_id
                FROM creation_collaboration_task_continuations c
                JOIN creation_collaboration_tasks t ON t.id=c.task_id
                WHERE c.continuation_delegation_id=?''', (delegation_id,)).fetchone()
            if continuation is not None:
                candidate = json.loads(continuation['candidate'])
                expected_grant = {
                    'allowed_node_ids': [candidate['node_id']],
                    'allowed_node_operations': [{'node_id': candidate['node_id'],
                                                'operation': candidate['operation']}],
                    'max_node_runs': candidate['max_node_runs'],
                }
                extension = self.delegations._get(conn, delegation_id)
                contract = extension['contract']
                require((continuation['task_owner_id'], continuation['task_project_id'],
                         continuation['task_canvas_id'], continuation['task_conversation_id']) ==
                        (self.ctx.requester_user_id, self.ctx.project_id, self.canvas_id,
                         self.conversation_id)
                        and (continuation['owner_id'], continuation['project_id'],
                             continuation['canvas_id'], continuation['conversation_id']) ==
                        (str(self.ctx.requester_user_id), str(self.ctx.project_id), self.canvas_id,
                         self.conversation_id),
                        '任务续跑预算锚点不属于当前用户、项目或画布', 'scope_violation')
                require(continuation['state'] in {
                            'delegated', 'starting', 'queued', 'running', 'delivered',
                            'partial', 'stopped', 'submission_unknown', 'blocked'},
                        '任务续跑预算锚点状态无效', 'scope_violation')
                require(continuation['task_delegation_id'] == continuation['budget_root_delegation_id']
                        and continuation['task_run_id'] == continuation['parent_run_id']
                        and bool(continuation['task_run_id']),
                        '任务续跑预算锚点与原根运行不匹配', 'scope_violation')
                parent = self._get(conn, continuation['parent_run_id'])
                require(parent['delegation_id'] == continuation['budget_root_delegation_id']
                        and parent.get('parent_run_id') is None
                        and parent.get('root_delegation_id') == continuation['budget_root_delegation_id'],
                        '任务续跑只能锚定其真实原根运行', 'scope_violation')
                require(extension['parent_id'] is None and contract.get('root_delegation_id') == delegation_id
                        and contract.get('canvas_id') == self.canvas_id
                        and contract.get('conversation_id') == self.conversation_id
                        and contract.get('execution_authorization') == expected_grant
                        and contract.get('allowed_operations') == ['node.run']
                        and contract.get('expanded_scope', {}).get('node_ids') == [candidate['node_id']]
                        and candidate.get('canvas_id') == self.canvas_id
                        and candidate.get('node_type') == 'imageGenNode'
                        and candidate.get('operation') == 'generate'
                        and candidate.get('max_node_runs') == 1,
                        '任务续跑授权未精确匹配已验证的候选节点', 'scope_violation')
                require(continuation['budget_root_delegation_id'] == parent['root_delegation_id']
                        and continuation['budget_root_delegation_id'] == continuation['task_delegation_id'],
                        '续跑只能扩展原任务根的模型预算锚点', 'scope_violation')
                return continuation['budget_root_delegation_id']
        return root_id

    def _historical_model_calls(self, conn, root_delegation_id):
        """Conservatively migrate old rounds, deduplicating durable attempt IDs."""
        if not table(conn, 'agent_runs') or not table(conn, 'agent_delegations'):
            return 0
        rows = conn.execute('''SELECT id, delegation_id, content FROM agent_runs
            WHERE project_id=? AND owner_id=? AND username=? AND canvas_id=? AND conversation_id=?''',
            (self.ctx.project_id, self.ctx.requester_user_id, self.username,
             self.canvas_id, self.conversation_id)).fetchall()
        received_or_unknown = {'submission_unknown', 'invalid_response', 'decision_saved', 'applied'}
        seen_attempts, used = set(), 0
        for run_row in rows:
            if self._root_delegation_id(conn, run_row['delegation_id']) != root_delegation_id:
                continue
            historical = json.loads(run_row['content'])
            for round_value in historical.get('rounds', []):
                if round_value.get('send_admitted') is False and round_value.get('phase') not in received_or_unknown:
                    continue
                attempt_id = round_value.get('attempt_id')
                if attempt_id and attempt_id in seen_attempts:
                    continue
                if attempt_id:
                    seen_attempts.add(attempt_id)
                used += 1
        return used

    def _ensure_root_model_budget(self, conn, root_delegation_id, *, root_limit=None):
        row = conn.execute('''SELECT * FROM agent_run_model_budgets
            WHERE project_id=? AND root_delegation_id=?''',
            (self.ctx.project_id, root_delegation_id)).fetchone()
        if row is not None:
            require(row['owner_id'] == self.ctx.requester_user_id,
                    '模型调用预算归属无效', 'scope_violation')
            return dict(row)

        root = self.delegations._get(conn, root_delegation_id)
        require(root['parent_id'] is None, '模型调用预算根委托无效', 'scope_violation')
        if root_limit is None:
            root_run = conn.execute('''SELECT content FROM agent_runs WHERE project_id=? AND owner_id=?
                AND username=? AND canvas_id=? AND conversation_id=? AND delegation_id=?''',
                (self.ctx.project_id, self.ctx.requester_user_id, self.username, self.canvas_id,
                 self.conversation_id, root_delegation_id)).fetchone()
            require(root_run is not None, '根运行尚未冻结模型调用额度', 'root_model_budget_missing')
            root_value = json.loads(root_run['content'])
            root_limit = root_value.get('root_max_model_calls', root_value.get('max_model_calls'))
        root_limit = integer(root_limit, 1)
        historical_used = self._historical_model_calls(conn, root_delegation_id)
        conn.execute('''INSERT INTO agent_run_model_budgets
            (project_id,owner_id,root_delegation_id,max_model_calls,used_model_calls,legacy_used_calls)
            VALUES (?,?,?,?,?,?)''',
            (self.ctx.project_id, self.ctx.requester_user_id, root_delegation_id,
             root_limit, historical_used, historical_used))
        return {'project_id': self.ctx.project_id, 'owner_id': self.ctx.requester_user_id,
                'root_delegation_id': root_delegation_id, 'max_model_calls': root_limit,
                'used_model_calls': historical_used, 'legacy_used_calls': historical_used}

    def _remaining_root_model_calls(self, conn, value):
        if conn is None:
            # There is no trusted shared ledger to read, so don't advertise a
            # per-run allowance as if it were available at the root.
            return 0
        root_id = value.get('root_delegation_id')
        if not root_id and table(conn, 'agent_delegations'):
            root_id = self._root_delegation_id(conn, value['delegation_id'])
        if not root_id:
            return 0
        if table(conn, 'agent_run_model_budgets'):
            row = conn.execute('''SELECT max_model_calls,used_model_calls FROM agent_run_model_budgets
                WHERE project_id=? AND owner_id=? AND root_delegation_id=?''',
                (self.ctx.project_id, self.ctx.requester_user_id, root_id)).fetchone()
            if row is not None:
                return max(0, row['max_model_calls'] - row['used_model_calls'])
        root_run = conn.execute('''SELECT content FROM agent_runs WHERE project_id=? AND owner_id=?
            AND username=? AND canvas_id=? AND conversation_id=? AND delegation_id=?''',
            (self.ctx.project_id, self.ctx.requester_user_id, self.username, self.canvas_id,
             self.conversation_id, root_id)).fetchone() if table(conn, 'agent_runs') else None
        if root_run is None:
            return 0
        root_value = json.loads(root_run['content'])
        limit = root_value.get('root_max_model_calls', root_value.get('max_model_calls', 0))
        if type(limit) is not int or limit < 1:
            return 0
        return max(0, limit - self._historical_model_calls(conn, root_id))

    def _reserve_model_attempt(self, conn, value, step, root_delegation_id):
        budget = self._ensure_root_model_budget(conn, root_delegation_id)
        existing = conn.execute('SELECT * FROM agent_run_model_attempts WHERE attempt_id=?',
                                (step['attempt_id'],)).fetchone()
        if existing is not None:
            require((existing['project_id'], existing['owner_id'], existing['root_delegation_id'],
                     existing['run_id'], existing['delegation_id']) ==
                    (self.ctx.project_id, self.ctx.requester_user_id, root_delegation_id,
                     value['id'], step['delegation_id']),
                    '模型 attempt 身份冲突', 'idempotency_conflict')
            return False
        require(budget['used_model_calls'] < budget['max_model_calls'],
                '根模型调用额度已用完', 'call_limit_reached')
        conn.execute('''INSERT INTO agent_run_model_attempts
            (attempt_id,project_id,owner_id,root_delegation_id,run_id,delegation_id,state,created_at)
            VALUES (?,?,?,?,?,?,?,?)''',
            (step['attempt_id'], self.ctx.project_id, self.ctx.requester_user_id,
             root_delegation_id, value['id'], step['delegation_id'], 'admitted', time.time()))
        updated = conn.execute('''UPDATE agent_run_model_budgets SET used_model_calls=used_model_calls+1
            WHERE project_id=? AND owner_id=? AND root_delegation_id=? AND used_model_calls<max_model_calls''',
            (self.ctx.project_id, self.ctx.requester_user_id, root_delegation_id))
        require(updated.rowcount == 1, '根模型调用额度已用完', 'call_limit_reached')
        return True

    def _set_model_attempt_state(self, conn, attempt_id, state):
        conn.execute('''UPDATE agent_run_model_attempts SET state=?
            WHERE attempt_id=? AND project_id=? AND owner_id=?''',
            (state, attempt_id, self.ctx.project_id, self.ctx.requester_user_id))

    @staticmethod
    def _action_id(identifier, index, action_index):
        return f'run:{identifier}:{index}:action:{action_index}'

    def _outputs_with_provenance(self, conn, value):
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
                    outputs.append((output, {'source_run_id': value['id'],
                        'source_round_index': index, 'source_action_index': action_index,
                        'source_action_request_id': self._action_id(value['id'], index, action_index),
                        'source_delegation_id': step['delegation_id'],
                        'source_operation_id': json.loads(row[0]).get('operation_id')}))
        return outputs

    def _outputs(self, conn, value):
        return [output for output, _ in self._outputs_with_provenance(conn, value)]

    def _child_rows(self, conn, parent_run_id, round_index=None):
        if not conn or not table(conn, 'agent_run_children'):
            return []
        query = 'SELECT * FROM agent_run_children WHERE parent_run_id=?'
        params = [parent_run_id]
        if round_index is not None:
            query += ' AND round_index=?'
            params.append(round_index)
        query += ' ORDER BY round_index, rowid'
        return [dict(row) for row in conn.execute(query, params)]

    def _children_public(self, conn, value):
        result = []
        for row in self._child_rows(conn, value['id']):
            plan = json.loads(row['content'])
            entry = {'task_key': row['task_key'], 'adoption_id': row['adoption_id'],
                     'round_index': row['round_index'], 'state': row['state'],
                     'delegation_id': row['delegation_id'], 'child_run_id': row['child_run_id'],
                     'depends_on': plan.get('depends_on', []),
                     'unmet': plan.get('unmet', []),
                     'goal': plan.get('goal'), 'acceptance': deepcopy(plan.get('acceptance', [])),
                     'output_summaries': []}
            frozen = next((item for item in value.get('specialists', [])
                           if item.get('adoption_id') == row['adoption_id']), None)
            profile = deepcopy(frozen.get('effective', {})) if isinstance(frozen, dict) else {}
            revision = frozen.get('revision') if isinstance(frozen, dict) else None
            child_contract = None
            if row['delegation_id']:
                child_contract = self.delegations._get(conn, row['delegation_id'])['contract']
                child_agent = child_contract.get('agent', {})
                pinned_profile = child_agent.get('effective')
                if isinstance(pinned_profile, dict):
                    profile = deepcopy(pinned_profile)
                    revision = child_agent.get('revision', revision)
                entry['goal'] = child_contract.get('goal', entry['goal'])
                entry['acceptance'] = deepcopy(child_contract.get('acceptance', entry['acceptance']))
            if profile:
                entry['agent'] = {'adoption_id': row['adoption_id'], 'revision': revision,
                                  'name': profile.get('name'), 'profile': profile}
            if row['child_run_id']:
                child = self._get(conn, row['child_run_id'])
                entry['state'] = child['state']
                entry['unmet'] = child.get('unmet', [])
                outputs = self._outputs(conn, child)
                for output in outputs:
                    object_payload = (output.get('object') or {}).get('payload', {})
                    summary = (output.get('summary') or output.get('description')
                                   or output.get('intent') or object_payload.get('text')
                                   or output.get('markdown'))
                    if isinstance(summary, str):
                        summary = summary[:500]
                    else:
                        summary = None
                    item = {'id': output.get('id'), 'operation': output.get('operation'),
                            'state': output.get('state'),
                            'title': output.get('title') or output.get('name') or object_payload.get('title'),
                            'summary': summary,
                            'conclusion': output.get('conclusion')}
                    if isinstance(output.get('prepared_execution'), dict):
                        item['prepared_execution'] = deepcopy(output['prepared_execution'])
                    if (output.get('operation') == 'script.document.draft'
                            and output.get('state') == 'draft'
                            and isinstance(output.get('document_ref'), dict)):
                        item['document_ref'] = deepcopy(output['document_ref'])
                        projection = output.get('projection')
                        if (isinstance(projection, dict) and projection.get('status') == 'applied'
                                and isinstance(projection.get('node_id'), str)):
                            item['node_id'] = projection['node_id']
                    entry['output_summaries'].append(item)
            result.append(entry)
        return result

    def _selectable_output_records(self, conn, value):
        result = []
        for output, provenance in self._outputs_with_provenance(conn, value):
            if (output.get('operation') == 'proposal.create'
                    and output.get('state') == 'candidate'
                    and isinstance(output.get('object_ref'), dict)):
                result.append((output, provenance))
                continue
            if self._is_script_document_draft_output(conn, output, provenance):
                result.append((output, provenance))
        return result

    def _is_script_document_draft_output(self, conn, output, provenance):
        ref = output.get('document_ref')
        projection = output.get('projection')
        origin = output.get('source_origin')
        if (output.get('operation') != 'script.document.draft' or output.get('state') != 'draft'
                or not isinstance(ref, dict) or ref.get('kind') != 'episode_script'
                or not isinstance(ref.get('document_id'), str) or not isinstance(ref.get('revision_id'), str)
                or not isinstance(projection, dict) or projection.get('status') != 'applied'
                or projection.get('canvas_id') != self.canvas_id
                or not isinstance(projection.get('node_id'), str)
                or not isinstance(origin, dict) or origin.get('kind') != 'agent_script_draft'
                or origin.get('project_id') != self.ctx.project_id
                or origin.get('canvas_id') != self.canvas_id
                or origin.get('delegation_id') != provenance.get('source_delegation_id')
                or origin.get('member_id') != output.get('member_id')):
            return False
        try:
            source_contract = self.delegations._get(conn, provenance['source_delegation_id'])['contract']
        except (KeyError, OperationError):
            return False
        agent = source_contract.get('agent', {})
        return (agent.get('adoption_id') == origin.get('member_id')
                and agent.get('revision') == origin.get('member_revision')
                and 'script.document.draft' in source_contract.get('allowed_operations', []))

    def _model_outputs(self, conn, value):
        visible = []
        for index, (output, provenance) in enumerate(self._selectable_output_records(conn, value)):
            payload = (output.get('object') or {}).get('payload', {})
            item = {'output_index': index, 'source_task_key': None,
                    'operation': output['operation'], 'state': output['state'],
                    'title': payload.get('title', output.get('title', '')),
                    'round_index': provenance['source_round_index'],
                    'action_index': provenance['source_action_index']}
            if output['operation'] == 'proposal.create':
                item['text'] = payload.get('text', '')
            else:
                item.update(kind='script_document',
                    document_ref={'kind': 'script_document', 'canvas_id': self.canvas_id,
                                  'document_id': output['document_ref']['document_id'],
                                  'revision_id': output['document_ref']['revision_id']},
                    summary=output.get('markdown', '')[:500])
            visible.append(item)
        return visible

    @staticmethod
    def _operation_semantics():
        return {
            'task_brief': (
                'task_brief is the frozen original user creation request; this role goal is only your bounded subtask. '
                'Every explicit age, appearance, clothing, setting, and event requirement in task_brief remains binding '
                'even when the subtask goal does not repeat it. Do not describe a detail as unconfirmed when the user '
                'specified it there. This brief constrains content only and never expands canvas/node scope, operations, '
                'execution authorization, or output counts.'),
            'asset.design.create': (
                'This is preparation only. On canvas scope set target_node_ids to []; on group scope use exactly [group_id]. '
                'On nodes scope choose exactly one of two branches: for the selected groupNode, use target_node_ids=[group_node_id]. '
                'A groupNode target does not require an image input reference. For a selected fixed image source, use target_node_ids=[source_node_id]; '
                'the source must be an imageGenNode, imageNode, or image-only uploadNode. Include that same frozen canvas_node input in input_indices '
                'and reference_indices. For the image-source branch, the service derives the output parent group from frozen parentId/ancestry and '
                'membership; with a null parent, the design is top-level add-only. Do not provide an output container ID. '
                'The service creates the text and image node IDs. '
                'It does not run media and does not require node.run authorization. Choose one source: provide both draft_title and '
                'draft_text for a new source draft in this operation, or provide source_proposal_input_index for a verified frozen '
                'run_output story_fragment input; do not send both modes. A canvas_node image input is a visual reference only; '
                'it is never a source_proposal_input_index. When no frozen run_output story_fragment input is supplied, use draft mode '
                'by providing both draft_title and draft_text. To use a selected image, include its index in both input_indices and '
                'reference_indices while still providing both draft fields. Explicit null for an unused optional field is treated the same as omission. '
                'For a new character design, default purpose=character_sheet unless the user explicitly asks for an avatar, half-body, '
                'or full-body-only framing. character_sheet means one 16:9 character sheet image with four views: a large face close-up '
                'and full-body front, side/profile, and back views. All four views must show the same person with an identical face, age, '
                'hairstyle, clothing, accessories, and body build; each full-body view must show the complete figure from head to shoes '
                'without cropping. If a frozen asset reference is supplied, preserve that character identity across every view. Do not '
                'turn the sheet into four different people or a half-body portrait. For character_sheet, when the user does not specify '
                'these settings, leave aspect_ratio and camera_selection omitted; the service applies the frozen defaults of 16:9, 50mm, '
                'and f/8. If the user explicitly requests these settings, preserve the explicitly requested aspect_ratio and '
                'camera_selection using only allowed schema values; leave unspecified fields omitted. The image must contain no text, '
                'caption, logo, or watermark.'),
            'script.document.draft': (
                'Create an unadopted, versioned episode screenplay draft on the current concept canvas. '
                'Provide the complete draft, title, and positive episode number. source_input_index is optional and, '
                'when used, must select a fixed verified run_output story_fragment input.'),
            'script.document.review': (
                'Review only the exact fixed script_document input selected by document_input_index. '
                'Return a version-scoped report; this does not adopt the draft or grant handoff/production permission.'),
        }

    def _run_output_ref(self, conn, parent_run_id, round_index, task_key, selection):
        parent = self._get(conn, parent_run_id)
        source_key = selection.get('source_task_key')
        consumer_row = conn.execute('''SELECT content FROM agent_run_children WHERE parent_run_id=?
            AND round_index=? AND task_key=?''', (parent_run_id, round_index, task_key)).fetchone()
        require(consumer_row is not None, '输出引用消费者没有子任务计划', 'scope_violation')
        consumer_plan = json.loads(consumer_row['content'])
        if source_key is not None:
            require(source_key in consumer_plan.get('depends_on', []),
                    '子任务只能选择已声明依赖的兄弟输出', 'scope_violation')
        if source_key is None:
            require(parent.get('run_schema_version', 1) >= 2,
                    '旧串行运行不接受新的兄弟输出引用', 'scope_violation')
            source = parent
            source_delegation_id = parent['delegation_id']
        else:
            require(source_key != task_key, '任务不能引用自己的输出', 'scope_violation')
            row = conn.execute('''SELECT * FROM agent_run_children
                WHERE parent_run_id=? AND round_index=? AND task_key=?''',
                (parent_run_id, round_index, source_key)).fetchone()
            require(row is not None and row['child_run_id'], '依赖任务尚未创建运行', 'dependency_unavailable')
            source = self._get(conn, row['child_run_id'])
            require(source['state'] == 'delivered' and self._has_verified_output(conn, source),
                    '依赖任务没有已交付的真实输出', 'dependency_unavailable')
            source_delegation_id = row['delegation_id']
        records = self._selectable_output_records(conn, source)
        index = integer(selection.get('output_index'), 0)
        require(index < len(records), '所选任务没有该序号的已验证文本候选', 'dependency_output_missing')
        output, provenance = records[index]
        require(self._root_delegation_id(conn, source['delegation_id']) ==
                self._root_delegation_id(conn, parent['delegation_id']),
                '依赖输出不属于当前父授权链', 'scope_violation')
        if output.get('operation') == 'script.document.draft':
            document_ref = output['document_ref']
            ref = {'kind': 'script_document', 'canvas_id': self.canvas_id,
                   'document_id': document_ref['document_id'],
                   'revision_id': document_ref['revision_id']}
            fixed = agent_context.inputs(self.ctx, conn, [ref], username=self.username,
                current_canvas_id=self.canvas_id)
            origin = fixed[0]['snapshot'].get('source_origin', {})
            require(origin.get('delegation_id') == provenance['source_delegation_id']
                    and origin.get('member_id') == output.get('member_id')
                    and origin.get('member_revision') == output.get('member', {}).get('revision'),
                    '正式剧本来源与运行输出身份不一致', 'scope_violation')
            from novelvideo.freezone.canvas_store import read_canvas
            canvas = read_canvas(self.ctx.state_dir, self.canvas_id)
            node = next((item for item in (canvas or {}).get('nodes', [])
                         if item.get('id') == output['projection']['node_id']), None)
            require(node is not None and node.get('data', {}).get('formalScriptRef', {}).get('document_id') ==
                    ref['document_id']
                    and node.get('data', {}).get('formalScriptRef', {}).get('revision_id') == ref['revision_id'],
                    '正式剧本画布引用已变化', 'stale_input')
            return ref
        obj_ref = output['object_ref']
        return {'kind': 'run_output', 'parent_run_id': parent_run_id,
                'source_run_id': source['id'], 'source_task_key': source_key,
                'source_delegation_id': source_delegation_id,
                'source_output_id': output['id'],
                'source_action_request_id': provenance['source_action_request_id'],
                'object_id': obj_ref['id'], 'revision': obj_ref['revision'],
                'canvas_id': self.canvas_id}

    def _resolve_run_output_input(self, conn, ref, *, parent_run_id, round_index, task_key,
                                  depends_on, authorization_delegation_id):
        exact(ref, {'kind', 'parent_run_id', 'source_run_id', 'source_task_key',
                    'source_delegation_id', 'source_output_id', 'source_action_request_id',
                    'object_id', 'revision', 'canvas_id'})
        require(ref['kind'] == 'run_output' and ref['parent_run_id'] == parent_run_id
                and ref['canvas_id'] == self.canvas_id,
                '运行输出引用不属于当前父运行画布', 'scope_violation')
        text(ref['source_run_id'], 128)
        text(ref['source_delegation_id'], 128)
        text(ref['source_output_id'], 128)
        text(ref['source_action_request_id'], 128)
        text(ref['object_id'], 128)
        revision = integer(ref['revision'], 1)
        parent = self._get(conn, parent_run_id)
        consumer = self.delegations._get(conn, authorization_delegation_id)['contract']
        require((consumer['project_id'], consumer['owner_id'], consumer['owner_username'],
                 consumer['canvas_id'], consumer['conversation_id']) ==
                (self.ctx.project_id, self.ctx.requester_user_id, self.username,
                 self.canvas_id, self.conversation_id),
                '输出消费者身份不属于当前运行会话', 'scope_violation')
        if ref['source_task_key'] is None:
            require(ref['source_run_id'] == parent_run_id
                    and ref['source_delegation_id'] == parent['delegation_id'],
                    '父运行输出来源不匹配', 'scope_violation')
            source = parent
        else:
            require(ref['source_task_key'] in depends_on,
                    '子任务只能读取已声明依赖的兄弟输出', 'scope_violation')
            source_row = conn.execute('''SELECT * FROM agent_run_children
                WHERE parent_run_id=? AND round_index=? AND task_key=?''',
                (parent_run_id, round_index, ref['source_task_key'])).fetchone()
            require(source_row is not None and source_row['child_run_id'] == ref['source_run_id']
                    and source_row['delegation_id'] == ref['source_delegation_id'],
                    '兄弟运行输出身份不匹配', 'scope_violation')
            source = self._get(conn, source_row['child_run_id'])
            require(source['state'] == 'delivered' and self._has_verified_output(conn, source),
                    '兄弟任务输出尚未交付', 'dependency_unavailable')
        require(self._root_delegation_id(conn, source['delegation_id']) ==
                self._root_delegation_id(conn, authorization_delegation_id),
                '运行输出不属于同一父授权链', 'scope_violation')
        source_match = False
        for action_round, step in enumerate(source.get('rounds', [])):
            for action_index, action in enumerate(step.get('decision', {}).get('actions', [])):
                request_id = self._action_id(source['id'], action_round, action_index)
                if (request_id == ref['source_action_request_id']
                        and step.get('delegation_id') == ref['source_delegation_id']
                        and action.get('operation') == 'proposal.create'
                        and action_index in step.get('actions_done', [])):
                    source_match = True
                    break
            if source_match:
                break
        require(source_match, '输出缺少已完成的运行动作来源', 'scope_violation')
        row = conn.execute('''SELECT d.content,d.operation_id,o.request_id,o.actor_id,
                r.domain,r.project_id,r.result FROM delegation_outputs d
            JOIN operations o ON o.id=d.operation_id
            JOIN creative_domain_receipts r ON r.request_id=o.request_id
            WHERE d.id=? AND d.project_id=? AND d.owner_id=? AND d.canvas_id=?
              AND d.conversation_id=? AND d.delegation_id=? AND o.actor_id=?''',
            (ref['source_output_id'], self.ctx.project_id, self.ctx.requester_user_id,
             self.canvas_id, self.conversation_id, ref['source_delegation_id'],
             self.ctx.requester_user_id)).fetchone()
        require(row is not None and row['domain'] == 'agent_tools'
                and row['project_id'] == self.ctx.project_id
                and row['request_id'] == ref['source_action_request_id'],
                '运行输出没有同用户的 AgentTools 回执', 'scope_violation')
        output = json.loads(row['content'])
        receipt = json.loads(row['result'])
        receipt_output = (receipt.get('output') if receipt.get('delegation_id') else None)
        require(output.get('id') == ref['source_output_id']
                and output.get('operation') == 'proposal.create' and output.get('state') == 'candidate'
                and output.get('object_ref') == {'id': ref['object_id'], 'revision': revision}
                and receipt.get('delegation_id') == ref['source_delegation_id']
                and receipt_output == output,
                '运行输出候选或回执内容不匹配', 'scope_violation')
        obj = ObjectStore._read(conn, ref['object_id'], revision)
        require(obj is not None and obj['kind'] == 'story_fragment'
                and obj['actor_id'] == self.ctx.requester_user_id
                and obj['payload'].get('canvas_id') == self.canvas_id,
                '固定故事候选版本或归属无效', 'scope_violation')
        provenance = {'source_run_id': source['id'],
            'source_task_key': ref['source_task_key'],
            'source_delegation_id': ref['source_delegation_id'],
            'source_output_id': ref['source_output_id'],
            'source_action_request_id': ref['source_action_request_id'],
            'source_operation_id': row['operation_id']}
        return {'ref': deepcopy(ref), 'snapshot': {'kind': 'story_fragment', 'id': obj['id'],
            'revision': obj['revision'], 'payload': obj['payload'], 'provenance': provenance}}

    def _run_output_input_for_consumer(self, conn, consumer_delegation_id, ref):
        row = conn.execute('''SELECT * FROM agent_run_children WHERE delegation_id=?''',
                           (consumer_delegation_id,)).fetchone() if table(conn, 'agent_run_children') else None
        require(row is not None, '运行输出消费者没有受信任的父任务记录', 'scope_violation')
        content = json.loads(row['content'])
        return self._resolve_run_output_input(conn, ref, parent_run_id=row['parent_run_id'],
            round_index=row['round_index'], task_key=row['task_key'],
            depends_on=content.get('depends_on', []),
            authorization_delegation_id=consumer_delegation_id)

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
        result['children'] = self._children_public(conn, value)
        result['remaining_model_calls'] = self._remaining_root_model_calls(conn, value)
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
            if request.action == 'admit_model':
                # A durable operation receipt may be replayed after the first
                # caller already received its send grant. Replays retain the
                # state change but cannot authorize another provider request.
                result['model_send_admitted'] = False
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

    async def start_with_pinned_specialists(self, body, specialists):
        """Start a trusted host run with the adoption revisions frozen at intake."""
        exact(body, {'request_id', 'delegation_id', 'contract_hash', 'max_model_calls'})
        require(isinstance(specialists, list) and len(specialists) <= agent_model.MAX_ELIGIBLE_SPECIALISTS,
                '专业Agent选择无效')
        frozen = []
        for item in specialists:
            exact(item, {'adoption_id', 'revision'})
            frozen.append({'adoption_id': text(item['adoption_id'], 128),
                           'revision': integer(item['revision'], 1)})
        require(len({item['adoption_id'] for item in frozen}) == len(frozen), '专业Agent选择无效')
        return await self._send('start_pinned', {
            **{key: value for key, value in body.items() if key != 'request_id'},
            'specialists': frozen,
        }, body['request_id'])

    async def start_continuation(self, body):
        """Start one task-linked continuation; only the task service calls this."""
        exact(body, {'request_id', 'delegation_id', 'contract_hash', 'task_id',
                     'continuation_id', 'parent_run_id', 'source_operation_id',
                     'source_request_id'})
        return await self._send('start_continuation', {
            key: value for key, value in body.items() if key != 'request_id'
        }, body['request_id'])

    async def control(self, identifier, action, body):
        exact(body, {'request_id', 'expected_revision'})
        require(action in {'stop', 'resume'}, '未知运行控制操作')
        result = await self._send(action, {'id': identifier, 'expected_revision': body['expected_revision']}, body['request_id'])
        if action == 'stop':
            await self._stop_child_runs(result['run'])
            await self._stop_accepted_node_runs(result['run'])
        return result

    async def _stop_child_runs(self, value):
        """Cascade an explicit parent stop to persisted child runs only."""
        rows = await registration_io(self._read_child_rows, value['id'])
        for row in rows:
            child_id = row.get('child_run_id')
            if not child_id:
                continue
            child = await self.get(child_id)
            # Unknown provider submissions are terminal evidence. Keep their
            # state and durable phase intact so neither stop nor resume can
            # make a paid request retryable.
            if child['state'] in {'delivered', 'partial', 'stopped', 'submission_unknown'}:
                continue
            try:
                stopped = await self._send('stop', {'id': child_id,
                    'expected_revision': child['revision']})
            except OperationError as exc:
                if exc.code != 'revision_conflict':
                    raise
                child = await self.get(child_id)
                if child['state'] not in TERMINAL:
                    stopped = await self._send('stop', {'id': child_id,
                        'expected_revision': child['revision']})
                else:
                    continue
            await self._stop_child_runs(stopped['run'])
            await self._stop_accepted_node_runs(stopped['run'])
        continuation_ids = await registration_io(self._read_linked_continuation_run_ids, value['id'])
        for continuation_id in continuation_ids:
            child = await self.get(continuation_id)
            if child['state'] in TERMINAL:
                continue
            try:
                stopped = await self._send('stop', {'id': continuation_id,
                    'expected_revision': child['revision']})
            except OperationError as exc:
                if exc.code != 'revision_conflict':
                    raise
                child = await self.get(continuation_id)
                if child['state'] in TERMINAL:
                    continue
                stopped = await self._send('stop', {'id': continuation_id,
                    'expected_revision': child['revision']})
            await self._stop_child_runs(stopped['run'])
            await self._stop_accepted_node_runs(stopped['run'])

    def _read_linked_continuation_run_ids(self, parent_run_id):
        with self.read() as conn:
            if not table(conn, 'creation_collaboration_task_continuations'):
                return []
            return [row[0] for row in conn.execute('''SELECT continuation_run_id
                FROM creation_collaboration_task_continuations
                WHERE parent_run_id=? AND continuation_run_id IS NOT NULL
                ORDER BY created_at,id''', (parent_run_id,))]

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
        self._assert_ancestors_not_stopped(conn, value)
        return value

    def _assert_ancestors_not_stopped(self, conn, value):
        """Keep child authority fenced after an explicit ancestor stop.

        Ancestor lease expiry is deliberately irrelevant: only a persisted stop
        revokes child work. The check is performed in the same transaction as
        child send/action admission so the parent stop commit is the fence.
        """
        seen = {value['id']}
        cursor = value
        while cursor.get('parent_run_id'):
            parent_id = cursor['parent_run_id']
            require(parent_id not in seen, 'Agent run 父链出现循环', 'scope_violation')
            seen.add(parent_id)
            cursor = self._get(conn, parent_id)
            require(cursor['state'] != 'stopped', '父运行已停止，子运行授权已冻结', 'run_fenced')

    def _save(self, conn, value):
        conn.execute('UPDATE agent_runs SET revision=?,content=? WHERE id=?',
                     (value['revision'], packed(value), value['id']))
        return {'run': self._public(conn, value)}

    def _create_run(self, conn, *, delegation_id, contract_hash, max_model_calls,
                    specialist_ids, specialist_versions=None, inherited_specialists=None, expected_route=None,
                    parent_run_id=None, task_key=None):
        contract = self._validate_current(conn, delegation_id)
        require(digest(contract) == text(contract_hash, 128), '委托快照不匹配', 'stale_input')
        limit = integer(max_model_calls, 1)
        require(limit <= agent_model.MAX_MODEL_CALLS,
                f'最多允许 {agent_model.MAX_MODEL_CALLS} 次明确授权的模型调用')
        require(isinstance(specialist_ids, list) and len(specialist_ids) <= agent_model.MAX_ELIGIBLE_SPECIALISTS
                and all(isinstance(i, str) for i in specialist_ids)
                and len(specialist_ids) == len(set(specialist_ids)), '专业Agent选择无效')
        if specialist_versions is not None:
            require(isinstance(specialist_versions, list)
                    and [item.get('adoption_id') for item in specialist_versions] == specialist_ids,
                    '专业Agent冻结名单无效', 'scope_violation')
            specialists = [self.delegations._agent(conn, item['adoption_id'], item['revision'])
                           for item in specialist_versions]
        elif inherited_specialists is None:
            specialists = [self.delegations._agent(conn, identifier) for identifier in specialist_ids]
        else:
            specialists = deepcopy(inherited_specialists)
            require([item.get('adoption_id') for item in specialists] == specialist_ids,
                    '子运行专业Agent必须继承父运行的冻结名单', 'scope_violation')
        if contract.get('preparation_kind') == 'formal_episode_script':
            require(self._formal_script_roster_ready(specialists),
                    '正式剧本任务需要分别固定具备剧本草稿和独立审阅能力的成员；请升级或替换主笔与剧本编辑后新建任务',
                    'formal_script_specialists_missing')
        available = agent_model.availability(self.ctx)
        require(available['available'], '当前未配置可用的受限模型API', available['code'])
        if expected_route is not None:
            require(available['route'] == expected_route['route']
                    and available['configuration_fingerprint'] == expected_route['configuration_fingerprint'],
                    '模型配置已变化，子运行不能改变父运行的已冻结路由', 'runtime_configuration_changed')
        existing = conn.execute('SELECT id FROM agent_runs WHERE delegation_id=?',
                                 (delegation_id,)).fetchone()
        require(existing is None, '此委托已有运行，请恢复原运行', 'run_exists',
                run_id=existing[0] if existing else None)
        root_delegation_id = self._root_delegation_id(conn, delegation_id)
        budget = self._ensure_root_model_budget(conn, root_delegation_id,
            root_limit=limit if root_delegation_id == delegation_id else None)
        run_limit = min(limit, budget['max_model_calls'])
        value = {'id': uuid4().hex, 'revision': 1, 'run_schema_version': 2,
                 'parent_run_id': parent_run_id, 'task_key': task_key,
                 'state': 'queued', 'delegation_id': delegation_id,
                 'root_delegation_id': root_delegation_id,
                 'root_max_model_calls': budget['max_model_calls'],
                 'contract_hash': contract_hash, 'route': available['route'],
                 'configuration_fingerprint': available['configuration_fingerprint'],
                 'max_model_calls': run_limit, 'specialists': specialists,
                 'pending': [delegation_id], 'rounds': [], 'lease': None,
                 'unmet': [], 'cost': {'status': 'unknown', 'amount': None}}
        conn.execute('INSERT INTO agent_runs VALUES (?,?,?,?,?,?,?,?,?)',
            (value['id'], self.ctx.project_id, self.ctx.requester_user_id, self.username,
             self.canvas_id, self.conversation_id, delegation_id, 1, packed(value)))
        return value

    def _persist_child_plans(self, conn, value, round_index, children):
        parent_delegation_id = value.get('pending', [None])[0]
        if parent_delegation_id:
            parent_contract = self._contract(conn, parent_delegation_id)
            if (parent_contract.get('delegable_operations') == FORMAL_SCRIPT_DELEGABLE_OPERATIONS
                    and (round_index == 0 or children)):
                self._validate_formal_script_children(value, children)
        for child in children:
            task_key = child['task_key']
            create_request = f"run:{value['id']}:{round_index}:child:{task_key}"
            start_request = f'{create_request}:start'
            require(len(start_request) <= 128, '子任务稳定请求编号过长')
            row = conn.execute('''SELECT * FROM agent_run_children
                WHERE parent_run_id=? AND round_index=? AND task_key=?''',
                (value['id'], round_index, task_key)).fetchone()
            content = deepcopy(child)
            if row is not None:
                require(row['adoption_id'] == child['adoption_id']
                        and row['create_request'] == create_request
                        and row['start_request'] == start_request
                        and json.loads(row['content']) == content,
                        '本回合子任务身份或计划已变化', 'idempotency_conflict')
                continue
            conn.execute('''INSERT INTO agent_run_children
                (parent_run_id,round_index,task_key,adoption_id,state,create_request,start_request,content)
                VALUES (?,?,?,?,?,?,?,?)''',
                (value['id'], round_index, task_key, child['adoption_id'], 'planned',
                 create_request, start_request, packed(content)))

    @staticmethod
    def _validate_formal_script_children(value, children):
        """Keep formal drafting and review in separate, frozen specialist runs."""
        writers = [child for child in children
                   if 'script.document.draft' in child.get('allowed_operations', [])]
        reviewers = [child for child in children
                     if 'script.document.review' in child.get('allowed_operations', [])]
        require(len(writers) == 1 and writers[0]['allowed_operations'] == ['script.document.draft'],
                '正式剧本须交给唯一主笔子任务起草', 'model_response_invalid')
        require(len(reviewers) == 1 and reviewers[0]['allowed_operations'] == ['script.document.review'],
                '正式剧本须交给独立编辑子任务审阅', 'model_response_invalid')
        writer, reviewer = writers[0], reviewers[0]
        require(writer['adoption_id'] != reviewer['adoption_id'],
                '主笔与审阅者必须是不同的固定专业成员', 'model_response_invalid')
        require(writer['task_key'] in reviewer.get('depends_on', [])
                and any(selection.get('source_task_key') == writer['task_key']
                        and selection.get('output_index') == 0
                        for selection in reviewer.get('input_output_selections', [])),
                '编辑审阅必须依赖并引用主笔的固定剧本输出', 'model_response_invalid')
        specialists = {item.get('adoption_id'): item for item in value.get('specialists', [])}
        writer_profile = specialists.get(writer['adoption_id'], {}).get('effective', {})
        reviewer_profile = specialists.get(reviewer['adoption_id'], {}).get('effective', {})
        require('script.document.draft' in writer_profile.get('capabilities', []),
                '固定主笔没有正式剧本起草能力；请升级或替换成员后新建任务',
                'formal_script_specialists_missing')
        require('script.document.review' in reviewer_profile.get('capabilities', []),
                '固定剧本编辑没有独立审阅能力；请升级或替换成员后新建任务',
                'formal_script_specialists_missing')

    def _formal_script_role_policy(self, conn, contract):
        """Mark the root's directly delegated writer/editor runs as execution leaves."""
        parent_id = contract.get('parent_id')
        if not parent_id:
            return None
        parent = self.delegations._get(conn, parent_id)
        root_contract = parent['contract']
        if root_contract.get('preparation_kind') != 'formal_episode_script':
            return None
        require(parent.get('parent_id') is None
                and root_contract.get('parent_id') is None
                and root_contract.get('root_delegation_id') == parent_id
                and root_contract.get('allowed_operations') == ['context.read']
                and root_contract.get('delegable_operations') == FORMAL_SCRIPT_DELEGABLE_OPERATIONS
                and contract.get('root_delegation_id') == parent_id,
                '正式剧本专业子任务不属于可信根委托', 'scope_violation')
        role = {
            ('script.document.draft',): 'writer',
            ('script.document.review',): 'reviewer',
        }.get(tuple(contract.get('allowed_operations', [])))
        require(role is not None, '正式剧本专业子任务权限无效', 'scope_violation')
        operation = contract['allowed_operations'][0]
        return {
            'role': role,
            'operation': operation,
            'instruction': f'Perform {operation} yourself in this run. Do not delegate to another specialist or create child tasks.',
        }

    def _validate_formal_script_role_decision(self, conn, value, contract, decision):
        """Require the fixed formal writer/editor to perform its own leaf operation."""
        role_policy = self._formal_script_role_policy(conn, contract)
        if role_policy is None:
            return
        operation = role_policy['operation']
        require(not decision.get('delegations'),
                '正式剧本主笔和编辑必须直接完成各自职责，不得继续委派', 'model_response_invalid')
        previous_completed = sum(
            action.get('operation') == operation
            for step in value.get('rounds', [])
            for index, action in enumerate(step.get('decision', {}).get('actions', []))
            if index in step.get('actions_done', []))
        actions = decision.get('actions', [])
        expected = [operation] if previous_completed == 0 else []
        require([action.get('operation') for action in actions] == expected,
                '正式剧本专业子任务必须直接提交且只提交一次对应职责动作', 'model_response_invalid')

    @staticmethod
    def _formal_script_roster_ready(specialists):
        """Require distinct frozen adoptions for the formal draft and review roles."""
        if not isinstance(specialists, list):
            return False
        profiles = []
        for item in specialists:
            if not isinstance(item, dict) or not isinstance(item.get('adoption_id'), str):
                continue
            effective = item.get('effective')
            capabilities = effective.get('capabilities', []) if isinstance(effective, dict) else []
            if not isinstance(capabilities, list):
                capabilities = []
            profiles.append((item['adoption_id'], set(capabilities)))
        writers = {identifier for identifier, capabilities in profiles
                   if 'script.document.draft' in capabilities}
        reviewers = {identifier for identifier, capabilities in profiles
                     if 'script.document.review' in capabilities}
        return any(writer != reviewer for writer in writers for reviewer in reviewers)

    def _start_child_dispatch(self, conn, p):
        exact(p, {'parent_run_id', 'owner', 'round_index', 'task_key', 'delegation_id'})
        parent = self._fence(conn, p['parent_run_id'], p['owner'])
        require(parent.get('run_schema_version', 1) >= 2,
                '旧串行运行不能创建新的独立子运行', 'run_not_resumable')
        round_index = integer(p['round_index'], 0)
        task_key = text(p['task_key'], 64)
        row = conn.execute('''SELECT * FROM agent_run_children
            WHERE parent_run_id=? AND round_index=? AND task_key=?''',
            (parent['id'], round_index, task_key)).fetchone()
        require(row is not None, '父运行没有该子任务计划', 'scope_violation')
        plan = json.loads(row['content'])
        require(row['create_request'] == f"run:{parent['id']}:{round_index}:child:{task_key}"
                and row['start_request'] == f"{row['create_request']}:start",
                '子任务稳定请求身份损坏', 'scope_violation')
        if row['child_run_id']:
            require(row['delegation_id'] == p['delegation_id'],
                    '任务键已绑定其他委托', 'idempotency_conflict')
            return {'run': self._public(conn, self._get(conn, row['child_run_id']))}
        require(row['state'] == 'planned' and plan['adoption_id'] == row['adoption_id'],
                '子任务当前不能启动', 'run_fenced')
        parent_step = parent['rounds'][round_index]
        require(parent_step.get('phase') == 'decision_saved'
                and parent_step.get('delegation_id') == parent['pending'][0],
                '子运行必须来自父当前回合', 'run_fenced')
        contract_row = self.delegations._get(conn, p['delegation_id'])
        parent_contract_row = self.delegations._get(conn, parent_step['delegation_id'])
        expected_scope = (self.ctx.project_id, self.ctx.requester_user_id, self.username,
                          self.canvas_id, self.conversation_id)
        require((contract_row['project_id'], contract_row['owner_id'], contract_row['owner_username'],
                 contract_row['canvas_id'], contract_row['conversation_id']) == expected_scope
                and (parent_contract_row['project_id'], parent_contract_row['owner_id'],
                     parent_contract_row['owner_username'], parent_contract_row['canvas_id'],
                     parent_contract_row['conversation_id']) == expected_scope,
                '父子委托不属于同一项目画布会话和用户', 'scope_violation')
        require(contract_row['parent_id'] == parent_step['delegation_id']
                and contract_row['contract']['agent'].get('adoption_id') == plan['adoption_id']
                and self._root_delegation_id(conn, p['delegation_id']) == parent['root_delegation_id'],
                '子委托不属于当前父授权链或所选专业Agent', 'scope_violation')
        require(any(item.get('adoption_id') == plan['adoption_id'] for item in parent['specialists']),
                '专业Agent不在父运行冻结名单中', 'capability_denied')
        conn.execute('''UPDATE agent_run_children SET delegation_id=? WHERE parent_run_id=?
            AND round_index=? AND task_key=? AND child_run_id IS NULL''',
            (p['delegation_id'], parent['id'], round_index, task_key))
        child = self._create_run(conn, delegation_id=p['delegation_id'],
            contract_hash=digest(contract_row['contract']), max_model_calls=parent['max_model_calls'],
            specialist_ids=[item['adoption_id'] for item in parent['specialists']],
            inherited_specialists=parent['specialists'],
            expected_route={'route': parent['route'],
                            'configuration_fingerprint': parent['configuration_fingerprint']},
            parent_run_id=parent['id'], task_key=task_key)
        conn.execute('''UPDATE agent_run_children SET state='started',child_run_id=?,content=?
            WHERE parent_run_id=? AND round_index=? AND task_key=?''',
            (child['id'], packed({**plan, 'start_request': row['start_request']}),
             parent['id'], round_index, task_key))
        return {'run': self._public(conn, child)}

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
        model_send_admitted = False
        if action == 'start':
            value = self._create_run(conn, delegation_id=p['delegation_id'],
                contract_hash=p['contract_hash'], max_model_calls=p['max_model_calls'],
                specialist_ids=p['specialist_ids'])
            return {'run': self._public(conn, value)}

        if action == 'start_pinned':
            exact(p, {'delegation_id', 'contract_hash', 'max_model_calls', 'specialists'})
            specialists = p['specialists']
            require(isinstance(specialists, list), '专业Agent冻结名单无效')
            specialist_ids = [item['adoption_id'] for item in specialists]
            value = self._create_run(conn, delegation_id=p['delegation_id'],
                contract_hash=p['contract_hash'], max_model_calls=p['max_model_calls'],
                specialist_ids=specialist_ids, specialist_versions=specialists)
            return {'run': self._public(conn, value)}

        if action == 'start_continuation':
            exact(p, {'delegation_id', 'contract_hash', 'task_id', 'continuation_id',
                      'parent_run_id', 'source_operation_id', 'source_request_id'})
            require(table(conn, 'creation_collaboration_task_continuations')
                    and table(conn, 'creation_collaboration_tasks'),
                    '协作任务续跑授权记录不存在', 'scope_violation')
            continuation = conn.execute('''SELECT c.*,t.owner_id AS task_owner_id,
                    t.project_id AS task_project_id,t.canvas_id AS task_canvas_id,
                    t.conversation_id AS task_conversation_id,t.run_id AS task_run_id,
                    t.delegation_id AS task_delegation_id
                FROM creation_collaboration_task_continuations c
                JOIN creation_collaboration_tasks t ON t.id=c.task_id
                WHERE c.id=? AND c.task_id=?''',
                (p['continuation_id'], p['task_id'])).fetchone()
            require(continuation is not None
                    and continuation['continuation_delegation_id'] == p['delegation_id']
                    and continuation['parent_run_id'] == p['parent_run_id']
                    and continuation['budget_root_delegation_id'] == continuation['task_delegation_id']
                    and continuation['task_run_id'] == p['parent_run_id']
                    and continuation['source_operation_id'] == p['source_operation_id']
                    and continuation['source_request_id'] == p['source_request_id'],
                    '续跑请求与已授权任务候选不匹配', 'scope_violation')
            require((continuation['task_owner_id'], continuation['task_project_id'],
                     continuation['task_canvas_id'], continuation['task_conversation_id']) ==
                    (str(self.ctx.requester_user_id), str(self.ctx.project_id), self.canvas_id,
                     self.conversation_id)
                    and (continuation['owner_id'], continuation['project_id'],
                         continuation['canvas_id'], continuation['conversation_id']) ==
                    (str(self.ctx.requester_user_id), str(self.ctx.project_id), self.canvas_id,
                     self.conversation_id),
                    '续跑候选不属于当前用户、项目或会话', 'scope_violation')
            parent = self._get(conn, p['parent_run_id'])
            self._assert_ancestors_not_stopped(conn, parent)
            require(parent['delegation_id'] == continuation['budget_root_delegation_id']
                    and parent.get('parent_run_id') is None
                    and parent.get('root_delegation_id') == continuation['budget_root_delegation_id'],
                    '续跑必须挂接到原协作任务根运行', 'scope_violation')
            contract_row = self.delegations._get(conn, p['delegation_id'])
            contract = contract_row['contract']
            require(contract_row['parent_id'] is None
                    and digest(contract) == text(p['contract_hash'], 128)
                    and self._root_delegation_id(conn, p['delegation_id']) == parent['root_delegation_id'],
                    '续跑委托或共享模型预算锚点不匹配', 'scope_violation')
            specialists = parent['specialists']
            value = self._create_run(conn, delegation_id=p['delegation_id'],
                contract_hash=digest(contract), max_model_calls=parent['max_model_calls'],
                specialist_ids=[item['adoption_id'] for item in specialists],
                inherited_specialists=specialists,
                expected_route={'route': parent['route'],
                                'configuration_fingerprint': parent['configuration_fingerprint']},
                parent_run_id=parent['id'], task_key=continuation['id'])
            return {'run': self._public(conn, value)}

        if action == 'start_child':
            return self._start_child_dispatch(conn, p)

        value = self._get(conn, p['id'])
        if action in {'stop', 'resume'}:
            require(value['revision'] == integer(p['expected_revision'], 1), '运行状态已变化', 'revision_conflict')
            if action == 'stop':
                if value['state'] in {'delivered', 'partial', 'stopped', 'submission_unknown'}:
                    return {'run': self._public(conn, value)}
                if value['rounds'] and value['rounds'][-1]['phase'] == 'model_inflight':
                    step = value['rounds'][-1]
                    response_received = bool(step.get('response_received'))
                    step['phase'] = 'invalid_response' if response_received else 'submission_unknown'
                    self._set_model_attempt_state(conn, step.get('attempt_id'),
                                                  'response_received' if response_received else 'unknown')
                value.update(state='stopped', lease=None, provider_cancelled=False)
            else:
                if (value['state'] == 'blocked' and value.get('error', {}).get('code') ==
                        'formal_script_specialists_missing'
                        and value.get('rounds')
                        and value['rounds'][-1].get('phase') == 'invalid_response'):
                    raise OperationError('formal_script_specialists_missing',
                        '冻结主笔或剧本编辑缺少正式剧本能力；升级或替换成员后请新建任务')
                self._assert_ancestors_not_stopped(conn, value)
                root_id = self._root_delegation_id(conn, value['delegation_id'])
                budget = self._ensure_root_model_budget(conn, root_id)
                value.setdefault('root_delegation_id', root_id)
                value.setdefault('root_max_model_calls', budget['max_model_calls'])
                require(value['state'] in {'stopped', 'blocked', 'paused', 'queued', 'applying', 'model_inflight',
                                           'waiting_children'}, '此状态不能安全继续', 'run_not_resumable')
                require(not value.get('lease') or value['lease']['expires'] <= time.time(), '运行正在执行', 'run_busy')
                if value['rounds'] and value['rounds'][-1]['phase'] == 'model_inflight':
                    step = value['rounds'][-1]
                    response_received = bool(step.get('response_received'))
                    if not response_received:
                        step['phase'] = 'submission_unknown'
                        self._set_model_attempt_state(conn, step.get('attempt_id'), 'unknown')
                        value.update(state='submission_unknown', lease=None)
                        value['revision'] += 1
                        return self._save(conn, value)
                    step['phase'] = 'invalid_response'
                    self._set_model_attempt_state(conn, step.get('attempt_id'), 'response_received')
                    value.update(state='blocked', lease=None,
                                 error={'code': 'model_response_processing_interrupted',
                                        'message': '模型已返回，但运行处理响应时中断；未自动重发'})
                    if len(value['rounds']) >= value['max_model_calls']:
                        value['revision'] += 1
                        return self._save(conn, value)
                require(not any(s['phase'] in {'model_inflight', 'submission_unknown'} for s in value['rounds']),
                        '模型提交结果未知，不能重发付费请求', 'model_submission_unknown')
                require(bool(value['rounds'] and value['rounds'][-1]['phase'] == 'decision_saved')
                        or len(value['rounds']) < value['max_model_calls'], '已达到本次调用上限', 'call_limit_reached')
                has_pending_node_action = self._validate_resume_node_action(conn, value)
                if not has_pending_node_action:
                    self._validate_current(conn, value['pending'][0] if value['pending'] else value['delegation_id'])
                value.update(state='waiting_children' if value['state'] == 'waiting_children' else 'queued', lease=None)
                value.pop('error', None)
        elif action == 'claim':
            self._assert_ancestors_not_stopped(conn, value)
            if value['state'] in TERMINAL:
                return {'run': self._public(conn, value)}
            root_id = self._root_delegation_id(conn, value['delegation_id'])
            budget = self._ensure_root_model_budget(conn, root_id)
            value.setdefault('root_delegation_id', root_id)
            value.setdefault('root_max_model_calls', budget['max_model_calls'])
            require(not value.get('lease') or value['lease']['expires'] <= time.time(), '运行正在执行', 'run_busy')
            if value['rounds'] and value['rounds'][-1]['phase'] == 'model_inflight':
                step = value['rounds'][-1]
                response_received = bool(step.get('response_received'))
                step['phase'] = 'invalid_response' if response_received else 'submission_unknown'
                self._set_model_attempt_state(conn, step.get('attempt_id'),
                                              'response_received' if response_received else 'unknown')
                if response_received:
                    value.update(state='blocked', lease=None,
                                 error={'code': 'model_response_processing_interrupted',
                                        'message': '模型已返回，但运行处理响应时中断；未自动重发'})
                else:
                    value.update(state='submission_unknown', lease=None)
            else:
                value['lease'] = {'owner': p['owner'], 'expires': time.time() + LEASE_SECONDS}
                if value['state'] != 'waiting_children':
                    value['state'] = 'applying' if value['rounds'] and value['rounds'][-1]['phase'] == 'decision_saved' else 'queued'
        else:
            value = self._fence(conn, p['id'], p['owner'])
            if action == 'heartbeat':
                value['lease']['expires'] = time.time() + LEASE_SECONDS
            elif action == 'admit_model':
                step = value['rounds'][-1]
                require(value['state'] == 'model_inflight', '模型回合已被认领', 'run_fenced')
                self._validate_current(conn, step['delegation_id'])
                root_id = self._root_delegation_id(conn, step['delegation_id'])
                require(root_id == value.get('root_delegation_id', root_id),
                        '运行模型预算根身份已变化', 'scope_violation')
                if step.get('send_admitted'):
                    # The attempt was already admitted, so retain/account it
                    # but never authorize a second provider send for it.
                    self._reserve_model_attempt(conn, value, step, root_id)
                else:
                    model_send_admitted = self._reserve_model_attempt(conn, value, step, root_id)
                    step['send_admitted'] = True
            elif action == 'model_budget_exhausted':
                step = value['rounds'][-1]
                require(value['state'] == 'model_inflight' and not step.get('send_admitted'),
                        '已接纳的模型请求不能标记为未发送', 'run_fenced')
                root_id = self._root_delegation_id(conn, step['delegation_id'])
                budget = self._ensure_root_model_budget(conn, root_id)
                require(budget['used_model_calls'] >= budget['max_model_calls'],
                        '根模型调用额度仍有剩余', 'run_fenced')
                step['phase'] = 'budget_exhausted'
                value.update(state='partial', lease=None)
                value.setdefault('unmet', []).append('根模型调用额度已用完，当前回合未发送')
            elif action == 'prepare':
                require(len(value['rounds']) < value['max_model_calls'] and bool(value['pending']), '模型调用额度已用完', 'call_limit_reached')
                root_id = self._root_delegation_id(conn, value['delegation_id'])
                budget = self._ensure_root_model_budget(conn, root_id)
                require(root_id == value.get('root_delegation_id', root_id),
                        '运行模型预算根身份已变化', 'scope_violation')
                if budget['used_model_calls'] >= budget['max_model_calls']:
                    value.update(state='partial', lease=None)
                    value.setdefault('unmet', []).append('根模型调用额度已用完，仍有待处理事项')
                else:
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
                    authorization = contract.get('execution_authorization')
                    payload_contract = _payload_contract(authorization)
                    delegation_policy = None
                    if contract.get('delegable_operations') == FORMAL_SCRIPT_DELEGABLE_OPERATIONS:
                        empty_parent_inputs = not contract.get('inputs')
                        parent_input_rule = (
                            'This parent has an empty input list, so both writer and reviewer must use input_indices=[].'
                            if empty_parent_inputs else
                            'If a child needs parent inputs, input_indices may select only indexes from this frozen list.')
                        delegation_policy = {
                            'delegable_operations': FORMAL_SCRIPT_DELEGABLE_OPERATIONS,
                            'instruction': (
                                'You are coordinating a formal screenplay task, not its author or reviewer. '
                                'Do not write the episode yourself. Delegate exactly one script.document.draft task '
                                'to a dedicated writer pinned specialist whose frozen profile has that capability, '
                                'and delegate '
                                'script.document.review to a different pinned editor whose frozen profile has the '
                                'review capability. Make the review depend on and select output 0 from the writer. '
                                'ChildDecision.input_indices contains only zero-based indexes into this parent run’s frozen '
                                'inputs; it never selects a child output. Do not put writer output 0 in input_indices. '
                                f'{parent_input_rule} '
                                'For the reviewer, use the writer task_key in both depends_on and source_task_key, with '
                                'output_index=0 in input_output_selections. If you choose another writer task_key, use that '
                                'same key consistently in both reviewer fields. '
                                'The supplied child fragments show reference fields only; fill all other required fields '
                                'using actual pinned specialist adoption_ids and this frozen scope. '
                                'Do not combine writer and reviewer roles in one child. The child operations listed '
                                'here are delegation-only; they are not available to your own run.'),
                            'input_selection_rule': (
                                'ChildDecision.input_indices are zero-based indexes into only parent frozen inputs. '
                                f'They cannot reference child outputs; {parent_input_rule} '
                                'Use depends_on and input_output_selections for a sibling output.'),
                            'reviewer_output_reference_example': {
                                'writer_child_field_fragment': {
                                    'task_key': 'draft_episode_1',
                                    'input_indices': [],
                                },
                                'reviewer_child_field_fragment': {
                                    'task_key': 'review_episode_1',
                                    'input_indices': [],
                                    'depends_on': ['draft_episode_1'],
                                    'input_output_selections': [
                                        {'source_task_key': 'draft_episode_1', 'output_index': 0}],
                                },
                            },
                        }
                    formal_script_role_policy = self._formal_script_role_policy(conn, contract)
                    prompt_value = {'goal': contract['goal'], 'agent': contract['agent']['effective'],
                        'task_brief': contract.get('task_brief'),
                        'nodes': nodes, 'inputs': contract['inputs'],
                        'context': contract['context'], 'acceptance': contract['acceptance'],
                        'allowed_operations': contract['allowed_operations'], 'specialists': value['specialists'],
                        'delegation_policy': delegation_policy,
                        'operation_semantics': self._operation_semantics(),
                        'node_execution': {'authorization': authorization,
                            'root_max_node_runs': contract.get('root_max_node_runs', 0),
                            'budget_chain': contract.get('node_run_budget_chain', []),
                            'read_node_ids': contract.get('read_node_ids', []), 'targets': node_context},
                        'outputs': self._model_outputs(conn, value),
                        'output_selection_contract': (
                            'ChildDecision.input_indices 只索引当前父委托冻结 inputs；兄弟子任务输出不是父input。'
                            '用 depends_on 声明 writer task_key，并用 input_output_selections 中的 '
                            '{source_task_key: writer_task_key, output_index: 0} 选择该 writer 的首个已回执输出；'
                            '不得提交对象 ID。'),
                        'remaining_model_calls': max(0,
                            budget['max_model_calls'] - budget['used_model_calls']),
                        'payload_contract': payload_contract}
                    if formal_script_role_policy is not None:
                        prompt_value['formal_script_role_policy'] = formal_script_role_policy
                    frozen_skill_context = task_skill_context(contract)
                    prompt_skill_context, skill_context_receipt = _task_skill_prompt_context(
                        contract, frozen_skill_context)
                    if prompt_skill_context['skills']:
                        prompt_value['task_skill_context'] = prompt_skill_context
                    prompt = packed(prompt_value)
                    require(len(prompt.encode()) <= 250_000, '冻结上下文过大，请缩小范围', 'context_too_large')
                    value['rounds'].append({'delegation_id': value['pending'][0], 'attempt_id': uuid4().hex,
                        'phase': 'model_inflight', 'prompt': prompt, 'node_context': node_context,
                        'skill_context': skill_context_receipt,
                        'send_admitted': False, 'actions_done': [], 'actions_admitted': [], 'children_done': []})
                    value['state'] = 'model_inflight'
            elif action == 'model_response_received':
                step = value['rounds'][-1]
                require(value['state'] == 'model_inflight' and step['phase'] == 'model_inflight'
                        and step.get('attempt_id') == p['attempt_id'],
                        '模型响应不属于当前回合', 'run_fenced')
                usage = p.get('usage', {})
                require(isinstance(usage, dict) and len(packed(usage).encode()) <= 16_384,
                        '模型用量回执无效', 'invalid_input')
                self._set_model_attempt_state(conn, step.get('attempt_id'), 'response_received')
                step.update(response_received=True, usage=usage)
            elif action == 'decision':
                decision = agent_model.Decision.model_validate(p['result']['decision']).model_dump(mode='json')
                require(len(packed(decision).encode()) <= 250_000, '模型输出过大')
                step = value['rounds'][-1]
                require(step['phase'] == 'model_inflight', '模型回合已完成', 'run_fenced')
                frozen_contract = self._contract(conn, step['delegation_id'])
                index_issues = _decision_index_issues(decision, frozen_contract)
                if index_issues:
                    raise OperationError(
                        'model_response_invalid', '模型返回了超出冻结委托范围的索引',
                        response_received=True,
                        usage=p['result'].get('usage', {}),
                        diagnostic={
                            'stage': 'decision_payload',
                            'code': 'decision_contract_invalid',
                            'issues': index_issues,
                        },
                    )
                self._validate_formal_script_role_decision(conn, value, frozen_contract, decision)
                self._set_model_attempt_state(conn, step.get('attempt_id'), 'response_received')
                step.update(phase='decision_saved', decision=decision, usage=p['result'].get('usage', {}))
                if value.get('run_schema_version', 1) >= 2:
                    self._persist_child_plans(conn, value, len(value['rounds']) - 1,
                                              decision['delegations'])
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
            elif action == 'wait_children':
                require(value.get('run_schema_version', 1) >= 2,
                        '旧串行运行不使用独立子任务等待状态', 'run_fenced')
                rows = self._child_rows(conn, value['id'], len(value['rounds']) - 1)
                require(bool(rows), '当前回合没有持久子任务', 'run_fenced')
                value.update(state='waiting_children', lease=None)
            elif action == 'child_failed':
                round_index = integer(p['round_index'], 0)
                task_key = text(p['task_key'], 64)
                state = p['state']
                require(state in {'blocked', 'unsatisfied'}, '子任务失败状态无效', 'run_fenced')
                row = conn.execute('''SELECT content FROM agent_run_children WHERE parent_run_id=?
                    AND round_index=? AND task_key=?''', (value['id'], round_index, task_key)).fetchone()
                require(row is not None, '子任务计划不存在', 'run_fenced')
                content = json.loads(row['content'])
                content.update(state=state, unmet=list(p.get('unmet', [])), error=deepcopy(p.get('error')))
                conn.execute('''UPDATE agent_run_children SET state=?,content=? WHERE parent_run_id=?
                    AND round_index=? AND task_key=?''',
                    (state, packed(content), value['id'], round_index, task_key))
            elif action == 'finish_round':
                step = value['rounds'][-1]
                if value.get('run_schema_version', 1) >= 2:
                    rows = self._child_rows(conn, value['id'], len(value['rounds']) - 1)
                    outstanding = [row for row in rows
                        if row['state'] == 'planned' or row['state'] == 'started' and row['child_run_id']
                        and self._get(conn, row['child_run_id'])['state'] not in
                            (TERMINAL | {'blocked'})]
                    require(not outstanding, '必需子任务仍在运行或等待依赖', 'run_fenced')
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
                child_task_unmet = {}
                unlinked_child_unmet = []
                child_has_verified_output = False
                if value.get('run_schema_version', 1) >= 2:
                    for child_row in self._child_rows(conn, value['id']):
                        task_identity = f"{child_row['round_index']}:{child_row['task_key']}"
                        if child_row['state'] in {'blocked', 'unsatisfied'}:
                            content = json.loads(child_row['content'])
                            reasons = list(content.get('unmet', [])) or [
                                f"子任务 {task_identity} 未能启动（{child_row['state']}）"]
                            child_task_unmet[task_identity] = reasons
                            if child_row['delegation_id']:
                                value['unmet_by_delegation'][child_row['delegation_id']] = reasons
                            else:
                                unlinked_child_unmet.extend(reasons)
                            continue
                        child_run = self._get(conn, child_row['child_run_id']) if child_row['child_run_id'] else None
                        if child_run is None:
                            reasons = [f'子任务 {task_identity} 没有独立运行回执']
                            child_task_unmet[task_identity] = reasons
                            unlinked_child_unmet.extend(reasons)
                            continue
                        verified = (child_run['state'] == 'delivered'
                                    and self._has_verified_output(conn, child_run))
                        child_has_verified_output = child_has_verified_output or verified
                        if not verified:
                            reasons = list(child_run.get('unmet', []))
                            if not reasons:
                                reasons = [f"子任务 {task_identity} 未形成合格结果（{child_run['state']}）"]
                            child_task_unmet[task_identity] = reasons
                            value['unmet_by_delegation'][child_row['delegation_id']] = reasons
                value['unmet_by_task'] = child_task_unmet
                # Node execution obligations survive later model rounds until
                # the same durable request has a verified result. A later
                # decision's `finished` flag cannot erase them.
                value['unmet'] = [item for remaining in value['unmet_by_delegation'].values()
                                  for item in remaining]
                value['unmet'].extend(unlinked_child_unmet)
                value['pending'].pop(0)
                if not decision['finished'] and decision['actions']:
                    value['pending'].append(step['delegation_id'])
                if not value['pending'] or len(value['rounds']) >= value['max_model_calls']:
                    has_output = self._has_verified_output(conn, value) or child_has_verified_output
                    value['state'] = 'partial' if value['pending'] or value['unmet'] or not has_output else 'delivered'
                    if value['pending'] and not value['unmet']:
                        value['unmet'] = ['本次模型调用次数已用完，仍有待处理事项']
                    value['lease'] = None
                else:
                    value['state'] = 'queued'
            elif action == 'failure':
                value['error'] = p['error']
                if value['rounds'] and value['rounds'][-1]['phase'] == 'model_inflight':
                    received = p['error'].get('response_received', False)
                    self._set_model_attempt_state(conn, value['rounds'][-1].get('attempt_id'),
                                                  'response_received' if received else 'unknown')
                    step = value['rounds'][-1]
                    step.update(phase='invalid_response' if received else 'submission_unknown',
                                usage=p['error'].get('usage') or step.get('usage', {}))
                    if received:
                        value['rounds'][-1]['response_received'] = True
                    value['state'] = 'blocked' if received else 'submission_unknown'
                else:
                    value['state'] = 'blocked'
                value['lease'] = None
            else:
                require(False, '未知可信宿主状态转换')
        value['revision'] += 1
        result = self._save(conn, value)
        if action == 'admit_model':
            result['model_send_admitted'] = model_send_admitted
        return result

    async def _model(self, value, owner):
        from novelvideo.text_task_runtime.models import AgentTaskRouteSnapshot
        step = value['rounds'][-1]
        # Durable send admission is the stop/send linearization point. No network
        # runs under this lock; a later stop can only fence the admitted request.
        try:
            admission = await self._send('admit_model', {'id': value['id'], 'owner': owner})
        except OperationError as exc:
            if exc.code == 'call_limit_reached':
                await self._send('model_budget_exhausted', {'id': value['id'], 'owner': owner})
            raise
        if not admission.get('model_send_admitted'):
            raise OperationError('model_submission_unknown',
                                 '此模型 attempt 已接纳或结果未知，不能再次发送')
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
                self._assert_ancestors_not_stopped(conn, value)
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

    def _child_task_state(self, conn, row):
        if row['state'] in {'blocked', 'unsatisfied'}:
            return row['state']
        if not row['child_run_id']:
            return row['state']
        return self._get(conn, row['child_run_id'])['state']

    def _read_child_rows(self, parent_run_id, round_index=None):
        with self.read() as conn:
            return self._child_rows(conn, parent_run_id, round_index)

    def _read_child_row(self, parent_run_id, round_index, task_key):
        with self.read() as conn:
            row = conn.execute('''SELECT * FROM agent_run_children WHERE parent_run_id=?
                AND round_index=? AND task_key=?''',
                (parent_run_id, round_index, task_key)).fetchone() if table(conn, 'agent_run_children') else None
            return dict(row) if row else None

    def _read_child_task_state(self, row):
        with self.read() as conn:
            return self._child_task_state(conn, row)

    def _read_run_output_ref(self, parent_run_id, round_index, task_key, selection):
        with self.read() as conn:
            return self._run_output_ref(conn, parent_run_id, round_index, task_key, selection)

    def _read_child_has_verified_output(self, row):
        with self.read() as conn:
            if not row.get('child_run_id'):
                return False
            child = self._get(conn, row['child_run_id'])
            return child['state'] == 'delivered' and self._has_verified_output(conn, child)

    def _read_drive_progress_signature(self, identifier):
        """Return durable work state without revision/heartbeat churn."""
        with self.read() as conn:
            value = self._get(conn, identifier)
            rounds = tuple((step.get('phase'), step.get('send_admitted'),
                tuple(step.get('actions_admitted', [])), tuple(step.get('actions_done', [])),
                tuple(step.get('children_done', []))) for step in value.get('rounds', []))
            children = tuple((row['round_index'], row['task_key'], row['state'], row['child_run_id'],
                self._child_task_state(conn, row)) for row in self._child_rows(conn, identifier))
            return value['state'], rounds, children

    async def _drive_child_round(self, identifier, owner, round_index, step):
        """Start ready children independently, then release the parent lease while they run."""
        value = await registration_io(self._read_value, identifier, raw=True)
        rows = await registration_io(self._read_child_rows, identifier, round_index)
        if not rows:
            return False

        for row in rows:
            if row['state'] != 'planned':
                continue
            plan = json.loads(row['content'])
            dependency_rows = []
            dependency_pending = False
            dependency_failed = None
            for dependency_key in plan.get('depends_on', []):
                dep = await registration_io(self._read_child_row,
                    identifier, round_index, dependency_key)
                if dep is None:
                    dependency_failed = f'依赖任务 {dependency_key} 不存在'
                    break
                dep_state = await registration_io(self._read_child_task_state, dep)
                if dep['state'] == 'started' and not dep['child_run_id']:
                    dependency_failed = f'依赖任务 {dependency_key} 没有独立运行回执'
                    break
                if dep_state == 'stopped':
                    # Stop is recoverable through an explicit child resume.
                    # Keep this task planned so a later parent resume can
                    # re-evaluate the prerequisite rather than freezing it as
                    # permanently unsatisfied.
                    dependency_pending = True
                    continue
                if dep_state in {'planned', 'started', 'queued', 'applying', 'model_inflight',
                                 'waiting_children'}:
                    dependency_pending = True
                    continue
                if dep_state != 'delivered':
                    dependency_failed = f'依赖任务 {dependency_key} 未交付（{dep_state}）'
                    break
                if not await registration_io(self._read_child_has_verified_output, dep):
                    dependency_failed = f'依赖任务 {dependency_key} 未产生可验证输出'
                    break
                dependency_rows.append(dep)
            if dependency_failed:
                await self._send('child_failed', {'id': identifier, 'owner': owner,
                    'round_index': round_index, 'task_key': row['task_key'], 'state': 'unsatisfied',
                    'unmet': [dependency_failed], 'error': {'code': 'dependency_unavailable'}})
                continue
            if dependency_pending:
                continue
            try:
                specialist = next((agent for agent in value['specialists']
                                   if agent['adoption_id'] == plan['adoption_id']), None)
                require(specialist is not None, '专业Agent不在本次明确选择中', 'capability_denied')
                parent_contract = self.delegations.get(step['delegation_id'])['contract']
                AgentTools._indices(plan['input_indices'], len(parent_contract['inputs']))
                scope = ({'kind': 'canvas'} if plan.get('scope_kind') == 'canvas'
                         else {'kind': 'nodes', 'node_ids': plan['target_node_ids']})
                input_refs = [parent_contract['inputs'][index]['ref']
                              for index in plan['input_indices']]
                for selection in plan.get('input_output_selections', []):
                    ref = await registration_io(self._read_run_output_ref,
                        identifier, round_index, row['task_key'], selection)
                    input_refs.append(ref)
                body = {'canvas_id': self.canvas_id, 'conversation_id': self.conversation_id,
                    'parent_id': step['delegation_id'], 'scope': scope,
                    'agent': {'kind': 'adoption', 'adoption_id': specialist['adoption_id'],
                              'revision': specialist['revision']},
                    'input_refs': input_refs, 'allowed_operations': plan['allowed_operations'],
                    'goal': plan['goal'], 'acceptance': plan['acceptance']}
                if parent_contract.get('task_brief') is not None:
                    body['task_brief'] = parent_contract['task_brief']
                if plan.get('execution_authorization') is not None:
                    body['execution_authorization'] = deepcopy(plan['execution_authorization'])
                children = _RunDelegations(self, identifier, owner, step['delegation_id'],
                    round_index=round_index, task_key=row['task_key'],
                    depends_on=plan.get('depends_on', []))
                previous = children.receipt(row['create_request'])
                if previous is None:
                    preview = await registration_io(children.preview, body)
                    previous = await children.apply({'request_id': row['create_request'],
                        'action': 'delegation.create',
                        'payload': {**body, 'preview_proof': preview['proof']}})
                await self._send('start_child', {'parent_run_id': identifier, 'owner': owner,
                    'round_index': round_index, 'task_key': row['task_key'],
                    'delegation_id': previous['delegation_id']}, request_id=row['start_request'])
            except OperationError as exc:
                if exc.code in {'run_busy', 'run_fenced'}:
                    raise
                await self._send('child_failed', {'id': identifier, 'owner': owner,
                    'round_index': round_index, 'task_key': row['task_key'], 'state': 'blocked',
                    'unmet': [f"子任务 {row['task_key']} 启动失败（{exc.code}）"],
                    'error': {'code': exc.code}})

        status = await registration_io(self._read_value, identifier, raw=True)
        status_rows = await registration_io(self._read_child_rows, identifier, round_index)
        active_ids, outstanding, recoverable_stopped = [], False, False
        for row in status_rows:
            child_state = await registration_io(self._read_child_task_state, row)
            recoverable_stopped = recoverable_stopped or child_state == 'stopped'
            if child_state == 'planned' or child_state in {
                    'queued', 'applying', 'model_inflight', 'waiting_children', 'stopped'}:
                outstanding = True
            if row['child_run_id'] and child_state in {
                    'queued', 'applying', 'model_inflight', 'waiting_children'}:
                active_ids.append(row['child_run_id'])
        if not outstanding:
            return False
        if not active_ids:
            if recoverable_stopped:
                # There is recoverable work, but resuming it must be an
                # explicit child control action. Release the parent lease and
                # leave it waiting for a later explicit parent drive.
                await self._send('wait_children', {'id': identifier, 'owner': owner})
                return None
            # Every row that could advance was already classified. A planned
            # dependent with no live prerequisite must become an unmet task,
            # not keep reclaiming an idle parent run forever.
            for row in status_rows:
                if row['state'] == 'planned':
                    await self._send('child_failed', {'id': identifier, 'owner': owner,
                        'round_index': round_index, 'task_key': row['task_key'],
                        'state': 'unsatisfied',
                        'unmet': [f"依赖任务未能推进，子任务 {row['task_key']} 未启动"],
                        'error': {'code': 'dependency_unavailable'}})
            return False
        await self._send('wait_children', {'id': identifier, 'owner': owner})
        before = {child_id: await registration_io(self._read_drive_progress_signature, child_id)
                  for child_id in active_ids}
        await asyncio.gather(*(self.drive(child_id) for child_id in active_ids))
        after = {child_id: await registration_io(self._read_drive_progress_signature, child_id)
                 for child_id in active_ids}
        if all(before[child_id] == after[child_id] for child_id in active_ids):
            # A nested child may itself be waiting on a stopped prerequisite,
            # or another worker may still own it. Do not reclaim and spin when
            # durable work state did not change; a later explicit drive can
            # reconcile once the child or grandchild has advanced.
            return None
        try:
            claimed = await self._send('claim', {'id': identifier, 'owner': owner})
        except OperationError as exc:
            if exc.code == 'run_busy':
                return None
            raise
        if claimed['run']['state'] in TERMINAL:
            return None
        return True

    async def drive(self, identifier):
        owner = uuid4().hex
        value = None
        model_response_received = False
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
                    if value['state'] in TERMINAL:
                        return
                    checkpoint('before_model')
                    result = await self._model(value, owner)
                    model_response_received = True
                    await self._send('model_response_received', {
                        'id': identifier, 'owner': owner,
                        'attempt_id': value['rounds'][-1]['attempt_id'],
                        'usage': result.get('usage', {}) if isinstance(result, dict) else {},
                    })
                    checkpoint('after_model')
                    await self._send('decision', {'id': identifier, 'owner': owner, 'result': result})
                    model_response_received = False
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
                if value.get('run_schema_version', 1) >= 2:
                    result = await self._drive_child_round(identifier, owner, index, step)
                    if result is None:
                        return
                    if result:
                        continue
                else:
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
                        if parent.get('task_brief') is not None:
                            body['task_brief'] = parent['task_brief']
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
            try:
                failure_run = await registration_io(self._read_value, identifier, raw=True)
            except Exception:
                failure_run = value
            error = _drive_failure_error(exc, failure_run, response_received=model_response_received)
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
                         node_context=step.get('node_context', {}),
                         run_output_resolver=lambda conn, ref: runs._run_output_input_for_consumer(
                             conn, delegation_id, ref))
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
    def __init__(self, runs, identifier, owner, parent_id, *, round_index=None,
                 task_key=None, depends_on=None):
        super().__init__(runs.ctx, username=runs.username, authority='agent', parent_id=parent_id)
        self.runs, self.identifier, self.owner = runs, identifier, owner
        self.round_index, self.task_key = round_index, task_key
        self.depends_on = list(depends_on or [])

    def _run_output_input(self, conn, ref, *, parent_id):
        require(self.round_index is not None and self.task_key is not None
                and parent_id == self.parent_id,
                '运行输出引用缺少当前父任务上下文', 'scope_violation')
        return self.runs._resolve_run_output_input(conn, ref, parent_run_id=self.identifier,
            round_index=self.round_index, task_key=self.task_key, depends_on=self.depends_on,
            authorization_delegation_id=parent_id)

    async def apply(self, body):
        async with asset_project_lock(self.ctx.state_dir):
            await self.runs._fresh('editor')
            return await super().apply(body)

    def dispatch(self, conn, request, operation_id):
        self.runs._fence(conn, self.identifier, self.owner)
        return super().dispatch(conn, request, operation_id)
