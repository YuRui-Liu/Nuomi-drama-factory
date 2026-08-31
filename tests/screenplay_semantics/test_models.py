from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from novelvideo.screenplay_semantics import (
    DramaticBeat,
    Scene,
    ScreenplaySemanticRevision,
    SemanticValidationIssue,
    SemanticValidationReport,
    SourceBlock,
    SourceRange,
)


def source_block(
    *,
    block_id: str = "line-8",
    ordinal: int = 1,
    start_line: int = 8,
    end_line: int = 8,
) -> SourceBlock:
    return SourceBlock(
        id=block_id,
        ordinal=ordinal,
        kind="action",
        text="△林默撞门。",
        source_range=SourceRange(start_line=start_line, end_line=end_line),
    )


def scene(
    *,
    scene_id: str = "scene-1",
    ordinal: int = 1,
    start_line: int = 7,
    end_line: int = 15,
    blocks: tuple[SourceBlock, ...] | None = None,
) -> Scene:
    if blocks is None:
        block_line = start_line + 1
        blocks = (
            source_block(
                block_id=f"line-{block_line}",
                start_line=block_line,
                end_line=block_line,
            ),
        )
    return Scene(
        id=scene_id,
        ordinal=ordinal,
        source_range=SourceRange(start_line=start_line, end_line=end_line),
        heading="1-1 广播站 深夜 内",
        interior_exterior="interior",
        location="广播站",
        time_of_day="深夜",
        characters=("林默",),
        blocks=blocks,
        content_hash=f"hash-{scene_id}",
        status="validated",
    )


def beat(
    *,
    beat_id: str = "beat-scene-1-01",
    ordinal: int = 1,
    scene_id: str = "scene-1",
    source_ranges: tuple[SourceRange, ...] | None = None,
) -> DramaticBeat:
    return DramaticBeat(
        id=beat_id,
        ordinal=ordinal,
        scene_id=scene_id,
        source_ranges=source_ranges
        if source_ranges is not None
        else (SourceRange(start_line=8, end_line=12),),
        characters=("林默",),
        goal="进入广播室",
        obstacle="门被反锁",
        action="林默连续撞门",
        reaction="门内传来拖拽声",
        turn="门锁自行弹开",
        result="林默停在门口",
        emotional_shift="急迫转为警惕",
        dialogue_source_ids=(),
        estimated_duration_seconds=6.0,
        must_show=("撞门", "门锁弹开", "停步"),
        script_facts=("林默撞门", "门锁弹开"),
        director_interpretation=("用近景强调迟疑",),
    )


def revision(
    *,
    scenes: tuple[Scene, ...] | None = None,
    beats: tuple[DramaticBeat, ...] | None = None,
    status: str = "draft",
    validation_report: SemanticValidationReport | None = None,
) -> ScreenplaySemanticRevision:
    return ScreenplaySemanticRevision(
        revision_id="semantic-1",
        parent_revision_id=None,
        episode=1,
        source_revision=3,
        source_hash="source-hash-v3",
        version=1,
        status=status,
        scenes=scenes if scenes is not None else (scene(),),
        beats=beats if beats is not None else (beat(),),
        metadata_blocks=(),
        validation_report=validation_report or SemanticValidationReport(passed=True),
        created_at=datetime(2026, 8, 31, 16, 0, tzinfo=timezone(timedelta(hours=8))),
        activated_at=None,
    )


def test_dramatic_beat_spans_multiple_lines_and_separates_fact_from_interpretation():
    item = beat()

    assert item.source_ranges[0].end_line - item.source_ranges[0].start_line == 4
    assert "近景" not in item.script_facts
    assert item.director_interpretation == ("用近景强调迟疑",)

    with pytest.raises(ValidationError, match="frozen"):
        item.action = "改写剧情"


