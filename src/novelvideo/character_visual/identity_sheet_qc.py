"""Read-only visual quality control for generated character identity sheets."""

from __future__ import annotations

import json
from pathlib import Path

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
)


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

The large three-quarter portrait must be the unique visible face source. The front
full-body panel must keep its head safety zone empty: front_face_detected is a defect
when any face, facial feature, head, or substitute object appears there. The back
full-body panel must face fully away: back_face_visible is a defect when it contains
any turned face, profile, reflected face, mirror face, or other visible facial detail.
Reject text, labels, watermark, poster markings, and extra faces anywhere on the sheet;
report the closest stable layout defect code (front_face_detected, back_face_visible,
or non_neutral_presentation) rather than inventing a new code.

Also check that the portrait is large enough to judge, all views depict the same
identity and outfit state, and the presentation is neutral rather than an action pose
or dramatic scene. Reject mismatch with the stated style, dead eyes, unnatural skin
texture, and plastic material according to the style policy above.
所有风格都必须拒绝死眼和不符合该风格的错误塑料感。

Return exactly one JSON object containing all nine boolean keys: {fields}.
Every key is a defect flag: true means the named defect is present and false means it
is absent. Do not add prose or markdown.
"""


def _extract_checks(text: str) -> dict[str, bool]:
    """Extract one complete, strictly boolean QC object from provider text."""

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


def _unavailable_report(
    style_family: IdentitySheetStyleFamily,
) -> IdentitySheetQualityReport:
    return IdentitySheetQualityReport(
        passed=False,
        checks={"qc_unavailable": False},
        issues=["qc_unavailable"],
        style_family=style_family,
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
        )
        checks = _extract_checks(response)
    except Exception:
        return _unavailable_report(style_family)

    issues = [code for code in _ISSUE_CODES if checks[code]]
    return IdentitySheetQualityReport(
        passed=not issues,
        checks=checks,
        issues=issues,
        style_family=style_family,
    )


__all__ = ["assess_identity_sheet_quality"]
