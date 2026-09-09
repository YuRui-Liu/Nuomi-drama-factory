import pytest

from novelvideo.asset_imports import AssetType, decode_asset_table, extract_candidates


MIXED = """# 场景与道具
## 场景表
| 编号 | 场景名 | 内／外 | 常用时段 | 故事功能 | 长期视觉特征 |
|---|---|---|---|---|---|
| L001 | 谢家碑坊 | 内 | 夜／日 | 修复证据 | 冷灰石墙、木架拓片 |
## 道具表
| 编号 | 道具 | 来源／权利 | 首次出现 | 初始持有人 | 状态变化 |
|---|---|---|---|---|---|
| P001 | 缺角木尺 | 原创设定 | E001 | 石九 | 与界碑凹槽吻合 |
"""


def test_decode_accepts_bom_and_normalizes_newlines():
    result = decode_asset_table(
        "人物表.md", b"\xef\xbb\xbf# \xe4\xba\xba\xe7\x89\xa9\xe8\xa1\xa8\r\n"
    )
    assert result.text == "# 人物表\n"


def test_mixed_file_isolated_by_asset_type():
    scenes = extract_candidates(MIXED, AssetType.SCENE)
    props = extract_candidates(MIXED, AssetType.PROP)
    assert [item.name for item in scenes.candidates] == ["谢家碑坊"]
    assert [item.name for item in props.candidates] == ["缺角木尺"]
    assert scenes.candidates[0].fields["scene_type"] == "interior"
    assert props.candidates[0].fields["owner"] == "石九"


def test_prop_table_does_not_create_assets_from_rules_after_eight_rows():
    rows = "\n".join(
        f"| P00{i} | 道具{i} | 甲 | E001 | 角色 | 状态 | E002 |" for i in range(1, 9)
    )
    text = f"""## 道具表
| 编号 | 道具 | 来源／权利 | 首次出现 | 初始持有人 | 状态变化 | 回收窗口 |
|---|---|---|---|---|---|---|
{rows}
## 道具使用红线
- 第九件道具仅被说明提及，不能创建。
"""
    result = extract_candidates(text, AssetType.PROP)
    assert len(result.candidates) == 8
    assert all(item.name != "第九件道具" for item in result.candidates)


def test_character_heading_entries_skip_boundary_section():
    text = """# 人物表
## 谢砚秋
- 年龄：24 岁。
- 身份／职业：碑刻修复师。
- 视觉锚点：青灰窄袖衣、右眉浅疤。
## 第一案人物
### 苏娘
- 身份：绣工。
## 人物信息边界
- 谢衡改碑目的未知。
"""
    result = extract_candidates(text, AssetType.CHARACTER)
    assert [item.name for item in result.candidates] == ["谢砚秋", "苏娘"]
    assert result.candidates[0].fields["role"] == "碑刻修复师。"
    assert "谢衡" not in [item.name for item in result.candidates]


def test_character_parser_rejects_ordinary_document_headings():
    text = """# 项目设定
## 世界观
- 时代：架空古代。
## 创作原则
- 目标：保持克制。
## 剧情梗概
- 主线：查清旧案。
"""
    import pytest

    with pytest.raises(ValueError, match="未识别"):
        extract_candidates(text, AssetType.CHARACTER)


def test_character_parser_rejects_plot_heading_with_only_functional_keys():
    text = """# 人物表
## 谢砚秋
- 身份：修复师。
## 剧情梗概
- 目标：找出真相。
- 功能：推进案件。
- 关键选择：公开证据。
"""
    result = extract_candidates(text, AssetType.CHARACTER)
    assert [item.name for item in result.candidates] == ["谢砚秋"]


def test_character_parser_stops_at_next_root_section():
    text = """# 人物表
## 甲
- 身份：证人。
# 剧情分析
## 冲突核心
- 身份：制度性障碍。
- 目标：制造阻力。
"""

    result = extract_candidates(text, AssetType.CHARACTER)

    assert [item.name for item in result.candidates] == ["甲"]


def test_three_conflicting_duplicate_rows_keep_field_permanently_rejected():
    text = """## 人物表
| 人物 | 功能 |
|---|---|
| 甲 | A |
| 甲 | B |
| 甲 | C |
"""

    result = extract_candidates(text, AssetType.CHARACTER)

    assert "role" not in result.candidates[0].fields
    assert "description" not in result.candidates[0].fields
    assert result.warnings == [
        "甲 的 role 存在冲突，已跳过",
        "甲 的 description 存在冲突，已跳过",
    ]


@pytest.mark.parametrize("name", ["../x", "甲/乙", "甲\\乙", ".", ".."])
def test_character_parser_rejects_path_like_asset_names(name):
    text = f"# 人物表\n## {name}\n- 身份：证人。\n"

    with pytest.raises(ValueError, match="资产名"):
        extract_candidates(text, AssetType.CHARACTER)


def test_character_parser_preserves_chinese_quotes_and_exact_evidence_spans():
    text = """# 人物表
## “渡四”旧拓
- 视觉锚点: 泛黄旧拓纸。
- 身份: 关键物证。
- 前史: 从渡口石碑揭下。
"""

    result = extract_candidates(text, AssetType.CHARACTER)

    candidate = result.candidates[0]
    assert candidate.name == "“渡四”旧拓"
    description_evidence = [
        evidence for evidence in candidate.evidence if evidence.field == "description"
    ]
    assert len(description_evidence) == 2
    for evidence in candidate.evidence:
        assert evidence.source_start >= 0
        assert evidence.source_end == evidence.source_start + len(evidence.quote)
        assert text[evidence.source_start : evidence.source_end] == evidence.quote


def test_identical_character_field_lines_keep_evidence_in_their_own_blocks():
    text = """# 人物表
## 甲
- 身份：证人。
## 乙
- 身份：证人。
"""

    result = extract_candidates(text, AssetType.CHARACTER)

    first_block_start = text.index("## 甲")
    second_block_start = text.index("## 乙")
    evidence_by_name = {
        candidate.name: next(
            evidence for evidence in candidate.evidence if evidence.field == "role"
        )
        for candidate in result.candidates
    }
    assert first_block_start <= evidence_by_name["甲"].source_start < second_block_start
    assert evidence_by_name["乙"].source_start >= second_block_start
    for evidence in evidence_by_name.values():
        assert text[evidence.source_start : evidence.source_end] == evidence.quote


def test_identical_table_rows_retain_each_raw_row_source_start():
    text = """## 人物表
| 人物 | 功能 |
|---|---|
| 甲 | 证人 |
| 甲 | 证人 |
"""

    result = extract_candidates(text, AssetType.CHARACTER)

    role_evidence = [
        evidence
        for evidence in result.candidates[0].evidence
        if evidence.field == "role"
    ]
    assert [evidence.source_start for evidence in role_evidence] == [
        text.index("| 甲 | 证人 |"),
        text.rindex("| 甲 | 证人 |"),
    ]
    assert all(text[item.source_start : item.source_end] == item.quote for item in role_evidence)