def test_source_ranges_must_be_ordered():
    with pytest.raises(ValidationError, match="source range end must not precede start"):
        SourceRange(start_line=12, end_line=8)

    with pytest.raises(ValidationError, match="source ranges must be ordered"):
        beat(
            source_ranges=(
                SourceRange(start_line=11, end_line=12),
                SourceRange(start_line=8, end_line=9),
            )
        )


@pytest.mark.parametrize(
    ("scenes", "beats", "message"),
    [
        (
            (scene(), scene(scene_id="scene-1", ordinal=2)),
            (beat(),),
            "scene ids must be unique",
        ),
        (
            (scene(), scene(scene_id="scene-2", ordinal=2, start_line=20, end_line=30)),
            (beat(), beat(beat_id="beat-scene-1-01", scene_id="scene-2")),
            "beat ids must be unique",
        ),
        (
            (scene(), scene(scene_id="scene-2", ordinal=1, start_line=20, end_line=30)),
            (beat(),),
            "scene ordinals must be unique",
        ),
        (
            (scene(),),
            (beat(), beat(beat_id="beat-scene-1-02", ordinal=1)),
            "beat ordinals must be unique within a scene",
        ),
    ],
)
def test_revision_rejects_duplicate_ids_and_ordinals(
    scenes: tuple[Scene, ...],
    beats: tuple[DramaticBeat, ...],
    message: str,
):
    with pytest.raises(ValidationError, match=message):
        revision(scenes=scenes, beats=beats)


def test_revision_requires_contiguous_scene_and_beat_ordinals():
    with pytest.raises(ValidationError, match="scene ordinals must be contiguous"):
        revision(scenes=(scene(ordinal=2),))

    with pytest.raises(ValidationError, match="beat ordinals must be contiguous"):
        revision(beats=(beat(ordinal=2),))


def test_beat_must_belong_to_an_existing_scene_and_stay_within_its_range():
    with pytest.raises(ValidationError, match="beat must reference an existing scene"):
        revision(beats=(beat(scene_id="scene-missing"),))

    with pytest.raises(ValidationError, match="beat source range must belong to its scene"):
        revision(
            beats=(
                beat(
                    source_ranges=(SourceRange(start_line=14, end_line=16),),
                ),
            )
        )


def test_scene_rejects_duplicate_or_out_of_range_source_blocks():
    with pytest.raises(ValidationError, match="source block ids must be unique"):
        scene(
            blocks=(
                source_block(),
                source_block(ordinal=2, start_line=9, end_line=9),
            )
        )

    with pytest.raises(ValidationError, match="source block must belong to its scene"):
        scene(blocks=(source_block(start_line=16, end_line=16),))


def test_active_revision_cannot_contain_validation_errors():
    report = SemanticValidationReport.from_issues(
        (
            SemanticValidationIssue(
                code="unsupported_script_fact",
                message="事实缺少原文证据",
                severity="error",
                scene_id="scene-1",
                beat_id="beat-scene-1-01",
                source_range=SourceRange(start_line=8, end_line=12),
            ),
        )
    )

    assert report.passed is False
    with pytest.raises(ValidationError, match="active revision cannot contain errors"):
        revision(status="active", validation_report=report)


def test_scene_preserves_explicit_character_roster():
    assert scene().characters == ("林默",)


def test_active_revision_requires_a_passing_report_even_without_issues():
    with pytest.raises(ValidationError, match="active revision requires a passing report"):
        revision(
            status="active",
            validation_report=SemanticValidationReport(passed=False, issues=()),
        )


def test_revision_exposes_stable_status_version_and_utc_timestamps():
    item = revision(status="review_required")

    assert item.version == 1
    assert item.source_revision == 3
    assert item.status == "review_required"
    assert item.created_at == datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)
    assert item.beats_for("scene-1") == (beat(),)


def test_models_reject_unknown_fields_and_empty_script_facts():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SourceRange(start_line=1, end_line=1, undocumented=True)

    payload = beat().model_dump()
    payload["script_facts"] = ()
    with pytest.raises(ValidationError, match="at least 1 item"):
        DramaticBeat.model_validate(payload)
