"""Explicit no-person establishing shot shared by contract integration tests."""
from novelvideo.director_plan.models import ShotPlan


def creative_shot(shot_id="shot-1", next_shot_id=None, duration=20):
    base = {"purpose": "Let the audience inspect the locked doorway", "source": {"kind": "director_choice", "reference_ids": ["span-1"]}}
    def decision(**values):
        return {**base, **values}
    return ShotPlan.model_validate({
        "id": shot_id, "source_span_ids": ["span-1"], "subject": "door",
        "action": "hold on door while wind sounds", "visible_start_state": "door closed",
        "visible_end_state": "door closed", "duration_seconds": duration,
        "cinematography": {
            "source": "director_plan", "source_ids": ["span-1"], "axis": "hallway",
            "camera_side": "south", "screen_direction": "no travel", "subjects": [],
            "lights": [{"light_id": "lamp", "source_type": "practical", "world_position": "above door",
                        "direction": "down", "color_temperature": "warm", "relative_intensity": "key",
                        "attachment": "fixed", "motivation": "hall lamp"}],
            "key_light_id": "lamp", "shadow_direction": "below", "exposure_priority": "door",
            "transition_intent": "hold tension"},
        "creative": {
            "optics": decision(mode="physical", focal_length_mm=35, sensor_basis="full frame", subject_distance="3 metres", depth_of_field="deep", focus_target="door", focus_change="hold"),
            "framing": decision(start_frame="full door", end_frame="full door", must_read=["lock"]),
            "camera_position": decision(landmark="door", position="south wall", height="1.5 metres", distance="3 metres", direction="north", axis_side="south"),
            "composition": decision(attention_center="lock", start_composition="centered door", end_composition="centered door", subject_placement="center", frame_occupancy="half", depth_layers="door then wall", occlusion="none", look_room="symmetric"),
            "motion": decision(mode="fixed", trigger="wind begins", path="hold position", speed_profile="zero", end_condition="wind dies", constraints="keep door readable"),
            "performance": decision(applicability="not_applicable", not_applicable_reason="No characters visible"),
            "lighting": decision(light_ids=["lamp"], camera_relative_lighting="top light", shadow="under frame", quality="soft", color="warm", subject_background_exposure="door brighter", eye_light="not applicable, no eyes"),
            "handoff": decision(next_shot_id=next_shot_id, cut_trigger="wind dies", spatial_handoff="same door", gaze_handoff="no characters", action_phase="held", held_object_handoff="no objects held", emotional_handoff="tension", sound_bridge="wind trails")}})
