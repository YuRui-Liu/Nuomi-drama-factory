from novelvideo.creation.asset_design import (
    AssetDesignBrief,
    CompileResult,
    Fact,
    FactSource,
    Issue,
    Wardrobe,
)


def test_brief_requires_asset_identity():
    brief = AssetDesignBrief(
        asset_kind="character",
        asset_id="lin-du",
        facts=[
            Fact(
                field="age",
                value="约40岁",
                source=FactSource(episode=1, quote="四十来岁的男人"),
            )
        ],
        design={
            "silhouette": "普通偏瘦",
            "wardrobe": [Wardrobe(piece="运动夹克", color="褪色赭红")],
        },
    )
    assert brief.schema_version == 1
    assert brief.variant_id is None
    assert brief.facts[0].source.episode == 1


def test_issue_severity_limited():
    issue = Issue(code="brief_source_missing", severity="error", message="quote not found")
    assert issue.severity == "error"


def test_compile_result_defaults():
    result = CompileResult()
    assert result.compiled == {}
    assert result.prompt_text == ""
    assert result.issues == []
