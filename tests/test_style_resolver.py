import json
from pathlib import Path

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


def test_jinshi_style_projects_full_image_and_h3_safe_video_contract() -> None:
    from novelvideo.services.style_service import StyleService

    first = StyleService.resolve_style_snapshot(
        "drama_ext.jinshi_ink_suspense"
    )
    second = StyleService.resolve_style_snapshot(
        "drama_ext.jinshi_ink_suspense"
    )

    assert "semi-realistic 2.5D Chinese ink-line animation" in first.projections.image
    assert "stable facial planes" in first.projections.video
    assert "stone-cyan, charcoal, and ash-white palette" in first.projections.video
    assert "restrained lens distortion" in first.projections.image
    assert "restrained lens distortion" not in first.projections.video
    assert "no non-diegetic typography" in first.projections.image
    assert "no non-diegetic typography" not in first.projections.video
    assert first.style_hash == second.style_hash
    assert first.projections == second.projections


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


def test_style_hash_uses_selected_payload_and_catalog_generation() -> None:
    selected = extension_style()
    unrelated = ExtensionStyle.from_dict(
        {
            **selected.projection_input(),
            "id": "drama_ext.unrelated",
            "name": "Unrelated",
            "preview_asset": "/images/extension-styles/unrelated.webp",
        }
    )

    first = StyleResolver(
        (selected,), catalog_generation=7, catalog_hash="catalog-a"
    ).resolve(selected.id, None)
    same_generation = StyleResolver(
        (selected, unrelated), catalog_generation=7, catalog_hash="catalog-b"
    ).resolve(selected.id, None)
    next_generation = StyleResolver(
        (selected,), catalog_generation=8, catalog_hash="catalog-a"
    ).resolve(selected.id, None)

    assert same_generation.style_hash == first.style_hash
    assert next_generation.style_hash != first.style_hash
    assert first.catalog_hash == "catalog-a"


def test_service_resolve_uses_one_catalog_snapshot(monkeypatch) -> None:
    from novelvideo.extension_styles.registry import CatalogSnapshot
    from novelvideo.services.style_service import StyleService

    catalog = (extension_style(),)
    snapshot = CatalogSnapshot(
        styles=catalog,
        generation=11,
        catalog_hash="catalog-11",
        fingerprint=None,
        diagnostics=None,  # type: ignore[arg-type]
    )

    class OneShotRegistry:
        calls = 0

        def snapshot(self):
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("catalog snapshot read more than once")
            return snapshot

    registry = OneShotRegistry()
    monkeypatch.setattr(StyleService, "_extension_registry", registry, raising=False)

    resolved = StyleService.resolve_style_snapshot(catalog[0].id)

    assert resolved.style_id == catalog[0].id
    assert resolved.catalog_hash == "catalog-11"
    assert registry.calls == 1


def test_existing_style_snapshot_stays_frozen_after_catalog_reload(
    monkeypatch, tmp_path: Path
) -> None:
    from novelvideo.extension_styles.registry import ExtensionStyleRegistry
    from novelvideo.services.style_service import StyleService

    source = (
        Path(__file__).parents[1]
        / "src"
        / "novelvideo"
        / "extension_styles"
        / "catalog.json"
    )
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_bytes(source.read_bytes())
    registry = ExtensionStyleRegistry(catalog_path)
    monkeypatch.setattr(StyleService, "_extension_registry", registry)
    style_id = "drama_ext.japanese_cel_animation"

    old_snapshot = StyleService.resolve_style_snapshot(style_id)
    old_payload = old_snapshot.model_dump(mode="json")
    raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    raw[0]["prompt_fragment"]["medium"][0] += " refreshed"
    catalog_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    registry.reload(force=True)
    new_snapshot = StyleService.resolve_style_snapshot(style_id)

    assert old_snapshot.model_dump(mode="json") == old_payload
    assert new_snapshot.style_hash != old_snapshot.style_hash
    assert new_snapshot.projections != old_snapshot.projections
