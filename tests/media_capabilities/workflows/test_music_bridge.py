from types import SimpleNamespace
import pytest
from novelvideo.music.generation import advance_generation
from novelvideo.music.store import MusicStore


async def test_registered_music_uses_service_and_never_legacy_submit(tmp_path):
    store = MusicStore(tmp_path / "music.db")
    store.create_job(
        "p",
        "j",
        "generate",
        {
            "workflow": "123",
            "request": {"tags": "piano", "seed": "1"},
            "owner": "alice",
            "workflowPackage": {"manifest": {"id": "music", "version": 1}},
        },
    )

    class Service:
        async def run(self, identifier, version, inputs, **kwargs):
            assert identifier == "music"
            assert inputs["tags"] == "Instrumental, no vocals. piano"
            return SimpleNamespace(
                run_id="run", provider_task_id=None, status="unknown", artifacts={}
            )

    class Client:
        async def submit(self, *args):
            pytest.fail("registered music must use the generic service")

    result = await advance_generation(
        store, "p", "j", Client(), tmp_path, workflow_service=Service()
    )
    assert result["status"] == "submission_unknown"
    assert result["workflowRunId"] == "run"
