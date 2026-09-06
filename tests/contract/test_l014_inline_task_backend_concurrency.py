import asyncio
import os
import signal
import sqlite3
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from novelvideo.ports import registry
from novelvideo.ports.local.tasks import (
    InlineTaskBackend,
    InMemoryCancellationStore,
    SQLiteCancellationStore,
)
from novelvideo.project_context import ProjectContext
from novelvideo.generators import tts_generator, video_composer, video_generator
from novelvideo.generators.tts_generator import EdgeTTSGenerator, MockTTSGenerator
from novelvideo.generators.video_composer import SceneAsset, VideoComposer
from novelvideo.generators.video_generator import MockVideoGenerator
from novelvideo.task_backend.cancel import (
    TaskCancelled,
    TaskLeaseLost,
    TaskTimedOut,
    raise_if_envelope_cancel_requested,
)
from novelvideo.task_backend.limits import (
    global_lane_concurrency,
    project_lane_active_limit,
    project_lane_effective_active_limit,
    project_lane_min_active_limit,
    project_user_lane_active_limit,
)
from novelvideo.task_backend.lease_store import SQLiteLaneLeaseStore
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_backend.subprocesses import (
    active_subprocess_count,
    kill_task_processes,
    run_project_subprocess,
)
from novelvideo.task_state import TaskStateManager


pytestmark = pytest.mark.m07


def test_video_lane_defaults_allow_five_tasks_for_one_user_in_one_project(monkeypatch):
    for env_name in (
        "ST_PROJECT_MAX_ACTIVE_VIDEO_TASKS",
        "ST_PROJECT_MIN_ACTIVE_VIDEO_TASKS",
        "ST_PROJECT_USER_MAX_ACTIVE_VIDEO_TASKS",
        "ST_CE_GLOBAL_MAX_ACTIVE_VIDEO_TASKS",
    ):
        monkeypatch.delenv(env_name, raising=False)

    assert project_lane_active_limit("video") == 5
    assert project_lane_min_active_limit("video") == 5
    assert project_user_lane_active_limit("video") == 5
    assert project_lane_effective_active_limit("video", eligible_user_count=1) == 5
    assert global_lane_concurrency("video") == 5


def test_video_lane_positive_environment_overrides_remain_supported(monkeypatch):
    monkeypatch.setenv("ST_PROJECT_MAX_ACTIVE_VIDEO_TASKS", "7")
    monkeypatch.setenv("ST_PROJECT_MIN_ACTIVE_VIDEO_TASKS", "6")
    monkeypatch.setenv("ST_PROJECT_USER_MAX_ACTIVE_VIDEO_TASKS", "4")
    monkeypatch.setenv("ST_CE_GLOBAL_MAX_ACTIVE_VIDEO_TASKS", "3")

    assert project_lane_active_limit("video") == 7
    assert project_lane_min_active_limit("video") == 6
    assert project_user_lane_active_limit("video") == 4
    assert project_lane_effective_active_limit("video", eligible_user_count=1) == 6
    assert global_lane_concurrency("video") == 3


def test_saved_ce_concurrency_unifies_default_lane_until_restart(
    monkeypatch, tmp_path
):
    from novelvideo import config
    from novelvideo.task_concurrency_settings import (
        reset_process_task_concurrency_for_tests,
        save_task_concurrency_settings,
    )

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    for name in (
        "ST_PROJECT_MAX_ACTIVE_DEFAULT_TASKS",
        "ST_PROJECT_MIN_ACTIVE_DEFAULT_TASKS",
        "ST_PROJECT_USER_MAX_ACTIVE_DEFAULT_TASKS",
        "ST_CE_GLOBAL_MAX_ACTIVE_DEFAULT_TASKS",
    ):
        monkeypatch.delenv(name, raising=False)
    reset_process_task_concurrency_for_tests()
    save_task_concurrency_settings(
        {"default": 7, "video": 5, "world": 1, "ffmpeg": 1}
    )

    assert project_lane_active_limit("default") == 7
    assert project_lane_min_active_limit("default") == 7
    assert project_user_lane_active_limit("default") == 7
    assert project_lane_effective_active_limit("default", eligible_user_count=1) == 7
    assert global_lane_concurrency("default") == 7

    save_task_concurrency_settings(
        {"default": 9, "video": 5, "world": 1, "ffmpeg": 1}
    )
    assert project_lane_active_limit("default") == 7
    assert project_user_lane_active_limit("default") == 7
    assert global_lane_concurrency("default") == 7


def test_saved_ce_concurrency_keeps_environment_override_precedence(
    monkeypatch, tmp_path
):
    from novelvideo import config
    from novelvideo.task_concurrency_settings import (
        reset_process_task_concurrency_for_tests,
        save_task_concurrency_settings,
    )

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    reset_process_task_concurrency_for_tests()
    save_task_concurrency_settings(
        {"default": 7, "video": 5, "world": 1, "ffmpeg": 1}
    )
    monkeypatch.setenv("ST_PROJECT_MAX_ACTIVE_DEFAULT_TASKS", "6")
    monkeypatch.setenv("ST_PROJECT_MIN_ACTIVE_DEFAULT_TASKS", "5")
    monkeypatch.setenv("ST_PROJECT_USER_MAX_ACTIVE_DEFAULT_TASKS", "4")
    monkeypatch.setenv("ST_CE_GLOBAL_MAX_ACTIVE_DEFAULT_TASKS", "3")

    assert project_lane_active_limit("default") == 6
    assert project_lane_min_active_limit("default") == 5
    assert project_user_lane_active_limit("default") == 4
    assert project_lane_effective_active_limit("default", eligible_user_count=1) == 5
    assert global_lane_concurrency("default") == 3


