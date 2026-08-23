"""Public, credential-free catalog of production video models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from novelvideo.media_capabilities.runtime.credentials import CredentialResolver
from novelvideo.media_capabilities.store import MediaCapabilityStore
from novelvideo.media_capabilities.video.workflow_registry import (
    H3_WORKFLOW_ID,
    build_video_workflow_registry,
)


H3_MODEL_ID = H3_WORKFLOW_ID


class VideoModelCatalogItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    label: str
    provider: str
    available: bool
    supported_modes: tuple[str, ...]
    default_mode: str = "auto"
    unavailable_reason: str | None = None


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
            supported_modes=definition.modes,
            default_mode=definition.default_mode,
            unavailable_reason=definition.unavailable_reason,
        )
        for definition in build_video_workflow_registry(store, resolver).list()
    )


__all__ = ["H3_MODEL_ID", "VideoModelCatalogItem", "list_video_models"]
