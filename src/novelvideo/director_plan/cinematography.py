"""Provider-independent, source-attributed blocking and lighting directions."""

from typing import TYPE_CHECKING, Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

if TYPE_CHECKING:
    from .models import DirectorPlanRevision, ShotPlan

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DirectionModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SubjectBlocking(DirectionModel):
    subject_id: Text
    world_position: Text
    screen_position: Text
    facing: Text
    gaze_target: Text
    motion_path: Text


class MotivatedLight(DirectionModel):
    light_id: Text
    source_type: Literal["practical", "sun", "moon", "ambient", "artificial"]
    world_position: Text
    direction: Text
    color_temperature: Text
    relative_intensity: Text
    attachment: Text  # "fixed" or an existing subject/prop ID
    motivation: Text


class ShotCinematography(DirectionModel):
    source: Literal["director_plan", "director_world", "reference_observation"]
    source_ids: tuple[Text, ...] = Field(min_length=1)
    axis: Text
    camera_side: Text
    screen_direction: Text
    subjects: tuple[SubjectBlocking, ...] = ()  # Empty for establishing/prop shots.
    lights: tuple[MotivatedLight, ...] = Field(min_length=1)
    key_light_id: Text
    shadow_direction: Text
    exposure_priority: Text
    transition_intent: Text
    requires_spatial_control: bool = False

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        light_ids = [light.light_id for light in self.lights]
        if len(set(light_ids)) != len(light_ids):
            raise ValueError("light ids must be unique")
        if self.key_light_id not in light_ids:
            raise ValueError("key light must reference a declared light")
        subject_ids = [subject.subject_id for subject in self.subjects]
        if len(set(subject_ids)) != len(subject_ids):
            raise ValueError("blocking subject ids must be unique")
        return self

    def prompt_facts(self) -> str:
        """Keep world-space facts distinct from their shot-specific projection."""
        lines = [
            f"Action axis: {self.axis}; camera side: {self.camera_side}; "
            f"screen travel: {self.screen_direction}.",
        ]
        for subject in self.subjects:
            lines.append(
                f"{subject.subject_id}: world position {subject.world_position}; "
                f"screen position {subject.screen_position}; body facing {subject.facing}; "
                f"gaze target {subject.gaze_target}; motion path {subject.motion_path}."
            )
        for light in self.lights:
            lines.append(
                f"Light {light.light_id} ({light.source_type}): world origin "
                f"{light.world_position}; direction {light.direction}; "
                f"temperature {light.color_temperature}; intensity {light.relative_intensity}; "
                f"attachment {light.attachment}; motivation {light.motivation}."
            )
        lines.append(
            f"Key light: {self.key_light_id}; shadows: {self.shadow_direction}; "
            f"exposure: {self.exposure_priority}; cut intent: {self.transition_intent}."
        )
        return "\n".join(lines)


def production_direction_errors(shot: "ShotPlan") -> tuple[str, ...]:
    """Preflight facts without inventing missing directions for historical shots."""
    facts = shot.cinematography
    if facts is None:
        return ("cinematography_missing",)
    errors = []
    character_ids = {
        item.entity_key for item in shot.asset_requirements
        if item.kind in {"character_identity", "character_state"}
    }
    if character_ids and {item.subject_id for item in facts.subjects} != character_ids:
        errors.append("blocking_subject_mismatch")
    if facts.source == "director_plan" and not set(facts.source_ids).issubset(shot.source_span_ids):
        errors.append("cinematography_source_mismatch")
    return tuple(errors)


def require_production_directions(plan: "DirectorPlanRevision | None", group_id: str) -> None:
    if plan is None:
        raise ValueError("director_plan_required")
    group = next((item for item in plan.groups if item.id == group_id), None)
    if group is None:
        raise ValueError("director_group_missing")
    issues = [f"{shot.id}:{code}" for shot in group.shots
              for code in production_direction_errors(shot)]
    if issues:
        raise ValueError(";".join(issues))
