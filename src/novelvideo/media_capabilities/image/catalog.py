"""Public, credential-free catalog of configured image models."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

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


class ImageModelSelectionKind(StrEnum):
    EMPTY = "empty"
    GRSAI = "grsai"
    LEGACY = "legacy"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ResolvedImageModel:
    model: str
    requested_model: str
    resolution_source: Literal["explicit", "project", "runtime"]


def legacy_image_model_values(
    selections: Mapping[str, Mapping[str, str]],
    aliases: Mapping[str, str],
) -> frozenset[str]:
    """Return the live legacy keys, aliases, and configured downstream models."""
    return frozenset(
        {
            *selections,
            *aliases,
            *(str(entry.get("model") or "").strip() for entry in selections.values()),
        }
        - {""}
    )


def classify_image_model_selection(
    value: str | None,
    *,
    legacy_values: Collection[str],
) -> ImageModelSelectionKind:
    model = str(value or "").strip()
    if not model:
        return ImageModelSelectionKind.EMPTY
    if model in GRSAI_IMAGE_MODELS:
        return ImageModelSelectionKind.GRSAI
    if model in legacy_values:
        return ImageModelSelectionKind.LEGACY
    return ImageModelSelectionKind.UNKNOWN


def resolve_grsai_image_model(
    *,
    requested_model: str | None,
    project_model: str | None,
    runtime_model: str,
    legacy_values: Collection[str],
) -> ResolvedImageModel:
    requested = str(requested_model or "").strip()
    project = str(project_model or "").strip()
    requested_kind = classify_image_model_selection(
        requested, legacy_values=legacy_values
    )
    if requested_kind is ImageModelSelectionKind.GRSAI:
        return ResolvedImageModel(requested, requested, "explicit")
    if requested_kind is ImageModelSelectionKind.UNKNOWN:
        raise ValueError(f"Unsupported GRSAI image model: {requested}")
    if requested_kind is ImageModelSelectionKind.LEGACY:
        return ResolvedImageModel(runtime_model, requested, "runtime")

    project_kind = classify_image_model_selection(project, legacy_values=legacy_values)
    if project_kind is ImageModelSelectionKind.GRSAI:
        return ResolvedImageModel(project, project, "project")
    if project_kind is ImageModelSelectionKind.UNKNOWN:
        raise ValueError(f"Unsupported GRSAI image model: {project}")
    return ResolvedImageModel(runtime_model, project or runtime_model, "runtime")


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


__all__ = [
    "ImageModelCatalogItem",
    "ImageModelSelectionKind",
    "ResolvedImageModel",
    "classify_image_model_selection",
    "legacy_image_model_values",
    "list_image_models",
    "resolve_grsai_image_model",
]
