"""Scene-aware registry of executable production video workflows."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from novelvideo.media_capabilities.models import MediaCapability
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


class VideoWorkflowScene(StrEnum):
    NARRATIVE_GROUP = "narrative_group"
    SINGLE_BEAT = "single_beat"
    FREEZONE = "freezone"


class VideoWorkflowDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    label: str
    provider: str
    adapter_key: str
    scenes: frozenset[VideoWorkflowScene]
    supported_modes: tuple[str, ...]
    default_mode: str = "auto"
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
            if definition.available:
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


def _h3_parameters() -> tuple[VideoWorkflowParameterDefinition, ...]:
    return (
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
                VideoWorkflowParameterOption(value="enforce", label="启用新编译"),
            ),
        ),
    )


def build_video_workflow_registry(
    store: MediaCapabilityStore,
    resolver: CredentialResolver,
) -> VideoWorkflowRegistry:
    unavailable_reason = _h3_unavailable_reason(store, resolver)
    return VideoWorkflowRegistry(
        (
            VideoWorkflowDefinition(
                id=H3_WORKFLOW_ID,
                label="RunningHub MiniMax H3",
                provider="runninghub",
                adapter_key="minimax-h3",
                scenes=frozenset({VideoWorkflowScene.NARRATIVE_GROUP}),
                supported_modes=("auto", "i2va", "fl2va"),
                parameters=_h3_parameters(),
                available=unavailable_reason is None,
                unavailable_reason=unavailable_reason,
            ),
        )
    )


__all__ = [
    "H3_WORKFLOW_ID",
    "VideoWorkflowDefinition",
    "VideoWorkflowRegistry",
    "VideoWorkflowScene",
    "VideoWorkflowUnavailable",
    "build_video_workflow_registry",
]
