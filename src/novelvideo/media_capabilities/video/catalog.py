"""Public, credential-free catalog of production video models."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from novelvideo.media_capabilities.models import MediaCapability
from novelvideo.media_capabilities.resolver import (
    AvailableImplementations,
    ConfigurationError,
    resolve_priority_route,
)
from novelvideo.media_capabilities.runtime.credentials import CredentialResolver
from novelvideo.media_capabilities.store import MediaCapabilityStore
from novelvideo.media_capabilities.video.parameters import (
    VideoWorkflowParameterDefinition,
)
from novelvideo.media_capabilities.video.workflow_registry import (
    H3_REFERENCE_WORKFLOW_ID,
    H3_WORKFLOW_ID,
    VideoReferencePolicy,
    build_video_workflow_registry,
)
from novelvideo.shot_continuity.models import H3ResolvedMode


H3_MODEL_ID = H3_WORKFLOW_ID
H3_REFERENCE_MODEL_ID = H3_REFERENCE_WORKFLOW_ID

_H3_RESOLVED_MODES: tuple[H3ResolvedMode, ...] = (
    "t2va",
    "i2va",
    "fl2va",
    "l2va",
    "ref2va",
)


class H3ModeCapability(BaseModel):
    """Public input contract for one resolved MiniMax H3 mode."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: H3ResolvedMode
    enabled: bool
    reason: str | None = None
    requires_first_frame: bool = False
    requires_last_frame: bool = False
    requires_references: bool = False


class VideoModelCatalogItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    label: str
    provider: str
    available: bool
    supported_modes: tuple[str, ...]
    default_mode: str = "auto"
    parameters: tuple[VideoWorkflowParameterDefinition, ...] = ()
    reference_policy: VideoReferencePolicy = VideoReferencePolicy()
    unavailable_reason: str | None = None


def h3_mode_capabilities(
    *,
    supported_modes: Sequence[str],
    reference_unavailable_reason: str | None,
) -> tuple[H3ModeCapability, ...]:
    requirements = {
        "t2va": (False, False, False),
        "i2va": (True, False, False),
        "fl2va": (True, True, False),
        "l2va": (False, True, False),
        "ref2va": (False, False, True),
    }
    capabilities = []
    for mode in _H3_RESOLVED_MODES:
        enabled = mode in supported_modes
        reason = None if enabled else "workflow_capability_unverified"
        if mode == "ref2va" and not enabled:
            reason = reference_unavailable_reason or "hybrid_input_unverified"
        first_frame, last_frame, references = requirements[mode]
        capabilities.append(
            H3ModeCapability(
                mode=mode,
                enabled=enabled,
                reason=reason,
                requires_first_frame=first_frame,
                requires_last_frame=last_frame,
                requires_references=references,
            )
        )
    return tuple(capabilities)


def list_video_models(
    store: MediaCapabilityStore,
    resolver: CredentialResolver,
) -> tuple[VideoModelCatalogItem, ...]:
    return tuple(
        VideoModelCatalogItem(
            id=definition.id,
            label=definition.label,
            provider=definition.provider,
            available=definition.available,
            supported_modes=definition.supported_modes,
            default_mode=definition.default_mode,
            parameters=definition.parameters,
            reference_policy=definition.reference_policy,
            unavailable_reason=definition.unavailable_reason,
        )
        for definition in build_video_workflow_registry(store, resolver).list()
    )


def resolve_video_model_route(
    *,
    project_runninghub: str | None,
    local_custom: Sequence[str],
    official_catalog: Sequence[str],
    system_default: str,
    available: AvailableImplementations,
) -> tuple[str, ...]:
    """Resolve video models in the product-defined source priority order.

    The project-explicit slot is intentionally RunningHub H3-specific. Generic
    Seedance/NewAPI entries belong to the lower custom/catalog/default layers.
    """
    if isinstance(local_custom, (str, bytes)):
        raise ConfigurationError("local_custom must not be str or bytes")
    if isinstance(official_catalog, (str, bytes)):
        raise ConfigurationError("official_catalog must not be str or bytes")

    explicit = str(project_runninghub or "").strip()
    if explicit and explicit != H3_MODEL_ID:
        raise ConfigurationError(
            "project_runninghub must be runninghub:minimax-h3"
        )
    return resolve_priority_route(
        MediaCapability.VIDEO_I2VA,
        (
            (explicit,) if explicit else (),
            tuple(local_custom),
            tuple(official_catalog),
            (system_default,),
        ),
        available,
    )


__all__ = [
    "H3_MODEL_ID",
    "H3_REFERENCE_MODEL_ID",
    "H3ModeCapability",
    "VideoModelCatalogItem",
    "h3_mode_capabilities",
    "list_video_models",
    "resolve_video_model_route",
]
