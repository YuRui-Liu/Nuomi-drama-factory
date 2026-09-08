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
    integrated_multimodal_description: _NonEmptyString
    overall_soundscape: _NonEmptyString
    non_diegetic_music: _NonEmptyString

    @model_validator(mode="after")
    def validate_image_alignment(self) -> Self:
        description = self.integrated_multimodal_description
        if self.mode in {H3Mode.I2VA, H3Mode.FL2VA}:
            if "provided first image" not in description:
                raise ValueError(
                    f"{self.mode.value} description must include provided first image"
                )
        if self.mode in {H3Mode.FL2VA, H3Mode.L2VA}:
            if "provided last image" not in description:
                raise ValueError(
                    f"{self.mode.value} description must include provided last image"
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

    return "\n\n".join(f"{name}:\n{value}" for name, value in sections)
