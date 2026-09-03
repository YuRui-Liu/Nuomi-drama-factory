from __future__ import annotations

from types import SimpleNamespace

import pytest


VALID_PROMPT = """正面：磨砂玻璃双开门居中，门内连接三米宽直走廊，墙上固定电子时钟。
左侧：灰色吸音板墙沿走廊延伸，嵌入两扇隔音门并连接直播间。
右侧：连续观察窗下方固定金属线槽，尽头与设备间防火门相接。
背面：走廊尽头为灰色防火门，旁侧固定配电箱，与左右墙转角闭合。
光源：白色矿棉板吊顶内嵌冷白条形灯，沿走廊中轴连续布置。
材质/风格：灰色环氧地坪、浅灰吸音板墙面、白色矿棉板吊顶、拉丝金属门框。
禁止元素：人物、文字、水印、移动道具、临时剧情状态。"""


LEGACY_META_PROMPT = """正面：以“广播站设备走廊”最能代表地点身份的主入口作为正面；根据原文证据确定固定结构。
左侧：从正面视角向左延伸，布置与场景功能一致的侧墙和通道。
右侧：不要复制正面主体，只做合理连续补全。
背面：可为入口反向或走廊尽端；必须和正面/左右侧构成完整 360 度闭合空间。
光源：使用中性默认状态的稳定环境光。
材质/风格：保持 interior 场景的固定建筑风格。
禁止元素：不出现人物、字幕、水印。"""


def test_quality_gate_accepts_concrete_seven_section_prompt():
    from novelvideo.cognee.pipeline import scene_environment_prompt_issues

    assert scene_environment_prompt_issues(VALID_PROMPT) == []


def test_quality_gate_rejects_known_meta_template():
    from novelvideo.cognee.pipeline import scene_environment_prompt_issues

    issues = scene_environment_prompt_issues(LEGACY_META_PROMPT)

    assert any(item.startswith("meta_instruction:") for item in issues)


def test_quality_gate_rejects_single_meta_instruction_even_with_all_sections():
    from novelvideo.cognee.pipeline import scene_environment_prompt_issues

    prompt = VALID_PROMPT.replace(
        "正面：磨砂玻璃双开门居中，门内连接三米宽直走廊，墙上固定电子时钟。",
        "正面：根据原文证据合理补全最合适的正面。",
    )

    issues = scene_environment_prompt_issues(prompt)

    assert "meta_instruction:根据原文证据" in issues


def test_quality_gate_rejects_vague_directions_and_materials():
    from novelvideo.cognee.pipeline import scene_environment_prompt_issues

    prompt = """正面：墙面。
左侧：通道。
右侧：空间。
背面：入口。
光源：环境光。
材质/风格：现代风格。
禁止元素：人物。"""

    issues = scene_environment_prompt_issues(prompt)

    assert "direction_not_concrete:正面" in issues
    assert "light_source_not_concrete" in issues
    assert "material_not_concrete" in issues


@pytest.mark.parametrize(
    ("replacement", "expected_prefix"),
    [
        ("正面：男人站在玻璃双开门旁挥手，门内连接三米宽直走廊。", "story_contamination:person:"),
        ("正面：玻璃双开门居中，人物正在争吵并推开大门。", "story_contamination:person:"),
        ("正面：玻璃双开门居中，本场出现临时摆放的手机和桌椅。", "story_contamination:transient:"),
        ("正面：深夜暴雨中的玻璃双开门居中，夕阳照进直走廊。", "story_contamination:time_weather:"),
    ],
)
def test_quality_gate_rejects_story_contamination(replacement, expected_prefix):
    from novelvideo.cognee.pipeline import scene_environment_prompt_issues

    issues = scene_environment_prompt_issues(
        VALID_PROMPT.replace(VALID_PROMPT.splitlines()[0], replacement)
    )

    assert any(item.startswith(expected_prefix) for item in issues)


