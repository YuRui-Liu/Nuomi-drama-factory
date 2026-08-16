from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import AbstractSet, TypeAlias

from pydantic import JsonValue

from novelvideo.media_capabilities.models import (
    CapabilityImplementation,
    MediaCapability,
)


ParameterLayer: TypeAlias = Mapping[str, JsonValue | None]
AvailableImplementations: TypeAlias = (
    AbstractSet[str]
    | Mapping[str, MediaCapability | CapabilityImplementation]
)


class ConfigurationError(ValueError):
    """Raised when media routing configuration cannot produce a valid route."""


def merge_parameters(
    provider_defaults: ParameterLayer,
    system_defaults: ParameterLayer,
    project_overrides: ParameterLayer,
    task_overrides: ParameterLayer,
) -> dict[str, JsonValue]:
    """Merge parameter layers without allowing ``None`` to erase lower values."""
    merged: dict[str, JsonValue] = {}
    for layer in (
        provider_defaults,
        system_defaults,
        project_overrides,
        task_overrides,
    ):
        for name, value in layer.items():
            if value is not None:
                merged[name] = deepcopy(value)
    return merged


def resolve_route(
    capability: MediaCapability,
    default_implementation: str,
    fallback_chain: Sequence[str],
    available: AvailableImplementations,
) -> tuple[str, ...]:
    """Return all available, capability-compatible explicit routes in order.

    A set supplies availability only. A mapping additionally verifies each
    implementation's capability from either a ``MediaCapability`` value or a
    ``CapabilityImplementation`` value.
    """
    if not default_implementation.strip():
        raise ConfigurationError("default implementation must not be empty")
    if default_implementation != default_implementation.strip():
        raise ConfigurationError(
            "default implementation must not have surrounding whitespace"
        )

    if isinstance(fallback_chain, (str, bytes)):
        raise ConfigurationError("fallback chain must not be str or bytes")

    fallbacks = tuple(fallback_chain)
    for fallback in fallbacks:
        if not fallback.strip():
            raise ConfigurationError("fallback ID must not be empty")
        if fallback != fallback.strip():
            raise ConfigurationError(
                "fallback ID must not have surrounding whitespace"
            )
    if len(fallbacks) != len(set(fallbacks)):
        raise ConfigurationError("fallback chain must not contain duplicates")
    if default_implementation in fallbacks:
        raise ConfigurationError("fallback chain must not contain default")

    candidates: list[str] = []
    for implementation_id in (default_implementation, *fallbacks):
        if implementation_id not in available:
            continue
        if isinstance(available, Mapping):
            implementation = available[implementation_id]
            implementation_capability = (
                implementation.capability
                if isinstance(implementation, CapabilityImplementation)
                else implementation
            )
            if implementation_capability != capability:
                continue
        candidates.append(implementation_id)

    if candidates:
        return tuple(candidates)

    raise ConfigurationError(
        f"no available implementation for capability {capability.value} "
        "in the explicit route chain"
    )
