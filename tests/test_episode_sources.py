import hashlib
from typing import get_type_hints, Literal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import novelvideo.episode_sources as episode_sources

from novelvideo.episode_sources import (
    EpisodeCandidate,
    EpisodeSourceCandidate,
    apply_manual_episode_numbers,
    canonical_novel_text,
    compose_canonical_novel,
    content_sha256,
    detect_episode_number,
    inspect_episode_source,
    resolve_episode_candidates,
    validate_resolutions,
)
from novelvideo.utils.document_parsers import decode_novel_bytes


def test_episode_import_route_only_accepts_existing_script_intent():
    from novelvideo.api.routes import episode_imports

    assert hasattr(episode_imports, "_require_existing_script_intent")
    require_intent = episode_imports._require_existing_script_intent
    assert require_intent("existing_script") == "existing_script"
    with pytest.raises(HTTPException) as caught:
        require_intent("story_adaptation")

    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "WRONG_IMPORT_ENTRY"


def test_episode_import_commit_rejects_retransmitted_intent():
    from novelvideo.api.schemas import EpisodeImportCommitRequest

    with pytest.raises(ValidationError, match="extra_forbidden"):
        EpisodeImportCommitRequest(
            preview_id="preview-1",
            expected_revision=0,
            resolutions=[],
            intent="story_adaptation",
        )


@pytest.mark.parametrize(
    ("content", "filename", "expected_number", "expected_source", "warning"),
    [
        ("# 第十二集 失踪\n正文", "E02.md", 12, "body", True),
        ("Episode 7: Return\nBody", "第02集.txt", 7, "body", True),
        ("没有标题", "第02集.md", 2, "filename", False),
        ("No heading", "episode-003.txt", 3, "filename", False),
        ("No heading", "notes.md", None, None, False),
    ],
)
def test_detect_episode_number_prefers_content_and_supports_chinese_and_english(
    content, filename, expected_number, expected_source, warning
):
    result = detect_episode_number(content, filename)

    assert result.episode_number == expected_number
    assert result.source == expected_source
    assert result.has_filename_mismatch is warning


def test_inspect_episode_source_preserves_preview_contract_and_mismatch_warning():
    item = inspect_episode_source("E03.md", "# 第2集 地下追逐\n正文")

    assert isinstance(item, EpisodeSourceCandidate)
    assert item.file_id != "E03.md"
    assert item.filename == "E03.md"
    assert item.title == "地下追逐"
    assert item.content_hash == content_sha256("# 第2集 地下追逐\n正文")
    assert item.episode_number == 2
    assert item.number_source == "body"
    assert item.warnings == ("正文集号 2 与文件名集号 3 不一致",)


def test_same_filename_candidates_have_distinct_file_ids():
    first = inspect_episode_source("episode.md", "第1集\n甲")
    second = inspect_episode_source("episode.md", "第2集\n乙")

    assert first.file_id != second.file_id
    assert first.source_filename == second.source_filename == "episode.md"


def test_split_episode_candidates_supports_chinese_and_english_heading_boundaries():
    content = (
        "# 第十二集失踪\n"
        "甲\n\n"
        "## ePiSoDe 7: Return\n"
        "Body 提到第99集"
    )

    candidates = episode_sources.split_episode_candidates("collection.md", content)

    assert [item.episode_number for item in candidates] == [12, 7]
    assert [item.number_source for item in candidates] == ["body", "body"]
    assert [item.title for item in candidates] == ["失踪", "Return"]
    assert [item.content for item in candidates] == [
        "# 第十二集失踪\n甲\n\n",
        "## ePiSoDe 7: Return\nBody 提到第99集",
    ]


def test_split_episode_candidates_does_not_treat_bare_body_lines_as_headings():
    content = (
        "# 第1集 起点\n"
        "第一集正文\n"
        "Episode 2: 转折\n"
        "第二集正文\n"
        "第3集 空集"
    )

    candidates = episode_sources.split_episode_candidates("collection.md", content)

    assert [item.episode_number for item in candidates] == [1, 2, 3]
    assert [item.content for item in candidates] == [
        "# 第1集 起点\n第一集正文\n",
        "Episode 2: 转折\n第二集正文\n",
        "第3集 空集",
    ]
    assert candidates[2].warnings == ("分集标题后缺少正文",)


