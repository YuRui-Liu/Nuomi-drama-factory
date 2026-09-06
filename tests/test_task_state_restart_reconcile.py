"""服务重启后基于 lease 的 inline 任务僵尸回收。"""

from pathlib import Path
import threading
import time

import pytest

from novelvideo.project_context import ProjectContext
from novelvideo.task_state import TaskStateManager

pytestmark = pytest.mark.m07

_ANCIENT = "2000-01-01T00:00:00.000000Z"


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        project_id="proj_reconcile",
        project_name="demo",
        owner_type="user",
        owner_id="owner",
        owner_username="alice",
        requester_user_id="editor",
        requester_username="bob",
        requester_principals=(("user", "editor"),),
        effective_role="editor",
        home_node_id="node_a",
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "runtime",
        is_home_node=True,
    )


def _backdate(manager: TaskStateManager, ctx: ProjectContext, task_id: str) -> None:
    with manager._connect_context(ctx) as conn:
        conn.execute(
            "UPDATE task_states SET updated_at = ?, created_at = ? WHERE task_id = ?",
            (_ANCIENT, _ANCIENT, task_id),
        )


def _set_lease(
    manager: TaskStateManager,
    ctx: ProjectContext,
    task_id: str,
    expires_at: str,
) -> None:
    with manager._connect_context(ctx) as conn:
        conn.execute(
            "UPDATE task_states SET execution_owner_id = ?, lease_expires_at = ?, "
            "heartbeat_at = ? WHERE task_id = ?",
            ("worker-a", expires_at, expires_at, task_id),
        )


def _restarted() -> TaskStateManager:
    """清扫按库记忆化在 manager 实例上;新实例 = 模拟重启后的进程。"""
    return TaskStateManager()


