import pytest
from contextlib import asynccontextmanager

from novelvideo.media_capabilities.workflows.service import WorkflowService
from novelvideo.media_capabilities.workflows.registry import WorkflowRegistry
from novelvideo.media_capabilities.runtime.runninghub_client import (
    ProviderResult,
    ProviderTaskSnapshot,
    RunningHubError,
)
from novelvideo.media_capabilities.runtime.artifacts import ArtifactStore
from novelvideo.media_capabilities.store import MediaCapabilityStore
from novelvideo.media_capabilities.task_store import TaskStore
from tests.media_capabilities.workflows.test_runtime_contracts import package


class Client:
    def __init__(self, graph):
        self.graph = graph
        self.submits = 0
        self.downloads = []
        self.fail_download = True
        self.unknown = False

    async def workflow_json(self, workflow_id):
        return self.graph

    async def submit(self, workflow_id, node_info):
        self.submits += 1
        assert node_info == [
            {"nodeId": "1", "fieldName": "text", "fieldValue": "hello"}
        ]
        if self.unknown:
            raise TimeoutError("lost response")
        return "remote-1"

    async def query(self, task_id):
        return ProviderTaskSnapshot(
            status="succeeded",
            results=(
                ProviderResult(
                    url="https://example.com/a.mp3", output_type="audio", node_id="2"
                ),
                ProviderResult(
                    url="https://example.com/b.mp3", output_type="audio", node_id="2"
                ),
            ),
        )

    async def download(self, url):
        self.downloads.append(url)
        if self.fail_download and url.endswith("b.mp3"):
            raise RunningHubError("temporary network failure", retriable=True)
        return url.encode()

    async def cancel(self, task_id):
        pass


class Concurrency:
    @asynccontextmanager
    async def lease(self, account, capability):
        yield


def setup(tmp_path):
    root, data, graph = package(tmp_path)
    registry = WorkflowRegistry(MediaCapabilityStore(tmp_path / "settings.db"))
    registry.register(root)
    client = Client(graph)
    store = TaskStore(tmp_path / "tasks.db")
    service = WorkflowService(
        registry,
        store,
        client,
        ArtifactStore(tmp_path / "artifacts"),
        Concurrency(),
        account_id="account",
    )
    return service, store, client


async def test_run_resume_and_partial_download(tmp_path):
    service, store, client = setup(tmp_path)
    first = await service.run("custom", 1, {"prompt": "hello"}, idempotency_key="one")
    assert first.status == "submitted"
    same = await service.run("custom", 1, {"prompt": "hello"}, idempotency_key="one")
    assert same.run_id == first.run_id
    assert client.submits == 1
    await service.resume(first.run_id)
    client.fail_download = False
    result = await service.resume(first.run_id)
    assert result.status == "succeeded"
    assert len(result.artifacts["audio"]) == 2
    assert client.downloads.count("https://example.com/a.mp3") == 1
    assert client.submits == 1


async def test_unknown_submission_cannot_resubmit(tmp_path):
    service, store, client = setup(tmp_path)
    client.unknown = True
    result = await service.run("custom", 1, {"prompt": "hello"}, idempotency_key="one")
    assert result.status == "unknown"
    assert (await service.resume(result.run_id)).status == "unknown"
    assert client.submits == 1


async def test_cancel_checks_frozen_account(tmp_path):
    from novelvideo.media_capabilities.workflows.models import WorkflowError

    service, store, client = setup(tmp_path)
    result = await service.run("custom", 1, {"prompt": "hello"})
    service.account_id = "another-account"
    with pytest.raises(WorkflowError, match="account_mismatch"):
        await service.cancel(result.run_id)


def test_output_contract_rejects_ambiguous_or_extra_results(tmp_path):
    from novelvideo.media_capabilities.workflows.models import (
        WorkflowError,
        WorkflowManifest,
    )
    from novelvideo.media_capabilities.runtime.output_collection import select_outputs

    root, data, graph = package(tmp_path)
    manifest = WorkflowManifest.model_validate(data)
    with pytest.raises(WorkflowError, match="output_count"):
        select_outputs(
            manifest,
            [ProviderResult(url="https://example.com/a.mp3", output_type="audio")],
        )
    with pytest.raises(WorkflowError, match="output_type"):
        select_outputs(
            manifest,
            [
                ProviderResult(
                    url="https://example.com/a.mp4", output_type="video", node_id="2"
                )
            ],
        )
    with pytest.raises(WorkflowError, match="output_count"):
        select_outputs(
            manifest,
            [
                ProviderResult(
                    url="https://example.com/a.mp3", output_type="audio", node_id="2"
                )
            ]
            * 3,
        )
