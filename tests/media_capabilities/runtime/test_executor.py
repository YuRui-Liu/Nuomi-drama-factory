from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from novelvideo.media_capabilities.models import (
    MediaArtifact,
    MediaCapability,
    MediaTaskStatus,
    WorkflowProfile,
)
from novelvideo.media_capabilities.runtime.executor import RunningHubExecutor
from novelvideo.media_capabilities.runtime.runninghub_client import (
    ProviderResult,
    ProviderTaskSnapshot,
    RunningHubError,
)
from novelvideo.media_capabilities.task_store import MediaErrorCode, TaskStore


class FakeClient:
    def __init__(self, snapshot: ProviderTaskSnapshot | None = None) -> None:
        self.snapshot = snapshot
        self.submit_calls: list[tuple[str, list[dict[str, object]]]] = []
        self.query_calls: list[str] = []
        self.download_calls: list[str] = []
        self.error: RunningHubError | None = None

    async def submit(self, workflow_id: str, node_info: list[dict[str, object]]) -> str:
        self.submit_calls.append((workflow_id, node_info))
        return "remote-1"

    async def query(self, task_id: str) -> ProviderTaskSnapshot:
        self.query_calls.append(task_id)
        if self.error is not None:
            raise self.error
        assert self.snapshot is not None
        return self.snapshot

    async def download(self, url: str) -> bytes:
        self.download_calls.append(url)
        return b"result"


class FakeArtifacts:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str, str, dict[str, object] | None]] = []

    def put_bytes(
        self,
        data: bytes,
        media_type: str,
        extension: str,
        metadata: dict[str, object] | None = None,
    ) -> MediaArtifact:
        self.calls.append((data, media_type, extension, metadata))
        return MediaArtifact(
            id="artifact-1",
            media_type=media_type,
            local_path=f"artifact-1.{extension}",
            content_sha256="sha256",
            metadata=metadata or {},
        )


class FakeConcurrency:
    def __init__(self) -> None:
        self.calls: list[tuple[str, MediaCapability]] = []

    @asynccontextmanager
    async def lease(self, provider_account_id: str, capability: MediaCapability):
        self.calls.append((provider_account_id, capability))
        yield


def profile() -> WorkflowProfile:
    return WorkflowProfile(
        id="profile-1",
        version=1,
        workflow_id="workflow-1",
        capabilities=[MediaCapability.IMAGE_SINGLE],
        bindings={"prompt": {"node_id": "7", "field": "text"}},
    )


def setup_attempt(tmp_path: Path, *, submitted: bool):
    store = TaskStore(tmp_path / "tasks.db")
    task = store.create_task(
        MediaCapability.IMAGE_SINGLE,
        "request-1",
        {"id": "implementation-1"},
        {"prompt": "hello"},
    )
    attempt = store.start_attempt(task.id, "account-1")
    if submitted:
        attempt = store.record_provider_task(attempt.id, "remote-existing")
    return store, task, attempt


@pytest.mark.asyncio
async def test_existing_provider_task_queries_without_resubmitting(tmp_path: Path) -> None:
    store, task, _ = setup_attempt(tmp_path, submitted=True)
    client = FakeClient(ProviderTaskSnapshot(status="submitted"))
    concurrency = FakeConcurrency()
    executor = RunningHubExecutor(store, client, FakeArtifacts(), concurrency)

    await executor.step(task.id, profile=profile(), semantic_values={"prompt": "hello"})

    assert client.query_calls == ["remote-existing"]
    assert client.submit_calls == []
    assert concurrency.calls == [("account-1", MediaCapability.IMAGE_SINGLE)]


@pytest.mark.asyncio
async def test_missing_provider_task_submits_once_and_returns_submitted(tmp_path: Path) -> None:
    store, task, attempt = setup_attempt(tmp_path, submitted=False)
    client = FakeClient()
    executor = RunningHubExecutor(store, client, FakeArtifacts(), FakeConcurrency())

    result = await executor.step(
        task.id,
        profile=profile(),
        semantic_values={"prompt": "hello"},
    )

    assert client.submit_calls == [
        (
            "workflow-1",
            [{"nodeId": "7", "fieldName": "text", "fieldValue": "hello"}],
        )
    ]
    assert client.query_calls == []
    assert store.get_attempt(attempt.id).provider_task_id == "remote-1"
    assert result.status == MediaTaskStatus.SUBMITTED


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_status", ["pending", "running"])
async def test_pending_or_running_records_provider_status_and_returns(
    tmp_path: Path, provider_status: str
) -> None:
    store, task, attempt = setup_attempt(tmp_path, submitted=True)
    client = FakeClient(ProviderTaskSnapshot(status=provider_status))
    executor = RunningHubExecutor(store, client, FakeArtifacts(), FakeConcurrency())

    result = await executor.step(task.id, profile=profile(), semantic_values={})

    assert store.get_attempt(attempt.id).provider_status == provider_status
    assert result.status == MediaTaskStatus.SUBMITTED
    assert client.query_calls == ["remote-existing"]