def test_inline_backend_reports_frozen_lane_runtime_status(monkeypatch, tmp_path):
    from novelvideo import config
    from novelvideo.task_concurrency_settings import (
        reset_process_task_concurrency_for_tests,
        save_task_concurrency_settings,
    )

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    for lane in ("DEFAULT", "VIDEO", "WORLD", "FFMPEG"):
        monkeypatch.delenv(f"ST_CE_GLOBAL_MAX_ACTIVE_{lane}_TASKS", raising=False)
    reset_process_task_concurrency_for_tests()
    save_task_concurrency_settings(
        {"default": 6, "video": 5, "world": 1, "ffmpeg": 1}
    )

    backend = InlineTaskBackend()

    assert backend.lane_runtime_status()["default"] == {
        "active": 0,
        "queued": 0,
        "executor_limit": 6,
        "queue_limit": 512,
    }


def _ctx(tmp_path: Path, project_id: str = "proj_l014", requester: str = "editor_1") -> ProjectContext:
    return ProjectContext(
        project_id=project_id,
        project_name=project_id,
        owner_type="user",
        owner_id="owner_1",
        owner_username="alice",
        requester_user_id=requester,
        requester_username=requester,
        requester_principals=(("user", requester),),
        effective_role="editor",
        home_node_id="node_l014",
        output_dir=tmp_path / "output" / project_id,
        state_dir=tmp_path / "state" / project_id,
        runtime_dir=tmp_path / "runtime" / project_id,
        is_home_node=True,
    )


@pytest.fixture(autouse=True)
def _task_ports(monkeypatch, tmp_path):
    from novelvideo import config
    from novelvideo.task_concurrency_settings import (
        reset_process_task_concurrency_for_tests,
    )

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    reset_process_task_concurrency_for_tests()
    manager = TaskStateManager()
    monkeypatch.setenv("ST_CE_TASK_RUNTIME_DB", str(tmp_path / "runtime" / "tasks.db"))
    monkeypatch.setattr(registry, "_PORTS", dict(registry._PORTS))
    registry.register_port("cancellation_store", InMemoryCancellationStore())
    monkeypatch.setattr("novelvideo.task_state._task_manager", manager)
    monkeypatch.setattr("novelvideo.ports.local.tasks.get_task_manager", lambda: manager)
    yield manager
    reset_process_task_concurrency_for_tests()


def test_contract_fixture_isolates_ce_task_concurrency_settings(tmp_path):
    from novelvideo import config
    from novelvideo.shared.runtime_env import is_ce_effective

    assert Path(config.STATE_DIR) == tmp_path / "state"
    assert is_ce_effective() is True


def test_runtime_lane_store_admission_is_transactional_across_instances(tmp_path):
    database_path = tmp_path / "runtime" / "tasks.db"
    first = SQLiteLaneLeaseStore(database_path)
    second = SQLiteLaneLeaseStore(database_path)

    assert first.admit(
        owner_id="worker-a",
        task_id="task-a",
        lane="world",
        active_limit=1,
        queue_limit=1,
        lease_seconds=60,
    ) == "active"
    assert second.admit(
        owner_id="worker-b",
        task_id="task-b",
        lane="world",
        active_limit=1,
        queue_limit=1,
        lease_seconds=60,
    ) == "queued"

    with pytest.raises(Exception) as exc_info:
        second.admit(
            owner_id="worker-b",
            task_id="task-c",
            lane="world",
            active_limit=1,
            queue_limit=1,
            lease_seconds=60,
        )
    assert exc_info.value.__class__.__name__ == "GlobalLaneQueueLimitExceeded"

    assert first.release(owner_id="worker-a", task_id="task-a")
    assert second.promote(
        owner_id="worker-b",
        task_id="task-b",
        lane="world",
        active_limit=1,
        lease_seconds=60,
    )
    assert second.release(owner_id="worker-b", task_id="task-b")


def test_runtime_lane_store_reclaims_expired_leases_without_promoting_old_tasks(tmp_path):
    database_path = tmp_path / "runtime" / "tasks.db"
    store = SQLiteLaneLeaseStore(database_path)
    assert store.admit(
        owner_id="dead-worker",
        task_id="old-task",
        lane="world",
        active_limit=1,
        queue_limit=1,
        lease_seconds=0.01,
    ) == "active"
    time.sleep(0.03)

    assert store.admit(
        owner_id="new-worker",
        task_id="new-task",
        lane="world",
        active_limit=1,
        queue_limit=1,
        lease_seconds=60,
    ) == "active"
    assert not store.promote(
        owner_id="dead-worker",
        task_id="old-task",
        lane="world",
        active_limit=1,
        lease_seconds=60,
    )


def test_runtime_lane_lease_expiry_uses_sqlite_clock_after_write_lock(tmp_path):
    database_path = tmp_path / "runtime" / "tasks.db"
    store = SQLiteLaneLeaseStore(database_path)
    store.counts("world")
    started = threading.Event()
    result: list[str] = []

    def admit() -> None:
        started.set()
        result.append(
            store.admit(
                owner_id="worker-a",
                task_id="task-a",
                lane="world",
                active_limit=1,
                queue_limit=1,
                lease_seconds=1.0,
            )
        )

    lock_conn = sqlite3.connect(database_path, timeout=10)
    lock_conn.execute("BEGIN IMMEDIATE")
    thread = threading.Thread(target=admit)
    thread.start()
    assert started.wait(timeout=1)
    time.sleep(1.2)
    lock_conn.commit()
    lock_conn.close()
    thread.join(timeout=2)

    assert result == ["active"]
    assert store.heartbeat(owner_id="worker-a", task_id="task-a", lease_seconds=60)