def test_expired_inline_running_task_is_failed_on_read(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    created = manager.create_task_for_project(
        ctx, "ingest_fast", 0, scope="job_1", metadata={"backend": "inline"}
    )
    manager.update_progress_for_project(ctx, "ingest_fast", 0, progress=0.1, scope="job_1")
    _backdate(manager, ctx, created.task_id)
    _set_lease(manager, ctx, created.task_id, _ANCIENT)
    manager = _restarted()

    listed = manager.list_tasks_for_project(ctx)

    assert len(listed) == 1
    assert listed[0].status == "failed"
    assert listed[0].error == "TASK_LEASE_EXPIRED"
    assert listed[0].result is not None
    assert listed[0].result["error_code"] == "TASK_LEASE_EXPIRED"
    assert listed[0].metadata is not None
    assert listed[0].metadata["error_code"] == "TASK_LEASE_EXPIRED"

    fetched = manager.get_task_for_project(ctx, "ingest_fast", 0, scope="job_1")
    assert fetched is not None
    assert fetched.status == "failed"


def test_expired_celery_running_task_is_untouched_by_startup_sweep(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    created = manager.create_task_for_project(
        ctx, "ingest_fast", 0, scope="job_celery", metadata={"backend": "celery"}
    )
    manager.update_progress_for_project(ctx, "ingest_fast", 0, progress=0.1, scope="job_celery")
    _backdate(manager, ctx, created.task_id)
    _set_lease(manager, ctx, created.task_id, _ANCIENT)
    manager = _restarted()

    listed = manager.list_tasks_for_project(ctx)

    assert len(listed) == 1
    assert listed[0].status == "running"


def test_fresh_inline_running_task_is_untouched(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    manager.create_task_for_project(
        ctx, "ingest_fast", 0, scope="job_fresh", metadata={"backend": "inline"}
    )
    manager.update_progress_for_project(ctx, "ingest_fast", 0, progress=0.1, scope="job_fresh")

    listed = manager.list_tasks_for_project(ctx)

    assert len(listed) == 1
    assert listed[0].status == "running"


def test_ancient_unleased_inline_running_task_is_untouched(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    created = manager.create_task_for_project(
        ctx, "ingest_fast", 0, scope="job_unleased", metadata={"backend": "inline"}
    )
    manager.update_progress_for_project(
        ctx, "ingest_fast", 0, progress=0.1, scope="job_unleased"
    )
    _backdate(manager, ctx, created.task_id)

    listed = _restarted().list_tasks_for_project(ctx)

    assert len(listed) == 1
    assert listed[0].status == "running"


def test_expired_inline_task_unblocks_reservation(tmp_path: Path) -> None:
    """准入闸(reserve)也必须看不到僵尸,否则重启后重试提交仍被去重守卫拒绝。"""
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    created = manager.create_task_for_project(
        ctx, "ingest_fast", 0, scope="job_r", metadata={"backend": "inline"}
    )
    manager.update_progress_for_project(ctx, "ingest_fast", 0, progress=0.1, scope="job_r")
    _backdate(manager, ctx, created.task_id)
    _set_lease(manager, ctx, created.task_id, _ANCIENT)
    manager = _restarted()

    state, reserved = manager.reserve_task_for_project(
        ctx, "ingest_fast", 0, scope="job_r", metadata={"backend": "inline"}
    )

    assert reserved is True
    assert state.task_id != created.task_id


def test_sweep_runs_once_per_db_by_design(tmp_path: Path) -> None:
    """清扫按库记忆化:进程启动后新出现的'过期'行不再被扫(启动前遗留才是僵尸)。"""
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    first = manager.create_task_for_project(
        ctx, "ingest_fast", 0, scope="job_a", metadata={"backend": "inline"}
    )
    _backdate(manager, ctx, first.task_id)
    _set_lease(manager, ctx, first.task_id, _ANCIENT)
    manager = _restarted()
    assert manager.list_tasks_for_project(ctx)[0].status == "failed"

    second = manager.create_task_for_project(
        ctx, "ingest_fast", 0, scope="job_b", metadata={"backend": "inline"}
    )
    manager.update_progress_for_project(ctx, "ingest_fast", 0, progress=0.1, scope="job_b")
    _backdate(manager, ctx, second.task_id)
    _set_lease(manager, ctx, second.task_id, _ANCIENT)

    statuses = {t.scope: t.status for t in manager.list_tasks_for_project(ctx)}
    assert statuses["job_b"] == "running"


def test_expired_inline_task_no_longer_blocks_active_count(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    created = manager.create_task_for_project(
        ctx, "ingest_fast", 0, scope="job_2", metadata={"backend": "inline"}
    )
    manager.update_progress_for_project(ctx, "ingest_fast", 0, progress=0.1, scope="job_2")
    _backdate(manager, ctx, created.task_id)
    _set_lease(manager, ctx, created.task_id, _ANCIENT)
    manager = _restarted()

    assert manager.count_active_tasks_for_project(ctx) == 0


def test_task_lease_claim_heartbeat_and_expiry(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    created = manager.create_task_for_project(ctx, "render", 1, scope="lease")

    assert manager.claim_task_lease(ctx, created.task_id, "worker-a", lease_seconds=60)
    assert not manager.claim_task_lease(ctx, created.task_id, "worker-b", lease_seconds=60)
    assert not manager.heartbeat_task_lease(
        ctx, created.task_id, "worker-b", lease_seconds=60
    )
    assert manager.heartbeat_task_lease(
        ctx, created.task_id, "worker-a", lease_seconds=60
    )

    with manager._connect_context(ctx) as conn:
        conn.execute(
            "UPDATE task_states SET lease_expires_at = ? WHERE task_id = ?",
            (_ANCIENT, created.task_id),
        )
    assert manager.expire_task_leases(ctx) == 1
    state = manager.get_task_for_project(ctx, "render", 1, scope="lease")
    assert state is not None
    assert state.status == "failed"
    assert state.error == "TASK_LEASE_EXPIRED"
    assert state.metadata is not None
    assert state.metadata["error_code"] == "TASK_LEASE_EXPIRED"
    assert not manager.claim_task_lease(ctx, created.task_id, "worker-b", lease_seconds=60)


def test_task_lease_heartbeat_cannot_revive_an_expired_lease(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    created = manager.create_task_for_project(ctx, "render", 1, scope="expired-heartbeat")
    assert manager.claim_task_lease(ctx, created.task_id, "worker-a", lease_seconds=60)
    _set_lease(manager, ctx, created.task_id, _ANCIENT)

    assert not manager.heartbeat_task_lease(
        ctx, created.task_id, "worker-a", lease_seconds=60
    )
    with manager._connect_context(ctx) as conn:
        expires_at = conn.execute(
            "SELECT lease_expires_at FROM task_states WHERE task_id = ?",
            (created.task_id,),
        ).fetchone()[0]
    assert expires_at == _ANCIENT


def test_task_lease_claim_by_same_owner_cannot_revive_an_expired_lease(
    tmp_path: Path,
) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    created = manager.create_task_for_project(ctx, "render", 1, scope="expired-claim")
    assert manager.claim_task_lease(ctx, created.task_id, "worker-a", lease_seconds=60)
    _set_lease(manager, ctx, created.task_id, _ANCIENT)

    assert not manager.claim_task_lease(
        ctx, created.task_id, "worker-a", lease_seconds=60
    )
    with manager._connect_context(ctx) as conn:
        expires_at = conn.execute(
            "SELECT lease_expires_at FROM task_states WHERE task_id = ?",
            (created.task_id,),
        ).fetchone()[0]
    assert expires_at == _ANCIENT


def test_task_lease_expiry_is_computed_after_waiting_for_sqlite_write_lock(
    tmp_path: Path,
) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    created = manager.create_task_for_project(ctx, "render", 1, scope="lock-clock")
    started = threading.Event()
    result: list[bool] = []

    def claim() -> None:
        started.set()
        result.append(
            manager.claim_task_lease(
                ctx, created.task_id, "worker-a", lease_seconds=1.0
            )
        )

    with manager._connect_context(ctx) as conn:
        conn.execute("BEGIN IMMEDIATE")
        thread = threading.Thread(target=claim)
        thread.start()
        assert started.wait(timeout=1)
        time.sleep(1.2)
    thread.join(timeout=2)

    assert result == [True]
    assert manager.heartbeat_task_lease(
        ctx, created.task_id, "worker-a", lease_seconds=60
    )


@pytest.mark.parametrize("operation", ["update", "complete", "fail"])
def test_owner_scoped_task_write_is_atomic_against_owner_replacement(
    monkeypatch,
    tmp_path: Path,
    operation: str,
) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    created = manager.create_task_for_project(ctx, "render", 1, scope=operation)
    assert manager.claim_task_lease(ctx, created.task_id, "worker-a", lease_seconds=60)
    original_get = manager.get_task_for_project
    raced = False

    def get_then_replace_owner(*args, **kwargs):
        nonlocal raced
        state = original_get(*args, **kwargs)
        if not raced:
            raced = True
            with manager._connect_context(ctx) as conn:
                conn.execute(
                    "UPDATE task_states SET execution_owner_id = ? WHERE task_id = ?",
                    ("worker-b", created.task_id),
                )
        return state

    monkeypatch.setattr(manager, "get_task_for_project", get_then_replace_owner)
    common = {
        "expected_task_id": created.task_id,
        "expected_execution_owner_id": "worker-a",
    }
    if operation == "update":
        written = manager.update_progress_for_project(
            ctx, "render", 1, scope=operation, progress=0.5, **common
        )
    elif operation == "complete":
        written = manager.complete_task_for_project(
            ctx, "render", 1, scope=operation, result={"ok": True}, **common
        )
    else:
        written = manager.fail_task_for_project(
            ctx, "render", 1, scope=operation, error="boom", **common
        )

    assert written is False
    fresh = TaskStateManager().get_task_for_project(ctx, "render", 1, scope=operation)
    assert fresh is not None
    assert fresh.execution_owner_id == "worker-b"
    assert fresh.status == "queued"


def test_expire_task_leases_counts_inline_expiry_after_restart(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    created = manager.create_task_for_project(
        ctx, "render", 1, scope="inline_expiry", metadata={"backend": "inline"}
    )
    assert manager.claim_task_lease(ctx, created.task_id, "worker-a", lease_seconds=60)
    _set_lease(manager, ctx, created.task_id, _ANCIENT)

    restarted = _restarted()

    assert restarted.expire_task_leases(ctx) == 1
    state = restarted.get_task_for_project(ctx, "render", 1, scope="inline_expiry")
    assert state is not None
    assert state.status == "failed"


def test_task_state_schema_migrates_lease_and_cancel_columns(tmp_path: Path) -> None:
    manager = TaskStateManager()
    ctx = _ctx(tmp_path)
    manager.create_task_for_project(ctx, "render", 1)

    with manager._connect_context(ctx) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(task_states)")}

    assert {
        "execution_owner_id",
        "lease_expires_at",
        "heartbeat_at",
        "cancel_requested_at",
    } <= columns
