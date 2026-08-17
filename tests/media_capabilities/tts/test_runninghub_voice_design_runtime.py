from __future__ import annotations

from dataclasses import dataclass

import pytest

from novelvideo.media_capabilities.runtime.runninghub_client import (
    ProviderResult,
    ProviderTaskSnapshot,
)
from novelvideo.media_capabilities.tts.runninghub_voice_design import (
    generate_qwen3_voice_sample,
)


class FakeClient:
    def __init__(self) -> None:
        self.submitted: tuple[str, list[dict]] | None = None
        self.queries = 0

    async def submit(self, workflow_id: str, node_info: list[dict]) -> str:
        self.submitted = (workflow_id, node_info)
        return "task-1"

    async def query(self, _task_id: str) -> ProviderTaskSnapshot:
        self.queries += 1
        if self.queries == 1:
            return ProviderTaskSnapshot(status="running")
        return ProviderTaskSnapshot(
            status="succeeded",
            results=(ProviderResult(url="https://cdn.invalid/voice.wav", node_id="18"),),
        )

    async def download(self, _url: str) -> bytes:
        return b"RIFFvoice"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


@dataclass
class FakeRuntime:
    client: FakeClient

    def workflow_id(self, _capability) -> str:
        return "workflow-qwen3"

    def create_client(self, *, download_allowed_hosts=()):
        assert set(download_allowed_hosts) in (set(), {"cdn.invalid"})
        return self.client


@pytest.mark.asyncio
async def test_voice_design_submits_locked_qwen3_bindings_and_downloads(monkeypatch) -> None:
    client = FakeClient()
    monkeypatch.setattr("novelvideo.media_capabilities.tts.runninghub_voice_design.asyncio.sleep", lambda _: _noop())

    content, filename = await generate_qwen3_voice_sample(
        FakeRuntime(client),
        audition_text="你好，我是秦。",
        voice_description="沉稳、温暖的青年男声",
        language="Chinese",
        poll_interval=0,
    )

    assert content == b"RIFFvoice"
    assert filename == "voice.wav"
    assert client.submitted == (
        "workflow-qwen3",
        [
            {"nodeId": "14", "fieldName": "text", "fieldValue": "你好，我是秦。"},
            {"nodeId": "15", "fieldName": "text", "fieldValue": "沉稳、温暖的青年男声"},
        ],
    )


async def _noop() -> None:
    return None
