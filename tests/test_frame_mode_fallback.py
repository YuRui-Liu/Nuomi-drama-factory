"""C 切片：帧模式确定性护栏（只降级，不升级）。"""

from novelvideo.freezone.video_director.segment_planner import apply_frame_mode_guard


def test_missing_last_frame_downgrades():
    segment = {"frame_mode": "first_last", "first_frame": "a.png", "last_frame": None}
    guarded, issues = apply_frame_mode_guard(segment)
    assert guarded["frame_mode"] == "first"
    assert guarded["last_frame"] is None
    assert issues[0]["code"] == "frame_mode_fallback"
    assert issues[0]["severity"] == "warning"


def test_missing_first_frame_downgrades():
    segment = {"frame_mode": "first_last", "first_frame": None, "last_frame": "b.png"}
    guarded, issues = apply_frame_mode_guard(segment)
    assert guarded["frame_mode"] == "first"
    assert [issue["code"] for issue in issues] == ["frame_mode_fallback"]


def test_identical_frames_downgrade():
    segment = {"frame_mode": "first_last", "first_frame": "a.png", "last_frame": "a.png"}
    guarded, issues = apply_frame_mode_guard(segment)
    assert guarded["frame_mode"] == "first"
    assert issue_codes(issues) == ["frame_mode_fallback"]


def test_never_upgrades():
    segment = {"frame_mode": "first", "first_frame": "a.png", "last_frame": "b.png"}
    guarded, issues = apply_frame_mode_guard(segment)
    assert guarded["frame_mode"] == "first"
    assert issues == []


def test_valid_first_last_is_kept():
    segment = {"frame_mode": "first_last", "first_frame": "a.png", "last_frame": "b.png"}
    guarded, issues = apply_frame_mode_guard(segment)
    assert guarded["frame_mode"] == "first_last"
    assert guarded["last_frame"] == "b.png"
    assert issues == []


def test_downgrade_keeps_other_fields():
    segment = {
        "segment_id": "G01-S01",
        "frame_mode": "first_last",
        "first_frame": "a.png",
        "last_frame": None,
        "duration_seconds": 6.0,
    }
    guarded, _ = apply_frame_mode_guard(segment)
    assert guarded["segment_id"] == "G01-S01"
    assert guarded["duration_seconds"] == 6.0
    assert segment["frame_mode"] == "first_last"  # input is not mutated


def issue_codes(issues):
    return [issue["code"] for issue in issues]
