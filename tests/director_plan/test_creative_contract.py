import pytest
from pydantic import ValidationError

from novelvideo.director_plan.creative_contract import OpticsDecision
from novelvideo.director_plan.models import ShotPlan, SourceSpan
from novelvideo.director_plan.validation import validate_director_plan
from tests.director_plan.creative_fixtures import creative_shot
from tests.director_plan.test_models import make_group, make_revision


def test_optics_rejects_focal_length_without_sensor_basis():
    with pytest.raises(ValidationError, match="sensor"):
        OpticsDecision.model_validate(dict(
            purpose="Read the face", source={"kind": "director_choice", "reference_ids": ["span-1"]},
            mode="physical", focal_length_mm=50, subject_distance="two metres",
            depth_of_field="shallow", focus_target="eyes", focus_change="hold",
        ))


def test_2d_optics_requires_explicit_reason():
    data = dict(purpose="Flat composition", source={"kind": "director_choice", "reference_ids": ["span-1"]},
                mode="not_applicable", subject_distance="layer separation", depth_of_field="all layers readable",
                focus_target="letter", focus_change="hold")
    with pytest.raises(ValidationError, match="reason"):
        OpticsDecision.model_validate(data)
    assert OpticsDecision.model_validate({**data, "not_applicable_reason": "Drawn 2D layers"}).focal_length_mm is None


def test_new_contract_accepts_five_long_static_shots_and_roundtrips():
    shots = tuple(creative_shot(f"shot-{i}", f"shot-{i+1}" if i < 4 else None) for i in range(5))
    group = make_group(creative_contract_version=1, shots=shots)
    revision = make_revision(creative_contract_version=1, groups=(group,))
    assert type(revision).model_validate_json(revision.model_dump_json()) == revision
    spans = [SourceSpan(id="span-1", ordinal=1, scene="hallway", time="night", text="Door remains closed.")]
    assert validate_director_plan(revision, spans).passed


def test_creative_decisions_do_not_implicitly_upgrade_legacy_group():
    with pytest.raises(ValidationError):
        make_group(shots=(creative_shot(duration=5),))


@pytest.mark.parametrize("mutation,code", [
    (lambda c: c["lighting"].update(light_ids=["missing"]), "creative_light_reference_missing"),
    (lambda c: c["optics"].update(source={"kind": "script_fact", "reference_ids": ["missing"]}), "creative_source_mismatch"),
    (lambda c: c.update(keyframe_anchor="end"), "end keyframe requires"),
])
def test_invalid_cross_field_decisions_are_rejected(mutation, code):
    payload = creative_shot().model_dump(mode="json")
    mutation(payload["creative"])
    with pytest.raises(ValidationError, match=code):
        ShotPlan.model_validate(payload)


def test_handoff_to_wrong_next_shot_blocks_validation():
    group = make_group(creative_contract_version=1, shots=(creative_shot(next_shot_id="unknown"),))
    revision = make_revision(creative_contract_version=1, groups=(group,))
    assert "creative_handoff_mismatch" in {issue.code for issue in validate_director_plan(revision, ()).issues}


@pytest.mark.parametrize("duration", [float("inf"), float("nan"), 0])
def test_new_contract_still_requires_positive_finite_duration(duration):
    with pytest.raises(ValidationError):
        creative_shot(duration=duration)


def test_actor_performance_preserves_profile_and_event_timing():
    payload = creative_shot(duration=8).model_dump(mode="json")
    payload["asset_requirements"] = [{"kind": "character_identity", "entity_key": "listener"}]
    payload["cinematography"]["subjects"] = [{"subject_id": "listener", "world_position": "doorway", "screen_position": "left", "facing": "camera right", "gaze_target": "offscreen speaker", "motion_path": "hold"}]
    actor = {"subject_id": "listener", "master_profile_id": "listener-master", "master_profile_version": "2", "start_behavior": "fingers rest on door handle", "end_behavior": "fingers tighten on handle", "goal": "hide recognition", "obstacle": "speaker knows the secret", "tactics": "maintain eye contact", "visible_behavior": "jaw tightens after accusation", "gaze": "offscreen speaker", "contact": "right palm on handle", "held_objects": "none", "weight_and_inertia": "weight stays on rear foot", "beats": [{"at_seconds": 3, "event_anchor": "accusation ends", "behavior": "jaw tightens"}]}
    payload["creative"]["performance"].update(applicability="applicable", not_applicable_reason=None, characters=[actor])
    shot = ShotPlan.model_validate(payload)
    assert shot.creative.performance.characters[0].master_profile_version == "2"
    actor["beats"][0]["at_seconds"] = 9
    with pytest.raises(ValidationError, match="creative_beat_time_invalid"):
        ShotPlan.model_validate(payload)
