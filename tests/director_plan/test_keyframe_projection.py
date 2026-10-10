from copy import deepcopy

import pytest

from tests.director_plan.creative_fixtures import creative_shot
from novelvideo.narrative_groups.image_prompt import panel_description


def beat():
    value = creative_shot(duration=5).model_dump(mode="json")
    value.update(creative_contract_version=1, director_revision_id="r1", scene_name="hall")
    value["cinematography"]["subjects"] = [{"subject_id": "hero", "world_position": "door",
        "screen_position": "left", "facing": "toward window", "gaze_target": "door handle",
        "motion_path": "later runs outside"}]
    value["cinematography"]["exposure_priority"] = "face before bright window"
    return value


def test_static_projection_preserves_optics_gaze_and_exposure_without_future_action():
    result = panel_description(beat())
    for expected in ("35", "full frame", "toward window", "door handle", "face before bright window"):
        assert expected in result
    assert "later runs outside" not in result


@pytest.mark.parametrize('override', ['', 'Improve the natural skin texture'])
def test_legacy_keyframe_preserves_current_facing_gaze_even_with_override(override):
    value = beat()
    value.pop('creative_contract_version')
    value['image_prompt_override'] = override
    result = panel_description(value)
    for expected in ('toward window', 'door handle', 'face before bright window'):
        assert expected in result
    assert 'later runs outside' not in result
    if override:
        assert override in result


def test_batch_context_uses_neighbor_world_state_without_locking_screen_sides():
    from novelvideo.narrative_groups.image_prompt import adjacent_keyframe_context
    previous, current, following = (deepcopy(beat()) for _ in range(3))
    previous.update(id='a', visible_end_state='hero at the door')
    current.update(id='b')
    following.update(id='c', visible_start_state='partner looks toward door')
    context = adjacent_keyframe_context([previous, current, following], ['b'])
    assert 'hero at the door' in context
    assert 'partner looks toward door' in context
    assert 'reverse angle' in context
    assert 'later runs outside' not in context
    assert 'not additional panels' in context


def test_neighbor_context_uses_only_explicit_endpoint_blocking_and_same_scene():
    from novelvideo.narrative_groups.image_prompt import adjacent_keyframe_context
    previous, current, following = (deepcopy(beat()) for _ in range(3))
    previous.update(id='a', visible_end_state='at old door')
    current.update(id='b')
    following.update(id='c', scene_name='another location', visible_start_state='elsewhere')
    previous['creative']['end_cinematography'] = deepcopy(previous['cinematography'])
    previous['creative']['end_cinematography']['subjects'][0]['facing'] = 'toward stair'
    context = adjacent_keyframe_context([previous, current, following], ['b'])
    assert 'toward stair' in context
    assert 'elsewhere' not in context
    assert 'later runs outside' not in context
    previous['creative'].pop('end_cinematography')
    context = adjacent_keyframe_context([previous, current], ['b'])
    assert 'at old door' in context
    assert 'toward window' not in context


def test_override_cannot_bypass_adopted_creative_decisions():
    with pytest.raises(ValueError, match="creative_override_requires_revision"):
        panel_description({**beat(), "image_prompt_override": "move camera to opposite side"})


def test_grid_prompt_preflight_rejects_missing_adopted_facing_before_image_request():
    from novelvideo.task_backend.runners.narrative_group import _grid_prompt
    value = beat()
    del value['cinematography']['subjects'][0]['facing']
    with pytest.raises(ValueError, match='facing'):
        _grid_prompt({'beats': [value]})


def test_dependency_hash_ignores_later_motion_but_tracks_current_gaze():
    from novelvideo.director_plan.keyframes import project_keyframe
    original = beat()
    later = deepcopy(original)
    later["creative"]["motion"]["path"] = "move after the opening instant"
    later["action"] = "run later"
    assert project_keyframe(original).dependency_hash == project_keyframe(later).dependency_hash
    changed = deepcopy(original)
    changed["cinematography"]["subjects"][0]["gaze_target"] = "ceiling"
    assert project_keyframe(original).dependency_hash != project_keyframe(changed).dependency_hash


def test_end_frame_requires_explicit_end_direction_and_uses_it():
    from novelvideo.director_plan.keyframes import project_keyframe
    value = beat()
    value["creative"]["keyframe_anchor"] = "end"
    with pytest.raises(ValueError, match="end keyframe"):
        project_keyframe(value)
    value["creative"]["end_cinematography"] = deepcopy(value["cinematography"])
    value["creative"]["end_cinematography"]["subjects"][0]["gaze_target"] = "outside"
    value["creative"]["optics"]["end_focus_target"] = "outside"
    value["visible_end_state"] = "door open"
    result = project_keyframe(value)
    assert result.anchor == "end"
    assert result.facts["state"] == "door open"
    assert result.facts["optics"]["focus_target"] == "outside"
    assert result.facts["subjects"][0]["gaze_target"] == "outside"
