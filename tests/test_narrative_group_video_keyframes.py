from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from novelvideo.director_plan.models import (
    DirectorPlanRevision,
    NarrativeGroupPlan,
    ShotPlan,
    ValidationReport,
)
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.media_capabilities.video.h3_reference_runtime import (
    load_h3_reference_input_snapshot,
)


def _activate_three_shot_plan(project_dir: Path) -> str:
    shots = tuple(
        ShotPlan(
            id=f"beat-{index}",
            source_span_ids=(f"span-{index}",),
            subject="protagonist",
            action=f"performs action {index}",
            visible_start_state=f"start {index}",
            visible_end_state=f"end {index}",
            duration_seconds=4,
        )
        for index in range(1, 4)
    )
    group = NarrativeGroupPlan(
        id="ng-01",
        ordinal=1,
        source_span_ids=("span-1", "span-2", "span-3"),
        scene_anchor="hallway",
        time_anchor="night",
        objective="reach the door",
        visible_turn="the door opens",
        relation_to_previous="single",
        shots=shots,
    )
    revision = DirectorPlanRevision(
        revision_id="rev-keyframes-active",
        episode=1,
        status="review_required",
        source_script_hash="sha256:test-source",
        director_model="external:codex",
        prompt_version="v3-keyframe-instant",
        project_style_snapshot_id="style-1",
        groups=(group,),
        validation_report=ValidationReport(passed=True),
        created_at=datetime(2026, 10, 25, tzinfo=timezone.utc),
    )
    store = DirectorPlanStore(project_dir)
    store.save(revision)
    store.activate(1, revision.revision_id)
    return revision.revision_id


def _image(path: Path, color: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (24, 24), color).save(path, format="PNG")
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _request_bindings(project_dir: Path):
    bindings = []
    for index in range(1, 4):
        first = Path("frames") / f"shot-{index}-first.png"
        last = Path("frames") / f"shot-{index}-last.png"
        first_sha = _image(project_dir / first, f"#{index:02x}3050")
        last_sha = _image(project_dir / last, f"#50{index:02x}30")
        bindings.append({
            "shot_id": f"beat-{index}",
            "first_frame": {"path": first.as_posix(), "sha256": first_sha},
            "last_frame": {"path": last.as_posix(), "sha256": last_sha},
        })
    return bindings


def test_keyframe_route_uses_director_and_binding_revision_cas(monkeypatch, tmp_path):
    from tests.test_api_narrative_groups import make_client

    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    director_revision_id = _activate_three_shot_plan(tmp_path)
    base = "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/keyframes"

    initial = client.get(base)
    assert initial.status_code == 200, initial.text
    assert initial.json()["data"]["revision"] == 0
    assert initial.json()["data"]["director_revision_id"] == director_revision_id
    assert initial.json()["data"]["shot_ids"] == ["beat-1", "beat-2", "beat-3"]

    request = {
        "expected_revision": 0,
        "expected_director_revision_id": director_revision_id,
        "bindings": _request_bindings(tmp_path),
    }
    saved = client.put(base, json=request)
    stale = client.put(base, json=request)

    assert saved.status_code == 200, saved.text
    data = saved.json()["data"]
    assert data["revision"] == 1
    assert data["director_revision_id"] == director_revision_id
    assert data["binding_sha256"]
    assert [entry["shot_id"] for entry in data["bindings"]] == [
        "beat-1", "beat-2", "beat-3"
    ]
    assert stale.status_code == 409


def test_keyframe_route_rejects_incomplete_or_unsafe_bindings(monkeypatch, tmp_path):
    from tests.test_api_narrative_groups import make_client

    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    director_revision_id = _activate_three_shot_plan(tmp_path)
    endpoint = "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/keyframes"
    bindings = _request_bindings(tmp_path)
    base = {
        "expected_revision": 0,
        "expected_director_revision_id": director_revision_id,
    }

    incomplete = client.put(endpoint, json={**base, "bindings": bindings[:-1]})
    unsafe = client.put(endpoint, json={
        **base,
        "bindings": [
            {**bindings[0], "first_frame": {"path": "../outside.png", "sha256": "0" * 64}},
            *bindings[1:],
        ],
    })
    stale_plan = client.put(endpoint, json={
        **base,
        "expected_director_revision_id": "rev-obsolete",
        "bindings": bindings,
    })

    assert incomplete.status_code == 422
    assert unsafe.status_code == 422
    assert stale_plan.status_code == 409


def test_keyframed_video_plan_keeps_shots_independent_and_uses_same_shot_endpoints():
    from novelvideo.task_backend.runners.narrative_group_video import _build_segments

    beats = [
        {"id": f"shot-{index}", "duration_seconds": 4, "visual_description": f"shot {index}"}
        for index in range(1, 4)
    ]
    saved = {
        "beat_ids": [beat["id"] for beat in beats],
        "cell_assets": [
            {"beat_id": beat["id"], "path": f"storyboard-{index}.png"}
            for index, beat in enumerate(beats, start=1)
        ],
        "video_plan": {"units": [
            {"beat_ids": ["shot-1", "shot-2"], "mode": "fl2va"},
            {"beat_ids": ["shot-3"], "mode": "i2va"},
        ]},
        "video_keyframes": {
            beat["id"]: {
                "first_frame": f"{beat['id']}-first.png",
                "last_frame": f"{beat['id']}-last.png",
            }
            for beat in beats
        },
    }

    segments = _build_segments({"mode": "auto"}, beats, saved)

    assert [segment.source_shot_ids for segment in segments] == [
        ("shot-1",), ("shot-2",), ("shot-3",)
    ]
    assert [(segment.first_frame, segment.last_frame) for segment in segments] == [
        ("shot-1-first.png", "shot-1-last.png"),
        ("shot-2-first.png", "shot-2-last.png"),
        ("shot-3-first.png", "shot-3-last.png"),
    ]


