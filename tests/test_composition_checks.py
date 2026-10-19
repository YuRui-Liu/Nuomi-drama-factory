from novelvideo.music.composition_checks import check_timeline


def test_missing_segment_blocks():
    issues = check_timeline(segments=[{"id": "S1", "video": "a.mp4"}, {"id": "S2", "video": None}],
                            tracks=["dialogue", "music"])
    assert issues[0].code == "timeline_gap"


def test_duplicate_track_blocks():
    issues = check_timeline(segments=[{"id": "S1", "video": "a.mp4"}],
                            tracks=["dialogue", "dialogue"])
    assert issues[0].code == "duplicate_audio_track"


def test_clean_timeline_passes():
    assert check_timeline(segments=[{"id": "S1", "video": "a.mp4"}],
                          tracks=["dialogue", "music"]) == []
