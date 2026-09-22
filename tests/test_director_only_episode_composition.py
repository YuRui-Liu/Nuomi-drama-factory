from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from novelvideo.task_backend.runners import video


def _director(monkeypatch, tmp_path, statuses=("completed",)):
    from novelvideo.director_plan.store import DirectorPlanStore

    groups = [SimpleNamespace(id=f"group-{i}", ordinal=i, director_revision_id="r1", stages={"video": SimpleNamespace(status=status, needs_regeneration=False, revision=2, manifest_asset=f"{tmp_path}/group-{i}.json")}) for i, status in enumerate(statuses, 1)]
    active = SimpleNamespace(revision_id="r1", groups=tuple(SimpleNamespace(id=g.id) for g in groups))
    monkeypatch.setattr(DirectorPlanStore, "load_active", lambda *args: active)
    monkeypatch.setattr(video, "load_materialized_groups", lambda *args: groups)
    return groups, active


@pytest.mark.parametrize("status", ("pending", "running", "failed"))
def test_director_only_source_cannot_skip_unfinished_group(monkeypatch, tmp_path, status):
    _director(monkeypatch, tmp_path, (status,))
    with pytest.raises(ValueError, match="DIRECTOR_COMPOSITION_INCOMPLETE.*group-1"):
        video.resolve_episode_composition_sources(tmp_path, 1, [])


def test_director_group_snapshot_requires_complete_active_membership(monkeypatch, tmp_path):
    groups, active = _director(monkeypatch, tmp_path)
    active.groups += (SimpleNamespace(id="group-missing"),)
    with pytest.raises(ValueError, match="DIRECTOR_COMPOSITION_INCOMPLETE"):
        video.director_composition_snapshot(tmp_path, 1)


async def test_api_queues_director_only_episode_without_fabricating_beats(monkeypatch, tmp_path):
    from novelvideo.api.routes import generation

    _director(monkeypatch, tmp_path)
    ctx = SimpleNamespace(project_id="project")
    resolved = SimpleNamespace(ctx=ctx, username="alice", project_name="demo", output_dir=str(tmp_path))
    monkeypatch.setattr(generation, "_resolve_generation_project", AsyncMock(return_value=resolved))
    monkeypatch.setattr(generation, "make_sqlite_store_for_context", AsyncMock(return_value=SimpleNamespace(get_beats_as_dicts=AsyncMock(return_value=[]))))
    enqueue = AsyncMock(return_value=SimpleNamespace(task_state=SimpleNamespace(task_id="task"), backend="local", queue="ffmpeg"))
    monkeypatch.setattr(generation, "get_task_backend", lambda: SimpleNamespace(enqueue_project_task=enqueue))
    result = await generation.compose_video("project", 1, generation.VideoComposeRequest(), {"username": "alice"})
    assert result["ok"] is True
    payload = enqueue.call_args.kwargs["payload"]
    assert payload["beats"] == []
    assert payload["director_composition"]["group_ids"] == ["group-1"]
    assert payload["director_composition"]["revision_id"] == "r1"


def test_runner_rejects_changed_frozen_director_groups_before_ffmpeg(monkeypatch, tmp_path):
    _director(monkeypatch, tmp_path)
    monkeypatch.setattr(video, "get_task_manager", lambda: SimpleNamespace())
    with pytest.raises(ValueError, match="DIRECTOR_COMPOSITION_CHANGED"):
        video.run_compose_episode({"episode": 1, "payload": {"beats": [], "director_composition": {"revision_id": "old", "group_ids": ["old"]}}}, SimpleNamespace(output_dir=tmp_path))


@pytest.mark.parametrize("change_during_compose", (False, True))
def test_three_shots_in_two_native_spans_compose_without_legacy_beats(monkeypatch, tmp_path, change_during_compose):
    from novelvideo.media_capabilities.video.h3_timeline import (
        DialogueSource, H3DirectorOutputManifest, H3DirectorSegment,
        build_h3_timeline_data, save_h3_director_manifest,
    )

    groups, active = _director(monkeypatch, tmp_path)
    active.groups[0].shots = [SimpleNamespace(id=f"s{i}") for i in (1, 2, 3)]
    source = tmp_path / "director.mp4"
    source.write_bytes(b"native source")
    segments = tuple(H3DirectorSegment(segment_id=f"span-{n}", beat_number=n, prompt="p", duration_seconds=1, first_frame="frame.png", dialogue_source=DialogueSource.H3_NATIVE) for n in (1, 3))
    save_h3_director_manifest(Path(groups[0].stages["video"].manifest_asset), H3DirectorOutputManifest(physical_video=str(source), entries=build_h3_timeline_data(segments).entries))
    snapshot = video.director_composition_snapshot(tmp_path, 1)
    calls = []

    def run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout="audio", stderr="")
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"composed")
        if len(calls) == 2 and change_during_compose:
            groups[0].stages["video"].revision += 1
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(video, "run_project_subprocess", run)
    monkeypatch.setattr(video, "raise_if_envelope_cancel_requested", lambda *a, **kw: None)
    monkeypatch.setattr(video, "get_task_manager", lambda: SimpleNamespace(update_progress_for_project=lambda *a, **kw: None))
    envelope = {"episode": 1, "payload": {"beats": [], "director_composition": snapshot}}
    if change_during_compose:
        with pytest.raises(ValueError, match="DIRECTOR_COMPOSITION_CHANGED"):
            video.run_compose_episode(envelope, SimpleNamespace(output_dir=tmp_path))
        assert not (tmp_path / "videos/episodes/ep001_final.mp4").exists()
    else:
        result = video.run_compose_episode(envelope, SimpleNamespace(output_dir=tmp_path))
        assert Path(result["video_path"]).read_bytes() == b"composed"
        assert len(calls) == 2


def test_director_only_stale_group_is_blocked(monkeypatch, tmp_path):
    groups, _ = _director(monkeypatch, tmp_path)
    groups[0].stages["video"].needs_regeneration = True
    with pytest.raises(ValueError, match="DIRECTOR_COMPOSITION_INCOMPLETE.*group-1"):
        video.director_composition_snapshot(tmp_path, 1)
