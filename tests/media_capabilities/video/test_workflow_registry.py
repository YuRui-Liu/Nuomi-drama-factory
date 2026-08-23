from __future__ import annotations

import pytest

from novelvideo.media_capabilities.models import (
    ProviderAccount,
    RunningHubWorkflowSettings,
)
from novelvideo.media_capabilities.runtime.credentials import CredentialResolver
from novelvideo.media_capabilities.store import MediaCapabilityStore
from novelvideo.media_capabilities.video.workflow_registry import (
    VideoWorkflowDefinition,
    VideoWorkflowRegistry,
    VideoWorkflowScene,
    VideoWorkflowUnavailable,
    build_video_workflow_registry,
)


def _configured_registry(tmp_path) -> VideoWorkflowRegistry:
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
    return build_video_workflow_registry(
        store,
        CredentialResolver(env={"RUNNINGHUB_API_KEY": "rh-secret"}),
    )


def test_registry_filters_workflows_by_scene(tmp_path) -> None:
    registry = _configured_registry(tmp_path)

    assert [item.id for item in registry.list(VideoWorkflowScene.NARRATIVE_GROUP)] == [
        "runninghub:minimax-h3"
    ]
    assert registry.list(VideoWorkflowScene.SINGLE_BEAT) == ()
    assert registry.list(VideoWorkflowScene.FREEZONE) == ()


def test_registry_resolves_default_available_workflow(tmp_path) -> None:
    registry = _configured_registry(tmp_path)

    resolved = registry.resolve(
        "runninghub:minimax-h3", VideoWorkflowScene.NARRATIVE_GROUP
    )

    assert registry.default(VideoWorkflowScene.NARRATIVE_GROUP) is resolved
    assert resolved == VideoWorkflowDefinition(
        id="runninghub:minimax-h3",
        label="RunningHub MiniMax H3",
        provider="runninghub",
        adapter_key="minimax-h3",
        scenes=frozenset({VideoWorkflowScene.NARRATIVE_GROUP}),
        modes=("auto", "i2va", "fl2va"),
        default_mode="auto",
        available=True,
    )


def test_registry_rejects_unknown_wrong_scene_and_unavailable_workflows(
    tmp_path,
) -> None:
    registry = _configured_registry(tmp_path)

    with pytest.raises(VideoWorkflowUnavailable, match="unknown workflow"):
        registry.resolve("missing", VideoWorkflowScene.NARRATIVE_GROUP)
    with pytest.raises(VideoWorkflowUnavailable, match="not available for scene"):
        registry.resolve("runninghub:minimax-h3", VideoWorkflowScene.SINGLE_BEAT)

    unavailable = build_video_workflow_registry(
        MediaCapabilityStore(tmp_path / "empty.db"), CredentialResolver(env={})
    )
    with pytest.raises(VideoWorkflowUnavailable, match="provider_not_configured"):
        unavailable.resolve("runninghub:minimax-h3", VideoWorkflowScene.NARRATIVE_GROUP)
    with pytest.raises(VideoWorkflowUnavailable, match="no available workflow"):
        unavailable.default(VideoWorkflowScene.NARRATIVE_GROUP)


@pytest.mark.parametrize(
    ("setup", "expected_reason"),
    [
        ("provider", "provider_not_configured"),
        ("credential", "credential_unavailable"),
        ("workflow", "workflow_not_configured"),
        ("profile", "profile_invalid"),
    ],
)
def test_h3_availability_reports_specific_reason(
    tmp_path, monkeypatch, setup: str, expected_reason: str
) -> None:
    store = MediaCapabilityStore(tmp_path / f"{setup}.db")
    resolver = CredentialResolver(env={})
    if setup != "provider":
        store.save_provider(
            ProviderAccount(
                id="runninghub-main",
                provider_type="runninghub",
                credential_ref="env://RUNNINGHUB_API_KEY",
                enabled=True,
            )
        )
    if setup not in {"provider", "credential"}:
        resolver = CredentialResolver(env={"RUNNINGHUB_API_KEY": "rh-secret"})
    if setup == "workflow":
        store.save_runninghub_workflows(RunningHubWorkflowSettings(video_minimax_h3=""))
    if setup == "profile":
        monkeypatch.setattr(
            "novelvideo.media_capabilities.video.workflow_registry.load_h3_workflow_profile",
            lambda: (_ for _ in ()).throw(ValueError("invalid profile")),
        )

    definition = build_video_workflow_registry(store, resolver).list()[0]

    assert definition.available is False
    assert definition.unavailable_reason == expected_reason
