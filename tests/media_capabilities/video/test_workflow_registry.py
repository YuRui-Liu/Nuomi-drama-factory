from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from novelvideo.media_capabilities import models as capability_models
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
from novelvideo.media_capabilities.video import workflow_registry as workflow_registry_module
from novelvideo.media_capabilities.video.workflow_registry import (
    H3_WORKFLOW_ID,
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
        "workflow_settings_key": "video_minimax_h3",
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
        "runninghub:minimax-h3",
        "runninghub:minimax-h3-ref",
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
        workflow_settings_key="video_minimax_h3",
        scenes=frozenset({VideoWorkflowScene.NARRATIVE_GROUP}),
        supported_modes=("auto", "i2va", "fl2va"),
        default_mode="auto",
        is_default=True,
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
        ),
        available=True,
    )


def test_registry_registers_reference_workflow_after_legacy_h3(tmp_path) -> None:
    definitions = _configured_registry(tmp_path).list()

    assert [definition.id for definition in definitions] == [
        H3_WORKFLOW_ID,
        "runninghub:minimax-h3-ref",
    ]
    reference = definitions[1]
    assert reference.label == "RunningHub MiniMax H3 · Ref"
    assert reference.provider == "runninghub"
    assert reference.adapter_key == "minimax-h3-ref"
    assert reference.workflow_settings_key == "video_minimax_h3_ref"
    assert isinstance(
        reference.workflow_settings_key,
        capability_models.RunningHubWorkflowSettingsKey,
    )
    assert reference.scenes == frozenset({VideoWorkflowScene.NARRATIVE_GROUP})
    assert reference.supported_modes == ("auto", "i2va", "fl2va")
    assert reference.default_mode == "auto"
    assert reference.is_default is False
    assert reference.reference_policy == workflow_registry_module.VideoReferencePolicy(
        required=True,
        min_images=1,
        max_images=5,
        source_kinds=(
            "character_identity",
            "scene_master",
            "prop_reference",
            "temporary_upload",
        ),
    )


def test_reference_policy_uses_provider_max_images_setting(tmp_path) -> None:
    store = MediaCapabilityStore(tmp_path / "settings.db")
    store.save_provider(
        ProviderAccount(
            id="runninghub-main",
            provider_type="runninghub",
            credential_ref="env://RUNNINGHUB_API_KEY",
        )
    )
    store.save_runninghub_workflows(
        RunningHubWorkflowSettings(video_minimax_h3_ref_max_images=9)
    )

    reference = build_video_workflow_registry(
        store, CredentialResolver(env={"RUNNINGHUB_API_KEY": "rh-secret"})
    ).list()[1]

    assert reference.reference_policy.max_images == 9


def test_reference_workflow_profile_loader_validates_and_normalizes_contract() -> None:
    profile = workflow_registry_module._load_h3_reference_workflow_profile(
        workflow_id=" 2096502793044582401 "
    )

    assert profile.workflow_id == "2096502793044582401"
    assert profile.provider == "runninghub"
    assert profile.capabilities == ["video.ref2va"]
    assert profile.bindings == {
        "timeline_data": {"node_id": "12", "field": "timeline_data"}
    }
    assert profile.outputs == {
        "video": {"node_id": "7", "media_type": "video"}
    }


@pytest.mark.parametrize("workflow_id", ["", " ", "ref-workflow", "12.5", 123])
def test_reference_workflow_profile_loader_rejects_invalid_runtime_id(
    workflow_id: object,
) -> None:
    with pytest.raises(ValueError, match="digits only"):
        workflow_registry_module._load_h3_reference_workflow_profile(
            workflow_id=workflow_id  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "other"),
        ("capabilities", ["video.i2va"]),
        ("bindings", {"timeline_data": {"node_id": "99", "field": "timeline_data"}}),
        ("bindings", {"timeline_data": {"node_id": "12", "field": "other"}}),
        ("outputs", {"video": {"node_id": "99", "media_type": "video"}}),
        ("outputs", {"video": {"node_id": "7", "media_type": "image"}}),
    ],
)
def test_reference_workflow_profile_loader_rejects_incompatible_metadata(
    tmp_path, monkeypatch, field: str, value: object
) -> None:
    profile = json.loads(
        workflow_registry_module._H3_REFERENCE_PROFILE_PATH.read_text(encoding="utf-8")
    )
    profile[field] = value
    invalid_profile = tmp_path / "invalid-profile.json"
    invalid_profile.write_text(json.dumps(profile), encoding="utf-8")
    monkeypatch.setattr(
        workflow_registry_module, "_H3_REFERENCE_PROFILE_PATH", invalid_profile
    )

    with pytest.raises(ValueError, match="reference profile"):
        workflow_registry_module._load_h3_reference_workflow_profile(
            workflow_id="2096502793044582401"
        )


def test_reference_policy_defaults_are_non_reference() -> None:
    policy_type = workflow_registry_module.VideoReferencePolicy
    assert policy_type() == policy_type(
        required=False,
        min_images=0,
        max_images=0,
        source_kinds=(),
    )


