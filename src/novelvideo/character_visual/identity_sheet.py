"""Shared contract and deterministic compositor for Identity Sheet v2."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import re
from statistics import median

from PIL import Image
from pydantic import BaseModel, Field


IDENTITY_SHEET_LAYOUT_VERSION = "identity_sheet_v2"
IDENTITY_SHEET_PANEL_LAYOUT = (
    "portrait_3q",
    "front_headless",
    "back_fullbody",
)
IDENTITY_SHEET_SIZE = (1536, 1024)
IDENTITY_SHEET_PANEL_BOUNDS = {
    "portrait_3q": (0, 0, 768, 1024),
    "front_headless": (768, 0, 1152, 1024),
    "back_fullbody": (1152, 0, 1536, 1024),
}
_DYNAMIC_PROMPT_SEPARATOR = re.compile(
    r"(?:[\n\r。!！?？;；]+|(?<!\d)\.(?!\d))"
)
_DIRECTING_PHRASE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:golden[- ]hour\s+)?(?:street|city|forest|room|battlefield)\s+scene\b",
        r"\b\d+(?:\.\d+)?\s*mm\s+lens(?:\s+close[- ]?up)?\b",
        r"\b(?:cinematic\s+lens|depth\s+of\s+field)\b",
        r"\b(?:add\s+)?film\s+grain\b",
        r"\b(?:dramatic\s+)?(?:rim|volumetric)\s+light(?:ing)?\b",
        r"\b(?:[a-z-]+\s+)?environment\s+color\s+cast\b",
        r"\brainy\s+environment\b",
        r"\b(?:apply\s+)?(?:cinematic\s+)?lut\b",
    )
)


class IdentitySheetStyleFamily(StrEnum):
    TWO_D = "2d"
    TWO_POINT_FIVE_D = "2.5d"
    THREE_D_REALISTIC = "3d_realistic"
    THREE_D_STYLIZED = "3d_stylized"


class IdentitySheetQualityReport(BaseModel):
    passed: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    style_family: IdentitySheetStyleFamily


def classify_identity_sheet_style(
    style: str | IdentitySheetStyleFamily,
) -> IdentitySheetStyleFamily:
    """Classify a configured project style without imposing a new style switch."""
    if isinstance(style, IdentitySheetStyleFamily):
        return style
    value = str(style or "").casefold().replace("-", "_").replace(" ", "_")
    if any(token in value for token in ("2_5d", "2.5d", "2½d")):
        return IdentitySheetStyleFamily.TWO_POINT_FIVE_D
    if "3d" in value:
        if any(token in value for token in ("realistic", "photoreal", "写实")):
            return IdentitySheetStyleFamily.THREE_D_REALISTIC
        return IdentitySheetStyleFamily.THREE_D_STYLIZED
    if any(
        token in value
        for token in (
            "anime",
            "illustration",
            "cartoon",
            "ink_wash",
            "inkwash",
            "水墨",
            "2d",
            "二维",
        )
    ):
        return IdentitySheetStyleFamily.TWO_D
    return IdentitySheetStyleFamily.THREE_D_STYLIZED


def resolve_identity_sheet_style_family(
    style: str | IdentitySheetStyleFamily,
    project_dir: str | Path | None = None,
) -> IdentitySheetStyleFamily:
    """Resolve style family from project configuration before using ID heuristics."""
    if isinstance(style, IdentitySheetStyleFamily):
        return style

    from novelvideo.services.style_service import StyleService

    try:
        config = StyleService.get_style(str(style or ""), project_dir=project_dir)
    except Exception:
        config = None
    if config is None:
        return classify_identity_sheet_style(style)

    family = str(getattr(config, "style_family", "") or "").casefold()
    subtype = str(getattr(config, "animation_subtype", "") or "").casefold()
    instructions = str(getattr(config, "style_instructions", "") or "")
    explicit = classify_identity_sheet_style(instructions)
    normalized_instructions = instructions.casefold().replace("-", "_").replace(" ", "_")
    has_2_5d = any(token in normalized_instructions for token in ("2_5d", "2.5d", "2½d"))
    has_3d = "3d" in normalized_instructions
    has_2d = any(
        token in normalized_instructions
        for token in ("2d", "anime", "illustration", "ink_wash", "inkwash", "水墨")
    )

    if subtype == "3d":
        return IdentitySheetStyleFamily.THREE_D_STYLIZED
    if subtype == "2d":
        return IdentitySheetStyleFamily.TWO_D
    if has_2_5d:
        return IdentitySheetStyleFamily.TWO_POINT_FIVE_D
    if family == "animation":
        if has_3d:
            return IdentitySheetStyleFamily.THREE_D_STYLIZED
        if has_2d or subtype in {"", "hybrid"}:
            return IdentitySheetStyleFamily.TWO_D
    if family == "live_action":
        if has_2d and not has_3d:
            return IdentitySheetStyleFamily.TWO_D
        return IdentitySheetStyleFamily.THREE_D_REALISTIC
    return explicit


def style_quality_instructions(family: IdentitySheetStyleFamily) -> str:
    if family is IdentitySheetStyleFamily.TWO_D:
        return (
            "Preserve project-specific linework, color blocks, hair edges, and pupil "
            "highlights; keep the eyes lively without imposing photoreal skin detail."
        )
    if family is IdentitySheetStyleFamily.TWO_POINT_FIVE_D:
        return (
            "Preserve stylized forms with natural material response, lively eye highlights, "
            "subtle facial asymmetry, and no waxy smoothing or plastic skin."
        )
    if family is IdentitySheetStyleFamily.THREE_D_REALISTIC:
        return (
            "Preserve credible skin and fabric detail, natural eye highlights, subtle facial "
            "asymmetry, and non-plastic material response."
        )
    return (
        "Preserve the configured stylized modeling and rendering language with lively eyes "
        "and intentional materials; do not force photoreal skin detail."
    )


def _neutral_dynamic_text(value: str, *, fallback: str = "") -> str:
    """Remove explicit directing phrases while retaining character/style facts."""
    fragments = []
    for raw_fragment in _DYNAMIC_PROMPT_SEPARATOR.split(str(value or "")):
        fragment = raw_fragment
        for pattern in _DIRECTING_PHRASE_PATTERNS:
            fragment = pattern.sub("", fragment)
        fragment = re.sub(r"\s{2,}", " ", fragment).strip(" ,，-")
        if fragment:
            fragments.append(fragment)
    return "; ".join(fragments) or fallback


def build_identity_sheet_v2_prompt(
    *,
    character_name: str,
    character_tag: str,
    appearance: str,
    project_style: str,
    style_instructions: str,
    avoid_instructions: str,
    ethnicity: str,
    has_costume_reference: bool,
    project_dir: str | Path | None = None,
) -> str:
    """Compile the provider-neutral, single-face Identity Sheet v2 prompt."""
    style_family = resolve_identity_sheet_style_family(project_style, project_dir=project_dir)
    appearance = _neutral_dynamic_text(appearance, fallback="No additional state details.")
    project_style = _neutral_dynamic_text(
        project_style, fallback="the configured project visual style"
    )
    style_instructions = _neutral_dynamic_text(
        style_instructions, fallback="preserve the configured rendering language"
    )
    avoid_instructions = _neutral_dynamic_text(
        avoid_instructions, fallback="no additional exclusions"
    )
    costume_block = ""
    if has_costume_reference:
        costume_block = """