@pytest.mark.asyncio
async def test_two_inline_backends_share_global_lane_active_and_queue_limits(
    monkeypatch,
    _task_ports,
    tmp_path,
):
    monkeypatch.setenv("ST_CE_GLOBAL_MAX_ACTIVE_WORLD_TASKS", "1")
    monkeypatch.setenv("ST_CE_GLOBAL_MAX_QUEUED_WORLD_TASKS", "1")
    monkeypatch.setenv("ST_PROJECT_MAX_ACTIVE_WORLD_TASKS", "5")
    monkeypatch.setenv("ST_PROJECT_USER_MAX_ACTIVE_WORLD_TASKS", "5")
    first_backend = InlineTaskBackend()
    second_backend = InlineTaskBackend()
    first_ctx = _ctx(tmp_path, "shared_lane_a")
    second_ctx = _ctx(tmp_path, "shared_lane_b")
    first_release = threading.Event()
    second_finished = threading.Event()
    lock = threading.Lock()
    active = 0
    maximum_active = 0

    def runner(envelope, run_ctx):
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            if envelope["project_id"] == first_ctx.project_id:
                first_release.wait(timeout=3)
            else:
                second_finished.set()
            return {"ok": True}
        finally:
            with lock:
                active -= 1

    register_project_task_runner("shared_lane_first", runner)
    register_project_task_runner("shared_lane_second", runner)

    await first_backend.enqueue_project_task(
        first_ctx, task_type="shared_lane_first", episode=1, queue_kind="world"
    )
    await _wait_for_status(_task_ports, first_ctx, "shared_lane_first", "running")
    await second_backend.enqueue_project_task(
        second_ctx, task_type="shared_lane_second", episode=1, queue_kind="world"
    )

    await asyncio.sleep(0.1)
    assert not second_finished.is_set()
    assert second_backend.lane_snapshot()["world"]["queued"] == 1
    first_release.set()
    assert await asyncio.to_thread(second_finished.wait, 3)
    await _wait_for_status(_task_ports, second_ctx, "shared_lane_second", "completed")
    assert maximum_active == 1


@pytest.mark.asyncio
async def test_remote_inline_backend_cancel_is_observed_by_owner_worker_via_project_sqlite(
    _task_ports,
    tmp_path,
):
    ctx = _ctx(tmp_path, "shared_cancel")
    project_db = ctx.state_dir / "data.db"
    owner_store = SQLiteCancellationStore(project_db)
    registry.register_port("cancellation_store", owner_store)
    owner_backend = InlineTaskBackend()
    remote_backend = InlineTaskBackend()
    started = threading.Event()
    observed = threading.Event()

    def runner(envelope, run_ctx):
        started.set()
        try:
            while True:
                raise_if_envelope_cancel_requested(envelope)
                time.sleep(0.02)
        except TaskCancelled:
            observed.set()
            raise

    register_project_task_runner("shared_cancel_runner", runner)
    queued = await owner_backend.enqueue_project_task(
        ctx, task_type="shared_cancel_runner", episode=1, queue_kind="world"
    )
    assert await asyncio.to_thread(started.wait, 3)
    running = await _wait_for_status(_task_ports, ctx, "shared_cancel_runner", "running")
    assert running.execution_owner_id

    # Simulate the cancelling request being handled by another CE instance.
    registry.register_port("cancellation_store", SQLiteCancellationStore(project_db))
    await remote_backend.cancel_project_task(ctx, queued.task_state)

    assert await asyncio.to_thread(observed.wait, 3)
    cancelled = await _wait_for_status(
        _task_ports, ctx, "shared_cancel_runner", "cancelled"
    )
    assert cancelled.execution_owner_id == running.execution_owner_id
    assert cancelled.cancel_requested_at


@pytest.mark.asyncio
async def test_inline_worker_stops_locally_when_project_task_lease_is_lost(
    monkeypatch,
    _task_ports,
    tmp_path,
):
    monkeypatch.setenv("ST_CE_TASK_LEASE_SECONDS", "0.3")
    monkeypatch.setenv("ST_CE_TASK_HEARTBEAT_SECONDS", "0.03")
    ctx = _ctx(tmp_path, "lease_loss")
    backend = InlineTaskBackend()
    started = threading.Event()
    stopped = threading.Event()

    def runner(envelope, run_ctx):
        started.set()
        try:
            while True:
                raise_if_envelope_cancel_requested(envelope)
                time.sleep(0.01)
        except TaskLeaseLost:
            stopped.set()
            raise

    register_project_task_runner("lease_loss_runner", runner)
    queued = await backend.enqueue_project_task(
        ctx, task_type="lease_loss_runner", episode=1, queue_kind="world"
    )
    assert await asyncio.to_thread(started.wait, 3)
    running = await _wait_for_status(_task_ports, ctx, "lease_loss_runner", "running")
    original_owner = running.execution_owner_id
    assert original_owner

    with _task_ports._connect_context(ctx) as conn:
        conn.execute(
            "UPDATE task_states SET execution_owner_id = ? WHERE task_id = ?",
            ("replacement-worker", queued.task_state.task_id),
        )

    assert await asyncio.to_thread(stopped.wait, 3)
    await _wait_lane_idle(backend, "world")
    state = _task_ports.get_task_for_project(ctx, "lease_loss_runner", 1)
    assert state is not None
    assert state.execution_owner_id == "replacement-worker"
    assert state.status == "running"


