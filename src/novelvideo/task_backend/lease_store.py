"""SQLite coordination for CE inline task lane admission."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from novelvideo.config import RUNTIME_DIR
from novelvideo.sqlite_pragmas import configure_sqlite_connection
from novelvideo.task_backend.limits import GlobalLaneQueueLimitExceeded
from novelvideo.task_backend.queues import normalize_queue_kind


_SCHEMA = """
CREATE TABLE IF NOT EXISTS inline_lane_leases (
    task_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    project_id TEXT NOT NULL DEFAULT '',
    lane TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('active', 'queued')),
    expires_at REAL NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inline_lane_leases_lane_state
ON inline_lane_leases(lane, state, created_at);
"""


def runtime_task_database_path() -> Path:
    configured = os.environ.get("ST_CE_TASK_RUNTIME_DB", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path(RUNTIME_DIR) / "task_backend.db").resolve()


class SQLiteLaneLeaseStore:
    """Process-shared lane slots backed by short-lived SQLite leases."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = Path(database_path or runtime_task_database_path()).resolve()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.database_path, timeout=10, check_same_thread=False)
        configure_sqlite_connection(conn)
        conn.executescript(_SCHEMA)
        conn.commit()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._reclaim_expired(conn)
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _reclaim_expired(conn: sqlite3.Connection) -> int:
        cursor = conn.execute(
            "DELETE FROM inline_lane_leases "
            "WHERE expires_at <= ((julianday('now') - 2440587.5) * 86400.0)",
        )
        return max(cursor.rowcount, 0)

    @staticmethod
    def _sqlite_now(conn: sqlite3.Connection) -> float:
        """Read the database clock after the write transaction has been acquired."""
        row = conn.execute(
            "SELECT ((julianday('now') - 2440587.5) * 86400.0)"
        ).fetchone()
        return float(row[0])

    @staticmethod
    def _positive_seconds(value: float) -> float:
        seconds = float(value)
        if seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        return seconds

    def admit(
        self,
        *,
        owner_id: str,
        task_id: str,
        lane: str,
        active_limit: int,
        queue_limit: int,
        lease_seconds: float,
        project_id: str = "",
    ) -> str:
        """Atomically reserve either an active slot or one bounded queue slot."""
        clean_owner = str(owner_id or "").strip()
        clean_task = str(task_id or "").strip()
        if not clean_owner or not clean_task:
            raise ValueError("owner_id and task_id are required")
        clean_lane = normalize_queue_kind(lane)
        seconds = self._positive_seconds(lease_seconds)
        with self._transaction() as conn:
            now = self._sqlite_now(conn)
            existing = conn.execute(
                "SELECT owner_id, lane, state FROM inline_lane_leases WHERE task_id = ?",
                (clean_task,),
            ).fetchone()
            if existing is not None:
                if existing[0] != clean_owner or existing[1] != clean_lane:
                    raise RuntimeError(f"task lane lease already owned: {clean_task}")
                conn.execute(
                    "UPDATE inline_lane_leases SET expires_at = ?, updated_at = ? "
                    "WHERE task_id = ? AND owner_id = ?",
                    (now + seconds, now, clean_task, clean_owner),
                )
                return str(existing[2])

            active = int(
                conn.execute(
                    "SELECT COUNT(*) FROM inline_lane_leases "
                    "WHERE lane = ? AND state = 'active'",
                    (clean_lane,),
                ).fetchone()[0]
            )
            state = "active" if active < max(int(active_limit), 1) else "queued"
            if state == "queued":
                queued = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM inline_lane_leases "
                        "WHERE lane = ? AND state = 'queued'",
                        (clean_lane,),
                    ).fetchone()[0]
                )
                if queued >= max(int(queue_limit), 1):
                    raise GlobalLaneQueueLimitExceeded(
                        project_id=str(project_id or ""),
                        queue_kind=clean_lane,
                        limit=max(int(queue_limit), 1),
                        queued=queued,
                    )
            conn.execute(
                "INSERT INTO inline_lane_leases "
                "(task_id, owner_id, project_id, lane, state, expires_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    clean_task,
                    clean_owner,
                    str(project_id or ""),
                    clean_lane,
                    state,
                    now + seconds,
                    now,
                    now,
                ),
            )
            return state

    def promote(
        self,
        *,
        owner_id: str,
        task_id: str,
        lane: str,
        active_limit: int,
        lease_seconds: float,
    ) -> bool:
        clean_lane = normalize_queue_kind(lane)
        seconds = self._positive_seconds(lease_seconds)
        with self._transaction() as conn:
            now = self._sqlite_now(conn)
            active = int(
                conn.execute(
                    "SELECT COUNT(*) FROM inline_lane_leases "
                    "WHERE lane = ? AND state = 'active'",
                    (clean_lane,),
                ).fetchone()[0]
            )
            if active >= max(int(active_limit), 1):
                return False
            cursor = conn.execute(
                "UPDATE inline_lane_leases SET state = 'active', expires_at = ?, updated_at = ? "
                "WHERE task_id = ? AND owner_id = ? AND lane = ? AND state = 'queued'",
                (now + seconds, now, str(task_id), str(owner_id), clean_lane),
            )
            return cursor.rowcount == 1

    def heartbeat(
        self,
        *,
        owner_id: str,
        task_id: str,
        lease_seconds: float,
    ) -> bool:
        seconds = self._positive_seconds(lease_seconds)
        with self._transaction() as conn:
            now = self._sqlite_now(conn)
            cursor = conn.execute(
                "UPDATE inline_lane_leases SET expires_at = ?, updated_at = ? "
                "WHERE task_id = ? AND owner_id = ?",
                (now + seconds, now, str(task_id), str(owner_id)),
            )
            return cursor.rowcount == 1

    def release(self, *, owner_id: str, task_id: str) -> bool:
        with self._transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM inline_lane_leases WHERE task_id = ? AND owner_id = ?",
                (str(task_id), str(owner_id)),
            )
            return cursor.rowcount == 1

    def release_task(self, *, task_id: str) -> bool:
        """Release a queued task even when its original process owner is gone."""
        with self._transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM inline_lane_leases WHERE task_id = ? AND state = 'queued'",
                (str(task_id),),
            )
            return cursor.rowcount == 1

    def counts(self, lane: str) -> dict[str, int]:
        clean_lane = normalize_queue_kind(lane)
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT state, COUNT(*) FROM inline_lane_leases "
                "WHERE lane = ? GROUP BY state",
                (clean_lane,),
            ).fetchall()
        counts = {str(state): int(count) for state, count in rows}
        return {"active": counts.get("active", 0), "queued": counts.get("queued", 0)}
