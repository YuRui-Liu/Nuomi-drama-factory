from novelvideo.screenplay_semantics.editing import (
    MergeAdjacentBeats,
    ReorderBeats,
    SplitBeat,
    UpdateBeat,
    apply_semantic_edit,
)
from novelvideo.screenplay_semantics.models import SourceRange
from tests.screenplay_semantics.test_models import beat, revision


def test_split_beat_creates_new_revision_and_invalidates_original_beat():
    original = revision(status="active")
    result = apply_semantic_edit(original, SplitBeat(beat_id="beat-scene-1-01", before_line=11))

    assert result.parent_revision_id == original.revision_id
    assert [item.source_ranges for item in result.beats] == [
        (SourceRange(start_line=8, end_line=10),),
        (SourceRange(start_line=11, end_line=12),),
    ]
    assert result.invalidated_beat_ids == ("beat-scene-1-01",)
    assert result.status == "review_required"


def test_update_beat_preserves_script_facts_unless_explicitly_changed():
    original = revision()
    result = apply_semantic_edit(
        original,
        UpdateBeat(beat_id="beat-scene-1-01", action="林默用肩膀连续撞门"),
    )
    assert result.beats[0].action == "林默用肩膀连续撞门"
    assert result.beats[0].script_facts == original.beats[0].script_facts


def test_merge_and_reorder_are_scene_local():
    original = revision(beats=(beat(), beat(beat_id="beat-scene-1-02", ordinal=2, source_ranges=(SourceRange(start_line=13, end_line=14),))))
    merged = apply_semantic_edit(
        original,
        MergeAdjacentBeats(first_beat_id="beat-scene-1-01", second_beat_id="beat-scene-1-02"),
    )
    assert len(merged.beats) == 1
    assert merged.invalidated_beat_ids == ("beat-scene-1-01", "beat-scene-1-02")

    reordered = apply_semantic_edit(
        original,
        ReorderBeats(scene_id="scene-1", beat_ids=("beat-scene-1-02", "beat-scene-1-01")),
    )
    assert [item.id for item in reordered.beats] == ["beat-scene-1-02", "beat-scene-1-01"]


def test_merge_after_reorder_keeps_evidence_ranges_reloadable():
    original = revision(
        beats=(
            beat(),
            beat(
                beat_id="beat-scene-1-02",
                ordinal=2,
                source_ranges=(SourceRange(start_line=13, end_line=14),),
            ),
        )
    )
    reordered = apply_semantic_edit(
        original,
        ReorderBeats(
            scene_id="scene-1",
            beat_ids=("beat-scene-1-02", "beat-scene-1-01"),
        ),
    )

    merged = apply_semantic_edit(
        reordered,
        MergeAdjacentBeats(
            first_beat_id="beat-scene-1-02",
            second_beat_id="beat-scene-1-01",
        ),
    )

    assert merged.beats[0].source_ranges == (
        SourceRange(start_line=8, end_line=12),
        SourceRange(start_line=13, end_line=14),
    )
    type(merged).model_validate(merged.model_dump(mode="python"))
