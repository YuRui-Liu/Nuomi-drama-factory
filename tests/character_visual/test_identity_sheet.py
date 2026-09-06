from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from PIL import Image

from novelvideo.character_visual.identity_sheet import (
    IDENTITY_SHEET_LAYOUT_VERSION,
    IDENTITY_SHEET_PANEL_LAYOUT,
    IdentitySheetStyleFamily,
    build_identity_sheet_v2_prompt,
    classify_identity_sheet_style,
    compose_identity_sheet_v2,
    resolve_identity_sheet_style_family,
)


def test_identity_sheet_v2_contract_and_style_families() -> None:
    assert IDENTITY_SHEET_LAYOUT_VERSION == "identity_sheet_v2"
    assert IDENTITY_SHEET_PANEL_LAYOUT == (
        "portrait_3q",
        "front_headless",
        "back_fullbody",
    )
    assert classify_identity_sheet_style("anime_2d") is IdentitySheetStyleFamily.TWO_D
    assert (
        classify_identity_sheet_style("stylized_2_5d")
        is IdentitySheetStyleFamily.TWO_POINT_FIVE_D
    )
    assert (
        classify_identity_sheet_style("realistic_3d")
        is IdentitySheetStyleFamily.THREE_D_REALISTIC
    )
    assert (
        classify_identity_sheet_style("pixar_like_3d")
        is IdentitySheetStyleFamily.THREE_D_STYLIZED
    )
    assert (
        classify_identity_sheet_style(IdentitySheetStyleFamily.TWO_D)
        is IdentitySheetStyleFamily.TWO_D
    )


def test_style_family_resolver_uses_real_preset_metadata() -> None:
    assert (
        resolve_identity_sheet_style_family("chinese_period_drama")
        is IdentitySheetStyleFamily.THREE_D_REALISTIC
    )


def test_style_family_resolver_uses_2d_heuristic_only_for_unknown_style() -> None:
    assert (
        resolve_identity_sheet_style_family("ink_wash")
        is IdentitySheetStyleFamily.TWO_D
    )


def test_style_family_resolver_reads_custom_style_instructions(monkeypatch) -> None:
    from types import SimpleNamespace

    from novelvideo.services.style_service import StyleService

    custom = SimpleNamespace(
        style_family="animation",
        animation_subtype="",
        style_instructions="Semi-realistic 2.5D ink painting with layered materials",
    )
    monkeypatch.setattr(
        StyleService,
        "get_style",
        lambda style, **kwargs: custom if style == "my_custom_style" else None,
    )

    assert (
        resolve_identity_sheet_style_family("my_custom_style", project_dir="/project")
        is IdentitySheetStyleFamily.TWO_POINT_FIVE_D
    )


def _prompt(project_style: str) -> str:
    return build_identity_sheet_v2_prompt(
        character_name="林昭",
        character_tag="[LinZ]",
        appearance="深蓝长袍",
        project_style=project_style,
        style_instructions="preserve the configured rendering language",
        avoid_instructions="no text",
        ethnicity="Chinese",
        has_costume_reference=False,
    )


def test_v2_prompt_has_one_face_source_and_no_cinematic_baking() -> None:
    prompt = _prompt("anime_2d")

    assert "LARGE THREE-QUARTER PORTRAIT" in prompt
    assert "FACELESS FRONT FULL BODY" in prompt
    assert "BACK FULL BODY" in prompt
    assert "the only visible face source" in prompt
    assert "complete head, hairstyle and hair outline" in prompt
    assert "no identifiable facial features" in prompt
    assert "no wound, blood, gore, or horror" in prompt
    assert "neutral gray background" in prompt
    assert "no film grain" in prompt
    assert "no cinematic lens" in prompt
    assert "visible pores" not in prompt
    assert "HEADLESS" not in prompt
    assert "neck up empty" not in prompt
    assert "FRONT VIEW" not in prompt
    assert "SIDE VIEW" not in prompt


def test_v2_prompt_drops_scene_and_camera_fragments_from_dynamic_inputs() -> None:
    prompt = build_identity_sheet_v2_prompt(
        character_name="林昭",
        character_tag="[LinZ]",
        appearance=(
            "深蓝长袍。golden-hour street scene。85mm lens close-up。"
            "dramatic rim lighting。teal environment color cast"
        ),
        project_style="anime_2d",
        style_instructions=(
            "clean ink lines; add film grain; cinematic lens; flat cel colors; depth of field"
        ),
        avoid_instructions="no text; rainy environment; no watermark",
        ethnicity="Chinese",
        has_costume_reference=False,
    )

    assert "深蓝长袍" in prompt
    assert "clean ink lines" in prompt
    assert "flat cel colors" in prompt
    assert "no text" in prompt and "no watermark" in prompt
    for injected in (
        "golden-hour street scene",
        "85mm lens close-up",
        "dramatic rim lighting",
        "teal environment color cast",
        "add film grain",
        "rainy environment",
    ):
        assert injected not in prompt


