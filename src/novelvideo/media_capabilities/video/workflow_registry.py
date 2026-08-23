"""Scene-aware registry of executable production video workflows."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from novelvideo.media_capabilities.models import MediaCapability
from novelvideo.media_capabilities.runtime.configuration import (
    MediaRuntimeConfigurationError,
    load_runninghub_runtime_configuration,
)
from novelvideo.media_capabilities.runtime.credentials import CredentialResolver
from novelvideo.media_capabilities.store import MediaCapabilityStore
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
    available: bool = True
    unavailable_reason: str | None = None


class VideoWorkflowUnavailable(LookupError):
    """A workflow cannot be used for the requested scene."""


class VideoWorkflowRegistry:
    def __init__(self, definitions: tuple[VideoWorkflowDefinition, ...]) -> None:
        self._definitions = definitions

    def list(
        self, scene: VideoWorkflowScene | str | None = None
    ) -> tuple[VideoWorkflowDefinition, ...]:
        if scene is None:
            return self._definitions
        requested_scene = VideoWorkflowScene(scene)
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
        requested_scene = VideoWorkflowScene(scene)
        definition = next(
            (item for item in self._definitions if item.id == identifier), None
        )
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
        requested_scene = VideoWorkflowScene(scene)
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
    except Exception:
        return "profile_invalid"
    return None


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
