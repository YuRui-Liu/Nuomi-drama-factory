"""Durable canvas Director attempts. Input snapshots never change after insertion."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .models import DirectorDraft


_PROTECTED = frozenset({
    "id", "project_id", "canvas_id", "node_id", "request_id", "parent_attempt_id",
    "snapshot", "stage", "detail", "created_at", "updated_at",
})


def _validate_detail(detail: dict) -> None:
    if _PROTECTED.intersection(detail):
        raise ValueError("attempt identity and snapshot columns are immutable")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DirectorAttemptStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = self.root / "attempts.sqlite3"
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    canvas_id TEXT NOT NULL, node_id TEXT NOT NULL,
                    request_id TEXT NOT NULL, parent_attempt_id TEXT,
                    snapshot TEXT NOT NULL, stage TEXT NOT NULL,
                    detail TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_id,canvas_id,node_id,request_id),
                    UNIQUE(parent_attempt_id)
                );
                CREATE INDEX IF NOT EXISTS attempts_node ON attempts(project_id,canvas_id,node_id,created_at,id);
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=30000")
            yield db
        finally:
            db.close()

    @staticmethod
    def _row(row):
        if row is None:
            return None
        data = dict(row)
        data["snapshot"] = json.loads(data["snapshot"])
        detail = json.loads(data.pop("detail"))
        data.update({key: value for key, value in detail.items() if key not in _PROTECTED})
        return data

    def get(self, attempt_id: str):
        with self._connect() as db:
            return self._row(db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone())

    def list(self, project_id: str, canvas_id: str | None = None, node_id: str | None = None):
        clauses, args = ["project_id=?"], [project_id]
        if canvas_id is not None:
            clauses.append("canvas_id=?")
            args.append(canvas_id)
        if node_id is not None:
            clauses.append("node_id=?")
            args.append(node_id)
        with self._connect() as db:
            return [self._row(row) for row in db.execute(
                "SELECT * FROM attempts WHERE " + " AND ".join(clauses) +
                " ORDER BY created_at DESC,id DESC", args)]

    def create(self, project_id: str, canvas_id: str, node_id: str,
               request_id: str, draft: DirectorDraft, *, detail: dict | None = None):
        _validate_detail(detail or {})
        attempt_id, now = uuid4().hex, _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("""INSERT OR IGNORE INTO attempts
                (id,project_id,canvas_id,node_id,request_id,parent_attempt_id,snapshot,stage,detail,created_at,updated_at)
                VALUES (?,?,?,?,?,NULL,?,'created',?,?,?)""",
                (attempt_id, project_id, canvas_id, node_id, request_id,
                 draft.model_dump_json(), json.dumps(detail or {}), now, now))
            created = cursor.rowcount == 1
            row = db.execute("SELECT * FROM attempts WHERE project_id=? AND canvas_id=? AND node_id=? AND request_id=?",
                             (project_id, canvas_id, node_id, request_id)).fetchone()
            db.commit()
        return self._row(row), created

    def update(self, attempt_id: str, **changes):
        _validate_detail({key: value for key, value in changes.items() if key != "stage"})
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
            if row is None:
                raise KeyError(attempt_id)
            detail = json.loads(row["detail"])
            stage = changes.pop("stage", row["stage"])
            detail.update(changes)
            db.execute("UPDATE attempts SET stage=?,detail=?,updated_at=? WHERE id=?",
                       (stage, json.dumps(detail, ensure_ascii=False), _now(), attempt_id))
            db.commit()
        return self.get(attempt_id)

    def claim(self, attempt_id: str, expected_stage: str, stage: str) -> bool:
        with self._connect() as db:
            return db.execute("UPDATE attempts SET stage=?,updated_at=? WHERE id=? AND stage=?",
                              (stage, _now(), attempt_id, expected_stage)).rowcount == 1

    def retry(self, parent_id: str):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            parent = db.execute("SELECT * FROM attempts WHERE id=?", (parent_id,)).fetchone()
            if parent is None:
                raise KeyError(parent_id)
            existing = db.execute("SELECT * FROM attempts WHERE parent_attempt_id=?", (parent_id,)).fetchone()
            if existing:
                db.commit()
                return self._row(existing), False
            attempt_id, now = uuid4().hex, _now()
            previous = json.loads(parent["detail"])
            cache = {"frozen_images": previous.get("frozen_images", {}),
                     "reference_limit": previous.get("reference_limit", 5)}
            # The child has a new paid-task identity, but can reuse the exact
            # immutable optimization until its writing rules/profile change.
            cache.update({key: previous[key] for key in ("optimized", "rules_hash")
                          if key in previous})
            db.execute("""INSERT INTO attempts
                (id,project_id,canvas_id,node_id,request_id,parent_attempt_id,snapshot,stage,detail,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,'created',?,?,?)""",
                (attempt_id, parent["project_id"], parent["canvas_id"], parent["node_id"],
                 "retry:" + attempt_id, parent_id, parent["snapshot"], json.dumps(cache), now, now))
            row = db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
            db.commit()
        return self._row(row), True
