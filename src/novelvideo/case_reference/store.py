"""Project-local SQLite proposal storage with transactional state changes."""
import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import uuid4


def fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProposalStore:
    def __init__(self, root: Path):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / 'case_reference.sqlite3'
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS proposals '
                       '(id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL, '
                       'fingerprint TEXT NOT NULL, body TEXT NOT NULL, UNIQUE(owner, request_id))')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, owner: str, request_id: str, inputs: dict, evidence: dict):
        digest = fingerprint(inputs)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT fingerprint, body FROM proposals WHERE owner=? AND request_id=?',
                             (owner, request_id)).fetchone()
            if old:
                if old[0] != digest:
                    raise ValueError('request_id 已用于不同输入')
                return json.loads(old[1]), False
            item = dict(id=uuid4().hex, owner=owner, request_id=request_id, input=inputs,
                        input_fingerprint=digest, evidence=evidence, status='queued',
                        result=None, error=None, created_at=now(), updated_at=now())
            db.execute('INSERT INTO proposals VALUES (?, ?, ?, ?, ?)',
                       (item['id'], owner, request_id, digest, json.dumps(item, ensure_ascii=False)))
            return item, True

    def get(self, proposal_id: str, owner: str):
        with self.connection() as db:
            row = db.execute('SELECT body FROM proposals WHERE id=? AND owner=?', (proposal_id, owner)).fetchone()
        if not row:
            raise KeyError(proposal_id)
        return json.loads(row[0])

    def list(self, owner: str, canvas_id: str, node_id: str, segment_id: str):
        with self.connection() as db:
            rows = db.execute('SELECT body FROM proposals WHERE owner=? ORDER BY rowid DESC', (owner,)).fetchall()
        items = [json.loads(row[0]) for row in rows]
        return [item for item in items if all(item['input'].get(key) == value for key, value in
                [('canvas_id', canvas_id), ('node_id', node_id), ('segment_id', segment_id)])][:20]

    def _change(self, proposal_id, owner, allowed, status, changes):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT body FROM proposals WHERE id=? AND owner=?', (proposal_id, owner)).fetchone()
            if not row:
                raise KeyError(proposal_id)
            item = json.loads(row[0])
            if item['status'] not in allowed:
                return item, False
            item.update(changes, status=status or item['status'], updated_at=now())
            db.execute('UPDATE proposals SET body=? WHERE id=?', (json.dumps(item, ensure_ascii=False), proposal_id))
            return item, True

    def transition(self, proposal_id, owner, allowed, status, **changes):
        return self._change(proposal_id, owner, allowed, status, changes)[0]

    def claim(self, proposal_id, owner):
        return self._change(proposal_id, owner, ('queued',), 'running', {})[1]
