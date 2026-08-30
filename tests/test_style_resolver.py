import pytest
from pydantic import ValidationError

from novelvideo.director_plan.models import ShotPlan
from novelvideo.extension_styles import ExtensionStyle
from novelvideo.styles.resolver import StyleResolver, apply_director_projection


def extension_style() -> ExtensionStyle:
    return ExtensionStyle.from_dict(
        {
            "id": "drama_ext.test_ink",
            "name": "Test Ink",
            "category": "chinese",
            "summary": "Structured style for resolver tests.",
            "prompt_fragment": {
                "medium": ["ink wash"],
                "rendering": ["dry brush texture"],
                "lighting": ["soft side light"],
                "color": ["restrained black and vermilion"],
                "camera": ["static wide shot"],
                "constraints": ["negative prompt: no text"],
            },
            "use_cases": ["poetic imagery"],
            "preview_asset": "/images/extension-styles/test-ink.webp",
            "source": {
                "license_review": "approved",
                "imported_revision": "a" * 40,
                "local_path": "private/catalog/source.json",
            },
            "version": "1.2.3",
        }
    )


def shot_plan(**updates: object) -> ShotPlan:
    values: dict[str, object] = {
        "id": "shot-1",
        "source_span_ids": ("span-1",),
        "subject": "hero",
        "action": "opens the door",
        "visible_start_state": "door closed",
        "visible_end_state": "door open",
        "camera_motion": "handheld push-in",
        "duration_seconds": 3,
    }
    values.update(updates)
    return ShotPlan(**values)  # type: ignore[arg-type]


def test_resolver_builds_stable_purpose_specific_snapshot() -> None:
    catalog = (extension_style(),)

    first = StyleResolver(catalog).resolve("drama_ext.test_ink", None)
    second = StyleResolver(catalog).resolve("drama_ext.test_ink", None)

    assert first == second
    assert first.style_hash == second.style_hash
    assert first.projections.director == (
        "ink wash, static wide shot, negative prompt: no text"
    )
    assert first.projections.image == (
        "ink wash, dry brush texture, soft side light, "
        "restrained black and vermilion, static wide shot, negative prompt: no text"
    )
    assert first.projections.video == (
        "ink wash, dry brush texture, soft side light, restrained black and vermilion"
    )
    assert "negative prompt" not in first.projections.video.casefold()
    assert "static wide shot" not in first.projections.video.casefold()
    assert first.projections.panel_tag == (
        "ink wash, dry brush texture, soft side light, restrained black and vermilion, "
        "static wide shot, negative prompt: no text"
    )


def test_override_selects_a_different_style_and_hash() -> None:
    base = extension_style()
    override = ExtensionStyle.from_dict(
        {
            **base.projection_input(),
            "id": "drama_ext.override",
            "name": "Override",
            "preview_asset": "/images/extension-styles/override.webp",
            "source": {
                "license_review": "approved",
                "imported_revision": "b" * 40,
            },
        }
    )
    resolver = StyleResolver((base, override))

    base_snapshot = resolver.resolve(base.id, None)
    overridden = resolver.resolve(base.id, override.id)

    assert overridden.style_id == override.id
    assert overridden.style_hash != base_snapshot.style_hash


def test_explicit_shot_camera_wins_over_style_tendency() -> None:
    snapshot = StyleResolver((extension_style(),)).resolve("drama_ext.test_ink", None)
    shot = shot_plan(camera_motion="handheld push-in")

    resolved = apply_director_projection(shot, snapshot)

    assert resolved.camera_motion == "handheld push-in"
    assert resolved.camera_angle == shot.camera_angle
    assert resolved.composition == shot.composition


def test_style_snapshot_is_immutable() -> None:
    snapshot = StyleResolver((extension_style(),)).resolve("drama_ext.test_ink", None)

    with pytest.raises(ValidationError, match="frozen"):
        snapshot.projections.video = "changed"  # type: ignore[misc]
