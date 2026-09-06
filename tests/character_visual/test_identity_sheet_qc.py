import importlib
import json
from pathlib import Path

import pytest


def _qc_module():
    return importlib.import_module("novelvideo.character_visual.identity_sheet_qc")


@pytest.mark.asyncio
async def test_qc_uses_shared_vision_gateway_and_parses_fenced_json(monkeypatch):
    qc = _qc_module()
    captured = {}

    async def fake_call(**kwargs):
        captured.update(kwargs)
        return "vision-model", """```json
        {
          "front_face_detected": false,
          "back_face_visible": false,
          "portrait_too_small": false,
          "state_inconsistent": false,
          "non_neutral_presentation": false,
          "style_mismatch": false,
          "dead_eyes": false,
          "unnatural_skin_texture": false,
          "plastic_material": false
        }
        ```"""

    monkeypatch.setattr(qc, "call_freezone_vision_model", fake_call)

    report = await qc.assess_identity_sheet_quality(
        image_data=b"sheet",
        media_type="image/webp",
        style="anime_2d",
        model_override="vision-model",
    )

    assert report.passed is True
    assert report.issues == []
    assert report.style_family.value == "2d"
    assert captured["model_override"] == "vision-model"
    assert len(captured["images"]) == 1
    assert captured["images"][0].data == b"sheet"
    assert captured["images"][0].media_type == "image/webp"
    assert "identity sheet" in captured["prompt"].lower()
    assert "2D" in captured["prompt"]
    assert "不要求毛孔" in captured["prompt"]
    qc_prompt = captured["prompt"].lower()
    assert "complete head" in qc_prompt
    assert "hair outline" in qc_prompt
    assert "facial features" in qc_prompt
    assert "head safety zone" not in qc_prompt
    assert "any face, facial feature, head" not in qc_prompt
    assert "unique visible face source" in qc_prompt
    assert "turned face" in qc_prompt
    assert "reflected face" in qc_prompt
    assert "text, labels, watermark" in qc_prompt
    assert "extra faces" in qc_prompt


@pytest.mark.asyncio
async def test_qc_returns_stable_issue_codes_from_model_checks(monkeypatch):
    qc = _qc_module()
    payload = {
        "front_face_detected": True,
        "back_face_visible": True,
        "portrait_too_small": True,
        "state_inconsistent": True,
        "non_neutral_presentation": True,
        "style_mismatch": True,
        "dead_eyes": True,
        "unnatural_skin_texture": True,
        "plastic_material": True,
    }

    async def fake_call(**_kwargs):
        return "vision-model", json.dumps(payload)

    monkeypatch.setattr(qc, "call_freezone_vision_model", fake_call)

    report = await qc.assess_identity_sheet_quality(
        image_data=b"sheet", style="realistic_3d"
    )

    assert report.passed is False
    assert report.style_family.value == "3d_realistic"
    assert report.issues == [
        "front_face_detected",
        "back_face_visible",
        "portrait_too_small",
        "state_inconsistent",
        "non_neutral_presentation",
        "style_mismatch",
        "dead_eyes",
        "unnatural_skin_texture",
        "plastic_material",
    ]
    assert report.checks["front_face_detected"] is True
    assert report.checks["portrait_too_small"] is True


@pytest.mark.asyncio
async def test_qc_ignores_provider_metadata_outside_stable_check_codes(monkeypatch):
    qc = _qc_module()
    payload = {
        "front_face_detected": False,
        "back_face_visible": False,
        "portrait_too_small": False,
        "state_inconsistent": False,
        "non_neutral_presentation": False,
        "style_mismatch": False,
        "dead_eyes": False,
        "unnatural_skin_texture": False,
        "plastic_material": False,
        "summary": "looks acceptable",
    }

    async def fake_call(**_kwargs):
        return "vision-model", f"Analysis follows:\n{json.dumps(payload)}"

    monkeypatch.setattr(qc, "call_freezone_vision_model", fake_call)

    report = await qc.assess_identity_sheet_quality(
        image_data=b"sheet", style="anime_2d"
    )

    assert report.passed is True
    assert set(report.checks) == set(payload) - {"summary"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        "not json",
        '{"front_face_detected": "yes"}',
        '{"front_face_detected": true}',
    ],
)
async def test_qc_malformed_or_incomplete_response_is_unavailable(
    monkeypatch, response
):
    qc = _qc_module()

    async def fake_call(**_kwargs):
        return "vision-model", response

    monkeypatch.setattr(qc, "call_freezone_vision_model", fake_call)

    report = await qc.assess_identity_sheet_quality(
        image_data=b"sheet", style="stylized_2_5d"
    )

    assert report.passed is False
    assert report.issues == ["qc_unavailable"]
    assert report.checks == {"qc_unavailable": False}
    assert report.style_family.value == "2.5d"


