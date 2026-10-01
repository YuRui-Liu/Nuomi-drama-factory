from novelvideo.task_backend.runners.narrative_group_video import (
    _build_segments, _synthetic_pair_beat, _source_dialogue_lines,
)


def _beat():
    return {
        "id": "shot-1", "dialogue": "甲句\n乙句", "speaker": "甲 / 乙",
        "dialogue_lines": [
            {"speaker": "甲", "text": "甲句", "tone": "低声"},
            {"speaker": "乙", "text": "乙句", "tone": ""},
        ],
    }


def test_single_shot_preserves_each_source_dialogue_line():
    beat = _beat()
    for plan in ({}, {"units": [{"beat_ids": ["shot-1"]}]}):
        segment = _build_segments({"mode": "auto"}, [beat], {
            "beat_ids": ["shot-1"], "video_plan": plan,
            "cell_assets": [{"beat_id": "shot-1", "path": "frame.png"}],
        })[0]
        assert [line.model_dump() for line in segment.dialogue_lines] == beat["dialogue_lines"]


def test_pair_flattens_source_lines_without_joining_speakers():
    beat = _beat()
    pair = _synthetic_pair_beat(beat, {"id": "shot-2"})
    assert list(pair["dialogue_lines"]) == beat["dialogue_lines"]
    assert [line.model_dump() for line in _source_dialogue_lines((beat,))] == beat["dialogue_lines"]


def test_context_cast_comes_from_director_subjects_without_continuity(monkeypatch):
    from novelvideo.task_backend.runners import narrative_group_video as runner
    monkeypatch.setattr(runner, "_snapshot_frame_sha256", lambda *args: "a" * 64)
    beat = {**_beat(), "cinematography": {"subjects": [{"subject_id": "甲"}, {"subject_id": "乙"}]}}
    pair = runner._synthetic_pair_beat(beat, {"id": "shot-2", "detected_identities": ["丙"]})
    segment = runner.H3DirectorSegment(segment_id="shot-1", beat_number=1, prompt="test", duration_seconds=4, first_frame="frame.png")
    assert runner._prompt_context(segment, beat, None, None).active_character_ids == ("甲", "乙")
    assert runner._prompt_context(segment, pair, None, None).active_character_ids == ("甲", "乙", "丙")
