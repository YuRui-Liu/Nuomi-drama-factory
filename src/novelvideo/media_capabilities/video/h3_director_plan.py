"""Typed director-plan contracts for MiniMax H3 frame-conditioned video."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .h3_rigid_prompt import H3RigidPromptPlan
from .models import H3Mode


H3_FPS = 24
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")
H3_STATIC_CAMERA_TYPES = frozenset({"fixed", "locked", "none", "static"})
_RESERVED_WIRE_MARKERS = ("<d>", "</d>", "<scenetrans>", "<cutoff>")
_RESERVED_WIRE_FIELDS = (
    "integrated_multimodal_description:",
    "overall_soundscape:",
    "non_diegetic_music:",
)


def _has_control_character(value: str) -> bool:
    return any(
        unicodedata.category(char) in {"Cc", "Zl", "Zp"} for char in value
    )


def _safe_structural_text(value: object) -> object:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    if not normalized:
        raise ValueError("value must not be blank")
    if _has_control_character(normalized):
        raise ValueError("structural text must not contain a control character")
    lowered = normalized.casefold()
    if any(token in lowered for token in _RESERVED_WIRE_MARKERS):
        raise ValueError("structural text contains a reserved wire marker")
    if any(token in lowered for token in _RESERVED_WIRE_FIELDS):
        raise ValueError("structural text contains a reserved wire field")
    return normalized


def _safe_top_level_text(value: object) -> object:
    if isinstance(value, str):
        lowered = value.casefold()
        if any(token in lowered for token in _RESERVED_WIRE_FIELDS):
            raise ValueError("top-level text contains a reserved wire field")
    return _safe_structural_text(value)


def _safe_dialogue_text(value: object) -> object:
    if not isinstance(value, str):
        return value
    if not value.strip():
        raise ValueError("dialogue text must not be blank")
    if _has_control_character(value):
        raise ValueError("dialogue text must not contain a control character")
    lowered = value.casefold()
    if any(token in lowered for token in _RESERVED_WIRE_MARKERS):
        raise ValueError("dialogue text contains a reserved wire marker")
    return value


class H3FrameAnchor(BaseModel):
    model_config = _MODEL_CONFIG

    sha256: str
    description: str = Field(min_length=1)

    @field_validator("sha256", mode="before")
    @classmethod
    def normalize_sha256(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
            raise ValueError("sha256 must be exactly 64 hexadecimal characters")
        return normalized

    @field_validator("description", mode="before")
    @classmethod
    def trim_description(cls, value: object) -> object:
        return _safe_structural_text(value)


class H3ReferenceSubjectPlan(BaseModel):
    model_config = _MODEL_CONFIG

    subject_index: int = Field(ge=1)
    source_picture_indexes: tuple[int, ...] = Field(min_length=1)
    description: str = Field(min_length=1)
    retention_marker: Literal[
        "fully_preserved",
        "partially_preserved",
        "attribute_transfer",
        "weak_reference",
    ]
    retention_detail: str = Field(min_length=1)
    shot_ids: tuple[str, ...] = Field(min_length=1)
    speaker_id: str | None = Field(default=None, pattern=r"^S[1-9][0-9]*$")

    @field_validator("description", "retention_detail", mode="before")
    @classmethod
    def trim_description_fields(cls, value: object) -> object:
        return _safe_structural_text(value)

    @field_validator("source_picture_indexes")
    @classmethod
    def validate_source_picture_indexes(
        cls, value: tuple[int, ...]
    ) -> tuple[int, ...]:
        if any(index < 1 for index in value):
            raise ValueError("source_picture_indexes must contain positive integers")
        if any(left >= right for left, right in zip(value, value[1:])):
            raise ValueError(
                "source_picture_indexes must be strictly increasing and unique"
            )
        return value

    @field_validator("shot_ids", mode="before")
    @classmethod
    def validate_shot_ids(cls, value: object) -> object:
        if not isinstance(value, (list, tuple)):
            return value
        normalized = tuple(_safe_structural_text(item) for item in value)
        if any(
            not isinstance(shot_id, str)
            or re.fullmatch(r"[1-9][0-9]*", shot_id) is None
            for shot_id in normalized
        ):
            raise ValueError("shot_ids must contain positive integer strings")
        numeric = tuple(int(shot_id) for shot_id in normalized)
        if any(left >= right for left, right in zip(numeric, numeric[1:])):
            raise ValueError("shot_ids must be strictly increasing and unique")
        return normalized


class H3CameraPlan(BaseModel):
    model_config = _MODEL_CONFIG

    type: str = Field(min_length=1)
    direction: str | None = None
    amplitude: str | None = None
    speed: str | None = None

    @field_validator("type", "direction", "amplitude", "speed", mode="before")
    @classmethod
    def trim_text(cls, value: object) -> object:
        return _safe_structural_text(value)

    @property
    def is_static(self) -> bool:
        return self.type.casefold() in H3_STATIC_CAMERA_TYPES

    @model_validator(mode="after")
    def validate_dynamic_camera(self) -> "H3CameraPlan":
        motion_parameters = (self.direction, self.amplitude, self.speed)
        if self.is_static and any(motion_parameters):
            raise ValueError("static camera contradicts supplied motion parameters")
        if not self.is_static and self.direction is None:
            raise ValueError("dynamic camera requires direction")
        return self


class H3ActionPlan(BaseModel):
    model_config = _MODEL_CONFIG

    phase: Literal[
        "establish", "prepare", "execute", "react", "settle", "end_lock"
    ]
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    description: str = Field(min_length=1)
    change_domain: Literal[
        "subject_or_prop", "camera_only", "lighting_only", "environment_only"
    ] = "subject_or_prop"
    moving_entities: tuple[str, ...] = ()

    @field_validator("description", mode="before")
    @classmethod
    def trim_description(cls, value: object) -> object:
        return _safe_structural_text(value)

    @field_validator("moving_entities", mode="before")
    @classmethod
    def trim_moving_entities(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            return tuple(_safe_structural_text(item) for item in value)
        return value

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
    voice_descriptor: str | None = None
    delivery: str | None = None
    physical_action: str | None = None
    facial_reaction: str | None = None
    continuation: bool = False
    truncated: bool = False

    @field_validator(
        "speaker",
        "speaker_id",
        "language",
        "voice_descriptor",
        "delivery",
        "physical_action",
        "facial_reaction",
        mode="before",
    )
    @classmethod
    def validate_structural_text(cls, value: object) -> object:
        return _safe_structural_text(value)

    @field_validator("text", mode="before")
    @classmethod
    def validate_dialogue_text(cls, value: object) -> object:
        return _safe_dialogue_text(value)

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
        return _safe_structural_text(value)


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
        return _safe_structural_text(value)

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
        previous_end = -1
        for cue in cues:
            if cue.start_frame < self.start_frame or cue.end_frame > self.end_frame:
                raise ValueError(f"{name} must remain inside shot boundaries")
            if cue.start_frame < previous_start:
                raise ValueError(f"{name} must be in increasing frame order")
            if cue.start_frame < previous_end:
                raise ValueError(f"{name} must not overlap")
            previous_start = cue.start_frame
            previous_end = cue.end_frame


class H3DirectorPlan(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal[1, 2, 3] = 1
    mode: H3Mode
    fps: Literal[24] = H3_FPS
    total_frames: int = Field(gt=0)
    visual_style: str = Field(min_length=1)
    continuity_locks: tuple[str, ...] = Field(min_length=1)
    shots: tuple[H3ShotPlan, ...] = Field(min_length=1)
    frame_differences: tuple[H3FrameDifference, ...] = ()
    soundscape: str = Field(min_length=1)
    music: str = Field(min_length=1)
    rigid_prompt: H3RigidPromptPlan | None = None
    first_frame_anchor: H3FrameAnchor | None = None
    last_frame_anchor: H3FrameAnchor | None = None
    reference_summary: str | None = None
    reference_subjects: tuple[H3ReferenceSubjectPlan, ...] = ()

    @field_validator(
        "visual_style", "soundscape", "music", "reference_summary", mode="before"
    )
    @classmethod
    def trim_required_text(cls, value: object) -> object:
        return _safe_top_level_text(value)

    @field_validator("continuity_locks", mode="before")
    @classmethod
    def trim_continuity_locks(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            return tuple(_safe_top_level_text(item) for item in value)
        return value

    @model_validator(mode="after")
    def validate_plan(self) -> "H3DirectorPlan":
        if self.schema_version < 3 and self.mode not in {
            H3Mode.I2VA,
            H3Mode.FL2VA,
        }:
            raise ValueError("H3 director plans support only i2va and fl2va")
        if self.schema_version == 1 and self.rigid_prompt is not None:
            raise ValueError("rigid_prompt requires schema_version=2")
        self._validate_shot_coverage()
        self._validate_speaker_identity()
        self._validate_dialogue_continuations()
        if self.schema_version == 3:
            self._validate_v3_mode_inputs()
        if self.mode is H3Mode.I2VA:
            self._validate_i2va_anchor()
        elif self.mode is H3Mode.FL2VA:
            self._validate_fl2va_convergence()
        elif self.mode is H3Mode.L2VA:
            self._validate_l2va_convergence()
        elif self.mode is H3Mode.REF2VA:
            self._validate_reference_subjects()
        return self

    def _validate_v3_mode_inputs(self) -> None:
        has_references = bool(self.reference_subjects) or bool(
            self.reference_summary
        )
        if self.mode is H3Mode.T2VA:
            if self.first_frame_anchor or self.last_frame_anchor or has_references:
                raise ValueError("t2va forbids frame anchors and reference inputs")
            return
        if self.mode is H3Mode.I2VA:
            if self.first_frame_anchor is None:
                raise ValueError("i2va requires first_frame_anchor")
            if self.last_frame_anchor is not None:
                raise ValueError("i2va forbids last_frame_anchor")
            if has_references:
                raise ValueError("i2va forbids reference inputs")
            return
        if self.mode is H3Mode.FL2VA:
            if self.first_frame_anchor is None:
                raise ValueError("fl2va requires first_frame_anchor")
            if self.last_frame_anchor is None:
                raise ValueError("fl2va requires last_frame_anchor")
            if has_references:
                raise ValueError("fl2va forbids reference inputs")
            self._validate_distinct_frame_anchors()
            return
        if self.mode is H3Mode.L2VA:
            if self.last_frame_anchor is None:
                raise ValueError("l2va requires last_frame_anchor")
            if self.first_frame_anchor is not None:
                raise ValueError("l2va forbids first_frame_anchor")
            if has_references:
                raise ValueError("l2va forbids reference inputs")
            return
        if self.first_frame_anchor is not None or self.last_frame_anchor is not None:
            raise ValueError("ref2va forbids first and last frame anchors")
        if self.reference_summary is None:
            raise ValueError("ref2va requires reference_summary")
        if not self.reference_subjects:
            raise ValueError("ref2va requires non-empty reference_subjects")

    def _validate_distinct_frame_anchors(self) -> None:
        if (
            self.first_frame_anchor is not None
            and self.last_frame_anchor is not None
            and self.first_frame_anchor.sha256 == self.last_frame_anchor.sha256
        ):
            raise ValueError(
                "first and last frame anchor sha256 values must be different"
            )

    def _validate_shot_coverage(self) -> None:
        expected = 0
        for number, shot in enumerate(self.shots, start=1):
            if shot.shot_id != str(number):
                raise ValueError(
                    "shot_id values must be continuous string numbers from 1"
                )
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
        self._validate_frame_differences("fl2va")
        self._validate_terminal_convergence("fl2va")

    def _validate_l2va_convergence(self) -> None:
        self._validate_frame_differences("l2va")
        self._validate_terminal_convergence("l2va")

    def _validate_frame_differences(self, mode: str) -> None:
        if not self.frame_differences:
            raise ValueError(f"{mode} requires frame differences")
        previous = 0
        for difference in self.frame_differences:
            if difference.convergence_frame >= self.total_frames:
                raise ValueError("frame differences must converge before the final frame")
            if difference.convergence_frame <= previous:
                raise ValueError(
                    "frame differences must converge in strictly increasing frame order"
                )
            previous = difference.convergence_frame

    def _validate_terminal_convergence(self, mode: str) -> None:
        final_action = self.shots[-1].actions[-1]
        if final_action.phase not in {"settle", "end_lock"}:
            raise ValueError(f"{mode} final action must be settle or end_lock")
        if final_action.end_frame != self.total_frames:
            raise ValueError(f"{mode} final action must converge at total_frames")

    def _validate_reference_subjects(self) -> None:
        expected_indexes = tuple(range(1, len(self.reference_subjects) + 1))
        actual_indexes = tuple(
            subject.subject_index for subject in self.reference_subjects
        )
        if actual_indexes != expected_indexes:
            raise ValueError(
                "reference subject_index values must be continuous from 1"
            )
        known_shot_ids = {shot.shot_id for shot in self.shots}
        dialogue_speaker_ids = {
            cue.speaker_id for shot in self.shots for cue in shot.dialogue
        }
        bound_speaker_ids: set[str] = set()
        for subject in self.reference_subjects:
            if not set(subject.shot_ids).issubset(known_shot_ids):
                raise ValueError(
                    "reference subject shot_ids must exist in director plan shots"
                )
            if subject.speaker_id is None:
                continue
            if subject.speaker_id not in dialogue_speaker_ids:
                raise ValueError(
                    "reference subject speaker_id must exist in plan dialogue"
                )
            if subject.speaker_id in bound_speaker_ids:
                raise ValueError(
                    "speaker_id may bind to only one reference subject"
                )
            bound_speaker_ids.add(subject.speaker_id)

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

    def _validate_dialogue_continuations(self) -> None:
        last_shot_index = len(self.shots) - 1
        for index, shot in enumerate(self.shots):
            for cue_index, cue in enumerate(shot.dialogue):
                if cue.truncated:
                    if index != last_shot_index:
                        raise ValueError("truncated dialogue is only valid in final shot")
                    if cue_index != len(shot.dialogue) - 1:
                        raise ValueError(
                            "truncated dialogue must be the last dialogue cue"
                        )
                    if cue.end_frame != self.total_frames:
                        raise ValueError(
                            "truncated dialogue must end at total_frames"
                        )

        for left, right in zip(self.shots, self.shots[1:]):
            left_cue = left.dialogue[-1] if left.dialogue else None
            right_cue = right.dialogue[0] if right.dialogue else None
            left_continuation = bool(left_cue and left_cue.continuation)
            right_continuation = bool(right_cue and right_cue.continuation)
            if left_continuation != right_continuation:
                raise ValueError(
                    "cross-shot continuation must be paired at adjacent shot ends"
                )
            if left_continuation and left_cue.speaker_id != right_cue.speaker_id:
                raise ValueError(
                    "cross-shot continuation must keep the same speaker_id"
                )

        for index, shot in enumerate(self.shots):
            for cue_index, cue in enumerate(shot.dialogue):
                if not cue.continuation:
                    continue
                incoming = (
                    index > 0
                    and cue_index == 0
                    and bool(self.shots[index - 1].dialogue)
                    and self.shots[index - 1].dialogue[-1].continuation
                )
                outgoing = (
                    index < last_shot_index
                    and cue_index == len(shot.dialogue) - 1
                    and bool(self.shots[index + 1].dialogue)
                    and self.shots[index + 1].dialogue[0].continuation
                )
                if not incoming and not outgoing:
                    raise ValueError(
                        "cross-shot continuation must be paired at adjacent shot ends"
                    )


__all__ = [
    "H3ActionPlan",
    "H3CameraPlan",
    "H3DialogueCue",
    "H3DirectorPlan",
    "H3FrameAnchor",
    "H3FrameDifference",
    "H3ReferenceSubjectPlan",
    "H3ShotPlan",
    "H3_STATIC_CAMERA_TYPES",
]
