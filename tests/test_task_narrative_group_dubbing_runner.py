from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.audio.dubbing_contracts import DialogueSnapshot, DubbingPreparation
from novelvideo.audio.dubbing_service import (
    compile_dialogue_performance,
    configured_index_tts2_profile,
)
from novelvideo.audio.dubbing_store import DubbingStore
from novelvideo.task_backend.runners import narrative_group_dubbing as runner


def _seed_queued_task(tmp_path: Path):
    line = DialogueSnapshot(
        dialogue_id="line-1",
        character_id="char-1",
        group_id="group-1",
        text="走吧。",
        voice_version_id="voice-v1",
        performance=compile_dialogue_performance("走吧。", {"mode": "neutral"}),
    )
    prep = DubbingPreparation(
        preparation_id="prep-1",
        episode=1,
        group_id="group-1",
        script_revision="script-r1",
        source_revision="video-r1:source",
        expected_revision=0,
        source_snapshot_hash=hashlib.sha256(b"snapshot").hexdigest(),
        dialogue_ids=(line.dialogue_id,),
        dialogues=(line,),
        source_dialogues=(line,),
    )
    store = DubbingStore(tmp_path / "data.db", tmp_path)
    store.save_preparation(prep)
    task, _ = store.create_task(
        episode=1,
        group_id="group-1",
        preparation_id=prep.preparation_id,
        request_id="request-1",
        payload_hash=hashlib.sha256(b"payload").hexdigest(),
        dialogue_ids=(line.dialogue_id,),
    )
    task = store.update_task(task.task_id, status="queued", backend_task_id="backend-1")
    return store, task


class FakeSQLiteStore:
    def __init__(self, _label, *, output_dir: str, state_dir: str):
        self.db_path = Path(output_dir) / "data.db"

    async def initialize(self):
        pass

    async def load_graph_state(self):
        pass

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_runner_configuration_drift_fails_before_provider_call(monkeypatch, tmp_path):
    from novelvideo import config
    from novelvideo import sqlite_store as sqlite_store_module
    from novelvideo.audio import dubbing_service

    monkeypatch.setattr(config, "INDEXTTS2_PROVIDER", "newapi")
    monkeypatch.setattr(config, "INDEXTTS2_NEWAPI_MODEL", "index-tts-2")
    monkeypatch.setattr(
        config,
        "get_effective_newapi_gateway_config",
        lambda: SimpleNamespace(base_url="https://gateway.example/v1", api_key="secret"),
    )
    store, task = _seed_queued_task(tmp_path)
    monkeypatch.setattr(sqlite_store_module, "SQLiteStore", FakeSQLiteStore)
    monkeypatch.setattr(runner, "_configured_coordinator", lambda _provider: object())
    monkeypatch.setattr(runner, "_configured_generator", lambda _profile: object())
    calls = []

    async def do_not_call(**_kwargs):
        calls.append(True)
        return {}

    monkeypatch.setattr(dubbing_service, "run_dubbing_task", do_not_call)
    profile = configured_index_tts2_profile()
    payload = {
        "internal_task_id": task.task_id,
        "episode": 1,
        "group_id": "group-1",
        "provider": "fal",
        "provider_id": "indextts2-fal",
        "model": "IndexTTS2",
        "workflow_version": profile["workflow_version"],
    }
    ctx = SimpleNamespace(
        owner_project_label="demo",
        output_dir=str(tmp_path),
        state_dir=str(tmp_path),
    )

    with pytest.raises(ValueError, match="configuration changed"):
        await runner._run_narrative_group_dubbing({"payload": payload}, ctx)

    assert calls == []
    assert store.get_task(task.task_id).status == "failed"


@pytest.mark.asyncio
async def test_runner_records_failure_when_generation_is_cancelled(monkeypatch, tmp_path):
    from novelvideo import config
    from novelvideo import sqlite_store as sqlite_store_module
    from novelvideo.audio import dubbing_service

    monkeypatch.setattr(config, "INDEXTTS2_PROVIDER", "fal")
    monkeypatch.setattr(config, "INDEXTTS2_FAL_ENDPOINT", "https://fal.example/index-tts-2")
    store, task = _seed_queued_task(tmp_path)
    monkeypatch.setattr(sqlite_store_module, "SQLiteStore", FakeSQLiteStore)
    monkeypatch.setattr(runner, "_configured_coordinator", lambda _provider: object())
    monkeypatch.setattr(runner, "_configured_generator", lambda _profile: object())

    async def cancel(**_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(dubbing_service, "run_dubbing_task", cancel)
    profile = configured_index_tts2_profile()
    payload = {
        "internal_task_id": task.task_id,
        "episode": 1,
        "group_id": "group-1",
        **profile,
        "compiler_version": "1",
    }
    ctx = SimpleNamespace(
        owner_project_label="demo",
        output_dir=str(tmp_path),
        state_dir=str(tmp_path),
    )

    with pytest.raises(asyncio.CancelledError):
        await runner._run_narrative_group_dubbing({"payload": payload}, ctx)

    current = store.get_task(task.task_id)
    assert current.status == "failed"
    assert "CancelledError" in current.error


def test_runner_uses_saved_default_lane_concurrency(monkeypatch):
    from novelvideo.task_concurrency_settings import TaskConcurrencySettings

    calls = []

    class Coordinator:
        def configure(self, provider_id, **kwargs):
            calls.append((provider_id, kwargs))

    monkeypatch.setattr(runner, "_INDEX_TTS2_COORDINATORS", {})
    monkeypatch.setattr(runner, "ProviderConcurrencyCoordinator", Coordinator)
    monkeypatch.setattr(
        "novelvideo.task_concurrency_settings.load_task_concurrency_settings",
        lambda: TaskConcurrencySettings({"default": 9, "video": 5, "world": 1, "ffmpeg": 1}),
    )

    runner._configured_coordinator("indextts2-newapi")

    assert calls == [
        (
            "indextts2-newapi",
            {"max_concurrency": 9, "queue_limit": runner.DEFAULT_QUEUE_LIMIT},
        )
    ]
