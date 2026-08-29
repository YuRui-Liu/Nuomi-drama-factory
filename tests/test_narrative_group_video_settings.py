from dataclasses import FrozenInstanceError, replace
import json

import pytest

from novelvideo.narrative_groups import models, service


DEFAULT_WORKFLOW_ID = "runninghub:minimax-h3"


def _beats():
    return [{"id": "beat-1"}, {"id": "beat-2"}]


def _save_group_with_video_stage(tmp_path, **stage_changes):
    group = service.group_beats(_beats())[0]
    stages = dict(group.stages)
    stages["video"] = replace(stages["video"], **stage_changes)
    group = replace(group, stages=stages)
    service.save_groups(tmp_path, 1, [group])
    return group


def test_video_settings_defaults_are_frozen_and_do_not_share_overrides():
    first = models.VideoSettings()
    second = models.VideoSettings()

    assert first.workflow_id == DEFAULT_WORKFLOW_ID
    assert first.revision == 0
    assert first.overrides == {}
    assert first.overrides is not second.overrides
    with pytest.raises(FrozenInstanceError):
        first.revision = 1


def test_old_sidecar_without_video_settings_loads_defaults(tmp_path):
    path = service.sidecar_path(tmp_path, 1)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "episode": 1,
                "groups": [
                    {
                        "id": "ng-01",
                        "ordinal": 1,
                        "beat_ids": ["beat-1"],
                        "layout": {"rows": 1, "columns": 1, "capacity": 1},
                        "cell_to_beat": [{"cell": 0, "beat_id": "beat-1"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    group = service.load_groups(tmp_path, 1)[0]

    assert group.video_settings == models.VideoSettings()
    assert group.to_dict()["video_settings"] == {
        "workflow_id": DEFAULT_WORKFLOW_ID,
        "revision": 0,
        "overrides": {},
    }


def test_video_settings_save_load_round_trip(tmp_path):
    group = replace(
        service.group_beats(_beats())[0],
        video_settings=models.VideoSettings(
            workflow_id="workflow-a",
            revision=4,
            overrides={"duration": "10", "aspect_ratio": "9:16"},
        ),
    )

    service.save_groups(tmp_path, 1, [group])
    restored = service.load_groups(tmp_path, 1)[0]

    assert restored.video_settings == group.video_settings
    assert restored.to_dict()["video_settings"] == {
        "workflow_id": "workflow-a",
        "revision": 4,
        "overrides": {"duration": "10", "aspect_ratio": "9:16"},
    }


def test_update_video_settings_advances_revision_and_saves_only_differences(tmp_path):
    service.save_groups(tmp_path, 1, service.group_beats(_beats()))

    updated = service.update_video_settings(
        tmp_path,
        1,
        "ng-01",
        expected_revision=0,
        workflow_id="workflow-a",
        overrides={"duration": "10", "quality": "high"},
        project_defaults={"duration": "5", "quality": "high"},
    )

    assert updated.video_settings == models.VideoSettings(
        workflow_id="workflow-a",
        revision=1,
        overrides={"duration": "10"},
    )
    assert service.load_groups(tmp_path, 1)[0].video_settings == updated.video_settings


def test_update_video_settings_missing_group_raises_key_error(tmp_path):
    service.save_groups(tmp_path, 1, service.group_beats(_beats()))

    with pytest.raises(KeyError, match="missing"):
        service.update_video_settings(
            tmp_path,
            1,
            "missing",
            expected_revision=0,
            workflow_id="workflow-a",
            overrides={},
            project_defaults={},
        )


def test_update_video_settings_rejects_stale_revision_without_writing(tmp_path):
    service.save_groups(tmp_path, 1, service.group_beats(_beats()))
    path = service.sidecar_path(tmp_path, 1)
    before = path.read_bytes()

    with pytest.raises(RuntimeError, match="stale"):
        service.update_video_settings(
            tmp_path,
            1,
            "ng-01",
            expected_revision=9,
            workflow_id="workflow-a",
            overrides={"duration": "10"},
            project_defaults={},
        )

    assert path.read_bytes() == before


@pytest.mark.parametrize("status", ["queued", "running"])
def test_update_video_settings_rejects_active_video_stage(tmp_path, status):
    _save_group_with_video_stage(tmp_path, status=status, revision=3)
    before = service.load_groups(tmp_path, 1)[0]

    with pytest.raises(RuntimeError, match=status):
        service.update_video_settings(
            tmp_path,
            1,
            "ng-01",
            expected_revision=0,
            workflow_id="workflow-a",
            overrides={},
            project_defaults={},
        )

    assert service.load_groups(tmp_path, 1)[0] == before


def test_update_video_settings_restores_project_defaults_by_removing_overrides(tmp_path):
    group = replace(
        service.group_beats(_beats())[0],
        video_settings=models.VideoSettings(
            workflow_id="workflow-a",
            revision=2,
            overrides={"duration": "10", "quality": "high"},
        ),
    )
    service.save_groups(tmp_path, 1, [group])

    updated = service.update_video_settings(
        tmp_path,
        1,
        "ng-01",
        expected_revision=2,
        workflow_id="workflow-a",
        overrides={"duration": "5", "quality": "high"},
        project_defaults={"duration": "5", "quality": "high"},
    )

    assert updated.video_settings.revision == 3
    assert updated.video_settings.overrides == {}


def test_switching_workflow_does_not_reuse_previous_overrides(tmp_path):
    group = replace(
        service.group_beats(_beats())[0],
        video_settings=models.VideoSettings(
            workflow_id="workflow-a",
            revision=1,
            overrides={"duration": "10", "quality": "high"},
        ),
    )
    service.save_groups(tmp_path, 1, [group])

    updated = service.update_video_settings(
        tmp_path,
        1,
        "ng-01",
        expected_revision=1,
        workflow_id="workflow-b",
        overrides={"seed": "42"},
        project_defaults={"seed": "0"},
    )

    assert updated.video_settings == models.VideoSettings(
        workflow_id="workflow-b", revision=2, overrides={"seed": "42"}
    )


def test_settings_update_preserves_existing_video_and_manifest_without_rewriting(tmp_path):
    manifest = tmp_path / "existing-manifest.json"
    manifest.write_text('{"entries": [{"keep": true}]}', encoding="utf-8")
    _save_group_with_video_stage(
        tmp_path,
        status="completed",
        revision=7,
        video_asset="existing-video.mp4",
        manifest_asset=str(manifest),
        error="historical warning",
        created_at="2026-08-28T12:00:00+00:00",
    )
    original_stage = service.load_groups(tmp_path, 1)[0].stages["video"]
    manifest_before = manifest.read_bytes()

    updated = service.update_video_settings(
        tmp_path,
        1,
        "ng-01",
        expected_revision=0,
        workflow_id="workflow-a",
        overrides={"duration": "10"},
        project_defaults={},
    )

    assert updated.stages["video"] == original_stage
    assert service.load_groups(tmp_path, 1)[0].stages["video"] == original_stage
    assert manifest.read_bytes() == manifest_before
    assert not (tmp_path / "existing-video.mp4").exists()


def test_update_video_settings_copies_inputs_without_mutating_them(tmp_path):
    service.save_groups(tmp_path, 1, service.group_beats(_beats()))
    overrides = {"duration": "10", "quality": "high"}
    defaults = {"duration": "5", "quality": "high"}
    overrides_before = dict(overrides)
    defaults_before = dict(defaults)

    updated = service.update_video_settings(
        tmp_path,
        1,
        "ng-01",
        expected_revision=0,
        workflow_id="workflow-a",
        overrides=overrides,
        project_defaults=defaults,
    )
    assert overrides == overrides_before
    assert defaults == defaults_before
    overrides["duration"] = "99"
    defaults["duration"] = "99"

    assert overrides_before == {"duration": "10", "quality": "high"}
    assert defaults_before == {"duration": "5", "quality": "high"}
    assert updated.video_settings.overrides == {"duration": "10"}
    assert service.load_groups(tmp_path, 1)[0].video_settings.overrides == {
        "duration": "10"
    }


@pytest.mark.parametrize(
    ("workflow_id", "overrides", "project_defaults"),
    [
        (123, {}, {}),
        ("workflow-a", {1: "10"}, {}),
        ("workflow-a", {"duration": 10}, {}),
        ("workflow-a", {}, {1: "10"}),
        ("workflow-a", {}, {"duration": 10}),
    ],
)
def test_update_video_settings_rejects_non_string_workflow_keys_and_values(
    tmp_path, workflow_id, overrides, project_defaults
):
    service.save_groups(tmp_path, 1, service.group_beats(_beats()))

    with pytest.raises((TypeError, ValueError)):
        service.update_video_settings(
            tmp_path,
            1,
            "ng-01",
            expected_revision=0,
            workflow_id=workflow_id,
            overrides=overrides,
            project_defaults=project_defaults,
        )


def test_ensure_and_rebuild_preserve_video_settings_for_unchanged_group(tmp_path):
    group = replace(
        service.group_beats(_beats())[0],
        video_settings=models.VideoSettings(
            workflow_id="workflow-a",
            revision=3,
            overrides={"duration": "10"},
        ),
    )
    service.save_groups(tmp_path, 1, [group])

    ensured = service.ensure_groups(tmp_path, 1, _beats())
    rebuilt = service.rebuild_groups(tmp_path, 1, _beats())

    assert ensured[0].video_settings == group.video_settings
    assert rebuilt[0].video_settings == group.video_settings
    assert service.load_groups(tmp_path, 1)[0].video_settings == group.video_settings
