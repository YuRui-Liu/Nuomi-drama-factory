"""Durable SQLite state for media tasks and provider attempts."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Iterator
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from novelvideo.media_capabilities.diagnostics import sanitize_diagnostics
from novelvideo.media_capabilities.diagnostic_safety import (
    contains_sensitive_value,
    is_sensitive_key,
)
from novelvideo.media_capabilities.models import MediaCapability, MediaTaskStatus
from novelvideo.sqlite_pragmas import configure_sqlite_connection


class TaskStoreConflictError(RuntimeError):
    """Raised when an immutable or concurrency constraint is violated."""


class TaskNotFoundError(LookupError):
    """Raised when a requested task or attempt does not exist."""


class InvalidTaskTransition(RuntimeError):
    """Raised when a task or attempt status transition is not allowed."""


class MediaErrorCode(StrEnum):
    CONFIG_INVALID = "CONFIG_INVALID"
    INPUT_INVALID = "INPUT_INVALID"
    UPLOAD_FAILED = "UPLOAD_FAILED"
    SUBMIT_FAILED = "SUBMIT_FAILED"
    PROVIDER_REJECTED = "PROVIDER_REJECTED"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    POLL_FAILED = "POLL_FAILED"
    OUTPUT_MISSING = "OUTPUT_MISSING"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    QUALITY_FAILED = "QUALITY_FAILED"
    CANCELLED = "CANCELLED"


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
ErrorCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
ErrorMessage = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
ProviderStatus = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]
MAX_DIAGNOSTICS_BYTES = 16 * 1024


class MediaTaskRecord(_Record):
    id: Identifier
    capability: MediaCapability
    idempotency_key: Identifier
    implementation_snapshot: JsonValue
    input_snapshot: JsonValue
    output: JsonValue = Field(default_factory=dict)
    status: MediaTaskStatus
    attempt_count: int
    next_run_at: datetime | None = None
    next_poll_at: datetime | None = None
    heartbeat_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class MediaAttemptRecord(_Record):
    id: Identifier
    task_id: Identifier
    attempt_no: int
    provider_account_id: Identifier
    provider_task_id: Identifier | None = None
    error_code: MediaErrorCode | None = None
    error_message: ErrorMessage | None = None
    implementation_snapshot: JsonValue | None = None
    workflow_version: JsonValue | None = None
    effective_params: JsonValue = Field(default_factory=dict)
    input_asset_hashes: JsonValue = Field(default_factory=list)
    cost: JsonValue | None = None
    output: JsonValue = Field(default_factory=dict)
    provider_status: ProviderStatus | None = None
    diagnostics: JsonValue = Field(default_factory=dict)
    status: MediaTaskStatus
    next_run_at: datetime | None = None
    next_poll_at: datetime | None = None
    heartbeat_at: datetime | None = None
    started_at: datetime
    created_at: datetime
    updated_at: datetime
    submitted_at: datetime | None = None
    completed_at: datetime | None = None


_TERMINAL = {
    MediaTaskStatus.SUCCEEDED,
    MediaTaskStatus.FAILED,
    MediaTaskStatus.QUALITY_FAILED,
    MediaTaskStatus.CANCELLED,
}
_POLL_STATES = {
    MediaTaskStatus.SUBMITTED,
    MediaTaskStatus.RUNNING,
    MediaTaskStatus.DOWNLOADING,
    MediaTaskStatus.VALIDATING,
}
_MAIN_NEXT = {
    MediaTaskStatus.QUEUED: MediaTaskStatus.PREPARING,
    MediaTaskStatus.PREPARING: MediaTaskStatus.UPLOADING,
    MediaTaskStatus.UPLOADING: MediaTaskStatus.SUBMITTED,
    MediaTaskStatus.SUBMITTED: MediaTaskStatus.RUNNING,
    MediaTaskStatus.RUNNING: MediaTaskStatus.DOWNLOADING,
    MediaTaskStatus.DOWNLOADING: MediaTaskStatus.VALIDATING,
    MediaTaskStatus.VALIDATING: MediaTaskStatus.SUCCEEDED,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(timezone.utc).isoformat()


def _json_copy(value: Any) -> JsonValue:
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    return json.loads(encoded, parse_constant=lambda value: _bad_json(value))


def _bad_json(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _reject_sensitive_keys(value: JsonValue) -> None:
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if any(
                is_sensitive_key(key)
                or contains_sensitive_value(str(key), inspect_urls=False)
                for key in current
            ):
                raise TaskStoreConflictError("task_store.prohibited_field")
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
        elif isinstance(current, str) and contains_sensitive_value(current):
            raise TaskStoreConflictError("task_store.prohibited_value")


def _validated_text(value: str, field: str, max_length: int = 512) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise ValueError(f"invalid {field}")
    return normalized


def _json_for_storage(value: Any) -> tuple[JsonValue, str]:
    copied = _json_copy(value)
    _reject_sensitive_keys(copied)
    encoded = json.dumps(
        copied, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return copied, encoded


def _json_from_storage(raw: str | None) -> JsonValue | None:
    if raw is None:
        return None
    return json.loads(raw, parse_constant=lambda value: _bad_json(value))


class TaskStore:
    """Persist media task state with short, serialized write transactions."""

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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS media_tasks (
                    id TEXT PRIMARY KEY,
                    capability TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    implementation_snapshot_json TEXT NOT NULL,
                    input_snapshot_json TEXT NOT NULL,
                    output_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_run_at TEXT,
                    next_poll_at TEXT,
                    heartbeat_at TEXT,
                    retry_from_status TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS media_attempts (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES media_tasks(id) ON DELETE CASCADE,
                    attempt_no INTEGER NOT NULL,
                    provider_account_id TEXT NOT NULL,
                    provider_task_id TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    implementation_snapshot_json TEXT,
                    workflow_version_json TEXT,
                    effective_params_json TEXT NOT NULL DEFAULT '{}',
                    input_asset_hashes_json TEXT NOT NULL DEFAULT '[]',
                    cost_json TEXT,
                    output_json TEXT NOT NULL DEFAULT '{}',
                    provider_status TEXT,
                    diagnostics_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL,
                    next_run_at TEXT,
                    next_poll_at TEXT,
                    heartbeat_at TEXT,
                    retry_from_status TEXT,
                    started_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    submitted_at TEXT,
                    completed_at TEXT,
                    UNIQUE(task_id, attempt_no)
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS media_attempts_one_active
                ON media_attempts(task_id)
                WHERE status NOT IN ('succeeded', 'failed', 'quality_failed', 'cancelled')
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS media_tasks_recoverable
                ON media_tasks(status, next_run_at, created_at, id)
                """
            )
            self._migrate_schema(connection)

    @staticmethod
    def _migrate_schema(connection: sqlite3.Connection) -> None:
        task_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(media_tasks)")
        }
        if "output_json" not in task_columns:
            connection.execute(
                "ALTER TABLE media_tasks ADD COLUMN output_json TEXT NOT NULL DEFAULT '{}'"
            )
        attempt_info = {
            row[1]: row for row in connection.execute("PRAGMA table_info(media_attempts)")
        }
        started_at = attempt_info.get("started_at")
        if started_at is None or started_at[3] != 1 or started_at[4] is not None:
            TaskStore._rebuild_attempts_table(connection, set(attempt_info))
            return
        for column in ("error_code", "error_message"):
            if column not in attempt_info:
                connection.execute(f"ALTER TABLE media_attempts ADD COLUMN {column} TEXT")
        connection.execute(
            """
            UPDATE media_attempts
            SET started_at = created_at
            WHERE started_at IS NULL OR started_at = ''
            """
        )
        nullable_json_columns = (
            "implementation_snapshot_json",
            "workflow_version_json",
            "cost_json",
        )
        for column in nullable_json_columns:
            if column not in attempt_info:
                connection.execute(
                    f"ALTER TABLE media_attempts ADD COLUMN {column} TEXT"
                )
        default_json_columns = {
            "effective_params_json": "{}",
            "input_asset_hashes_json": "[]",
            "output_json": "{}",
            "diagnostics_json": "{}",
        }
        for column, default in default_json_columns.items():
            if column not in attempt_info:
                connection.execute(
                    f"""
                    ALTER TABLE media_attempts
                    ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'
                    """
                )
        if "provider_status" not in attempt_info:
            connection.execute(
                "ALTER TABLE media_attempts ADD COLUMN provider_status TEXT"
            )

    @staticmethod
    def _rebuild_attempts_table(
        connection: sqlite3.Connection,
        existing_columns: set[str],
    ) -> None:
        error_code = "error_code" if "error_code" in existing_columns else "NULL"
        error_message = (
            "error_message" if "error_message" in existing_columns else "NULL"
        )
        started_at = (
            "COALESCE(NULLIF(started_at, ''), created_at)"
            if "started_at" in existing_columns
            else "created_at"
        )
        implementation_snapshot = (
            "implementation_snapshot_json"
            if "implementation_snapshot_json" in existing_columns
            else "NULL"
        )
        workflow_version = (
            "workflow_version_json"
            if "workflow_version_json" in existing_columns
            else "NULL"
        )
        effective_params = (
            "COALESCE(effective_params_json, '{}')"
            if "effective_params_json" in existing_columns
            else "'{}'"
        )
        input_asset_hashes = (
            "COALESCE(input_asset_hashes_json, '[]')"
            if "input_asset_hashes_json" in existing_columns
            else "'[]'"
        )
        cost = "cost_json" if "cost_json" in existing_columns else "NULL"
        output = (
            "COALESCE(output_json, '{}')"
            if "output_json" in existing_columns
            else "'{}'"
        )
        provider_status = (
            "provider_status" if "provider_status" in existing_columns else "NULL"
        )
        diagnostics = (
            "COALESCE(diagnostics_json, '{}')"
            if "diagnostics_json" in existing_columns
            else "'{}'"
        )
        connection.execute("DROP INDEX IF EXISTS media_attempts_one_active")
        connection.execute("ALTER TABLE media_attempts RENAME TO media_attempts_legacy")
        connection.execute(
            """
            CREATE TABLE media_attempts (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES media_tasks(id) ON DELETE CASCADE,
                attempt_no INTEGER NOT NULL,
                provider_account_id TEXT NOT NULL,
                provider_task_id TEXT,
                error_code TEXT,
                error_message TEXT,
                implementation_snapshot_json TEXT,
                workflow_version_json TEXT,
                effective_params_json TEXT NOT NULL DEFAULT '{}',
                input_asset_hashes_json TEXT NOT NULL DEFAULT '[]',
                cost_json TEXT,
                output_json TEXT NOT NULL DEFAULT '{}',
                provider_status TEXT,
                diagnostics_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL,
                next_run_at TEXT,
                next_poll_at TEXT,
                heartbeat_at TEXT,
                retry_from_status TEXT,
                started_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                submitted_at TEXT,
                completed_at TEXT,
                UNIQUE(task_id, attempt_no)
            )
            """
        )
        connection.execute(
            f"""
            INSERT INTO media_attempts (
                id, task_id, attempt_no, provider_account_id, provider_task_id,
                error_code, error_message, implementation_snapshot_json,
                workflow_version_json, effective_params_json,
                input_asset_hashes_json, cost_json, output_json,
                provider_status, diagnostics_json,
                status, next_run_at, next_poll_at,
                heartbeat_at, retry_from_status, started_at, created_at, updated_at,
                submitted_at, completed_at
            )
            SELECT
                id, task_id, attempt_no, provider_account_id, provider_task_id,
                {error_code}, {error_message}, {implementation_snapshot},
                {workflow_version}, {effective_params}, {input_asset_hashes},
                {cost}, {output}, {provider_status}, {diagnostics},
                status, next_run_at, next_poll_at,
                heartbeat_at, retry_from_status, {started_at}, created_at, updated_at,
                submitted_at, completed_at
            FROM media_attempts_legacy
            """
        )
        connection.execute("DROP TABLE media_attempts_legacy")
        connection.execute(
            """
            CREATE UNIQUE INDEX media_attempts_one_active
            ON media_attempts(task_id)
            WHERE status NOT IN ('succeeded', 'failed', 'quality_failed', 'cancelled')
            """
        )

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> MediaTaskRecord:
        implementation = json.loads(
            row["implementation_snapshot_json"],
            parse_constant=lambda value: _bad_json(value),
        )
        input_snapshot = json.loads(
            row["input_snapshot_json"],
            parse_constant=lambda value: _bad_json(value),
        )
        output = json.loads(
            row["output_json"],
            parse_constant=lambda value: _bad_json(value),
        )
        return MediaTaskRecord.model_validate(
            {
                "id": row["id"],
                "capability": row["capability"],
                "idempotency_key": row["idempotency_key"],
                "implementation_snapshot": implementation,
                "input_snapshot": input_snapshot,
                "output": output,
                "status": row["status"],
                "attempt_count": row["attempt_count"],
                "next_run_at": row["next_run_at"],
                "next_poll_at": row["next_poll_at"],
                "heartbeat_at": row["heartbeat_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "completed_at": row["completed_at"],
            }
        )

    @staticmethod
    def _attempt_from_row(row: sqlite3.Row) -> MediaAttemptRecord:
        return MediaAttemptRecord.model_validate(
            {
                "id": row["id"],
                "task_id": row["task_id"],
                "attempt_no": row["attempt_no"],
                "provider_account_id": row["provider_account_id"],
                "provider_task_id": row["provider_task_id"],
                "error_code": row["error_code"],
                "error_message": row["error_message"],
                "implementation_snapshot": _json_from_storage(
                    row["implementation_snapshot_json"]
                ),
                "workflow_version": _json_from_storage(row["workflow_version_json"]),
                "effective_params": _json_from_storage(row["effective_params_json"]),
                "input_asset_hashes": _json_from_storage(
                    row["input_asset_hashes_json"]
                ),
                "cost": _json_from_storage(row["cost_json"]),
                "output": _json_from_storage(row["output_json"]),
                "provider_status": row["provider_status"],
                "diagnostics": _json_from_storage(row["diagnostics_json"]),
                "status": row["status"],
                "next_run_at": row["next_run_at"],
                "next_poll_at": row["next_poll_at"],
                "heartbeat_at": row["heartbeat_at"],
                "started_at": row["started_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "submitted_at": row["submitted_at"],
                "completed_at": row["completed_at"],
            }
        )

    def get_task(self, task_id: str) -> MediaTaskRecord | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM media_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            return None if row is None else self._task_from_row(row)
        finally:
            connection.close()

    def get_attempt(self, attempt_id: str) -> MediaAttemptRecord | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM media_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
            return None if row is None else self._attempt_from_row(row)
        finally:
            connection.close()

    def list_attempts(self, task_id: str) -> list[MediaAttemptRecord]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM media_attempts WHERE task_id = ? ORDER BY attempt_no",
                (task_id,),
            ).fetchall()
            return [self._attempt_from_row(row) for row in rows]
        finally:
            connection.close()

    def create_task(
        self,
        capability: MediaCapability,
        idempotency_key: str,
        implementation_snapshot: JsonValue,
        input_snapshot: JsonValue,
    ) -> MediaTaskRecord:
        key = idempotency_key.strip()
        if not key:
            raise ValueError("idempotency_key must be non-empty")
        capability = MediaCapability(capability)
        implementation = _json_copy(implementation_snapshot)
        inputs = _json_copy(input_snapshot)
        _reject_sensitive_keys(implementation)
        _reject_sensitive_keys(inputs)
        implementation_json = json.dumps(
            implementation, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        input_json = json.dumps(
            inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        with self._write() as connection:
            existing = connection.execute(
                "SELECT * FROM media_tasks WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if existing is not None:
                if (
                    existing["capability"] != capability.value
                    or existing["implementation_snapshot_json"] != implementation_json
                    or existing["input_snapshot_json"] != input_json
                ):
                    raise TaskStoreConflictError(
                        f"idempotency key {key!r} has different immutable input"
                    )
                return self._task_from_row(existing)
            timestamp = _iso(_now())
            task_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO media_tasks (
                    id, capability, idempotency_key, implementation_snapshot_json,
                    input_snapshot_json, output_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    capability.value,
                    key,
                    implementation_json,
                    input_json,
                    "{}",
                    MediaTaskStatus.QUEUED.value,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM media_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            return self._task_from_row(row)

    def start_attempt(
        self,
        task_id: str,
        provider_account_id: str,
        *,
        implementation_snapshot: JsonValue | None = None,
        workflow_version: JsonValue | None = None,
        effective_params: JsonValue | None = None,
        input_asset_hashes: JsonValue | None = None,
    ) -> MediaAttemptRecord:
        task_id = _validated_text(task_id, "task_id")
        account_id = _validated_text(provider_account_id, "provider_account_id")
        implementation_json = (
            None
            if implementation_snapshot is None
            else _json_for_storage(implementation_snapshot)[1]
        )
        workflow_json = (
            None
            if workflow_version is None
            else _json_for_storage(workflow_version)[1]
        )
        params_json = _json_for_storage(
            {} if effective_params is None else effective_params
        )[1]
        hashes_json = _json_for_storage(
            [] if input_asset_hashes is None else input_asset_hashes
        )[1]
        with self._write() as connection:
            task = connection.execute(
                "SELECT * FROM media_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise TaskNotFoundError(f"task {task_id} not found")
            if implementation_json is None:
                inherited = _json_from_storage(task["implementation_snapshot_json"])
                if inherited is None:
                    raise TaskStoreConflictError(
                        "task_store.missing_implementation_snapshot"
                    )
                _reject_sensitive_keys(inherited)
                implementation_json = task["implementation_snapshot_json"]
            active = connection.execute(
                """
                SELECT 1 FROM media_attempts
                WHERE task_id = ?
                  AND status NOT IN ('succeeded', 'failed', 'quality_failed', 'cancelled')
                """,
                (task_id,),
            ).fetchone()
            if active is not None:
                raise TaskStoreConflictError(f"task {task_id} already has an active attempt")
            self._validate_transition(MediaTaskStatus(task["status"]), MediaTaskStatus.PREPARING)
            attempt_no = int(task["attempt_count"]) + 1
            timestamp = _iso(_now())
            attempt_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO media_attempts (
                    id, task_id, attempt_no, provider_account_id,
                    implementation_snapshot_json, workflow_version_json,
                    effective_params_json, input_asset_hashes_json,
                    status, started_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    task_id,
                    attempt_no,
                    account_id,
                    implementation_json,
                    workflow_json,
                    params_json,
                    hashes_json,
                    MediaTaskStatus.PREPARING.value,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                UPDATE media_tasks
                SET status = ?, attempt_count = ?, updated_at = ?
                WHERE id = ?
                """,
                (MediaTaskStatus.PREPARING.value, attempt_no, timestamp, task_id),
            )
            row = connection.execute(
                "SELECT * FROM media_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
            return self._attempt_from_row(row)

    def record_provider_task(
        self, attempt_id: str, provider_task_id: str
    ) -> MediaAttemptRecord:
        attempt_id = _validated_text(attempt_id, "attempt_id")
        remote_id = _validated_text(provider_task_id, "provider_task_id")
        with self._write() as connection:
            row = self._attempt_row(connection, attempt_id)
            if row["provider_task_id"] is not None:
                if row["provider_task_id"] != remote_id:
                    raise TaskStoreConflictError(
                        f"attempt {attempt_id} provider_task_id is immutable"
                    )
                return self._attempt_from_row(row)
            current = MediaTaskStatus(row["status"])
            if current not in {MediaTaskStatus.PREPARING, MediaTaskStatus.UPLOADING}:
                raise InvalidTaskTransition(f"cannot record provider task from {current.value}")
            timestamp = _iso(_now())
            connection.execute(
                """
                UPDATE media_attempts
                SET provider_task_id = ?, status = ?, submitted_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (remote_id, MediaTaskStatus.SUBMITTED.value, timestamp, timestamp, attempt_id),
            )
            self._sync_task(connection, row["task_id"], MediaTaskStatus.SUBMITTED, timestamp)
            return self._attempt_from_row(self._attempt_row(connection, attempt_id))

    def complete_success(
        self, attempt_id: str, output: JsonValue
    ) -> MediaAttemptRecord:
        attempt_id = _validated_text(attempt_id, "attempt_id")
        _, output_json = _json_for_storage(output)
        with self._write() as connection:
            row = self._attempt_row(connection, attempt_id)
            current = MediaTaskStatus(row["status"])
            self._validate_transition(current, MediaTaskStatus.SUCCEEDED)
            timestamp = _iso(_now())
            connection.execute(
                """
                UPDATE media_attempts
                SET status = ?, output_json = ?, updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    MediaTaskStatus.SUCCEEDED.value,
                    output_json,
                    timestamp,
                    timestamp,
                    attempt_id,
                ),
            )
            connection.execute(
                """
                UPDATE media_tasks
                SET status = ?, output_json = ?, next_run_at = NULL,
                    retry_from_status = NULL, updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    MediaTaskStatus.SUCCEEDED.value,
                    output_json,
                    timestamp,
                    timestamp,
                    row["task_id"],
                ),
            )
            return self._attempt_from_row(self._attempt_row(connection, attempt_id))

    def record_attempt_cost(
        self, attempt_id: str, cost: JsonValue
    ) -> MediaAttemptRecord:
        attempt_id = _validated_text(attempt_id, "attempt_id")
        _, cost_json = _json_for_storage(cost)
        with self._write() as connection:
            row = self._attempt_row(connection, attempt_id)
            if MediaTaskStatus(row["status"]) in _TERMINAL:
                raise InvalidTaskTransition("terminal attempt cost cannot change")
            timestamp = _iso(_now())
            connection.execute(
                """
                UPDATE media_attempts
                SET cost_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (cost_json, timestamp, attempt_id),
            )
            return self._attempt_from_row(self._attempt_row(connection, attempt_id))

    def fail_attempt(
        self,
        attempt_id: str,
        error_code: MediaErrorCode | str,
        error_message: str,
        *,
        retry_task: bool = False,
        quality_failed: bool = False,
    ) -> MediaAttemptRecord:
        attempt_id = _validated_text(attempt_id, "attempt_id")
        error_code_text = _validated_text(str(error_code), "error_code", 128)
        _reject_sensitive_keys(error_code_text)
        try:
            stable_error_code = MediaErrorCode(error_code_text)
        except ValueError:
            raise ValueError("invalid error_code") from None
        error_message = _validated_text(error_message, "error_message", 2048)
        _reject_sensitive_keys(error_message)
        target = (
            MediaTaskStatus.QUALITY_FAILED
            if quality_failed
            else MediaTaskStatus.FAILED
        )
        with self._write() as connection:
            row = self._attempt_row(connection, attempt_id)
            current = MediaTaskStatus(row["status"])
            self._validate_transition(current, target)
            timestamp = _iso(_now())
            connection.execute(
                """
                UPDATE media_attempts
                SET status = ?, error_code = ?, error_message = ?,
                    updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    target.value,
                    stable_error_code.value,
                    error_message,
                    timestamp,
                    timestamp,
                    attempt_id,
                ),
            )
            task_status = MediaTaskStatus.QUEUED if retry_task else target
            task_completed = None if retry_task else timestamp
            self._sync_task(
                connection,
                row["task_id"],
                task_status,
                timestamp,
                completed_at=task_completed,
            )
            return self._attempt_from_row(self._attempt_row(connection, attempt_id))

    def mark_unknown(
        self,
        attempt_id: str,
        error_code: MediaErrorCode | str,
        error_message: str,
    ) -> MediaAttemptRecord:
        """Persist an ambiguous provider submission without permitting auto-retry."""
        attempt_id = _validated_text(attempt_id, "attempt_id")
        error_code_text = _validated_text(str(error_code), "error_code", 128)
        _reject_sensitive_keys(error_code_text)
        try:
            stable_error_code = MediaErrorCode(error_code_text)
        except ValueError:
            raise ValueError("invalid error_code") from None
        error_message = _validated_text(error_message, "error_message", 2048)
        _reject_sensitive_keys(error_message)
        with self._write() as connection:
            row = self._attempt_row(connection, attempt_id)
            current = MediaTaskStatus(row["status"])
            self._validate_transition(current, MediaTaskStatus.UNKNOWN)
            timestamp = _iso(_now())
            connection.execute(
                """
                UPDATE media_attempts
                SET status = ?, error_code = ?, error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    MediaTaskStatus.UNKNOWN.value,
                    stable_error_code.value,
                    error_message,
                    timestamp,
                    attempt_id,
                ),
            )
            self._sync_task(
                connection,
                row["task_id"],
                MediaTaskStatus.UNKNOWN,
                timestamp,
            )
            return self._attempt_from_row(self._attempt_row(connection, attempt_id))

    @staticmethod
    def _attempt_row(
        connection: sqlite3.Connection, attempt_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM media_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise TaskNotFoundError(f"attempt {attempt_id} not found")
        return row

    @staticmethod
    def _validate_transition(
        current: MediaTaskStatus, target: MediaTaskStatus
    ) -> None:
        if current in _TERMINAL:
            raise InvalidTaskTransition(f"terminal status {current.value} cannot change")
        allowed = target == _MAIN_NEXT.get(current)
        allowed |= target in {MediaTaskStatus.FAILED, MediaTaskStatus.CANCEL_REQUESTED}
        allowed |= current in {
            MediaTaskStatus.PREPARING,
            MediaTaskStatus.UPLOADING,
        } and target == MediaTaskStatus.UNKNOWN
        allowed |= current == MediaTaskStatus.CANCEL_REQUESTED and target in {
            MediaTaskStatus.CANCELLED,
            MediaTaskStatus.FAILED,
        }
        allowed |= current in {
            MediaTaskStatus.SUBMITTED,
            MediaTaskStatus.RUNNING,
        } and target == MediaTaskStatus.RETRY_WAIT
        allowed |= current == MediaTaskStatus.VALIDATING and target == MediaTaskStatus.QUALITY_FAILED
        if not allowed:
            raise InvalidTaskTransition(
                f"invalid transition {current.value} -> {target.value}"
            )

    @staticmethod
    def _sync_task(
        connection: sqlite3.Connection,
        task_id: str,
        status: MediaTaskStatus,
        timestamp: str,
        *,
        next_run_at: str | None = None,
        retry_from_status: str | None = None,
        completed_at: str | None = None,
    ) -> None:
        connection.execute(
            """
            UPDATE media_tasks SET
                status = ?, next_run_at = ?, retry_from_status = ?,
                updated_at = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                status.value,
                next_run_at,
                retry_from_status,
                timestamp,
                completed_at,
                task_id,
            ),
        )

    def transition_attempt(
        self,
        attempt_id: str,
        status: MediaTaskStatus,
        *,
        next_run_at: datetime | None = None,
    ) -> MediaAttemptRecord:
        target = MediaTaskStatus(status)
        if target in {
            MediaTaskStatus.SUCCEEDED,
            MediaTaskStatus.FAILED,
            MediaTaskStatus.QUALITY_FAILED,
        }:
            raise InvalidTaskTransition(
                f"status {target.value} requires a dedicated atomic terminal API"
            )
        with self._write() as connection:
            row = self._attempt_row(connection, attempt_id)
            current = MediaTaskStatus(row["status"])
            if target in _POLL_STATES and not row["provider_task_id"]:
                raise TaskStoreConflictError(
                    f"attempt {attempt_id} requires provider_task_id"
                )
            self._validate_transition(current, target)
            if target == MediaTaskStatus.RETRY_WAIT and next_run_at is None:
                raise ValueError("next_run_at is required for retry_wait")
            timestamp = _iso(_now())
            run_at = _iso(next_run_at) if target == MediaTaskStatus.RETRY_WAIT else None
            retry_from = current.value if target == MediaTaskStatus.RETRY_WAIT else None
            completed = timestamp if target in _TERMINAL else None
            connection.execute(
                """
                UPDATE media_attempts SET
                    status = ?, next_run_at = ?, retry_from_status = ?,
                    updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (target.value, run_at, retry_from, timestamp, completed, attempt_id),
            )
            self._sync_task(
                connection,
                row["task_id"],
                target,
                timestamp,
                next_run_at=run_at,
                retry_from_status=retry_from,
                completed_at=completed,
            )
            return self._attempt_from_row(self._attempt_row(connection, attempt_id))

    def transition_task(
        self,
        task_id: str,
        status: MediaTaskStatus,
        *,
        next_run_at: datetime | None = None,
    ) -> MediaTaskRecord:
        connection = self._connect()
        try:
            attempt = connection.execute(
                """
                SELECT id FROM media_attempts
                WHERE task_id = ?
                ORDER BY attempt_no DESC LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        finally:
            connection.close()
        if attempt is None:
            task = self.get_task(task_id)
            if task is None:
                raise TaskNotFoundError(f"task {task_id} not found")
            raise TaskStoreConflictError(f"task {task_id} has no attempt")
        self.transition_attempt(attempt["id"], status, next_run_at=next_run_at)
        task = self.get_task(task_id)
        assert task is not None
        return task

    def mark_running(self, attempt_id: str) -> MediaAttemptRecord:
        return self.transition_attempt(attempt_id, MediaTaskStatus.RUNNING)

    def resume_retry(self, task_id: str, now: datetime) -> MediaAttemptRecord:
        with self._write() as connection:
            task = connection.execute(
                "SELECT * FROM media_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise TaskNotFoundError(f"task {task_id} not found")
            if task["status"] != MediaTaskStatus.RETRY_WAIT.value:
                raise InvalidTaskTransition(f"task {task_id} is not in retry_wait")
            if task["next_run_at"] is None or datetime.fromisoformat(
                task["next_run_at"]
            ) > now:
                raise TaskStoreConflictError(f"task {task_id} retry is not due")
            attempt = connection.execute(
                """
                SELECT * FROM media_attempts
                WHERE task_id = ? ORDER BY attempt_no DESC LIMIT 1
                """,
                (task_id,),
            ).fetchone()
            if attempt is None:
                raise TaskStoreConflictError(f"task {task_id} has no retry attempt")
            target = MediaTaskStatus(attempt["retry_from_status"])
            if attempt["provider_task_id"] and target not in {
                MediaTaskStatus.SUBMITTED,
                MediaTaskStatus.RUNNING,
            }:
                target = MediaTaskStatus.SUBMITTED
            timestamp = _iso(_now())
            connection.execute(
                """
                UPDATE media_attempts SET
                    status = ?, next_run_at = NULL, retry_from_status = NULL,
                    updated_at = ? WHERE id = ?
                """,
                (target.value, timestamp, attempt["id"]),
            )
            self._sync_task(connection, task_id, target, timestamp)
            return self._attempt_from_row(self._attempt_row(connection, attempt["id"]))

    def recoverable(self, now: datetime) -> list[MediaTaskRecord]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM media_tasks
                WHERE status IN ('submitted', 'running')
                   OR (status = 'retry_wait' AND next_run_at <= ?)
                ORDER BY
                    CASE WHEN status = 'retry_wait' THEN next_run_at ELSE created_at END,
                    created_at,
                    id
                """,
                (_iso(now),),
            ).fetchall()
            return [self._task_from_row(row) for row in rows]
        finally:
            connection.close()

    def get_public_diagnostics(self, attempt_id: str) -> dict[str, JsonValue]:
        """Return the attempt's diagnostics projected onto the public contract."""
        attempt = self.get_attempt(attempt_id)
        raw = attempt.diagnostics if isinstance(attempt.diagnostics, dict) else {}
        return sanitize_diagnostics(raw)

    def record_provider_status(
        self,
        attempt_id: str,
        provider_status: str,
        diagnostics: JsonValue | None = None,
        *,
        now: datetime | None = None,
        next_poll_at: datetime | None = None,
    ) -> MediaAttemptRecord:
        attempt_id = _validated_text(attempt_id, "attempt_id")
        provider_status = _validated_text(provider_status, "provider_status", 128)
        _reject_sensitive_keys(provider_status)
        diagnostics_json: str | None = None
        if diagnostics is not None:
            _, diagnostics_json = _json_for_storage(diagnostics)
            if len(diagnostics_json.encode("utf-8")) > MAX_DIAGNOSTICS_BYTES:
                raise ValueError("invalid diagnostics size")
        heartbeat_at = now or _now()
        timestamp = _iso(_now())
        with self._write() as connection:
            row = self._attempt_row(connection, attempt_id)
            if MediaTaskStatus(row["status"]) in _TERMINAL:
                raise InvalidTaskTransition("terminal attempt diagnostics cannot change")
            saved_diagnostics = (
                row["diagnostics_json"]
                if diagnostics_json is None
                else diagnostics_json
            )
            connection.execute(
                """
                UPDATE media_attempts
                SET provider_status = ?, diagnostics_json = ?, heartbeat_at = ?,
                    next_poll_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    provider_status,
                    saved_diagnostics,
                    _iso(heartbeat_at),
                    _iso(next_poll_at),
                    timestamp,
                    attempt_id,
                ),
            )
            connection.execute(
                """
                UPDATE media_tasks
                SET heartbeat_at = ?, next_poll_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    _iso(heartbeat_at),
                    _iso(next_poll_at),
                    timestamp,
                    row["task_id"],
                ),
            )
            return self._attempt_from_row(self._attempt_row(connection, attempt_id))

    def heartbeat(
        self,
        attempt_id: str,
        *,
        now: datetime | None = None,
        next_poll_at: datetime | None = None,
    ) -> MediaAttemptRecord:
        heartbeat_at = now or _now()
        timestamp = _iso(_now())
        with self._write() as connection:
            row = self._attempt_row(connection, attempt_id)
            connection.execute(
                """
                UPDATE media_attempts
                SET heartbeat_at = ?, next_poll_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (_iso(heartbeat_at), _iso(next_poll_at), timestamp, attempt_id),
            )
            connection.execute(
                """
                UPDATE media_tasks
                SET heartbeat_at = ?, next_poll_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (_iso(heartbeat_at), _iso(next_poll_at), timestamp, row["task_id"]),
            )
            return self._attempt_from_row(self._attempt_row(connection, attempt_id))


MediaTaskStore = TaskStore
