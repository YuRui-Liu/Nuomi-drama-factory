from __future__ import annotations

from types import SimpleNamespace

import pytest

from novelvideo.narrative_groups.service import group_beats


def test_group_beats_excludes_nonvisual_production_notes() -> None:
    groups = group_beats(
        [
            {
                "id": "production-note",
                "visual_description": "时长信息卡片（185s），属于制作说明，无可直接拍摄。",
                "dialogue": "",
                "narration": "",
            }
        ]
    )

    assert groups == []


def test_group_beats_keeps_silent_visual_beats() -> None:
    groups = group_beats(
        [
            {
                "id": "silent-visual",
                "visual_description": "阿远抬起手电筒，照向空无一人的走廊。",
                "dialogue": "",
                "narration": "",
            }
        ]
    )

    assert [group.beat_ids for group in groups] == [("silent-visual",)]


@pytest.mark.parametrize(
    ("spoken_field", "spoken_text"),
    [
        ("dialogue", "这里有一张时长信息卡。"),
        ("narration", "画面提示本段持续一百八十五秒。"),
    ],
)
def test_group_beats_keeps_production_notes_with_spoken_content(
    spoken_field: str,
    spoken_text: str,
) -> None:
    beat = {
        "id": f"spoken-{spoken_field}",
        "visual_description": "制作说明：时长信息卡片，无可直接拍摄。",
        "dialogue": "",
        "narration": "",
        spoken_field: spoken_text,
    }

    groups = group_beats([beat])

    assert [group.beat_ids for group in groups] == [(beat["id"],)]


def test_group_beats_keeps_mapping_production_note_with_narration_segment() -> None:
    groups = group_beats(
        [
            {
                "id": "mapped-narration-segment",
                "visual_description": "制作说明：时长信息卡片，无可直接拍摄。",
                "narration_segment": "本段持续一百八十五秒。",
            }
        ]
    )

    assert [group.beat_ids for group in groups] == [("mapped-narration-segment",)]


def test_group_beats_keeps_object_production_note_with_narration_segment() -> None:
    beat = SimpleNamespace(
        id="object-narration-segment",
        visual_description="制作说明：时长信息卡片，无可直接拍摄。",
        narration_segment="本段持续一百八十五秒。",
    )

    groups = group_beats([beat])

    assert [group.beat_ids for group in groups] == [("object-narration-segment",)]
