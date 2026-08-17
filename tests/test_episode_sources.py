import hashlib
from typing import get_type_hints, Literal

import pytest

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
