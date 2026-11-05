import pytest
from pydantic import ValidationError

from novelvideo.director_plan.workspace import DirectorWorkspaceStore, SceneBlocking


def snapshot():
    return {
        "schemaVersion": 1, "savedAt": 123,
        "actors": [{"id": "hero", "label": "Hero", "color": "#ffffff",
                    "position": [1, 0, -2], "yawDeg": 45, "scale": [1, 1, 1]}],
        "props": [], "stagings": [],
        "outputCamera": {"azim": 20, "elev": 10, "distance": 5,
                         "focalPoint": [1, 1, 0], "fov": 45},
        "lights": [{"id": "key", "label": "Key", "color": "#ffffff",
                    "intensity": 2, "position": [2, 3, 4], "direction": [0, -1, 0],
                    "enabled": True}],
    }


def test_stage_snapshot_roundtrips_in_shared_scene_store(tmp_path):
    store = DirectorWorkspaceStore(tmp_path)
    saved = store.save_scene_blocking(
        1, "scene-1", base_version=0, semantic_revision_id=None, group_ids=(),
        blocking={"label": "Hall", "camera": None, "stage_snapshot": snapshot()},
    )
    restored = store.get_scene_blocking(1, "scene-1")["current"]
    assert restored["blocking"]["stage_snapshot"]["outputCamera"]["fov"] == 45
    assert restored["blocking"]["stage_snapshot"]["actors"][0]["position"] == [1, 0, -2]
    assert saved["revision_id"] == restored["revision_id"]


@pytest.mark.parametrize("field,value", [("fov", 180), ("distance", -1), ("azim", float("nan"))])
def test_stage_snapshot_rejects_invalid_camera(field, value):
    data = snapshot()
    data["outputCamera"][field] = value
    with pytest.raises(ValidationError):
        SceneBlocking.model_validate({"label": "Hall", "camera": None, "stage_snapshot": data})


def test_stage_snapshot_rejects_duplicate_entity_ids():
    data = snapshot()
    data["actors"].append(data["actors"][0].copy())
    with pytest.raises(ValidationError, match="unique"):
        SceneBlocking.model_validate({"label": "Hall", "camera": None, "stage_snapshot": data})


@pytest.mark.parametrize("x,azim,expected", [(-2, 0, "screen-left"), (2, 0, "screen-right"), (-2, 180, "screen-right"), (2, 180, "screen-left")])
def test_camera_projection_changes_with_actor_and_reverse_camera(x, azim, expected):
    from novelvideo.director_plan.stage_snapshot import StageSnapshot, screen_position
    data = snapshot()
    data["actors"][0]["position"] = [x, 0, 0]
    data["outputCamera"].update(azim=azim, focalPoint=[0, 0, 0])
    stage = StageSnapshot.model_validate(data)
    assert expected in screen_position(stage.actors[0], stage)


@pytest.mark.parametrize("anchor", ["start", "end"])
def test_stage_compile_updates_selected_creative_camera_contract(anchor):
    from tests.director_plan.creative_fixtures import creative_shot
    from tests.director_plan.test_models import make_group, make_revision
    from novelvideo.director_plan.models import ShotPlan, SourceSpan
    from novelvideo.director_plan.workspace import compile_scene_blocking_candidate
    from novelvideo.director_plan.keyframes import project_keyframe

    payload = creative_shot().model_dump(mode="json")
    payload["creative"].update(keyframe_anchor=anchor, end_cinematography=payload["cinematography"])
    payload["creative"]["optics"]["end_focus_target"] = "door"
    shot = ShotPlan.model_validate(payload)
    revision = make_revision(status="active", creative_contract_version=1, groups=(make_group(creative_contract_version=1, shots=(shot,)),))
    stage = snapshot()
    stage["actors"] = []
    stage["lights"][0]["id"] = "lamp"
    reference = {"scene_id": "hallway", "group_id": "group-1", "director_revision_id": revision.revision_id,
                 "revision_id": "blocking-1", "version": 1, "semantic_revision_id": None,
                 "blocking": {"label": "Hall", "camera": None, "stage_snapshot": stage}}
    result = compile_scene_blocking_candidate(revision, [reference], source_spans=(SourceSpan(id="span-1", ordinal=1, scene="hallway", time="night", text="door closed"),))
    updated = result.groups[0].shots[0]
    projected = project_keyframe({**updated.model_dump(mode="json"), "creative_contract_version": 1})
    assert projected.facts["optics"]["mode"] == "field_of_view"
    assert projected.facts["optics"]["field_of_view_degrees"] == 45
    assert "focal_length_mm" not in projected.facts["optics"]
    assert "fov=45" in projected.facts["camera_side"]
    assert "xyz=(2, 3, 4)" in projected.facts["lights"][0]["world_position"]
    if anchor == "start":
        assert "3D" in projected.facts["camera_position"]["position"]
        assert projected.facts["camera_position"]["axis_side"] != "south"
    assert projected.facts["composition"]["layout"] != "centered door"
    assert projected.facts["lighting"]["color"] == "lamp #ffffff"
