"""C 切片：按段时长累积切批，单批不得超过运行时上限。"""

import pytest

from novelvideo.freezone.video_director.batching import batch_segments


def _segments(*durations):
    return [
        {"segment_id": f"S{index}", "duration_seconds": duration}
        for index, duration in enumerate(durations)
    ]


def test_batches_under_limit():
    batches = batch_segments(_segments(6, 6, 6, 6), limit_seconds=15)
    assert [sum(item["duration_seconds"] for item in batch) for batch in batches] == [12, 12]
    assert [[item["segment_id"] for item in batch] for batch in batches] == [
        ["S0", "S1"],
        ["S2", "S3"],
    ]


def test_default_limit_is_fifteen_seconds():
    batches = batch_segments(_segments(10, 10))
    assert [[item["segment_id"] for item in batch] for batch in batches] == [["S0"], ["S1"]]


def test_exact_limit_stays_in_one_batch():
    batches = batch_segments(_segments(5, 5, 5), limit_seconds=15)
    assert len(batches) == 1


def test_over_limit_single_segment_raises():
    with pytest.raises(ValueError, match="batch_over_limit"):
        batch_segments(_segments(20), limit_seconds=15)


def test_empty_input_returns_no_batches():
    assert batch_segments([], limit_seconds=15) == []


def test_fractional_durations_do_not_overflow():
    batches = batch_segments(_segments(7.5, 7.5, 2.5), limit_seconds=15)
    assert [len(batch) for batch in batches] == [2, 1]
