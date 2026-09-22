from copy import deepcopy

import pytest
from pydantic import ValidationError

from novelvideo.director_plan.models import ShotPlan, UpdateShot
from novelvideo.shot_continuity.builder import build_shot_continuity_contract
from novelvideo.shot_continuity.compiler import continuity_locks_for


def photography():
    return {
        "source": "director_plan",
        "source_ids": ["span-1"],
        "axis": "stairs toward lighthouse",
        "camera_side": "seaward side of stairs",
        "screen_direction": "lower left toward upper right",
        "subjects": [{
            "subject_id": "lin", "world_position": "below lighthouse",
            "screen_position": "left third", "facing": "uphill, away from camera",
            "gaze_target": "lighthouse lamp", "motion_path": "up the stairs",
        }],
        "lights": [{
            "light_id": "oil-lamp", "source_type": "practical",
            "world_position": "in lin's right hand", "direction": "upward from hand",
            "color_temperature": "warm amber", "relative_intensity": "key",
            "attachment": "lantern", "motivation": "lit oil lamp",
        }],
        "key_light_id": "oil-lamp", "shadow_direction": "away from lantern",
        "exposure_priority": "retain night and facial detail",
        "transition_intent": "hard cut matching upward gaze",
    }


def shot(**updates):
    return ShotPlan.model_validate({
        "id": "s1", "source_span_ids": ["span-1"], "subject": "lin",
        "action": "climb", "visible_start_state": "foot planted",
        "visible_end_state": "next step", "duration_seconds": 4,
        "cinematography": photography(), **updates,
    })


def contract(value):
    return build_shot_continuity_contract(
        value, scene_id="lighthouse", scene_state="night", predecessor=None,
        director_world=None, asset_evidence_by_entity={},
    )


def test_director_facts_survive_storage_and_contract_projection():
    value = shot()
    restored = ShotPlan.model_validate_json(value.model_dump_json())
    result = contract(restored)
    assert result.subjects[0].facing == "uphill, away from camera"
    assert result.subjects[0].gaze_target == "lighthouse lamp"
    assert result.subjects[0].screen_position == "left third"
    assert result.scene.camera_side == "seaward side of stairs"
    assert result.lighting.key_source == "oil-lamp"
    assert result.lighting.direction == "upward from hand"
    assert result.cinematography == restored.cinematography


def test_cinematography_stays_in_contract_without_a_second_global_narrative():
    source = contract(shot())
    assert continuity_locks_for((source,)) == ("subject lin keeps identity",)
    prompt = source.cinematography.prompt_facts()
    for fact in ("uphill, away from camera", "lighthouse lamp", "below lighthouse",
                 "warm amber", "in lin's right hand", "away from lantern",
                 "hard cut matching upward gaze"):
        assert fact in prompt


def test_old_shots_remain_readable_without_invented_director_facts():
    value = shot(cinematography=None)
    assert contract(value).cinematography is None


@pytest.mark.parametrize("field,value", [
    ("camera_side", "opposite side"), ("axis", "new axis"),
    ("key_light_id", "moon"),
])
def test_conflicting_directions_are_not_merged_into_one_continuous_segment(field, value):
    from novelvideo.director_plan.generation import _can_merge

    next_facts = photography()
    next_facts[field] = value
    if field == "key_light_id":
        next_facts["lights"].append({**next_facts["lights"][0], "light_id": "moon"})
    first = shot(continuous_with_next=True)
    second = shot(id="s2", cinematography=next_facts)
    assert not _can_merge([first], second, "lighthouse")


def test_consistent_director_facts_allow_continuous_pair():
    from novelvideo.director_plan.generation import _can_merge

    assert _can_merge([shot(continuous_with_next=True)], shot(id="s2"), "lighthouse")


@pytest.mark.parametrize("mutation", ["blank_facing", "missing_key", "duplicate_light"])
def test_invalid_photography_is_rejected(mutation):
    value = deepcopy(photography())
    if mutation == "blank_facing":
        value["subjects"][0]["facing"] = "  "
    elif mutation == "missing_key":
        value["key_light_id"] = "invented-light"
    else:
        value["lights"].append(deepcopy(value["lights"][0]))
    with pytest.raises(ValidationError):
        shot(cinematography=value)


def test_edit_command_accepts_versioned_director_facts():
    edit = UpdateShot.model_validate({"kind": "update_shot", "shot_id": "s1",
                                     "cinematography": photography()})
    assert edit.cinematography.key_light_id == "oil-lamp"


def test_edit_can_end_continuous_run_before_an_intentional_cut():
    from novelvideo.director_plan.editing import _update_shot
    from tests.shot_continuity.test_builder import _plan

    plan = _plan(shot(continuous_with_next=True))
    command = UpdateShot.model_validate({
        "kind": "update_shot", "shot_id": "s1", "continuous_with_next": False,
    })
    updated = _update_shot(plan.groups, command)[0].shots[0]
    assert updated.continuous_with_next is False
    assert updated.cinematography == plan.groups[0].shots[0].cinematography


def test_edit_preserves_nested_types():
    from novelvideo.director_plan.editing import _update_shot
    from tests.shot_continuity.test_builder import _plan

    plan = _plan(shot(cinematography=None))
    edit = UpdateShot.model_validate({"kind": "update_shot", "shot_id": "s1",
                                     "cinematography": photography()})
    updated = _update_shot(plan.groups, edit)[0].shots[0]
    assert updated.cinematography.key_light_id == "oil-lamp"


def test_reference_image_projection_contains_the_same_director_facts(monkeypatch, tmp_path):
    from novelvideo.narrative_groups.service import generation_beats_for_group
    from novelvideo.director_plan.store import DirectorPlanStore
    from tests.shot_continuity.test_builder import _plan

    plan = _plan(shot())
    monkeypatch.setattr(DirectorPlanStore, "load_active", lambda *args: plan)
    beat = generation_beats_for_group(tmp_path, 1, "group-1", ())[0]
    assert beat["cinematography"] == shot().cinematography.model_dump(mode="json")
    for text in ("uphill, away from camera", "warm amber", "in lin's right hand"):
        assert text in beat["visual_description"]


def test_planning_prompts_request_blocking_and_lighting():
    from novelvideo.director_plan.prompts import _EPISODE_AUTHORITY, _REPAIR_AUTHORITY

    for prompt in (_EPISODE_AUTHORITY, _REPAIR_AUTHORITY):
        for field in ("cinematography", "world_position", "key_light_id", "camera_side"):
            assert field in prompt


def test_production_preflight_rejects_missing_and_mismatched_blocking():
    from novelvideo.director_plan.cinematography import production_direction_errors
    assert "cinematography_missing" in production_direction_errors(shot(cinematography=None))
    value = shot(asset_requirements=[{"kind": "character_identity", "entity_key": "other"}])
    assert "blocking_subject_mismatch" in production_direction_errors(value)
    assert production_direction_errors(shot()) == ()


def test_production_preflight_rejects_fabricated_plan_sources():
    from novelvideo.director_plan.cinematography import production_direction_errors
    facts = photography()
    facts["source_ids"] = ["wrong-span"]
    assert "cinematography_source_mismatch" in production_direction_errors(shot(cinematography=facts))


def test_declared_axis_does_not_force_every_shot_into_3d_control():
    from novelvideo.shot_continuity.risk import signals_for_shot
    assert signals_for_shot(shot(), contract(shot())).exact_axis is False
