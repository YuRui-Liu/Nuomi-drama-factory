from novelvideo.screenplay_semantics import SourceRange
from novelvideo.screenplay_semantics.parser import parse_screenplay_document
import pytest
from novelvideo.utils.screenplay_scene_parser import parse_scene_blocks


@pytest.mark.parametrize('episode', ['第一集', '第1集', '第二集', '第2集'])
def test_xyq_heading_keeps_scene_and_dialogue_evidence(episode):
    number = 2 if '二' in episode or '2' in episode else 1
    text = (f'{episode} {number}-1\n场景：广播间 夜\n人物：岑砚\n'
            '△岑砚停笔。\n岑砚（OS）：会是她吗？\n\n'
            f'{episode} {number}-2\n场景：观察廊 夜\n人物：岑砚\n△光束停住。')
    parsed = parse_screenplay_document(text)
    assert [s.location for s in parsed.scenes] == ['广播间', '观察廊']
    assert [s.time_of_day for s in parsed.scenes] == ['夜', '夜']
    assert all(s.interior_exterior == 'unspecified' for s in parsed.scenes)
    assert [s.source_range.start_line for s in parsed.scenes] == [1, 7]
    spoken = next(b for s in parsed.scenes for b in s.blocks if b.kind == 'dialogue')
    assert spoken.text == '岑砚（OS）：会是她吗？'
    assert spoken.source_range.start_line == 5
    assert all(s.characters == ('岑砚',) for s in parsed.scenes)
    raw = parse_scene_blocks(text)
    assert [s.episode for s in raw] == [number, number]
    assert [s.scene_no for s in raw] == ['1', '2']
    assert [s.location for s in raw] == ['广播间', '观察廊']


@pytest.mark.parametrize('label, expected', [
    ('岑砚（OS）', ('岑砚', 'OS')),
    ('岑砚(低声)', ('岑砚', '低声')),
    ('广播（扬声器传出，失真）', ('广播', '扬声器传出，失真')),
    ('居民甲', ('居民甲', '')),
])
def test_dialogue_identity_is_separate_from_delivery(label, expected):
    from novelvideo.screenplay_semantics.parser import split_speaker_delivery
    assert split_speaker_delivery(label) == expected


def test_action_ending_in_time_word_does_not_open_an_unlabeled_scene():
    parsed = parse_screenplay_document('第一集 1-1\n场景：广播间 夜\n人物：岑砚\n他等到 夜')
    assert len(parsed.scenes) == 1
    assert parsed.scenes[0].blocks[0].text == '他等到 夜'


def test_bold_cast_and_section_labels_preserve_evidence_without_fake_dialogue():
    text = "1-1 广播间 夜 内\n**出场人物：**岑砚、居民甲\n**动作：**\n岑砚推门。\n**对白：**\n岑砚：等等。\n**结尾钩子：**门突然关闭。"
    parsed = parse_screenplay_document(text)
    scene = parsed.scenes[0]
    assert scene.characters == ("岑砚", "居民甲")
    assert [b.kind for b in scene.blocks] == ["formatting", "action", "formatting", "dialogue", "action"]
    assert scene.blocks[-1].text == "**结尾钩子：**门突然关闭。"
    assert scene.blocks[-1].source_range.start_line == 7
    assert any(b.text == "**出场人物：**岑砚、居民甲" for b in parsed.metadata_blocks)


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
        "cast",
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


def test_markdown_numbered_scene_heading_sets_semantic_location():
    parsed = parse_screenplay_document("### 1-1 谢家碑坊\n石九停步。")

    assert parsed.scenes[0].location == "谢家碑坊"


def test_closed_atx_numbered_scene_heading_excludes_closing_markers_from_location():
    parsed = parse_screenplay_document("### 1-1 谢家碑坊 ###\n石九停步。")

    assert parsed.scenes[0].location == "谢家碑坊"


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


def test_workspace_markdown_heading_and_cast_keep_clean_names():
    parsed = parse_screenplay_document(
        "## 1-1｜账房 · 夜 · 内\n出场人物：林川、掌柜。\n林川：快走。"
    )
    scene = parsed.scenes[0]
    assert scene.location == "账房"
    assert scene.time_of_day == "夜"
    assert scene.interior_exterior == "interior"
    assert scene.characters == ("林川", "掌柜")
    assert scene.blocks[0].kind == "dialogue"
