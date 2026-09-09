import re
from collections.abc import Sequence
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .models import H3Mode


_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True)
_NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
_DurationSeconds = Annotated[float, Field(ge=4, le=15)]
_NO_MUSIC_VALUES = frozenset(
    {"", "n/a", "none", "none.", "no music", "no music.", "no music. sfx only."}
)
H3_VISUAL_RETENTION_RELATIONS = frozenset(
    {
        "fully_preserved",
        "partially_preserved",
        "attribute_transfer",
        "weak_reference",
    }
)
H3_AUDIO_RETENTION_RELATIONS = frozenset(
    {"fully_copy", "partially_copy", "reference", "weak_reference"}
)
H3_MAX_REFERENCE_PICTURES = 10
_SUBJECT_TAG_PATTERN = re.compile(r"<Subject ([1-9][0-9]*)>")
_PICTURE_TAG_PATTERN = re.compile(r"<?Picture ([1-9][0-9]*)>?", re.IGNORECASE)
_REFERENCE_IMAGE_PATTERN = re.compile(
    r"\breference image ([1-9][0-9]*)\b", re.IGNORECASE
)


def normalize_h3_music(value: str | None) -> str:
    normalized = str(value or "").strip()
    semantic_value = " ".join(normalized.casefold().split())
    return "N/A" if semantic_value in _NO_MUSIC_VALUES else normalized


def parse_h3_retention_relation(value: str, *, audio: bool = False) -> str | None:
    relation, separator, detail = value.strip().partition(" - ")
    allowed = H3_AUDIO_RETENTION_RELATIONS if audio else H3_VISUAL_RETENTION_RELATIONS
    return relation if separator and detail.strip() and relation in allowed else None


def normalize_h3_visual_retention(value: str) -> str:
    normalized = value.strip()
    if parse_h3_retention_relation(normalized) is not None:
        return normalized
    return f"fully_preserved - {normalized}"


def inspect_h3_reference_semantics(
    subject_definitions: str,
    retention_items: Sequence[tuple[str, str]],
    detailed_description: str,
    *,
    additional_text: Sequence[str] = (),
) -> tuple[str, ...]:
    """Return stable semantic issue names for the official Ref2VA wire."""
    issues: list[str] = []
    definition_subjects: list[int] = []
    defined_pictures: set[int] = set()
    definition_lines = tuple(
        line.strip() for line in subject_definitions.splitlines() if line.strip()
    )
    for line in definition_lines:
        subject_match = re.match(r"^<Subject ([1-9][0-9]*)>(?:\s|:)", line)
        subject_tags = tuple(int(value) for value in _SUBJECT_TAG_PATTERN.findall(line))
        picture_indexes = tuple(
            int(value)
            for value in (
                *_PICTURE_TAG_PATTERN.findall(line),
                *_REFERENCE_IMAGE_PATTERN.findall(line),
            )
        )
        if (
            subject_match is None
            or subject_tags != (int(subject_match.group(1)),)
            or not picture_indexes
            or len(picture_indexes) != len(set(picture_indexes))
        ):
            _append_once(issues, "reference_definition_invalid")
            continue
        definition_subjects.append(int(subject_match.group(1)))
        defined_pictures.update(picture_indexes)
        if any(index > H3_MAX_REFERENCE_PICTURES for index in picture_indexes):
            _append_once(issues, "reference_picture_out_of_range")
    if definition_subjects != list(range(1, len(definition_lines) + 1)):
        _append_once(issues, "reference_definition_invalid")

    retention_subjects: list[int] = []
    retention_text: list[str] = []
    for subject, retain in retention_items:
        subject_match = re.match(r"^<Subject ([1-9][0-9]*)>\s+\S", subject)
        subject_tags = tuple(int(value) for value in _SUBJECT_TAG_PATTERN.findall(subject))
        if subject_match is None or subject_tags != (int(subject_match.group(1)),):
            _append_once(issues, "reference_subject_mismatch")
        else:
            retention_subjects.append(int(subject_match.group(1)))
        if parse_h3_retention_relation(retain) is None:
            _append_once(issues, "reference_relation_invalid")
        retention_text.extend((subject, retain))
    if (
        retention_subjects != definition_subjects
        or len(retention_subjects) != len(set(retention_subjects))
    ):
        _append_once(issues, "reference_subject_mismatch")

    active_subjects = {
        int(value) for value in _SUBJECT_TAG_PATTERN.findall(detailed_description)
    }
    if active_subjects != set(definition_subjects):
        _append_once(issues, "reference_subject_inactive")

    all_text = "\n".join(
        (subject_definitions, detailed_description, *retention_text, *additional_text)
    )
    used_pictures = {int(value) for value in _PICTURE_TAG_PATTERN.findall(all_text)}
    if not used_pictures.issubset(defined_pictures):
        _append_once(issues, "reference_picture_out_of_range")
    return tuple(issues)


