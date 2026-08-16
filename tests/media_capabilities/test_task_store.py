from __future__ import annotations

import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from novelvideo.media_capabilities.models import MediaCapability, MediaTaskStatus
from novelvideo.media_capabilities.task_store import (
    InvalidTaskTransition,
    MediaAttemptRecord,
    MediaErrorCode,
    MediaTaskRecord,
    TaskNotFoundError,
    TaskStore,
    TaskStoreConflictError,
)


NOW = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)


def create_task(store: TaskStore, key: str = "request-1") -> MediaTaskRecord:
    return store.create_task(
        MediaCapability.IMAGE_SINGLE,
        key,
        {"implementation": "image-primary", "version": 3},
        {"prompt": "a lighthouse", "options": {"seed": 7}},
    )


def submitted_attempt(store: TaskStore) -> MediaAttemptRecord:
    task = create_task(store)
    attempt = store.start_attempt(task.id, "provider-account-1")
    return store.record_provider_task(attempt.id, "remote-task-1")


def test_schema_enables_foreign_keys_and_cascades_attempts(tmp_path: Path) -> None:
    database = tmp_path / "nested" / "tasks.db"
    store = TaskStore(database)
    task = create_task(store)
    store.start_attempt(task.id, "provider-account-1")

    connection = store._connect()
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(media_attempts)"
        ).fetchall()
        assert any(row[2] == "media_tasks" and row[6] == "CASCADE" for row in foreign_keys)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM media_tasks WHERE id = ?", (task.id,))
        connection.commit()
        assert connection.execute("SELECT COUNT(*) FROM media_attempts").fetchone()[0] == 0
    finally:
        connection.close()


