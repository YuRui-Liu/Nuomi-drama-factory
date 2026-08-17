from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runninghub_h3_is_the_default_video_backend() -> None:
    schemas = (ROOT / "src/novelvideo/api/schemas.py").read_text(encoding="utf-8")
    config = (ROOT / "src/novelvideo/config.py").read_text(encoding="utf-8")
    route = (ROOT / "src/novelvideo/api/routes/generation.py").read_text(
        encoding="utf-8"
    )

    assert 'video_backend: str = "runninghub_minimax_h3"' in schemas
    assert 'os.environ.get("VIDEO_BACKEND", "runninghub_minimax_h3")' in config
    assert 'value="runninghub_minimax_h3"' in route
    assert 'label="RunningHub MiniMax H3"' in route
    assert 'supported_modes=["first_frame", "last_frame", "keyframe"]' in route


def test_ltx23_remains_available_as_a_legacy_fallback() -> None:
    route = (ROOT / "src/novelvideo/api/routes/generation.py").read_text(
        encoding="utf-8"
    )

    assert 'value="ltx23"' in route
    assert 'label="LTX 2.3（旧版回退）"' in route
