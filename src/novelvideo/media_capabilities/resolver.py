from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import AbstractSet, TypeAlias

from pydantic import JsonValue

from novelvideo.media_capabilities.models import (
    CapabilityProfile,
    CapabilityRequirement,
    CapabilityImplementation,
    MediaCapability,
    RoutingPolicy,
)


ParameterLayer: TypeAlias = Mapping[str, JsonValue | None]
AvailableImplementations: TypeAlias = (
    AbstractSet[str]
    | Mapping[str, MediaCapability | CapabilityImplementation]
)


class ConfigurationError(ValueError):
    """Raised when media routing configuration cannot produce a valid route."""


def resolve_policy_route(
    policy: RoutingPolicy,
    *,
    profiles: Mapping[str, CapabilityProfile],
    requirement: CapabilityRequirement,
    user_selection: str | None = None,
    project_default: str | None = None,
) -> tuple[str, ...]:
    """Resolve task > project > stage > explicitly approved equivalent fallback."""
    if policy.capability != requirement.capability:
        raise ConfigurationError("routing policy capability does not match requirement")
    ordered: list[str] = []
    for implementation_id in (
        user_selection,
        project_default,
        policy.default_implementation,
        *policy.fallback_chain,
    ):
        if implementation_id and implementation_id not in ordered:
            ordered.append(implementation_id)

    compatible = tuple(
        implementation_id
        for implementation_id in ordered
        if implementation_id in profiles
        and profiles[implementation_id].satisfies(requirement)
    )
    if compatible:
        return compatible
    raise ConfigurationError(
        f"no approved equivalent implementation satisfies {requirement.capability.value}"
    )


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


def resolve_priority_route(
    capability: MediaCapability,
    priority_layers: Sequence[Sequence[str]],
    available: AvailableImplementations,
) -> tuple[str, ...]:
    """Resolve ordered source layers without allowing lower layers to jump ahead.

    Empty layers are allowed. Duplicate identifiers are kept at their highest
    priority occurrence so a catalog cannot demote a project selection.
    """
    if isinstance(priority_layers, (str, bytes)):
        raise ConfigurationError("priority layers must not be str or bytes")

    ordered: list[str] = []
    seen: set[str] = set()
    for layer in priority_layers:
        if isinstance(layer, (str, bytes)):
            raise ConfigurationError("priority layer must not be str or bytes")
        layer_seen: set[str] = set()
        for implementation_id in layer:
            if not isinstance(implementation_id, str):
                raise ConfigurationError("implementation ID must be a string")
            if not implementation_id.strip():
                raise ConfigurationError("implementation ID must not be empty")
            if implementation_id != implementation_id.strip():
                raise ConfigurationError(
                    "implementation ID must not have surrounding whitespace"
                )
            if implementation_id in layer_seen:
                raise ConfigurationError(
                    "priority layer must not contain duplicates"
                )
            layer_seen.add(implementation_id)
            if implementation_id not in seen:
                seen.add(implementation_id)
                ordered.append(implementation_id)

    if not ordered:
        raise ConfigurationError("priority route must contain an implementation")
    return resolve_route(
        capability,
        default_implementation=ordered[0],
        fallback_chain=ordered[1:],
        available=available,
    )


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
    if not isinstance(default_implementation, str):
        raise ConfigurationError("default implementation must be a string")

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
        if not isinstance(fallback, str):
            raise ConfigurationError("fallback ID must be a string")
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
            if isinstance(implementation, CapabilityImplementation):
                implementation_capability = implementation.capability
            elif isinstance(implementation, MediaCapability):
                implementation_capability = implementation
            else:
                raise ConfigurationError(
                    "available mapping values must be MediaCapability or "
                    "CapabilityImplementation instances"
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
