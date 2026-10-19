from novelvideo.creation.asset_design.models import AssetDesignBrief, Consistency
from novelvideo.creation.asset_design.validator import validate_brief


def test_fact_conflict_blocks():
    brief = AssetDesignBrief(asset_kind="character", asset_id="x", design={"face": "胡茬明显"}, render={})
    brief.facts = []
    issues = validate_brief(brief, documents={1: "无胡子"}, style_fragment="3D 国漫")
    assert any(issue.code == "brief_fact_conflict" for issue in issues)


def test_style_duplicate_blocks():
    brief = AssetDesignBrief(asset_kind="character", asset_id="x", design={"face": "3D 国漫风格"})
    issues = validate_brief(brief, documents={}, style_fragment="3D 国漫")
    assert any(issue.code == "brief_style_duplicate" for issue in issues)
