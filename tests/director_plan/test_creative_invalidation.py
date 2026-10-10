from dataclasses import replace

from novelvideo.director_plan.creative_invalidation import invalidate_creative_stages
from novelvideo.narrative_groups.models import GroupStageState
from tests.director_plan.creative_fixtures import creative_shot
from tests.director_plan.test_models import make_group, make_revision


def revision(**changes):
    shot = creative_shot(duration=5)
    data = shot.model_dump(mode="json")
    for key, value in changes.items():
        if key == "motion":
            data["creative"]["motion"]["path"] = value
        elif key == "focus":
            data["creative"]["optics"]["focus_target"] = value
        else:
            data[key] = value
    group = make_group(creative_contract_version=1, shots=(type(shot).model_validate(data),))
    return make_revision(creative_contract_version=1, groups=(group,))


def stages():
    return {name: GroupStageState(status="completed", revision=2, grid_asset="old.png", video_asset="old.mp4",
                                 revision_history=({"revision": 1, "grid_asset": "older.png"},))
            for name in ("sketch", "render", "video")}


def test_later_motion_preserves_images_and_history_but_invalidates_video():
    previous = stages()
    result = invalidate_creative_stages(revision(), revision(motion="push after opening"), "group-1", previous)
    assert result["sketch"] is previous["sketch"]
    assert result["render"] is previous["render"]
    assert result["video"].needs_regeneration
    assert result["video"].video_asset == "old.mp4"
    assert result["video"].revision_history == previous["video"].revision_history
    assert not previous["video"].needs_regeneration


def test_focus_change_marks_shared_images_and_video_stale():
    result = invalidate_creative_stages(revision(), revision(focus="keyhole"), "group-1", stages())
    assert all(stage.needs_regeneration for stage in result.values())
    assert all(stage.grid_asset == "old.png" for stage in result.values())


def test_duration_only_preserves_static_and_revision_only_preserves_all():
    old = revision()
    previous = stages()
    same = old.model_copy(update={"revision_id": "new-revision"})
    assert invalidate_creative_stages(old, same, "group-1", previous) == previous
    changed = invalidate_creative_stages(old, revision(duration_seconds=9), "group-1", previous)
    assert not changed["render"].needs_regeneration
    assert changed["video"].needs_regeneration


def test_missing_revision_or_changed_shot_identity_invalidates_all():
    for old, new in ((None, revision()), (revision(), revision(id="replacement"))):
        assert all(stage.needs_regeneration for stage in
                   invalidate_creative_stages(old, new, "group-1", stages()).values())


def test_existing_staleness_is_never_cleared():
    previous = stages()
    previous["render"] = replace(previous["render"], needs_regeneration=True, stale_reason="earlier_edit")
    result = invalidate_creative_stages(revision(), revision(), "group-1", previous)
    assert result["render"] == previous["render"]


def test_style_and_asset_changes_invalidate_shared_static_asset():
    old = revision()
    new_style = old.model_copy(update={"project_style_snapshot_id": "new-style"})
    new_asset = revision(asset_requirements=[{"kind": "prop", "entity_key": "new-lock"}])
    for new in (new_style, new_asset):
        assert all(state.needs_regeneration for state in
                   invalidate_creative_stages(old, new, "group-1", stages()).values())