@pytest.mark.asyncio
async def test_queued_lane_lease_loss_fails_owned_project_task_without_replay(
    monkeypatch,
    _task_ports,
    tmp_path,
):
    monkeypatch.setenv("ST_CE_GLOBAL_MAX_ACTIVE_WORLD_TASKS", "1")
    monkeypatch.setenv("ST_PROJECT_MAX_ACTIVE_WORLD_TASKS", "5")
    monkeypatch.setenv("ST_PROJECT_USER_MAX_ACTIVE_WORLD_TASKS", "5")
    backend = InlineTaskBackend()
    ctx = _ctx(tmp_path, "queued_lease_loss")
    release = threading.Event()

    def runner(envelope, run_ctx):
        release.wait(timeout=3)
        return {"ok": True}

    register_project_task_runner("queued_lease_active", runner)
    register_project_task_runner("queued_lease_lost", runner)
    await backend.enqueue_project_task(
        ctx, task_type="queued_lease_active", episode=1, queue_kind="world"
    )
    await _wait_for_status(_task_ports, ctx, "queued_lease_active", "running")
    queued = await backend.enqueue_project_task(
        ctx, task_type="queued_lease_lost", episode=1, queue_kind="world"
    )
    queued_state = _task_ports.get_task_for_project(ctx, "queued_lease_lost", 1)
    assert queued_state is not None
    assert queued_state.execution_owner_id == backend._execution_owner_id

    assert backend._lease_store.release(
        owner_id=backend._execution_owner_id,
        task_id=queued.task_state.task_id,
    )

    failed = await _wait_for_status(
        _task_ports, ctx, "queued_lease_lost", "failed", timeout=3
    )
    assert failed.error == "TASK_LEASE_EXPIRED"
    assert backend.lane_snapshot()["world"]["queued"] == 0
    release.set()


@pytest.mark.asyncio
async def test_remote_cancel_of_queued_task_is_terminal_and_frees_queue_capacity(
    monkeypatch,
    _task_ports,
    tmp_path,
):
    monkeypatch.setenv("ST_CE_GLOBAL_MAX_ACTIVE_WORLD_TASKS", "1")
    monkeypatch.setenv("ST_CE_GLOBAL_MAX_QUEUED_WORLD_TASKS", "1")
    monkeypatch.setenv("ST_PROJECT_MAX_ACTIVE_WORLD_TASKS", "5")
    monkeypatch.setenv("ST_PROJECT_USER_MAX_ACTIVE_WORLD_TASKS", "5")
    monkeypatch.setenv("ST_CE_TASK_HEARTBEAT_SECONDS", "0.02")
    ctx = _ctx(tmp_path, "queued_remote_cancel")
    project_db = ctx.state_dir / "data.db"
    registry.register_port("cancellation_store", SQLiteCancellationStore(project_db))
    owner_backend = InlineTaskBackend()
    remote_backend = InlineTaskBackend()
    release = threading.Event()

    def blocking_runner(envelope, run_ctx):
        release.wait(timeout=3)
        return {"ok": True}

    register_project_task_runner("queued_cancel_active", blocking_runner)
    register_project_task_runner("queued_cancel_target", blocking_runner)
    register_project_task_runner("queued_cancel_replacement", blocking_runner)
    await owner_backend.enqueue_project_task(
        ctx, task_type="queued_cancel_active", episode=1, queue_kind="world"
    )
    await _wait_for_status(_task_ports, ctx, "queued_cancel_active", "running")
    queued = await owner_backend.enqueue_project_task(
        ctx, task_type="queued_cancel_target", episode=1, queue_kind="world"
    )

    registry.register_port("cancellation_store", SQLiteCancellationStore(project_db))
    await remote_backend.cancel_project_task(ctx, queued.task_state)

    cancelled = await _wait_for_status(
        _task_ports, ctx, "queued_cancel_target", "cancelled"
    )
    assert cancelled.execution_owner_id == owner_backend._execution_owner_id
    await owner_backend.enqueue_project_task(
        ctx, task_type="queued_cancel_replacement", episode=1, queue_kind="world"
    )
    replacement_state = _task_ports.get_task_for_project(
        ctx, "queued_cancel_replacement", 1
    )
    assert replacement_state is not None
    assert replacement_state.status == "queued"
    assert owner_backend._lease_store.counts("world")["queued"] == 1
    release.set()
    await _wait_for_status(_task_ports, ctx, "queued_cancel_active", "completed")
    await _wait_for_status(_task_ports, ctx, "queued_cancel_replacement", "completed")
    await _wait_lane_idle(owner_backend, "world")


