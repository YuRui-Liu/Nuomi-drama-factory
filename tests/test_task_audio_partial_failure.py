from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_indextts2_audio_runner_marks_collected_beat_errors_partial_failure(
    monkeypatch, tmp_path
):
    from novelvideo.audio.indextts2_beat_audio_task import IndexTTS2BeatAudioTaskResult
    from novelvideo.task_backend.runners import audio

    class Store:
        def __init__(self, *_args, **_kwargs):
            self.closed = False

        async def initialize(self):
            return None

        async def close(self):
            self.closed = True

    detail = IndexTTS2BeatAudioTaskResult(
        total_targets=2,
        generated=1,
        generated_beats=[1],
        failed=["Beat 02: provider failed"],
        mode="redo_selected",
    )

    async def generate(**_kwargs):
        return detail

    monkeypatch.setattr("novelvideo.sqlite_store.SQLiteStore", Store)
    monkeypatch.setattr(
        "novelvideo.audio.indextts2_beat_audio_task.run_indextts2_beat_audio_generation",
        generate,
    )
    monkeypatch.setattr(
        audio,
        "get_task_manager",
        lambda: SimpleNamespace(update_progress_for_project=lambda *_a, **_k: None),
    )
    ctx = SimpleNamespace(
        owner_project_label="alice/demo",
        owner_username="alice",
        project_name="demo",
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
    )

    result = await audio._run_indextts2_audio(
        {
            "episode": 1,
            "payload": {"beat_numbers": [1, 2], "mode": "redo_selected"},
        },
        ctx,
    )

    assert result is not None
    assert result["status"] == "partial_failure"
    assert result["failed"] == 1
    assert result["indextts2_detail"] == detail.to_dict()
