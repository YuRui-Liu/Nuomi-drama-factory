from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .h3_prompt import H3Mode


_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True)
_NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
_DurationSeconds = Annotated[float, Field(ge=4, le=15)]


class H3RetentionItem(BaseModel):
    model_config = _MODEL_CONFIG

    subject: _NonEmptyString
    retain: _NonEmptyString


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
        if not description.startswith("[Shot 1]"):
            raise ValueError("description must start with [Shot 1]")
        if self.mode in {H3Mode.FL2VA, H3Mode.L2VA}:
            final_shot = f"[Shot {self.final_shot_number}]"
            if final_shot not in description:
                raise ValueError(
                    f"{self.mode.value} description must include {final_shot}"
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
