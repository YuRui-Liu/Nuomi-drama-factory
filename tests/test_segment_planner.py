"""C 切片：分镜行拆段。

默认一格一段（规格 R1）；只有调用方显式允许时才合并相邻格。
"""

from novelvideo.freezone.video_director.segment_planner import split_segments


def _rows(*durations):
    return [
        {
            "shot_no": index + 1,
            "duration": duration,
            "cell_image": f"cell-{index + 1}.png",
            "director": {"panel_spec": {"shot": f"镜头{index + 1}"}, "technique_intent": ""},
        }
        for index, duration in enumerate(durations)
    ]


def test_one_cell_one_segment_by_default():
    plan = split_segments(_rows(6, 7), group={"id": "G01"})
    assert plan.group_id == "G01"
    assert [segment.segment_id for segment in plan.segments] == ["G01-S01", "G01-S02"]
    assert [segment.source_rows for segment in plan.segments] == [[1], [2]]
    assert [segment.duration_seconds for segment in plan.segments] == [6.0, 7.0]


def test_merge_adjacent_when_short():
    plan = split_segments(_rows(2, 3), group={"id": "G01"}, merge_adjacent=True)
    assert len(plan.segments) == 1
    segment = plan.segments[0]
    assert segment.source_rows == [1, 2]
    assert segment.duration_seconds == 5.0
    assert segment.first_frame == "cell-1.png"
    assert segment.last_frame == "cell-2.png"
    assert segment.panel_spec == {"shot": "镜头1"}


def test_merge_respects_limit():
    plan = split_segments(_rows(10, 10), group={"id": "G01"}, merge_adjacent=True)
    assert [segment.source_rows for segment in plan.segments] == [[1], [2]]


def test_segment_ids_follow_row_order():
    plan = split_segments(_rows(6, 6, 6), group={"id": "G02"})
    assert [segment.segment_id for segment in plan.segments] == [
        "G02-S01",
        "G02-S02",
        "G02-S03",
    ]
