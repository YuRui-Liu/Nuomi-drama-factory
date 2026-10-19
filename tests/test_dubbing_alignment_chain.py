from novelvideo.audio.dubbing_alignment import align_dialogue


def test_pause_trim_first():
    result = align_dialogue(audio_seconds=6.4, segment_seconds=6.0, pause_budget=0.5)
    assert result.strategy == "trim_pauses"


def test_speed_within_five_percent():
    result = align_dialogue(audio_seconds=6.2, segment_seconds=6.0, pause_budget=0.1)
    assert result.strategy == "speed"
    assert 0.95 <= result.speed <= 1.05


def test_beyond_limit_blocks():
    result = align_dialogue(audio_seconds=7.0, segment_seconds=6.0, pause_budget=0.1)
    assert result.blocked


def test_within_segment_needs_no_alignment():
    result = align_dialogue(audio_seconds=5.5, segment_seconds=6.0, pause_budget=0.5)
    assert result.strategy == "none"
    assert result.speed == 1.0
    assert not result.blocked
