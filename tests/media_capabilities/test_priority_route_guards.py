from __future__ import annotations

import pytest

from novelvideo.media_capabilities.models import MediaCapability
from novelvideo.media_capabilities.resolver import (
    ConfigurationError,
    resolve_priority_route,
)
from novelvideo.media_capabilities.video.catalog import resolve_video_model_route


@pytest.mark.parametrize(
    ("field", "local_custom", "official_catalog"),
    [
        ("local_custom", "local:model", ()),
        ("official_catalog", (), b"official:model"),
    ],
)
def test_catalog_rejects_string_priority_collections(
    field: str,
    local_custom,
    official_catalog,
) -> None:
    with pytest.raises(ConfigurationError, match=field):
        resolve_video_model_route(
            project_runninghub=None,
            local_custom=local_custom,
            official_catalog=official_catalog,
            system_default="system:default",
            available={"system:default"},
        )


def test_priority_route_rejects_duplicate_identifier_within_layer() -> None:
    with pytest.raises(ConfigurationError, match="duplicates"):
        resolve_priority_route(
            MediaCapability.VIDEO_I2VA,
            (("local:model", "local:model"),),
            {"local:model"},
        )


@pytest.mark.parametrize("identifier", ["", "   ", 7])
def test_priority_route_rejects_empty_or_non_string_identifier(identifier) -> None:
    with pytest.raises(ConfigurationError, match="implementation ID"):
        resolve_priority_route(
            MediaCapability.VIDEO_I2VA,
            ((identifier,),),
            set(),
        )


def test_priority_route_rejects_invalid_available_mapping_value() -> None:
    with pytest.raises(ConfigurationError, match="available mapping"):
        resolve_priority_route(
            MediaCapability.VIDEO_I2VA,
            (("broken",),),
            {"broken": "not-a-capability"},
        )
