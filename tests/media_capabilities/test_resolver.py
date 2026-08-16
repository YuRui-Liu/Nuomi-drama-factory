from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from novelvideo.media_capabilities.models import (
    CapabilityImplementation,
    MediaCapability,
)
from novelvideo.media_capabilities.resolver import (
    ConfigurationError,
    merge_parameters,
    resolve_route,
)


def test_merge_parameters_uses_task_to_provider_precedence() -> None:
    result = merge_parameters(
        {"model": "provider", "steps": 10, "nested": {"source": "provider"}},
        {"model": "system", "steps": 20},
        {"model": "project"},
        {"model": "task", "items": [1, {"enabled": True}]},
    )

    assert result == {
        "model": "task",
        "steps": 20,
        "nested": {"source": "provider"},
        "items": [1, {"enabled": True}],
    }


def test_merge_parameters_none_inherits_lower_layer_value() -> None:
    result = merge_parameters(
        {"model": "provider", "seed": 7},
        {"model": "system", "seed": None},
        {"model": None},
        {"model": None, "seed": None},
    )

    assert result == {"model": "system", "seed": 7}


def test_merge_parameters_does_not_mutate_or_alias_inputs() -> None:
    layers = (
        {"nested": {"values": [1, 2]}},
        {"other": {"enabled": True}},
        {"nested": None},
        {"items": ["a", "b"]},
    )
    originals = copy.deepcopy(layers)

    result = merge_parameters(*layers)
    result["nested"]["values"].append(3)
    result["items"].append("c")

    assert layers == originals


def test_resolve_route_uses_explicit_fallback_when_default_is_unavailable() -> None:
    assert resolve_route(
        MediaCapability.VIDEO_T2VA,
        default_implementation="primary",
        fallback_chain=("fallback",),
        available={"fallback"},
    ) == ("fallback",)


def test_resolve_route_returns_all_available_explicit_candidates_in_order() -> None:
    assert resolve_route(
        MediaCapability.VIDEO_T2VA,
        default_implementation="primary",
        fallback_chain=("fallback-a", "fallback-b"),
        available={"fallback-b", "primary", "fallback-a", "unrelated"},
    ) == ("primary", "fallback-a", "fallback-b")


def test_resolve_route_never_adds_available_implementation_outside_chain() -> None:
    assert resolve_route(
        MediaCapability.VIDEO_T2VA,
        default_implementation="primary",
        fallback_chain=("fallback",),
        available={"primary", "fallback", "unrelated"},
    ) == ("primary", "fallback")


def test_resolve_route_raises_when_all_explicit_routes_are_unavailable() -> None:
    with pytest.raises(ConfigurationError, match="no available implementation"):
        resolve_route(
            MediaCapability.VIDEO_T2VA,
            default_implementation="primary",
            fallback_chain=("fallback-a", "fallback-b"),
            available=set(),
        )


def test_resolve_route_filters_mapping_entry_with_wrong_capability() -> None:
    available = {
        "wrong": MediaCapability.IMAGE_SINGLE,
        "right": MediaCapability.VIDEO_T2VA,
    }

    assert resolve_route(
        MediaCapability.VIDEO_T2VA,
        default_implementation="wrong",
        fallback_chain=("right",),
        available=available,
    ) == ("right",)


def test_resolve_route_accepts_capability_implementation_mapping() -> None:
    available = {
        "primary": CapabilityImplementation(
            id="primary",
            capability=MediaCapability.VIDEO_T2VA,
            provider_account="provider",
        )
    }

    assert resolve_route(
        MediaCapability.VIDEO_T2VA,
        default_implementation="primary",
        fallback_chain=(),
        available=available,
    ) == ("primary",)


def test_resolve_route_raises_when_mapping_has_only_wrong_capability() -> None:
    with pytest.raises(ConfigurationError, match="no available implementation"):
        resolve_route(
            MediaCapability.VIDEO_T2VA,
            default_implementation="primary",
            fallback_chain=(),
            available={"primary": MediaCapability.IMAGE_SINGLE},
        )


@pytest.mark.parametrize("default", ["", "   "])
def test_resolve_route_rejects_empty_default(default: str) -> None:
    with pytest.raises(ConfigurationError, match="default implementation must not be empty"):
        resolve_route(
            MediaCapability.VIDEO_T2VA,
            default_implementation=default,
            fallback_chain=(),
            available=set(),
        )


@pytest.mark.parametrize("default", [" primary", "primary ", " primary "])
def test_resolve_route_rejects_default_with_surrounding_whitespace(
    default: str,
) -> None:
    with pytest.raises(
        ConfigurationError,
        match="default implementation must not have surrounding whitespace",
    ):
        resolve_route(
            MediaCapability.VIDEO_T2VA,
            default_implementation=default,
            fallback_chain=(),
            available={default},
        )


@pytest.mark.parametrize("fallback_chain", ["fallback", b"fallback"])
def test_resolve_route_rejects_string_or_bytes_fallback_chain(
    fallback_chain: object,
) -> None:
    with pytest.raises(
        ConfigurationError,
        match="fallback chain must not be str or bytes",
    ):
        resolve_route(
            MediaCapability.VIDEO_T2VA,
            default_implementation="primary",
            fallback_chain=fallback_chain,  # type: ignore[arg-type]
            available={"primary", "fallback"},
        )


@pytest.mark.parametrize("fallback", ["", "   "])
def test_resolve_route_rejects_empty_fallback_id(fallback: str) -> None:
    with pytest.raises(ConfigurationError, match="fallback ID must not be empty"):
        resolve_route(
            MediaCapability.VIDEO_T2VA,
            default_implementation="primary",
            fallback_chain=(fallback,),
            available={"primary", fallback},
        )


@pytest.mark.parametrize("fallback", [" fallback", "fallback ", " fallback "])
def test_resolve_route_rejects_fallback_id_with_surrounding_whitespace(
    fallback: str,
) -> None:
    with pytest.raises(
        ConfigurationError,
        match="fallback ID must not have surrounding whitespace",
    ):
        resolve_route(
            MediaCapability.VIDEO_T2VA,
            default_implementation="primary",
            fallback_chain=(fallback,),
            available={"primary", fallback},
        )


def test_resolve_route_rejects_duplicate_fallbacks() -> None:
    with pytest.raises(ConfigurationError, match="fallback chain must not contain duplicates"):
        resolve_route(
            MediaCapability.VIDEO_T2VA,
            default_implementation="primary",
            fallback_chain=("fallback", "fallback"),
            available={"primary", "fallback"},
        )


def test_resolve_route_rejects_default_in_fallback_chain() -> None:
    with pytest.raises(ConfigurationError, match="fallback chain must not contain default"):
        resolve_route(
            MediaCapability.VIDEO_T2VA,
            default_implementation="primary",
            fallback_chain=("fallback", "primary"),
            available={"primary", "fallback"},
        )