def test_split_episode_candidates_accepts_only_a_document_start_utf8_bom():
    raw = b"\xef\xbb\xbf" + (
        "# 第1集 开端\n"
        "甲\n"
        "\ufeff第2集 不是边界\n"
        "仍属首集\n"
        "Episode 3 终章\n"
        "乙"
    ).encode("utf-8")
    content = decode_novel_bytes(raw)

    candidates = episode_sources.split_episode_candidates("collection.md", content)

    assert [item.episode_number for item in candidates] == [1, 3]
    assert [item.title for item in candidates] == ["开端", "终章"]
    assert candidates[0].content == (
        "\ufeff# 第1集 开端\n"
        "甲\n"
        "\ufeff第2集 不是边界\n"
        "仍属首集\n"
    )
    assert candidates[0].warnings == ()
    assert candidates[1].content == "Episode 3 终章\n乙"


def test_split_episode_candidates_preserves_preface_without_polluting_heading_number():
    content = (
        "企划说明：正文曾提到第99集。\r\n"
        "保留此行\r\n"
        "# 第2集 开端\r\n"
        "正文\r\n"
        "Episode 5 End\r\n"
        "尾声"
    )

    candidates = episode_sources.split_episode_candidates("E09.md", content)

    assert [item.episode_number for item in candidates] == [2, 5]
    assert candidates[0].content == (
        "企划说明：正文曾提到第99集。\r\n"
        "保留此行\r\n"
        "# 第2集 开端\r\n"
        "正文\r\n"
    )
    assert candidates[0].warnings == (
        "正文集号 2 与文件名集号 9 不一致",
        "首集包含合集前言",
    )
    assert candidates[1].warnings == ("正文集号 5 与文件名集号 9 不一致",)


def test_split_episode_candidates_ignores_episode_mentions_inside_body_sentences():
    content = (
        "# 第1集 起点\n"
        "正文提到 Episode 2 会更精彩。\n"
        "角色还说第3集会反转。\n"
        "仍是第一集正文。\n"
        "# 第4集 终点\n"
        "结尾"
    )

    candidates = episode_sources.split_episode_candidates("collection.md", content)

    assert [item.episode_number for item in candidates] == [1, 4]
    assert "Episode 2" in candidates[0].content
    assert "第3集" in candidates[0].content


@pytest.mark.parametrize(
    ("filename", "content", "expected_number", "expected_title"),
    [
        ("E03.md", "正文提到第2集，但不是标题。", 2, ""),
        ("single.md", "# 第8集 单篇\n正文", 8, "单篇"),
    ],
)
def test_split_episode_candidates_keeps_single_candidate_behavior_with_fewer_than_two_boundaries(
    filename, content, expected_number, expected_title
):
    candidates = episode_sources.split_episode_candidates(filename, content)

    assert len(candidates) == 1
    assert candidates[0].source_filename == filename
    assert candidates[0].content == content
    assert candidates[0].episode_number == expected_number
    assert candidates[0].title == expected_title


def test_split_episode_candidates_keeps_source_order_warns_empty_body_and_uses_unique_ids():
    content = (
        "Episode 10 Ten\n"
        "\n"
        "第2集 Two\n"
        "正文\n"
        "第1集 One\n"
        "   \n"
    )

    candidates = episode_sources.split_episode_candidates("mixed.md", content)

    assert [item.episode_number for item in candidates] == [10, 2, 1]
    assert [item.content for item in candidates] == [
        "Episode 10 Ten\n\n",
        "第2集 Two\n正文\n",
        "第1集 One\n   \n",
    ]
    assert episode_sources.EPISODE_EMPTY_BODY_WARNING == "分集标题后缺少正文"
    assert candidates[0].warnings == (episode_sources.EPISODE_EMPTY_BODY_WARNING,)
    assert candidates[1].warnings == ()
    assert candidates[2].warnings == (episode_sources.EPISODE_EMPTY_BODY_WARNING,)
    assert {item.source_filename for item in candidates} == {"mixed.md"}
    assert len({item.file_id for item in candidates}) == 3


def test_number_source_type_only_allows_supported_detection_sources():
    assert get_type_hints(EpisodeCandidate)["number_source"] == (
        Literal["body", "filename", "manual"] | None
    )