def _append_once(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


class H3RetentionItem(BaseModel):
    model_config = _MODEL_CONFIG

    subject: _NonEmptyString
    retain: _NonEmptyString

    @field_validator("retain")
    @classmethod
    def validate_visual_relation(cls, value: str) -> str:
        if parse_h3_retention_relation(value) is None:
            raise ValueError("reference_relation_invalid")
        return value


class H3BaseWire(BaseModel):
    model_config = _MODEL_CONFIG

    mode: Literal[
        H3Mode.T2VA,
        H3Mode.I2VA,
        H3Mode.FL2VA,
        H3Mode.L2VA,
    ]
    duration_seconds: _DurationSeconds
    final_shot_number: int = Field(default=1, ge=1)
    integrated_multimodal_description: _NonEmptyString
    overall_soundscape: _NonEmptyString
    non_diegetic_music: _NonEmptyString

    @model_validator(mode="after")
    def validate_image_alignment(self) -> Self:
        description = self.integrated_multimodal_description
        shot_sequence = tuple(
            int(number) for number in re.findall(r"\[Shot (\d+)\]", description)
        )
        if not description.startswith("[Shot 1]") or shot_sequence[0] != 1:
            raise ValueError("shot sequence must start with [Shot 1]")
        if any(left >= right for left, right in zip(shot_sequence, shot_sequence[1:])):
            raise ValueError(
                "shot sequence must be strictly increasing without duplicates"
            )
        if shot_sequence[-1] != self.final_shot_number:
            raise ValueError(
                "final shot in shot sequence must match "
                f"[Shot {self.final_shot_number}]"
            )
        return self


class H3ReferenceWire(BaseModel):
    model_config = _MODEL_CONFIG

    mode: Literal[H3Mode.REF2VA]
    duration_seconds: _DurationSeconds
    subject_definitions: _NonEmptyString
    summary: _NonEmptyString
    retention_analysis: tuple[H3RetentionItem, ...] = Field(min_length=1)
    detailed_description: _NonEmptyString
    overall_soundscape: _NonEmptyString
    non_diegetic_music: _NonEmptyString

    @model_validator(mode="after")
    def validate_reference_semantics(self) -> Self:
        issues = inspect_h3_reference_semantics(
            self.subject_definitions,
            tuple((item.subject, item.retain) for item in self.retention_analysis),
            self.detailed_description,
            additional_text=(
                self.summary,
                self.overall_soundscape,
                self.non_diegetic_music,
            ),
        )
        if issues:
            raise ValueError(", ".join(issues))
        return self


H3Wire = H3BaseWire | H3ReferenceWire


def compile_h3_wire(wire: H3Wire) -> str:
    if isinstance(wire, H3ReferenceWire):
        retention_analysis = "\n".join(
            f"- {item.subject}: {item.retain}" for item in wire.retention_analysis
        )
        sections = (
            ("subject_definitions", wire.subject_definitions),
            ("summary", wire.summary),
            ("retention_analysis", retention_analysis),
            ("detailed_description", wire.detailed_description),
            ("overall_soundscape", wire.overall_soundscape),
            ("non_diegetic_music", wire.non_diegetic_music),
        )
    else:
        sections = (
            (
                "integrated_multimodal_description",
                wire.integrated_multimodal_description,
            ),
            ("overall_soundscape", wire.overall_soundscape),
            ("non_diegetic_music", wire.non_diegetic_music),
        )
        body = "\n\n".join(f"{name}: {value}" for name, value in sections)
        instruction = _base_alignment_instruction(wire)
        return f"{instruction}\n\n{body}" if instruction else body

    return "\n\n".join(f"{name}:\n{value}" for name, value in sections)


def _base_alignment_instruction(wire: H3BaseWire) -> str:
    if wire.mode is H3Mode.I2VA:
        return (
            "For the target video, at 0.00 seconds into the target video, "
            "<Picture 1> (from [Shot 1]) is fully referenced."
        )
    if wire.mode is H3Mode.FL2VA:
        return (
            "How the reference pictures align with the target video — Picture 1 "
            "(from Shot 1) aligns with the 0.00-second mark of the target video; "
            f"Picture 2 (from Shot {wire.final_shot_number}) aligns with the "
            f"{wire.duration_seconds:.2f}-second mark of the target video."
        )
    if wire.mode is H3Mode.L2VA:
        return (
            "How the reference pictures align with the target video — "
            f"<Picture 1> (from [Shot {wire.final_shot_number}]) aligns with the "
            f"{wire.duration_seconds:.2f}-second mark of the target video."
        )
    return ""
