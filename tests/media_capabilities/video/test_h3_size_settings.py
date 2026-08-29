from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from novelvideo.media_capabilities.video.h3_size_settings import (
    H3SizeSettingError,
    resolve_h3_size_setting,
)


@pytest.mark.parametrize(
    ("resolution", "aspect_ratio", "megapixels", "width", "height"),
    [
        ("720p", "9:16", 0.9, 736, 1280),
        ("720p", "16:9", 0.9, 1280, 736),
        ("1080p", "9:16", 2.0, 1088, 1920),
        ("1080p", "16:9", 2.0, 1920, 1088),
    ],
)
def test_resolve_h3_size_setting_uses_director_product_presets(
    resolution: str,
    aspect_ratio: str,
    megapixels: float,
    width: int,
    height: int,
) -> None:
    setting = resolve_h3_size_setting(resolution, aspect_ratio)

    assert setting.resolution == resolution
    assert setting.aspect_ratio == aspect_ratio
    assert setting.megapixels == megapixels
    assert setting.multiple == 32
    assert (setting.width, setting.height) == (width, height)
    assert setting.long_edge == max(width, height)
    assert setting.ref_max_size == setting.long_edge


def test_h3_size_setting_is_frozen_and_slotted() -> None:
    setting = resolve_h3_size_setting("720p", "9:16")

    with pytest.raises(FrozenInstanceError):
        setting.width = 1280
    assert not hasattr(setting, "__dict__")


@pytest.mark.parametrize(
    ("resolution", "aspect_ratio", "message"),
    [
        ("4k", "9:16", "4k"),
        ("736x1280", "9:16", "736x1280"),
        ("720p", "1:1", "1:1"),
    ],
)
def test_resolve_h3_size_setting_rejects_unknown_product_values(
    resolution: str,
    aspect_ratio: str,
    message: str,
) -> None:
    with pytest.raises(H3SizeSettingError, match=message):
        resolve_h3_size_setting(resolution, aspect_ratio)
