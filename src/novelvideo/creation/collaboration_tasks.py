"""Durable task cards bridging creation discussions to bounded AgentRuns."""

from datetime import datetime, timezone
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3

from fastapi import HTTPException

from . import agent_context, agent_model
from .agent_runs import AgentRuns, _pending_admitted_node_run
from .collaboration_store import CollaborationStore
from .delegations import Delegations
from .library_references import digest
from .ontology import DomainCommand, exact, integer, require, text
from .operations import OperationError


PREPARATION_OPERATIONS = frozenset({
    'context.read', 'proposal.create', 'ontology.definition.draft', 'media.propose',
    'script.document.draft', 'script.document.review',
})
ASSET_IMAGE_REFERENCE_NODE_TYPES = frozenset({
    'exportImageNode', 'imageGenNode', 'imageNode', 'narrativeGroupStoryboardNode',
    'storyboardGenNode', 'uploadNode',
})
MAX_TASK_MODEL_CALLS = 8
PREPARED_CANDIDATE_EFFECT = {
    'operation': 'node.run', 'node_type': 'imageGenNode',
    'node_operation': 'generate', 'max_node_runs': 1,
}


def _packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _task_identifier(owner_id, project_id, canvas_id, conversation_id, request_id):
    source = '\0'.join(map(str, (owner_id, project_id, canvas_id, conversation_id, request_id)))
    return 'task-' + hashlib.sha256(source.encode('utf-8')).hexdigest()[:32]


def _scope_fingerprint(value):
    if not isinstance(value, dict):
        return None
    keys = ('scope', 'node_ids', 'nodes', 'membership')
    return digest({key: value.get(key) for key in keys})


def _validated_plan_dag(tasks):
    """Validate and return a stable topological order for a small review plan."""
    require(isinstance(tasks, list) and 1 <= len(tasks) <= 16
            and all(isinstance(task, dict) for task in tasks),
            '计划必须包含 1 到 16 个任务', 'invalid_input')
    keys = [task.get('task_key') for task in tasks]
    require(all(isinstance(key, str) and 1 <= len(key) <= 64
                and all(char.isalnum() or char in '_-' for char in key) for key in keys)
            and len(set(keys)) == len(keys), '任务编号无效或重复', 'invalid_input')
    by_key = dict(zip(keys, tasks))
    for task in tasks:
        dependencies = task.get('depends_on')
        require(isinstance(dependencies, list)
                and all(isinstance(dep, str) for dep in dependencies)
                and len(dependencies) == len(set(dependencies))
                and all(dep in by_key and dep != task['task_key'] for dep in dependencies),
                '任务依赖目标不存在、重复或指向自身', 'invalid_input')
    pending = {key: set(task['depends_on']) for key, task in by_key.items()}
    order = []
    while pending:
        ready = [key for key in keys if key in pending and not pending[key]]
        require(bool(ready), '任务依赖关系存在循环', 'invalid_input')
        for key in ready:
            order.append(by_key[key])
            pending.pop(key)
        for dependencies in pending.values():
            dependencies.difference_update(ready)
    return order


def _resume_status(run):
    """Return a safe task-card recovery hint without treating unknown sends as retryable."""
    if not isinstance(run, dict):
        return False, 'run_not_resumable'
    rounds = run.get('rounds') if isinstance(run.get('rounds'), list) else []
    steps = [item for item in rounds if isinstance(item, dict)]
    phases = [item.get('phase') for item in steps]
    if run.get('state') == 'submission_unknown' or any(
            item.get('phase') == 'submission_unknown'
            or item.get('phase') == 'model_inflight' and not item.get('response_received')
            for item in steps):
        return False, 'model_submission_unknown'
    if any(item.get('phase') == 'model_inflight' and item.get('response_received') for item in steps):
        return False, 'model_response_processing'
    state = run.get('state')
    if state in {'queued', 'applying', 'model_inflight', 'paused', 'waiting_children', 'stopped'}:
        return True, None
    error = run.get('error') if isinstance(run.get('error'), dict) else {}
    error_code = error.get('code') if isinstance(error.get('code'), str) else None
    if state == 'blocked' and error_code == 'formal_script_specialists_missing':
        return False, error_code
    last_step = steps[-1] if steps else {}
    if (state == 'blocked' and rounds and phases[-1] == 'invalid_response'
            and (error_code == 'model_response_invalid'
                 or error.get('response_received') is True
                 or last_step.get('response_received') is True)):
        limit = run.get('max_model_calls')
        if type(limit) is int and len(rounds) >= limit:
            return False, 'call_limit_reached'
        return True, None
    if (state == 'blocked' and error_code == 'node_execution_failed'
            and _pending_admitted_node_run(run)):
        return True, None
    if state == 'blocked' and error_code and error_code.replace('_', '').isalnum():
        return False, error_code
    return False, 'run_not_resumable'


