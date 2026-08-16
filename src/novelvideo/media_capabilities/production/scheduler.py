"""Fair scheduling and safety gates for production DAG nodes."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from novelvideo.media_capabilities.production.models import (
    ProductionNode,
    ProductionNodeStatus,
    ProductionRunStatus,
)
from novelvideo.media_capabilities.production.store import ProductionStore


class _Dispatcher(Protocol):
    async def submit(self, node: ProductionNode) -> object: ...

    async def poll(self, node: ProductionNode) -> object: ...


class _Concurrency(Protocol):
    def lease(self, provider_id: str, capability: str) -> AsyncIterator[Any]: ...

    def snapshot(self, provider_id: str) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class SchedulerSnapshot:
    submitted: tuple[str, ...]
    polled: tuple[str, ...]
    blocked: Mapping[str, str]
    resources: Mapping[str, Mapping[str, object]]


class ProductionScheduler:
    def __init__(
        self,
        store: ProductionStore,
        dispatcher: _Dispatcher,
        concurrency: _Concurrency,
        *,
        submission_limit: int | None = None,
        consecutive_failure_limit: int = 3,
    ) -> None:
        if submission_limit is not None and submission_limit <= 0:
            raise ValueError("submission_limit must be positive")
        if consecutive_failure_limit <= 0:
            raise ValueError("consecutive_failure_limit must be positive")
        self.store = store
        self.dispatcher = dispatcher
        self.concurrency = concurrency
        self.submission_limit = submission_limit
        self.consecutive_failure_limit = consecutive_failure_limit
        self._last_submitted_project: str | None = None

    async def tick(self) -> SchedulerSnapshot:
        for run in self.store.list_running_runs():
            self.store.recompute_ready(run.id)

        submitted: list[str] = []
        polled: list[str] = []
        blocked: dict[str, str] = {}
        providers: set[str] = set()
        spent_by_run: dict[str, float] = {}
        candidates: list[tuple[ProductionNode, str, str]] = []

        ready = self._rotate_after_last_project(self.store.list_ready_round_robin())
        for node in ready:
            config = self._config(node)
            operation = str(config.get("operation", "submit"))
            if operation == "poll":
                await self.dispatcher.poll(node)
                polled.append(node.id)
                continue
            if self.submission_limit is not None and len(candidates) >= self.submission_limit:
                continue

            provider_id = str(config.get("provider_id", "local"))
            capability = str(config.get("capability", node.node_type))
            if (
                self.store.consecutive_provider_failures(provider_id)
                >= self.consecutive_failure_limit
            ):
                blocked[node.id] = "provider_circuit_open"
                continue

            estimated_cost = self._cost(config.get("estimated_cost", 0.0))
            run = self.store.get_run(node.run_id)
            budget = self._budget(run.config_snapshot)
            spent = spent_by_run.setdefault(
                node.run_id, self.store.spent_cost(node.run_id)
            )
            if budget is not None and spent + estimated_cost > budget:
                blocked[node.id] = "budget_exhausted"
                continue

            spent_by_run[node.run_id] = spent + estimated_cost
            providers.add(provider_id)
            self._last_submitted_project = run.project_id
            candidates.append((node, provider_id, capability))

        async def submit_one(
            candidate: tuple[ProductionNode, str, str],
        ) -> str:
            node, provider_id, capability = candidate
            async with self.concurrency.lease(provider_id, capability):
                await self.dispatcher.submit(node)
            self.store.transition_node(node.id, ProductionNodeStatus.QUEUED)
            return node.id

        if candidates:
            submitted.extend(await asyncio.gather(*(submit_one(item) for item in candidates)))

        resources = {
            provider_id: self.concurrency.snapshot(provider_id)
            for provider_id in sorted(providers)
        }
        return SchedulerSnapshot(
            submitted=tuple(submitted),
            polled=tuple(polled),
            blocked=blocked,
            resources=resources,
        )

    async def pause(self, run_id: str) -> None:
        self.store.transition_run(run_id, ProductionRunStatus.PAUSED)

    async def resume(self, run_id: str) -> None:
        self.store.recompute_ready(run_id)
        self.store.transition_run(run_id, ProductionRunStatus.RUNNING)

    async def cancel(self, run_id: str) -> None:
        self.store.cancel_unsubmitted(run_id)
        self.store.transition_run(run_id, ProductionRunStatus.CANCELLING)

    def _rotate_after_last_project(
        self, nodes: list[ProductionNode]
    ) -> list[ProductionNode]:
        if self._last_submitted_project is None:
            return nodes
        for index, node in enumerate(nodes):
            project_id = self.store.get_run(node.run_id).project_id
            if project_id != self._last_submitted_project:
                return nodes[index:] + nodes[:index]
        return nodes

    @staticmethod
    def _config(node: ProductionNode) -> Mapping[str, object]:
        snapshot = node.config_snapshot
        return snapshot if isinstance(snapshot, Mapping) else {}

    @staticmethod
    def _cost(value: object) -> float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0.0, float(value))
        return 0.0

    @staticmethod
    def _budget(snapshot: object) -> float | None:
        if not isinstance(snapshot, Mapping):
            return None
        value = snapshot.get("budget")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0.0, float(value))
        return None


__all__ = ["ProductionScheduler", "SchedulerSnapshot"]
