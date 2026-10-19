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