def test_schema_and_records_include_output_and_attempt_diagnostics(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    task = create_task(store)
    attempt = store.start_attempt(task.id, "provider-account-1")

    assert task.output == {}
    assert attempt.started_at is not None
    assert attempt.error_code is None
    assert attempt.error_message is None
    connection = store._connect()
    try:
        task_columns = {
            row[1]: row for row in connection.execute("PRAGMA table_info(media_tasks)")
        }
        attempt_columns = {
            row[1]: row
            for row in connection.execute("PRAGMA table_info(media_attempts)")
        }
        assert task_columns["output_json"][4] == "'{}'"
        assert {"error_code", "error_message", "started_at"} <= set(attempt_columns)
        assert {"provider_status", "diagnostics_json"} <= set(attempt_columns)
        assert attempt_columns["started_at"][3] == 1
        assert attempt_columns["error_code"][3] == 0
        assert attempt_columns["error_message"][3] == 0
    finally:
        connection.close()


def test_attempt_persists_trace_snapshots_cost_and_attempt_output(tmp_path: Path) -> None:
    database = tmp_path / "tasks.db"
    store = TaskStore(database)
    task = create_task(store)
    implementation = {"id": "image-primary", "revision": 4}
    effective_params = {"width": 1024, "steps": 20}
    input_hashes = ["sha256:aaa", "sha256:bbb"]
    attempt = store.start_attempt(
        task.id,
        "provider-a",
        implementation_snapshot=implementation,
        workflow_version=7,
        effective_params=effective_params,
        input_asset_hashes=input_hashes,
    )
    implementation["revision"] = 99
    effective_params["steps"] = 99
    input_hashes.append("sha256:ccc")

    cost = {"currency": "USD", "amount": 0.125, "units": {"images": 1}}
    charged = store.record_attempt_cost(attempt.id, cost)
    charged = store.transition_attempt(charged.id, MediaTaskStatus.UPLOADING)
    charged = store.record_provider_task(charged.id, "remote-trace")
    charged = store.transition_attempt(charged.id, MediaTaskStatus.RUNNING)
    charged = store.transition_attempt(charged.id, MediaTaskStatus.DOWNLOADING)
    charged = store.transition_attempt(charged.id, MediaTaskStatus.VALIDATING)
    completed = store.complete_success(
        charged.id,
        {"artifacts": [{"sha256": "sha256:result", "path": "final.png"}]},
    )

    restarted = TaskStore(database)
    saved = restarted.get_attempt(completed.id)
    assert saved.implementation_snapshot == {"id": "image-primary", "revision": 4}
    assert saved.workflow_version == 7
    assert saved.effective_params == {"width": 1024, "steps": 20}
    assert saved.input_asset_hashes == ["sha256:aaa", "sha256:bbb"]
    assert saved.cost == cost
    assert saved.output == {
        "artifacts": [{"sha256": "sha256:result", "path": "final.png"}]
    }


def test_start_attempt_inherits_task_implementation_or_uses_explicit_override(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    inherited_task = create_task(store, "inherit-implementation")
    inherited = store.start_attempt(inherited_task.id, "provider-a")
    assert inherited.implementation_snapshot == inherited_task.implementation_snapshot

    overridden_task = create_task(store, "override-implementation")
    overridden = store.start_attempt(
        overridden_task.id,
        "provider-b",
        implementation_snapshot={"implementation": "fallback", "revision": 8},
    )
    assert overridden.implementation_snapshot == {
        "implementation": "fallback",
        "revision": 8,
    }


def test_record_provider_status_persists_bounded_diagnostics_atomically(
    tmp_path: Path,
) -> None:
    database = tmp_path / "tasks.db"
    store = TaskStore(database)
    attempt = submitted_attempt(store)
    next_poll = NOW + timedelta(seconds=20)

    updated = store.record_provider_status(
        attempt.id,
        "processing",
        {"progress": 0.5, "queue": {"position": 2}},
        now=NOW,
        next_poll_at=next_poll,
    )

    assert updated.provider_status == "processing"
    assert updated.diagnostics == {"progress": 0.5, "queue": {"position": 2}}
    assert updated.heartbeat_at == NOW
    assert updated.next_poll_at == next_poll
    restarted = TaskStore(database).get_attempt(attempt.id)
    assert restarted.provider_status == "processing"
    assert restarted.diagnostics == updated.diagnostics


def test_diagnostics_reject_oversize_or_non_json_without_partial_update(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    attempt = submitted_attempt(store)

    with pytest.raises(ValueError, match="diagnostics"):
        store.record_provider_status(
            attempt.id,
            "processing",
            {"detail": "x" * 20_000},
        )
    with pytest.raises((TypeError, ValueError)):
        store.record_provider_status(
            attempt.id,
            "processing",
            {"not_json": object()},
        )

    unchanged = store.get_attempt(attempt.id)
    assert unchanged.provider_status is None
    assert unchanged.diagnostics == {}


def test_attempt_trace_json_defaults_are_safe_for_historical_rows(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    task = create_task(store)
    attempt = store.start_attempt(task.id, "provider-a")

    assert attempt.implementation_snapshot == task.implementation_snapshot
    assert attempt.workflow_version is None
    assert attempt.effective_params == {}
    assert attempt.input_asset_hashes == []
    assert attempt.cost is None
    assert attempt.output == {}


def test_attempt_trace_json_is_revalidated_on_read(tmp_path: Path) -> None:
    database = tmp_path / "tasks.db"
    store = TaskStore(database)
    attempt = store.start_attempt(create_task(store).id, "provider-a")
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE media_attempts SET effective_params_json = ? WHERE id = ?",
            ('{"bad": NaN}', attempt.id),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises((ValidationError, ValueError)):
        store.get_attempt(attempt.id)


def test_old_schema_migration_backfills_and_enforces_started_at_not_null(
    tmp_path: Path,
) -> None:
    database = tmp_path / "old-tasks.db"
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE media_tasks (
                id TEXT PRIMARY KEY,
                capability TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                implementation_snapshot_json TEXT NOT NULL,
                input_snapshot_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_run_at TEXT,
                next_poll_at TEXT,
                heartbeat_at TEXT,
                retry_from_status TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE media_attempts (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES media_tasks(id) ON DELETE CASCADE,
                attempt_no INTEGER NOT NULL,
                provider_account_id TEXT NOT NULL,
                provider_task_id TEXT,
                status TEXT NOT NULL,
                next_run_at TEXT,
                next_poll_at TEXT,
                heartbeat_at TEXT,
                retry_from_status TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                submitted_at TEXT,
                completed_at TEXT,
                UNIQUE(task_id, attempt_no)
            );
            """
        )
        timestamp = NOW.isoformat()
        connection.execute(
            """
            INSERT INTO media_tasks (
                id, capability, idempotency_key, implementation_snapshot_json,
                input_snapshot_json, status, attempt_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "old-task",
                MediaCapability.IMAGE_SINGLE.value,
                "old-request",
                '{"implementation":"legacy"}',
                '{"prompt":"legacy"}',
                MediaTaskStatus.FAILED.value,
                1,
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO media_attempts (
                id, task_id, attempt_no, provider_account_id, status,
                created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "old-attempt",
                "old-task",
                1,
                "old-provider",
                MediaTaskStatus.FAILED.value,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    store = TaskStore(database)
    migrated = store.get_attempt("old-attempt")
    assert migrated.started_at == NOW
    assert migrated.implementation_snapshot is None
    assert migrated.workflow_version is None
    assert migrated.effective_params == {}
    assert migrated.input_asset_hashes == []
    assert migrated.cost is None
    assert migrated.output == {}
    connection = store._connect()
    try:
        columns = {
            row[1]: row
            for row in connection.execute("PRAGMA table_info(media_attempts)")
        }
        assert columns["started_at"][3] == 1
        assert columns["started_at"][4] is None
        assert columns["error_code"][3] == 0
        assert columns["error_message"][3] == 0
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
            connection.execute(
                """
                INSERT INTO media_attempts (
                    id, task_id, attempt_no, provider_account_id, status,
                    started_at, created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "null-started-at",
                    "old-task",
                    2,
                    "other-provider",
                    MediaTaskStatus.FAILED.value,
                    None,
                    NOW.isoformat(),
                    NOW.isoformat(),
                    NOW.isoformat(),
                ),
            )
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
            connection.execute(
                """
                INSERT INTO media_attempts (
                    id, task_id, attempt_no, provider_account_id, status,
                    created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "omitted-started-at",
                    "old-task",
                    2,
                    "other-provider",
                    MediaTaskStatus.FAILED.value,
                    NOW.isoformat(),
                    NOW.isoformat(),
                    NOW.isoformat(),
                ),
            )
    finally:
        connection.rollback()
        connection.close()


def test_nullable_started_at_intermediate_schema_is_rebuilt_without_default(
    tmp_path: Path,
) -> None:
    database = tmp_path / "intermediate.db"
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE media_tasks (
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
            );
            CREATE TABLE media_attempts (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES media_tasks(id) ON DELETE CASCADE,
                attempt_no INTEGER NOT NULL,
                provider_account_id TEXT NOT NULL,
                provider_task_id TEXT,
                error_code TEXT,
                error_message TEXT,
                status TEXT NOT NULL,
                next_run_at TEXT,
                next_poll_at TEXT,
                heartbeat_at TEXT,
                retry_from_status TEXT,
                started_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                submitted_at TEXT,
                completed_at TEXT,
                UNIQUE(task_id, attempt_no)
            );
            """
        )
        timestamp = NOW.isoformat()
        connection.execute(
            """
            INSERT INTO media_tasks (
                id, capability, idempotency_key, implementation_snapshot_json,
                input_snapshot_json, status, attempt_count, created_at, updated_at
            ) VALUES ('middle-task', 'image.single', 'middle-key', '{}', '{}',
                      'failed', 2, ?, ?)
            """,
            (timestamp, timestamp),
        )
        connection.executemany(
            """
            INSERT INTO media_attempts (
                id, task_id, attempt_no, provider_account_id, status,
                started_at, created_at, updated_at, completed_at
            ) VALUES (?, 'middle-task', ?, 'provider', 'failed', ?, ?, ?, ?)
            """,
            [
                ("kept-start", 1, timestamp, timestamp, timestamp, timestamp),
                ("null-start", 2, None, timestamp, timestamp, timestamp),
            ],
        )
        connection.commit()
    finally:
        connection.close()

    store = TaskStore(database)
    assert store.get_attempt("kept-start").started_at == NOW
    assert store.get_attempt("null-start").started_at == NOW
    connection = store._connect()
    try:
        started_at = next(
            row
            for row in connection.execute("PRAGMA table_info(media_attempts)")
            if row[1] == "started_at"
        )
        assert started_at[3] == 1
        assert started_at[4] is None
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
            connection.execute(
                """
                INSERT INTO media_attempts (
                    id, task_id, attempt_no, provider_account_id, status,
                    started_at, created_at, updated_at, completed_at
                ) VALUES ('bad-null', 'middle-task', 3, 'provider', 'failed',
                          NULL, ?, ?, ?)
                """,
                (NOW.isoformat(), NOW.isoformat(), NOW.isoformat()),
            )
    finally:
        connection.rollback()
        connection.close()

def test_complete_success_atomically_persists_output_and_completion(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    attempt = submitted_attempt(store)
    attempt = store.transition_attempt(attempt.id, MediaTaskStatus.RUNNING)
    attempt = store.transition_attempt(attempt.id, MediaTaskStatus.DOWNLOADING)
    attempt = store.transition_attempt(attempt.id, MediaTaskStatus.VALIDATING)

    completed = store.complete_success(
        attempt.id,
        {"artifacts": [{"path": "render/final.mp4", "sha256": "abc"}]},
    )

    task = store.get_task(attempt.task_id)
    assert completed.status == MediaTaskStatus.SUCCEEDED
    assert completed.completed_at is not None
    assert task.status == MediaTaskStatus.SUCCEEDED
    assert task.completed_at == completed.completed_at
    assert task.output == {
        "artifacts": [{"path": "render/final.mp4", "sha256": "abc"}]
    }


def test_fail_attempt_can_requeue_task_for_a_new_provider_attempt(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    task = create_task(store)
    first = store.start_attempt(task.id, "provider-a")

    failed = store.fail_attempt(
        first.id,
        MediaErrorCode.PROVIDER_TIMEOUT,
        "timed out",
        retry_task=True,
    )
    requeued = store.get_task(task.id)
    second = store.start_attempt(task.id, "provider-b")

    assert failed.status == MediaTaskStatus.FAILED
    assert failed.error_code == MediaErrorCode.PROVIDER_TIMEOUT
    assert failed.error_message == "timed out"
    assert failed.completed_at is not None
    assert requeued.status == MediaTaskStatus.QUEUED
    assert requeued.completed_at is None
    assert second.attempt_no == 2
    assert second.provider_account_id == "provider-b"
    assert store.get_task(task.id).attempt_count == 2
    assert [item.id for item in store.list_attempts(task.id)] == [first.id, second.id]


def test_fail_attempt_without_retry_terminates_task_and_preserves_diagnostics(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    attempt = store.start_attempt(create_task(store).id, "provider-a")

    failed = store.fail_attempt(
        attempt.id,
        MediaErrorCode.INPUT_INVALID,
        "unsupported format",
    )
    task = store.get_task(attempt.task_id)

    assert task.status == MediaTaskStatus.FAILED
    assert task.completed_at == failed.completed_at
    restarted = TaskStore(tmp_path / "tasks.db")
    assert restarted.get_attempt(attempt.id).error_code == MediaErrorCode.INPUT_INVALID
    with pytest.raises(InvalidTaskTransition):
        restarted.fail_attempt(
            attempt.id,
            MediaErrorCode.PROVIDER_REJECTED,
            "must not change",
        )


def test_quality_failure_can_explicitly_requeue_task(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    attempt = submitted_attempt(store)
    attempt = store.transition_attempt(attempt.id, MediaTaskStatus.RUNNING)
    attempt = store.transition_attempt(attempt.id, MediaTaskStatus.DOWNLOADING)
    attempt = store.transition_attempt(attempt.id, MediaTaskStatus.VALIDATING)

    failed = store.fail_attempt(
        attempt.id,
        MediaErrorCode.QUALITY_FAILED,
        "artifact rejected",
        retry_task=True,
        quality_failed=True,
    )

    assert failed.status == MediaTaskStatus.QUALITY_FAILED
    assert store.get_task(attempt.task_id).status == MediaTaskStatus.QUEUED
    assert store.start_attempt(attempt.task_id, "provider-b").attempt_no == 2


def test_quality_failure_without_retry_is_terminal_for_attempt_and_task(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    attempt = submitted_attempt(store)
    attempt = store.transition_attempt(attempt.id, MediaTaskStatus.RUNNING)
    attempt = store.transition_attempt(attempt.id, MediaTaskStatus.DOWNLOADING)
    attempt = store.transition_attempt(attempt.id, MediaTaskStatus.VALIDATING)

    failed = store.fail_attempt(
        attempt.id,
        MediaErrorCode.QUALITY_FAILED,
        "artifact rejected",
        quality_failed=True,
    )

    assert failed.status == MediaTaskStatus.QUALITY_FAILED
    assert store.get_task(attempt.task_id).status == MediaTaskStatus.QUALITY_FAILED
    with pytest.raises(InvalidTaskTransition):
        store.fail_attempt(
            attempt.id,
            MediaErrorCode.QUALITY_FAILED,
            "must not change",
        )


def test_records_forbid_extra_fields_and_require_json_values() -> None:
    with pytest.raises(ValidationError):
        MediaTaskRecord.model_validate({"unexpected": True})
    with pytest.raises(ValidationError):
        MediaAttemptRecord.model_validate({"unexpected": True})


def test_record_output_defaults_to_empty_object_and_identifiers_are_non_empty(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    task = create_task(store)
    attempt = store.start_attempt(task.id, "provider-a")
    task_data = task.model_dump()
    task_data.pop("output")

    assert MediaTaskRecord.model_validate(task_data).output == {}
    for record, field in ((task, "id"), (attempt, "provider_account_id")):
        invalid = record.model_dump()
        invalid[field] = "   "
        with pytest.raises(ValidationError):
            type(record).model_validate(invalid)


def test_create_task_is_idempotent_for_same_immutable_input(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    first = create_task(store)
    second = create_task(store)

    assert second == first
    assert second.status == MediaTaskStatus.QUEUED
    assert second.attempt_count == 0


@pytest.mark.parametrize(
    ("capability", "implementation", "input_snapshot"),
    [
        (MediaCapability.VIDEO_T2VA, {"implementation": "image-primary"}, {"prompt": "a lighthouse"}),
        (MediaCapability.IMAGE_SINGLE, {"implementation": "other"}, {"prompt": "a lighthouse"}),
        (MediaCapability.IMAGE_SINGLE, {"implementation": "image-primary"}, {"prompt": "different"}),
    ],
)
def test_create_task_rejects_idempotency_conflicts(
    tmp_path: Path,
    capability: MediaCapability,
    implementation: dict[str, object],
    input_snapshot: dict[str, object],
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    original = create_task(store)

    with pytest.raises(TaskStoreConflictError, match="idempotency key"):
        store.create_task(capability, "request-1", implementation, input_snapshot)

    assert store.get_task(original.id) == original


@pytest.mark.parametrize("key", ["", "   "])
def test_create_task_requires_non_empty_idempotency_key(tmp_path: Path, key: str) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    with pytest.raises(ValueError, match="idempotency_key"):
        create_task(store, key)


def test_snapshots_are_deep_copied_json_and_revalidated_on_read(tmp_path: Path) -> None:
    database = tmp_path / "tasks.db"
    store = TaskStore(database)
    implementation = {"implementation": "image-primary", "nested": {"version": 1}}
    input_snapshot = {"prompt": "original", "references": ["one.png"]}
    task = store.create_task(
        MediaCapability.IMAGE_SINGLE,
        "snapshot-1",
        implementation,
        input_snapshot,
    )
    implementation["nested"]["version"] = 99
    input_snapshot["references"].append("two.png")

    restarted = TaskStore(database)
    saved = restarted.get_task(task.id)
    assert saved is not None
    assert saved.implementation_snapshot == {
        "implementation": "image-primary",
        "nested": {"version": 1},
    }
    assert saved.input_snapshot == {"prompt": "original", "references": ["one.png"]}

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE media_tasks SET input_snapshot_json = ? WHERE id = ?",
            ('{"bad": NaN}', task.id),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises((ValidationError, ValueError)):
        restarted.get_task(task.id)


@pytest.mark.parametrize(
    "secret_key",
    [
        "auth",
        "Authorization",
        "credential_ref",
        "password",
        "api_token",
        "api-key",
        "apiKey",
        "apikey",
        "privateKey",
        "clientsecret",
    ],
)
def test_snapshots_reject_sensitive_key_variants_without_echoing_values(
    tmp_path: Path,
    secret_key: str,
) -> None:
    database = tmp_path / "tasks.db"
    store = TaskStore(database)
    secret_value = "known-plaintext-token"

    with pytest.raises(TaskStoreConflictError) as exc_info:
        store.create_task(
            MediaCapability.IMAGE_SINGLE,
            f"credential-{secret_key}",
            {"implementation": "image-primary", "nested": {secret_key: secret_value}},
            {"prompt": "safe"},
        )

    assert str(exc_info.value) == "task_store.prohibited_field"
    assert secret_key not in str(exc_info.value)
    assert secret_value not in str(exc_info.value)
    assert secret_value.encode() not in database.read_bytes()


def test_tokenizer_key_is_not_misclassified_as_sensitive(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    task = store.create_task(
        MediaCapability.IMAGE_SINGLE,
        "tokenizer-safe",
        {"implementation": "image-primary", "tokenizer": "example"},
        {"prompt": "safe"},
    )
    assert task.implementation_snapshot["tokenizer"] == "example"


def test_output_rejects_sensitive_fields_without_persisting_or_echoing(
    tmp_path: Path,
) -> None:
    database = tmp_path / "tasks.db"
    store = TaskStore(database)
    attempt = submitted_attempt(store)
    attempt = store.transition_attempt(attempt.id, MediaTaskStatus.RUNNING)
    attempt = store.transition_attempt(attempt.id, MediaTaskStatus.DOWNLOADING)
    attempt = store.transition_attempt(attempt.id, MediaTaskStatus.VALIDATING)

    with pytest.raises(TaskStoreConflictError) as exc_info:
        store.complete_success(attempt.id, {"apiKey": "known-output-token"})

    assert str(exc_info.value) == "task_store.prohibited_field"
    assert "apiKey" not in str(exc_info.value)
    assert "known-output-token" not in str(exc_info.value)
    assert b"known-output-token" not in database.read_bytes()
    assert store.get_attempt(attempt.id).status == MediaTaskStatus.VALIDATING


@pytest.mark.parametrize(
    "signed_url",
    [
        "https://cdn.invalid/input.png?token=known-url-secret",
        "https://cdn.invalid/input.png?signature=known-url-secret",
        "https://cdn.invalid/input.png?X-Amz-Signature=known-url-secret",
        "https://cdn.invalid/input.png?x-goog-signature=known-url-secret",
    ],
)
def test_json_boundaries_reject_signed_urls_without_echoing_secret(
    tmp_path: Path,
    signed_url: str,
) -> None:
    database = tmp_path / "tasks.db"
    store = TaskStore(database)
    with pytest.raises(TaskStoreConflictError) as exc_info:
        store.create_task(
            MediaCapability.IMAGE_SINGLE,
            "signed-input",
            {"implementation": "image-primary"},
            {"asset_url": signed_url},
        )

    assert str(exc_info.value) == "task_store.prohibited_value"
    assert "known-url-secret" not in str(exc_info.value)
    assert b"known-url-secret" not in database.read_bytes()


def test_attempt_snapshots_cost_output_and_errors_reject_signed_urls(
    tmp_path: Path,
) -> None:
    database = tmp_path / "tasks.db"
    store = TaskStore(database)
    task = create_task(store)
    signed_url = "https://cdn.invalid/a?X-Amz-Credential=known-attempt-secret"

    with pytest.raises(TaskStoreConflictError, match="^task_store.prohibited_value$"):
        store.start_attempt(
            task.id,
            "provider-a",
            effective_params={"source": signed_url},
        )
    attempt = store.start_attempt(task.id, "provider-a")
    with pytest.raises(TaskStoreConflictError, match="^task_store.prohibited_value$"):
        store.record_attempt_cost(attempt.id, {"receipt_url": signed_url})
    with pytest.raises(TaskStoreConflictError, match="^task_store.prohibited_value$"):
        store.fail_attempt(
            attempt.id,
            MediaErrorCode.PROVIDER_REJECTED,
            f"provider returned {signed_url}",
        )

    attempt = store.transition_attempt(attempt.id, MediaTaskStatus.UPLOADING)
    attempt = store.record_provider_task(attempt.id, "remote-signed-output")
    attempt = store.transition_attempt(attempt.id, MediaTaskStatus.RUNNING)
    attempt = store.transition_attempt(attempt.id, MediaTaskStatus.DOWNLOADING)
    attempt = store.transition_attempt(attempt.id, MediaTaskStatus.VALIDATING)
    with pytest.raises(TaskStoreConflictError, match="^task_store.prohibited_value$"):
        store.complete_success(attempt.id, {"artifact_url": signed_url})

    assert "known-attempt-secret".encode() not in database.read_bytes()
    assert store.get_attempt(attempt.id).status == MediaTaskStatus.VALIDATING


def test_diagnostics_reject_secrets_and_signed_urls_without_echoing(
    tmp_path: Path,
) -> None:
    database = tmp_path / "tasks.db"
    store = TaskStore(database)
    attempt = submitted_attempt(store)
    secret = "known-diagnostic-secret"

    for diagnostics in (
        {"token": secret},
        {"detail": f"api_key={secret}"},
        {"url": f"https://cdn.invalid/a?signature={secret}"},
    ):
        with pytest.raises(TaskStoreConflictError) as exc_info:
            store.record_provider_status(attempt.id, "processing", diagnostics)
        assert secret not in str(exc_info.value)

    assert secret.encode() not in database.read_bytes()


def test_media_error_code_whitelist_accepts_enum_and_value_and_rejects_unknown(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    first = store.start_attempt(create_task(store, "enum-code").id, "provider")
    failed = store.fail_attempt(
        first.id,
        MediaErrorCode.UPLOAD_FAILED,
        "upload failed",
    )
    assert failed.error_code == MediaErrorCode.UPLOAD_FAILED

    second = store.start_attempt(create_task(store, "string-code").id, "provider")
    failed = store.fail_attempt(second.id, "POLL_FAILED", "poll failed")
    assert failed.error_code == MediaErrorCode.POLL_FAILED

    third = store.start_attempt(create_task(store, "invalid-code").id, "provider")
    invalid = "UNEXPECTED_PROVIDER_CODE"
    with pytest.raises(ValueError) as exc_info:
        store.fail_attempt(third.id, invalid, "must reject")
    assert invalid not in str(exc_info.value)
    assert store.get_attempt(third.id).status == MediaTaskStatus.PREPARING


@pytest.mark.parametrize(
    "secret_text",
    [
        "api_key=known-text-secret",
        "token=known-text-secret",
        "Authorization: Bearer known-text-secret",
        "Authorization: Basic a25vd24tdGV4dC1zZWNyZXQ=",
        "Cookie: session=known-text-secret",
        "Set-Cookie: session=known-text-secret",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJrbm93biJ9.c2lnbmF0dXJl",
    ],
)
def test_sensitive_text_patterns_are_rejected_without_echoing(
    tmp_path: Path,
    secret_text: str,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    with pytest.raises(TaskStoreConflictError) as exc_info:
        store.create_task(
            MediaCapability.IMAGE_SINGLE,
            f"secret-text-{abs(hash(secret_text))}",
            {"implementation": "image-primary"},
            {"note": secret_text, "tokenizer": "must remain allowed"},
        )
    assert secret_text not in str(exc_info.value)


def test_error_code_and_message_apply_secret_text_boundary(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    attempt = store.start_attempt(create_task(store).id, "provider")
    secret = "api_key=known-error-secret"

    with pytest.raises(TaskStoreConflictError) as code_error:
        store.fail_attempt(attempt.id, secret, "safe message")
    assert secret not in str(code_error.value)
    with pytest.raises(TaskStoreConflictError) as message_error:
        store.fail_attempt(attempt.id, MediaErrorCode.INPUT_INVALID, secret)
    assert secret not in str(message_error.value)
    assert store.get_attempt(attempt.id).status == MediaTaskStatus.PREPARING


def test_two_stores_allow_only_one_active_attempt(tmp_path: Path) -> None:
    database = tmp_path / "tasks.db"
    first = TaskStore(database)
    task = create_task(first)
    second = TaskStore(database)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(store.start_attempt, task.id, "provider-account-1")
            for store in (first, second)
        ]
    results = []
    conflicts = []
    for future in futures:
        try:
            results.append(future.result())
        except TaskStoreConflictError as exc:
            conflicts.append(exc)

    assert len(results) == 1
    assert len(conflicts) == 1
    assert first.get_task(task.id).attempt_count == 1
    assert [item.attempt_no for item in first.list_attempts(task.id)] == [1]


def test_start_attempt_rolls_back_insert_and_counter_when_task_update_fails(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    task = create_task(store)
    connection = store._connect()
    try:
        connection.execute(
            """
            CREATE TRIGGER abort_task_update
            BEFORE UPDATE ON media_tasks
            BEGIN
                SELECT RAISE(ABORT, 'forced task update failure');
            END
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(sqlite3.IntegrityError, match="forced task update failure"):
        store.start_attempt(task.id, "provider-account-1")

    assert store.get_task(task.id).attempt_count == 0
    assert store.get_task(task.id).status == MediaTaskStatus.QUEUED
    assert store.list_attempts(task.id) == []


def test_record_provider_task_rolls_back_attempt_when_task_update_fails(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    attempt = store.start_attempt(create_task(store).id, "provider-account-1")
    connection = store._connect()
    try:
        connection.execute(
            """
            CREATE TRIGGER abort_task_update
            BEFORE UPDATE ON media_tasks
            BEGIN
                SELECT RAISE(ABORT, 'forced task update failure');
            END
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(sqlite3.IntegrityError, match="forced task update failure"):
        store.record_provider_task(attempt.id, "remote-task-1")

    unchanged = store.get_attempt(attempt.id)
    assert unchanged.provider_task_id is None
    assert unchanged.status == MediaTaskStatus.PREPARING


def test_provider_task_id_is_write_once_and_submission_is_idempotent(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    task = create_task(store)
    attempt = store.start_attempt(task.id, "provider-account-1")

    saved = store.record_provider_task(attempt.id, "remote-task-1")
    repeated = store.record_provider_task(attempt.id, "remote-task-1")
    assert repeated == saved
    assert saved.provider_task_id == "remote-task-1"
    assert saved.status == MediaTaskStatus.SUBMITTED
    assert store.get_task(task.id).status == MediaTaskStatus.SUBMITTED

    with pytest.raises(TaskStoreConflictError, match="provider_task_id"):
        store.record_provider_task(attempt.id, "remote-task-2")
    assert store.get_attempt(attempt.id) == saved


@pytest.mark.parametrize("provider_task_id", ["", "   "])
def test_provider_task_id_must_be_non_empty(tmp_path: Path, provider_task_id: str) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    attempt = store.start_attempt(create_task(store).id, "provider-account-1")
    with pytest.raises(ValueError, match="provider_task_id"):
        store.record_provider_task(attempt.id, provider_task_id)


def test_poll_states_require_provider_task_id(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    attempt = store.start_attempt(create_task(store).id, "provider-account-1")
    with pytest.raises(TaskStoreConflictError, match="provider_task_id"):
        store.transition_attempt(attempt.id, MediaTaskStatus.RUNNING)


def test_restart_recovers_same_task_and_does_not_create_an_attempt(tmp_path: Path) -> None:
    database = tmp_path / "tasks.db"
    first = TaskStore(database)
    attempt = submitted_attempt(first)

    second = TaskStore(database)
    recovered = second.recoverable(NOW)

    assert [item.id for item in recovered] == [attempt.task_id]
    assert second.get_task(attempt.task_id).id == attempt.task_id
    assert [item.id for item in second.list_attempts(attempt.task_id)] == [attempt.id]


def test_main_state_machine_and_terminal_state_are_enforced(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    attempt = store.start_attempt(create_task(store).id, "provider-account-1")
    attempt = store.transition_attempt(attempt.id, MediaTaskStatus.UPLOADING)
    attempt = store.record_provider_task(attempt.id, "remote-task-1")
    for status in (
        MediaTaskStatus.RUNNING,
        MediaTaskStatus.DOWNLOADING,
        MediaTaskStatus.VALIDATING,
    ):
        attempt = store.transition_attempt(attempt.id, status)
    attempt = store.complete_success(attempt.id, {"artifact": "final.png"})

    assert attempt.completed_at is not None
    task = store.get_task(attempt.task_id)
    assert task.status == MediaTaskStatus.SUCCEEDED
    assert task.completed_at == attempt.completed_at
    with pytest.raises(InvalidTaskTransition):
        store.transition_attempt(attempt.id, MediaTaskStatus.FAILED)


def test_invalid_transition_rolls_back_without_changing_rows(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    attempt = store.start_attempt(create_task(store).id, "provider-account-1")

    with pytest.raises(InvalidTaskTransition):
        store.transition_attempt(attempt.id, MediaTaskStatus.SUCCEEDED)

    assert store.get_attempt(attempt.id) == attempt
    assert store.get_task(attempt.task_id).status == MediaTaskStatus.PREPARING


def test_cancellation_failure_and_quality_failure_paths(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    cancelled = store.start_attempt(create_task(store, "cancel").id, "account")
    cancelled = store.transition_attempt(cancelled.id, MediaTaskStatus.CANCEL_REQUESTED)
    cancelled = store.transition_attempt(cancelled.id, MediaTaskStatus.CANCELLED)
    assert cancelled.completed_at is not None

    failed = store.start_attempt(create_task(store, "fail").id, "account")
    failed = store.fail_attempt(
        failed.id,
        MediaErrorCode.PROVIDER_REJECTED,
        "provider failed",
    )
    assert failed.completed_at is not None

    quality = store.start_attempt(create_task(store, "quality").id, "account")
    quality = store.record_provider_task(quality.id, "remote-quality")
    quality = store.transition_attempt(quality.id, MediaTaskStatus.RUNNING)
    quality = store.transition_attempt(quality.id, MediaTaskStatus.DOWNLOADING)
    quality = store.transition_attempt(quality.id, MediaTaskStatus.VALIDATING)
    quality = store.fail_attempt(
        quality.id,
        MediaErrorCode.QUALITY_FAILED,
        "quality validation failed",
        quality_failed=True,
    )
    assert quality.completed_at is not None


@pytest.mark.parametrize(
    "terminal",
    [
        MediaTaskStatus.SUCCEEDED,
        MediaTaskStatus.FAILED,
        MediaTaskStatus.QUALITY_FAILED,
    ],
)
def test_transition_attempt_rejects_states_owned_by_atomic_terminal_apis(
    tmp_path: Path,
    terminal: MediaTaskStatus,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    attempt = store.start_attempt(create_task(store).id, "provider")

    with pytest.raises(InvalidTaskTransition, match="dedicated"):
        store.transition_attempt(attempt.id, terminal)

    assert store.get_attempt(attempt.id) == attempt


def test_retry_wait_recovers_due_same_attempt_and_resumes_polling(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    attempt = submitted_attempt(store)
    due = NOW + timedelta(minutes=1)
    store.transition_attempt(
        attempt.id,
        MediaTaskStatus.RETRY_WAIT,
        next_run_at=due,
    )

    assert store.recoverable(NOW) == []
    assert [item.id for item in store.recoverable(due)] == [attempt.task_id]
    resumed = store.resume_retry(attempt.task_id, due)
    assert resumed.id == attempt.id
    assert resumed.provider_task_id == "remote-task-1"
    assert resumed.status == MediaTaskStatus.SUBMITTED
    assert len(store.list_attempts(attempt.task_id)) == 1


def test_recoverable_has_stable_schedule_then_creation_order(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    first = submitted_attempt(store)
    second_task = create_task(store, "request-2")
    second = store.record_provider_task(
        store.start_attempt(second_task.id, "provider-account-1").id,
        "remote-task-2",
    )
    third_task = create_task(store, "request-3")
    third = store.record_provider_task(
        store.start_attempt(third_task.id, "provider-account-1").id,
        "remote-task-3",
    )
    store.transition_attempt(
        first.id,
        MediaTaskStatus.RETRY_WAIT,
        next_run_at=NOW - timedelta(minutes=1),
    )
    store.transition_attempt(
        second.id,
        MediaTaskStatus.RETRY_WAIT,
        next_run_at=NOW - timedelta(minutes=2),
    )

    assert [item.id for item in store.recoverable(NOW)] == [
        second.task_id,
        first.task_id,
        third.task_id,
    ]


def test_heartbeat_and_next_poll_are_persisted_on_attempt_and_task(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    attempt = submitted_attempt(store)
    next_poll = NOW + timedelta(seconds=15)

    updated = store.heartbeat(attempt.id, now=NOW, next_poll_at=next_poll)

    assert updated.heartbeat_at == NOW
    assert updated.next_poll_at == next_poll
    task = store.get_task(attempt.task_id)
    assert task.heartbeat_at == NOW
    assert task.next_poll_at == next_poll


def test_missing_ids_raise_stable_error(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    with pytest.raises(TaskNotFoundError, match="missing"):
        store.start_attempt("missing", "account")
    with pytest.raises(TaskNotFoundError, match="missing"):
        store.record_provider_task("missing", "remote")


def test_attempt_foreign_key_rejects_orphan_rows(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    connection = store._connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO media_attempts (
                    id, task_id, attempt_no, provider_account_id, status,
                    started_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "orphan",
                    "missing",
                    1,
                    "account",
                    MediaTaskStatus.PREPARING.value,
                    NOW.isoformat(),
                    NOW.isoformat(),
                    NOW.isoformat(),
                ),
            )
    finally:
        connection.rollback()
        connection.close()


def test_database_enforces_unique_attempt_number_per_task(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    task = create_task(store)
    store.start_attempt(task.id, "provider-a")
    connection = store._connect()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            connection.execute(
                """
                INSERT INTO media_attempts (
                    id, task_id, attempt_no, provider_account_id, status,
                    started_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "duplicate-attempt",
                    task.id,
                    1,
                    "provider-b",
                    MediaTaskStatus.FAILED.value,
                    NOW.isoformat(),
                    NOW.isoformat(),
                    NOW.isoformat(),
                ),
            )
    finally:
        connection.rollback()
        connection.close()


def test_concurrent_create_same_input_returns_one_task_id(tmp_path: Path) -> None:
    database = tmp_path / "tasks.db"
    stores = (TaskStore(database), TaskStore(database))
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(create_task, store, "concurrent") for store in stores]
    tasks = [future.result() for future in futures]

    assert tasks[0].id == tasks[1].id
    connection = stores[0]._connect()
    try:
        assert connection.execute("SELECT COUNT(*) FROM media_tasks").fetchone()[0] == 1
    finally:
        connection.close()


def test_concurrent_create_different_input_has_one_stable_conflict(
    tmp_path: Path,
) -> None:
    database = tmp_path / "tasks.db"
    stores = (TaskStore(database), TaskStore(database))

    def create(store: TaskStore, prompt: str) -> MediaTaskRecord:
        return store.create_task(
            MediaCapability.IMAGE_SINGLE,
            "concurrent-conflict",
            {"implementation": "image-primary"},
            {"prompt": prompt},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(create, stores[0], "first"),
            executor.submit(create, stores[1], "second"),
        ]
    successes = []
    conflicts = []
    for future in futures:
        try:
            successes.append(future.result())
        except TaskStoreConflictError as exc:
            conflicts.append(str(exc))

    assert len(successes) == 1
    assert conflicts == [
        "idempotency key 'concurrent-conflict' has different immutable input"
    ]


def test_recoverable_breaks_equal_timestamps_by_task_id(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    attempts = []
    for key in ("tie-a", "tie-b", "tie-c"):
        task = create_task(store, key)
        attempts.append(
            store.record_provider_task(
                store.start_attempt(task.id, "provider-a").id,
                f"remote-{key}",
            )
        )
    connection = store._connect()
    try:
        connection.execute(
            "UPDATE media_tasks SET created_at = ?",
            (NOW.isoformat(),),
        )
        connection.commit()
    finally:
        connection.close()

    expected = sorted(attempt.task_id for attempt in attempts)
    assert [task.id for task in store.recoverable(NOW)] == expected


def test_reads_revalidate_corrupted_output_json(tmp_path: Path) -> None:
    database = tmp_path / "tasks.db"
    store = TaskStore(database)
    task = create_task(store)
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE media_tasks SET output_json = ? WHERE id = ?",
            ('{"bad": NaN}', task.id),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises((ValidationError, ValueError)):
        store.get_task(task.id)


@pytest.mark.parametrize(
    ("method", "value"),
    [
        ("start_task", ""),
        ("start_provider", "   "),
        ("record_attempt", ""),
        ("record_remote", "   "),
        ("fail_attempt", ""),
        ("fail_code", "   "),
        ("fail_message", ""),
    ],
)
def test_method_identifiers_and_diagnostics_must_be_non_empty(
    tmp_path: Path,
    method: str,
    value: str,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    task = create_task(store)
    attempt = store.start_attempt(task.id, "provider-a")
    with pytest.raises(ValueError):
        if method == "start_task":
            store.start_attempt(value, "provider-a")
        elif method == "start_provider":
            store.start_attempt(task.id, value)
        elif method == "record_attempt":
            store.record_provider_task(value, "remote-a")
        elif method == "record_remote":
            store.record_provider_task(attempt.id, value)
        elif method == "fail_attempt":
            store.fail_attempt(value, MediaErrorCode.INPUT_INVALID, "message")
        elif method == "fail_code":
            store.fail_attempt(attempt.id, value, "message")
        else:
            store.fail_attempt(attempt.id, MediaErrorCode.INPUT_INVALID, value)


@pytest.mark.parametrize(
    ("error_code", "error_message"),
    [("c" * 129, "safe"), (MediaErrorCode.INPUT_INVALID, "m" * 2049)],
)
def test_diagnostics_are_bounded_without_echoing_rejected_values(
    tmp_path: Path,
    error_code: str,
    error_message: str,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    attempt = store.start_attempt(create_task(store).id, "provider-a")

    with pytest.raises(ValueError) as exc_info:
        store.fail_attempt(attempt.id, error_code, error_message)

    assert error_code not in str(exc_info.value)
    assert error_message not in str(exc_info.value)
    assert store.get_attempt(attempt.id).status == MediaTaskStatus.PREPARING