class CollaborationTaskStore:
    def __init__(self, db_path):
        self.db_path = Path(db_path)

    def _connect(self, *, readonly=False):
        if readonly:
            if not self.db_path.is_file():
                return None
            conn = sqlite3.connect(self.db_path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout = 10000')
        return conn

    @staticmethod
    def _create_schema(conn):
        conn.execute('''CREATE TABLE IF NOT EXISTS creation_collaboration_tasks (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            canvas_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            source_request_id TEXT NOT NULL,
            source_payload_hash TEXT NOT NULL,
            user_message TEXT NOT NULL,
            intent TEXT NOT NULL CHECK(intent IN ('prepare','execute_request')),
            preparation_kind TEXT,
            speaker TEXT NOT NULL,
            specialists TEXT NOT NULL,
            scope_snapshot TEXT NOT NULL,
            goal TEXT NOT NULL,
            acceptance TEXT NOT NULL,
            requested_action TEXT,
            execution_authorization TEXT,
            max_model_calls INTEGER NOT NULL DEFAULT 8,
            authorization_request_id TEXT,
            authorization_payload_hash TEXT,
            delegation_request_id TEXT NOT NULL,
            run_request_id TEXT NOT NULL,
            delegation_id TEXT,
            run_id TEXT,
            state TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision >= 1),
            error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(owner_id,project_id,canvas_id,conversation_id,source_request_id)
        )''')
        conn.execute('''CREATE INDEX IF NOT EXISTS creation_collaboration_tasks_history
            ON creation_collaboration_tasks(owner_id,project_id,canvas_id,conversation_id,created_at,id)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS creation_collaboration_task_continuations (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            canvas_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            source_operation_id TEXT NOT NULL,
            source_request_id TEXT NOT NULL,
            candidate TEXT NOT NULL,
            source TEXT NOT NULL,
            authorization_request_id TEXT NOT NULL,
            authorization_payload_hash TEXT NOT NULL,
            delegation_request_id TEXT NOT NULL UNIQUE,
            continuation_delegation_id TEXT,
            run_request_id TEXT NOT NULL UNIQUE,
            parent_run_id TEXT NOT NULL,
            budget_root_delegation_id TEXT NOT NULL,
            continuation_run_id TEXT,
            state TEXT NOT NULL,
            error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(task_id,source_operation_id,source_request_id),
            UNIQUE(task_id,authorization_request_id))''')
        conn.execute('''CREATE INDEX IF NOT EXISTS creation_collaboration_task_continuations_anchor
            ON creation_collaboration_task_continuations(continuation_delegation_id,task_id)''')
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(creation_collaboration_tasks)')}
        if 'max_model_calls' not in columns:
            conn.execute('ALTER TABLE creation_collaboration_tasks ADD COLUMN max_model_calls INTEGER NOT NULL DEFAULT 8')
        if 'preparation_kind' not in columns:
            conn.execute('ALTER TABLE creation_collaboration_tasks ADD COLUMN preparation_kind TEXT')

    @staticmethod
    def _row(row):
        if row is None:
            return None
        value = dict(row)
        value.setdefault('max_model_calls', MAX_TASK_MODEL_CALLS)
        value.setdefault('preparation_kind', None)
        for field in ('speaker', 'specialists', 'scope_snapshot', 'acceptance',
                      'requested_action', 'execution_authorization'):
            raw = value.get(field)
            value[field] = json.loads(raw) if raw else ([] if field in {'specialists', 'acceptance'} else None)
        return value

    def _ensure(self, *, identifier, owner_id, project_id, request_id, request_hash,
                user_message, intent, preparation_kind=None, speaker, specialists, scope_snapshot, acceptance,
                requested_action, state, error_code=None):
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        delegation_request_id = f'{identifier}:delegation'
        run_request_id = f'{identifier}:run'
        conn = self._connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            self._create_schema(conn)
            row = conn.execute('''SELECT * FROM creation_collaboration_tasks
                WHERE owner_id=? AND project_id=? AND canvas_id=? AND conversation_id=?
                  AND source_request_id=?''',
                (str(owner_id), str(project_id), scope_snapshot['scope']['canvas_id'],
                 scope_snapshot['conversation_id'], request_id)).fetchone()
            if row is not None:
                existing = self._row(row)
                require(existing['source_payload_hash'] == request_hash
                        and existing['user_message'] == user_message
                        and existing['intent'] == intent
                        and existing.get('preparation_kind') == preparation_kind
                        and existing['speaker'] == speaker
                        and existing['scope_snapshot'] == scope_snapshot,
                        '来源请求已关联不同的协作任务', 'idempotency_conflict')
                conn.commit()
                return existing
            conn.execute('''INSERT INTO creation_collaboration_tasks
                (id,owner_id,project_id,canvas_id,conversation_id,source_request_id,
                 source_payload_hash,user_message,intent,preparation_kind,speaker,specialists,scope_snapshot,
                 goal,acceptance,requested_action,execution_authorization,max_model_calls,
                 authorization_request_id,authorization_payload_hash,delegation_request_id,
                 run_request_id,delegation_id,run_id,state,revision,error_code,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (identifier, str(owner_id), str(project_id), scope_snapshot['scope']['canvas_id'],
                 scope_snapshot['conversation_id'], request_id, request_hash,
                 user_message, intent, preparation_kind, _packed(speaker), _packed(specialists),
                 _packed(scope_snapshot), user_message, _packed(acceptance),
                 _packed(requested_action) if requested_action is not None else None,
                 None, MAX_TASK_MODEL_CALLS, None, None, delegation_request_id, run_request_id,
                 None, None, state, 1, error_code, now, now))
            row = conn.execute('SELECT * FROM creation_collaboration_tasks WHERE id=?', (identifier,)).fetchone()
            conn.commit()
            return self._row(row)
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get(self, identifier, *, owner_id, project_id, canvas_id, conversation_id):
        conn = self._connect(readonly=True)
        if conn is None:
            return None
        try:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                ('creation_collaboration_tasks',)).fetchone():
                return None
            row = conn.execute('''SELECT * FROM creation_collaboration_tasks
                WHERE id=? AND owner_id=? AND project_id=? AND canvas_id=? AND conversation_id=?''',
                (identifier, str(owner_id), str(project_id), canvas_id, conversation_id)).fetchone()
            return self._row(row)
        finally:
            conn.close()

    def catalog(self, *, owner_id, project_id, canvas_id, conversation_id):
        conn = self._connect(readonly=True)
        if conn is None:
            return []
        try:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                ('creation_collaboration_tasks',)).fetchone():
                return []
            rows = conn.execute('''SELECT * FROM creation_collaboration_tasks
                WHERE owner_id=? AND project_id=? AND canvas_id=? AND conversation_id=?
                ORDER BY created_at,id''',
                (str(owner_id), str(project_id), canvas_id, conversation_id)).fetchall()
            return [self._row(row) for row in rows]
        finally:
            conn.close()

    def continuations(self, task_id, *, owner_id, project_id, canvas_id, conversation_id):
        conn = self._connect(readonly=True)
        if conn is None:
            return []
        try:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                ('creation_collaboration_task_continuations',)).fetchone():
                return []
            rows = conn.execute('''SELECT c.* FROM creation_collaboration_task_continuations c
                JOIN creation_collaboration_tasks t ON t.id=c.task_id
                WHERE c.task_id=? AND c.owner_id=? AND c.project_id=? AND c.canvas_id=?
                  AND c.conversation_id=? AND t.owner_id=? AND t.project_id=?
                  AND t.canvas_id=? AND t.conversation_id=?
                ORDER BY c.created_at,c.id''',
                (task_id, str(owner_id), str(project_id), canvas_id, conversation_id,
                 str(owner_id), str(project_id), canvas_id, conversation_id)).fetchall()
            return [self._continuation_row(row) for row in rows]
        finally:
            conn.close()

    @staticmethod
    def _continuation_row(row):
        value = dict(row)
        for field in ('candidate', 'source'):
            value[field] = json.loads(value[field])
        return value

    def continuation_by_candidate(self, task_id, source_operation_id, source_request_id,
                                  *, owner_id, project_id, canvas_id, conversation_id):
        rows = self.continuations(task_id, owner_id=owner_id, project_id=project_id,
                                  canvas_id=canvas_id, conversation_id=conversation_id)
        return next((row for row in rows if row['source_operation_id'] == source_operation_id
                     and row['source_request_id'] == source_request_id), None)

    def continuation_by_delegation(self, delegation_id):
        conn = self._connect(readonly=True)
        if conn is None:
            return None
        try:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                ('creation_collaboration_task_continuations',)).fetchone():
                return None
            row = conn.execute('''SELECT * FROM creation_collaboration_task_continuations
                WHERE continuation_delegation_id=?''', (delegation_id,)).fetchone()
            return self._continuation_row(row) if row else None
        finally:
            conn.close()

    def begin_continuation(self, identifier, *, owner_id, project_id, canvas_id, conversation_id,
                           expected_revision, request_id, payload_hash, source_operation_id,
                           source_request_id, candidate, source, parent_run_id,
                           budget_root_delegation_id, continuation_id,
                           delegation_request_id, run_request_id):
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        conn = self._connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            self._create_schema(conn)
            row = conn.execute('''SELECT * FROM creation_collaboration_tasks
                WHERE id=? AND owner_id=? AND project_id=? AND canvas_id=? AND conversation_id=?''',
                (identifier, str(owner_id), str(project_id), canvas_id, conversation_id)).fetchone()
            require(row is not None, '协作任务不存在', 'scope_violation')
            existing = conn.execute('''SELECT * FROM creation_collaboration_task_continuations
                WHERE task_id=? AND source_operation_id=? AND source_request_id=?''',
                (identifier, source_operation_id, source_request_id)).fetchone()
            if existing is not None:
                value = self._continuation_row(existing)
                require(value['authorization_request_id'] == request_id
                        and value['authorization_payload_hash'] == payload_hash,
                        '此准备候选已被其他授权请求使用', 'candidate_already_authorized')
                conn.commit()
                return self._row(row), value, False
            duplicate_request = conn.execute('''SELECT 1 FROM creation_collaboration_task_continuations
                WHERE task_id=? AND authorization_request_id=?''', (identifier, request_id)).fetchone()
            require(duplicate_request is None, '授权请求编号已用于其他准备候选', 'idempotency_conflict')
            require(row['revision'] == expected_revision, '协作任务状态已变化', 'revision_conflict')
            require(row['run_id'] == parent_run_id
                    and row['delegation_id'] == budget_root_delegation_id
                    and row['intent'] == 'prepare',
                    '来源任务尚未交付可续跑候选', 'revision_conflict')
            conn.execute('''INSERT INTO creation_collaboration_task_continuations
                (id,task_id,owner_id,project_id,canvas_id,conversation_id,source_operation_id,
                 source_request_id,candidate,source,authorization_request_id,authorization_payload_hash,
                 delegation_request_id,continuation_delegation_id,run_request_id,parent_run_id,
                 budget_root_delegation_id,continuation_run_id,state,error_code,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (continuation_id, identifier, str(owner_id), str(project_id), canvas_id, conversation_id,
                 source_operation_id, source_request_id, _packed(candidate), _packed(source), request_id,
                 payload_hash, delegation_request_id, None, run_request_id, parent_run_id,
                 budget_root_delegation_id, None, 'authorizing', None, now, now))
            conn.execute('''UPDATE creation_collaboration_tasks SET revision=revision+1,updated_at=? WHERE id=?''',
                         (now, identifier))
            saved_task = conn.execute('SELECT * FROM creation_collaboration_tasks WHERE id=?',
                                      (identifier,)).fetchone()
            saved_continuation = conn.execute('''SELECT * FROM creation_collaboration_task_continuations
                WHERE id=?''', (continuation_id,)).fetchone()
            conn.commit()
            return self._row(saved_task), self._continuation_row(saved_continuation), True
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def update_continuation(self, continuation_id, *, owner_id, project_id, canvas_id, conversation_id,
                            state=None, error_code=None,
                            continuation_delegation_id=None, continuation_run_id=None,
                            preserve_stop=False):
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        conn = self._connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            self._create_schema(conn)
            row = conn.execute('''SELECT * FROM creation_collaboration_task_continuations
                WHERE id=? AND owner_id=? AND project_id=? AND canvas_id=? AND conversation_id=?''',
                (continuation_id, str(owner_id), str(project_id), canvas_id,
                 conversation_id)).fetchone()
            require(row is not None, '协作任务续跑授权不存在', 'scope_violation')
            persisted_state = row['state']
            if not (preserve_stop and persisted_state in {'stopped', 'submission_unknown'}):
                persisted_state = state or persisted_state
            values = {
                'state': persisted_state,
                'error_code': error_code if error_code is not None else row['error_code'],
                'continuation_delegation_id': (continuation_delegation_id
                    if continuation_delegation_id is not None else row['continuation_delegation_id']),
                'continuation_run_id': (continuation_run_id
                    if continuation_run_id is not None else row['continuation_run_id']),
            }
            conn.execute('''UPDATE creation_collaboration_task_continuations SET state=?,error_code=?,
                continuation_delegation_id=?,continuation_run_id=?,updated_at=? WHERE id=?''',
                (values['state'], values['error_code'], values['continuation_delegation_id'],
                 values['continuation_run_id'], now, continuation_id))
            saved = conn.execute('''SELECT * FROM creation_collaboration_task_continuations
                WHERE id=?''', (continuation_id,)).fetchone()
            conn.commit()
            return self._continuation_row(saved)
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def update(self, identifier, *, owner_id, project_id, canvas_id, conversation_id,
               state=None, delegation_id=None, run_id=None, error_code=None,
               execution_authorization=None, authorization_request_id=None,
               authorization_payload_hash=None, expected_revision=None):
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        conn = self._connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            self._create_schema(conn)
            row = conn.execute('''SELECT * FROM creation_collaboration_tasks
                WHERE id=? AND owner_id=? AND project_id=? AND canvas_id=? AND conversation_id=?''',
                (identifier, str(owner_id), str(project_id), canvas_id, conversation_id)).fetchone()
            require(row is not None, '协作任务不存在', 'scope_violation')
            if expected_revision is not None:
                require(row['revision'] == expected_revision, '协作任务状态已变化', 'revision_conflict')
            values = {
                'state': state if state is not None else row['state'],
                'delegation_id': delegation_id if delegation_id is not None else row['delegation_id'],
                'run_id': run_id if run_id is not None else row['run_id'],
                'error_code': error_code if error_code is not None else row['error_code'],
                'execution_authorization': (_packed(execution_authorization)
                    if execution_authorization is not None else row['execution_authorization']),
                'authorization_request_id': (authorization_request_id if authorization_request_id is not None
                    else row['authorization_request_id']),
                'authorization_payload_hash': (authorization_payload_hash if authorization_payload_hash is not None
                    else row['authorization_payload_hash']),
            }
            revision = row['revision'] + (1 if expected_revision is not None else 0)
            conn.execute('''UPDATE creation_collaboration_tasks SET state=?,delegation_id=?,run_id=?,
                error_code=?,execution_authorization=?,authorization_request_id=?,
                authorization_payload_hash=?,revision=?,updated_at=? WHERE id=?''',
                (values['state'], values['delegation_id'], values['run_id'], values['error_code'],
                 values['execution_authorization'], values['authorization_request_id'],
                 values['authorization_payload_hash'], revision, now, identifier))
            saved = conn.execute('SELECT * FROM creation_collaboration_tasks WHERE id=?', (identifier,)).fetchone()
            conn.commit()
            return self._row(saved)
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def authorize(self, identifier, *, owner_id, project_id, canvas_id, conversation_id,
                  expected_revision, request_id, authorization, payload_hash):
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        conn = self._connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            self._create_schema(conn)
            row = conn.execute('''SELECT * FROM creation_collaboration_tasks
                WHERE id=? AND owner_id=? AND project_id=? AND canvas_id=? AND conversation_id=?''',
                (identifier, str(owner_id), str(project_id), canvas_id, conversation_id)).fetchone()
            require(row is not None, '协作任务不存在', 'scope_violation')
            if row['authorization_request_id']:
                require(row['authorization_request_id'] == request_id
                        and row['authorization_payload_hash'] == payload_hash,
                        '协作任务已使用其他授权请求', 'idempotency_conflict')
                conn.commit()
                return self._row(row)
            require(row['state'] == 'awaiting_authorization', '协作任务当前不等待授权', 'revision_conflict')
            require(row['revision'] == expected_revision, '协作任务状态已变化', 'revision_conflict')
            conn.execute('''UPDATE creation_collaboration_tasks SET state='bridging',
                execution_authorization=?,authorization_request_id=?,authorization_payload_hash=?,
                revision=revision+1,updated_at=? WHERE id=?''',
                (_packed(authorization), request_id, payload_hash, now, identifier))
            saved = conn.execute('SELECT * FROM creation_collaboration_tasks WHERE id=?', (identifier,)).fetchone()
            conn.commit()
            return self._row(saved)
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()


class CollaborationTasks:
    def __init__(self, ctx, *, username, canvas_id, conversation_id, resolve_context,
                 allow_task_creation=True):
        self.ctx = ctx
        self.username = text(username, 200)
        self.canvas_id = text(canvas_id, 128)
        self.conversation_id = text(conversation_id, 64)
        self.owner_id = str(ctx.requester_user_id)
        self.project_id = str(ctx.project_id)
        self.resolve_context = resolve_context
        self.allow_task_creation = allow_task_creation
        self.store = CollaborationTaskStore(Path(ctx.state_dir) / 'creation' / 'objects.sqlite3')

    def _key(self):
        return {'owner_id': self.owner_id, 'project_id': self.project_id,
                'canvas_id': self.canvas_id, 'conversation_id': self.conversation_id}

    async def _fresh(self, role):
        try:
            current = await self.resolve_context(role)
        except HTTPException as exc:
            raise OperationError('scope_violation', '协作任务访问范围已失效') from exc
        require(current.project_id == self.project_id
                and str(current.requester_user_id) == self.owner_id
                and Path(current.state_dir).resolve() == Path(self.ctx.state_dir).resolve()
                and Path(current.output_dir).resolve() == Path(self.ctx.output_dir).resolve(),
                '协作任务项目身份已变化', 'scope_violation')
        agent_context.conversation(current, self.username, self.canvas_id, self.conversation_id)
        return current

    def _get(self, identifier):
        return self.store.get(identifier, **self._key())

    async def _public(self, row):
        result = {
            'id': row['id'], 'revision': row['revision'], 'state': row['state'],
            'source_request_id': row['source_request_id'], 'canvas_id': row['canvas_id'],
            'conversation_id': row['conversation_id'], 'user_message': row['user_message'],
            'intent': row['intent'], 'speaker': row['speaker'], 'goal': row['goal'],
            'preparation_kind': row.get('preparation_kind'),
            'acceptance': row['acceptance'], 'requested_action': row['requested_action'],
            'execution_authorization': row['execution_authorization'],
            'max_model_calls': row['max_model_calls'],
            'scope': {
                'kind': row['scope_snapshot']['scope']['kind'],
                'canvas_id': row['scope_snapshot']['scope']['canvas_id'],
                'node_ids': row['scope_snapshot']['node_ids'],
                'observed_canvas_revision': row['scope_snapshot']['observed_canvas_revision'],
            },
            'delegation_id': row['delegation_id'], 'run_id': row['run_id'],
            'error_code': row['error_code'],
            'resume_available': row['state'] in {'bridging', 'delegated', 'stopped'},
            'resume_blocked_reason': (row['error_code'] or
                ('authorization_required' if row['state'] == 'awaiting_authorization'
                 else None if row['state'] in {'bridging', 'delegated', 'stopped'}
                 else 'run_not_started')),
            'prepared_execution_candidates': [],
            'continuations': [],
            'created_at': row['created_at'], 'updated_at': row['updated_at'],
        }
        runs = None
        if row['run_id']:
            runs = await self._runs(self.ctx)
            run = await runs.get(row['run_id'])
            result['run'] = run
            result['state'] = run['state']
            result['resume_available'], result['resume_blocked_reason'] = _resume_status(run)
            result['prepared_execution_candidates'] = await self._prepared_execution_candidates(runs, run, row)
        continuations = await asyncio.to_thread(self.store.continuations, row['id'], **self._key())
        for item in continuations:
            public = {
                'id': item['id'], 'state': item['state'], 'candidate': item['candidate'],
                'effect': PREPARED_CANDIDATE_EFFECT,
                'source': item['source'],
                'authorization_request_id': item['authorization_request_id'],
                'delegation_request_id': item['delegation_request_id'],
                'delegation_id': item['continuation_delegation_id'],
                'run_request_id': item['run_request_id'],
                'run_id': item['continuation_run_id'], 'error_code': item['error_code'],
            }
            if item['continuation_run_id'] and runs is not None:
                continuation_run = await runs.get(item['continuation_run_id'])
                public['run'] = continuation_run
                public['state'] = continuation_run['state']
                if continuation_run['state'] == 'submission_unknown':
                    public['error_code'] = 'submission_unknown'
            result['continuations'].append(public)
        return result

    async def _run_tree(self, runs, root_run):
        values, seen, pending = [], set(), [root_run]
        while pending and len(seen) < 128:
            run = pending.pop()
            run_id = run.get('id') if isinstance(run, dict) else None
            if not isinstance(run_id, str) or run_id in seen:
                continue
            seen.add(run_id)
            values.append(run)
            for child in run.get('children', []):
                child_id = child.get('child_run_id') if isinstance(child, dict) else None
                if isinstance(child_id, str) and child_id not in seen:
                    pending.append(await runs.get(child_id))
        return values

    def _candidate_record_valid(self, run, output, candidate):
        if not isinstance(candidate, dict) or set(candidate) != {
                'canvas_id', 'node_id', 'node_type', 'operation', 'max_node_runs',
                'source_operation_id', 'request_id'}:
            return False
        return (
            candidate.get('node_type') == 'imageGenNode'
            and candidate.get('operation') == 'generate'
            and candidate.get('max_node_runs') == 1
            and candidate.get('canvas_id') == self.canvas_id
            and isinstance(candidate.get('node_id'), str) and bool(candidate['node_id'])
            and isinstance(candidate.get('source_operation_id'), str) and bool(candidate['source_operation_id'])
            and isinstance(candidate.get('request_id'), str) and bool(candidate['request_id'])
            and output.get('delegation_id') == run.get('delegation_id')
            and output.get('source', {}).get('delegation_id') == run.get('delegation_id')
            and output.get('state') == 'applied'
            and output.get('projection_status') == 'applied'
            and output.get('graph_operation_id') == candidate.get('source_operation_id')
            and output.get('image_node_id') == candidate.get('node_id')
            and output.get('asset_design', {}).get('prepared_execution') == candidate
        )

    async def _prepared_execution_candidates(self, runs, root_run, task_row):
        """Expose only outputs from the task's real AgentRuns tree and durable asset receipts."""
        from .asset_generation import AssetGeneration

        existing = await asyncio.to_thread(self.store.continuations, task_row['id'], **self._key())
        by_candidate = {(item['source_operation_id'], item['source_request_id']): item
                        for item in existing}
        found = []
        for run in await self._run_tree(runs, root_run):
            run_id = run['id']
            if run.get('root_delegation_id') != task_row['delegation_id']:
                continue
            for output in run.get('outputs', []):
                if not isinstance(output, dict):
                    continue
                if output.get('operation') != 'asset.design.create':
                    continue
                raw_candidate = output.get('prepared_execution')
                if not self._candidate_record_valid(run, output, raw_candidate):
                    continue
                candidate = AssetGeneration.verified_prepared_execution(self.ctx, raw_candidate)
                continuation = by_candidate.get((raw_candidate['source_operation_id'], raw_candidate['request_id']))
                if continuation is not None:
                    status, code = 'authorized', None
                elif candidate is None:
                    status, code = 'stale', 'stale_input'
                else:
                    try:
                        self._continuation_speaker(task_row, candidate, self.ctx)
                    except OperationError as exc:
                        if exc.code != 'capability_denied':
                            raise
                        status, code = 'blocked', exc.code
                    else:
                        status, code = 'ready_to_authorize', None
                found.append({
                    'status': status,
                    'code': code,
                    'candidate': raw_candidate,
                    'effect': PREPARED_CANDIDATE_EFFECT,
                    'continuation_id': continuation['id'] if continuation else None,
                    'source': {
                        'run_id': run_id,
                        'delegation_id': output.get('delegation_id'),
                        'output_id': output.get('id'),
                        'operation': output.get('operation'),
                        'operation_id': output.get('operation_id'),
                    },
                })
        return found

    async def _find_prepared_execution(self, runs, root_run, task_row, source_operation_id, source_request_id):
        from .asset_generation import AssetGeneration

        for run in await self._run_tree(runs, root_run):
            if run.get('root_delegation_id') != task_row['delegation_id']:
                continue
            for output in run.get('outputs', []):
                if not isinstance(output, dict) or output.get('operation') != 'asset.design.create':
                    continue
                candidate = output.get('prepared_execution')
                if (not isinstance(candidate, dict)
                        or candidate.get('source_operation_id') != source_operation_id
                        or candidate.get('request_id') != source_request_id):
                    continue
                require(self._candidate_record_valid(run, output, candidate),
                        '准备产物回执与Agent运行来源不匹配', 'scope_violation')
                verified = AssetGeneration.verified_prepared_execution(self.ctx, candidate)
                require(verified is not None, '准备产物或当前节点绑定已变化', 'stale_input')
                source = {
                    'run_id': run['id'], 'delegation_id': output['delegation_id'],
                    'output_id': output['id'], 'operation': output['operation'],
                    'operation_id': output.get('operation_id'),
                }
                return verified, source
        raise OperationError('scope_violation', '准备候选不属于此协作任务的真实运行回执')

    def _continuation_speaker(self, row, candidate, ctx):
        """Resolve only identities frozen into this task; never bind a current member revision."""
        delegations = Delegations(ctx, username=self.username, authority='user')
        frozen = [{'adoption_id': row['speaker']['adoption_id'],
                   'revision': row['speaker']['adoption_revision']}]
        frozen.extend({'adoption_id': item['adoption_id'], 'revision': item['revision']}
                      for item in row['specialists'])
        seen = set()
        with delegations.read() as conn:
            for item in frozen:
                identity = (item['adoption_id'], item['revision'])
                if identity in seen:
                    continue
                seen.add(identity)
                adopted = delegations._agent(conn, *identity)
                profile = adopted['effective']
                if 'node.run' not in profile.get('capabilities', []):
                    continue
                required = profile.get('required_node_operations', [])
                if not any(isinstance(operation, dict)
                           and operation.get('node_type') == candidate['node_type']
                           and operation.get('operation') == candidate['operation']
                           for operation in required):
                    continue
                return {'adoption_id': item['adoption_id'], 'revision': item['revision'],
                        'profile': profile}
        raise OperationError('capability_denied',
            '此任务冻结的主讲者和专业成员都未声明 imageGenNode.generate 能力')

    @staticmethod
    def _continuation_identifier(task_id, source_operation_id, source_request_id):
        source = '\0'.join((task_id, source_operation_id, source_request_id))
        return 'task-cont-' + hashlib.sha256(source.encode('utf-8')).hexdigest()[:32]

    @staticmethod
    def _continuation_payload_hash(task_id, request_id, expected_revision,
                                   source_operation_id, source_request_id, candidate, source):
        stable_source = {key: value for key, value in source.items()
                         if key != 'continuation_speaker'}
        value = {
            'task_id': task_id, 'request_id': request_id,
            'expected_revision': expected_revision, 'source_operation_id': source_operation_id,
            'source_request_id': source_request_id, 'confirmed': True,
            'candidate': candidate, 'source': stable_source, 'effect': PREPARED_CANDIDATE_EFFECT,
        }
        return hashlib.sha256(_packed(value).encode('utf-8')).hexdigest()

    def _candidate_asset_image_input_refs(self, ctx, task_row, candidate):
        """Re-authorize only candidate image dependencies frozen by this task."""
        from novelvideo.freezone.canvas_store import read_canvas
        from .asset_generation import AssetGeneration

        require(AssetGeneration.verified_prepared_execution(ctx, candidate) == candidate,
                '准备候选或其图片参考已变化', 'stale_input')
        canvas = read_canvas(ctx.state_dir, self.canvas_id)
        require(isinstance(canvas, dict) and canvas.get('project_id') == self.project_id,
                '候选画布不可用', 'scope_violation')
        candidate_node = next((node for node in canvas.get('nodes', [])
                               if isinstance(node, dict) and node.get('id') == candidate['node_id']), None)
        binding = (candidate_node or {}).get('data', {}).get('designBinding')
        require(isinstance(binding, dict) and isinstance(binding.get('references'), list),
                '候选设计缺少固定图片依赖', 'scope_violation')
        frozen_scope = task_row.get('scope_snapshot')
        frozen_ids = set(frozen_scope.get('node_ids', [])) if isinstance(frozen_scope, dict) else set()
        frozen_nodes = {item.get('node_id'): item for item in frozen_scope.get('nodes', [])
                        if isinstance(item, dict)} if isinstance(frozen_scope, dict) else {}
        current_nodes = {node.get('id'): node for node in canvas.get('nodes', [])
                         if isinstance(node, dict) and isinstance(node.get('id'), str)}
        refs = []
        for reference in binding['references']:
            require(isinstance(reference, dict) and reference.get('kind') == 'canvas_node'
                    and reference.get('canvas_id') == self.canvas_id,
                    '候选图片依赖超出当前画布', 'scope_violation')
            node_id = reference.get('node_id')
            frozen = frozen_nodes.get(node_id)
            source = current_nodes.get(node_id)
            require(isinstance(node_id, str) and node_id in frozen_ids and frozen is not None
                    and source is not None and source.get('type') == frozen.get('node_type'),
                    '候选图片依赖不属于原委托冻结范围', 'scope_violation')
            require(agent_context.node_snapshot(source) == frozen,
                    '候选图片依赖已偏离原委托快照', 'stale_input', node_id=node_id)
            current_fingerprint = AssetGeneration._canvas_reference_fingerprint(
                ctx, self.project_id, self.canvas_id, source)
            require(current_fingerprint == reference.get('input_fingerprint'),
                    '候选图片依赖的内容已变化', 'stale_input', node_id=node_id)
            ref = {'kind': 'canvas_node', 'canvas_id': self.canvas_id, 'node_id': node_id}
            require(ref not in refs, '候选设计重复引用了同一图片', 'invalid_input')
            refs.append(ref)
        require(len(refs) <= 100, '候选设计包含过多图片依赖', 'invalid_input')
        return refs

    async def _continue_candidate(self, task_row, continuation, *, background=None):
        continuation = await asyncio.to_thread(self.store.continuation_by_candidate,
            task_row['id'], continuation['source_operation_id'], continuation['source_request_id'],
            **self._key()) or continuation
        # A stop is durable across a crash between authorization, run creation,
        # and scheduling. An exact request replay must never restart it.
        if continuation['state'] in {'stopped', 'submission_unknown'}:
            return await self._public(task_row)
        current = await self._fresh('editor')
        delegations = Delegations(current, username=self.username, authority='user')
        candidate = continuation['candidate']
        try:
            input_refs = self._candidate_asset_image_input_refs(current, task_row, candidate)
        except OperationError as exc:
            if exc.code in {'stale_input', 'scope_violation', 'dependency_input_unavailable', 'invalid_input'}:
                await asyncio.to_thread(self.store.update_continuation, continuation['id'],
                    **self._key(), state='blocked', error_code=exc.code)
                return await self._public(task_row)
            raise
        speaker = continuation['source'].get('continuation_speaker')
        if not isinstance(speaker, dict):
            try:
                speaker = self._continuation_speaker(task_row, candidate, current)
            except OperationError as exc:
                if exc.code == 'capability_denied':
                    await asyncio.to_thread(self.store.update_continuation, continuation['id'],
                        **self._key(), state='blocked', error_code=exc.code)
                    return await self._public(task_row)
                raise
            source = {**continuation['source'], 'continuation_speaker': speaker}
            self._save_continuation_source(continuation['id'], source)
            continuation = {**continuation, 'source': source}

        body = {
            'canvas_id': self.canvas_id, 'conversation_id': self.conversation_id,
            'scope': {'kind': 'nodes', 'node_ids': [candidate['node_id']]},
            'agent': {'kind': 'adoption', 'adoption_id': speaker['adoption_id'],
                      'revision': speaker['revision']},
            'input_refs': input_refs,
            'goal': f"按任务卡对已准备图像节点的明确授权，生成并投影一张资产图：{task_row['goal']}",
            'allowed_operations': ['node.run'],
            'acceptance': ['仅对这一个已准备 imageGenNode 执行一次 generate 并保存真实投影回执。'],
            'execution_authorization': {
                'allowed_node_ids': [candidate['node_id']],
                'allowed_node_operations': [{'node_id': candidate['node_id'], 'operation': candidate['operation']}],
                'max_node_runs': 1,
            },
        }
        try:
            preview = delegations.preview(body)
            require(preview['contract']['expanded_scope']['node_ids'] == [candidate['node_id']],
                    '候选节点范围已变化', 'stale_input')
            require(preview['contract']['execution_authorization'] == {
                        'allowed_node_ids': [candidate['node_id']],
                        'allowed_node_operations': [{'node_id': candidate['node_id'],
                                                     'operation': candidate['operation']}],
                        'max_node_runs': 1},
                    '续跑授权与用户确认的候选不一致', 'scope_violation')
        except OperationError as exc:
            if exc.code in {'stale_input', 'scope_violation'}:
                await asyncio.to_thread(self.store.update_continuation, continuation['id'],
                    **self._key(), state='stale', error_code=exc.code)
                return await self._public(task_row)
            if exc.code == 'capability_denied':
                await asyncio.to_thread(self.store.update_continuation, continuation['id'],
                    **self._key(), state='blocked', error_code=exc.code)
                return await self._public(task_row)
            raise

        receipt = delegations.receipt(continuation['delegation_request_id'])
        if receipt is None:
            receipt = await delegations.apply(DomainCommand(
                request_id=continuation['delegation_request_id'], action='delegation.create',
                payload={**body, 'preview_proof': preview['proof']}))
        actual = receipt.get('contract')
        require(isinstance(actual, dict)
                and actual.get('agent', {}).get('adoption_id') == speaker['adoption_id']
                and actual.get('agent', {}).get('revision') == speaker['revision']
                and actual.get('allowed_operations') == ['node.run']
                and actual.get('expanded_scope', {}).get('node_ids') == [candidate['node_id']]
                and actual.get('execution_authorization', {}).get('allowed_node_ids') == [candidate['node_id']]
                and actual.get('execution_authorization', {}).get('allowed_node_operations') == [
                    {'node_id': candidate['node_id'], 'operation': candidate['operation']}]
                and actual.get('execution_authorization', {}).get('max_node_runs') == 1,
                '续跑委托回执与已授权范围不匹配', 'scope_violation')
        continuation = await asyncio.to_thread(self.store.update_continuation,
            continuation['id'], **self._key(), state='delegated',
            continuation_delegation_id=receipt['delegation_id'],
            preserve_stop=True)
        if continuation['state'] in {'stopped', 'submission_unknown'}:
            return await self._public(task_row)
        runs = await self._runs(current)
        run_receipt = await runs.receipt(continuation['run_request_id'])
        if run_receipt is None:
            run_receipt = await runs.start_continuation({
                'request_id': continuation['run_request_id'],
                'delegation_id': receipt['delegation_id'], 'contract_hash': receipt['contract_hash'],
                'task_id': task_row['id'], 'continuation_id': continuation['id'],
                'parent_run_id': continuation['parent_run_id'],
                'source_operation_id': continuation['source_operation_id'],
                'source_request_id': continuation['source_request_id'],
            })
        run_id = run_receipt['run']['id']
        continuation = await asyncio.to_thread(self.store.update_continuation,
            continuation['id'], **self._key(), state='queued',
            continuation_delegation_id=receipt['delegation_id'],
            continuation_run_id=run_id, error_code=None, preserve_stop=True)
        if continuation['state'] in {'stopped', 'submission_unknown'}:
            run = await runs.get(run_id)
            if run['state'] not in {'delivered', 'partial', 'stopped', 'blocked', 'submission_unknown'}:
                await runs.control(run_id, 'stop', {
                    'request_id': f"task-stop-{hashlib.sha256((continuation['id'] + run_id).encode()).hexdigest()[:40]}",
                    'expected_revision': run['revision'],
                })
            return await self._public(task_row)
        await self._schedule_drive(runs, run_id, background)
        latest_task = await asyncio.to_thread(self._get, task_row['id'])
        return await self._public(latest_task)

    def _save_continuation_source(self, continuation_id, source):
        conn = self.store._connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            self.store._create_schema(conn)
            row = conn.execute('''SELECT 1 FROM creation_collaboration_task_continuations
                WHERE id=? AND owner_id=? AND project_id=? AND canvas_id=? AND conversation_id=?''',
                (continuation_id, self.owner_id, self.project_id, self.canvas_id,
                 self.conversation_id)).fetchone()
            require(row is not None, '协作任务续跑授权不存在', 'scope_violation')
            conn.execute('UPDATE creation_collaboration_task_continuations SET source=?,updated_at=? WHERE id=?',
                         (_packed(source), datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                          continuation_id))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    async def authorize_candidate(self, identifier, body, *, background=None):
        exact(body, {'request_id', 'expected_revision', 'source_operation_id',
                     'source_request_id', 'confirmed'})
        request_id = text(body['request_id'], 128)
        expected_revision = integer(body['expected_revision'], 1)
        source_operation_id = text(body['source_operation_id'], 128)
        source_request_id = text(body['source_request_id'], 128)
        require(type(body['confirmed']) is bool, '授权确认必须是布尔值')
        require(body['confirmed'], '请确认任务卡中列出的单个图像节点与一次生成操作',
                'authorization_required')
        current = await self._fresh('editor')
        task_row = await asyncio.to_thread(self._get, identifier)
        require(task_row is not None, '协作任务不存在', 'scope_violation')
        require(task_row['intent'] == 'prepare' and task_row['run_id'] and task_row['delegation_id'],
                '协作任务没有可续跑的准备运行', 'revision_conflict')

        existing = await asyncio.to_thread(self.store.continuation_by_candidate,
            identifier, source_operation_id, source_request_id, **self._key())
        if existing is not None:
            if existing['authorization_request_id'] != request_id:
                raise OperationError('candidate_already_authorized', '此准备候选已被其他授权请求使用')
            payload_hash = self._continuation_payload_hash(identifier, request_id, expected_revision,
                source_operation_id, source_request_id, existing['candidate'], existing['source'])
            require(existing['authorization_payload_hash'] == payload_hash,
                    '授权请求编号已用于不同的候选或任务版本', 'idempotency_conflict')
            return await self._continue_candidate(task_row, existing, background=background)

        runs = await self._runs(current)
        root_run = await runs.get(task_row['run_id'])
        require(root_run.get('delegation_id') == task_row['delegation_id']
                and root_run.get('parent_run_id') is None
                and root_run.get('root_delegation_id') == task_row['delegation_id'],
                '协作任务原根运行身份无效', 'scope_violation')
        require(root_run['state'] in {'delivered', 'partial'},
                '协作任务根运行尚未交付可授权候选', 'revision_conflict')
        candidate, source = await self._find_prepared_execution(
            runs, root_run, task_row, source_operation_id, source_request_id)
        payload_hash = self._continuation_payload_hash(identifier, request_id, expected_revision,
            source_operation_id, source_request_id, candidate, source)
        continuation_id = self._continuation_identifier(identifier, source_operation_id, source_request_id)
        saved_task, continuation, _created = await asyncio.to_thread(self.store.begin_continuation,
            identifier, **self._key(), expected_revision=expected_revision, request_id=request_id,
            payload_hash=payload_hash, source_operation_id=source_operation_id,
            source_request_id=source_request_id, candidate=candidate, source=source,
            parent_run_id=task_row['run_id'], budget_root_delegation_id=task_row['delegation_id'],
            continuation_id=continuation_id,
            delegation_request_id=f'{continuation_id}:delegation', run_request_id=f'{continuation_id}:run')
        return await self._continue_candidate(saved_task, continuation, background=background)

    async def catalog(self):
        await self._fresh('viewer')
        rows = await asyncio.to_thread(self.store.catalog, **self._key())
        return [await self._public(row) for row in rows]

    async def get(self, identifier):
        await self._fresh('viewer')
        row = await asyncio.to_thread(self._get, identifier)
        require(row is not None, '协作任务不存在', 'scope_violation')
        return await self._public(row)

    async def accept_intake(self, *, request, request_hash, intake, speaker,
                            specialists, scope_snapshot, task_id, background=None):
        require(self.allow_task_creation, 'Agent 会话不能以用户身份创建协作任务', 'scope_violation')
        require(intake['intent'] in {'prepare', 'execute_request'}, '协作任务意图无效')
        preparation_kind = intake.get('preparation_kind')
        require(preparation_kind in {None, 'formal_episode_script'}
                and (preparation_kind is None or intake['intent'] == 'prepare'),
                '协作任务准备类型无效', 'invalid_input')
        # Creating a task card is a project edit even if it still awaits a
        # separate, explicit execution authorization. Check before persistence.
        current = await self._fresh('editor')
        acceptance = (intake.get('acceptance') if intake['intent'] == 'prepare'
                      else [intake['requested_action']['effect']])
        state = 'bridging' if intake['intent'] == 'prepare' else 'awaiting_authorization'
        error_code = None
        if intake['intent'] == 'execute_request':
            error_code = self._requested_action_error(scope_snapshot, speaker, intake['requested_action'])
            if error_code is not None:
                state = 'blocked'
        row = await asyncio.to_thread(self.store._ensure,
            identifier=task_id, owner_id=self.owner_id, project_id=self.project_id,
            request_id=request.request_id, request_hash=request_hash, user_message=request.message,
            intent=intake['intent'], preparation_kind=preparation_kind,
            speaker=speaker, specialists=specialists,
            scope_snapshot=scope_snapshot, acceptance=acceptance,
            requested_action=intake.get('requested_action'), state=state, error_code=error_code)
        if row['run_id']:
            return await self.resume_bridge(row['id'], background=background)
        if row['state'] in {'stale', 'blocked', 'stopped'}:
            return await self._public(row)
        if not await self._scope_matches(row, current):
            row = await asyncio.to_thread(self.store.update, row['id'],
                **self._key(), state='stale', error_code='stale_input')
            return await self._public(row)
        if row['state'] == 'awaiting_authorization':
            return await self._public(row)
        return await self.bridge(row['id'], background=background)

    async def resume_bridge(self, identifier, *, background=None):
        row = await asyncio.to_thread(self._get, identifier)
        require(row is not None, '协作任务不存在', 'scope_violation')
        if row['state'] in {'stale', 'awaiting_authorization', 'blocked'}:
            return await self._public(row)
        if row['run_id']:
            runs = await self._runs(self.ctx)
            run = await runs.get(row['run_id'])
            if run['state'] in {'queued', 'applying', 'model_inflight', 'paused', 'waiting_children'}:
                await self._schedule_drive(runs, row['run_id'], background)
            return await self._public(row)
        return await self.bridge(identifier, background=background)

    async def bridge(self, identifier, *, background=None):
        row = await asyncio.to_thread(self._get, identifier)
        require(row is not None, '协作任务不存在', 'scope_violation')
        require(row['state'] in {'bridging', 'delegated'}, '协作任务当前不能继续桥接', 'revision_conflict')
        current = await self._fresh('editor')
        if not await self._scope_matches(row, current):
            row = await asyncio.to_thread(self.store.update, row['id'],
                **self._key(), state='stale', error_code='stale_input')
            return await self._public(row)

        if len(row['specialists']) > agent_model.MAX_ELIGIBLE_SPECIALISTS:
            row = await asyncio.to_thread(self.store.update, row['id'],
                **self._key(), state='blocked', error_code='specialist_limit')
            return await self._public(row)

        allowed = self._allowed_operations(row)
        required_execution_operation = None
        authorization = row['execution_authorization']
        if authorization is not None:
            action = row['requested_action']
            required_execution_operation = action['operation']
            capability = 'node.run' if required_execution_operation == 'node.run' else 'node.patch'
            allowed = sorted(set(allowed) | {capability})
            require(capability in row['speaker']['profile'].get('capabilities', []),
                    '固定专业成员没有该节点操作能力', 'capability_denied')
            require(action['node_id'] in row['scope_snapshot']['node_ids'],
                    '请求动作超出固定选择范围', 'scope_violation')
        if not allowed:
            row = await asyncio.to_thread(self.store.update, row['id'],
                **self._key(), state='blocked', error_code='capability_denied')
            return await self._public(row)

        delegations = Delegations(current, username=self.username, authority='user',
            trusted_preparation_kind=(row.get('preparation_kind')
                                      if row.get('preparation_kind') == 'formal_episode_script' else None))
        if row.get('preparation_kind') == 'formal_episode_script':
            try:
                with delegations.read() as conn:
                    frozen_specialists = [delegations._agent(conn, item['adoption_id'], item['revision'])
                                          for item in row['specialists']]
                specialists_ready = AgentRuns._formal_script_roster_ready(frozen_specialists)
            except OperationError as exc:
                if exc.code not in {'capability_denied', 'scope_violation', 'stale_input'}:
                    raise
                specialists_ready = False
            if not specialists_ready:
                row = await asyncio.to_thread(self.store.update, row['id'],
                    **self._key(), state='blocked', error_code='formal_script_specialists_missing')
                return await self._public(row)
        scope = {key: value for key, value in row['scope_snapshot']['scope'].items()
                 if key != 'canvas_id'}
        try:
            input_refs = (self._selected_asset_image_input_refs(row)
                          if 'asset.design.create' in allowed else [])
            body = {
                'canvas_id': self.canvas_id, 'conversation_id': self.conversation_id,
                'scope': scope,
                'agent': {'kind': 'adoption', 'adoption_id': row['speaker']['adoption_id'],
                          'revision': row['speaker']['adoption_revision']},
                'input_refs': input_refs, 'goal': row['goal'], 'allowed_operations': allowed,
                'acceptance': row['acceptance'], 'task_brief': row['user_message'],
            }
            if authorization is not None:
                body['execution_authorization'] = authorization
            preview = delegations.preview(body)
            require(_scope_fingerprint(preview['contract']['expanded_scope'])
                    == _scope_fingerprint(row['scope_snapshot']),
                    '选择内容或节点版本已变化', 'stale_input')
        except OperationError as exc:
            if exc.code in {'stale_input', 'scope_violation'}:
                row = await asyncio.to_thread(self.store.update, row['id'],
                    **self._key(), state='stale', error_code=exc.code)
                return await self._public(row)
            if exc.code == 'capability_denied':
                row = await asyncio.to_thread(self.store.update, row['id'],
                    **self._key(), state='blocked', error_code=exc.code)
                return await self._public(row)
            if exc.code in {'dependency_input_unavailable', 'invalid_input'}:
                row = await asyncio.to_thread(self.store.update, row['id'],
                    **self._key(), state='blocked', error_code=exc.code)
                return await self._public(row)
            raise

        receipt = delegations.receipt(row['delegation_request_id'])
        if receipt is None:
            payload = {**body, 'preview_proof': preview['proof']}
            receipt = await delegations.apply(DomainCommand(
                request_id=row['delegation_request_id'], action='delegation.create', payload=payload))
        row = await asyncio.to_thread(self.store.update, row['id'],
            **self._key(), state='delegated', delegation_id=receipt['delegation_id'])
        runs = await self._runs(current)
        run_receipt = await runs.receipt(row['run_request_id'])
        if run_receipt is None:
            run_receipt = await runs.start_with_pinned_specialists({
                'request_id': row['run_request_id'],
                'delegation_id': receipt['delegation_id'],
                'contract_hash': receipt['contract_hash'],
                'max_model_calls': row['max_model_calls'],
            }, row['specialists'])
        row = await asyncio.to_thread(self.store.update, row['id'],
            **self._key(), state='queued', run_id=run_receipt['run']['id'], error_code=None)
        await self._schedule_drive(runs, row['run_id'], background)
        return await self._public(row)

    async def authorize(self, identifier, body, *, background=None):
        exact(body, {'request_id', 'expected_revision', 'confirmed'})
        request_id = text(body['request_id'], 128)
        expected_revision = integer(body['expected_revision'], 1)
        require(type(body['confirmed']) is bool, '授权确认必须是布尔值')
        require(body['confirmed'], '请确认任务卡中列出的具体动作与影响', 'authorization_required')
        await self._fresh('editor')
        row = await asyncio.to_thread(self._get, identifier)
        require(row is not None, '协作任务不存在', 'scope_violation')
        action = row['requested_action']
        require(row['intent'] == 'execute_request' and isinstance(action, dict),
                '协作任务没有待授权动作', 'revision_conflict')
        require(action['node_id'] in row['scope_snapshot']['node_ids'],
                '请求动作超出固定选择范围', 'scope_violation')
        if not await self._scope_matches(row, self.ctx):
            row = await asyncio.to_thread(self.store.update, identifier,
                **self._key(), state='stale', error_code='stale_input')
            return await self._public(row)
        capability = 'node.run' if action['operation'] == 'node.run' else 'node.patch'
        require(capability in row['speaker']['profile'].get('capabilities', []),
                '固定专业成员没有该节点操作能力', 'capability_denied')
        node_operation = ('patch' if action['operation'] == 'node.patch'
                          else action['node_operation'])
        authorization = {
            'allowed_node_ids': [action['node_id']],
            'allowed_node_operations': [{'node_id': action['node_id'], 'operation': node_operation}],
            'max_node_runs': 1 if action['operation'] == 'node.run' else 0,
        }
        payload_hash = hashlib.sha256(_packed({
            'request_id': request_id, 'task_id': identifier,
            'expected_revision': expected_revision, 'confirmed': True,
            'authorization': authorization,
        }).encode('utf-8')).hexdigest()
        row = await asyncio.to_thread(self.store.authorize, identifier,
            **self._key(), expected_revision=expected_revision, request_id=request_id,
            authorization=authorization, payload_hash=payload_hash)
        if row['run_id']:
            return await self.resume_bridge(identifier, background=background)
        return await self.bridge(identifier, background=background)

    async def control(self, identifier, action, body, *, background=None):
        exact(body, {'request_id', 'expected_revision'}, {'continuation_id'})
        require(action in {'stop', 'resume'}, '未知运行控制操作')
        await self._fresh('editor')
        row = await asyncio.to_thread(self._get, identifier)
        require(row is not None, '协作任务不存在', 'scope_violation')
        request_id = text(body['request_id'], 128)
        expected_revision = integer(body['expected_revision'], 1)
        if row['run_id'] is None:
            if action == 'stop' and row['state'] == 'stopped':
                return await self._public(row)
            require('continuation_id' not in body,
                    '尚无可控制的运行续接', 'revision_conflict')
            require(row['revision'] == expected_revision,
                    '协作任务状态已变化', 'revision_conflict')
            if action == 'stop':
                require(row['state'] in {'bridging', 'delegated'},
                        '协作任务当前不能停止', 'revision_conflict')
                row = await asyncio.to_thread(self.store.update, identifier,
                    **self._key(), state='stopped')
                return await self._public(row)
            require(row['state'] in {'bridging', 'delegated', 'stopped'},
                    '协作任务当前不能恢复', 'revision_conflict')
            if row['state'] == 'stopped':
                row = await asyncio.to_thread(self.store.update, identifier,
                    **self._key(), state='bridging')
            return await self.resume_bridge(identifier, background=background)
        runs = await self._runs(self.ctx)
        continuations = await asyncio.to_thread(self.store.continuations,
            identifier, **self._key())
        selected_id = body.get('continuation_id')
        if selected_id is not None:
            selected_id = text(selected_id, 128)
            selected = next((item for item in continuations if item['id'] == selected_id), None)
            require(selected is not None, '续跑不属于此协作任务', 'scope_violation')
        else:
            selected = next((item for item in reversed(continuations)
                             if item['continuation_run_id']), None)

        def control_request_id(run_id, target):
            token = hashlib.sha256(f'{request_id}\0{identifier}\0{run_id}\0{action}\0{target}'.encode()).hexdigest()[:40]
            return f'task-{action}-{token}'

        root_run = await runs.get(row['run_id'])
        if action == 'stop':
            # Stop a selected/current task continuation first so its nested
            # node runs are fenced before touching the preparation root.
            if selected and selected['continuation_run_id']:
                continuation_run = await runs.get(selected['continuation_run_id'])
                if continuation_run['state'] not in {'delivered', 'partial', 'stopped', 'blocked', 'submission_unknown'}:
                    result = await runs.control(selected['continuation_run_id'], 'stop', {
                        'request_id': control_request_id(selected['continuation_run_id'], selected['id']),
                        'expected_revision': expected_revision,
                    })
                    await asyncio.to_thread(self.store.update_continuation, selected['id'],
                        **self._key(), state=result['run']['state'])
            elif selected and not selected['continuation_run_id'] and selected['state'] not in {
                    'stopped', 'submission_unknown', 'delivered', 'partial', 'blocked'}:
                require(row['revision'] == expected_revision,
                        '协作任务状态已变化', 'revision_conflict')
                await asyncio.to_thread(self.store.update_continuation, selected['id'],
                    **self._key(), state='stopped')
            if root_run['state'] not in {'delivered', 'partial', 'stopped', 'blocked', 'submission_unknown'}:
                result = await runs.control(row['run_id'], 'stop', {
                    'request_id': control_request_id(row['run_id'], 'root'),
                    'expected_revision': root_run['revision'],
                })
                await asyncio.to_thread(self.store.update, identifier,
                    **self._key(), state=result['run']['state'])
        else:
            root_resumed = False
            if root_run['state'] == 'stopped':
                can_resume, reason = _resume_status(root_run)
                require(can_resume, '当前运行不可安全恢复', reason or 'run_not_resumable')
                result = await runs.control(row['run_id'], 'resume', {
                    'request_id': control_request_id(row['run_id'], 'root'),
                    'expected_revision': root_run['revision'],
                })
                await self._schedule_drive(runs, row['run_id'], background)
                root_run = result['run']
                root_resumed = True
                await asyncio.to_thread(self.store.update, identifier,
                    **self._key(), state=root_run['state'])
            elif root_run['state'] == 'blocked':
                can_resume, reason = _resume_status(root_run)
                require(can_resume, '当前运行不可安全恢复', reason or 'run_not_resumable')
                result = await runs.control(row['run_id'], 'resume', {
                    'request_id': control_request_id(row['run_id'], 'root'),
                    'expected_revision': root_run['revision'],
                })
                await self._schedule_drive(runs, row['run_id'], background)
                root_run = result['run']
                root_resumed = True
                await asyncio.to_thread(self.store.update, identifier,
                    **self._key(), state=root_run['state'])
            if selected and selected['continuation_run_id']:
                continuation_run = await runs.get(selected['continuation_run_id'])
                if continuation_run['state'] == 'stopped':
                    result = await runs.control(selected['continuation_run_id'], 'resume', {
                        'request_id': control_request_id(selected['continuation_run_id'], selected['id']),
                        'expected_revision': expected_revision,
                    })
                    await asyncio.to_thread(self.store.update_continuation, selected['id'],
                        **self._key(), state=result['run']['state'])
                    await self._schedule_drive(runs, selected['continuation_run_id'], background)
                elif continuation_run['state'] in {'queued', 'applying', 'model_inflight', 'paused', 'waiting_children'}:
                    await self._schedule_drive(runs, selected['continuation_run_id'], background)
            elif selected and not selected['continuation_run_id'] and selected['state'] == 'stopped':
                require(row['revision'] == expected_revision,
                        '协作任务状态已变化', 'revision_conflict')
                await asyncio.to_thread(self.store.update_continuation, selected['id'],
                    **self._key(),
                    state='delegated' if selected['continuation_delegation_id'] else 'authorizing')
                task_row = await asyncio.to_thread(self._get, identifier)
                resumed = await asyncio.to_thread(self.store.continuation_by_candidate,
                    identifier, selected['source_operation_id'], selected['source_request_id'],
                    **self._key())
                await self._continue_candidate(task_row, resumed, background=background)
            elif selected and not selected['continuation_run_id'] and selected['state'] in {
                    'authorizing', 'delegated', 'starting'}:
                require(row['revision'] == expected_revision,
                        '协作任务状态已变化', 'revision_conflict')
                task_row = await asyncio.to_thread(self._get, identifier)
                await self._continue_candidate(task_row, selected, background=background)
            elif not selected:
                can_resume, reason = _resume_status(root_run)
                require(can_resume, '当前运行不可安全恢复', reason or 'run_not_resumable')
                if not root_resumed:
                    await self._schedule_drive(runs, row['run_id'], background)
        return await self.get(identifier)

    async def _scope_matches(self, row, ctx):
        try:
            current = await asyncio.to_thread(
                agent_context.graph_scope, ctx, self.canvas_id,
                {key: value for key, value in row['scope_snapshot']['scope'].items()
                 if key != 'canvas_id'})
        except Exception:
            return False
        return _scope_fingerprint(current) == _scope_fingerprint(row['scope_snapshot'])

    def _selected_asset_image_input_refs(self, row):
        """Freeze only verifiable image outputs already inside the user's selected scope."""
        from novelvideo.freezone.canvas_store import read_canvas
        from novelvideo.freezone.orchestration.models import frozen_node_result
        from .asset_generation import AssetGeneration

        snapshot = row['scope_snapshot']
        scope = snapshot.get('scope') if isinstance(snapshot, dict) else None
        selected_ids = snapshot.get('node_ids') if isinstance(snapshot, dict) else None
        require(isinstance(scope, dict) and scope.get('canvas_id') == self.canvas_id
                and isinstance(selected_ids, list) and all(isinstance(node_id, str) for node_id in selected_ids),
                '固定图片范围无效', 'scope_violation')
        frozen_nodes = snapshot.get('nodes')
        require(isinstance(frozen_nodes, list), '固定图片范围无效', 'scope_violation')
        canvas = read_canvas(self.ctx.state_dir, self.canvas_id)
        require(isinstance(canvas, dict) and canvas.get('project_id') == self.project_id,
                '固定图片画布不可用', 'scope_violation')
        current_nodes = {node.get('id'): node for node in canvas.get('nodes', [])
                         if isinstance(node, dict) and isinstance(node.get('id'), str)}
        refs = []
        for frozen in frozen_nodes:
            if (not isinstance(frozen, dict)
                    or frozen.get('node_id') not in selected_ids
                    or frozen.get('node_type') not in ASSET_IMAGE_REFERENCE_NODE_TYPES
                    or 'imageUrl' not in frozen.get('media_evidence', {}).get('fields', [])):
                continue
            node_id = frozen['node_id']
            node = current_nodes.get(node_id)
            require(node is not None and node.get('type') == frozen['node_type'],
                    '固定图片节点已变化', 'stale_input', node_id=node_id)
            image_url = frozen_node_result(node).get('imageUrl')
            if not isinstance(image_url, str) or not image_url.strip():
                continue
            # Check that this is a real same-project image before exposing its
            # node reference to the model. The downstream asset tool repeats
            # this verification and freezes the resulting bytes fingerprint.
            AssetGeneration._canvas_reference_fingerprint(
                self.ctx, self.project_id, self.canvas_id, node)
            refs.append({'kind': 'canvas_node', 'canvas_id': self.canvas_id, 'node_id': node_id})
        require(len(refs) <= 100, '所选范围包含过多图片参考，请缩小范围', 'invalid_input')
        return refs

    @staticmethod
    def _allowed_operations(row):
        capabilities = {capability for capability in
                        row['speaker']['profile'].get('capabilities', [])
                        if isinstance(capability, str)}
        if row.get('preparation_kind') == 'formal_episode_script':
            if 'context.read' not in capabilities:
                return []
            # The lead may read the frozen context and coordinate the formal
            # document workflow. Only separately pinned writer/reviewer runs
            # may create or review the versioned screenplay. Delegation-only
            # operations are minted by the trusted task bridge, never from
            # intake/model JSON.
            return ['context.read']
        allowed = PREPARATION_OPERATIONS.intersection(
            capabilities)
        # Asset-design preparation is a concrete reversible tool operation
        # carried by the existing media.propose capability. Delegations and
        # AgentTools enforce that mapping; it is never a node.run grant.
        if 'media.propose' in allowed:
            allowed = {*allowed, 'asset.design.create'}
        return sorted(allowed)

    @staticmethod
    def _requested_action_error(scope_snapshot, speaker, action):
        node_id = action['node_id']
        if node_id not in scope_snapshot['node_ids']:
            return 'scope_violation'
        capability = 'node.run' if action['operation'] == 'node.run' else 'node.patch'
        if capability not in speaker['profile'].get('capabilities', []):
            return 'capability_denied'
        node = next((item for item in scope_snapshot['nodes'] if item['node_id'] == node_id), None)
        if node is None:
            return 'scope_violation'
        if action['operation'] == 'node.patch' and not node.get('editable_fields'):
            return 'capability_denied'
        if action['operation'] == 'node.run':
            from novelvideo.freezone.orchestration.capabilities import catalog_by_type
            operations = catalog_by_type().get(node['node_type'], {}).get('operations', [])
            if action['node_operation'] not in {item['name'] for item in operations}:
                return 'capability_denied'
        return None

    async def _runs(self, ctx):
        async def resolve(role):
            current = await self.resolve_context(role)
            require(current.project_id == ctx.project_id
                    and current.requester_user_id == ctx.requester_user_id
                    and current.state_dir.resolve() == ctx.state_dir.resolve()
                    and current.output_dir.resolve() == ctx.output_dir.resolve(),
                    '协作任务项目身份已变化', 'scope_violation')
            return current

        return AgentRuns(ctx, username=self.username, canvas_id=self.canvas_id,
                         conversation_id=self.conversation_id, resolve_context=resolve)

    async def _schedule_drive(self, runs, run_id, background):
        run = await runs.get(run_id)
        if run['state'] in {'delivered', 'partial', 'stopped', 'blocked', 'submission_unknown'}:
            return
        if background is None:
            await runs.drive(run_id)
        else:
            background.add_task(runs.drive, run_id)


class AgentPlanDrafts:
    """Durable, text-only review snapshots for natural-language Agent requests."""

    def __init__(self, ctx, *, username, canvas_id, conversation_id, resolve_context):
        self.ctx = ctx
        self.username = text(username, 200)
        self.canvas_id = text(canvas_id, 128)
        self.conversation_id = text(conversation_id, 64)
        self.owner_id = str(ctx.requester_user_id)
        self.project_id = str(ctx.project_id)
        self.resolve_context = resolve_context
        self.store = CollaborationStore(Path(ctx.state_dir) / 'creation' / 'objects.sqlite3')

    def _key(self):
        return {'owner_id': self.owner_id, 'project_id': self.project_id,
                'canvas_id': self.canvas_id, 'conversation_id': self.conversation_id}

    async def _fresh(self, role='viewer'):
        try:
            current = await self.resolve_context(role)
        except HTTPException as exc:
            raise OperationError('scope_violation', 'Agent 计划访问范围已失效') from exc
        require(current.project_id == self.project_id
                and str(current.requester_user_id) == self.owner_id
                and Path(current.state_dir).resolve() == Path(self.ctx.state_dir).resolve()
                and Path(current.output_dir).resolve() == Path(self.ctx.output_dir).resolve(),
                'Agent 计划项目身份已变化', 'scope_violation')
        agent_context.conversation(current, self.username, self.canvas_id, self.conversation_id)
        return current

    @staticmethod
    def _public(saved):
        plan = saved['plan']
        return {**plan, 'id': saved['id'], 'revision': saved['revision'],
                'state': saved['state'], 'run_id': saved['run_id'],
                'created_at': saved['created_at'], 'updated_at': saved['updated_at']}

    @staticmethod
    def _hash(value):
        return hashlib.sha256(_packed(value).encode('utf-8')).hexdigest()

    @staticmethod
    def _agent_id(speaker):
        profile = speaker.get('profile') or {}
        source = profile.get('source') if isinstance(profile.get('source'), dict) else {}
        ref = source.get('ref') if isinstance(source.get('ref'), dict) else {}
        candidate = profile.get('id') or profile.get('agent_id') or ref.get('id')
        return candidate if isinstance(candidate, str) else ''

    async def preview(self, body):
        exact(body, {'request_id', 'canvas_id', 'conversation_id', 'message', 'selection'},
              {'target_adoption_id'})
        require(body['canvas_id'] == self.canvas_id
                and body['conversation_id'] == self.conversation_id,
                'Agent 计划不属于当前画布会话', 'scope_violation')
        request_id = text(body['request_id'], 128)
        message = text(body['message'], 12000)
        selection = body['selection']
        request_hash = self._hash({key: body[key] for key in sorted(body)})
        previous = await asyncio.to_thread(self.store.get_plan_by_request, request_id, **self._key())
        if previous is not None:
            require(previous['plan'].get('source_payload_hash') == request_hash,
                    '计划请求编号已用于不同内容', 'idempotency_conflict')
            return self._public(previous)

        current = await self._fresh('viewer')
        from .collaboration import Collaboration
        from .discussions import DiscussionIntake, ROLE, _system_prompt_for_prompt, availability, run_message

        speaker = await asyncio.to_thread(Collaboration(
            current, username=self.username, canvas_id=self.canvas_id,
            conversation_id=self.conversation_id).resolve_speaker, body.get('target_adoption_id'))
        scope_snapshot = await asyncio.to_thread(
            agent_context.graph_scope, current, self.canvas_id, selection)
        scope_snapshot['conversation_id'] = self.conversation_id
        members = await asyncio.to_thread(Collaboration(
            current, username=self.username, canvas_id=self.canvas_id,
            conversation_id=self.conversation_id).group)
        specialists = [{'adoption_id': item['adoption_id'], 'revision': item['adoption_revision']}
                       for item in members['members'] if item['adoption_id'] != speaker['adoption_id']]
        runtime = availability(current)
        if not runtime.get('available') or not runtime.get('route'):
            raise OperationError('runtime_unavailable', '当前讨论模型暂不可用，无法准备 Agent 计划')
        prompt = _packed({
            'canvas_text': '',
            'selection_context': {'scope_kind': selection.get('kind'), 'node_ids': scope_snapshot['node_ids']},
            'recent_discussion': [], 'current_user_message': message,
            'current_speaker': {'kind': speaker['kind'], 'adoption_id': speaker['adoption_id'],
                'adoption_revision': speaker['adoption_revision'], 'profile': speaker['profile']},
        })
        intake = DiscussionIntake.model_validate(await run_message(
            current, runtime['route'], prompt, request_id=request_id))

        from .agent_skill_context import task_skill_context
        from .collaboration_tasks import PREPARATION_OPERATIONS, MAX_TASK_MODEL_CALLS
        fixed_id = self._agent_id(speaker)
        allowed = sorted(set(speaker['profile'].get('capabilities', [])) & PREPARATION_OPERATIONS)
        if 'media.propose' in allowed:
            allowed.append('asset.design.create')
        if intake.intent == 'execute_request':
            permission = 'media' if intake.requested_action.operation == 'node.run' else 'project_edit'
            goal = intake.requested_action.effect
            acceptance = [goal]
            operation = intake.requested_action.operation
        else:
            permission = 'text'
            goal = message
            acceptance = intake.acceptance or ['返回可供创作者审阅的文本候选。']
            operation = 'proposal.create'
        # Preview the writing Skill under the text-writer role. This is a
        # recommendation only; the saved execution grant remains the
        # speaker's capability set and is validated again at confirmation.
        skill_contract = {
            'goal': goal, 'allowed_operations': sorted(set(allowed) | {'script.document.draft'}),
            'delegable_operations': [],
            'agent': {'fixed': {'id': fixed_id or 'builtin:text-writer'}},
        }
        skill_manifest = task_skill_context(skill_contract)
        frozen_inputs = [{
            'kind': 'canvas_node', 'canvas_id': self.canvas_id, 'node_id': node['node_id'],
            'canvas_revision': scope_snapshot['observed_canvas_revision'],
            'input_fingerprint': node['input_fingerprint'],
        } for node in scope_snapshot['nodes']]
        plan = {
            'source_request_id': request_id, 'source_payload_hash': request_hash,
            'message': message, 'intent': intake.intent, 'required_permission': permission,
            'scope': {**scope_snapshot['scope'], 'node_ids': scope_snapshot['node_ids'],
                      'observed_canvas_revision': scope_snapshot['observed_canvas_revision']},
            'frozen_inputs': frozen_inputs,
            'role_assignments': [{'adoption_id': speaker['adoption_id'],
                'revision': speaker['adoption_revision'], 'kind': speaker['kind'],
                'label': speaker['profile'].get('name', '项目主创')}],
            'specialists': specialists,
            'tasks': [{
                'task_key': 'main', 'goal': goal, 'acceptance': acceptance,
                'role': {'adoption_id': speaker['adoption_id'],
                         'revision': speaker['adoption_revision'],
                         'label': speaker['profile'].get('name', '项目主创')},
                'skills': [{'id': item['id'],
                            'documents': [{'path': doc['path'], 'sha256': doc['sha256']}
                                          for doc in item['documents']]}
                           for item in skill_manifest['skills']],
                'skill_manifest': skill_manifest['receipt'],
                'depends_on': [], 'outputs': [{'type': 'candidate', 'target': 'canvas'}],
                'operation': operation, 'permission': permission,
            }],
            'max_model_calls': MAX_TASK_MODEL_CALLS,
            'speaker': speaker, 'scope_snapshot': scope_snapshot,
        }
        identifier = 'plan-' + self._hash([self.owner_id, self.project_id, self.canvas_id,
                                            self.conversation_id, request_id])[:32]
        saved, _created = await asyncio.to_thread(self.store.create_plan,
            **self._key(), identifier=identifier, request_id=request_id,
            payload_hash=request_hash, plan=plan)
        return self._public(saved)

    async def get(self, identifier):
        await self._fresh('viewer')
        saved = await asyncio.to_thread(self.store.get_plan, identifier, **self._key())
        require(saved is not None, 'Agent 计划草稿不存在', 'scope_violation')
        return self._public(saved)

    async def edit(self, identifier, body):
        exact(body, {'request_id', 'expected_revision', 'tasks'})
        request_id = text(body['request_id'], 128)
        expected_revision = integer(body['expected_revision'], 1)
        await self._fresh('editor')
        saved = await asyncio.to_thread(self.store.get_plan, identifier, **self._key())
        require(saved is not None, 'Agent 计划草稿不存在', 'scope_violation')
        original = saved['plan']
        tasks = _validated_plan_dag(body['tasks'])
        allowed_fields = {'task_key', 'goal', 'acceptance', 'role', 'skills', 'skill_manifest',
                          'depends_on', 'outputs', 'operation', 'permission'}
        base_task = original['tasks'][0]
        updated_tasks = []
        for edited in tasks:
            require(set(edited) == allowed_fields, '计划任务结构无效', 'invalid_input')
            # Roles, Skills, and operations are fixed to the selected execution
            # identity. New DAG nodes inherit the same pinned task contract.
            for field in ('role', 'skills', 'skill_manifest', 'operation'):
                require(edited.get(field) == base_task.get(field),
                        '当前编辑器不能更换已固定的职责、Skill 或操作', 'invalid_input')
            require(isinstance(edited.get('goal'), str) and edited['goal'].strip()
                    and isinstance(edited.get('acceptance'), list)
                    and all(isinstance(item, str) and item.strip() for item in edited['acceptance'])
                    and isinstance(edited.get('outputs'), list)
                    and all(isinstance(item, dict) and isinstance(item.get('type'), str)
                            and isinstance(item.get('target'), str) for item in edited['outputs']),
                    '任务目标、验收条件或输出格式无效', 'invalid_input')
            updated_tasks.append({**base_task, **edited})
        permissions = {task['permission'] for task in updated_tasks}
        required_permission = ('media' if 'media' in permissions else
                               'project_edit' if 'project_edit' in permissions else 'text')
        updated = {**original, 'tasks': updated_tasks,
                   'required_permission': required_permission}
        payload_hash = self._hash({'expected_revision': expected_revision, 'tasks': tasks})
        saved = await asyncio.to_thread(self.store.edit_plan, identifier,
            **self._key(), expected_revision=expected_revision, request_id=request_id,
            payload_hash=payload_hash, plan=updated)
        return self._public(saved)

    async def confirm(self, identifier, body, *, background=None):
        exact(body, {'request_id', 'expected_revision', 'confirmed', 'permission'})
        request_id = text(body['request_id'], 128)
        expected_revision = integer(body['expected_revision'], 1)
        require(type(body['confirmed']) is bool and body['confirmed'],
                '请明确确认文本计划', 'authorization_required')
        require(body['permission'] == 'text', '文本确认不包含媒体或项目写入授权',
                'media_confirmation_required')
        await self._fresh('editor')
        saved = await asyncio.to_thread(self.store.get_plan, identifier, **self._key())
        require(saved is not None, 'Agent 计划草稿不存在', 'scope_violation')
        plan = saved['plan']
        confirm_hash = self._hash({'request_id': request_id, 'expected_revision': expected_revision,
                                   'permission': 'text', 'plan_revision': saved['revision']})
        if saved['state'] == 'confirmed':
            require(saved.get('confirm_request_id') == request_id
                    and saved.get('confirm_payload_hash') == confirm_hash,
                    '计划已由其他请求确认', 'revision_conflict')
            return self._public(saved)
        require(saved['revision'] == expected_revision, '计划已变化', 'revision_conflict')
        ordered_tasks = _validated_plan_dag(plan['tasks'])
        require(plan['required_permission'] == 'text'
                and all(task.get('permission') == 'text'
                        and task.get('operation') in {'context.read', 'proposal.create', 'ontology.definition.draft'}
                        and all(isinstance(output, dict) and output.get('type') == 'candidate'
                                for output in task.get('outputs', []))
                        for task in ordered_tasks),
                '文本确认不能授权媒体或项目写入任务', 'media_confirmation_required')
        current = await self._fresh('editor')
        frozen_scope_request = {key: value for key, value in plan['scope_snapshot']['scope'].items()
                                if key != 'canvas_id'}
        latest_scope = await asyncio.to_thread(agent_context.graph_scope, current, self.canvas_id,
                                               frozen_scope_request)
        latest_inputs = [{'node_id': node['node_id'], 'input_fingerprint': node['input_fingerprint']}
                         for node in latest_scope['nodes']]
        frozen = [{'node_id': item['node_id'], 'input_fingerprint': item['input_fingerprint']}
                  for item in plan['frozen_inputs']]
        require(latest_scope['observed_canvas_revision'] == plan['scope']['observed_canvas_revision']
                and latest_inputs == frozen, '画布输入版本已变化，请重新预览计划', 'stale_input')

        from types import SimpleNamespace
        execution_goal = '依赖顺序计划草稿（由单个 coordinator 文本任务执行，不进行节点级并行调度）：\n' + '\n'.join(
            f"{index}. [{task['task_key']}] {task['goal']}"
            + (f"（依赖：{'、'.join(task['depends_on'])}）" if task['depends_on'] else '')
            for index, task in enumerate(ordered_tasks, start=1))
        execution_acceptance = [f"[{task['task_key']}] {criterion}"
                                for task in ordered_tasks for criterion in task['acceptance']]
        intake = {'intent': 'prepare', 'message': execution_goal,
                  'acceptance': execution_acceptance}
        task_request_id = f"agent-plan:{identifier}:confirmed"
        task_hash = self._hash({'plan_id': identifier, 'plan_revision': expected_revision,
                                'plan': plan, 'permission': 'text'})
        tasks = CollaborationTasks(current, username=self.username, canvas_id=self.canvas_id,
            conversation_id=self.conversation_id, resolve_context=self.resolve_context)
        task = await tasks.accept_intake(request=SimpleNamespace(
                request_id=task_request_id, message=execution_goal), request_hash=task_hash,
            intake=intake, speaker=plan['speaker'], specialists=plan['specialists'],
            scope_snapshot=plan['scope_snapshot'],
            task_id=_task_identifier(self.owner_id, self.project_id, self.canvas_id,
                                     self.conversation_id, task_request_id),
            background=background)
        confirmed = await asyncio.to_thread(self.store.confirm_plan, identifier,
            **self._key(), expected_revision=expected_revision, request_id=request_id,
            payload_hash=confirm_hash, run_id=task.get('run_id'))
        result = self._public(confirmed)
        result['task_id'] = task['id']
        return result

