from novelvideo.media_capabilities.models import MediaCapability, VideoGenerationRequest
from novelvideo.media_capabilities.video.quality import VideoProbe, validate_video


def request(**overrides):
    values = {
        "capability": MediaCapability.VIDEO_I2VA,
        "prompt": "人物向前走",
        "duration": 5,
        "first_frame": "first.png",
        "resolution": "1080x1920",
        "generate_audio": True,
    }
    values.update(overrides)
    return VideoGenerationRequest(**values)


def codes(probe, **kwargs):
    return {issue.code for issue in validate_video(probe, request(), **kwargs)}


def test_quality_reports_stable_issue_codes() -> None:
    issues = codes(
        VideoProbe(duration=8, width=720, height=1280, fps=0, has_audio=False),
        first_frame_similarity=0.4,
    )
    assert issues == {
        "video.stream_invalid",
        "video.duration_mismatch",
        "video.resolution_mismatch",
        "video.audio_missing",
        "video.first_frame_mismatch",
    }


def test_quality_accepts_matching_candidate() -> None:
    assert validate_video(
        VideoProbe(duration=5.2, width=1080, height=1920, fps=24, has_audio=True),
        request(),
        first_frame_similarity=0.95,
    ) == ()
