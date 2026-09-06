"""Local CE task and cancellation port implementations."""

from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import contextlib
from dataclasses import dataclass, field
from functools import partial
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any
import uuid

from novelvideo.ports import get_cancellation_store
from novelvideo.ports.tasks import QueuedTask, cancel_key, display_metadata_for_task
from novelvideo.project_context import require_project_home_node
from novelvideo.sqlite_pragmas import configure_sqlite_connection
from novelvideo.task_backend.limits import (
    GlobalLaneQueueLimitExceeded,
    global_lane_concurrency,
    global_lane_queue_limit,
    project_lane_effective_active_limit,
)
from novelvideo.task_backend.cancel import clear_local_task_stop, request_local_task_stop
from novelvideo.task_backend.lease_store import SQLiteLaneLeaseStore
from novelvideo.task_backend.queues import QUEUE_KINDS, normalize_queue_kind
from novelvideo.task_backend.registry import get_project_task_runner_registration
from novelvideo.task_backend.run_core import (
    _ensure_builtin_runners_registered,
    run_project_task_core_sync,
)
from novelvideo.task_backend.subprocesses import kill_task_processes
from novelvideo.task_state import (
    ACTIVE_PROJECT_TASK_STATUSES,
    get_project_task_db_path_for_context,
    get_task_manager,
)
from novelvideo.text_task_runtime.settings import resolve_configured_agent_task_route

logger = logging.getLogger(__name__)


@dataclass
class _InlineLaneJob:
    envelope: dict[str, Any]
    ctx: Any
    manager: Any
    run_task_id: str
    metadata: dict[str, Any]
    lease_deadline_monotonic: float = 0.0

    @property
    def project_id(self) -> str:
        return str(self.envelope.get("project_id") or "")


@dataclass
class _InlineLane:
    name: str
    concurrency: int
    queue_limit: int
    executor: ThreadPoolExecutor
    queued: deque[_InlineLaneJob] = field(default_factory=deque)
    active: int = 0
    last_started_project_id: str | None = None