@pytest.mark.asyncio
async def test_qc_gateway_failure_is_unavailable(monkeypatch):
    qc = _qc_module()

    async def fake_call(**_kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(qc, "call_freezone_vision_model", fake_call)

    report = await qc.assess_identity_sheet_quality(
        image_data=b"sheet", style="pixar_like_3d"
    )

    assert report.passed is False
    assert report.issues == ["qc_unavailable"]
    assert report.style_family.value == "3d_stylized"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("style", "resolved_value"),
    [
        ("chinese_period_drama", "3d_realistic"),
        ("ink_wash", "2d"),
        ("my_custom_style", "2.5d"),
    ],
)
async def test_qc_resolves_preset_and_custom_style_with_project_dir(
    monkeypatch, tmp_path, style, resolved_value
):
    qc = _qc_module()
    captured = {}

    def fake_resolve(style_arg, project_dir=None):
        captured["style"] = style_arg
        captured["project_dir"] = project_dir
        return qc.IdentitySheetStyleFamily(resolved_value)

    async def fake_call(**_kwargs):
        return "vision-model", json.dumps(
            {
                "front_face_detected": False,
                "back_face_visible": False,
                "portrait_too_small": False,
                "state_inconsistent": False,
                "non_neutral_presentation": False,
                "style_mismatch": False,
                "dead_eyes": False,
                "unnatural_skin_texture": False,
                "plastic_material": False,
            }
        )

    monkeypatch.setattr(qc, "resolve_identity_sheet_style_family", fake_resolve)
    monkeypatch.setattr(qc, "call_freezone_vision_model", fake_call)

    project_dir = Path(tmp_path)
    report = await qc.assess_identity_sheet_quality(
        image_data=b"sheet",
        style=style,
        project_dir=project_dir,
    )

    assert report.style_family.value == resolved_value
    assert captured == {"style": style, "project_dir": project_dir}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("style", "required_prompt", "forbidden_prompt"),
    [
        ("stylized_2_5d", "自然材质", "不要求毛孔"),
        ("realistic_3d", "自然材质", "不要求毛孔"),
        ("pixar_like_3d", "遵循既定风格", "不要求毛孔"),
    ],
)
async def test_qc_prompt_applies_style_specific_material_policy(
    monkeypatch, style, required_prompt, forbidden_prompt
):
    qc = _qc_module()
    captured = {}

    async def fake_call(**kwargs):
        captured.update(kwargs)
        return "vision-model", json.dumps(
            {
                "front_face_detected": False,
                "back_face_visible": False,
                "portrait_too_small": False,
                "state_inconsistent": False,
                "non_neutral_presentation": False,
                "style_mismatch": False,
                "dead_eyes": False,
                "unnatural_skin_texture": False,
                "plastic_material": False,
            }
        )

    monkeypatch.setattr(qc, "call_freezone_vision_model", fake_call)
    await qc.assess_identity_sheet_quality(image_data=b"sheet", style=style)

    assert required_prompt in captured["prompt"]
    assert forbidden_prompt not in captured["prompt"]
    assert "死眼" in captured["prompt"]
    assert "塑料" in captured["prompt"]


@pytest.mark.asyncio
async def test_qc_rejects_empty_image_before_calling_gateway(monkeypatch):
    qc = _qc_module()
    called = False

    async def fake_call(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not call gateway")

    monkeypatch.setattr(qc, "call_freezone_vision_model", fake_call)

    with pytest.raises(ValueError, match="image_data"):
        await qc.assess_identity_sheet_quality(image_data=b"", style="2D")

    assert called is False
