from __future__ import annotations

import pytest
from pydantic import ValidationError

from novelvideo.media_capabilities.models import (
    ProviderAccount,
    RunningHubWorkflowSettings,
)
from novelvideo.media_capabilities.runtime.credentials import CredentialResolver
from novelvideo.media_capabilities.store import MediaCapabilityStore
from novelvideo.media_capabilities.video.parameters import (
    VideoWorkflowParameterDefinition,
    VideoWorkflowParameterOption,
)
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


def _definition(**updates) -> VideoWorkflowDefinition:
    values = {
        "id": "workflow",
        "label": "Workflow",
        "provider": "provider",
        "adapter_key": "adapter",
        "scenes": frozenset({VideoWorkflowScene.NARRATIVE_GROUP}),
        "supported_modes": ("auto",),
    }
    values.update(updates)
    return VideoWorkflowDefinition(**values)


def _resolution_parameter() -> VideoWorkflowParameterDefinition:
    return VideoWorkflowParameterDefinition(
        key="resolution",
        label="分辨率",
        default="720p",
        options=(
            VideoWorkflowParameterOption(value="720p", label="标准"),
            VideoWorkflowParameterOption(value="1080p", label="高清"),
        ),
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
        supported_modes=("auto", "i2va", "fl2va"),
        default_mode="auto",
        parameters=(
            VideoWorkflowParameterDefinition(
                key="resolution",
                label="分辨率",
                default="720p",
                scope="narrative_group",
                options=(
                    VideoWorkflowParameterOption(
                        value="720p",
                        label="标准",
                        relative_cost="standard",
                    ),
                    VideoWorkflowParameterOption(
                        value="1080p",
                        label="高清",
                        description="画质更高，预计耗时和额度增加。",
                        relative_cost="higher",
                    ),
                ),
            ),
            VideoWorkflowParameterDefinition(
                key="continuity_policy",
                label="连续性策略",
                default="legacy",
                scope="narrative_group",
                options=(
                    VideoWorkflowParameterOption(value="legacy", label="旧流程"),
                    VideoWorkflowParameterOption(value="observe", label="只观察"),
                    VideoWorkflowParameterOption(
                        value="guard", label="阻断确定性错误"
                    ),
                    VideoWorkflowParameterOption(
                        value="enforce", label="启用新编译"
                    ),
                ),
            ),
        ),
        available=True,
    )


def test_workflow_definition_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        VideoWorkflowDefinition(
            id="workflow",
            label="Workflow",
            provider="provider",
            adapter_key="adapter",
            scenes=frozenset({VideoWorkflowScene.NARRATIVE_GROUP}),
            supported_modes=("auto",),
            unexpected=True,  # type: ignore[call-arg]
        )


def test_workflow_definition_is_frozen(tmp_path) -> None:
    definition = _configured_registry(tmp_path).list()[0]

    with pytest.raises(ValidationError, match="Instance is frozen"):
        definition.label = "changed"  # type: ignore[misc]


def test_workflow_definition_exposes_supported_modes(tmp_path) -> None:
    definition = _configured_registry(tmp_path).list()[0]

    assert definition.supported_modes == ("auto", "i2va", "fl2va")
    assert "modes" not in VideoWorkflowDefinition.model_fields


def test_h3_registry_exposes_staged_continuity_policy(tmp_path) -> None:
    definition = _configured_registry(tmp_path).list()[0]

    assert [parameter.key for parameter in definition.parameters] == [
        "resolution",
        "continuity_policy",
    ]
    policy = definition.parameters[1]
    assert policy.default == "legacy"
    assert [(option.value, option.label) for option in policy.options] == [
        ("legacy", "旧流程"),
        ("observe", "只观察"),
        ("guard", "阻断确定性错误"),
        ("enforce", "启用新编译"),
    ]


@pytest.mark.parametrize(
    "updates",
    [
        {"scenes": frozenset()},
        {"supported_modes": ()},
        {"default_mode": "fl2va"},
        {"available": True, "unavailable_reason": "unexpected"},
        {"available": False, "unavailable_reason": None},
        {"available": False, "unavailable_reason": "  "},
    ],
)
def test_workflow_definition_rejects_invalid_invariants(updates) -> None:
    with pytest.raises(ValidationError):
        _definition(**updates)


def test_registry_rejects_duplicate_workflow_ids() -> None:
    definition = _definition()

    with pytest.raises(ValueError, match="duplicate workflow id: workflow"):
        VideoWorkflowRegistry((definition, definition))


def test_workflow_definition_rejects_duplicate_parameter_keys() -> None:
    parameter = _resolution_parameter()

    with pytest.raises(ValidationError, match="duplicate workflow parameter key: resolution"):
        _definition(parameters=(parameter, parameter))


@pytest.mark.parametrize("method", ["list", "resolve", "default"])
def test_registry_rejects_invalid_scene_consistently(tmp_path, method: str) -> None:
    registry = _configured_registry(tmp_path)

    with pytest.raises(VideoWorkflowUnavailable, match="unknown workflow scene"):
        if method == "list":
            registry.list("invalid")
        elif method == "resolve":
            registry.resolve("runninghub:minimax-h3", "invalid")
        else:
            registry.default("invalid")


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

        def invalid_profile(*, workflow_id: str | None = None):
            raise ValueError(f"invalid profile: {workflow_id}")

        monkeypatch.setattr(
            "novelvideo.media_capabilities.video.workflow_registry.load_h3_workflow_profile",
            invalid_profile,
        )

    definition = build_video_workflow_registry(store, resolver).list()[0]

    assert definition.available is False
    assert definition.unavailable_reason == expected_reason


@pytest.mark.parametrize("unexpected", [TypeError("bug"), AssertionError("bug")])
def test_h3_availability_propagates_unexpected_profile_errors(
    tmp_path, monkeypatch, unexpected: Exception
) -> None:
    store = MediaCapabilityStore(tmp_path / "unexpected.db")
    store.save_provider(
        ProviderAccount(
            id="runninghub-main",
            provider_type="runninghub",
            credential_ref="env://RUNNINGHUB_API_KEY",
            enabled=True,
        )
    )
    resolver = CredentialResolver(env={"RUNNINGHUB_API_KEY": "rh-secret"})

    def broken_profile(*, workflow_id: str | None = None):
        raise unexpected

    monkeypatch.setattr(
        "novelvideo.media_capabilities.video.workflow_registry.load_h3_workflow_profile",
        broken_profile,
    )

    with pytest.raises(type(unexpected), match="bug"):
        build_video_workflow_registry(store, resolver)
