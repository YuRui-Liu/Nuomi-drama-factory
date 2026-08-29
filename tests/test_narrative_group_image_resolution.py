from __future__ import annotations

import pytest

from novelvideo.narrative_groups.image_resolution import (
    InvalidGridImageResolution,
    resolve_grid_image_resolution,
    supported_grid_image_sizes,
)


def test_supported_grid_image_sizes_are_model_specific() -> None:
    assert supported_grid_image_sizes("gpt-image-2") == ("1K",)
    assert supported_grid_image_sizes("gpt-image-2-vip") == ("1K", "2K", "4K")
    assert supported_grid_image_sizes("unknown-model") == ()


@pytest.mark.parametrize(
    ("tier", "cell_aspect_ratio", "rows", "columns", "logical_ratio", "size"),
    [
        ("1K", "16:9", 1, 1, "16:9", "1280x720"),
        ("2K", "9:16", 1, 1, "9:16", "1152x2048"),
        ("4K", "4:3", 2, 2, "4:3", "3264x2448"),
        ("1K", "16:9", 3, 3, "16:9", "1280x720"),
    ],
)
def test_standard_logical_ratios_use_the_tier_size_table(
    tier: str,
    cell_aspect_ratio: str,
    rows: int,
    columns: int,
    logical_ratio: str,
    size: str,
) -> None:
    resolution = resolve_grid_image_resolution(
        "gpt-image-2-vip", tier, cell_aspect_ratio, rows, columns
    )

    assert resolution.requested_tier == tier
    assert resolution.logical_aspect_ratio == logical_ratio
    assert resolution.provider_aspect_ratio == logical_ratio
    assert resolution.provider_size == size
    assert resolution.requires_aspect_normalization is False
    assert resolution.reason is None


@pytest.mark.parametrize(
    ("cell_aspect_ratio", "rows", "columns", "logical_ratio", "size"),
    [
        ("9:16", 1, 2, "9:8", "1008x896"),
        ("9:16", 2, 3, "27:32", "864x1024"),
        ("16:9", 2, 3, "8:3", "1664x624"),
    ],
)
def test_custom_grid_ratios_preserve_the_reduced_logical_ratio(
    cell_aspect_ratio: str,
    rows: int,
    columns: int,
    logical_ratio: str,
    size: str,
) -> None:
    resolution = resolve_grid_image_resolution(
        "gpt-image-2-vip", "1K", cell_aspect_ratio, rows, columns
    )

    assert resolution.logical_aspect_ratio == logical_ratio
    assert resolution.provider_aspect_ratio == logical_ratio
    assert resolution.provider_size == size
    assert resolution.width % 16 == 0
    assert resolution.height % 16 == 0
    assert 655_360 <= resolution.width * resolution.height <= 8_294_400


def test_ultrawide_grid_clamps_only_the_provider_request() -> None:
    resolution = resolve_grid_image_resolution(
        "gpt-image-2-vip", "1K", "16:9", rows=1, columns=2
    )

    assert resolution.logical_aspect_ratio == "32:9"
    assert resolution.provider_aspect_ratio == "3:1"
    assert resolution.provider_size == "1728x576"
    assert resolution.requires_aspect_normalization is True
    assert resolution.reason is not None
    assert "3:1" in resolution.reason


def test_custom_4k_resolution_honors_provider_limits() -> None:
    resolution = resolve_grid_image_resolution(
        "gpt-image-2-vip", "4K", "16:9", rows=2, columns=3
    )

    assert resolution.logical_aspect_ratio == "8:3"
    assert resolution.provider_size == "3840x1440"
    assert max(resolution.width, resolution.height) <= 3840
    assert resolution.width * resolution.height <= 8_294_400


@pytest.mark.parametrize(
    ("model", "tier", "cell_aspect_ratio", "rows", "columns"),
    [
        ("gpt-image-2", "2K", "16:9", 1, 1),
        ("unknown-model", "1K", "16:9", 1, 1),
        ("gpt-image-2-vip", "8K", "16:9", 1, 1),
        ("gpt-image-2-vip", "1K", "bad", 1, 1),
        ("gpt-image-2-vip", "1K", "16:9", 0, 1),
    ],
)
def test_invalid_grid_resolution_requests_are_rejected(
    model: str,
    tier: str,
    cell_aspect_ratio: str,
    rows: int,
    columns: int,
) -> None:
    with pytest.raises(InvalidGridImageResolution):
        resolve_grid_image_resolution(model, tier, cell_aspect_ratio, rows, columns)