def test_manual_episode_numbers_are_required_positive_integers():
    candidates = [EpisodeCandidate("unknown.md", "正文")]

    with pytest.raises(ValueError, match="手动指定"):
        apply_manual_episode_numbers(candidates, {})
    with pytest.raises(ValueError, match="正整数"):
        apply_manual_episode_numbers(candidates, {"unknown.md": 0})
    with pytest.raises(ValueError, match="正整数"):
        apply_manual_episode_numbers(candidates, {"unknown.md": True})

    assigned = apply_manual_episode_numbers(candidates, {"unknown.md": 4})
    assert assigned[0].episode_number == 4
    assert assigned[0].number_source == "manual"


def test_batch_duplicates_must_be_resolved_explicitly():
    candidates = [
        EpisodeCandidate("a.md", "第1集\nA", episode_number=1),
        EpisodeCandidate("b.md", "第1集\nB", episode_number=1),
    ]

    with pytest.raises(ValueError, match="批次.*重复.*1"):
        resolve_episode_candidates(candidates, existing_numbers=set(), decisions={})


def test_existing_conflicts_require_overwrite_or_skip():
    candidate = EpisodeCandidate("E02.md", "第2集\n新", episode_number=2)

    with pytest.raises(ValueError, match="overwrite.*skip"):
        resolve_episode_candidates([candidate], {2}, {})
    with pytest.raises(ValueError, match="overwrite.*skip"):
        resolve_episode_candidates([candidate], {2}, {2: "merge"})

    skipped = resolve_episode_candidates([candidate], {2}, {2: "skip"})
    assert skipped.imports == ()
    assert skipped.skipped == (candidate,)
    assert skipped.overwritten_episode_numbers == frozenset()

    overwritten = resolve_episode_candidates([candidate], {2}, {2: "overwrite"})
    assert overwritten.imports == (candidate,)
    assert overwritten.skipped == ()
    assert overwritten.overwritten_episode_numbers == frozenset({2})


def test_validate_resolutions_accepts_revision_mapping_and_blocks_unresolved_conflict():
    item = inspect_episode_source("E02.md", "第2集 新版")

    with pytest.raises(ValueError, match="overwrite.*skip"):
        validate_resolutions([item], existing={2: 4}, resolutions={})

    result = validate_resolutions(
        [item], existing={2: 4}, resolutions={2: "overwrite"}
    )
    assert result.overwritten_episode_numbers == frozenset({2})


def test_new_candidates_need_no_decision_and_are_sorted():
    candidates = [
        EpisodeCandidate("E10.md", "十", episode_number=10),
        EpisodeCandidate("E02.md", "二", episode_number=2),
    ]

    result = resolve_episode_candidates(candidates, {1}, {})

    assert [item.episode_number for item in result.imports] == [2, 10]


def test_hash_is_stable_sha256_of_utf8_content():
    content = "第1集\r\n你好"

    assert content_sha256(content) == "sha256:" + hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()


def test_canonical_novel_text_sorts_and_normalizes_boundaries_only():
    episodes = [
        EpisodeCandidate("E02.md", "第2集\nB\n", episode_number=2),
        EpisodeCandidate("E01.md", "  第1集\nA  ", episode_number=1),
    ]

    assert canonical_novel_text(episodes) == "第1集\nA\n\n第2集\nB\n"


def test_canonical_novel_text_rejects_missing_or_duplicate_numbers():
    with pytest.raises(ValueError, match="集号"):
        canonical_novel_text([EpisodeCandidate("x.md", "x")])
    with pytest.raises(ValueError, match="重复"):
        canonical_novel_text(
            [
                EpisodeCandidate("a.md", "a", episode_number=1),
                EpisodeCandidate("b.md", "b", episode_number=1),
            ]
        )


def test_compose_canonical_novel_is_compatible_and_rejects_bool_episode_number():
    episodes = [
        EpisodeCandidate("E02.md", "第2集\nB", episode_number=2),
        EpisodeCandidate("E01.md", "第1集\nA", episode_number=1),
    ]
    assert compose_canonical_novel(episodes) == "第1集\nA\n\n第2集\nB\n"

    invalid = EpisodeCandidate("true.md", "正文", episode_number=True)
    with pytest.raises(ValueError, match="合法集号"):
        compose_canonical_novel([invalid])
    with pytest.raises(ValueError, match="正整数"):
        resolve_episode_candidates([invalid], set(), {})
