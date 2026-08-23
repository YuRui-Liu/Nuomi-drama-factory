from __future__ import annotations

from novelvideo.media_capabilities.models import (
    ProviderAccount,
    RunningHubWorkflowSettings,
)
from novelvideo.media_capabilities.runtime.credentials import CredentialResolver
from novelvideo.media_capabilities.store import MediaCapabilityStore
from novelvideo.media_capabilities.video.catalog import list_video_models
from novelvideo.media_capabilities.video.workflow_registry import (
    build_video_workflow_registry,
)


def test_h3_is_listed_when_unconfigured_without_leaking_key(tmp_path) -> None:
    store = MediaCapabilityStore(tmp_path / "settings.db")
    models = list_video_models(store, CredentialResolver(env={}))

    assert [item.id for item in models] == ["runninghub:minimax-h3"]
    assert models[0].available is False
    assert models[0].supported_modes == ("auto", "i2va", "fl2va")
    assert "key" not in models[0].model_dump_json().lower()


def test_h3_is_available_only_with_enabled_account_key_workflow_and_profile(
    tmp_path,
) -> None:
    store = MediaCapabilityStore(tmp_path / "settings.db")
    store.save_provider(
        ProviderAccount(
            id="runninghub-main",
            provider_type="runninghub",
            base_url="https://www.runninghub.cn",
            credential_ref="env://RUNNINGHUB_API_KEY",
            enabled=True,
        )
    )
    resolver = CredentialResolver(env={"RUNNINGHUB_API_KEY": "rh-secret"})

    assert list_video_models(store, resolver)[0].available is True

    store.save_runninghub_workflows(RunningHubWorkflowSettings(video_minimax_h3=""))
    unavailable = list_video_models(store, resolver)[0]
    assert unavailable.available is False
    assert unavailable.unavailable_reason == "workflow_not_configured"


def test_catalog_is_credential_free_registry_projection(tmp_path) -> None:
    store = MediaCapabilityStore(tmp_path / "settings.db")
    resolver = CredentialResolver(env={})

    definition = build_video_workflow_registry(store, resolver).list()[0]
    item = list_video_models(store, resolver)[0]

    assert item.model_dump() == {
        "id": definition.id,
        "label": definition.label,
        "provider": definition.provider,
        "available": definition.available,
        "supported_modes": definition.modes,
        "default_mode": definition.default_mode,
        "unavailable_reason": definition.unavailable_reason,
    }
    assert "credential" not in item.model_dump_json().lower()
