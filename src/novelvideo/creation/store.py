"""SQLite semantic object history; IDs are always bound SQL values."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4

from .models import OBJECT_KINDS, ObjectKind


class RevisionConflict(RuntimeError):
    """An object's current revision differs from the caller's expectation."""

    def __init__(self, object_id: str, expected_revision: int, current_revision: int):
        self.object_id = object_id
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(f'{object_id}: expected revision {expected_revision}, current {current_revision}')


_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS objects (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, base_asset_id TEXT, variant_id TEXT,
        current_revision INTEGER NOT NULL CHECK(current_revision >= 1))''',
    '''CREATE TABLE IF NOT EXISTS object_versions (
        object_id TEXT NOT NULL REFERENCES objects(id), revision INTEGER NOT NULL CHECK(revision >= 1),
        payload TEXT NOT NULL, actor_id TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(object_id, revision))''',
    '''CREATE TABLE IF NOT EXISTS relations (
        id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES objects(id),
        target_id TEXT NOT NULL REFERENCES objects(id), kind TEXT NOT NULL, payload TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS episode_canvases (
        episode_id TEXT PRIMARY KEY, canvas_id TEXT NOT NULL UNIQUE)''',
    '''CREATE TABLE IF NOT EXISTS canvas_workspaces (
        canvas_id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('concept','episode','free')),
        episode_id TEXT UNIQUE, title TEXT NOT NULL, operation_id TEXT NOT NULL, created_at TEXT NOT NULL)''',
    "CREATE UNIQUE INDEX IF NOT EXISTS single_concept_canvas ON canvas_workspaces(kind) WHERE kind='concept'",
    '''CREATE TABLE IF NOT EXISTS operations (
        id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
        state TEXT NOT NULL, payload TEXT NOT NULL, result TEXT, actor_id TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS projection_outbox (
        operation_id TEXT PRIMARY KEY REFERENCES operations(id), canvas_id TEXT NOT NULL,
        patch TEXT NOT NULL, applied_revision INTEGER)''',
    '''CREATE TABLE IF NOT EXISTS proposals (
        id TEXT PRIMARY KEY, scope TEXT NOT NULL, input_hash TEXT NOT NULL,
        authorized_operations TEXT NOT NULL, decision TEXT, actor_id TEXT)''',
)


class ObjectStore:
    def __init__(self, project_dir: Path | str):
        self.db_path = Path(project_dir) / 'creation' / 'objects.sqlite3'
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as conn:
            for statement in _SCHEMA:
                conn.execute(statement)
            # Explicit initialization is the only backfill boundary. Require a
            # semantic creation operation; arbitrary legacy maps are not registered.
            conn.execute("""INSERT OR IGNORE INTO canvas_workspaces
                SELECT e.canvas_id, 'episode', e.episode_id, json_extract(v.payload,'$.title'),
                       o.id, v.created_at
                FROM episode_canvases e JOIN objects obj ON obj.id=e.episode_id AND obj.kind='episode'
                JOIN object_versions v ON v.object_id=obj.id AND v.revision=obj.current_revision
                JOIN operations o ON o.id=json_extract(v.payload,'$.operation_id')
                WHERE json_extract(o.payload,'$.kind')='episode.create'
                  AND json_extract(o.payload,'$.scope.episode_id')=e.episode_id
                  AND json_extract(o.payload,'$.scope.canvas_id')=e.canvas_id
                  AND json_extract(v.payload,'$.title') IS NOT NULL""")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Atomic write boundary. Callers must never perform network I/O here."""
        conn = self._connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _read(conn: sqlite3.Connection, object_id: str, revision: int | None) -> dict[str, Any] | None:
        if revision is None:
            from .asset_sync import assert_current_available
            assert_current_available(conn, object_id)
        row = conn.execute(
            '''SELECT o.id, o.kind, o.base_asset_id, o.variant_id,
                      v.revision, v.payload, v.actor_id, v.created_at
               FROM objects o JOIN object_versions v ON v.object_id = o.id
               WHERE o.id = ? AND v.revision = COALESCE(?, o.current_revision)''',
            (object_id, revision),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result['payload'] = json.loads(result['payload'])
        return result

    def read(self, object_id: str, revision: int | None = None) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            # Pending checks and the selected version share a read snapshot.
            conn.execute('BEGIN')
            return self._read(conn, object_id, revision)
        finally:
            conn.close()

    def create(self, kind: ObjectKind, payload: dict[str, Any], actor_id: str) -> dict[str, Any]:
        if kind not in OBJECT_KINDS:
            raise ValueError(f'Unknown object kind: {kind}')
        serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        object_id = uuid4().hex
        with self.transaction() as conn:
            conn.execute('INSERT INTO objects (id, kind, current_revision) VALUES (?, ?, 1)', (object_id, kind))
            conn.execute('INSERT INTO object_versions VALUES (?, 1, ?, ?, ?)',
                         (object_id, serialized, actor_id, datetime.now(timezone.utc).isoformat()))
            return self._read(conn, object_id, 1)

    def update(self, object_id: str, expected_revision: int, payload: dict[str, Any], actor_id: str) -> dict[str, Any]:
        serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        with self.transaction() as conn:
            row = conn.execute('SELECT current_revision, kind FROM objects WHERE id = ?', (object_id,)).fetchone()
            if row is None:
                raise KeyError(object_id)
            if row['kind'].startswith('custom:'):
                raise ValueError('自定义对象必须通过已确认定义的领域操作更新')
            current = row['current_revision']
            if current != expected_revision:
                raise RevisionConflict(object_id, expected_revision, current)
            revision = current + 1
            conn.execute('INSERT INTO object_versions VALUES (?, ?, ?, ?, ?)',
                         (object_id, revision, serialized, actor_id, datetime.now(timezone.utc).isoformat()))
            conn.execute('UPDATE objects SET current_revision = ? WHERE id = ?', (revision, object_id))
            return self._read(conn, object_id, revision)
