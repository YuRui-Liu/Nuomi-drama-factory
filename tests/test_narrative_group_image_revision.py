import pytest

from novelvideo.narrative_groups.service import (
    advance_revision,
    load_groups,
    record_stage_result,
)
from tests.test_api_narrative_groups import (
    activate_director_plan,
    install_empty_planned_snapshot,
    make_client,
)


@pytest.mark.parametrize("stage", ["sketch", "render"])
@pytest.mark.parametrize("outcome,action", [
    ("completed", "generate"), ("failed", "generate"), ("completed", "split"),
    ("queued", "generate"), ("running", "generate"),
])
def test_image_generation_revisions_preserve_retries_and_split(
    monkeypatch, tmp_path, stage, outcome, action,
):
    client, backend = make_client(monkeypatch, tmp_path)
    planned = install_empty_planned_snapshot(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    activate_director_plan(tmp_path, group_id="ng-01", shot_id="beat-1")
    base = f"/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/{stage}"
    body = {**planned, "allow_unconstrained": True}
    response = client.post(f"{base}/generate", json=body)
    assert response.status_code == 202
    record_stage_result(
        tmp_path, 1, "ng-01", stage, expected_revision=1, status=outcome,
        grid_asset="paid-grid.png", cell_assets=[{"path": "paid-cell.png"}],
    )
    if stage == "sketch":
        advance_revision(tmp_path, 1, "ng-01", "render")
        record_stage_result(
            tmp_path, 1, "ng-01", "render", expected_revision=1,
            status="completed", grid_asset="render.png", source_sketch_revision=1,
        )
    advance_revision(tmp_path, 1, "ng-01", "video")
    record_stage_result(tmp_path, 1, "ng-01", "video", expected_revision=1,
                        status="completed", video_asset="paid-video.mp4")
    response = client.post(f"{base}/{action}", json=body)
    assert response.status_code == 202, response.text
    fresh = outcome == "completed" and action == "generate"
    expected_revision = 2 if fresh else 1
    assert response.json()["data"]["metadata"]["revision"] == expected_revision
    assert backend.calls[-1][1]["payload"]["revision"] == expected_revision
    assert backend.calls[-1][1]["payload"]["split_only"] == (action == "split")
    group = load_groups(tmp_path, 1)[0]
    state = group.stages[stage]
    assert state.grid_asset == ("" if fresh else "paid-grid.png")
    if fresh:
        assert state.revision_history[-1]["grid_asset"] == "paid-grid.png"
    assert group.stages["video"].needs_regeneration == fresh
    assert group.stages["video"].video_asset == "paid-video.mp4"
    if stage == "sketch":
        assert group.stages["render"].needs_regeneration == fresh


@pytest.mark.parametrize("stage", ["video", "render"])
@pytest.mark.parametrize("historical_source,current_source,stale", [
    ("a" * 64, "b" * 64, True),
    ("", "b" * 64, True),
    ("a" * 64, "", True),
    ("a" * 64, "a" * 64, False),
    ("", "", False),
])
def test_rollback_preserves_storyboard_video_freshness(
    tmp_path, stage, historical_source, current_source, stale,
):
    from novelvideo.narrative_groups.models import GridLayout, GroupStageState, NarrativeGroup
    from novelvideo.narrative_groups.service import rollback_stage_revision, save_groups

    history = {
        "revision": 1, "status": "completed",
        "selected_storyboard_id": historical_source if stage == "render" else "",
        "source_storyboard_id": historical_source if stage == "video" else "",
        "video_asset": "old-video.mp4" if stage == "video" else "",
    }
    render = GroupStageState(status="completed", revision=2,
        selected_storyboard_id=current_source,
        revision_history=(history,) if stage == "render" else ())
    video = GroupStageState(status="completed", revision=2,
        source_storyboard_id=current_source, video_asset="current-video.mp4",
        revision_history=(history,) if stage == "video" else ())
    group = NarrativeGroup(id="group", ordinal=1, beat_ids=("shot",),
        layout=GridLayout(1, 1, 1), cell_to_beat=(),
        stages={"render": render, "video": video})
    save_groups(tmp_path, 1, [group])

    result = rollback_stage_revision(tmp_path, 1, "group", stage, revision=1)

    state = result.stages["video"]
    assert state.needs_regeneration is stale
    assert state.stale_reason == ("storyboard_source_changed" if stale else "")
    assert state.video_asset == ("old-video.mp4" if stage == "video" else "current-video.mp4")
    assert load_groups(tmp_path, 1)[0].stages["video"] == state