COSTUME REFERENCE (CRITICAL):
- Copy clothing, fabric, accessories, colors, and styling from the costume reference.
- Combine the Portrait identity with the costume reference; do not copy its person or face.
"""
    quality = style_quality_instructions(style_family)
    return f"""Identity Sheet v2 for {character_tag} ({character_name}).
Create exactly one 3-panel sheet, LEFT TO RIGHT, with fixed proportional regions:
- LEFT 50%: LARGE THREE-QUARTER PORTRAIT. Use the confirmed Portrait exactly as the identity anchor; this is the only visible face source in the entire sheet.
- CENTER 25%: FACELESS FRONT FULL BODY in a neutral standing pose, fully visible from the complete top of the head to the soles of the feet. Preserve the complete head, hairstyle and hair outline, ears, neck, body proportions, outfit, and footwear without cropping. Render the facial plane as a smooth neutral surface consistent with the project style, with no identifiable facial features: no eyes, eyebrows, nose, lips, beard, or face-like markings. Do not replace the face with a mask, veil, prop, wound, hole, or horror element. This is a clean identity-isolation presentation with no wound, blood, gore, or horror.
- RIGHT 25%: BACK FULL BODY, naturally facing fully away, head to feet. Never turn back; show no profile, visible face, mirror face, or reflection.

