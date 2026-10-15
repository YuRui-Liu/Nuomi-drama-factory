"""A user/scope-owned index; each conversation has its own history and turn lock."""
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
import sqlite3
import hashlib
import json
from uuid import uuid4

from .session_store import for_scope


class ConversationStore:
    def __init__(self, username, scope_kind='home', project_id=None):
        self.username, self.scope_kind, self.project_id = username, scope_kind, project_id
        self.default = for_scope(username, scope_kind, project_id, conversation_id='default')
        self.index_path = self.default.db_path.parent / 'conversations.db'

    def _entry(self, id, title, created_at, canvas_id=None):
        store = for_scope(self.username, self.scope_kind, self.project_id, conversation_id=id)
        state, running = store.peek()
        return {'id': id, 'title': title, 'canvas_id': canvas_id,
                **(asdict(state) if state else {'session_id': None, 'generation': 0, 'status': 'uninitialized'}), 'running': running,
                'created_at': created_at,
                'updated_at': datetime.fromtimestamp(store.db_path.stat().st_mtime, timezone.utc).isoformat() if store.db_path.is_file() else ''}

    def _rows(self):
        if not self.index_path.is_file():
            return []
        with closing(sqlite3.connect(self.index_path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='conversations'").fetchone():
                return []
            return [dict(r) for r in db.execute('SELECT * FROM conversations ORDER BY created_at DESC,id')]

    def lookup(self, identifier):
        return next((r for r in self._rows() if r['id'] == identifier), None)

    def list(self, *, canvas_id=None, include_default=True):
        rows = [r for r in self._rows() if r.get('canvas_id') == canvas_id and r.get('state', 'ready') == 'ready']
        default = [self._entry('default', '默认会话', '')] if canvas_id is None and include_default else []
        return default + [self._entry(r['id'], r['title'], r['created_at'], r.get('canvas_id')) for r in rows]

    def create(self, title, *, canvas_id=None, request_id=None):
        title = title.strip()
        if not title or len(title) > 100:
            raise ValueError('会话名称需为 1–100 字')
        if canvas_id and not request_id:
            raise ValueError('画布会话须提供原请求编号')
        request_id = request_id or uuid4().hex
        digest = hashlib.sha256(json.dumps([title, canvas_id], ensure_ascii=False).encode()).hexdigest()
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.index_path, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL)')
            columns = {r[1] for r in db.execute('PRAGMA table_info(conversations)')}
            for name, declaration in [('canvas_id', 'TEXT'), ('request_id', 'TEXT'), ('request_hash', 'TEXT'), ('state', "TEXT NOT NULL DEFAULT 'ready'")]:
                if name not in columns:
                    db.execute(f'ALTER TABLE conversations ADD COLUMN {name} {declaration}')
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS conversation_requests ON conversations(request_id)')
            db.row_factory = sqlite3.Row
            old = db.execute('SELECT * FROM conversations WHERE request_id=?', (request_id,)).fetchone()
            if old:
                if old['request_hash'] != digest:
                    raise ValueError('请求编号已用于不同会话内容')
                id, created_at = old['id'], old['created_at']
            else:
                id, created_at = uuid4().hex, datetime.now(timezone.utc).isoformat()
                db.execute("INSERT INTO conversations (id,title,created_at,canvas_id,request_id,request_hash,state) VALUES (?,?,?,?,?,?,'preparing')", (id, title, created_at, canvas_id, request_id, digest))
        # Persist owner/locator before creating a session; interrupted creation
        # is recovered under the same request ID, never an orphan conversation.
        for_scope(self.username, self.scope_kind, self.project_id, conversation_id=id).get_or_create()
        with closing(sqlite3.connect(self.index_path, timeout=10)) as db, db:
            db.execute("UPDATE conversations SET state='ready' WHERE id=?", (id,))
        return self._entry(id, title, created_at, canvas_id)
