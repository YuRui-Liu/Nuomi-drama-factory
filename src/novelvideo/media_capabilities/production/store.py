"""SQLite persistence for production runs and their DAGs."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict, deque
from collections.abc import Mapping
from typing import Any, Iterator
from uuid import uuid4

from pydantic import JsonValue

from novelvideo.media_capabilities.production.models import (
    ProductionEdge,
    ProductionNode,
    ProductionNodeStatus,
    ProductionRun,
    ProductionRunStatus,
)
from novelvideo.sqlite_pragmas import configure_sqlite_connection


class InvalidProductionTransition(RuntimeError):
    """Raised when a production run or node transition is not allowed."""


_NODE_TERMINAL = {
    ProductionNodeStatus.SUCCEEDED,
    ProductionNodeStatus.FAILED,
    ProductionNodeStatus.QUALITY_FAILED,
    ProductionNodeStatus.CANCELLED,
    ProductionNodeStatus.SKIPPED,
}
_NODE_NEXT = {
    ProductionNodeStatus.PENDING: {ProductionNodeStatus.READY},
    ProductionNodeStatus.READY: {ProductionNodeStatus.QUEUED},
    ProductionNodeStatus.QUEUED: {ProductionNodeStatus.RUNNING},
    ProductionNodeStatus.RUNNING: _NODE_TERMINAL,
}
_RUN_TERMINAL = {
    ProductionRunStatus.SUCCEEDED,
    ProductionRunStatus.FAILED,
    ProductionRunStatus.CANCELLED,
}
_RUN_NEXT = {
    ProductionRunStatus.RUNNING: {
        ProductionRunStatus.PAUSED,
        ProductionRunStatus.CANCELLING,
        ProductionRunStatus.SUCCEEDED,
        ProductionRunStatus.FAILED,
    },
    ProductionRunStatus.PAUSED: {
        ProductionRunStatus.RUNNING,
        ProductionRunStatus.CANCELLING,
    },
    ProductionRunStatus.CANCELLING: {
        ProductionRunStatus.CANCELLED,
        ProductionRunStatus.FAILED,
    },
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must be non-empty")
    return normalized


def _snapshot_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _snapshot(raw: str) -> JsonValue:
    return json.loads(raw, parse_constant=lambda value: _invalid_json(value))


def _invalid_json(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


class ProductionStore:
    """Persist production DAG records using short SQLite transactions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        try:
            connection.row_factory = sqlite3.Row
            configure_sqlite_connection(connection)
            return connection
        except Exception:
            connection.close()
            raise

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_schema(self) -> None:
        with self._write() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS production_runs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    config_snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS production_nodes (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL
                        REFERENCES production_runs(id) ON DELETE CASCADE,
                    node_type TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    config_snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(run_id, idempotency_key),
                    UNIQUE(run_id, id)
                );

                CREATE TABLE IF NOT EXISTS production_edges (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL
                        REFERENCES production_runs(id) ON DELETE CASCADE,
                    upstream_node_id TEXT NOT NULL,
                    downstream_node_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (run_id, upstream_node_id)
                        REFERENCES production_nodes(run_id, id) ON DELETE CASCADE,
                    FOREIGN KEY (run_id, downstream_node_id)
                        REFERENCES production_nodes(run_id, id) ON DELETE CASCADE,
                    UNIQUE(run_id, upstream_node_id, downstream_node_id),
                    CHECK(upstream_node_id <> downstream_node_id)
                );

                CREATE TRIGGER IF NOT EXISTS production_runs_snapshot_immutable
                BEFORE UPDATE OF config_snapshot_json ON production_runs
                WHEN NEW.config_snapshot_json <> OLD.config_snapshot_json
                BEGIN
                    SELECT RAISE(ABORT, 'production run config snapshot is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS production_nodes_snapshot_immutable
                BEFORE UPDATE OF config_snapshot_json ON production_nodes
                WHEN NEW.config_snapshot_json <> OLD.config_snapshot_json
                BEGIN
                    SELECT RAISE(ABORT, 'production node config snapshot is immutable');
                END;
                """
            )

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> ProductionRun:
        return ProductionRun(
            id=row["id"],
            project_id=row["project_id"],
            status=ProductionRunStatus(row["status"]),
            config_snapshot=_snapshot(row["config_snapshot_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _node_from_row(row: sqlite3.Row) -> ProductionNode:
        return ProductionNode(
            id=row["id"],
            run_id=row["run_id"],
            node_type=row["node_type"],
            idempotency_key=row["idempotency_key"],
            status=ProductionNodeStatus(row["status"]),
            config_snapshot=_snapshot(row["config_snapshot_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _edge_from_row(row: sqlite3.Row) -> ProductionEdge:
        return ProductionEdge(
            id=row["id"],
            run_id=row["run_id"],
            upstream_node_id=row["upstream_node_id"],
            downstream_node_id=row["downstream_node_id"],
            created_at=row["created_at"],
        )

    def create_run(
        self,
        project_id: str,
        config_snapshot: JsonValue,
    ) -> ProductionRun:
        project_id = _text(project_id, "project_id")
        snapshot_json = _snapshot_json(config_snapshot)
        timestamp = _now_iso()
        run_id = str(uuid4())
        with self._write() as connection:
            connection.execute(
                """
                INSERT INTO production_runs (
                    id, project_id, status, config_snapshot_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    project_id,
                    ProductionRunStatus.RUNNING.value,
                    snapshot_json,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM production_runs WHERE id = ?", (run_id,)
            ).fetchone()
            return self._run_from_row(row)

    def get_run(self, run_id: str) -> ProductionRun:
        run_id = _text(run_id, "run_id")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM production_runs WHERE id = ?", (run_id,)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise LookupError(f"production run {run_id} not found")
        return self._run_from_row(row)

    def list_runs(
        self,
        project_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ProductionRun]:
        project_id = _text(project_id, "project_id")
        if limit <= 0 or offset < 0:
            raise ValueError("limit must be positive and offset non-negative")
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM production_runs
                WHERE project_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ? OFFSET ?
                """,
                (project_id, limit, offset),
            ).fetchall()
        finally:
            connection.close()
        return [self._run_from_row(row) for row in rows]

    def count_runs(self, project_id: str) -> int:
        project_id = _text(project_id, "project_id")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT COUNT(*) FROM production_runs WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        finally:
            connection.close()
        return int(row[0])

    def add_node(
        self,
        run_id: str,
        node_type: str,
        idempotency_key: str,
        config_snapshot: JsonValue,
    ) -> ProductionNode:
        run_id = _text(run_id, "run_id")
        node_type = _text(node_type, "node_type")
        key = _text(idempotency_key, "idempotency_key")
        snapshot_json = _snapshot_json(config_snapshot)
        with self._write() as connection:
            existing = connection.execute(
                """
                SELECT * FROM production_nodes
                WHERE run_id = ? AND idempotency_key = ?
                """,
                (run_id, key),
            ).fetchone()
            if existing is not None:
                if (
                    existing["node_type"] != node_type
                    or existing["config_snapshot_json"] != snapshot_json
                ):
                    raise ValueError(
                        f"idempotency key {key!r} has different immutable input"
                    )
                return self._node_from_row(existing)
            timestamp = _now_iso()
            node_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO production_nodes (
                    id, run_id, node_type, idempotency_key, status,
                    config_snapshot_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node_id,
                    run_id,
                    node_type,
                    key,
                    ProductionNodeStatus.PENDING.value,
                    snapshot_json,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM production_nodes WHERE id = ?", (node_id,)
            ).fetchone()
            return self._node_from_row(row)

    def get_node(self, node_id: str) -> ProductionNode:
        node_id = _text(node_id, "node_id")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM production_nodes WHERE id = ?", (node_id,)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise LookupError(f"production node {node_id} not found")
        return self._node_from_row(row)

    def retry_node(self, run_id: str, node_id: str) -> ProductionNode:
        run_id = _text(run_id, "run_id")
        node_id = _text(node_id, "node_id")
        retryable = {
            ProductionNodeStatus.FAILED,
            ProductionNodeStatus.QUALITY_FAILED,
            ProductionNodeStatus.CANCELLED,
        }
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM production_nodes WHERE id = ? AND run_id = ?",
                (node_id, run_id),
            ).fetchone()
            if row is None:
                raise LookupError(f"production node {node_id} not found")
            current = ProductionNodeStatus(row["status"])
            if current not in retryable:
                raise InvalidProductionTransition(
                    f"node status {current.value} cannot be retried"
                )
            connection.execute(
                "UPDATE production_nodes SET status = ?, updated_at = ? WHERE id = ?",
                (ProductionNodeStatus.READY.value, _now_iso(), node_id),
            )
            updated = connection.execute(
                "SELECT * FROM production_nodes WHERE id = ?", (node_id,)
            ).fetchone()
        return self._node_from_row(updated)

    def list_nodes(self, run_id: str) -> list[ProductionNode]:
        run_id = _text(run_id, "run_id")
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM production_nodes WHERE run_id = ? ORDER BY rowid",
                (run_id,),
            ).fetchall()
        finally:
            connection.close()
        return [self._node_from_row(row) for row in rows]

    def list_running_runs(self) -> list[ProductionRun]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM production_runs WHERE status = ? ORDER BY rowid",
                (ProductionRunStatus.RUNNING.value,),
            ).fetchall()
        finally:
            connection.close()
        return [self._run_from_row(row) for row in rows]

    def recompute_ready(self, run_id: str) -> list[ProductionNode]:
        """Release pending nodes whose own upstream branch has succeeded."""
        run_id = _text(run_id, "run_id")
        released: list[ProductionNode] = []
        with self._write() as connection:
            rows = connection.execute(
                """
                SELECT node.* FROM production_nodes AS node
                WHERE node.run_id = ? AND node.status = ?
                ORDER BY node.rowid
                """,
                (run_id, ProductionNodeStatus.PENDING.value),
            ).fetchall()
            for row in rows:
                upstream = connection.execute(
                    """
                    SELECT parent.status
                    FROM production_edges AS edge
                    JOIN production_nodes AS parent
                      ON parent.id = edge.upstream_node_id
                    WHERE edge.run_id = ? AND edge.downstream_node_id = ?
                    """,
                    (run_id, row["id"]),
                ).fetchall()
                if upstream and any(
                    ProductionNodeStatus(parent["status"])
                    is not ProductionNodeStatus.SUCCEEDED
                    for parent in upstream
                ):
                    continue
                connection.execute(
                    "UPDATE production_nodes SET status = ?, updated_at = ? WHERE id = ?",
                    (ProductionNodeStatus.READY.value, _now_iso(), row["id"]),
                )
                updated = connection.execute(
                    "SELECT * FROM production_nodes WHERE id = ?", (row["id"],)
                ).fetchone()
                released.append(self._node_from_row(updated))
        return released

    def list_ready_round_robin(self) -> list[ProductionNode]:
        """Interleave runnable projects after sorting work within each project."""
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT node.*, run.project_id, node.rowid AS insertion_order,
                       (
                           SELECT COUNT(*)
                           FROM production_edges AS edge
                           WHERE edge.run_id = node.run_id
                             AND edge.upstream_node_id = node.id
                       ) AS unlock_count
                FROM production_nodes AS node
                JOIN production_runs AS run ON run.id = node.run_id
                WHERE node.status = ? AND run.status = ?
                ORDER BY node.rowid
                """,
                (
                    ProductionNodeStatus.READY.value,
                    ProductionRunStatus.RUNNING.value,
                ),
            ).fetchall()
        finally:
            connection.close()

        projects: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            projects[row["project_id"]].append(row)
        for project_rows in projects.values():
            project_rows.sort(key=self._ready_sort_key)

        queues = {project: deque(items) for project, items in projects.items()}
        ordered: list[ProductionNode] = []
        while queues:
            for project in tuple(queues):
                ordered.append(self._node_from_row(queues[project].popleft()))
                if not queues[project]:
                    del queues[project]
        return ordered

    @staticmethod
    def _ready_sort_key(row: sqlite3.Row) -> tuple[int, int, int, int]:
        snapshot = _snapshot(row["config_snapshot_json"])
        config = snapshot if isinstance(snapshot, Mapping) else {}
        return (
            -row["unlock_count"],
            ProductionStore._integer(config.get("episode"), 2**31 - 1),
            ProductionStore._integer(config.get("shot"), 2**31 - 1),
            row["insertion_order"],
        )

    @staticmethod
    def _integer(value: object, default: int) -> int:
        return value if isinstance(value, int) and not isinstance(value, bool) else default

    def spent_cost(self, run_id: str) -> float:
        spent_statuses = {
            ProductionNodeStatus.QUEUED,
            ProductionNodeStatus.RUNNING,
            ProductionNodeStatus.SUCCEEDED,
            ProductionNodeStatus.FAILED,
            ProductionNodeStatus.QUALITY_FAILED,
        }
        total = 0.0
        for node in self.list_nodes(run_id):
            if node.status not in spent_statuses or not isinstance(
                node.config_snapshot, Mapping
            ):
                continue
            value = node.config_snapshot.get("estimated_cost", 0.0)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                total += max(0.0, float(value))
        return total

    def consecutive_provider_failures(self, provider_id: str) -> int:
        provider_id = _text(provider_id, "provider_id")
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT status, config_snapshot_json
                FROM production_nodes
                WHERE status IN (?, ?, ?)
                ORDER BY updated_at DESC, rowid DESC
                """,
                (
                    ProductionNodeStatus.SUCCEEDED.value,
                    ProductionNodeStatus.FAILED.value,
                    ProductionNodeStatus.QUALITY_FAILED.value,
                ),
            ).fetchall()
        finally:
            connection.close()
        failures = 0
        for row in rows:
            snapshot = _snapshot(row["config_snapshot_json"])
            if not isinstance(snapshot, Mapping) or snapshot.get("provider_id") != provider_id:
                continue
            if ProductionNodeStatus(row["status"]) is ProductionNodeStatus.SUCCEEDED:
                break
            failures += 1
        return failures

    def cancel_unsubmitted(self, run_id: str) -> None:
        run_id = _text(run_id, "run_id")
        with self._write() as connection:
            connection.execute(
                """
                UPDATE production_nodes SET status = ?, updated_at = ?
                WHERE run_id = ? AND status IN (?, ?)
                """,
                (
                    ProductionNodeStatus.CANCELLED.value,
                    _now_iso(),
                    run_id,
                    ProductionNodeStatus.PENDING.value,
                    ProductionNodeStatus.READY.value,
                ),
            )

    def add_edge(
        self,
        run_id: str,
        upstream_node_id: str,
        downstream_node_id: str,
    ) -> ProductionEdge:
        run_id = _text(run_id, "run_id")
        upstream_node_id = _text(upstream_node_id, "upstream_node_id")
        downstream_node_id = _text(downstream_node_id, "downstream_node_id")
        with self._write() as connection:
            existing = connection.execute(
                """
                SELECT * FROM production_edges
                WHERE run_id = ? AND upstream_node_id = ? AND downstream_node_id = ?
                """,
                (run_id, upstream_node_id, downstream_node_id),
            ).fetchone()
            if existing is not None:
                return self._edge_from_row(existing)
            edge_id = str(uuid4())
            timestamp = _now_iso()
            connection.execute(
                """
                INSERT INTO production_edges (
                    id, run_id, upstream_node_id, downstream_node_id, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    edge_id,
                    run_id,
                    upstream_node_id,
                    downstream_node_id,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM production_edges WHERE id = ?", (edge_id,)
            ).fetchone()
            return self._edge_from_row(row)

    def list_edges(self, run_id: str) -> list[ProductionEdge]:
        run_id = _text(run_id, "run_id")
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM production_edges WHERE run_id = ? ORDER BY rowid",
                (run_id,),
            ).fetchall()
        finally:
            connection.close()
        return [self._edge_from_row(row) for row in rows]

    def transition_node(
        self,
        node_id: str,
        status: ProductionNodeStatus,
    ) -> ProductionNode:
        node_id = _text(node_id, "node_id")
        target = ProductionNodeStatus(status)
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM production_nodes WHERE id = ?", (node_id,)
            ).fetchone()
            if row is None:
                raise LookupError(f"production node {node_id} not found")
            current = ProductionNodeStatus(row["status"])
            self._validate_transition(current, target, _NODE_NEXT, _NODE_TERMINAL)
            connection.execute(
                "UPDATE production_nodes SET status = ?, updated_at = ? WHERE id = ?",
                (target.value, _now_iso(), node_id),
            )
            updated = connection.execute(
                "SELECT * FROM production_nodes WHERE id = ?", (node_id,)
            ).fetchone()
            return self._node_from_row(updated)

    def transition_run(
        self,
        run_id: str,
        status: ProductionRunStatus,
    ) -> ProductionRun:
        run_id = _text(run_id, "run_id")
        target = ProductionRunStatus(status)
        with self._write() as connection:
            row = connection.execute(
                "SELECT * FROM production_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise LookupError(f"production run {run_id} not found")
            current = ProductionRunStatus(row["status"])
            self._validate_transition(current, target, _RUN_NEXT, _RUN_TERMINAL)
            connection.execute(
                "UPDATE production_runs SET status = ?, updated_at = ? WHERE id = ?",
                (target.value, _now_iso(), run_id),
            )
            updated = connection.execute(
                "SELECT * FROM production_runs WHERE id = ?", (run_id,)
            ).fetchone()
            return self._run_from_row(updated)

    @staticmethod
    def _validate_transition(current, target, allowed, terminal) -> None:
        if current in terminal:
            raise InvalidProductionTransition(
                f"terminal status {current.value} cannot change"
            )
        if target not in allowed.get(current, set()):
            raise InvalidProductionTransition(
                f"invalid transition {current.value} -> {target.value}"
            )
