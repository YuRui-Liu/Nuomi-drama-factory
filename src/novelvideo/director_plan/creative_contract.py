"""Versioned creative choices, independent of execution providers.

References remain explicit; none of these fields is inferred from legacy prose.
Lighting references the existing cinematography light inventory, and handoff is
stored once, on the outgoing shot (including across narrative group boundaries).
"""
from typing import Literal, Self

from pydantic import Field, model_validator

from .cinematography import DirectionModel, ShotCinematography, Text


class DecisionSource(DirectionModel):
    kind: Literal["script_fact", "asset_fact", "director_choice", "media_observation"]
    reference_ids: tuple[Text, ...] = Field(min_length=1)


class Decision(DirectionModel):
    purpose: Text
    source: DecisionSource


class SceneLandmark(DirectionModel):
    landmark_id: Text
    world_position: Text
    relative_distance: Text


class GroupCreativeContext(Decision):
    """Shared geography stays in world coordinates, never screen-left/right."""
    scene_id: Text
    geography: Text
    landmarks: tuple[SceneLandmark, ...] = Field(min_length=1)
    action_axis: Text
    available_camera_regions: tuple[Text, ...] = Field(min_length=1)
    light_source_ids: tuple[Text, ...] = Field(min_length=1)
    initial_relationships: Text
    audience_information: Text
    performance_turn: Text
    required_beat_ids: tuple[Text, ...] = ()

    @model_validator(mode="after")
    def unique_landmarks(self) -> Self:
        ids = [item.landmark_id for item in self.landmarks]
        if len(ids) != len(set(ids)):
            raise ValueError("scene landmark ids must be unique")
        return self


class OpticsDecision(Decision):
    mode: Literal["physical", "field_of_view", "not_applicable"]
    focal_length_mm: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    sensor_basis: Text | None = None
    field_of_view_degrees: float | None = Field(default=None, gt=0, lt=180, allow_inf_nan=False)
    field_of_view_axis: Literal["horizontal", "vertical", "diagonal"] | None = None
    not_applicable_reason: Text | None = None
    subject_distance: Text
    depth_of_field: Text
    focus_target: Text
    focus_change: Text
    end_focus_target: Text | None = Field(default=None, description="Non-null terminal focus target required when keyframe_anchor=end.")

    @model_validator(mode="after")
    def validate_basis(self) -> Self:
        if self.mode == "physical" and (self.focal_length_mm is None or self.sensor_basis is None):
            raise ValueError("physical optics require focal length and sensor basis")
        if self.mode == "field_of_view" and (self.field_of_view_degrees is None or self.field_of_view_axis is None):
            raise ValueError("field of view requires angle and axis")
        if self.mode == "not_applicable":
            if not self.not_applicable_reason:
                raise ValueError("not applicable optics require a reason")
            if self.focal_length_mm is not None or self.field_of_view_degrees is not None:
                raise ValueError("not applicable optics cannot declare physical lens values")
        if (self.field_of_view_degrees is None) != (self.field_of_view_axis is None):
            raise ValueError("field of view angle and axis must be paired")
        return self


class FramingDecision(Decision):
    start_frame: Text
    end_frame: Text
    must_read: tuple[Text, ...] = Field(min_length=1)


class CameraPositionDecision(Decision):
    landmark: Text
    position: Text
    height: Text
    distance: Text
    direction: Text
    axis_side: Text


class CompositionDecision(Decision):
    attention_center: Text
    start_composition: Text
    end_composition: Text
    subject_placement: Text
    frame_occupancy: Text
    depth_layers: Text
    occlusion: Text
    look_room: Text


class MotionDecision(Decision):
    mode: Literal["fixed", "moving"]
    trigger: Text
    path: Text
    speed_profile: Text
    end_condition: Text
    constraints: Text


class PerformanceBeat(DirectionModel):
    at_seconds: float = Field(ge=0, allow_inf_nan=False)
    event_anchor: Text
    behavior: Text


