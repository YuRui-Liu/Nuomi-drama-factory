from dataclasses import replace

import pytest
from PIL import Image

from tests.test_storyboard_sources import _source
from novelvideo.narrative_groups.service import group_beats


def selected_group(tmp_path):
    source = _source(tmp_path)
    group = group_beats([{"id": "shot-1"}])[0]
    source = source.model_copy(update={"group_id": group.id})
    return replace(group, stages={**group.stages, "render": replace(
        group.stages["render"],
        selected_storyboard_id=source.source_id,
        selected_storyboard_sources={source.batch_id: source.source_id},
        storyboard_sources=(source.model_dump(mode="json"),),
        cell_assets=({"beat_id": "shot-1", "path": str(tmp_path / "wrong.png")},),
    )}), source


def test_freeze_uses_selected_source_not_shared_frames(tmp_path):
    from novelvideo.narrative_groups.storyboard_binding import freeze_selected_storyboard

    group, source = selected_group(tmp_path)
    Image.new("RGB", (8, 8), "red").save(tmp_path / "wrong.png")
    binding, frames = freeze_selected_storyboard(group, media_root=tmp_path,
                                               project_id="project", episode=1)
    assert binding.selection_id == source.source_id
    assert set(frames) == {str(tmp_path / "cell.png")}
    assert next(iter(frames.values())).sha256 == source.cells[0].sha256


def test_freeze_rejects_corrupt_selected_cell(tmp_path):
    from novelvideo.narrative_groups.storyboard_binding import freeze_selected_storyboard

    group, _ = selected_group(tmp_path)
    Image.new("RGB", (8, 8), "red").save(tmp_path / "cell.png")
    with pytest.raises(ValueError, match="digest"):
        freeze_selected_storyboard(group, media_root=tmp_path, project_id="project", episode=1)


def test_persisted_binding_survives_live_file_changes(tmp_path):
    from novelvideo.narrative_groups.storyboard_binding import freeze_selected_storyboard
    from novelvideo.media_capabilities.video.h3_reference_runtime import (
        persist_h3_reference_input_snapshot, load_h3_reference_input_snapshot,
    )

    group, source = selected_group(tmp_path)
    binding, frames = freeze_selected_storyboard(group, media_root=tmp_path,
                                               project_id="project", episode=1)
    persisted = persist_h3_reference_input_snapshot(
        state_root=tmp_path / "state", references=(), frames=frames,
        reference_revision=0, reference_limit=0, provider_workflow_id="workflow",
        storyboard_binding=binding,
    )
    Image.new("RGB", (8, 8), "red").save(tmp_path / "cell.png")
    loaded = load_h3_reference_input_snapshot(state_root=tmp_path / "state",
        snapshot_id=persisted.snapshot_id, expected_digest=persisted.digest, frame_sources=tuple(frames))
    assert loaded.storyboard_binding == binding
    assert next(iter(loaded.frames.values())).sha256 == source.cells[0].sha256
    from novelvideo.narrative_groups.storyboard_binding import load_queued_storyboard

    queued_binding, queued_snapshot = load_queued_storyboard(
        {"storyboard_contract_version": 1, "storyboard_source_id": binding.selection_id,
         "storyboard_binding": binding.model_dump(mode="json"),
         "reference_snapshot_id": persisted.snapshot_id, "reference_snapshot_digest": persisted.digest},
        media_root=tmp_path, state_root=tmp_path / "state", project_id="project",
        episode=1, group_id=group.id,
    )
    assert queued_binding == binding
    assert next(iter(queued_snapshot.frames.values())).sha256 == source.cells[0].sha256


def test_partial_selection_cannot_freeze_complete_group(tmp_path):
    from novelvideo.narrative_groups.storyboard_binding import freeze_selected_storyboard

    group, _ = selected_group(tmp_path)
    group = replace(group, beat_ids=("shot-1", "shot-2"))
    with pytest.raises(ValueError):
        freeze_selected_storyboard(group, media_root=tmp_path, project_id="project", episode=1)


