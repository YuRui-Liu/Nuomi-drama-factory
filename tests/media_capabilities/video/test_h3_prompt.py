import pytest

from novelvideo.media_capabilities.video.h3_prompt import compile_h3, select_mode
from novelvideo.media_capabilities.video.models import H3Mode, MotionSpec


@pytest.mark.parametrize(
    ("first_frame", "last_frame", "references", "expected"),
    [
        ("first.png", None, (), H3Mode.I2VA),
        (None, "last.png", (), H3Mode.L2VA),
        ("first.png", "last.png", (), H3Mode.FL2VA),
        (None, None, ("character.png",), H3Mode.REF2VA),
        (None, None, (), H3Mode.T2VA),
    ],
)
def test_select_mode_uses_exactly_one_supported_input_shape(
    first_frame, last_frame, references, expected
):
    assert select_mode(first_frame, last_frame, references) is expected


@pytest.mark.parametrize(
    ("first_frame", "last_frame"),
    [("first.png", None), (None, "last.png"), ("first.png", "last.png")],
)
def test_reference_mode_rejects_frame_inputs(first_frame, last_frame):
    with pytest.raises(ValueError, match="reference images cannot be combined with frames"):
        select_mode(first_frame, last_frame, ("character.png",))


def test_fl2va_prompt_has_official_section_order_and_original_dialogue():
    spec = MotionSpec(
        action="女孩从门口跑到窗边",
        dialogue="别过来！",
        soundscape="急促脚步",
    )

    prompt = compile_h3(spec, mode=H3Mode.FL2VA)

    assert (
        prompt.index("integrated_multimodal_description")
        < prompt.index("overall_soundscape")
        < prompt.index("non_diegetic_music")
    )
    assert "别过来！" in prompt


def test_ref2va_prompt_has_official_section_order():
    spec = MotionSpec(
        action="女孩转身看向镜头",
        subject_definitions=("女孩：红色风衣，黑色短发",),
        summary="女孩在雨中回头",
        retention_analysis="保留红色风衣和黑色短发",
        soundscape="雨声",
        music="低沉弦乐",
    )

    prompt = compile_h3(spec, mode=H3Mode.REF2VA)

    sections = (
        "subject_definitions",
        "summary",
        "retention_analysis",
        "detailed_description",
        "overall_soundscape",
        "non_diegetic_music",
    )
    assert [prompt.index(section) for section in sections] == sorted(
        prompt.index(section) for section in sections
    )
    for fact in (
        "女孩：红色风衣，黑色短发",
        "女孩在雨中回头",
        "保留红色风衣和黑色短发",
        "女孩转身看向镜头",
        "雨声",
        "低沉弦乐",
    ):
        assert fact in prompt


@pytest.mark.parametrize("mode", list(H3Mode))
def test_compile_h3_is_deterministic_for_every_mode(mode):
    spec = MotionSpec(action="人物抬头", dialogue="看那里。")

    assert compile_h3(spec, mode) == compile_h3(spec, mode)