class CharacterPerformance(DirectionModel):
    subject_id: Text
    master_profile_id: Text | None = None
    master_profile_version: Text | None = None
    master_profile_missing_reason: Text | None = None
    start_behavior: Text
    end_behavior: Text
    goal: Text
    obstacle: Text
    tactics: Text
    visible_behavior: Text
    gaze: Text
    contact: Text
    held_objects: Text
    weight_and_inertia: Text
    beats: tuple[PerformanceBeat, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_master_profile(self) -> Self:
        if (self.master_profile_id is None) != (self.master_profile_version is None):
            raise ValueError("master profile id and version must be paired")
        if self.master_profile_id is None and self.master_profile_missing_reason is None:
            raise ValueError("performance requires master profile reference or explicit missing reason")
        if self.master_profile_id is not None and self.master_profile_missing_reason is not None:
            raise ValueError("master profile reference and missing reason cannot coexist")
        return self


class PerformanceDecision(Decision):
    applicability: Literal["applicable", "not_applicable"]
    not_applicable_reason: Text | None = None
    characters: tuple[CharacterPerformance, ...] = ()

    @model_validator(mode="after")
    def validate_applicability(self) -> Self:
        if self.applicability == "applicable" and not self.characters:
            raise ValueError("applicable performance requires characters")
        if self.applicability == "not_applicable" and (not self.not_applicable_reason or self.characters):
            raise ValueError("not applicable performance requires reason and no characters")
        ids = [item.subject_id for item in self.characters]
        if len(ids) != len(set(ids)):
            raise ValueError("performance subject ids must be unique")
        return self


class LightingDecision(Decision):
    light_ids: tuple[Text, ...] = Field(min_length=1)
    camera_relative_lighting: Text
    shadow: Text
    quality: Text
    color: Text
    subject_background_exposure: Text
    eye_light: Text


class HandoffDecision(Decision):
    next_shot_id: Text | None
    cut_trigger: Text
    spatial_handoff: Text
    gaze_handoff: Text
    action_phase: Text
    held_object_handoff: Text
    emotional_handoff: Text
    sound_bridge: Text
    intentional_discontinuity: Text | None = None


class ShotCreativeDecisions(DirectionModel):
    keyframe_anchor: Literal["start", "end"] = "start"
    end_cinematography: ShotCinematography | None = Field(default=None, description="Complete terminal ShotCinematography snapshot required when keyframe_anchor=end; must not be null. Describe terminal positions, facing, gaze and lighting explicitly.")
    optics: OpticsDecision
    framing: FramingDecision
    camera_position: CameraPositionDecision
    composition: CompositionDecision
    motion: MotionDecision
    performance: PerformanceDecision
    lighting: LightingDecision
    handoff: HandoffDecision

    @model_validator(mode="after")
    def validate_end_snapshot(self) -> Self:
        if self.keyframe_anchor == "end" and (self.end_cinematography is None or self.optics.end_focus_target is None):
            raise ValueError("end keyframe requires end_cinematography and end_focus_target")
        return self


def creative_shot_errors(shot, *, explain: bool = False) -> tuple[str, ...]:
    creative = shot.creative
    if creative is None or shot.cinematography is None:
        return ("creative_decisions_missing",)
    errors = []
    for name in ShotCreativeDecisions.model_fields:
        if name in {"keyframe_anchor", "end_cinematography"}:
            continue
        source = getattr(creative, name).source
        if source.kind == "script_fact" and not set(source.reference_ids).issubset(shot.source_span_ids):
            missing = sorted(set(source.reference_ids) - set(shot.source_span_ids))
            errors.append("creative_source_mismatch" + (
                f": creative.{name}.source.reference_ids={missing!r} are outside this shot's source_span_ids; "
                "cite this shot's supported source spans, or classify an actual directing choice as director_choice"
                if explain else ""))
        if source.kind == "asset_fact" and not set(source.reference_ids).issubset({r.entity_key for r in shot.asset_requirements}):
            errors.append("creative_asset_source_mismatch")
    if not set(creative.lighting.light_ids).issubset({light.light_id for light in shot.cinematography.lights}):
        errors.append("creative_light_reference_missing")
    if {item.subject_id for item in creative.performance.characters} != {item.subject_id for item in shot.cinematography.subjects}:
        errors.append("creative_performance_subject_mismatch")
    for character in creative.performance.characters:
        times = [beat.at_seconds for beat in character.beats]
        if times != sorted(times) or any(time > shot.duration_seconds for time in times):
            errors.append("creative_beat_time_invalid")
    return tuple(dict.fromkeys(errors))
