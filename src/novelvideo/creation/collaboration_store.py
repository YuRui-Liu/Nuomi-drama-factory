"""Conversation-scoped, explicitly managed professional discussion members."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


class CollaborationStore:
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
        conn.execute('''CREATE TABLE IF NOT EXISTS creation_collaboration_members (
            owner_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            canvas_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            adoption_id TEXT NOT NULL,
            adoption_revision INTEGER NOT NULL CHECK(adoption_revision >= 1),
            profile TEXT NOT NULL,
            binding_revision INTEGER NOT NULL CHECK(binding_revision >= 1),
            active INTEGER NOT NULL CHECK(active IN (0,1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(owner_id, project_id, canvas_id, conversation_id, adoption_id)
        )''')
        conn.execute('''CREATE INDEX IF NOT EXISTS creation_collaboration_scope
            ON creation_collaboration_members(owner_id, project_id, canvas_id, conversation_id, active)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS creation_agent_plan_drafts (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            canvas_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            source_request_id TEXT NOT NULL,
            source_payload_hash TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK(revision >= 1),
            state TEXT NOT NULL CHECK(state IN ('draft','confirmed')),
            content TEXT NOT NULL,
            last_edit_request_id TEXT,
            last_edit_payload_hash TEXT,
            confirm_request_id TEXT,
            confirm_payload_hash TEXT,
            run_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(owner_id, project_id, canvas_id, conversation_id, source_request_id)
        )''')

    @staticmethod
    def _scope(owner_id, project_id, canvas_id, conversation_id):
        return (str(owner_id), str(project_id), str(canvas_id), str(conversation_id))

    @staticmethod
    def _plan(row):
        if row is None:
            return None
        return {
            'id': row['id'], 'revision': row['revision'], 'state': row['state'],
            'source_request_id': row['source_request_id'],
            'plan': json.loads(row['content']), 'run_id': row['run_id'],
            'confirm_request_id': row['confirm_request_id'],
            'confirm_payload_hash': row['confirm_payload_hash'],
            'created_at': row['created_at'], 'updated_at': row['updated_at'],
        }

    def create_plan(self, *, owner_id, project_id, canvas_id, conversation_id,
                    identifier, request_id, payload_hash, plan):
        scope = self._scope(owner_id, project_id, canvas_id, conversation_id)
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        content = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        conn = self._connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            self._create_schema(conn)
            row = conn.execute('''SELECT * FROM creation_agent_plan_drafts
                WHERE owner_id=? AND project_id=? AND canvas_id=? AND conversation_id=?
                  AND source_request_id=?''', (*scope, request_id)).fetchone()
            if row is not None:
                if row['source_payload_hash'] != payload_hash:
                    from .operations import OperationError
                    raise OperationError('idempotency_conflict', '计划请求编号已用于不同内容')
                conn.commit()
                return self._plan(row), False
            conn.execute('''INSERT INTO creation_agent_plan_drafts
                (id,owner_id,project_id,canvas_id,conversation_id,source_request_id,source_payload_hash,
                 revision,state,content,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                (identifier, *scope, request_id, payload_hash, 1, 'draft', content, now, now))
            saved = conn.execute('SELECT * FROM creation_agent_plan_drafts WHERE id=?', (identifier,)).fetchone()
            conn.commit()
            return self._plan(saved), True
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_plan(self, identifier, *, owner_id, project_id, canvas_id, conversation_id):
        conn = self._connect(readonly=True)
        if conn is None:
            return None
        try:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                  ('creation_agent_plan_drafts',)).fetchone()
            if not exists:
                return None
            row = conn.execute('''SELECT * FROM creation_agent_plan_drafts
                WHERE id=? AND owner_id=? AND project_id=? AND canvas_id=? AND conversation_id=?''',
                (identifier, *self._scope(owner_id, project_id, canvas_id, conversation_id))).fetchone()
            return self._plan(row)
        finally:
            conn.close()

    def get_plan_by_request(self, request_id, *, owner_id, project_id, canvas_id, conversation_id):
        conn = self._connect(readonly=True)
        if conn is None:
            return None
        try:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                  ('creation_agent_plan_drafts',)).fetchone()
            if not exists:
                return None
            row = conn.execute('''SELECT * FROM creation_agent_plan_drafts
                WHERE owner_id=? AND project_id=? AND canvas_id=? AND conversation_id=?
                  AND source_request_id=?''',
                (*self._scope(owner_id, project_id, canvas_id, conversation_id), request_id)).fetchone()
            return self._plan(row)
        finally:
            conn.close()

    def edit_plan(self, identifier, *, owner_id, project_id, canvas_id, conversation_id,
                  expected_revision, request_id, payload_hash, plan):
        scope = self._scope(owner_id, project_id, canvas_id, conversation_id)
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        content = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        conn = self._connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            self._create_schema(conn)
            row = conn.execute('''SELECT * FROM creation_agent_plan_drafts
                WHERE id=? AND owner_id=? AND project_id=? AND canvas_id=? AND conversation_id=?''',
                (identifier, *scope)).fetchone()
            from .operations import OperationError
            if row is None:
                raise OperationError('scope_violation', 'Agent 计划草稿不存在')
            if row['last_edit_request_id'] == request_id:
                if row['last_edit_payload_hash'] != payload_hash:
                    raise OperationError('idempotency_conflict', '编辑请求编号已用于不同计划内容')
                conn.commit()
                return self._plan(row)
            if row['state'] != 'draft' or row['revision'] != expected_revision:
                raise OperationError('revision_conflict', '计划已变化或已确认')
            conn.execute('''UPDATE creation_agent_plan_drafts SET revision=revision+1,content=?,
                last_edit_request_id=?,last_edit_payload_hash=?,updated_at=? WHERE id=?''',
                (content, request_id, payload_hash, now, identifier))
            saved = conn.execute('SELECT * FROM creation_agent_plan_drafts WHERE id=?', (identifier,)).fetchone()
            conn.commit()
            return self._plan(saved)
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def confirm_plan(self, identifier, *, owner_id, project_id, canvas_id, conversation_id,
                     expected_revision, request_id, payload_hash, run_id):
        scope = self._scope(owner_id, project_id, canvas_id, conversation_id)
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        conn = self._connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            self._create_schema(conn)
            row = conn.execute('''SELECT * FROM creation_agent_plan_drafts
                WHERE id=? AND owner_id=? AND project_id=? AND canvas_id=? AND conversation_id=?''',
                (identifier, *scope)).fetchone()
            from .operations import OperationError
            if row is None:
                raise OperationError('scope_violation', 'Agent 计划草稿不存在')
            if row['state'] == 'confirmed':
                if row['confirm_request_id'] != request_id or row['confirm_payload_hash'] != payload_hash:
                    raise OperationError('revision_conflict', '计划已由其他请求确认')
                conn.commit()
                return self._plan(row)
            if row['revision'] != expected_revision:
                raise OperationError('revision_conflict', '计划已变化')
            conn.execute('''UPDATE creation_agent_plan_drafts SET state='confirmed',
                confirm_request_id=?,confirm_payload_hash=?,run_id=?,updated_at=? WHERE id=?''',
                (request_id, payload_hash, run_id, now, identifier))
            saved = conn.execute('SELECT * FROM creation_agent_plan_drafts WHERE id=?', (identifier,)).fetchone()
            conn.commit()
            return self._plan(saved)
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _member(row):
        if row is None:
            return None
        return {
            'kind': 'member',
            'binding_revision': row['binding_revision'],
            'adoption_id': row['adoption_id'],
            'adoption_revision': row['adoption_revision'],
            'profile': json.loads(row['profile']),
            'active': bool(row['active']),
            'created_at': row['created_at'],
            'updated_at': row['updated_at'],
        }

    def members(self, owner_id, project_id, canvas_id, conversation_id):
        conn = self._connect(readonly=True)
        if conn is None:
            return []
        try:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                  ('creation_collaboration_members',)).fetchone()
            if not exists:
                return []
            scope = self._scope(owner_id, project_id, canvas_id, conversation_id)
            rows = conn.execute('''SELECT * FROM creation_collaboration_members
                WHERE owner_id=? AND project_id=? AND canvas_id=? AND conversation_id=? AND active=1
                ORDER BY created_at, adoption_id''', scope).fetchall()
            return [self._member(row) for row in rows]
        finally:
            conn.close()

    def member(self, owner_id, project_id, canvas_id, conversation_id, adoption_id):
        conn = self._connect(readonly=True)
        if conn is None:
            return None
        try:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                  ('creation_collaboration_members',)).fetchone()
            if not exists:
                return None
            row = conn.execute('''SELECT * FROM creation_collaboration_members
                WHERE owner_id=? AND project_id=? AND canvas_id=? AND conversation_id=?
                  AND adoption_id=? AND active=1''',
                (*self._scope(owner_id, project_id, canvas_id, conversation_id), adoption_id)).fetchone()
            return self._member(row)
        finally:
            conn.close()

    def join(self, owner_id, project_id, canvas_id, conversation_id,
             adoption_id, adoption_revision, profile):
        scope = self._scope(owner_id, project_id, canvas_id, conversation_id)
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        profile_json = json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        conn = self._connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            self._create_schema(conn)
            key = (*scope, adoption_id)
            row = conn.execute('''SELECT * FROM creation_collaboration_members
                WHERE owner_id=? AND project_id=? AND canvas_id=? AND conversation_id=? AND adoption_id=?''',
                key).fetchone()
            if (row and row['active'] and row['adoption_revision'] == adoption_revision
                    and row['profile'] == profile_json):
                conn.commit()
                return self._member(row)
            binding_revision = (row['binding_revision'] + 1) if row else 1
            created_at = row['created_at'] if row else now
            conn.execute('''INSERT INTO creation_collaboration_members
                (owner_id,project_id,canvas_id,conversation_id,adoption_id,adoption_revision,profile,
                 binding_revision,active,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,1,?,?)
                ON CONFLICT(owner_id,project_id,canvas_id,conversation_id,adoption_id) DO UPDATE SET
                    adoption_revision=excluded.adoption_revision, profile=excluded.profile,
                    binding_revision=excluded.binding_revision, active=1, updated_at=excluded.updated_at''',
                (*key, adoption_revision, profile_json, binding_revision, created_at, now))
            saved = conn.execute('''SELECT * FROM creation_collaboration_members
                WHERE owner_id=? AND project_id=? AND canvas_id=? AND conversation_id=? AND adoption_id=?''',
                key).fetchone()
            conn.commit()
            return self._member(saved)
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def leave(self, owner_id, project_id, canvas_id, conversation_id, adoption_id):
        conn = self._connect(readonly=True)
        if conn is None:
            return None
        try:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                  ('creation_collaboration_members',)).fetchone()
            if not exists:
                return None
        finally:
            conn.close()

        key = (*self._scope(owner_id, project_id, canvas_id, conversation_id), adoption_id)
        conn = self._connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('''SELECT * FROM creation_collaboration_members
                WHERE owner_id=? AND project_id=? AND canvas_id=? AND conversation_id=?
                  AND adoption_id=?''', key).fetchone()
            if row is None:
                conn.commit()
                return None
            if not row['active']:
                conn.commit()
                return self._member(row)
            now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            conn.execute('''UPDATE creation_collaboration_members SET active=0,
                binding_revision=binding_revision+1, updated_at=?
                WHERE owner_id=? AND project_id=? AND canvas_id=? AND conversation_id=?
                  AND adoption_id=? AND active=1''', (now, *key))
            saved = conn.execute('''SELECT * FROM creation_collaboration_members
                WHERE owner_id=? AND project_id=? AND canvas_id=? AND conversation_id=? AND adoption_id=?''', key).fetchone()
            conn.commit()
            return self._member(saved)
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
