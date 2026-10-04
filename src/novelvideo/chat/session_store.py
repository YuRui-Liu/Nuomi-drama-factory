"""Persistent assistant identity and generation fencing, independent of models."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
import hashlib
import json
import os
import sqlite3
import uuid


class SessionConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class SessionState:
    session_id: str
    generation: int
    backend: str | None = None
    provider_session_id: str | None = None
    mode: str | None = None
    status: str = 'ready'


class SessionStore:
    def __init__(self, db_path, *, migration_required=False, legacy_history=None):
        self.db_path = Path(db_path)
        self.migration_required = migration_required
        self.legacy_history = legacy_history

    @contextmanager
    def transaction(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path, timeout=10)
        try:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS assistant_session (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS assistant_clears (request_id TEXT PRIMARY KEY, generation INTEGER NOT NULL, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS assistant_turns (turn_id TEXT PRIMARY KEY, generation INTEGER NOT NULL, status TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS assistant_waiters (seq INTEGER PRIMARY KEY AUTOINCREMENT, ticket TEXT UNIQUE NOT NULL, generation INTEGER NOT NULL, pid INTEGER NOT NULL)')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _get(self, db):
        row = db.execute('SELECT data FROM assistant_session WHERE id=1').fetchone()
        if row:
            return SessionState(**json.loads(row[0]))
        if self.legacy_history:
            # Copy only proven user/scope history, under the identity creation
            # transaction. Never mutate the source or repeat after a clear.
            legacy = sqlite3.connect(f'{self.legacy_history.as_uri()}?mode=ro', uri=True)
            try:
                for table in ('chat_messages', 'chat_ui_events'):
                    schema = legacy.execute('SELECT sql FROM sqlite_master WHERE type=\'table\' AND name=?', (table,)).fetchone()
                    if not schema:
                        continue
                    if db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
                        continue
                    db.execute(schema[0])
                    rows = legacy.execute(f'SELECT * FROM {table}').fetchall()
                    if rows:
                        marks = ','.join('?' for _ in rows[0])
                        db.executemany(f'INSERT INTO {table} VALUES ({marks})', rows)
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='chat_messages'").fetchone():
                    history = [{'role': role, 'content': content} for role, content in db.execute(
                        "SELECT role,content FROM chat_messages WHERE role IN ('user','assistant') ORDER BY id")]
                    db.execute('CREATE TABLE IF NOT EXISTS assistant_legacy_context (data TEXT NOT NULL)')
                    db.execute('INSERT INTO assistant_legacy_context VALUES (?)', (json.dumps(history, ensure_ascii=False),))
            finally:
                legacy.close()
        state = SessionState(str(uuid.uuid4()), 1, status='migration_required' if self.migration_required else 'ready')
        self._put(db, state)
        return state

    @staticmethod
    def _put(db, state):
        db.execute('INSERT OR REPLACE INTO assistant_session VALUES (1,?)', (json.dumps(asdict(state)),))

    def get_or_create(self):
        with self.transaction() as db:
            return self._get(db)

    def migrated_context(self):
        with self.transaction() as db:
            self._get(db)
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_legacy_context'").fetchone():
                return []
            row = db.execute('SELECT data FROM assistant_legacy_context LIMIT 1').fetchone()
            return json.loads(row[0]) if row else []

    def require_current(self, session_id, generation):
        state = self.get_or_create()
        if (state.session_id, state.generation) != (session_id, generation):
            raise SessionConflict('会话已清除，请刷新当前项目对话。')
        return state

    def begin_turn(self, turn_id, generation):
        with self.transaction() as db:
            if db.execute('SELECT 1 FROM assistant_turns WHERE turn_id=?', (turn_id,)).fetchone():
                raise SessionConflict('该轮对话已提交，不能重复执行；请刷新查看结果。')
            if self._get(db).generation != generation:
                raise SessionConflict('会话已清除。')
            # A process lost during an external tool call cannot be replayed
            # safely. Require explicit clearing instead of guessing its result.
            if db.execute("SELECT 1 FROM assistant_turns WHERE generation=? AND status IN ('running','cancel_requested')", (generation,)).fetchone():
                raise SessionConflict('上一轮执行状态未知，请核对项目任务后手动清除会话。')
            db.execute('INSERT INTO assistant_turns VALUES (?,?,?)', (turn_id, generation, 'running'))

    def enqueue(self, state, ticket):
        with self.transaction() as db:
            if self._get(db) != state:
                current = self._get(db)
                if (current.session_id, current.generation) != (state.session_id, state.generation):
                    raise SessionConflict('会话已清除。')
            db.execute('INSERT INTO assistant_waiters(ticket,generation,pid) VALUES (?,?,?)', (ticket, state.generation, os.getpid()))
            return ticket

    def is_first(self, ticket, generation):
        import portalocker
        with self.transaction() as db:
            for (queued,) in db.execute('SELECT ticket FROM assistant_waiters WHERE generation=? ORDER BY seq', (generation,)).fetchall():
                owner_path = self.db_path.parent / f'{queued}.owner.lock'
                alive = False
                if owner_path.is_file():
                    with owner_path.open('r+b') as owner:
                        try:
                            portalocker.lock(owner, portalocker.LOCK_EX | portalocker.LOCK_NB)
                        except portalocker.exceptions.LockException:
                            alive = True
                        else:
                            portalocker.unlock(owner)
                if alive:
                    return queued == ticket
                db.execute('DELETE FROM assistant_waiters WHERE ticket=?', (queued,))
            return False

    def dequeue(self, ticket):
        with self.transaction() as db:
            db.execute('DELETE FROM assistant_waiters WHERE ticket=?', (ticket,))

    def request_cancel(self, generation):
        with self.transaction() as db:
            return bool(db.execute("UPDATE assistant_turns SET status='cancel_requested' WHERE generation=? AND status='running'", (generation,)).rowcount)

    def turn_cancelled(self, turn_id):
        with self.transaction() as db:
            return bool(db.execute("SELECT 1 FROM assistant_turns WHERE turn_id=? AND status='cancel_requested'", (turn_id,)).fetchone())

    def finish_turn(self, turn_id, status):
        with self.transaction() as db:
            db.execute('UPDATE assistant_turns SET status=? WHERE turn_id=?', (status, turn_id))

    def bind_provider(self, session_id, generation, backend, provider_session_id, mode):
        with self.transaction() as db:
            old = self._get(db)
            if (old.session_id, old.generation) != (session_id, generation):
                raise SessionConflict('会话已清除，旧任务不能继续。')
            if old.backend and old.backend != backend:
                raise SessionConflict('助手运行时已变更，请手动清除当前项目会话后生效。')
            if old.provider_session_id and provider_session_id and old.provider_session_id != provider_session_id:
                raise SessionConflict('供应商返回了不同会话，请重试恢复或手动清除会话。')
            new = SessionState(session_id, generation, backend,
                               provider_session_id or old.provider_session_id, mode, old.status)
            self._put(db, new)
            return new

    def clear(self, *, expected_generation, request_id):
        if not request_id or len(request_id) > 128:
            raise ValueError('invalid clear request ID')
        with self.transaction() as db:
            found = db.execute('SELECT generation,data FROM assistant_clears WHERE request_id=?', (request_id,)).fetchone()
            if found:
                if found[0] != expected_generation:
                    raise SessionConflict('清除请求 ID 已被其他操作使用。')
                return SessionState(**json.loads(found[1]))
            old = self._get(db)
            if old.generation != expected_generation:
                raise SessionConflict('会话版本已改变，请刷新后重试。')
            new = SessionState(str(uuid.uuid4()), old.generation + 1)
            self._put(db, new)
            for table in ('chat_messages', 'chat_ui_events', 'assistant_legacy_context'):
                if db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
                    db.execute(f'DELETE FROM {table}')
            db.execute('INSERT INTO assistant_clears VALUES (?,?,?)',
                       (request_id, expected_generation, json.dumps(asdict(new))))
            return new


def for_scope(username, scope_kind='home', project_id=None):
    if not username or username in {'.', '..'} or '/' in username or '\\' in username:
        raise ValueError('invalid username')
    if scope_kind not in {'home', 'project'} or (scope_kind == 'project' and not project_id):
        raise ValueError('invalid assistant scope')
    root = Path(os.environ.get('NOVELVIDEO_STATE_DIR') or Path(__file__).resolve().parents[3] / 'state')
    key = hashlib.sha256(json.dumps([scope_kind, project_id if scope_kind == 'project' else None]).encode()).hexdigest()
    from .session_migration import needs_confirmation, scoped_history
    return SessionStore(root / username / '.assistant-sessions' / key / 'chat.db',
                        migration_required=needs_confirmation(root / username, scope_kind, project_id),
                        legacy_history=scoped_history(root / username, scope_kind, project_id))