def test_bound_endpoints_do_not_add_selected_previs_cells_as_extra_conditioning(
    monkeypatch, tmp_path
):
    import novelvideo.task_backend.runners.narrative_group_video as runner

    def unexpected_storyboard_conditioning(*_args, **_kwargs):
        raise AssertionError("pre-vis cells must not be image conditioning")

    monkeypatch.setattr(
        runner, "build_storyboard_prompt_images", unexpected_storyboard_conditioning
    )
    images = runner._storyboard_conditioning_images(
        object(), None, [], media_root=tmp_path, reference_only=False,
        keyframe_endpoints_bound=True,
    )

    assert images == ()


def test_selected_storyboard_snapshot_also_loads_bound_endpoints(tmp_path):
    from types import SimpleNamespace

    from novelvideo.media_capabilities.video.h3_reference_runtime import (
        freeze_h3_reference_frames,
        persist_h3_reference_input_snapshot,
    )
    from novelvideo.narrative_groups.storyboard_binding import (
        freeze_selected_storyboard,
        load_queued_storyboard,
    )
    from tests.test_storyboard_snapshot_binding import selected_group

    group, _ = selected_group(tmp_path)
    binding, storyboard_frames = freeze_selected_storyboard(
        group, media_root=tmp_path, project_id="project", episode=1
    )
    endpoint = tmp_path / "frames" / "terminal.png"
    _image(endpoint, "#224466")
    endpoint_frame = freeze_h3_reference_frames(
        [SimpleNamespace(first_frame=str(endpoint), last_frame=None)], project_root=tmp_path
    )
    frozen = {**dict(storyboard_frames), **dict(endpoint_frame)}
    keyframe_binding = {"revision": 1, "binding_sha256": "a" * 64}
    persisted = persist_h3_reference_input_snapshot(
        state_root=tmp_path / "state",
        references=(),
        frames=frozen,
        reference_revision=0,
        reference_limit=0,
        provider_workflow_id="workflow",
        storyboard_binding=binding,
        video_keyframe_binding=keyframe_binding,
    )

    loaded_binding, snapshot = load_queued_storyboard(
        {
            "storyboard_contract_version": 1,
            "storyboard_source_id": binding.selection_id,
            "storyboard_binding": binding.model_dump(mode="json"),
            "reference_snapshot_id": persisted.snapshot_id,
            "reference_snapshot_digest": persisted.digest,
        },
        media_root=tmp_path,
        state_root=tmp_path / "state",
        project_id="project",
        episode=1,
        group_id=group.id,
        extra_frame_sources=(str(endpoint),),
    )

    assert loaded_binding == binding
    assert snapshot.video_keyframe_binding == keyframe_binding
    assert snapshot.frames[str(endpoint)].sha256 == endpoint_frame[str(endpoint)].sha256


def test_group_video_enqueue_freezes_bound_frames_in_immutable_snapshot(
    monkeypatch, tmp_path
):
    from novelvideo.narrative_groups.service import advance_revision, record_stage_result
    from tests.test_api_narrative_groups import make_client

    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    director_revision_id = _activate_three_shot_plan(tmp_path)
    bindings = _request_bindings(tmp_path)
    endpoint = "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/keyframes"
    saved = client.put(endpoint, json={
        "expected_revision": 0,
        "expected_director_revision_id": director_revision_id,
        "bindings": bindings,
    })
    assert saved.status_code == 200, saved.text

    cells = []
    for index in range(1, 4):
        path = tmp_path / "frames" / f"render-{index}.png"
        digest = _image(path, f"#{index:02x}3050")
        cells.append({"beat_id": f"beat-{index}", "path": str(path), "sha256": digest})
    advance_revision(tmp_path, 1, "ng-01", "render")
    record_stage_result(
        tmp_path,
        1,
        "ng-01",
        "render",
        expected_revision=1,
        status="completed",
        cell_assets=cells,
    )

    generated = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate",
        json={"model": "runninghub:minimax-h3", "mode": "auto", "revision": 0, "plan_revision": 1},
    )

    assert generated.status_code == 202, generated.text
    payload = backend.calls[0][1]["payload"]
    frozen_binding = payload["video_keyframe_binding"]
    assert frozen_binding["revision"] == 1
    assert frozen_binding["binding_sha256"] == saved.json()["data"]["binding_sha256"]
    frame_sources = [
        str((tmp_path / frame["path"]).resolve())
        for binding in bindings
        for frame in (binding["first_frame"], binding["last_frame"])
    ]
    snapshot = load_h3_reference_input_snapshot(
        state_root=tmp_path,
        snapshot_id=payload["reference_snapshot_id"],
        expected_digest=payload["reference_snapshot_digest"],
        frame_sources=frame_sources,
    )
    for binding in bindings:
        for endpoint_frame in (binding["first_frame"], binding["last_frame"]):
            source = str((tmp_path / endpoint_frame["path"]).resolve())
            assert snapshot.frames[source].sha256 == endpoint_frame["sha256"]