@pytest.mark.asyncio
async def test_provider_failed_status_finishes_attempt_instead_of_polling_forever(
    tmp_path: Path,
) -> None:
    store, task, attempt = setup_attempt(tmp_path, submitted=True)
    client = FakeClient(ProviderTaskSnapshot(status="failed"))
    executor = RunningHubExecutor(store, client, FakeArtifacts(), FakeConcurrency())

    result = await executor.step(task.id, profile=profile(), semantic_values={})

    failed = store.get_attempt(attempt.id)
    assert failed.status is MediaTaskStatus.FAILED
    assert failed.provider_status == "failed"
    assert failed.error_code is MediaErrorCode.POLL_FAILED
    assert result.status is MediaTaskStatus.FAILED


@pytest.mark.asyncio
async def test_succeeded_downloads_persists_and_completes(tmp_path: Path) -> None:
    store, task, attempt = setup_attempt(tmp_path, submitted=True)
    url = "https://cdn.example/result.PNG?signature=secret"
    client = FakeClient(
        ProviderTaskSnapshot(
            status="succeeded",
            results=(ProviderResult(url=url, output_type="image/png"),),
        )
    )
    artifacts = FakeArtifacts()
    executor = RunningHubExecutor(store, client, artifacts, FakeConcurrency())

    result = await executor.step(task.id, profile=profile(), semantic_values={})

    assert client.download_calls == [url]
    assert artifacts.calls == [(b"result", "image/png", "png", None)]
    assert result.status == MediaTaskStatus.SUCCEEDED
    assert result.output == {
        "artifacts": [
            {
                "id": "artifact-1",
                "media_type": "image/png",
                "local_path": "artifact-1.png",
                "content_sha256": "sha256",
                "metadata": {},
            }
        ]
    }
    assert store.get_attempt(attempt.id).status == MediaTaskStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_succeeded_selects_declared_output_node_instead_of_first_result(
    tmp_path: Path,
) -> None:
    store, task, _ = setup_attempt(tmp_path, submitted=True)
    client = FakeClient(
        ProviderTaskSnapshot(
            status="succeeded",
            results=(
                ProviderResult(url="https://cdn.example/preview.png", node_id="99"),
                ProviderResult(
                    url="https://cdn.example/video.mp4",
                    output_type="video/mp4",
                    node_id="136",
                ),
            ),
        )
    )
    selected_profile = WorkflowProfile.model_validate(
        {
            **profile().model_dump(mode="json"),
            "outputs": {"video": {"node_id": "136", "media_type": "video"}},
        }
    )
    executor = RunningHubExecutor(store, client, FakeArtifacts(), FakeConcurrency())

    await executor.step(task.id, profile=selected_profile, semantic_values={})

    assert client.download_calls == ["https://cdn.example/video.mp4"]


@pytest.mark.asyncio
async def test_provider_cancelled_status_finishes_attempt(tmp_path: Path) -> None:
    store, task, attempt = setup_attempt(tmp_path, submitted=True)
    client = FakeClient(ProviderTaskSnapshot(status="cancelled"))
    executor = RunningHubExecutor(store, client, FakeArtifacts(), FakeConcurrency())

    result = await executor.step(task.id, profile=profile(), semantic_values={})

    assert store.get_attempt(attempt.id).status is MediaTaskStatus.CANCELLED
    assert result.status is MediaTaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_runninghub_error_fails_with_stable_redacted_error(tmp_path: Path) -> None:
    store, task, attempt = setup_attempt(tmp_path, submitted=True)
    client = FakeClient()
    secret = "https://secret.example/output?token=do-not-leak"
    client.error = RunningHubError(secret)
    executor = RunningHubExecutor(store, client, FakeArtifacts(), FakeConcurrency())

    result = await executor.step(task.id, profile=profile(), semantic_values={})

    failed = store.get_attempt(attempt.id)
    assert failed.error_code == MediaErrorCode.POLL_FAILED
    assert failed.error_message == "RunningHub provider operation failed"
    assert secret not in failed.error_message
    assert result.status == MediaTaskStatus.FAILED