def test_v2_prompt_preserves_decimal_style_token_and_classifies_raw_style() -> None:
    prompt = build_identity_sheet_v2_prompt(
        character_name="林昭",
        character_tag="[LinZ]",
        appearance="深蓝长袍",
        project_style="stylized 2.5D",
        style_instructions="painterly 2.5D materials",
        avoid_instructions="no text",
        ethnicity="Chinese",
        has_costume_reference=False,
    )

    assert "stylized 2.5D" in prompt
    assert "painterly 2.5D materials" in prompt
    assert "natural material response, lively eye highlights" in prompt


def test_v2_prompt_removes_directing_phrases_without_dropping_character_facts() -> None:
    prompt = build_identity_sheet_v2_prompt(
        character_name="林昭",
        character_tag="[LinZ]",
        appearance=(
            "embroidered blue coat with dramatic rim lighting; "
            "background-pattern silk robe; scene-stealing red scarf"
        ),
        project_style="anime_2d",
        style_instructions="flat cel colors with add film grain",
        avoid_instructions="no text",
        ethnicity="Chinese",
        has_costume_reference=False,
    )

    assert "embroidered blue coat" in prompt
    assert "background-pattern silk robe" in prompt
    assert "scene-stealing red scarf" in prompt
    assert "flat cel colors" in prompt
    assert "dramatic rim lighting" not in prompt
    assert "add film grain" not in prompt


@pytest.mark.parametrize(
    ("project_style", "required", "forbidden"),
    [
        ("anime_2d", "linework, color blocks, hair edges, and pupil highlights", "skin pores"),
        ("stylized_2_5d", "natural material response, lively eye highlights", "visible pores"),
        ("realistic_3d", "credible skin and fabric detail", "flat color blocks"),
        ("pixar_like_3d", "configured stylized modeling and rendering language", "human skin pores"),
    ],
)
def test_v2_prompt_uses_style_aware_quality_language(
    project_style: str,
    required: str,
    forbidden: str,
) -> None:
    prompt = _prompt(project_style)

    assert required in prompt
    assert forbidden not in prompt


def test_legacy_prompt_builder_is_a_v2_compatible_wrapper() -> None:
    from novelvideo.generators.nanobanana_character import (
        CHARACTER_STATE_PANEL_LAYOUT,
        build_character_state_sheet_prompt,
    )

    prompt = build_character_state_sheet_prompt(
        character_name="林昭",
        character_tag="[LinZ]",
        appearance="深蓝长袍",
        style_instructions="anime linework",
        avoid_instructions="no text",
        ethnicity="Chinese",
        has_costume_reference=True,
        medium="2D animation",
        project_style="anime_2d",
    )

    assert CHARACTER_STATE_PANEL_LAYOUT == IDENTITY_SHEET_PANEL_LAYOUT
    assert "Identity Sheet v2" in prompt
    assert "COSTUME REFERENCE" in prompt
    assert "SIDE VIEW" not in prompt


