from novelvideo.creation.asset_design.facts import resolve_fact_sources


def test_quote_must_exist_verbatim():
    brief = {"facts": [{"field": "age", "value": "约40岁", "source": {"episode": 1, "quote": "四十来岁的男人"}}]}
    docs = {1: "门外站着一个四十来岁的男人。"}
    assert resolve_fact_sources(brief, docs) == []


def test_missing_quote_reports_issue():
    brief = {"facts": [{"field": "hair", "value": "短发", "source": {"episode": 1, "quote": "头发稀疏"}}]}
    docs = {1: "门外站着一个四十来岁的男人。"}
    issues = resolve_fact_sources(brief, docs)
    assert issues[0].code == "brief_source_missing"
