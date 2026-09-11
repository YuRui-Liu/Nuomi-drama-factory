import re
from collections.abc import Sequence
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
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
_REFERENCE_TAG_PATTERN = re.compile(
    r"<(?P<kind>Subject|Picture|Video|Audio) (?P<index>[1-9][0-9]*)>"
)
_REFERENCE_LIKE_PATTERN = re.compile(
    r"<?(?:Subject|Picture|Video|Audio) [0-9]+>?", re.IGNORECASE
)
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
    definition_labels: list[tuple[str, int]] = []
    declared_labels: set[tuple[str, int]] = set()
    definition_lines = tuple(
        line.strip() for line in subject_definitions.splitlines() if line.strip()
    )
    for line in definition_lines:
        if _has_malformed_reference_label(line):
            _append_once(issues, "reference_definition_invalid")
            continue
        leading = re.match(
            r"^<(?P<kind>Subject|Picture|Video|Audio) "
            r"(?P<index>[1-9][0-9]*)>(?:\s|:)",
            line,
        )
        labels = _reference_labels(line)
        legacy_pictures = tuple(
            ("Picture", int(value)) for value in _REFERENCE_IMAGE_PATTERN.findall(line)
        )
        if leading is None:
            _append_once(issues, "reference_definition_invalid")
            continue
        label = (leading.group("kind"), int(leading.group("index")))
        source_labels = tuple(item for item in labels if item != label)
        if label in definition_labels or len(labels) != len(set(labels)) or (
            label[0] == "Subject"
            and not any(
                kind in {"Picture", "Video"}
                for kind, _index in (*source_labels, *legacy_pictures)
            )
        ):
            _append_once(issues, "reference_definition_invalid")
            continue
        definition_labels.append(label)
        declared_labels.update((*labels, *legacy_pictures))
        if any(
            kind == "Picture" and index > H3_MAX_REFERENCE_PICTURES
            for kind, index in declared_labels
        ):
            _append_once(issues, "reference_picture_out_of_range")
    definition_subjects = [
        index for kind, index in definition_labels if kind == "Subject"
    ]
    if definition_subjects != list(range(1, len(definition_subjects) + 1)):
        _append_once(issues, "reference_definition_invalid")

    retention_labels: list[tuple[str, int]] = []
    retention_text: list[str] = []
    for subject, retain in retention_items:
        retention_label: tuple[str, int] | None = None
        if _has_malformed_reference_label(subject):
            _append_once(issues, "reference_subject_mismatch")
        else:
            label_match = re.match(
                r"^<(?P<kind>Subject|Picture|Video|Audio) "
                r"(?P<index>[1-9][0-9]*)>(?:\s+\S.*)?$",
                subject,
            )
            labels = _reference_labels(subject)
            if label_match is None or len(labels) != 1:
                _append_once(issues, "reference_subject_mismatch")
            else:
                retention_label = (
                    label_match.group("kind"),
                    int(label_match.group("index")),
                )
                retention_labels.append(retention_label)
        audio = retention_label is not None and retention_label[0] == "Audio"
        if parse_h3_retention_relation(retain, audio=audio) is None:
            _append_once(issues, "reference_relation_invalid")
        retention_text.extend((subject, retain))
    if (
        retention_labels != definition_labels
        or len(retention_labels) != len(set(retention_labels))
    ):
        _append_once(issues, "reference_subject_mismatch")

    if _has_malformed_reference_label(detailed_description):
        _append_once(issues, "reference_label_invalid")
    active_labels = set(_reference_labels(detailed_description))
    if not set(definition_labels).issubset(active_labels):
        _append_once(issues, "reference_subject_inactive")
    if any(
        kind == "Subject" for kind, _index in active_labels - declared_labels
    ):
        _append_once(issues, "reference_subject_inactive")
    if not active_labels.issubset(declared_labels):
        _append_once(issues, "reference_label_undefined")

    all_text = "\n".join(
        (subject_definitions, detailed_description, *retention_text, *additional_text)
    )
    if _has_malformed_reference_label(all_text):
        _append_once(issues, "reference_label_invalid")
    used_labels = set(_reference_labels(all_text))
    undefined_labels = used_labels - declared_labels
    if any(kind == "Picture" for kind, _index in undefined_labels):
        _append_once(issues, "reference_picture_out_of_range")
    if any(kind != "Picture" for kind, _index in undefined_labels):
        _append_once(issues, "reference_label_undefined")
    return tuple(issues)


def _reference_labels(value: str) -> tuple[tuple[str, int], ...]:
    return tuple(
        (match.group("kind"), int(match.group("index")))
        for match in _REFERENCE_TAG_PATTERN.finditer(value)
    )


def _has_malformed_reference_label(value: str) -> bool:
    return any(
        ("<" in match.group(0) or ">" in match.group(0))
        and _REFERENCE_TAG_PATTERN.fullmatch(match.group(0)) is None
        for match in _REFERENCE_LIKE_PATTERN.finditer(value)
    )


def _append_once(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


class H3RetentionItem(BaseModel):
    model_config = _MODEL_CONFIG

    subject: _NonEmptyString
    retain: _NonEmptyString

    @model_validator(mode="after")
    def validate_relation(self) -> Self:
        label = _REFERENCE_TAG_PATTERN.match(self.subject)
        is_audio = label is not None and label.group("kind") == "Audio"
        if parse_h3_retention_relation(self.retain, audio=is_audio) is None:
            raise ValueError("reference_relation_invalid")
        return self


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
