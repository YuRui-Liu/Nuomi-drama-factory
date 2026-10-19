"""C 切片：提示词与 panel_spec 的确定性交叉校验（不做语义评分）。"""

from novelvideo.freezone.video_director.crosscheck import crosscheck_prompt


def test_dialogue_must_be_verbatim():
    issues = crosscheck_prompt(
        prompt="他说：你好。", spec={"dialogue": ["老陈……是我！"]}, duration=6
    )
    assert issues[0]["code"] == "prompt_spec_conflict"
    assert issues[0]["severity"] == "error"
    assert "老陈……是我！" in issues[0]["message"]


def test_dialogue_present_is_clean():
    issues = crosscheck_prompt(
        prompt="他压低声音：老陈……是我！",
        spec={"dialogue": ["老陈……是我！"]},
        duration=6,
    )
    assert issues == []


def test_camera_side_contradiction():
    issues = crosscheck_prompt(prompt="从右侧拍摄", spec={"camera_side": "左侧"}, duration=6)
    assert any(issue["code"] == "prompt_spec_conflict" for issue in issues)


def test_matching_camera_side_is_clean():
    issues = crosscheck_prompt(prompt="从左侧拍摄，平视", spec={"camera_side": "左侧"}, duration=6)
    assert issues == []


def test_english_side_contradiction():
    issues = crosscheck_prompt(
        prompt="Shot from the right side.", spec={"camera_side": "left"}, duration=6
    )
    assert any(issue["code"] == "prompt_spec_conflict" for issue in issues)


def test_english_side_matches_on_word_boundaries_only():
    issues = crosscheck_prompt(
        prompt="A righteous stare into the lens.", spec={"camera_side": "left"}, duration=6
    )
    assert issues == []


def test_side_from_panel_sections_axis():
    issues = crosscheck_prompt(
        prompt="从右侧拍摄", spec={"sections": {"axis": "左侧"}}, duration=6
    )
    assert any(issue["code"] == "prompt_spec_conflict" for issue in issues)


def test_empty_spec_is_clean():
    assert crosscheck_prompt(prompt="任意提示词", spec={}, duration=6) == []


def test_dialogue_entry_may_be_a_mapping():
    issues = crosscheck_prompt(
        prompt="他把杯子放下。",
        spec={"dialogue": [{"speaker": "老陈", "text": "是我。"}]},
        duration=6,
    )
    assert issues[0]["code"] == "prompt_spec_conflict"


def test_duration_mention_must_match_spec():
    mismatched = crosscheck_prompt(
        prompt="镜头持续 8s，随后切走。", spec={"duration": 6}, duration=6
    )
    assert [issue["code"] for issue in mismatched] == ["prompt_spec_conflict"]
    assert "8" in mismatched[0]["message"] and "6" in mismatched[0]["message"]

    assert crosscheck_prompt(prompt="镜头持续 6 秒。", spec={"duration": 6}, duration=6) == []
    assert crosscheck_prompt(
        prompt="The shot lasts 6 seconds.", spec={"duration": 6}, duration=6
    ) == []
    # 没写时长、或写的是时间点而不是时长，都不报。
    assert crosscheck_prompt(prompt="他缓缓抬头。", spec={"duration": 6}, duration=6) == []
    assert crosscheck_prompt(prompt="第 2 秒他抬头。", spec={"duration": 6}, duration=6) == []


def test_frame_mode_mention_must_match_spec():
    last_frame_on_first_mode = crosscheck_prompt(
        prompt="以末帧收束，停在嘴角。", spec={"frame_mode": "first"}, duration=6
    )
    assert [issue["code"] for issue in last_frame_on_first_mode] == ["prompt_spec_conflict"]

    first_only_on_locked_mode = crosscheck_prompt(
        prompt="Start from the first frame only.", spec={"frame_mode": "first_last"}, duration=6
    )
    assert [issue["code"] for issue in first_only_on_locked_mode] == ["prompt_spec_conflict"]

    assert crosscheck_prompt(
        prompt="从首帧走到尾帧。", spec={"frame_mode": "first_last"}, duration=6
    ) == []
    assert crosscheck_prompt(
        prompt="从首帧开始，镜头缓慢推近。", spec={"frame_mode": "first"}, duration=6
    ) == []
