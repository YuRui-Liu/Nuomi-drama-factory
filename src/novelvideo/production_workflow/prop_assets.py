"""Formal prop-asset decisions and deterministic readable-content overlays."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PropAssetReason(str, Enum):
    PLOT_CRITICAL = "plot_critical"
    CLOSE_UP = "close_up"
    MULTI_SHOT = "multi_shot"
    REPEATED_USE = "repeated_use"
    EXACT_CONTENT = "exact_content"


class PropUsage(BaseModel):
    """Facts that determine whether a prop needs a continuity asset."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plot_critical: bool = False
    close_up: bool = False
    shot_count: int = Field(default=1, ge=0)
    repeated_use: bool = False
    requires_exact_content: bool = False


class PropAssetDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    formal_asset_required: bool
    reasons: tuple[PropAssetReason, ...] = ()


class PropAppearanceState(BaseModel):
    """Physical appearance only; readable content belongs to a content layer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prop_id: str = Field(min_length=1)
    state_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    appearance: str = Field(min_length=1)
    condition: str = ""
    asset_path: str = Field(min_length=1)


class NormalizedRegion(BaseModel):
    """Top-left-origin region in normalized output-image coordinates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def stay_inside_output(self) -> NormalizedRegion:
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("normalized region must stay inside the output image")
        return self


class PropContentLayer(BaseModel):
    """Authoritative content rendered after image generation.

    The image model may suggest visual context, but its rendered text or screen
    pixels are never accepted as the authoritative readable result.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    layer_id: str = Field(min_length=1)
    prop_id: str = Field(min_length=1)
    appearance_state_id: str = Field(min_length=1)
    target_region: NormalizedRegion
    exact_text: str | None = None
    rendered_content_path: str | None = None
    font_family: str = Field(default="Noto Sans CJK SC", min_length=1)
    font_size_px: int = Field(default=32, gt=0)
    text_color: str = Field(default="#000000", min_length=1)
    horizontal_alignment: Literal["left", "center", "right"] = "left"
    rotation_degrees: float = 0
    composition_method: Literal["deterministic_post_composite"] = (
        "deterministic_post_composite"
    )
    post_process_required: Literal[True] = True
    image_model_output_is_authoritative: Literal[False] = False

    @model_validator(mode="after")
    def require_one_authoritative_source(self) -> PropContentLayer:
        has_text = self.exact_text is not None and bool(self.exact_text.strip())
        has_rendered_content = self.rendered_content_path is not None and bool(
            self.rendered_content_path.strip()
        )
        if has_text == has_rendered_content:
            raise ValueError(
                "exactly one authoritative content source is required: "
                "exact_text or rendered_content_path"
            )
        return self


def plan_prop_asset(usage: PropUsage) -> PropAssetDecision:
    """Classify a prop deterministically and retain every triggering reason."""

    reasons: list[PropAssetReason] = []
    if usage.plot_critical:
        reasons.append(PropAssetReason.PLOT_CRITICAL)
    if usage.close_up:
        reasons.append(PropAssetReason.CLOSE_UP)
    if usage.shot_count >= 2:
        reasons.append(PropAssetReason.MULTI_SHOT)
    if usage.repeated_use:
        reasons.append(PropAssetReason.REPEATED_USE)
    if usage.requires_exact_content:
        reasons.append(PropAssetReason.EXACT_CONTENT)
    return PropAssetDecision(
        formal_asset_required=bool(reasons),
        reasons=tuple(reasons),
    )


__all__ = [
    "NormalizedRegion",
    "PropAppearanceState",
    "PropAssetDecision",
    "PropAssetReason",
    "PropContentLayer",
    "PropUsage",
    "plan_prop_asset",
]