class InlineTaskBackend:
    def __init__(
        self,
        *,
        lease_store: SQLiteLaneLeaseStore | None = None,
        execution_owner_id: str | None = None,
    ) -> None:
        self._background_tasks: set[asyncio.Task] = set()
        self._lane_pollers: dict[str, asyncio.Task] = {}
        self._lease_store = lease_store or SQLiteLaneLeaseStore()
        self._execution_owner_id = execution_owner_id or (
            f"inline:{os.getpid()}:{uuid.uuid4().hex}"
        )
        self._lease_seconds = self._positive_float_env("ST_CE_TASK_LEASE_SECONDS", 30.0)
        self._heartbeat_seconds = self._positive_float_env(
            "ST_CE_TASK_HEARTBEAT_SECONDS",
            min(5.0, self._lease_seconds / 3.0),
        )
        self._lanes: dict[str, _InlineLane] = {}
        for lane in sorted(QUEUE_KINDS):
            concurrency = global_lane_concurrency(lane)
            self._lanes[lane] = _InlineLane(
                name=lane,
                concurrency=concurrency,
                queue_limit=global_lane_queue_limit(lane),
                executor=ThreadPoolExecutor(
                    max_workers=concurrency,
                    thread_name_prefix=f"inline-{lane}",
                ),
            )

    @staticmethod
    def _positive_float_env(name: str, default: float) -> float:
        try:
            value = float(os.environ.get(name, ""))
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    @staticmethod
    def _bind_cancellation_store(ctx) -> Any:
        store = get_cancellation_store()
        bind = getattr(store, "bind_project", None)
        if callable(bind):
            bind(ctx.project_id, get_project_task_db_path_for_context(ctx))
        return store

    async def enqueue_project_task(
        self,
        ctx,
        *,
        task_type: str,
        queue_kind: str = "default",
        episode: int = 0,
        beat_num: int | None = None,
        scope: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> QueuedTask:
        require_project_home_node(ctx, operation="enqueue project task")
        self._bind_cancellation_store(ctx)
        manager = get_task_manager()
        payload = payload or {}
        lane_name = normalize_queue_kind(queue_kind)
        _ensure_builtin_runners_registered()
        registration = get_project_task_runner_registration(task_type)
        agent_route_snapshot: dict[str, Any] | None = None
        if registration is not None and registration.text_task_role is not None:
            snapshot = resolve_configured_agent_task_route(
                ctx=ctx,
                task_role=registration.text_task_role,
                task_override=payload.get("agent_route_override"),
            )
            agent_route_snapshot = snapshot.model_dump(mode="json")
        metadata = {
            "backend": "inline",
            "queue_kind": lane_name,
            "project_id": ctx.project_id,
            **display_metadata_for_task(task_type, payload),
        }
        if agent_route_snapshot is not None:
            metadata["agent_route_snapshot"] = agent_route_snapshot
        project_lane_limit = project_lane_effective_active_limit(
            lane_name,
            eligible_user_count=1,
        )
        state, reserved = manager.reserve_task_for_project(
            ctx,
            task_type,
            episode,
            beat_num=beat_num,
            scope=scope,
            metadata=metadata,
            queue_kind=lane_name,
            project_lane_limit=project_lane_limit,
        )
        if not reserved and state.status in ACTIVE_PROJECT_TASK_STATUSES:
            existing_metadata = state.metadata or {}
            return QueuedTask(
                task_state=state,
                backend=str(existing_metadata.get("backend") or "inline"),
                queue=None,
                celery_id=None,
            )

        manager.update_progress_for_project(
            ctx,
            task_type,
            episode,
            beat_num=beat_num,
            scope=scope,
            progress=0.0,
            current_task="任务已进入队列",
            metadata=metadata,
            status="queued",
            expected_task_id=state.task_id,
        )
        if not manager.claim_task_lease(
            ctx,
            state.task_id,
            self._execution_owner_id,
            lease_seconds=self._lease_seconds,
        ):
            raise RuntimeError(f"failed to claim queued inline task lease: {state.task_id}")
        envelope = {
            "project_id": ctx.project_id,
            "requester_user_id": ctx.requester_user_id,
            "task_type": task_type,
            "episode": episode,
            "beat_num": beat_num,
            "scope": scope,
            "queue_kind": lane_name,
            "payload": payload,
        }
        if agent_route_snapshot is not None:
            envelope["agent_route_snapshot"] = agent_route_snapshot
        self._submit_lane_job(
            _InlineLaneJob(
                envelope=envelope,
                ctx=ctx,
                manager=manager,
                run_task_id=state.task_id,
                metadata=metadata,
            )
        )
        return QueuedTask(task_state=state, backend="inline")

    def lane_snapshot(self) -> dict[str, dict[str, int]]:
        return {
            name: {
                "active": lane.active,
                "queued": len(lane.queued),
                "concurrency": lane.concurrency,
            }
            for name, lane in sorted(self._lanes.items())
        }

    def lane_runtime_status(self) -> dict[str, dict[str, int]]:
        return {
            name: {
                "active": lane.active,
                "queued": len(lane.queued),
                "executor_limit": lane.concurrency,
                "queue_limit": lane.queue_limit,
            }
            for name, lane in sorted(self._lanes.items())
        }

    def _submit_lane_job(self, job: _InlineLaneJob) -> None:
        lane = self._lanes[normalize_queue_kind(job.envelope.get("queue_kind"))]
        try:
            lease_state = self._lease_store.admit(
                owner_id=self._execution_owner_id,
                task_id=job.run_task_id,
                project_id=job.project_id,
                lane=lane.name,
                active_limit=lane.concurrency,
                queue_limit=lane.queue_limit,
                lease_seconds=self._lease_seconds,
            )
        except GlobalLaneQueueLimitExceeded:
            job.manager.fail_task_for_project(
                job.ctx,
                str(job.envelope["task_type"]),
                int(job.envelope.get("episode") or 0),
                beat_num=job.envelope.get("beat_num"),
                scope=job.envelope.get("scope"),
                error=f"{lane.name} lane queue is full",
                metadata=job.metadata,
                expected_task_id=job.run_task_id,
                expected_execution_owner_id=self._execution_owner_id,
            )
            raise
        job.lease_deadline_monotonic = time.monotonic() + self._lease_seconds
        if lease_state == "active":
            self._start_lane_job(lane, job)
            return
        lane.queued.append(job)
        self._ensure_lane_poller(lane.name)

    def _ensure_lane_poller(self, lane_name: str) -> None:
        existing = self._lane_pollers.get(lane_name)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(self._poll_lane(lane_name))
        self._lane_pollers[lane_name] = task

    async def _poll_lane(self, lane_name: str) -> None:
        lane = self._lanes[lane_name]
        try:
            while lane.queued:
                for job in tuple(lane.queued):
                    try:
                        state = job.manager.get_task_for_project(
                            job.ctx,
                            str(job.envelope["task_type"]),
                            int(job.envelope.get("episode") or 0),
                            beat_num=job.envelope.get("beat_num"),
                            scope=job.envelope.get("scope"),
                        )
                        if state is None or state.status not in ACTIVE_PROJECT_TASK_STATUSES:
                            with contextlib.suppress(ValueError):
                                lane.queued.remove(job)
                            self._lease_store.release(
                                owner_id=self._execution_owner_id,
                                task_id=job.run_task_id,
                            )
                            continue
                        store = self._bind_cancellation_store(job.ctx)
                        cancel_requested = await store.is_cancel_requested(
                            project_id=job.project_id,
                            task_type=str(job.envelope["task_type"]),
                            episode=int(job.envelope.get("episode") or 0),
                            task_id=job.run_task_id,
                            beat_num=job.envelope.get("beat_num"),
                            scope=job.envelope.get("scope"),
                        )
                        if cancel_requested:
                            cancelled = job.manager.update_progress_for_project(
                                job.ctx,
                                str(job.envelope["task_type"]),
                                int(job.envelope.get("episode") or 0),
                                beat_num=job.envelope.get("beat_num"),
                                scope=job.envelope.get("scope"),
                                progress=state.progress,
                                current_task="任务已取消",
                                status="cancelled",
                                expected_task_id=job.run_task_id,
                                expected_execution_owner_id=self._execution_owner_id,
                            )
                            with contextlib.suppress(ValueError):
                                lane.queued.remove(job)
                            self._lease_store.release(
                                owner_id=self._execution_owner_id,
                                task_id=job.run_task_id,
                            )
                            if cancelled is False:
                                job.manager.expire_task_leases(job.ctx)
                            continue
                        lane_owned = self._lease_store.heartbeat(
                            owner_id=self._execution_owner_id,
                            task_id=job.run_task_id,
                            lease_seconds=self._lease_seconds,
                        )
                        task_owned = job.manager.heartbeat_task_lease(
                            job.ctx,
                            job.run_task_id,
                            self._execution_owner_id,
                            lease_seconds=self._lease_seconds,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.warning(
                            "Queued inline task coordination check failed; retaining until lease deadline: %s",
                            job.run_task_id,
                            exc_info=True,
                        )
                        if time.monotonic() < job.lease_deadline_monotonic:
                            continue
                        with contextlib.suppress(ValueError):
                            lane.queued.remove(job)
                        with contextlib.suppress(Exception):
                            self._lease_store.release(
                                owner_id=self._execution_owner_id,
                                task_id=job.run_task_id,
                            )
                        with contextlib.suppress(Exception):
                            job.manager.expire_task_leases(job.ctx)
                        continue
                    if task_owned and lane_owned:
                        job.lease_deadline_monotonic = time.monotonic() + self._lease_seconds
                        continue
                    if lane_owned:
                        self._lease_store.release(
                            owner_id=self._execution_owner_id,
                            task_id=job.run_task_id,
                        )
                    if task_owned:
                        job.manager.fail_task_for_project(
                            job.ctx,
                            str(job.envelope["task_type"]),
                            int(job.envelope.get("episode") or 0),
                            beat_num=job.envelope.get("beat_num"),
                            scope=job.envelope.get("scope"),
                            error="TASK_LEASE_EXPIRED",
                            metadata={**job.metadata, "error_code": "TASK_LEASE_EXPIRED"},
                            expected_task_id=job.run_task_id,
                            expected_execution_owner_id=self._execution_owner_id,
                        )
                    else:
                        job.manager.expire_task_leases(job.ctx)
                    if not (task_owned and lane_owned):
                        with contextlib.suppress(ValueError):
                            lane.queued.remove(job)
                self._drain_lane(lane_name)
                if lane.queued:
                    await asyncio.sleep(min(self._heartbeat_seconds, 0.1))
        finally:
            self._lane_pollers.pop(lane_name, None)

    def _start_lane_job(self, lane: _InlineLane, job: _InlineLaneJob) -> None:
        lane.active += 1
        lane.last_started_project_id = job.project_id
        task = asyncio.create_task(self._run_inline(lane, job))
        self._background_tasks.add(task)
        task.add_done_callback(
            lambda done, lane_name=lane.name, completed_job=job: (
                self._on_background_task_done(done, lane_name, completed_job)
            )
        )

    def _pop_next_lane_job(self, lane: _InlineLane) -> _InlineLaneJob | None:
        if not lane.queued:
            return None
        if len(lane.queued) == 1:
            return lane.queued.popleft()
        for index, job in enumerate(lane.queued):
            if job.project_id != lane.last_started_project_id:
                del lane.queued[index]
                return job
        return lane.queued.popleft()

    def _drain_lane(self, lane_name: str) -> None:
        lane = self._lanes[lane_name]
        while lane.active < lane.concurrency:
            job = self._pop_next_lane_job(lane)
            if job is None:
                return
            try:
                promoted = self._lease_store.promote(
                    owner_id=self._execution_owner_id,
                    task_id=job.run_task_id,
                    lane=lane.name,
                    active_limit=lane.concurrency,
                    lease_seconds=self._lease_seconds,
                )
            except Exception:
                logger.warning(
                    "Queued inline task promotion coordination failed: %s",
                    job.run_task_id,
                    exc_info=True,
                )
                if time.monotonic() >= job.lease_deadline_monotonic:
                    with contextlib.suppress(Exception):
                        job.manager.expire_task_leases(job.ctx)
                    continue
                lane.queued.appendleft(job)
                self._ensure_lane_poller(lane_name)
                return
            if not promoted:
                lane.queued.appendleft(job)
                self._ensure_lane_poller(lane_name)
                return
            job.lease_deadline_monotonic = time.monotonic() + self._lease_seconds
            self._start_lane_job(lane, job)

    def _remove_queued_task(self, task_id: str) -> bool:
        for lane in self._lanes.values():
            for index, job in enumerate(lane.queued):
                if job.run_task_id == task_id:
                    del lane.queued[index]
                    self._lease_store.release(
                        owner_id=self._execution_owner_id,
                        task_id=task_id,
                    )
                    return True
        return False

    async def _run_inline(
        self,
        lane: _InlineLane,
        job: _InlineLaneJob,
    ) -> None:
        try:
            lane_owned = await asyncio.to_thread(
                self._lease_store.heartbeat,
                owner_id=self._execution_owner_id,
                task_id=job.run_task_id,
                lease_seconds=self._lease_seconds,
            )
            task_owned = await asyncio.to_thread(
                job.manager.heartbeat_task_lease,
                job.ctx,
                job.run_task_id,
                self._execution_owner_id,
                lease_seconds=self._lease_seconds,
            )
        except Exception:
            logger.warning(
                "Inline task coordination is uncertain before dispatch: %s",
                job.run_task_id,
                exc_info=True,
            )
            with contextlib.suppress(Exception):
                self._lease_store.release(
                    owner_id=self._execution_owner_id,
                    task_id=job.run_task_id,
                )
            with contextlib.suppress(Exception):
                job.manager.fail_task_for_project(
                    job.ctx,
                    str(job.envelope["task_type"]),
                    int(job.envelope.get("episode") or 0),
                    beat_num=job.envelope.get("beat_num"),
                    scope=job.envelope.get("scope"),
                    error="TASK_COORDINATION_UNCERTAIN",
                    metadata={
                        **job.metadata,
                        "error_code": "TASK_COORDINATION_UNCERTAIN",
                    },
                    expected_task_id=job.run_task_id,
                    expected_execution_owner_id=self._execution_owner_id,
                )
            with contextlib.suppress(Exception):
                job.manager.expire_task_leases(job.ctx)
            return
        if not (lane_owned and task_owned):
            if lane_owned:
                self._lease_store.release(
                    owner_id=self._execution_owner_id,
                    task_id=job.run_task_id,
                )
            if task_owned:
                job.manager.fail_task_for_project(
                    job.ctx,
                    str(job.envelope["task_type"]),
                    int(job.envelope.get("episode") or 0),
                    beat_num=job.envelope.get("beat_num"),
                    scope=job.envelope.get("scope"),
                    error="TASK_LEASE_LOST",
                    metadata={**job.metadata, "error_code": "TASK_LEASE_LOST"},
                    expected_task_id=job.run_task_id,
                    expected_execution_owner_id=self._execution_owner_id,
                )
            else:
                job.manager.expire_task_leases(job.ctx)
            return
        job.lease_deadline_monotonic = time.monotonic() + self._lease_seconds
        run_envelope = {
            **job.envelope,
            "__execution_owner_id": self._execution_owner_id,
            "__execution_lease_seconds": self._lease_seconds,
        }
        heartbeat = asyncio.create_task(self._heartbeat_active_job(job))
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                lane.executor,
                partial(
                    run_project_task_core_sync,
                    run_envelope,
                    job.ctx,
                    job.manager,
                    run_task_id=job.run_task_id,
                    metadata=job.metadata,
                ),
            )
            if not result.get("cancelled") and not result.get("failed"):
                await self._drain_episode_graph_outbox(job.ctx)
        finally:
            with contextlib.suppress(RuntimeError):
                heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError, RuntimeError, GeneratorExit):
                await heartbeat
            self._lease_store.release(
                owner_id=self._execution_owner_id,
                task_id=job.run_task_id,
            )
            clear_local_task_stop(job.run_task_id)

    async def _heartbeat_active_job(self, job: _InlineLaneJob) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            try:
                lane_owned, task_owned = await asyncio.gather(
                    asyncio.to_thread(
                        self._lease_store.heartbeat,
                        owner_id=self._execution_owner_id,
                        task_id=job.run_task_id,
                        lease_seconds=self._lease_seconds,
                    ),
                    asyncio.to_thread(
                        job.manager.heartbeat_task_lease,
                        job.ctx,
                        job.run_task_id,
                        self._execution_owner_id,
                        lease_seconds=self._lease_seconds,
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Active inline task coordination is uncertain; stopping locally: %s",
                    job.run_task_id,
                    exc_info=True,
                )
                request_local_task_stop(job.run_task_id, reason="lease_lost")
                await asyncio.to_thread(kill_task_processes, job.run_task_id)
                return
            if lane_owned and task_owned:
                job.lease_deadline_monotonic = time.monotonic() + self._lease_seconds
                continue
            request_local_task_stop(job.run_task_id, reason="lease_lost")
            await asyncio.to_thread(kill_task_processes, job.run_task_id)
            return

    async def _drain_episode_graph_outbox(self, ctx) -> None:
        from novelvideo.api.deps import make_sqlite_store_for_context
        from novelvideo.episode_source_store import EpisodeSourceStore

        repository = EpisodeSourceStore(await make_sqlite_store_for_context(ctx))
        pending = await repository.list_graph_outbox()
        if not pending:
            return
        latest = max(pending, key=lambda item: int(item["target_revision"]))
        for payload in pending:
            revision = int(payload["target_revision"])
            if revision != int(latest["target_revision"]):
                await repository.delete_graph_outbox(revision)
        await self.enqueue_project_task(
            ctx, task_type="episode_graph_index", queue_kind="default",
            episode=0, scope=f"revision:{latest['target_revision']}", payload=latest,
        )

    def _on_background_task_done(
        self,
        task: asyncio.Task,
        lane_name: str,
        job: _InlineLaneJob,
    ) -> None:
        self._background_tasks.discard(task)
        lane = self._lanes[lane_name]
        lane.active = max(lane.active - 1, 0)
        if task.cancelled():
            try:
                job.manager.fail_task_for_project(
                    job.ctx,
                    str(job.envelope["task_type"]),
                    int(job.envelope.get("episode") or 0),
                    beat_num=job.envelope.get("beat_num"),
                    scope=job.envelope.get("scope"),
                    error="TASK_INTERRUPTED",
                    metadata={**job.metadata, "error_code": "TASK_INTERRUPTED"},
                    expected_task_id=job.run_task_id,
                    expected_execution_owner_id=self._execution_owner_id,
                )
            except Exception:
                logger.exception(
                    "Cancelled inline task could not be persisted as interrupted: %s",
                    job.run_task_id,
                )
            self._drain_lane(lane_name)
            return
        try:
            exc = task.exception()
        except Exception:
            logger.exception("Inline project task background runner failed")
            return
        if exc is not None:
            logger.error(
                "Inline project task background runner failed",
                exc_info=(type(exc), exc, exc.__traceback__),
            )
        self._drain_lane(lane_name)

    async def cancel_project_task(self, ctx, task_state) -> bool:
        store = self._bind_cancellation_store(ctx)
        await store.request_cancel(
            project_id=ctx.project_id,
            task_type=task_state.task_type,
            episode=task_state.episode,
            task_id=task_state.task_id,
            beat_num=task_state.beat_num,
            scope=task_state.scope,
        )
        removed_locally = self._remove_queued_task(task_state.task_id)
        manager = get_task_manager()
        current = manager.get_task_for_project(
            ctx,
            task_state.task_type,
            task_state.episode,
            beat_num=task_state.beat_num,
            scope=task_state.scope,
        )
        # Queued work is safe to terminate from any API instance. Its original
        # owner may already be gone, in which case waiting for that owner leaves
        # the task permanently queued with an unconsumed cancellation request.
        if current is not None and (
            current.status == "queued" or removed_locally or not current.execution_owner_id
        ):
            manager.update_progress_for_project(
                ctx,
                task_state.task_type,
                task_state.episode,
                beat_num=task_state.beat_num,
                scope=task_state.scope,
                progress=current.progress,
                current_task="任务已取消",
                status="cancelled",
                expected_task_id=current.task_id,
            )
            if current.status == "queued":
                self._lease_store.release_task(task_id=current.task_id)
        # taskkill/killpg 是阻塞调用(Windows 上可达秒级),不得占事件循环
        await asyncio.get_running_loop().run_in_executor(
            None, kill_task_processes, task_state.task_id
        )
        return True


class InMemoryCancellationStore:
    def __init__(self) -> None:
        self._keys: dict[str, float] = {}
        self._lock = threading.Lock()

    async def request_cancel(
        self,
        *,
        project_id: str,
        task_type: str,
        episode: int,
        task_id: str,
        beat_num: int | None = None,
        scope: str | None = None,
        ttl_seconds: int = 86_400,
    ) -> None:
        key = cancel_key(
            project_id=project_id,
            task_type=task_type,
            episode=episode,
            task_id=task_id,
            beat_num=beat_num,
            scope=scope,
        )
        with self._lock:
            self._keys[key] = time.time() + max(int(ttl_seconds), 0)

    async def is_cancel_requested(
        self,
        *,
        project_id: str,
        task_type: str,
        episode: int,
        task_id: str,
        beat_num: int | None = None,
        scope: str | None = None,
    ) -> bool:
        key = cancel_key(
            project_id=project_id,
            task_type=task_type,
            episode=episode,
            task_id=task_id,
            beat_num=beat_num,
            scope=scope,
        )
        with self._lock:
            expires_at = self._keys.get(key)
            if expires_at is None:
                return False
            if expires_at < time.time():
                self._keys.pop(key, None)
                return False
            return True


class SQLiteCancellationStore:
    """Project-local persistent cancellation flags for the CE backend.

    A fixed database path is convenient for workers and tests.  The default CE
    instance is context-bound by ``InlineTaskBackend`` before it is used.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS task_cancellations (
        cancel_key TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        requested_at REAL NOT NULL,
        expires_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_task_cancellations_expiry
    ON task_cancellations(expires_at);
    """

    def __init__(self, database_path: str | Path | None = None) -> None:
        self._database_path = Path(database_path).resolve() if database_path else None
        self._project_paths: dict[str, Path] = {}
        self._lock = threading.Lock()

    def bind_project(self, project_id: str, database_path: str | Path) -> None:
        path = Path(database_path).resolve()
        with self._lock:
            self._project_paths[str(project_id)] = path

    def _path_for(self, project_id: str) -> Path:
        if self._database_path is not None:
            return self._database_path
        with self._lock:
            path = self._project_paths.get(str(project_id))
        if path is None:
            raise RuntimeError(f"cancellation store is not bound for project {project_id}")
        return path

    def _connect(self, project_id: str) -> sqlite3.Connection:
        path = self._path_for(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
        configure_sqlite_connection(conn)
        conn.executescript(self._SCHEMA)
        return conn

    async def request_cancel(
        self,
        *,
        project_id: str,
        task_type: str,
        episode: int,
        task_id: str,
        beat_num: int | None = None,
        scope: str | None = None,
        ttl_seconds: int = 86_400,
    ) -> None:
        key = cancel_key(
            project_id=project_id,
            task_type=task_type,
            episode=episode,
            task_id=task_id,
            beat_num=beat_num,
            scope=scope,
        )
        now = time.time()
        with self._connect(project_id) as conn:
            conn.execute(
                "INSERT INTO task_cancellations "
                "(cancel_key, project_id, task_id, requested_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(cancel_key) DO UPDATE SET "
                "requested_at = excluded.requested_at, expires_at = excluded.expires_at",
                (key, str(project_id), str(task_id), now, now + max(int(ttl_seconds), 0)),
            )
            # This column is part of the project task schema in upgraded CE DBs.
            # Keep the cancellation table usable in isolation for port tests.
            try:
                conn.execute(
                    "UPDATE task_states SET cancel_requested_at = ? "
                    "WHERE project_id = ? AND task_id = ?",
                    (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), project_id, task_id),
                )
            except sqlite3.OperationalError:
                pass

    async def is_cancel_requested(
        self,
        *,
        project_id: str,
        task_type: str,
        episode: int,
        task_id: str,
        beat_num: int | None = None,
        scope: str | None = None,
    ) -> bool:
        key = cancel_key(
            project_id=project_id,
            task_type=task_type,
            episode=episode,
            task_id=task_id,
            beat_num=beat_num,
            scope=scope,
        )
        now = time.time()
        with self._connect(project_id) as conn:
            conn.execute("DELETE FROM task_cancellations WHERE expires_at <= ?", (now,))
            row = conn.execute(
                "SELECT 1 FROM task_cancellations "
                "WHERE cancel_key = ? AND expires_at > ?",
                (key, now),
            ).fetchone()
        return row is not None
