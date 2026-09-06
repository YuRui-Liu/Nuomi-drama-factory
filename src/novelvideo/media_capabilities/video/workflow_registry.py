"""Scene-aware registry of executable production video workflows."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from novelvideo.media_capabilities.models import (
    MediaCapability,
    RunningHubWorkflowSettingsKey,
    WorkflowProfile,
)
from novelvideo.media_capabilities.runtime.configuration import (
    MediaRuntimeConfigurationError,
    load_runninghub_runtime_configuration,
)
from novelvideo.media_capabilities.runtime.credentials import CredentialResolver
from novelvideo.media_capabilities.store import MediaCapabilityStore
from novelvideo.media_capabilities.video.parameters import (
    VideoWorkflowParameterDefinition,
    VideoWorkflowParameterOption,
)
from novelvideo.media_capabilities.video.runtime import load_h3_workflow_profile


H3_WORKFLOW_ID = "runninghub:minimax-h3"
H3_REFERENCE_WORKFLOW_ID = "runninghub:minimax-h3-ref"
_H3_REFERENCE_PROFILE_PATH = (
    Path(__file__).with_name("profiles") / "minimax_h3_ref.json"
)


class VideoWorkflowScene(StrEnum):
    NARRATIVE_GROUP = "narrative_group"
    SINGLE_BEAT = "single_beat"
    FREEZONE = "freezone"


class VideoReferencePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    required: bool = False
    min_images: int = Field(default=0, ge=0)
    max_images: int = Field(default=0, ge=0)
    source_kinds: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        if self.min_images > self.max_images:
            raise ValueError("reference min_images must not exceed max_images")
        if self.required and self.min_images == 0:
            raise ValueError("required reference policy must accept at least one image")
        return self


class VideoWorkflowDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    label: str
    provider: str
    adapter_key: str
    workflow_settings_key: RunningHubWorkflowSettingsKey
    scenes: frozenset[VideoWorkflowScene]
    supported_modes: tuple[str, ...]
    default_mode: str = "auto"
    is_default: bool = False
    reference_policy: VideoReferencePolicy = VideoReferencePolicy()
    parameters: tuple[VideoWorkflowParameterDefinition, ...] = ()
    available: bool = True
    unavailable_reason: str | None = None

    @model_validator(mode="after")
    def validate_definition(self) -> Self:
        if not self.scenes:
            raise ValueError("workflow scenes must not be empty")
        if not self.supported_modes:
            raise ValueError("workflow supported_modes must not be empty")
        if self.default_mode not in self.supported_modes:
            raise ValueError("workflow default_mode must be supported")
        parameter_keys: set[str] = set()
        for parameter in self.parameters:
            if parameter.key in parameter_keys:
                raise ValueError(
                    f"duplicate workflow parameter key: {parameter.key}"
                )
            parameter_keys.add(parameter.key)
        if self.available and self.unavailable_reason is not None:
            raise ValueError("available workflow must not have an unavailable reason")
        if not self.available and not str(self.unavailable_reason or "").strip():
            raise ValueError("unavailable workflow must have a reason")
        return self


class VideoWorkflowUnavailable(LookupError):
    """A workflow cannot be used for the requested scene."""


class VideoWorkflowRegistry:
    def __init__(self, definitions: tuple[VideoWorkflowDefinition, ...]) -> None:
        self._definitions = definitions
        self._by_id: dict[str, VideoWorkflowDefinition] = {}
        for definition in definitions:
            if definition.id in self._by_id:
                raise ValueError(f"duplicate workflow id: {definition.id}")
            self._by_id[definition.id] = definition

    @staticmethod
    def _normalize_scene(scene: VideoWorkflowScene | str) -> VideoWorkflowScene:
        try:
            return VideoWorkflowScene(scene)
        except (TypeError, ValueError) as exc:
            raise VideoWorkflowUnavailable(f"unknown workflow scene: {scene}") from exc

    def list(
        self, scene: VideoWorkflowScene | str | None = None
    ) -> tuple[VideoWorkflowDefinition, ...]:
        if scene is None:
            return self._definitions
        requested_scene = self._normalize_scene(scene)
        return tuple(
            definition
            for definition in self._definitions
            if requested_scene in definition.scenes
        )

    def resolve(
        self,
        identifier: str,
        scene: VideoWorkflowScene | str,
    ) -> VideoWorkflowDefinition:
        requested_scene = self._normalize_scene(scene)
        definition = self._by_id.get(identifier)
        if definition is None:
            raise VideoWorkflowUnavailable(f"unknown workflow: {identifier}")
        if requested_scene not in definition.scenes:
            raise VideoWorkflowUnavailable(
                f"workflow {identifier} is not available for scene "
                f"{requested_scene.value}"
            )
        if not definition.available:
            raise VideoWorkflowUnavailable(
                f"workflow {identifier} is unavailable: "
                f"{definition.unavailable_reason or 'unavailable'}"
            )
        return definition

    def default(self, scene: VideoWorkflowScene | str) -> VideoWorkflowDefinition:
        requested_scene = self._normalize_scene(scene)
        for definition in self.list(requested_scene):
            if definition.is_default and definition.available:
                return definition
        raise VideoWorkflowUnavailable(
            f"no available workflow for scene: {requested_scene.value}"
        )


def _h3_unavailable_reason(
    store: MediaCapabilityStore,
    resolver: CredentialResolver,
) -> str | None:
    account = store.get_provider("runninghub-main")
    if account is None or account.provider_type != "runninghub" or not account.enabled:
        return "provider_not_configured"
    try:
        runtime = load_runninghub_runtime_configuration(store, resolver)
    except MediaRuntimeConfigurationError:
        return "credential_unavailable"
    try:
        workflow_id = runtime.workflow_id(MediaCapability.VIDEO_I2VA)
    except MediaRuntimeConfigurationError:
        return "workflow_not_configured"
    try:
        load_h3_workflow_profile(workflow_id=workflow_id)
    except (OSError, ValueError):
        return "profile_invalid"
    return None


def _load_h3_reference_workflow_profile(*, workflow_id: str) -> WorkflowProfile:
    if not isinstance(workflow_id, str):
        raise ValueError("H3 reference workflow ID must contain digits only")
    normalized_workflow_id = workflow_id.strip()
    if not normalized_workflow_id or not normalized_workflow_id.isdecimal():
        raise ValueError("H3 reference workflow ID must contain digits only")

    profile = WorkflowProfile.model_validate_json(
        _H3_REFERENCE_PROFILE_PATH.read_text(encoding="utf-8")
    )
    if (
        profile.provider != "runninghub"
        or profile.capabilities != [MediaCapability.VIDEO_REF2VA]
        or profile.bindings
        != {"timeline_data": {"node_id": "12", "field": "timeline_data"}}
        or profile.outputs
        != {"video": {"node_id": "7", "media_type": "video"}}
    ):
        raise ValueError("H3 reference profile metadata is incompatible")

    payload = profile.model_dump()
    payload["workflow_id"] = normalized_workflow_id
    return WorkflowProfile.model_validate(payload)


def _h3_reference_unavailable_reason(
    store: MediaCapabilityStore,
    resolver: CredentialResolver,
    definition: VideoWorkflowDefinition,
) -> str | None:
    account = store.get_provider("runninghub-main")
    if account is None or account.provider_type != "runninghub" or not account.enabled:
        return "provider_not_configured"
    try:
        runtime = load_runninghub_runtime_configuration(store, resolver)
    except MediaRuntimeConfigurationError:
        return "credential_unavailable"
    try:
        workflow_id = runtime.workflow_id_for_key(definition.workflow_settings_key)
    except MediaRuntimeConfigurationError:
        return "workflow_not_configured"
    try:
        _load_h3_reference_workflow_profile(workflow_id=workflow_id)
    except (OSError, ValueError):
        return "profile_invalid"
    return None


def build_video_workflow_registry(
    store: MediaCapabilityStore,
    resolver: CredentialResolver,
) -> VideoWorkflowRegistry:
    unavailable_reason = _h3_unavailable_reason(store, resolver)
    workflows = store.get_runninghub_workflows()
    reference_definition = VideoWorkflowDefinition(
        id=H3_REFERENCE_WORKFLOW_ID,
        label="RunningHub MiniMax H3 · Ref",
        provider="runninghub",
        adapter_key="minimax-h3-ref",
        workflow_settings_key=RunningHubWorkflowSettingsKey.VIDEO_MINIMAX_H3_REF,
        scenes=frozenset({VideoWorkflowScene.NARRATIVE_GROUP}),
        supported_modes=("auto", "i2va", "fl2va"),
        default_mode="auto",
        is_default=False,
        reference_policy=VideoReferencePolicy(
            required=True,
            min_images=1,
            max_images=workflows.video_minimax_h3_ref_max_images,
            source_kinds=(
                "character_identity",
                "scene_master",
                "prop_reference",
                "temporary_upload",
            ),
        ),
    )
    reference_unavailable_reason = _h3_reference_unavailable_reason(
        store,
        resolver,
        reference_definition,
    )
    if reference_unavailable_reason is not None:
        reference_definition = VideoWorkflowDefinition.model_validate(
            reference_definition.model_dump()
            | {
                "available": False,
                "unavailable_reason": reference_unavailable_reason,
            }
        )
    return VideoWorkflowRegistry(
        (
            VideoWorkflowDefinition(
                id=H3_WORKFLOW_ID,
                label="RunningHub MiniMax H3",
                provider="runninghub",
                adapter_key="minimax-h3",
                workflow_settings_key=(
                    RunningHubWorkflowSettingsKey.VIDEO_MINIMAX_H3
                ),
                scenes=frozenset({VideoWorkflowScene.NARRATIVE_GROUP}),
                supported_modes=("auto", "i2va", "fl2va"),
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
                available=unavailable_reason is None,
                unavailable_reason=unavailable_reason,
            ),
            reference_definition,
        )
    )


__all__ = [
    "H3_WORKFLOW_ID",
    "H3_REFERENCE_WORKFLOW_ID",
    "VideoReferencePolicy",
    "VideoWorkflowDefinition",
    "VideoWorkflowRegistry",
    "VideoWorkflowScene",
    "VideoWorkflowUnavailable",
    "build_video_workflow_registry",
]