@pytest.mark.asyncio
async def test_queued_poller_retries_transient_coordination_error_within_lease(
    monkeypatch,
    _task_ports,
    tmp_path,
):
    monkeypatch.setenv("ST_CE_GLOBAL_MAX_ACTIVE_WORLD_TASKS", "1")
    monkeypatch.setenv("ST_PROJECT_MAX_ACTIVE_WORLD_TASKS", "5")
    monkeypatch.setenv("ST_PROJECT_USER_MAX_ACTIVE_WORLD_TASKS", "5")
    monkeypatch.setenv("ST_CE_TASK_LEASE_SECONDS", "0.5")
    monkeypatch.setenv("ST_CE_TASK_HEARTBEAT_SECONDS", "0.02")
    backend = InlineTaskBackend()
    ctx = _ctx(tmp_path, "queued_retry")
    release = threading.Event()
    finished = threading.Event()

    def active_runner(envelope, run_ctx):
        release.wait(timeout=3)
        return {"ok": True}

    def queued_runner(envelope, run_ctx):
        finished.set()
        return {"ok": True}

    register_project_task_runner("queued_retry_active", active_runner)
    register_project_task_runner("queued_retry_target", queued_runner)
    await backend.enqueue_project_task(
        ctx, task_type="queued_retry_active", episode=1, queue_kind="world"
    )
    await _wait_for_status(_task_ports, ctx, "queued_retry_active", "running")
    queued = await backend.enqueue_project_task(
        ctx, task_type="queued_retry_target", episode=1, queue_kind="world"
    )
    original_heartbeat = backend._lease_store.heartbeat
    injected = False

    def flaky_heartbeat(**kwargs):
        nonlocal injected
        if kwargs["task_id"] == queued.task_state.task_id and not injected:
            injected = True
            raise sqlite3.OperationalError("database is temporarily busy")
        return original_heartbeat(**kwargs)

    monkeypatch.setattr(backend._lease_store, "heartbeat", flaky_heartbeat)
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and not injected:
        await asyncio.sleep(0.01)
    assert injected
    assert backend.lane_snapshot()["world"]["queued"] == 1
    release.set()
    finished_in_time = await asyncio.to_thread(finished.wait, 10)
    queued_state = _task_ports.get_task_for_project(ctx, "queued_retry_target", 1)
    assert finished_in_time, (
        backend.lane_snapshot()["world"],
        backend._lease_store.counts("world"),
        queued_state.status if queued_state is not None else None,
        queued_state.error if queued_state is not None else None,
    )
    await _wait_for_status(_task_ports, ctx, "queued_retry_target", "completed")
    await _wait_lane_idle(backend, "world")


@pytest.mark.asyncio
async def test_active_heartbeat_coordination_error_requests_conservative_local_stop(
    monkeypatch,
    _task_ports,
    tmp_path,
):
    monkeypatch.setenv("ST_CE_TASK_LEASE_SECONDS", "0.3")
    monkeypatch.setenv("ST_CE_TASK_HEARTBEAT_SECONDS", "0.02")
    backend = InlineTaskBackend()
    ctx = _ctx(tmp_path, "active_coordination_error")
    stopped = threading.Event()
    original_heartbeat = backend._lease_store.heartbeat
    calls = 0

    def flaky_heartbeat(**kwargs):
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise sqlite3.OperationalError("database is temporarily busy")
        return original_heartbeat(**kwargs)

    monkeypatch.setattr(backend._lease_store, "heartbeat", flaky_heartbeat)

    def runner(envelope, run_ctx):
        try:
            while True:
                raise_if_envelope_cancel_requested(envelope)
                time.sleep(0.01)
        except TaskLeaseLost:
            stopped.set()
            raise

    register_project_task_runner("active_coordination_error_runner", runner)
    await backend.enqueue_project_task(
        ctx,
        task_type="active_coordination_error_runner",
        episode=1,
        queue_kind="world",
    )

    assert await asyncio.to_thread(stopped.wait, 3)
    await _wait_lane_idle(backend, "world")


@pytest.mark.asyncio
async def test_inline_dispatch_aborts_when_lane_lease_is_lost(
    monkeypatch,
    _task_ports,
    tmp_path,
):
    backend = InlineTaskBackend()
    ctx = _ctx(tmp_path, "dispatch_lane_lost")
    captured = []
    monkeypatch.setattr(backend, "_start_lane_job", lambda lane, job: captured.append((lane, job)))
    called = False

    def runner(envelope, run_ctx):
        nonlocal called
        called = True
        return {"ok": True}

    register_project_task_runner("dispatch_lane_lost_runner", runner)
    queued = await backend.enqueue_project_task(
        ctx, task_type="dispatch_lane_lost_runner", episode=1, queue_kind="world"
    )
    lane, job = captured[0]
    assert backend._lease_store.release(
        owner_id=backend._execution_owner_id,
        task_id=queued.task_state.task_id,
    )

    await backend._run_inline(lane, job)

    assert called is False
    failed = _task_ports.get_task_for_project(ctx, "dispatch_lane_lost_runner", 1)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error == "TASK_LEASE_LOST"