def test_workflow_definition_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        VideoWorkflowDefinition(
            id="workflow",
            label="Workflow",
            provider="provider",
            adapter_key="adapter",
            workflow_settings_key="video_minimax_h3",
            scenes=frozenset({VideoWorkflowScene.NARRATIVE_GROUP}),
            supported_modes=("auto",),
            unexpected=True,  # type: ignore[call-arg]
        )


@pytest.mark.parametrize(
    "workflow_settings_key",
    ["video_minimax_h3_reff", "video_minimax_h3_ref_max_images"],
)
def test_workflow_definition_rejects_unsupported_settings_key(
    workflow_settings_key: str,
) -> None:
    with pytest.raises(ValidationError, match="workflow_settings_key"):
        _definition(workflow_settings_key=workflow_settings_key)


def test_workflow_definition_is_frozen(tmp_path) -> None:
    definition = _configured_registry(tmp_path).list()[0]

    with pytest.raises(ValidationError, match="Instance is frozen"):
        definition.label = "changed"  # type: ignore[misc]


def test_workflow_definition_exposes_supported_modes(tmp_path) -> None:
    definition = _configured_registry(tmp_path).list()[0]

    assert definition.supported_modes == ("auto", "i2va", "fl2va")
    assert "modes" not in VideoWorkflowDefinition.model_fields


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


def test_registry_default_uses_explicit_default_marker() -> None:
    non_default = _definition(id="first")
    default = _definition(id="second", is_default=True)

    assert (
        VideoWorkflowRegistry((non_default, default))
        .default(VideoWorkflowScene.NARRATIVE_GROUP)
        .id
        == "second"
    )


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


def test_reference_workflow_reports_empty_workflow_as_unavailable(tmp_path) -> None:
    store = MediaCapabilityStore(tmp_path / "reference.db")
    store.save_provider(
        ProviderAccount(
            id="runninghub-main",
            provider_type="runninghub",
            credential_ref="env://RUNNINGHUB_API_KEY",
        )
    )
    store.save_runninghub_workflows(
        RunningHubWorkflowSettings(video_minimax_h3_ref="")
    )

    reference = build_video_workflow_registry(
        store, CredentialResolver(env={"RUNNINGHUB_API_KEY": "rh-secret"})
    ).list()[1]

    assert reference.available is False
    assert reference.unavailable_reason == "workflow_not_configured"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "other"),
        ("capabilities", ["video.i2va"]),
        ("bindings", {"timeline_data": {"node_id": "99", "field": "timeline_data"}}),
        ("bindings", {"timeline_data": {"node_id": "12", "field": "other"}}),
        ("outputs", {"video": {"node_id": "99", "media_type": "video"}}),
        ("outputs", {"video": {"node_id": "7", "media_type": "image"}}),
    ],
)
def test_reference_workflow_reports_incompatible_metadata_as_profile_invalid(
    tmp_path, monkeypatch, field: str, value: object
) -> None:
    store = MediaCapabilityStore(tmp_path / "reference.db")
    store.save_provider(
        ProviderAccount(
            id="runninghub-main",
            provider_type="runninghub",
            credential_ref="env://RUNNINGHUB_API_KEY",
        )
    )

    profile = json.loads(
        workflow_registry_module._H3_REFERENCE_PROFILE_PATH.read_text(encoding="utf-8")
    )
    profile[field] = value
    invalid_profile = tmp_path / "invalid-profile.json"
    invalid_profile.write_text(json.dumps(profile), encoding="utf-8")
    monkeypatch.setattr(
        workflow_registry_module,
        "_H3_REFERENCE_PROFILE_PATH",
        invalid_profile,
    )

    reference = build_video_workflow_registry(
        store, CredentialResolver(env={"RUNNINGHUB_API_KEY": "rh-secret"})
    ).list()[1]

    assert reference.available is False
    assert reference.unavailable_reason == "profile_invalid"


def test_reference_workflow_reports_invalid_runtime_id_as_profile_invalid(
    tmp_path, monkeypatch
) -> None:
    store = MediaCapabilityStore(tmp_path / "reference.db")
    store.save_provider(
        ProviderAccount(
            id="runninghub-main",
            provider_type="runninghub",
            credential_ref="env://RUNNINGHUB_API_KEY",
        )
    )

    def invalid_workflow_id_for_key(self, settings_key):
        return "ref-workflow"

    monkeypatch.setattr(
        "novelvideo.media_capabilities.runtime.configuration."
        "RunningHubRuntimeConfiguration.workflow_id_for_key",
        invalid_workflow_id_for_key,
    )

    reference = build_video_workflow_registry(
        store, CredentialResolver(env={"RUNNINGHUB_API_KEY": "rh-secret"})
    ).list()[1]

    assert reference.available is False
    assert reference.unavailable_reason == "profile_invalid"


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
