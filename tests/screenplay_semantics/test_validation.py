from pathlib import Path

from novelvideo.screenplay_semantics import Scene, SourceBlock, SourceRange
from novelvideo.screenplay_semantics.extractor import DramaticBeatDraft
from novelvideo.screenplay_semantics.validation import validate_scene_beats


def scene() -> Scene:
    return Scene(
        id="scene-1", ordinal=1, source_range=SourceRange(start_line=7, end_line=10),
        heading="1-1 广播站 深夜 内", characters=("林默",), content_hash="hash",
        blocks=(
            SourceBlock(id="line-8", ordinal=1, kind="action", text="△林默撞门。", source_range=SourceRange(start_line=8, end_line=8)),
            SourceBlock(id="line-9", ordinal=2, kind="dialogue", text="林默：开门！", source_range=SourceRange(start_line=9, end_line=9)),
            SourceBlock(id="line-10", ordinal=3, kind="action", text="△门锁弹开，林默停步。", source_range=SourceRange(start_line=10, end_line=10)),
        ),
    )


def draft(**updates) -> DramaticBeatDraft:
    values = dict(
        source_ranges=(SourceRange(start_line=8, end_line=10),), characters=("林默",),
        goal="进入房间", obstacle="门被锁住", action="林默撞门",
        reaction="门框震动", turn="门锁弹开", result="林默停步",
        emotional_shift="急迫转为警惕", dialogue_source_ids=("line-9",),
        estimated_duration_seconds=6, must_show=("撞门", "门锁弹开"),
        script_facts=("林默撞门", "门锁弹开"),
    )
    values.update(updates)
    return DramaticBeatDraft(**values)


def test_rejects_character_fact_and_dialogue_without_source_evidence():
    report = validate_scene_beats(
        scene(),
        (draft(characters=("陌生人",), script_facts=("陌生人开枪",), dialogue_source_ids=("line-99",)),),
    )

    assert {issue.code for issue in report.issues} == {
        "unknown_character", "unsupported_script_fact", "invalid_dialogue_source"
    }


def test_valid_multiline_beat_covers_story_blocks_without_line_fallback():
    report = validate_scene_beats(scene(), (draft(),))
    assert report.passed is True
    assert report.issues == ()


def test_missing_story_coverage_is_reported_instead_of_inventing_a_fallback_beat():
    report = validate_scene_beats(
        scene(),
        (draft(source_ranges=(SourceRange(start_line=8, end_line=9),), script_facts=("林默撞门",)),),
    )
    assert "uncovered_story_block" in {issue.code for issue in report.issues}


def test_semantics_package_never_imports_literal_line_fallbacks():
    root = Path(__file__).parents[2] / "src" / "novelvideo" / "screenplay_semantics"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    assert "split_literal_source_text" not in source
    assert "LiteralScriptWritingWorkflow" not in source
    assert "line fallback" not in source.lower()
