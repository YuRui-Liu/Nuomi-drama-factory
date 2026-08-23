"""Typed director-plan contracts for MiniMax H3 frame-conditioned video."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import H3Mode


H3_FPS = 24
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")
_STATIC_CAMERA_TYPES = frozenset({"fixed", "locked", "none", "static"})


class H3CameraPlan(BaseModel):
    model_config = _MODEL_CONFIG

    type: str = Field(min_length=1)
    direction: str | None = None
    amplitude: str | None = None
    speed: str | None = None

    @field_validator("type", "direction", "amplitude", "speed", mode="before")
    @classmethod
    def trim_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_dynamic_camera(self) -> "H3CameraPlan":
        if self.type.casefold() not in _STATIC_CAMERA_TYPES and not all(
            (self.direction, self.amplitude, self.speed)
        ):
            raise ValueError(
                "dynamic camera requires direction, amplitude, and speed"
            )
        return self


class H3ActionPlan(BaseModel):
    model_config = _MODEL_CONFIG

    phase: Literal["establish", "prepare", "execute", "react", "end_lock"]
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    description: str = Field(min_length=1)

    @field_validator("description", mode="before")
    @classmethod
    def trim_description(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_interval(self) -> "H3ActionPlan":
        if self.end_frame <= self.start_frame:
            raise ValueError("action end_frame must be after start_frame")
        return self


class H3DialogueCue(BaseModel):
    model_config = _MODEL_CONFIG

    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    speaker: str = Field(min_length=1)
    speaker_id: str = Field(pattern=r"^S[1-9][0-9]*$")
    text: str = Field(min_length=1)
    language: str = Field(min_length=1)
    continuation: bool = False
    truncated: bool = False

    @field_validator("speaker", "speaker_id", "text", "language", mode="before")
    @classmethod
    def trim_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_interval(self) -> "H3DialogueCue":
        if self.end_frame <= self.start_frame:
            raise ValueError("dialogue end_frame must be after start_frame")
        return self


class H3FrameDifference(BaseModel):
    model_config = _MODEL_CONFIG

    description: str = Field(min_length=1)
    convergence_frame: int = Field(gt=0)

    @field_validator("description", mode="before")
    @classmethod
    def trim_description(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class H3ShotPlan(BaseModel):
    model_config = _MODEL_CONFIG

    shot_id: str = Field(min_length=1)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    framing: str = Field(min_length=1)
    angle: str = Field(min_length=1)
    focus: str = Field(min_length=1)
    composition: str = Field(min_length=1)
    camera: H3CameraPlan
    actions: tuple[H3ActionPlan, ...] = Field(min_length=1)
    dialogue: tuple[H3DialogueCue, ...] = ()

    @field_validator(
        "shot_id", "framing", "angle", "focus", "composition", mode="before"
    )
    @classmethod
    def trim_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_cues(self) -> "H3ShotPlan":
        if self.end_frame <= self.start_frame:
            raise ValueError("shot end_frame must be after start_frame")
        self._validate_sequence(self.actions, "actions")
        self._validate_sequence(self.dialogue, "dialogue")
        return self

    def _validate_sequence(
        self, cues: tuple[H3ActionPlan, ...] | tuple[H3DialogueCue, ...], name: str
    ) -> None:
        previous_start = -1
        for cue in cues:
            if cue.start_frame < self.start_frame or cue.end_frame > self.end_frame:
                raise ValueError(f"{name} must remain inside shot boundaries")
            if cue.start_frame < previous_start:
                raise ValueError(f"{name} must be in increasing frame order")
            previous_start = cue.start_frame


class H3DirectorPlan(BaseModel):
    model_config = _MODEL_CONFIG

    mode: H3Mode
    fps: Literal[24] = H3_FPS
    total_frames: int = Field(gt=0)
    visual_style: str = Field(min_length=1)
    continuity_locks: tuple[str, ...] = Field(min_length=1)
    shots: tuple[H3ShotPlan, ...] = Field(min_length=1)
    frame_differences: tuple[H3FrameDifference, ...] = ()
    soundscape: str = Field(min_length=1)
    music: str = Field(min_length=1)

    @field_validator("visual_style", "soundscape", "music", mode="before")
    @classmethod
    def trim_required_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("continuity_locks", mode="before")
    @classmethod
    def trim_continuity_locks(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            return tuple(item.strip() if isinstance(item, str) else item for item in value)
        return value

    @model_validator(mode="after")
    def validate_plan(self) -> "H3DirectorPlan":
        if self.mode not in {H3Mode.I2VA, H3Mode.FL2VA}:
            raise ValueError("H3 director plans support only i2va and fl2va")
        self._validate_shot_coverage()
        self._validate_speaker_identity()
        if self.mode is H3Mode.I2VA:
            self._validate_i2va_anchor()
        else:
            self._validate_fl2va_convergence()
        return self

    def _validate_shot_coverage(self) -> None:
        expected = 0
        for shot in self.shots:
            if shot.start_frame != expected:
                raise ValueError("shots must be contiguous from frame 0")
            expected = shot.end_frame
        if expected != self.total_frames:
            raise ValueError("shots must continuously cover total_frames")

    def _validate_i2va_anchor(self) -> None:
        first_action = self.shots[0].actions[0]
        if first_action.phase != "establish" or first_action.start_frame != 0:
            raise ValueError("i2va requires an establish action anchored at frame 0")
        if not any(
            action.start_frame > 0 and action.phase != "establish"
            for shot in self.shots
            for action in shot.actions
        ):
            raise ValueError("i2va requires a later visual change after its frame 0 anchor")

    def _validate_fl2va_convergence(self) -> None:
        if len(self.shots) != 1:
            raise ValueError("fl2va director plans require a single continuous shot")
        if not self.frame_differences:
            raise ValueError("fl2va requires frame differences")
        previous = 0
        for difference in self.frame_differences:
            if difference.convergence_frame >= self.total_frames:
                raise ValueError("frame differences must converge before the final frame")
            if difference.convergence_frame < previous:
                raise ValueError("frame differences must converge in increasing frame order")
            previous = difference.convergence_frame

    def _validate_speaker_identity(self) -> None:
        ids_by_speaker: dict[str, str] = {}
        speakers_by_id: dict[str, str] = {}
        for shot in self.shots:
            for cue in shot.dialogue:
                known_id = ids_by_speaker.setdefault(cue.speaker, cue.speaker_id)
                if known_id != cue.speaker_id:
                    raise ValueError("dialogue speakers must keep a stable speaker_id")
                known_speaker = speakers_by_id.setdefault(cue.speaker_id, cue.speaker)
                if known_speaker != cue.speaker:
                    raise ValueError("speaker_id must identify one stable dialogue speaker")


__all__ = [
    "H3ActionPlan",
    "H3CameraPlan",
    "H3DialogueCue",
    "H3DirectorPlan",
    "H3FrameDifference",
    "H3ShotPlan",
]
