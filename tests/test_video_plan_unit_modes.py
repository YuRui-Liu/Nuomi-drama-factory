import pytest

from novelvideo.api.routes.narrative_groups import NarrativeGroupVideoPlanUnitRequest
from novelvideo.narrative_groups.service import (
    ensure_groups, load_groups, update_video_plan,
)
from novelvideo.task_backend.runners.narrative_group_video import _build_planned_segments


def _beats():
    return [
        {"id": "shot-1", "duration_seconds": 3.0, "dialogue": "Wait.", "speaker": "A"},
        {"id": "shot-2", "duration_seconds": 3.5, "dialogue": "Ready.", "speaker": "B"},
        {"id": "shot-3", "duration_seconds": 4.0},
    ]


def test_api_accepts_explicit_paired_i2va():
    request = NarrativeGroupVideoPlanUnitRequest(beat_ids=["shot-1", "shot-2"], mode="i2va")
    assert request.model_dump()["mode"] == "i2va"


def test_api_round_trip_preserves_paired_i2va(monkeypatch, tmp_path):
    from tests.test_api_narrative_groups import make_client

    client, _ = make_client(monkeypatch, tmp_path)
    endpoint = "/api/v1/projects/demo/episodes/1/narrative-groups"
    group = client.get(endpoint).json()["data"][0]
    units = [{"beat_ids": unit["beat_ids"], "mode": "i2va"}
             for unit in group["video_plan"]["units"]]
    response = client.put(
        f"{endpoint}/{group['id']}/video/plan",
        json={"expected_revision": group["video_plan"]["revision"], "units": units},
    )
    assert response.status_code == 200
    persisted = client.get(endpoint).json()["data"][0]["video_plan"]
    assert persisted == response.json()["data"]["video_plan"]
    assert persisted["units"][0]["beat_ids"] == ["beat-1", "beat-2"]
    assert all(unit["mode"] == "i2va" for unit in persisted["units"])


def test_paired_i2va_plan_mode_survives_reload(tmp_path):
    beats = _beats()
    group = ensure_groups(tmp_path, 1, beats)[0]
    updated = update_video_plan(
        tmp_path, 1, group.id, beats, expected_revision=group.video_plan.revision,
        units=[{"beat_ids": ["shot-1", "shot-2"], "mode": "i2va"}, {"beat_ids": ["shot-3"]}],
    )
    assert updated.video_plan.units[0].mode == "i2va"
    assert updated.video_plan.units[0].duration_seconds == 6.5
    assert updated.video_plan.units[0].beat_ids == ("shot-1", "shot-2")
    assert load_groups(tmp_path, 1)[0].video_plan == updated.video_plan


@pytest.mark.parametrize("mode", ["fl2va", "invalid"])
def test_service_rejects_invalid_unit_mode(tmp_path, mode):
    beats = _beats()[:1]
    group = ensure_groups(tmp_path, 1, beats)[0]
    with pytest.raises(ValueError, match="mode|fl2va"):
        update_video_plan(tmp_path, 1, group.id, beats,
                          expected_revision=group.video_plan.revision,
                          units=[{"beat_ids": ["shot-1"], "mode": mode}])


def test_auto_paired_i2va_preserves_coverage_duration_dialogue_without_last_frame():
    beats = _beats()
    segments = _build_planned_segments(
        "auto", [{"beat_ids": ["shot-1", "shot-2"], "mode": "i2va"}],
        {beat["id"]: beat for beat in beats}, {"shot-1": {"first_frame": "first.png"}},
    )
    assert len(segments) == 1
    segment = segments[0]
    assert segment.source_shot_ids == ("shot-1", "shot-2")
    assert segment.duration_seconds == 6.5
    assert segment.first_frame == "first.png"
    assert segment.last_frame is None
    assert [line.text for line in segment.dialogue_lines] == ["Wait.", "Ready."]


@pytest.mark.parametrize("global_mode,unit_mode,count,last", [
    ("i2va", "fl2va", 2, None),
    ("fl2va", "i2va", 1, "last.png"),
    ("auto", None, 1, "last.png"),
])
def test_global_modes_and_legacy_auto_remain_compatible(global_mode, unit_mode, count, last):
    beats = _beats()
    segments = _build_planned_segments(
        global_mode, [{"beat_ids": ["shot-1", "shot-2"], "mode": unit_mode}],
        {beat["id"]: beat for beat in beats},
        {"shot-1": {"first_frame": "first.png"}, "shot-2": {"first_frame": "last.png"}},
    )
    assert len(segments) == count
    assert segments[0].last_frame == last
