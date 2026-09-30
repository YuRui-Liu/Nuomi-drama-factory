"""Scope-neutral SQLite persistence. Callers choose personal or project DB paths.

Revision zero means absent. Writes use compare-and-swap; immutable historical
rows are never replaced. Draft and binding results are JSON-copy dictionaries.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any

from .models import ExecutionSnapshot, ResourceVersion, TeamVersion
from .resources import verify_resource

MAX_PAYLOAD_BYTES = 1024 * 1024


class RevisionConflict(ValueError):
    """A stale revision or immutable identity was supplied."""


def _encode(data: Any) -> str:
    value = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    if len(value.encode('utf-8')) > MAX_PAYLOAD_BYTES:
        raise ValueError('payload exceeds maximum size')
    return value


class AgentTeamStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS versions (
                    kind TEXT, id TEXT, revision INTEGER, owner TEXT, data TEXT NOT NULL,
                    PRIMARY KEY(kind, id, revision));
                CREATE TABLE IF NOT EXISTS drafts (
                    project_id TEXT PRIMARY KEY, revision INTEGER, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS bindings (
                    project_id TEXT, revision INTEGER, draft_revision INTEGER, data TEXT NOT NULL,
                    PRIMARY KEY(project_id, revision));
                CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS resource_usage (
                    resource_id TEXT, resource_revision INTEGER, consumer_kind TEXT,
                    consumer_id TEXT, consumer_revision INTEGER,
                    PRIMARY KEY(resource_id, resource_revision, consumer_kind, consumer_id, consumer_revision));
            ''')

    @contextmanager
    def _connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _check(actual: int, expected: int):
        if type(expected) is not int or expected < 0 or actual != expected:
            raise RevisionConflict(f'expected revision {expected}; current revision {actual}')

    def _publish(self, kind, value, expected_revision):
        payload = _encode(value.model_dump(mode='json'))
        with self._connection(True) as db:
            previous = db.execute('SELECT revision, owner FROM versions WHERE kind=? AND id=? ORDER BY revision DESC LIMIT 1', (kind, value.id)).fetchone()
            current = previous['revision'] if previous else 0
            self._check(current, expected_revision)
            if value.revision != current + 1:
                raise RevisionConflict('published revision must be current revision + 1')
            if previous and previous['owner'] != value.owner:
                raise ValueError('owner cannot change')
            db.execute('INSERT INTO versions VALUES (?, ?, ?, ?, ?)', (kind, value.id, value.revision, value.owner, payload))
        return type(value).model_validate_json(payload)

    def publish_template(self, value: TeamVersion, expected_revision: int) -> TeamVersion:
        return self._publish('template', TeamVersion.model_validate(value.model_dump(mode='json')), expected_revision)

    def publish_resource(self, value: ResourceVersion, expected_revision: int) -> ResourceVersion:
        return self._publish('resource', verify_resource(value), expected_revision)

    def _get(self, kind, id, revision, model):
        query = 'SELECT data FROM versions WHERE kind=? AND id=?'
        args = [kind, id]
        if revision is not None:
            query += ' AND revision=?'
            args.append(revision)
        with self._connection() as db:
            row = db.execute(query + ' ORDER BY revision DESC LIMIT 1', args).fetchone()
        return model.model_validate_json(row['data']) if row else None

    def get_template(self, id: str, revision: int | None = None) -> TeamVersion | None:
        return self._get('template', id, revision, TeamVersion)

    def get_resource(self, id: str, revision: int | None = None) -> ResourceVersion | None:
        return self._get('resource', id, revision, ResourceVersion)

    def _list(self, kind, owner, model):
        with self._connection() as db:
            rows = db.execute('''SELECT v.data FROM versions v WHERE v.kind=?
                AND v.revision=(SELECT MAX(w.revision) FROM versions w WHERE w.kind=v.kind AND w.id=v.id)
                AND (? IS NULL OR v.owner=?) ORDER BY v.id''', (kind, owner, owner)).fetchall()
        return [model.model_validate_json(row['data']) for row in rows]

    def list_templates(self, owner: str | None = None) -> list[TeamVersion]:
        return self._list('template', owner, TeamVersion)

    def list_resources(self, owner: str | None = None) -> list[ResourceVersion]:
        return self._list('resource', owner, ResourceVersion)

    def save_draft(self, project_id: str, data: dict, expected_revision: int) -> dict:
        payload = _encode(data)
        with self._connection(True) as db:
            row = db.execute('SELECT revision FROM drafts WHERE project_id=?', (project_id,)).fetchone()
            current = row['revision'] if row else 0
            self._check(current, expected_revision)
            db.execute('INSERT INTO drafts VALUES (?, ?, ?) ON CONFLICT(project_id) DO UPDATE SET revision=excluded.revision, data=excluded.data', (project_id, current + 1, payload))
        return {'project_id': project_id, 'draft_revision': current + 1, 'data': json.loads(payload)}

    def get_draft(self, project_id: str) -> dict | None:
        with self._connection() as db:
            row = db.execute('SELECT * FROM drafts WHERE project_id=?', (project_id,)).fetchone()
        return {'project_id': project_id, 'draft_revision': row['revision'], 'data': json.loads(row['data'])} if row else None

    def activate(self, project_id: str, snapshot: dict, expected_active_revision: int, draft_revision: int) -> dict:
        """Caller supplies a self-contained snapshot; template access is service policy."""
        payload = _encode(snapshot)
        with self._connection(True) as db:
            draft = db.execute('SELECT revision FROM drafts WHERE project_id=?', (project_id,)).fetchone()
            if draft is None:
                raise RevisionConflict('activation requires a saved draft')
            self._check(draft['revision'], draft_revision)
            current = db.execute('SELECT COALESCE(MAX(revision), 0) FROM bindings WHERE project_id=?', (project_id,)).fetchone()[0]
            self._check(current, expected_active_revision)
            db.execute('INSERT INTO bindings VALUES (?, ?, ?, ?)', (project_id, current + 1, draft_revision, payload))
        return {'project_id': project_id, 'active_revision': current + 1, 'draft_revision': draft_revision, 'snapshot': json.loads(payload)}

    def list_versions(self, project_id: str) -> list[dict]:
        with self._connection() as db:
            rows = db.execute('SELECT * FROM bindings WHERE project_id=? ORDER BY revision', (project_id,)).fetchall()
        return [{'project_id': project_id, 'active_revision': row['revision'], 'draft_revision': row['draft_revision'], 'snapshot': json.loads(row['data'])} for row in rows]

    def get_binding(self, project_id: str) -> dict | None:
        versions = self.list_versions(project_id)
        return versions[-1] if versions else None

    def save_snapshot(self, snapshot: ExecutionSnapshot) -> ExecutionSnapshot:
        snapshot = ExecutionSnapshot.model_validate(snapshot.model_dump(mode='json'))
        for resource in snapshot.resource_snapshots:
            verify_resource(resource)
        payload = _encode(snapshot.model_dump(mode='json'))
        with self._connection(True) as db:
            try:
                db.execute('INSERT INTO snapshots VALUES (?, ?)', (snapshot.id, payload))
            except sqlite3.IntegrityError as exc:
                raise RevisionConflict('execution snapshot already exists') from exc
        return ExecutionSnapshot.model_validate_json(payload)

    def load_snapshot(self, id: str) -> ExecutionSnapshot | None:
        with self._connection() as db:
            row = db.execute('SELECT data FROM snapshots WHERE id=?', (id,)).fetchone()
        return ExecutionSnapshot.model_validate_json(row['data']) if row else None

    def record_resource_usage(self, resource_id: str, resource_revision: int, consumer_kind: str, consumer_id: str, consumer_revision: int) -> None:
        """Record historical pins, including resources copied from another scope."""
        if consumer_kind not in ('template', 'project'):
            raise ValueError('consumer kind must be template or project')
        if not resource_id or not consumer_id or type(resource_revision) is not int or type(consumer_revision) is not int or min(resource_revision, consumer_revision) < 1:
            raise ValueError('usage requires identities and positive revisions')
        with self._connection(True) as db:
            db.execute('INSERT OR IGNORE INTO resource_usage VALUES (?, ?, ?, ?, ?)', (resource_id, resource_revision, consumer_kind, consumer_id, consumer_revision))

    def list_resource_usage(self, resource_id: str, revision: int | None = None) -> list[dict]:
        with self._connection() as db:
            rows = db.execute('SELECT * FROM resource_usage WHERE resource_id=? AND (? IS NULL OR resource_revision=?) ORDER BY resource_revision, consumer_kind, consumer_id, consumer_revision', (resource_id, revision, revision)).fetchall()
        return [dict(row) for row in rows]