IDENTITY AND STATE LOCK:
- All panels depict the same age, body proportions, hair state, outfit, accessories, colors, footwear, and silhouette.
- Portrait is the sole facial identity authority. The two body panels supply body, outfit, and rear-silhouette information only.
- Default ethnicity when unspecified: {ethnicity}.

CHARACTER STATE:
{appearance}
{costume_block}
PROJECT STYLE:
- Preserve the configured project style ({project_style}): {style_instructions}
- {quality}

NEUTRAL PRESENTATION:
- Use one uniform neutral gray background and flat, even, readable lighting across all panels.
- No environment, props, action, text, labels, numbers, watermark, or poster composition.
- no film grain, no cinematic lens, no depth of field, no dramatic lighting, no LUT, and no scene color cast.

ADDITIONAL EXCLUSIONS:
- {avoid_instructions}
- Do not add an extra panel or any additional face.
""".strip()


@dataclass(frozen=True, slots=True)
class IdentitySheetComposition:
    output_path: Path
    neutral_gray: tuple[int, int, int]
    panel_bounds: dict[str, tuple[int, int, int, int]]


def fit_crop(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Resize and center-crop an image to exactly ``size`` without distortion."""
    target_width, target_height = size
    scale = max(target_width / image.width, target_height / image.height)
    resized = image.resize(
        (max(target_width, round(image.width * scale)), max(target_height, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - target_width) // 2
    top = (resized.height - target_height) // 2
    return resized.crop((left, top, left + target_width, top + target_height))


def sample_neutral_gray(image: Image.Image) -> tuple[int, int, int]:
    """Derive a stable neutral fill from small corner samples of the candidate."""
    rgb = image.convert("RGB")
    inset_x = min(max(rgb.width // 100, 0), rgb.width - 1)
    inset_y = min(max(rgb.height // 100, 0), rgb.height - 1)
    samples = (
        rgb.getpixel((inset_x, inset_y)),
        rgb.getpixel((rgb.width - 1 - inset_x, inset_y)),
        rgb.getpixel((inset_x, rgb.height - 1 - inset_y)),
        rgb.getpixel((rgb.width - 1 - inset_x, rgb.height - 1 - inset_y)),
    )
    sampled = tuple(int(median(channel)) for channel in zip(*samples))
    luminance = round(sum(sampled) / 3)
    neutral = min(192, max(96, luminance))
    return neutral, neutral, neutral


def compose_identity_sheet_v2(
    candidate_path: str | Path,
    portrait_path: str | Path,
    output_path: str | Path,
) -> IdentitySheetComposition:
    """Create a v2 sheet using deterministic Pillow resize, crop, and paste operations."""
    candidate_path = Path(candidate_path)
    portrait_path = Path(portrait_path)
    output_path = Path(output_path)
    with Image.open(candidate_path) as candidate_source:
        candidate = candidate_source.convert("RGB")
    with Image.open(portrait_path) as portrait_source:
        portrait = portrait_source.convert("RGB")

    neutral_gray = sample_neutral_gray(candidate)
    canvas = Image.new("RGB", IDENTITY_SHEET_SIZE, neutral_gray)

    portrait_end = candidate.width // 2
    front_end = candidate.width * 3 // 4
    if portrait_end < 1 or front_end <= portrait_end or candidate.width <= front_end:
        raise ValueError("candidate must be wide enough for a 50/25/25 panel layout")
    front_source = candidate.crop((portrait_end, 0, front_end, candidate.height))
    back_source = candidate.crop((front_end, 0, candidate.width, candidate.height))

    portrait_bounds = IDENTITY_SHEET_PANEL_BOUNDS["portrait_3q"]
    front_bounds = IDENTITY_SHEET_PANEL_BOUNDS["front_headless"]
    back_bounds = IDENTITY_SHEET_PANEL_BOUNDS["back_fullbody"]
    canvas.paste(fit_crop(portrait, (768, 1024)), portrait_bounds[:2])
    canvas.paste(fit_crop(front_source, (384, 1024)), front_bounds[:2])
    canvas.paste(fit_crop(back_source, (384, 1024)), back_bounds[:2])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG")
    return IdentitySheetComposition(
        output_path=output_path,
        neutral_gray=neutral_gray,
        panel_bounds=dict(IDENTITY_SHEET_PANEL_BOUNDS),
    )
