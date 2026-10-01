"""Read-only visual quality control for generated character identity sheets."""

from __future__ import annotations

import json
import asyncio
import hashlib
from pathlib import Path
import re

from pydantic import BaseModel, ConfigDict
from novelvideo.text_task_runtime.runtime import StructuredImage, StructuredTextRuntime

from novelvideo.freezone.vision_gateway import (
    VisionInput,
    call_freezone_vision_model,
)

from .identity_sheet import (
    AUTHORED_STATE_POLICY,
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

# Aesthetic judgments remain visible, but cannot veto a usable identity asset.
_ADVISORY_CODES = frozenset({
    "non_neutral_presentation", "style_mismatch", "dead_eyes",
    "unnatural_skin_texture", "plastic_material",
})


class _IdentitySheetQcChecks(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

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
    state_inconsistency_reason: str = ""


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


def _build_prompt(*, style: str, style_family: IdentitySheetStyleFamily,
                  expected_appearance: str = "", nonhuman_species: str = "") -> str:
    fields = ", ".join(_ISSUE_CODES)
    fields += (". Also return state_inconsistency_reason (string): when state_inconsistent "
               "is true, identify the actual view, visible contradictory feature and "
               "the exact expected requirement or contradictory second view. "
               "Use Chinese for this explanation. Do not use a generic mismatch label. "
               "When false, return an empty string")
    region_policy = '''Identify the front, rear and portrait views by their visible contents and
actual visible gutters. The suggested 40/30/30 widths (40% and 70% boundaries) are generation
guidance, not crop coordinates or invisible image edges. Do not mentally crop at those numbers.
Different panel widths alone do not prove any defect. Never count the rear-view head as a front-view head
because it falls inside a nominal percentage region. Judge body_cropped only against the actual
view boundary or outer image edge, and panel_boundary_intrusion only for substantial visible
overlap between views across an actual gutter. Each body must remain complete in its own view.
The front must still be genuinely headless and the rear must include its head; the portrait
must be the only visible face. Unclear boundaries are not proof of a defect.'''
    if nonhuman_species:
        return f"""Review a nonhuman identity sheet, read-only. Species: {nonhuman_species}.
Expected character state: {expected_appearance}
Project style: {style}. {_material_policy(style_family)}
{region_policy}
LEFT front view: complete headless animal body in natural posture; clean non-bloody neck boundary
is intentional, not body_cropped. CENTER rear view: complete rear animal view including head,
fully facing away. RIGHT portrait view: large unobstructed three-quarter portrait, the only visible face.
Check front_face_detected, back_face_visible, portrait_too_small, portrait_face_occluded,
panel_boundary_intrusion and body_cropped against this layout. All limbs and tails must fit.
Set state_inconsistent=true for species/anatomy or expected-state mismatch, even if all panels
agree: anthropomorphic human limbs, upright human posture, invented clothes or shoes are
defects unless the expected state explicitly requires them. Preserve stated fur, tail and markings.
Check non_neutral_presentation for action scenes, text or watermarks; style_mismatch for wrong
rendering style. dead_eyes, unnatural_skin_texture and plastic_material follow the style policy.
Return only JSON with all twelve boolean defect flags: {fields}. True means defect present.
Judge visible practical usability; small gutter variations and uncertain details are not defects.
"""
    return f"""You are reviewing one character identity sheet image. This is read-only
visual analysis; do not generate or edit an image.

Project style: {style}
Expected character state: {expected_appearance}. Report visible contradictions as state_inconsistent.
{AUTHORED_STATE_POLICY}
Style family: {style_family.value}
{_material_policy(style_family)}
{region_policy}

The RIGHT large three-quarter portrait must be the unique visible face.
portrait_face_occluded is a defect when hands, arms, weapons, tools, clothing, hair, or props
cover any eyes, nose, mouth, jawline, or the recognizable facial contour in that panel.
Limited unobstructed shoulder and neck visibility is valid.

panel_boundary_intrusion is a defect when a person or any body part crosses the neutral
gutter or an actual visible panel boundary. Each view must remain fully contained
inside its own panel. body_cropped is a defect when the front or back full-body view is cut
by its panel boundary or the outer image edge, including any missing body, hand, leg, foot, or sole,
or a missing back-view head or hair. The intentionally absent front-view head is valid.

The LEFT headless front full-body panel must show a clean collar and shoulder boundary
with no head, hair, ears, or face. front_face_detected is a defect when any head, hair, ear, face,
facial feature, mannequin head, blank face, mask, helmet, dark void, wound, hole, gore, exposed
bone or internal tissue appears above or replaces that boundary.
A smooth, non-bloody neck cross-section is valid, including visible neck skin above the collar;
do not flag a clean neck cross-section as front_face_detected. The headless front view provides
outfit and body proportions; facial detail belongs exclusively to the portrait. Do not demand
a small head or face on the front body, or mistake its intentional absence for body_cropped.
The CENTER back full-body panel must preserve
the back of the head, hair, and neck and face fully away: back_face_visible is a defect when it
contains any turned face, profile,
reflected face, mirror face, or other visible facial detail.
Flag text, labels, watermark, and poster markings as non_neutral_presentation.
Reject extra faces anywhere on the sheet: report front_face_detected, or
back_face_visible for an additional face in the rear panel. Extra faces must never
be reported solely under the advisory non_neutral_presentation code.

Also check that the portrait is large enough to judge, all views depict the same
identity and outfit state, and the presentation is neutral rather than an action pose
or dramatic scene. Flag mismatch with the stated style, dead eyes, unnatural skin
texture, and plastic material according to the style policy above.
死眼和错误塑料感应作为风格相关的观感建议记录，而不是结构性不合格。

Return exactly one JSON object containing all twelve boolean keys: {fields}.
Each boolean key is a defect flag: true means the named defect is present and false means it
is absent. Do not add prose or markdown.

Judge practical reference usability, not pixel-perfect compliance. Set a defect flag
only when clearly visible; uncertainty or details too small to resolve are not proof.
Use the actual visible regions; panel width and alignment variations alone are valid.
panel_boundary_intrusion requires substantial overlap between views, not a few pixels
near a nominal boundary. Natural bangs or side hair are valid unless they materially
hide recognizable facial features. Do not infer missing soles from a normal standing
view where the underside of the shoes is not visible. Perspective and lighting
differences alone do not establish state_inconsistent.
Material and expression flags are advisory aesthetic observations, evaluated relative
to the project's style, not reasons to demand photorealism from stylized characters.
"""


def _extract_assessment(text: str | _IdentitySheetQcChecks) -> dict:
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
        result = {code: value[code] for code in _ISSUE_CODES}
        reason = value.get("state_inconsistency_reason", "")
        result["state_inconsistency_reason"] = reason if isinstance(reason, str) else ""
        return result
    raise ValueError("vision response does not contain a complete QC object")


def _extract_checks(text: str | _IdentitySheetQcChecks) -> dict[str, bool]:
    result = _extract_assessment(text)
    return {code: result[code] for code in _ISSUE_CODES}


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
        blocking_issues=["qc_unavailable"],
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
    runtime: StructuredTextRuntime | None = None,
    style_family: IdentitySheetStyleFamily | None = None,
    expected_appearance: str = "",
    nonhuman_species: str = "",
) -> IdentitySheetQualityReport:
    """Analyze with an explicit QC runtime, or the legacy shared vision gateway.

    Provider and schema failures are represented by ``qc_unavailable`` so callers
    get a stable, fail-closed result instead of provider-specific exceptions.
    """

    if not image_data:
        raise ValueError("image_data must not be empty")

    style_family = style_family or resolve_identity_sheet_style_family(style, project_dir=project_dir)
    input_snapshot = {}
    try:
        prompt = _build_prompt(style=style, style_family=style_family,
                               expected_appearance=expected_appearance, nonhuman_species=nonhuman_species)
        input_snapshot = dict(prompt=prompt, expected_appearance=expected_appearance,
            image_sha256=hashlib.sha256(image_data).hexdigest(),
            prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
        if runtime is not None:
            response = await asyncio.wait_for(runtime.run_structured(
                prompt=prompt,
                images=[StructuredImage(data=image_data, media_type=media_type)],
                output_type=_IdentitySheetQcChecks,
            ), timeout=timeout_seconds)
            response = _IdentitySheetQcChecks.model_validate(
                response.model_dump() if isinstance(response, BaseModel) else response,
            )
        else:
            _model, response = await call_freezone_vision_model(
                prompt=prompt,
                images=[VisionInput(data=image_data, media_type=media_type)],
                model_override=model_override,
                timeout_seconds=timeout_seconds,
                output_type=_IdentitySheetQcChecks,
            )
        assessment = _extract_assessment(response)
        checks = {code: assessment[code] for code in _ISSUE_CODES}
    except Exception as error:
        return _unavailable_report(style_family, error).model_copy(update={'qc_input_snapshot': input_snapshot})

    issues = [code for code in _ISSUE_CODES if checks[code]]
    blocking_issues = [code for code in issues if code not in _ADVISORY_CODES]
    return IdentitySheetQualityReport(
        passed=not blocking_issues,
        checks=checks,
        issues=issues,
        blocking_issues=blocking_issues,
        warnings=[code for code in issues if code in _ADVISORY_CODES],
        style_family=style_family,
        qc_input_snapshot=input_snapshot,
        evidence=({"state_inconsistent": assessment["state_inconsistency_reason"]}
                  if checks["state_inconsistent"] and assessment.get("state_inconsistency_reason") else {}),
    )


def identity_sheet_qc_policy_fingerprint(runtime: StructuredTextRuntime | None = None) -> str:
    """Fingerprint policy text and effective route for persisted QC provenance."""
    policy = {
        "nonhuman_prompt": _build_prompt(style="", style_family=next(iter(IdentitySheetStyleFamily)), nonhuman_species="animal"),
        "prompts": [_build_prompt(style="", style_family=family) for family in IdentitySheetStyleFamily],
        "advisory_codes": sorted(_ADVISORY_CODES),
        "route": runtime.snapshot.model_dump(mode="json") if runtime is not None else {"runtime": "freezone_gateway"},
    }
    return hashlib.sha256(json.dumps(policy, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


__all__ = ["assess_identity_sheet_quality", "identity_sheet_qc_policy_fingerprint"]
