from novelvideo.screenplay_semantics import SourceRange
from novelvideo.screenplay_semantics.parser import parse_screenplay_document


def test_frontmatter_and_chapter_card_are_metadata_not_dramatic_content():
    parsed = parse_screenplay_document(
        """---
episode: E001
title: 不要叫名字
duration_seconds: 110
---
# E001 不要叫名字
1-1 广播站 深夜 内
人物：林默
△林默撞门，门锁突然弹开。
"""
    )

    assert [block.kind for block in parsed.metadata_blocks] == [
        "frontmatter",
        "frontmatter",
        "frontmatter",
        "frontmatter",
        "frontmatter",
        "chapter_card",
    ]
    assert len(parsed.scenes) == 1
    assert parsed.scenes[0].characters == ("林默",)
    assert parsed.scenes[0].blocks[0].text == "△林默撞门，门锁突然弹开。"
    assert parsed.scenes[0].blocks[0].source_range == SourceRange(
        start_line=9,
        end_line=9,
    )


def test_dialogue_keeps_exact_source_line_and_scene_header_is_not_a_beat_candidate():
    parsed = parse_screenplay_document(
        "1-1 天台 黄昏 外\n人物：林默、苏晴\n林默：别过来。\n△苏晴停在门边。"
    )

    scene = parsed.scenes[0]
    assert scene.source_range == SourceRange(start_line=1, end_line=4)
    assert [block.kind for block in scene.blocks] == ["dialogue", "action"]
    assert [block.source_range.start_line for block in scene.blocks] == [3, 4]
    assert scene.blocks[0].id == "line-3"


def test_document_without_scene_headers_does_not_invent_scenes():
    parsed = parse_screenplay_document(
        "episode: E001\ntitle: 章节信息\nduration_seconds: 110\nrights_risk: low"
    )

    assert parsed.scenes == ()
    assert all(block.kind in {"unclassified", "formatting"} for block in parsed.metadata_blocks)


def test_placeholder_scene_header_keeps_location_and_excludes_html_metadata():
    parsed = parse_screenplay_document(
        """---
episode: E002
---
2-1 广播站走廊 日/夜 内/外
人物：周禾 梁真 陶粒 石岚 罗竞 感染者
△周禾按下播放键。
周禾：声音只能争取时间。
<!-- main_change: 保安胸前钥匙指向录音间。 -->
2-2 转移通道 日/夜 内/外
人物：周禾 陶粒 石岚 罗竞 感染者
△周禾推车卡门。
"""
    )

    assert [scene.location for scene in parsed.scenes] == ["广播站走廊", "转移通道"]
    assert [scene.interior_exterior for scene in parsed.scenes] == [
        "unspecified",
        "unspecified",
    ]
    assert parsed.scenes[0].characters == (
        "周禾",
        "梁真",
        "陶粒",
        "石岚",
        "罗竞",
        "感染者",
    )
    assert "<!--" not in "\n".join(
        block.text for scene in parsed.scenes for block in scene.blocks
    )
    assert any(block.text.startswith("<!--") for block in parsed.metadata_blocks)
