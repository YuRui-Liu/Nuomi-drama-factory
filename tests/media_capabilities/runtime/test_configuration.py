from __future__ import annotations

import pytest

from novelvideo.media_capabilities.models import (
    MediaCapability,
    ProviderAccount,
    RunningHubWorkflowSettings,
)
from novelvideo.media_capabilities.runtime.configuration import (
    MediaRuntimeConfigurationError,
    load_grsai_runtime_configuration,
    load_runninghub_runtime_configuration,
)
from novelvideo.media_capabilities.runtime.credentials import CredentialResolver
from novelvideo.media_capabilities.store import MediaCapabilityStore


def test_persisted_keyring_key_and_workflow_ids_feed_runninghub_runtime(tmp_path) -> None:
    store = MediaCapabilityStore(tmp_path / "settings.db")
    store.save_provider(
        ProviderAccount(
            id="runninghub-main",
            provider_type="runninghub",
            base_url="https://www.runninghub.cn",
            credential_ref="keyring://dramaclaw/media/runninghub-main",
        )
    )
    store.save_runninghub_workflows(
        RunningHubWorkflowSettings(
            image_upscale="1001",
            video_minimax_h3="2002",
            tts_qwen3_voice_design="3003",
            tts_indextts2_voice_clone="4004",
        )
    )
    resolver = CredentialResolver(
        keyring_reader=lambda target: (
            "rh-key" if target == "dramaclaw/media/runninghub-main" else None
        )
    )

    runtime = load_runninghub_runtime_configuration(store, resolver)

    assert runtime.api_key == "rh-key"
    assert runtime.workflow_id(MediaCapability.IMAGE_GRID_UPSCALE_SPLIT) == "1001"
    assert runtime.workflow_id(MediaCapability.VIDEO_I2VA) == "2002"
    assert runtime.workflow_id(MediaCapability.VIDEO_L2VA) == "2002"
    assert runtime.workflow_id(MediaCapability.VIDEO_FL2VA) == "2002"
    assert runtime.workflow_id(MediaCapability.TTS_VOICE_DESIGN) == "3003"
    assert runtime.workflow_id(MediaCapability.TTS_VOICE_CLONE) == "4004"


def test_runtime_rejects_an_empty_workflow_slot(tmp_path) -> None:
    store = MediaCapabilityStore(tmp_path / "settings.db")
    store.save_provider(
        ProviderAccount(
            id="runninghub-main",
            provider_type="runninghub",
            credential_ref="env://RUNNINGHUB_API_KEY",
        )
    )
    runtime = load_runninghub_runtime_configuration(
        store,
        CredentialResolver(env={"RUNNINGHUB_API_KEY": "rh-key"}),
    )

    with pytest.raises(MediaRuntimeConfigurationError, match="not configured"):
        runtime.workflow_id(MediaCapability.IMAGE_GRID_UPSCALE_SPLIT)


def test_persisted_grsai_key_feeds_image_runtime(tmp_path) -> None:
    store = MediaCapabilityStore(tmp_path / "settings.db")
    store.save_provider(
        ProviderAccount(
            id="grsai-main",
            provider_type="grsai",
            base_url="https://grsai.example",
            credential_ref="keyring://dramaclaw/media/grsai-main",
        )
    )
    runtime = load_grsai_runtime_configuration(
        store,
        CredentialResolver(
            keyring_reader=lambda target: (
                "grsai-key" if target == "dramaclaw/media/grsai-main" else None
            )
        ),
    )

    assert runtime.api_key == "grsai-key"
    assert runtime.account.base_url == "https://grsai.example"
    assert runtime.model == "gpt-image-2"
    assert runtime.create_client().default_model == "gpt-image-2"