def test_quality_gate_rejects_abstract_direction_contract_and_meta_commands():
    from novelvideo.cognee.pipeline import scene_environment_prompt_issues

    prompt = VALID_PROMPT
    for label in ("正面", "左侧", "右侧", "背面"):
        original = next(line for line in prompt.splitlines() if line.startswith(f"{label}："))
        prompt = prompt.replace(original, f"{label}：该空间沿区域延伸并连接相邻区域，需要合理补全。")

    issues = scene_environment_prompt_issues(prompt)

    assert all(f"direction_not_concrete:{label}" in issues for label in ("正面", "左侧", "右侧", "背面"))
    assert "meta_instruction:需要" in issues


def test_quality_gate_keeps_static_compounds_that_contain_weather_or_request_words():
    from novelvideo.cognee.pipeline import scene_environment_prompt_issues

    prompt = VALID_PROMPT.replace(
        "磨砂玻璃双开门居中，门内连接三米宽直走廊，墙上固定电子时钟。",
        "防暴雨玻璃双开门居中，门内连接三米宽直走廊，墙上固定紧急请求按钮。",
    ).replace(
        "白色矿棉板吊顶内嵌冷白条形灯，沿走廊中轴连续布置。",
        "白色矿棉板吊顶内嵌月光灯，沿走廊中轴连续布置。",
    )

    assert scene_environment_prompt_issues(prompt) == []


def test_quality_gate_rejects_actual_character_names_from_scene_context():
    from novelvideo.cognee.pipeline import scene_environment_prompt_issues

    prompt = VALID_PROMPT.replace(
        VALID_PROMPT.splitlines()[0],
        "正面：王五倚靠在磨砂玻璃双开门旁，门内连接三米宽直走廊。",
    )

    issues = scene_environment_prompt_issues(prompt, characters=["王五"])

    assert "story_contamination:character:王五" in issues


@pytest.mark.parametrize("marker", ["白天", "夜晚", "雨夜", "晴天", "雪夜"])
def test_quality_gate_rejects_time_and_weather_variants(marker):
    from novelvideo.cognee.pipeline import scene_environment_prompt_issues

    prompt = VALID_PROMPT.replace(
        VALID_PROMPT.splitlines()[0],
        f"正面：{marker}的磨砂玻璃双开门居中，门内连接三米宽直走廊。",
    )

    issues = scene_environment_prompt_issues(prompt)

    assert f"story_contamination:time_weather:{marker}" in issues


@pytest.mark.parametrize(
    ("phrase", "marker"),
    [
        ("暴雨笼罩玻璃门", "暴雨"),
        ("夕阳照进走廊", "夕阳"),
        ("月光洒在地面", "月光"),
        ("夜晚灯火映亮大厅", "夜晚"),
        ("晴天映照窗面", "晴天"),
    ],
)
def test_quality_gate_rejects_time_weather_followed_by_natural_predicates(
    phrase, marker
):
    from novelvideo.cognee.pipeline import scene_environment_prompt_issues

    prompt = VALID_PROMPT.replace(
        VALID_PROMPT.splitlines()[0],
        f"正面：{phrase}，门内连接三米宽直走廊。",
    )

    issues = scene_environment_prompt_issues(prompt)

    assert f"story_contamination:time_weather:{marker}" in issues


def test_quality_gate_keeps_static_place_names_that_contain_person_words():
    from novelvideo.cognee.pipeline import scene_environment_prompt_issues

    prompt = VALID_PROMPT.replace(
        "磨砂玻璃双开门居中，门内连接三米宽直走廊，墙上固定电子时钟。",
        "白天鹅宾馆入口的磨砂玻璃双开门居中，门内连接三米宽直走廊。",
    ).replace(
        "吸音板墙面沿通道连续延伸，靠近入口设固定消防柜。",
        "老人活动中心左侧吸音板墙面连续延伸，靠近入口设固定消防柜。",
    ).replace(
        "观察窗嵌在墙面中段，下方固定金属设备柜，与正面门框齐平。",
        "行人天桥观察窗嵌在墙面中段，下方固定金属设备柜。",
    ).replace(
        "灰色防火门位于通道末端，门旁固定配电箱和疏散指示灯。",
        "演员通道末端设置灰色防火门，门旁固定配电箱和疏散指示灯。",
    )

    assert scene_environment_prompt_issues(prompt) == []


