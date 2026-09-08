"""Read-only visual quality control for generated character identity sheets."""

from __future__ import annotations

import json
from pathlib import Path
import re

from pydantic import BaseModel

from novelvideo.freezone.vision_gateway import (
    VisionInput,
    call_freezone_vision_model,
)

from .identity_sheet import (
    IdentitySheetQualityReport,
    IdentitySheetStyleFamily,
    resolve_identity_sheet_style_family,
)


_ISSUE_CODES = (
    "front_face_detected",
    "back_face_visible",
    "portrait_too_small",
    "state_inconsistent",
    "non_neutral_presentation",
    "style_mismatch",
    "dead_eyes",
    "unnatural_skin_texture",
    "plastic_material",
    "portrait_face_occluded",
    "panel_boundary_intrusion",
    "body_cropped",
)

_SAFE_DIAGNOSTIC_VALUE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_TECHNICAL_ERROR_LIMIT = 240


class _IdentitySheetQcChecks(BaseModel):
    front_face_detected: bool
    back_face_visible: bool
    portrait_too_small: bool
    state_inconsistent: bool
    non_neutral_presentation: bool
    style_mismatch: bool
    dead_eyes: bool
    unnatural_skin_texture: bool
    plastic_material: bool
    portrait_face_occluded: bool
    panel_boundary_intrusion: bool
    body_cropped: bool


def _material_policy(style_family: IdentitySheetStyleFamily) -> str:
    if style_family == IdentitySheetStyleFamily.TWO_D:
        return (
            "这是 2D 风格：遵循既定平面绘画语言，不要求毛孔；不要因缺少写实皮肤"
            "细节而判定 unnatural_skin_texture 或 plastic_material。"
        )
    if style_family in {
        IdentitySheetStyleFamily.TWO_POINT_FIVE_D,
        IdentitySheetStyleFamily.THREE_D_REALISTIC,
    }:
        return (
            "这是 2.5D 或写实 3D 风格：检查皮肤、头发和服装是否具有自然材质，"
            "拒绝蜡像感、涂抹感和错误塑料感。"
        )
    return (
        "这是非写实风格化 3D：材质判断应遵循既定风格，但仍须拒绝死眼和不符合"
        "该风格的错误塑料感。"
    )


def _build_prompt(*, style: str, style_family: IdentitySheetStyleFamily) -> str:
    fields = ", ".join(_ISSUE_CODES)
    return f"""You are reviewing one character identity sheet image. This is read-only
visual analysis; do not generate or edit an image.

Project style: {style}
Style family: {style_family.value}
{_material_policy(style_family)}

The RIGHT 30% large three-quarter portrait must be the unique visible face.
portrait_face_occluded is a defect when hands, arms, weapons, tools, clothing, hair, or props
cover any eyes, nose, mouth, jawline, or the recognizable facial contour in that panel.
Limited unobstructed shoulder and neck visibility is valid.

panel_boundary_intrusion is a defect when a person or any body part crosses the neutral
gutter or either panel boundary at 40% and 70% of image width. Each view must remain fully contained
inside its own panel. body_cropped is a defect when the front or back full-body view is cut
by its panel boundary or the outer image edge, including any missing body, hand, leg, foot, or sole,
or a missing back-view head or hair. The intentionally absent front-view head is valid.

The LEFT 40% headless front full-body panel must show a clean collar and shoulder boundary
with no head, hair, ears, or face. front_face_detected is a defect when any head, hair, ear, face,
facial feature, mannequin head, blank face, mask, helmet, dark void, wound, hole, gore, or exposed
anatomy appears above or replaces that boundary. The CENTER 30% back full-body panel must preserve
the back of the head, hair, and neck and face fully away: back_face_visible is a defect when it
contains any turned face, profile,
reflected face, mirror face, or other visible facial detail.
Reject text, labels, watermark, poster markings, and extra faces anywhere on the sheet;
report the closest stable layout defect code (front_face_detected, back_face_visible,
or non_neutral_presentation) rather than inventing a new code.

Also check that the portrait is large enough to judge, all views depict the same
identity and outfit state, and the presentation is neutral rather than an action pose
or dramatic scene. Reject mismatch with the stated style, dead eyes, unnatural skin
texture, and plastic material according to the style policy above.
所有风格都必须拒绝死眼和不符合该风格的错误塑料感。

Return exactly one JSON object containing all twelve boolean keys: {fields}.
Every key is a defect flag: true means the named defect is present and false means it
is absent. Do not add prose or markdown.
"""


def _extract_checks(text: str | _IdentitySheetQcChecks) -> dict[str, bool]:
    """Extract one complete, strictly boolean QC object from provider text."""

    if isinstance(text, _IdentitySheetQcChecks):
        return text.model_dump()

    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        if not set(_ISSUE_CODES).issubset(value):
            continue
        if not all(type(value[code]) is bool for code in _ISSUE_CODES):
            continue
        return {code: value[code] for code in _ISSUE_CODES}
    raise ValueError("vision response does not contain a complete QC object")


def _safe_technical_error(error: Exception) -> str:
    """Return only an allowlisted exception type; never inspect the instance."""

    raw_error_type = type(error).__name__
    error_type = (
        raw_error_type
        if _SAFE_DIAGNOSTIC_VALUE.fullmatch(raw_error_type)
        else "Exception"
    )
    return error_type[:_TECHNICAL_ERROR_LIMIT]


def _unavailable_report(
    style_family: IdentitySheetStyleFamily,
    error: Exception,
) -> IdentitySheetQualityReport:
    return IdentitySheetQualityReport(
        passed=False,
        checks={"qc_unavailable": False},
        issues=["qc_unavailable"],
        style_family=style_family,
        technical_error=_safe_technical_error(error),
    )


async def assess_identity_sheet_quality(
    *,
    image_data: bytes,
    style: str,
    project_dir: str | Path | None = None,
    media_type: str = "image/png",
    model_override: str | None = None,
    timeout_seconds: float = 120.0,
) -> IdentitySheetQualityReport:
    """Analyze an identity sheet using the shared Freezone vision gateway.

    Provider and schema failures are represented by ``qc_unavailable`` so callers
    get a stable, fail-closed result instead of provider-specific exceptions.
    """

    if not image_data:
        raise ValueError("image_data must not be empty")

    style_family = resolve_identity_sheet_style_family(style, project_dir=project_dir)
    try:
        _model, response = await call_freezone_vision_model(
            prompt=_build_prompt(style=style, style_family=style_family),
            images=[VisionInput(data=image_data, media_type=media_type)],
            model_override=model_override,
            timeout_seconds=timeout_seconds,
            output_type=_IdentitySheetQcChecks,
        )
        checks = _extract_checks(response)
    except Exception as error:
        return _unavailable_report(style_family, error)

    issues = [code for code in _ISSUE_CODES if checks[code]]
    return IdentitySheetQualityReport(
        passed=not issues,
        checks=checks,
        issues=issues,
        style_family=style_family,
    )


__all__ = ["assess_identity_sheet_quality"]
