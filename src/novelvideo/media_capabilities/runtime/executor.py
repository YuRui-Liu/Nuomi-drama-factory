"""Single-step RunningHub task execution."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from pathlib import PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlsplit

from pydantic import JsonValue

from novelvideo.media_capabilities.models import (
    MediaArtifact,
    MediaTaskStatus,
    WorkflowProfile,
)
from novelvideo.media_capabilities.runtime.compiler import compile_node_info
from novelvideo.media_capabilities.runtime.runninghub_client import (
    ProviderTaskSnapshot,
    RunningHubError,
)
from novelvideo.media_capabilities.task_store import (
    MediaErrorCode,
    MediaTaskRecord,
    TaskNotFoundError,
    TaskStore,
)


class _RunningHubClient(Protocol):
    async def submit(self, workflow_id: str, node_info: Any) -> str: ...

    async def query(self, task_id: str) -> ProviderTaskSnapshot: ...

    async def download(self, url: str) -> bytes: ...

    async def cancel(self, task_id: str) -> None: ...


class _ArtifactStore(Protocol):
    def put_bytes(
        self,
        data: bytes,
        media_type: str,
        extension: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> MediaArtifact: ...


class _ConcurrencyCoordinator(Protocol):
    def lease(self, provider_account_id: str, capability: object) -> AsyncIterator[Any]: ...


class RunningHubExecutor:
    """Advance one durable task by at most one remote provider step."""

    def __init__(
        self,
        store: TaskStore,
        client: _RunningHubClient,
        artifacts: _ArtifactStore,
        concurrency: _ConcurrencyCoordinator,
    ) -> None:
        self._store = store
        self._client = client
        self._artifacts = artifacts
        self._concurrency = concurrency

    async def step(
        self,
        task_id: str,
        *,
        profile: WorkflowProfile,
        semantic_values: Mapping[str, JsonValue],
    ) -> MediaTaskRecord:
        task = self._store.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"task {task_id} not found")
        attempts = self._store.list_attempts(task_id)
        if not attempts:
            raise TaskNotFoundError(f"task {task_id} has no attempt")
        attempt = attempts[-1]
        phase = "query" if attempt.provider_task_id else "submit"

        try:
            async with self._concurrency.lease(
                attempt.provider_account_id, task.capability
            ):
                if attempt.provider_task_id is None:
                    if attempt.status == MediaTaskStatus.QUEUED:
                        attempt = self._store.transition_attempt(
                            attempt.id, MediaTaskStatus.PREPARING
                        )
                    node_info = compile_node_info(profile, semantic_values)
                    provider_task_id = await self._client.submit(
                        profile.workflow_id, node_info
                    )
                    self._store.record_provider_task(attempt.id, provider_task_id)
                    return self._current_task(task_id)

                snapshot = await self._client.query(attempt.provider_task_id)
                if snapshot.status == "failed":
                    self._store.record_provider_status(attempt.id, snapshot.status)
                    self._store.fail_attempt(
                        attempt.id,
                        error_code=MediaErrorCode.POLL_FAILED,
                        error_message="RunningHub provider task failed",
                    )
                    return self._current_task(task_id)
                if snapshot.status == "cancelled":
                    self._store.record_provider_status(attempt.id, snapshot.status)
                    attempt = self._store.transition_attempt(
                        attempt.id, MediaTaskStatus.CANCEL_REQUESTED
                    )
                    self._store.transition_attempt(
                        attempt.id, MediaTaskStatus.CANCELLED
                    )
                    return self._current_task(task_id)
                if snapshot.status != "succeeded":
                    self._store.record_provider_status(attempt.id, snapshot.status)
                    return self._current_task(task_id)

                if not snapshot.results:
                    self._store.fail_attempt(
                        attempt.id,
                        error_code=MediaErrorCode.OUTPUT_MISSING,
                        error_message="RunningHub provider task returned no output",
                    )
                    return self._current_task(task_id)

                expected_node_ids = {
                    str(output["node_id"])
                    for output in profile.outputs.values()
                    if isinstance(output, dict) and output.get("node_id") is not None
                }
                result = next(
                    (
                        item
                        for item in snapshot.results
                        if not expected_node_ids or item.node_id in expected_node_ids
                    ),
                    None,
                )
                if result is None:
                    self._store.fail_attempt(
                        attempt.id,
                        error_code=MediaErrorCode.OUTPUT_MISSING,
                        error_message="RunningHub provider task omitted the declared output",
                    )
                    return self._current_task(task_id)
                if attempt.status == MediaTaskStatus.SUBMITTED:
                    attempt = self._store.transition_attempt(
                        attempt.id, MediaTaskStatus.RUNNING
                    )
                attempt = self._store.transition_attempt(
                    attempt.id, MediaTaskStatus.DOWNLOADING
                )
                phase = "download"
                data = await self._client.download(result.url)
                artifact = self._artifacts.put_bytes(
                    data,
                    media_type=result.output_type or "application/octet-stream",
                    extension=self._extension(result.url),
                )
                self._store.transition_attempt(attempt.id, MediaTaskStatus.VALIDATING)
                self._store.complete_success(
                    attempt.id,
                    {"artifacts": [artifact.model_dump(mode="json")]},
                )
                return self._current_task(task_id)
        except RunningHubError:
            error_code = {
                "submit": MediaErrorCode.SUBMIT_FAILED,
                "query": MediaErrorCode.POLL_FAILED,
                "download": MediaErrorCode.DOWNLOAD_FAILED,
            }[phase]
            self._store.fail_attempt(
                attempt.id,
                error_code=error_code,
                error_message="RunningHub provider operation failed",
            )
            return self._current_task(task_id)

    def _current_task(self, task_id: str) -> MediaTaskRecord:
        task = self._store.get_task(task_id)
        assert task is not None
        return task

    async def cancel(self, task_id: str) -> MediaTaskRecord:
        task = self._store.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"task {task_id} not found")
        attempts = self._store.list_attempts(task_id)
        if not attempts:
            raise TaskNotFoundError(f"task {task_id} has no attempt")
        attempt = attempts[-1]
        if attempt.status in {
            MediaTaskStatus.SUCCEEDED,
            MediaTaskStatus.FAILED,
            MediaTaskStatus.QUALITY_FAILED,
            MediaTaskStatus.CANCELLED,
        }:
            return task
        attempt = self._store.transition_attempt(
            attempt.id, MediaTaskStatus.CANCEL_REQUESTED
        )
        if attempt.provider_task_id is not None:
            try:
                await self._client.cancel(attempt.provider_task_id)
            except RunningHubError:
                self._store.fail_attempt(
                    attempt.id,
                    error_code=MediaErrorCode.CANCELLED,
                    error_message="RunningHub provider cancellation failed",
                )
                return self._current_task(task_id)
        self._store.transition_attempt(attempt.id, MediaTaskStatus.CANCELLED)
        return self._current_task(task_id)

    @staticmethod
    def _extension(url: str) -> str:
        suffix = PurePosixPath(urlsplit(url).path).suffix
        return suffix.removeprefix(".").lower() or "bin"


__all__ = ["RunningHubExecutor"]
