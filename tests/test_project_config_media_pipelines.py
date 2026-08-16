from __future__ import annotations

from novelvideo.project_config import (
    MediaPipelineSettings,
    load_project_config_from_state_dir,
    resolve_media_pipeline,
    save_project_config_in_state_dir,
)


def test_media_pipeline_flags_preserve_legacy_defaults(tmp_path) -> None:
    config = load_project_config_from_state_dir(tmp_path)

    assert config["image_pipeline"] == "legacy"
    assert config["video_pipeline"] == "legacy"
    assert config["tts_pipeline"] == "legacy"
    assert config["production_scheduler"] is False


def test_new_pipeline_requires_selector_and_scheduler_and_can_fall_back(tmp_path) -> None:
    save_project_config_in_state_dir(
        tmp_path,
        image_pipeline="production",
        video_pipeline="production",
        tts_pipeline="production",
        production_scheduler=False,
    )
    disabled = MediaPipelineSettings.from_config(
        load_project_config_from_state_dir(tmp_path)
    )
    assert resolve_media_pipeline(disabled, "image") == "legacy"

    save_project_config_in_state_dir(tmp_path, production_scheduler=True)
    enabled = MediaPipelineSettings.from_config(
        load_project_config_from_state_dir(tmp_path)
    )
    assert resolve_media_pipeline(enabled, "image") == "production"
    assert resolve_media_pipeline(enabled, "video") == "production"
    assert resolve_media_pipeline(enabled, "tts") == "production"
    assert resolve_media_pipeline(enabled, "video", production_available=False) == "legacy"


def test_invalid_pipeline_values_fall_back_to_legacy() -> None:
    settings = MediaPipelineSettings.from_config(
        {
            "image_pipeline": "unknown",
            "video_pipeline": None,
            "tts_pipeline": 7,
            "production_scheduler": True,
        }
    )

    assert settings.image_pipeline == "legacy"
    assert settings.video_pipeline == "legacy"
    assert settings.tts_pipeline == "legacy"
    assert settings.production_scheduler is True
