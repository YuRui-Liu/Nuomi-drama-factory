import pytest

from novelvideo.creation.asset_design.compiler import compile_brief
from novelvideo.creation.asset_design.models import AssetDesignBrief, Fact, FactSource, Wardrobe


def _brief():
    return AssetDesignBrief(
        asset_kind="character",
        asset_id="lin-du",
        facts=[Fact(field="age", value="约40岁", source=FactSource(episode=1, quote="四十来岁的男人"))],
        design={
            "silhouette": "普通偏瘦",
            "wardrobe": [Wardrobe(piece="运动夹克", color="褪色赭红", material="涤纶", state="旧")],
        },
        consistency={"anchors": ["赭红夹克"], "forbidden": ["帽子"]},
        render={"material": "柔和动画材质", "negative": ["摄影皮肤纹理"]},
    )


def test_compile_is_deterministic():
    first = compile_brief(_brief(), style_fragment="3D 国漫，柔和全局光", params={"aspect_ratio": "3:2"})
    second = compile_brief(_brief(), style_fragment="3D 国漫，柔和全局光", params={"aspect_ratio": "3:2"})
    assert first.prompt_text == second.prompt_text
    assert first.fingerprint.brief_hash == second.fingerprint.brief_hash


def test_style_injected_once():
    result = compile_brief(_brief(), style_fragment="3D 国漫", params={})
    assert result.prompt_text.count("3D 国漫") == 1


@pytest.mark.parametrize(
    ("asset_kind", "description"),
    [
        ("scene", "窄巷尽头的旧杂货铺，木门漆面斑驳"),
        ("prop", "一只铜制怀表，表盖边缘有细小凹痕"),
    ],
)
def test_scene_and_prop_description_and_volume_reach_compiled_prompt(asset_kind, description):
    volume = "厚重实体结构，清楚呈现正面与侧面厚度"
    brief = AssetDesignBrief(
        asset_kind=asset_kind,
        asset_id=f"{asset_kind}-example",
        design={"description": description},
        consistency={"anchors": ["固定识别锚点"]},
        render={"material": "哑光表面", "volume": volume},
    )

    result = compile_brief(brief, style_fragment="统一动画风格", params={})

    assert description in result.compiled["subject"]
    assert description in result.prompt_text
    assert result.compiled["volume"] == volume
    assert volume in result.prompt_text
    assert result.prompt_text.count("统一动画风格") == 1


@pytest.mark.parametrize("asset_kind", ["scene", "prop"])
def test_scene_and_prop_compile_output_and_fingerprint_are_deterministic(asset_kind):
    brief = AssetDesignBrief(
        asset_kind=asset_kind,
        asset_id=f"{asset_kind}-stable",
        design={"description": "有清晰轮廓的测试资产"},
        consistency={"anchors": ["稳定锚点"], "forbidden": ["额外标识"]},
        render={
            "material": "细腻石材",
            "volume": "边缘厚度明确",
            "negative": ["不要模糊背景"],
        },
    )
    arguments = {
        "style_fragment": "统一动画风格",
        "params": {"aspect_ratio": "3:2"},
        "style_id": "house-style",
        "style_revision": "rev-3",
    }

    first = compile_brief(brief, **arguments)
    second = compile_brief(brief, **arguments)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.compiled["negative"] == ["不要模糊背景", "额外标识"]
    assert first.prompt_text.count("统一动画风格") == 1