def _solid(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> Path:
    Image.new("RGB", size, color).save(path)
    return path


def _candidate(path: Path) -> Path:
    image = Image.new("RGB", (800, 600), (128, 128, 128))
    image.paste((210, 30, 40), (0, 0, 400, 600))
    image.paste((30, 210, 40), (400, 100, 600, 600))
    image.paste((30, 40, 210), (600, 100, 800, 600))
    image.save(path)
    return path


def test_compose_v2_uses_confirmed_portrait_and_preserves_body_panels(
    tmp_path: Path,
) -> None:
    candidate = _candidate(tmp_path / "candidate.png")
    portrait = _solid(tmp_path / "portrait.png", (320, 480), (12, 34, 56))
    output = tmp_path / "sheet.png"
    candidate_before = candidate.read_bytes()
    portrait_before = portrait.read_bytes()

    result = compose_identity_sheet_v2(candidate, portrait, output)

    with Image.open(output) as opened:
        image = opened.convert("RGB")
        assert image.size == (1536, 1024)
        assert image.getpixel((384, 512)) == (12, 34, 56)
        assert image.getpixel((900, 40)) == (128, 128, 128)
        assert image.getpixel((900, 300)) == (30, 210, 40)
        assert image.getpixel((1300, 512)) == (30, 40, 210)
        assert not opened.getexif()

    assert result.output_path == output
    assert result.panel_bounds == {
        "portrait_3q": (0, 0, 768, 1024),
        "front_headless": (768, 0, 1152, 1024),
        "back_fullbody": (1152, 0, 1536, 1024),
    }
    assert candidate.read_bytes() == candidate_before
    assert portrait.read_bytes() == portrait_before
    assert "generator" not in inspect.signature(compose_identity_sheet_v2).parameters
    assert "client" not in inspect.signature(compose_identity_sheet_v2).parameters


def test_compose_v2_preserves_front_head_region_without_masking(tmp_path: Path) -> None:
    candidate_image = Image.new("RGB", (800, 600), (128, 128, 128))
    candidate_image.paste((210, 30, 40), (0, 0, 400, 600))
    candidate_image.paste((30, 210, 40), (400, 0, 600, 600))
    candidate_image.paste((30, 40, 210), (600, 0, 800, 600))
    candidate = tmp_path / "candidate.png"
    candidate_image.save(candidate)
    portrait = _solid(tmp_path / "portrait.png", (64, 64), (12, 34, 56))
    output = tmp_path / "sheet.png"

    compose_identity_sheet_v2(candidate, portrait, output)

    image = Image.open(output).convert("RGB")
    assert image.getpixel((900, 40)) == (30, 210, 40)


def _assert_relaxed_portrait_framing(prompt: str) -> None:
    assert "face-only" not in prompt.lower()
    assert (
        "complete hairstyle and top of head, both ears, full face, entire chin, "
        "and complete neck"
    ) in prompt
    assert "small upper-shoulder outline" in prompt
    assert "bottom 10-15% of the frame" in prompt
    assert (
        "No chest, lower shoulders, torso, large clothing areas, hands, or props visible"
        in prompt
    )
    assert "TIGHT HEAD-AND-FACE CLOSE-UP" not in prompt
    assert "Frame ends immediately below the chin" not in prompt
    assert "No neck, shoulders, chest, clothing, hands, or props visible" not in prompt


def test_portrait_prompts_use_relaxed_identity_presentation(monkeypatch) -> None:
    from novelvideo.generators.nanobanana_character import NanoBananaCharacterGenerator

    generator = NanoBananaCharacterGenerator.__new__(NanoBananaCharacterGenerator)

    def build(family: str) -> str:
        monkeypatch.setattr(
            "novelvideo.generators.nanobanana_character.StyleService.get_style_branch",
            lambda *_args, **_kwargs: (family, "2d"),
        )
        return generator._build_character_prompt(
            character_name="林昭",
            character_prompt="细长眼睛",
            character_tag="[LinZ]",
            style_name="anime_2d" if family == "animation" else "realistic_3d",
            project_dir="",
            style_keywords="configured style",
            negative_keywords="no text",
        )

    for prompt in (build("animation"), build("live_action")):
        _assert_relaxed_portrait_framing(prompt)
        assert "flat, even, neutral lighting" in prompt
        assert "subtle natural facial asymmetry" in prompt
        assert "natural eye highlights" in prompt
        assert "THREE-QUARTER" not in prompt
        assert "Minimal visible clothing" not in prompt
        assert "Plain simple dark top" not in prompt


@pytest.mark.asyncio
async def test_character_portrait_generation_requests_square_images(
    monkeypatch, tmp_path: Path
) -> None:
    from novelvideo.generators.nanobanana_character import NanoBananaCharacterGenerator

    generator = NanoBananaCharacterGenerator.__new__(NanoBananaCharacterGenerator)
    generator.provider = "newapi"
    captured_calls: list[dict] = []

    async def fake_generate_single_image(**kwargs):
        captured_calls.append(kwargs)
        return b"portrait"

    generator._generate_single_image = fake_generate_single_image
    monkeypatch.setattr(
        "novelvideo.generators.nanobanana_character.get_style_preset",
        lambda *_args, **_kwargs: {
            "style_instructions": "configured style",
            "avoid_instructions": "no text",
        },
    )
    monkeypatch.setattr(
        "novelvideo.generators.nanobanana_character.StyleService.get_style_branch",
        lambda style, **_kwargs: (
            ("animation", "2d") if style == "anime_2d" else ("live_action", "")
        ),
    )

    for style in ("anime_2d", "realistic_3d"):
        result = await generator.generate_character_portrait(
            character_name="林昭",
            character_prompt="细长眼睛",
            character_tag="[LinZ]",
            style=style,
            output_dir=str(tmp_path / style / "assets" / "characters"),
        )
        assert result.success is True

    assert [call.get("aspect_ratio") for call in captured_calls] == ["1:1", "1:1"]


@pytest.mark.asyncio
async def test_legacy_composite_prompt_uses_relaxed_portrait_framing(
    monkeypatch,
) -> None:
    from novelvideo.generators.nanobanana_character import NanoBananaCharacterGenerator

    generator = NanoBananaCharacterGenerator.__new__(NanoBananaCharacterGenerator)
    generator.provider = "newapi"
    captured_prompt = ""

    async def fake_generate_single_image(**kwargs):
        nonlocal captured_prompt
        captured_prompt = kwargs["prompt"]
        return b"candidate"

    generator._generate_single_image = fake_generate_single_image
    monkeypatch.setattr(
        "novelvideo.generators.nanobanana_character.get_style_preset",
        lambda *_args, **_kwargs: {
            "style_instructions": "configured style",
            "avoid_instructions": "no text",
        },
    )

    result = await generator.generate_composite_reference(
        character_name="林昭",
        character_prompt="细长眼睛",
        character_tag="[LinZ]",
        style="anime_2d",
    )

    assert result.success is True
    assert "Create a 1:2 aspect ratio image divided into TWO EQUAL halves" in captured_prompt
    _assert_relaxed_portrait_framing(captured_prompt)


@pytest.mark.asyncio
async def test_identity_generation_requires_portrait_before_model_call(
    monkeypatch, tmp_path: Path
) -> None:
    from novelvideo.generators.nanobanana_character import NanoBananaCharacterGenerator

    generator = NanoBananaCharacterGenerator.__new__(NanoBananaCharacterGenerator)
    generator.provider = "newapi"
    generator.model = "model"
    generator.api_key = "key"
    generator.base_url = ""
    called = False

    async def fake_generate(**_kwargs):
        nonlocal called
        called = True
        return b"candidate"

    generator._generate_with_reference = fake_generate
    result = await generator.generate_identity_with_reference(
        character_name="林昭",
        identity_prompt="深蓝长袍",
        reference_image_path=str(tmp_path / "missing.png"),
        output_path=str(tmp_path / "sheet.png"),
        style="anime_2d",
    )

    assert result.success is False
    assert "Portrait" in (result.error or "")
    assert called is False


@pytest.mark.asyncio
async def test_identity_generation_composes_raw_candidate_once(
    monkeypatch, tmp_path: Path
) -> None:
    from novelvideo.generators.nanobanana_character import NanoBananaCharacterGenerator

    generator = NanoBananaCharacterGenerator.__new__(NanoBananaCharacterGenerator)
    generator.provider = "newapi"
    generator.model = "model"
    generator.api_key = "key"
    generator.base_url = ""
    portrait = _solid(tmp_path / "portrait.png", (320, 480), (12, 34, 56))
    output = tmp_path / "assets" / "characters" / "林昭" / "identities" / "sheet.png"
    calls = 0

    async def fake_generate(**kwargs):
        nonlocal calls
        calls += 1
        _candidate(Path(kwargs["output_path"]))
        return b"candidate"

    generator._generate_with_reference = fake_generate
    monkeypatch.setattr(
        "novelvideo.generators.nanobanana_character.get_style_preset",
        lambda *_args, **_kwargs: {
            "style_instructions": "clean ink lines",
            "avoid_instructions": "no text",
        },
    )
    monkeypatch.setattr(
        "novelvideo.generators.nanobanana_character.StyleService.get_style_branch",
        lambda *_args, **_kwargs: ("animation", "2d"),
    )

    result = await generator.generate_identity_with_reference(
        character_name="林昭",
        identity_prompt="深蓝长袍",
        reference_image_path=str(portrait),
        output_path=str(output),
        style="anime_2d",
    )

    assert result.success is True
    assert calls == 1
    assert output.exists()
    assert output.with_name("sheet_body_temp.png").exists()
    assert Image.open(output).size == (1536, 1024)
    assert Image.open(output).convert("RGB").getpixel((384, 512)) == (12, 34, 56)