@pytest.mark.parametrize("corrupt,race", [(False, False), (True, False), (False, True)])
@pytest.mark.parametrize("policy", ["legacy", "observe", "guard", "enforce"])
@pytest.mark.parametrize("visual_status", ["unavailable", "conflict"])
def test_api_freezes_selected_source_or_rejects_before_enqueue(tmp_path, monkeypatch, corrupt, race, policy, visual_status):
    from tests.test_api_narrative_groups import make_client
    from novelvideo.narrative_groups.service import load_groups, save_groups
    from novelvideo.media_capabilities.video.h3_reference_runtime import load_h3_reference_input_snapshot

    client, backend = make_client(monkeypatch, tmp_path, beat_count=1)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    group = load_groups(tmp_path, 1)[0]
    source = _source(tmp_path).model_copy(update={"project_id": "demo", "group_id": group.id})
    source = source.model_copy(update={"cells": (
        source.cells[0].model_copy(update={"shot_id": "beat-1"}),
    )})
    Image.new("RGB", (8, 8), "red").save(tmp_path / "wrong.png")
    group = replace(group, storyboard_contract_version=1, stages={**group.stages,
        "render": replace(group.stages["render"], status="completed",
            selected_storyboard_id=source.source_id,
            selected_storyboard_sources={source.batch_id: source.source_id},
            storyboard_sources=(source.model_dump(mode="json"),),
            cell_assets=({"beat_id": "beat-1", "path": str(tmp_path / "wrong.png")},))})
    save_groups(tmp_path, 1, [group])
    if race:
        from novelvideo.api.routes import narrative_groups as routes
        persist = routes.persist_h3_reference_input_snapshot

        def change_selection_after_persist(**kwargs):
            result = persist(**kwargs)
            current = load_groups(tmp_path, 1)[0]
            save_groups(tmp_path, 1, [replace(current, stages={**current.stages,
                "render": replace(current.stages["render"], selected_storyboard_id="b" * 64)})])
            return result

        monkeypatch.setattr(routes, "persist_h3_reference_input_snapshot", change_selection_after_persist)
    if corrupt:
        Image.new("RGB", (8, 8), "red").save(tmp_path / "cell.png")
    response = client.post(f"/api/v1/projects/demo/episodes/1/narrative-groups/{group.id}/video/generate",
        json={"mode": "i2va", "revision": 0, "plan_revision": group.video_plan.revision})
    if corrupt or race:
        assert response.status_code == (422 if corrupt else 409), response.text
        assert backend.calls == []
        assert load_groups(tmp_path, 1)[0].stages["video"].revision == 0
    else:
        assert response.status_code == 202, response.text
        payload = backend.calls[0][1]["payload"]
        assert payload["storyboard_contract_version"] == 1
        assert payload["storyboard_source_id"] == source.source_id
        loaded = load_h3_reference_input_snapshot(state_root=tmp_path,
            snapshot_id=payload["reference_snapshot_id"],
            expected_digest=payload["reference_snapshot_digest"],
            frame_sources=[str(tmp_path / "cell.png")])
        assert loaded.storyboard_binding.selection_id == source.source_id
        assert next(iter(loaded.frames.values())).sha256 == source.cells[0].sha256
        # Exercise the real runner through segment construction, then stop before
        # prompt/provider calls. Its live render alias still points at wrong.png.
        import asyncio
        from types import SimpleNamespace
        from novelvideo.task_backend.runners import narrative_group_video as runner

        class StopBeforeProvider(Exception):
            pass

        observed = []

        def build_segments(task, beats, render):
            observed.extend(render["cell_assets"])
            raise StopBeforeProvider

        monkeypatch.setattr(runner, "_workflow_definition_for_payload", lambda _: SimpleNamespace(
            id="runninghub:minimax-h3", adapter_key="minimax-h3", reference_policy=None))
        monkeypatch.setattr(runner, "_video_workflow_adapters", lambda: SimpleNamespace(resolve=lambda _: object()))
        monkeypatch.setattr(runner, "_load_canonical_beats", lambda *_: asyncio.sleep(0, result=[{"id": "beat-1"}]))
        monkeypatch.setattr(runner, "_build_segments", build_segments)
        ctx = SimpleNamespace(project_id="demo", output_dir=tmp_path, state_dir=tmp_path)
        with pytest.raises(StopBeforeProvider):
            asyncio.run(runner._execute({"episode": 1, "payload": payload}, ctx))
        assert observed[0]["path"] == str(tmp_path / "cell.png")
        # Continue through the real prompt boundary and verify a visual rejection
        # persists evidence without ever submitting video work.
        from tests.test_task_narrative_group_video_runner import _patch_test_workflow
        from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
        from novelvideo.media_capabilities.video.h3_storyboard_context import (
            StoryboardPromptBlocked, StoryboardPromptDecision,
        )
        _patch_test_workflow(monkeypatch, runner)
        payload = {**payload, "workflow_parameters": {**payload.get("workflow_parameters", {}),
                                                     "continuity_policy": policy}}
        monkeypatch.setattr(runner, "_workflow_definition_for_payload", lambda _: SimpleNamespace(
            id="runninghub:minimax-h3", adapter_key="minimax-h3", provider="runninghub",
            reference_policy=None, supported_modes=("i2va", "fl2va")))
        monkeypatch.setattr(runner, "_assert_stage_revision", lambda *_: None)
        monkeypatch.setattr(runner, "_load_active_director_plan", lambda *_: None)
        monkeypatch.setattr(runner, "_prepare_continuity", lambda **_: {})
        monkeypatch.setattr(runner, "_build_segments", lambda *_: [H3DirectorSegment(
            segment_id="beat-1", source_shot_ids=("beat-1",), beat_number=1,
            prompt="wait", duration_seconds=5, first_frame=str(tmp_path / "cell.png"))])
        captured = []
        provider_calls = []

        async def reject_visual(segments, beats, **kwargs):
            captured.append(kwargs.get("storyboard_binding"))
            raise StoryboardPromptBlocked(StoryboardPromptDecision(
                status=visual_status, plan=None,
                observations=(dict(image_label="image_001", framing="wide", orientation="back",
                                   spatial_relations="left of door"),) if visual_status == "conflict" else (),
                conflicts=(dict(image_label="image_001", shot_id="beat-1", field="framing",
                                observed="wide", required="medium at start"),)
                if visual_status == "conflict" else ()))

        async def provider(*args, **kwargs):
            provider_calls.append(True)
            raise AssertionError("provider must not be called")

        monkeypatch.setattr(runner, "_optimize_missing_prompts", reject_visual)
        monkeypatch.setattr(runner, "generate_h3_director_video", provider)
        with pytest.raises(StoryboardPromptBlocked):
            asyncio.run(runner._execute({"episode": 1, "payload": payload}, ctx))
        assert captured[0] == loaded.storyboard_binding
        assert provider_calls == []
        from novelvideo.media_capabilities.video.h3_timeline import load_h3_director_manifest
        manifests = list((tmp_path / "videos").rglob("*.manifest.json"))
        assert len(manifests) == 1
        rejected = load_h3_director_manifest(manifests[0])
        assert rejected.status == "quality_rejected"
        assert rejected.entries[0].input_summary["storyboard_blocker"]["transport_called"] is False
        # Even if the legacy replay checks accept a manifest, the visual-bound
        # task must re-enter visual planning when its evidence is text-only.
        from novelvideo.media_capabilities.video.h3_timeline import (
            save_h3_director_manifest, H3GenerationAttemptEvidence,
        )
        attempts = (H3GenerationAttemptEvidence(attempt=1, status="transport_failed",
                                               provider_task_id="old-provider-task"),)
        legacy = rejected.model_copy(update={"entries": tuple(
            entry.model_copy(update={"input_summary": {}, "attempts": attempts}) for entry in rejected.entries)})
        save_h3_director_manifest(manifests[0], legacy)
        monkeypatch.setattr(runner, "_has_complete_replay_snapshot", lambda *args, **kwargs: True)
        with pytest.raises(StoryboardPromptBlocked):
            asyncio.run(runner._execute({"episode": 1, "payload": payload}, ctx))
        assert len(captured) == 2
        assert provider_calls == []
        assert load_h3_director_manifest(manifests[0]).entries[0].attempts == attempts
