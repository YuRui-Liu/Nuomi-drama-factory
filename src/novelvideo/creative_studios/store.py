"""Revisioned studio documents. All writes use a single SQLite transaction."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

KINDS = frozenset({'character', 'director', 'previs', 'intro'})


class RevisionConflict(ValueError):
    """A different editor saved after the caller loaded its document."""


class StudioStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS studio_versions (
                kind TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
                name TEXT NOT NULL, data TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(kind, id, revision))''')

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _validate(kind: str, id: str | None = None):
        if kind not in KINDS:
            raise ValueError('未知工作室类型')
        if id is not None and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', id):
            raise ValueError('无效草稿标识')

    @staticmethod
    def _document(row) -> dict[str, Any]:
        result = dict(row)
        result['data'] = json.loads(result['data'])
        return result

    def list(self, kind: str) -> list[dict[str, Any]]:
        self._validate(kind)
        with self._connection() as db:
            rows = db.execute('''SELECT v.* FROM studio_versions v JOIN
                (SELECT id, MAX(revision) AS revision FROM studio_versions WHERE kind=? GROUP BY id) latest
                ON v.id=latest.id AND v.revision=latest.revision WHERE v.kind=?
                ORDER BY v.updated_at DESC, v.id''', (kind, kind)).fetchall()
        return [self._document(row) for row in rows]

    def get(self, kind: str, id: str) -> dict[str, Any] | None:
        self._validate(kind, id)
        with self._connection() as db:
            row = db.execute('SELECT * FROM studio_versions WHERE kind=? AND id=? ORDER BY revision DESC LIMIT 1', (kind, id)).fetchone()
        return self._document(row) if row else None

    def history(self, kind: str, id: str) -> list[dict[str, Any]]:
        self._validate(kind, id)
        with self._connection() as db:
            rows = db.execute('SELECT * FROM studio_versions WHERE kind=? AND id=? ORDER BY revision', (kind, id)).fetchall()
        return [self._document(row) for row in rows]

    def save(self, kind: str, id: str, name: str, data: dict[str, Any], expected_revision: int) -> dict[str, Any]:
        self._validate(kind, id)
        if not name.strip() or len(name) > 200 or not isinstance(data, dict) or expected_revision < 0:
            raise ValueError('草稿名称、内容或版本无效')
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False)
        if len(payload.encode('utf-8')) > 2_000_000:
            raise ValueError('草稿超过 2 MB，请通过素材上传接口保存媒体文件')
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT MAX(revision) FROM studio_versions WHERE kind=? AND id=?', (kind, id)).fetchone()[0] or 0
            if current != expected_revision:
                raise RevisionConflict('草稿已有新版本，请重新载入后再保存')
            revision = current + 1
            db.execute('INSERT INTO studio_versions VALUES (?, ?, ?, ?, ?, ?)', (kind, id, revision, name.strip(), payload, now))
        return dict(kind=kind, id=id, revision=revision, name=name.strip(), data=json.loads(payload), updated_at=now)