def test_quality_gate_rejects_missing_sections():
    from novelvideo.cognee.pipeline import scene_environment_prompt_issues

    issues = scene_environment_prompt_issues(
        "正面：玻璃门连接走廊。\n左侧：吸音板墙。\n右侧：观察窗。\n背面：防火门。"
    )

    assert "missing_section:光源" in issues
    assert "missing_section:材质/风格" in issues
    assert "missing_section:禁止元素" in issues


def test_legacy_detector_requires_a_stable_marker_combination():
    from novelvideo.cognee.pipeline import is_legacy_meta_scene_prompt

    assert is_legacy_meta_scene_prompt(LEGACY_META_PROMPT) is True
    assert is_legacy_meta_scene_prompt("根据原文证据可见，走廊铺灰色地砖。") is False


class _FakeAgent:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[str] = []

    async def run(self, request: str):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(output=response)


def _enrichment(prompt: str):
    from novelvideo.cognee.pipeline import SceneEnrichment, SceneEnrichmentList

    return SceneEnrichmentList(
        scenes=[
            SceneEnrichment(
                name="设备走廊",
                scene_type="interior",
                environment_prompt=prompt,
                description="广播站内连接直播间和设备间的固定走廊",
            )
        ]
    )


@pytest.mark.asyncio
async def test_enrich_retries_invalid_prompt_once_and_uses_second_result():
    from novelvideo.cognee.pipeline import enrich_scene_environment_from_context

    agent = _FakeAgent([_enrichment(LEGACY_META_PROMPT), _enrichment(VALID_PROMPT)])

    scene = await enrich_scene_environment_from_context(
        scene_name="设备走廊",
        scene_type="interior",
        context_lines=["广播站设备走廊连接直播间和设备间。"],
        enrichment_agent=agent,
    )

    assert scene.environment_prompt == VALID_PROMPT
    assert len(agent.requests) == 2
    assert "上一次输出未通过质量检查" in agent.requests[1]
    assert "meta_instruction:" in agent.requests[1]
    assert "最能代表地点身份" in agent.requests[1]


@pytest.mark.asyncio
async def test_enrich_recovers_when_first_model_call_raises():
    from novelvideo.cognee.pipeline import enrich_scene_environment_from_context

    agent = _FakeAgent([RuntimeError("temporary failure"), _enrichment(VALID_PROMPT)])

    scene = await enrich_scene_environment_from_context(
        scene_name="设备走廊",
        context_lines=["广播站设备走廊连接直播间和设备间。"],
        enrichment_agent=agent,
    )

    assert scene.environment_prompt == VALID_PROMPT
    assert len(agent.requests) == 2
    assert "model_error:RuntimeError" in agent.requests[1]


@pytest.mark.asyncio
async def test_enrich_fails_after_two_invalid_attempts_without_returning_fallback():
    from novelvideo.cognee.pipeline import (
        ScenePromptQualityError,
        enrich_scene_environment_from_context,
    )

    agent = _FakeAgent([_enrichment(LEGACY_META_PROMPT), _enrichment(LEGACY_META_PROMPT)])

    with pytest.raises(
        ScenePromptQualityError,
        match=r"SCENE_PROMPT_QUALITY_FAILED: 设备走廊",
    ):
        await enrich_scene_environment_from_context(
            scene_name="设备走廊",
            context_lines=["广播站设备走廊连接直播间和设备间。"],
            enrichment_agent=agent,
        )

    assert len(agent.requests) == 2
