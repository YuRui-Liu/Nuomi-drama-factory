"""Public, credential-free catalog of configured image models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from novelvideo.media_capabilities.models import GRSAI_IMAGE_MODELS
from novelvideo.media_capabilities.runtime.configuration import (
    MediaRuntimeConfigurationError,
    load_grsai_runtime_configuration,
)
from novelvideo.media_capabilities.runtime.credentials import CredentialResolver
from novelvideo.media_capabilities.store import MediaCapabilityStore


class ImageModelCatalogItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    label: str
    provider_id: str
    provider: str = "grsai"


def list_image_models(
    store: MediaCapabilityStore,
    resolver: CredentialResolver,
) -> tuple[ImageModelCatalogItem, ...]:
    try:
        runtime = load_grsai_runtime_configuration(store, resolver)
    except MediaRuntimeConfigurationError:
        return ()

    default_model = runtime.model
    model_ids = (default_model, *sorted(GRSAI_IMAGE_MODELS - {default_model}))
    return tuple(
        ImageModelCatalogItem(
            id=model_id,
            label=model_id,
            provider_id=runtime.account.id,
        )
        for model_id in model_ids
    )


__all__ = ["ImageModelCatalogItem", "list_image_models"]