async def _wait_for_status(manager, ctx, task_type: str, status: str, *, episode: int = 1, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    observed = None
    while time.monotonic() < deadline:
        observed = manager.get_task_for_project(ctx, task_type, episode)
        if observed is not None and observed.status == status:
            return observed
        await asyncio.sleep(0.02)
    assert observed is not None
    assert observed.status == status
    return observed


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        # os.kill(pid, 0) is not a liveness probe on Windows (WinError 87).
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _wait_until_dead(pid: int, *, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        await asyncio.sleep(0.02)
    return not _pid_alive(pid)


async def _wait_lane_idle(backend: InlineTaskBackend, lane: str, *, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if backend.lane_snapshot()[lane]["active"] == 0:
            return
        await asyncio.sleep(0.02)
    assert backend.lane_snapshot()[lane]["active"] == 0


def _spawn_tree_script(tmp_path: Path) -> tuple[Path, Path]:
    pidfile = tmp_path / "process-tree.pid"
    script = tmp_path / "spawn_tree.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import pathlib
            import subprocess
            import sys
            import time

            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            pathlib.Path({str(pidfile)!r}).write_text(str(child.pid), encoding="utf-8")
            child.wait()
            time.sleep(30)
            """
        ),
        encoding="utf-8",
    )
    return script, pidfile


@pytest.mark.asyncio
async def test_gate1_cooperative_cancel_releases_lane_without_outer_task_cancel(_task_ports, tmp_path):
    ctx = _ctx(tmp_path)
    backend = InlineTaskBackend()
    started = threading.Event()
    observed_cancel = threading.Event()
    finished = threading.Event()
    task_type = "l014_gate1_cooperative_cancel"

    def runner(envelope, run_ctx):
        started.set()
        try:
            while True:
                raise_if_envelope_cancel_requested(envelope)
                time.sleep(0.02)
        except TaskCancelled:
            observed_cancel.set()
            raise
        finally:
            finished.set()

    register_project_task_runner(task_type, runner)

    queued = await backend.enqueue_project_task(ctx, task_type=task_type, episode=1, queue_kind="world")
    assert await asyncio.to_thread(started.wait, 3) is True

    await backend.cancel_project_task(ctx, queued.task_state)

    assert await asyncio.to_thread(observed_cancel.wait, 3) is True
    await _wait_for_status(_task_ports, ctx, task_type, "cancelled")
    assert await asyncio.to_thread(finished.wait, 3) is True
    await _wait_lane_idle(backend, "world")


@pytest.mark.asyncio
async def test_gate2_cancel_kills_registered_process_group_and_unregisters_handle(_task_ports, tmp_path):
    ctx = _ctx(tmp_path)
    backend = InlineTaskBackend()
    script, pidfile = _spawn_tree_script(tmp_path)
    started = threading.Event()
    task_type = "l014_gate2_cancel_kills_process_group"

    def runner(envelope, run_ctx):
        started.set()
        run_project_subprocess(
            [sys.executable, str(script)],
            envelope=envelope,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {"ok": True}

    register_project_task_runner(task_type, runner)

    queued = await backend.enqueue_project_task(ctx, task_type=task_type, episode=1, queue_kind="ffmpeg")
    assert await asyncio.to_thread(started.wait, 3) is True
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not pidfile.exists():
        await asyncio.sleep(0.02)
    assert pidfile.exists()
    child_pid = int(pidfile.read_text(encoding="utf-8"))
    assert active_subprocess_count(queued.task_state.task_id) == 1

    await backend.cancel_project_task(ctx, queued.task_state)

    await _wait_for_status(_task_ports, ctx, task_type, "cancelled")
    assert await _wait_until_dead(child_pid)
    # Unregistration happens asynchronously in the runner thread after proc.communicate(),
    # so poll for the handle to drain rather than asserting a single instant.
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and active_subprocess_count(queued.task_state.task_id) != 0:
        await asyncio.sleep(0.02)
    assert active_subprocess_count(queued.task_state.task_id) == 0
    await _wait_lane_idle(backend, "ffmpeg")


@pytest.mark.asyncio
async def test_gate2_deadline_kills_process_group_and_marks_failed(monkeypatch, _task_ports, tmp_path):
    monkeypatch.setenv("ST_PROJECT_TASK_TIMEOUT_S", "1")
    ctx = _ctx(tmp_path)
    backend = InlineTaskBackend()
    script, pidfile = _spawn_tree_script(tmp_path)
    task_type = "l014_gate2_deadline_kills_process_group"

    def runner(envelope, run_ctx):
        run_project_subprocess(
            [sys.executable, str(script)],
            envelope=envelope,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return {"ok": True}

    register_project_task_runner(task_type, runner)

    queued = await backend.enqueue_project_task(ctx, task_type=task_type, episode=1, queue_kind="world")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not pidfile.exists():
        await asyncio.sleep(0.02)
    assert pidfile.exists()
    child_pid = int(pidfile.read_text(encoding="utf-8"))

    failed = await _wait_for_status(_task_ports, ctx, task_type, "failed", timeout=4)

    assert failed.metadata is not None
    assert failed.metadata["error_code"] == "TASK_TIMEOUT"
    assert await _wait_until_dead(child_pid)
    assert active_subprocess_count(queued.task_state.task_id) == 0
    await _wait_lane_idle(backend, "world")


@pytest.mark.asyncio
async def test_gate2_external_kill_reclassifies_running_subprocess_as_cancelled(tmp_path):
    script = tmp_path / "sleep.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    task_id = "l014_external_kill_cancel"
    result: dict[str, object] = {}

    def run_subprocess():
        try:
            run_project_subprocess(
                [sys.executable, str(script)],
                envelope={
                    "project_id": "proj_l014",
                    "task_type": "external_kill",
                    "episode": 1,
                    "__run_task_id": task_id,
                },
                capture_output=True,
                text=True,
                timeout=30,
                poll_seconds=5,
            )
        except BaseException as exc:  # noqa: BLE001 - test records control-flow exception type
            result["exc"] = exc

    thread = threading.Thread(target=run_subprocess)
    thread.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and active_subprocess_count(task_id) == 0:
        await asyncio.sleep(0.02)
    assert active_subprocess_count(task_id) == 1

    assert kill_task_processes(task_id) == 1

    thread.join(timeout=3)
    assert not thread.is_alive()
    assert isinstance(result.get("exc"), TaskCancelled)
    assert active_subprocess_count(task_id) == 0


@pytest.mark.asyncio
async def test_gate2_control_signals_are_not_swallowed_by_generator_fallbacks(monkeypatch, tmp_path):
    def raise_cancelled(*args, **kwargs):
        raise TaskCancelled()

    monkeypatch.setattr(tts_generator, "run_project_subprocess", raise_cancelled)
    monkeypatch.setattr(video_generator, "_run_video_subprocess", raise_cancelled)
    monkeypatch.setattr(video_composer, "_run_video_subprocess", raise_cancelled)

    with pytest.raises(TaskCancelled):
        await EdgeTTSGenerator()._get_audio_duration(str(tmp_path / "voice.mp3"))

    with pytest.raises(TaskCancelled):
        await MockTTSGenerator().generate("hello", str(tmp_path / "mock.mp3"))

    with pytest.raises(TaskCancelled):
        await MockVideoGenerator().generate(
            image_path=str(tmp_path / "frame.png"),
            prompt="move",
            output_path=str(tmp_path / "mock.mp4"),
        )

    scene = SceneAsset(
        scene_number=1,
        image_path=str(tmp_path / "frame.png"),
        audio_path=str(tmp_path / "voice.mp3"),
        duration_seconds=1.0,
    )
    with pytest.raises(TaskCancelled):
        await VideoComposer()._create_scene_video(scene, str(tmp_path / "scene.mp4"))


@pytest.mark.asyncio
async def test_gate2_timeout_signals_are_not_swallowed_by_generator_fallbacks(monkeypatch, tmp_path):
    def raise_timeout(*args, **kwargs):
        raise TaskTimedOut(timeout_seconds=1)

    monkeypatch.setattr(tts_generator, "run_project_subprocess", raise_timeout)
    monkeypatch.setattr(video_generator, "_run_video_subprocess", raise_timeout)
    monkeypatch.setattr(video_composer, "_run_video_subprocess", raise_timeout)

    with pytest.raises(TaskTimedOut):
        await EdgeTTSGenerator()._get_audio_duration(str(tmp_path / "voice.mp3"))

    with pytest.raises(TaskTimedOut):
        await MockTTSGenerator().generate("hello", str(tmp_path / "mock.mp3"))

    with pytest.raises(TaskTimedOut):
        await MockVideoGenerator().generate(
            image_path=str(tmp_path / "frame.png"),
            prompt="move",
            output_path=str(tmp_path / "mock.mp4"),
        )

    scene = SceneAsset(
        scene_number=1,
        image_path=str(tmp_path / "frame.png"),
        audio_path=str(tmp_path / "voice.mp3"),
        duration_seconds=1.0,
    )
    with pytest.raises(TaskTimedOut):
        await VideoComposer()._create_scene_video(scene, str(tmp_path / "scene.mp4"))


@pytest.mark.asyncio
async def test_gate3_world_lane_saturation_does_not_starve_default_lane(
    monkeypatch,
    _task_ports,
    tmp_path,
):
    monkeypatch.setenv("ST_CE_GLOBAL_MAX_ACTIVE_WORLD_TASKS", "1")
    monkeypatch.setenv("ST_CE_GLOBAL_MAX_ACTIVE_DEFAULT_TASKS", "1")
    ctx = _ctx(tmp_path)
    backend = InlineTaskBackend()
    world_release = threading.Event()
    default_done = threading.Event()

    def world_runner(envelope, run_ctx):
        world_release.wait(timeout=3)
        return {"ok": True}

    def default_runner(envelope, run_ctx):
        default_done.set()
        return {"ok": True}

    register_project_task_runner("l014_gate3_world_blocker", world_runner)
    register_project_task_runner("l014_gate3_default_fast", default_runner)

    await backend.enqueue_project_task(ctx, task_type="l014_gate3_world_blocker", episode=1, queue_kind="world")
    await _wait_for_status(_task_ports, ctx, "l014_gate3_world_blocker", "running")
    await backend.enqueue_project_task(ctx, task_type="l014_gate3_default_fast", episode=1, queue_kind="default")

    assert await asyncio.to_thread(default_done.wait, 1) is True
    await _wait_for_status(_task_ports, ctx, "l014_gate3_default_fast", "completed")
    assert backend.lane_snapshot()["world"]["active"] == 1
    world_release.set()


@pytest.mark.asyncio
async def test_gate3_same_lane_overflow_is_explicitly_queued_and_cancelable(
    monkeypatch,
    _task_ports,
    tmp_path,
):
    monkeypatch.setenv("ST_CE_GLOBAL_MAX_ACTIVE_WORLD_TASKS", "1")
    monkeypatch.setenv("ST_PROJECT_MAX_ACTIVE_WORLD_TASKS", "5")
    monkeypatch.setenv("ST_PROJECT_USER_MAX_ACTIVE_WORLD_TASKS", "5")
    ctx = _ctx(tmp_path)
    backend = InlineTaskBackend()
    release = threading.Event()
    started = []

    def runner(envelope, run_ctx):
        started.append(envelope["task_type"])
        release.wait(timeout=3)
        return {"ok": True}

    register_project_task_runner("l014_gate3_world_running", runner)
    register_project_task_runner("l014_gate3_world_queued", runner)

    await backend.enqueue_project_task(ctx, task_type="l014_gate3_world_running", episode=1, queue_kind="world")
    queued = await backend.enqueue_project_task(ctx, task_type="l014_gate3_world_queued", episode=1, queue_kind="world")

    await _wait_for_status(_task_ports, ctx, "l014_gate3_world_running", "running")
    pending = _task_ports.get_task_for_project(ctx, "l014_gate3_world_queued", 1)
    assert pending is not None
    assert pending.status == "queued"
    assert backend.lane_snapshot()["world"] == {"active": 1, "queued": 1, "concurrency": 1}

    await backend.cancel_project_task(ctx, queued.task_state)

    await _wait_for_status(_task_ports, ctx, "l014_gate3_world_queued", "cancelled")
    assert backend.lane_snapshot()["world"]["queued"] == 0
    assert "l014_gate3_world_queued" not in started
    release.set()


@pytest.mark.asyncio
async def test_gate3_global_lane_queue_overflow_raises_typed_limit_exception(
    monkeypatch,
    _task_ports,
    tmp_path,
):
    monkeypatch.setenv("ST_CE_GLOBAL_MAX_ACTIVE_WORLD_TASKS", "1")
    monkeypatch.setenv("ST_CE_GLOBAL_MAX_QUEUED_WORLD_TASKS", "1")
    monkeypatch.setenv("ST_PROJECT_MAX_ACTIVE_WORLD_TASKS", "5")
    monkeypatch.setenv("ST_PROJECT_USER_MAX_ACTIVE_WORLD_TASKS", "5")
    ctx = _ctx(tmp_path)
    backend = InlineTaskBackend()
    release = threading.Event()

    def runner(envelope, run_ctx):
        release.wait(timeout=3)
        return {"ok": True}

    register_project_task_runner("l014_gate3_world_running_overflow", runner)
    register_project_task_runner("l014_gate3_world_queued_overflow", runner)
    register_project_task_runner("l014_gate3_world_rejected_overflow", runner)
    from novelvideo.task_backend.limits import GlobalLaneQueueLimitExceeded

    await backend.enqueue_project_task(
        ctx,
        task_type="l014_gate3_world_running_overflow",
        episode=1,
        queue_kind="world",
    )
    await _wait_for_status(_task_ports, ctx, "l014_gate3_world_running_overflow", "running")
    await backend.enqueue_project_task(
        ctx,
        task_type="l014_gate3_world_queued_overflow",
        episode=1,
        queue_kind="world",
    )

    with pytest.raises(GlobalLaneQueueLimitExceeded) as exc_info:
        await backend.enqueue_project_task(
            ctx,
            task_type="l014_gate3_world_rejected_overflow",
            episode=1,
            queue_kind="world",
        )

    exc = exc_info.value
    assert exc.project_id == ctx.project_id
    assert exc.queue_kind == "world"
    assert exc.limit == 1
    assert exc.queued == 1
    rejected = _task_ports.get_task_for_project(ctx, "l014_gate3_world_rejected_overflow", 1)
    assert rejected is not None
    assert rejected.status == "failed"
    release.set()


def test_gate3_global_lane_queue_limit_exception_maps_to_http_429():
    from fastapi.testclient import TestClient

    from novelvideo.api.app import create_app
    from novelvideo.task_backend.limits import GlobalLaneQueueLimitExceeded

    app = create_app()

    @app.get("/_test/global-lane-limit")
    async def _raise_global_lane_limit():
        raise GlobalLaneQueueLimitExceeded(
            project_id="proj_l014",
            queue_kind="world",
            limit=1,
            queued=1,
        )

    response = TestClient(app).get("/_test/global-lane-limit")

    assert response.status_code == 429
    body = response.json()
    assert body["ok"] is False
    assert body["data"] == {
        "project_id": "proj_l014",
        "queue_kind": "world",
        "limit": 1,
        "queued": 1,
        "limit_scope": "global_lane_queue",
    }


@pytest.mark.asyncio
async def test_gate3_lane_scheduler_uses_independent_global_concurrency_config(monkeypatch):
    monkeypatch.setenv("ST_PROJECT_MAX_ACTIVE_WORLD_TASKS", "5")
    monkeypatch.setenv("ST_CE_GLOBAL_MAX_ACTIVE_WORLD_TASKS", "1")

    assert global_lane_concurrency("world") == 1


@pytest.mark.asyncio
async def test_gate3_multi_project_lane_dispatch_is_project_fair_fifo(
    monkeypatch,
    _task_ports,
    tmp_path,
):
    monkeypatch.setenv("ST_CE_GLOBAL_MAX_ACTIVE_WORLD_TASKS", "1")
    monkeypatch.setenv("ST_PROJECT_MAX_ACTIVE_WORLD_TASKS", "5")
    monkeypatch.setenv("ST_PROJECT_USER_MAX_ACTIVE_WORLD_TASKS", "5")
    backend = InlineTaskBackend()
    ctx_a = _ctx(tmp_path, "proj_l014_a", requester="editor_a")
    ctx_b = _ctx(tmp_path, "proj_l014_b", requester="editor_b")
    release_first = threading.Event()
    run_order: list[str] = []

    def runner(envelope, run_ctx):
        run_order.append(envelope["project_id"])
        if envelope["project_id"] == ctx_a.project_id and len(run_order) == 1:
            release_first.wait(timeout=3)
        return {"ok": True}

    register_project_task_runner("l014_gate3_a1", runner)
    register_project_task_runner("l014_gate3_a2", runner)
    register_project_task_runner("l014_gate3_b1", runner)

    await backend.enqueue_project_task(ctx_a, task_type="l014_gate3_a1", episode=1, queue_kind="world")
    await _wait_for_status(_task_ports, ctx_a, "l014_gate3_a1", "running")
    await backend.enqueue_project_task(ctx_a, task_type="l014_gate3_a2", episode=1, queue_kind="world")
    await backend.enqueue_project_task(ctx_b, task_type="l014_gate3_b1", episode=1, queue_kind="world")

    assert backend.lane_snapshot()["world"]["queued"] == 2
    release_first.set()

    await _wait_for_status(
        _task_ports, ctx_b, "l014_gate3_b1", "completed", timeout=10.0
    )
    await _wait_for_status(
        _task_ports, ctx_a, "l014_gate3_a2", "completed", timeout=10.0
    )
    assert run_order[:3] == [ctx_a.project_id, ctx_b.project_id, ctx_a.project_id]
