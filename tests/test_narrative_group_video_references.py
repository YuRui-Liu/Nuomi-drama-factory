import asyncio
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from novelvideo.narrative_groups import models, service
from novelvideo.narrative_groups import video_references
from novelvideo.narrative_groups.video_references import (
    VideoReferenceSelection,
    resolve_group_video_reference_preview,
    resolve_saved_video_references,
    temporary_upload_path,
)
from novelvideo.utils.path_resolver import (
    canonical_identity_path,
    canonical_prop_reference_path,
    canonical_scene_master_path,
)


def _run(awaitable):
    return asyncio.run(awaitable)


def _png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4), "red").save(path)
    return path


def _reference_id(source_kind: str, asset_id: str) -> str:
    return hashlib.sha256(f"{source_kind}\0{asset_id}".encode()).hexdigest()


class _Store:
    def __init__(self, beats):
        self.beats = beats
        identity = SimpleNamespace(
            identity_id="Alice_Hero",
            identity_name="Hero",
            appearance_details="blue coat and silver boots",
            face_prompt="",
            body_type="",
        )
        self.character = SimpleNamespace(
            name="Alice",
            face_prompt="freckled face",
            description="a determined courier",
            identities=[identity],
        )
        self.scene = SimpleNamespace(
            name="Station",
            environment_prompt="rainy glass railway station",
            description="",
        )
        self.prop = SimpleNamespace(
            name="Key",
            visual_prompt="small brass key with a red ribbon",
            description="",
        )

    async def get_beats_as_dicts(self, episode_number):
        assert episode_number == 1
        return list(self.beats)

    def get_all_characters(self):
        return [self.character]

    async def list_scenes(self):
        return [self.scene]

    async def list_props(self):
        return [self.prop]


def _group(*beat_ids):
    return models.NarrativeGroup(
        id="ng-01",
        ordinal=1,
        beat_ids=tuple(beat_ids),
        layout=models.GridLayout(rows=1, columns=len(beat_ids), capacity=len(beat_ids)),
        cell_to_beat=tuple(
            models.CellMapping(cell=index, beat_id=beat_id)
            for index, beat_id in enumerate(beat_ids)
        ),
    )


def _beats():
    return [
        {
            "id": "beat-1",
            "detected_identities": ["Alice_Hero", "Alice_Hero"],
            "detected_props": ["Key", "Key"],
            "scene_id": "Station",
            "visual_description": "Alice unlocks the station gate.",
        },
        {
            "id": "beat-2",
            "detected_identities": ["Alice_Hero"],
            "detected_props": ["Key"],
            "scene_id": "Station",
            "visual_description": "Alice enters the station.",
        },
        {
            "id": "beat-outside",
            "detected_props": ["OutsideProp"],
            "scene_id": "OutsideScene",
        },
    ]


def _prepare_assets(tmp_path):
    _png(canonical_identity_path(tmp_path, "Alice", "Alice_Hero"))
    _png(canonical_scene_master_path(tmp_path, "Station"))
    _png(canonical_prop_reference_path(tmp_path, "Key"))


def _preview(store, tmp_path, group=None, max_images=10):
    return _run(
        resolve_group_video_reference_preview(
            store=store,
            project_dir=tmp_path,
            episode_number=1,
            group=group or _group("beat-1", "beat-2"),
            max_images=max_images,
        )
    )


def test_video_reference_settings_are_frozen_and_explicitly_serialized():
    settings = models.VideoReferenceSettings(
        revision=3,
        references=(
            models.VideoReferenceItem(
                reference_id="opaque",
                source_kind="temporary_upload",
                label="uploaded",
                subject_description="uploaded product photo",
                temporary_upload_id="upload-1",
            ),
        ),
    )
    group = replace(_group("beat-1"), video_reference_settings=settings)

    with pytest.raises(FrozenInstanceError):
        settings.revision = 4
    assert group.to_dict()["video_reference_settings"] == {
        "revision": 3,
        "references": [
            {
                "reference_id": "opaque",
                "source_kind": "temporary_upload",
                "label": "uploaded",
                "subject_description": "uploaded product photo",
                "asset_id": "",
                "temporary_upload_id": "upload-1",
            }
        ],
    }


def test_video_reference_settings_copy_mutable_reference_input():
    reference = models.VideoReferenceItem(
        reference_id="opaque",
        source_kind="prop_reference",
        label="Key",
        subject_description="brass key",
        asset_id="Key",
    )
    source = [reference]

    settings = models.VideoReferenceSettings(references=source)
    source.clear()

    assert settings.references == (reference,)


def test_old_sidecar_defaults_video_references_without_migrating_image_refs(tmp_path):
    path = service.sidecar_path(tmp_path, 1)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "episode": 1,
                "groups": [
                    {
                        "id": "ng-01",
                        "ordinal": 1,
                        "beat_ids": ["beat-1"],
                        "layout": {"rows": 1, "columns": 1, "capacity": 1},
                        "cell_to_beat": [{"cell": 0, "beat_id": "beat-1"}],
                        "image_references": [{"id": "legacy"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    restored = service.load_groups(tmp_path, 1)[0]

    assert restored.video_reference_settings == models.VideoReferenceSettings()
    assert restored.to_dict()["video_reference_settings"] == {
        "revision": 0,
        "references": [],
    }


def test_preview_discovers_deduplicated_assets_in_stable_source_order(tmp_path):
    _prepare_assets(tmp_path)
    store = _Store(_beats())

    preview = _preview(store, tmp_path)

    assert [item.source_kind for item in preview.candidates] == [
        "character_identity",
        "scene_master",
        "prop_reference",
    ]
    assert [item.asset_id for item in preview.candidates] == [
        "Alice_Hero",
        "Station",
        "Key",
    ]
    assert len({item.reference_id for item in preview.candidates}) == 3
    assert preview.candidates[0].reference_id == _reference_id(
        "character_identity", "Alice_Hero"
    )
    assert "blue coat" in preview.candidates[0].subject_description
    assert "rainy glass" in preview.candidates[1].subject_description
    assert "brass key" in preview.candidates[2].subject_description
    assert all(
        "Subject" not in item.subject_description
        and "Picture" not in item.subject_description
        for item in preview.candidates
    )


def test_preview_default_descriptions_strip_reference_numbering(tmp_path):
    _prepare_assets(tmp_path)
    store = _Store(_beats())
    store.prop.visual_prompt = "<Picture 7> small brass key"
    store.scene.environment_prompt = "<Subject 3> rainy station"

    preview = _preview(store, tmp_path)

    assert all(
        "<Subject 3>" not in item.subject_description
        and "<Picture 7>" not in item.subject_description
        for item in preview.candidates
    )


def test_preview_uses_portrait_fallback_and_warns_for_missing_files(tmp_path):
    _png(tmp_path / "assets" / "characters" / "Alice" / "portrait.png")
    store = _Store(_beats())

    preview = _preview(store, tmp_path)

    assert [item.source_kind for item in preview.candidates] == [
        "character_identity"
    ]
    assert preview.candidates[0].asset_id == "Alice_Hero"
    assert any("portrait" in warning.lower() for warning in preview.warnings)
    assert any("Station" in warning for warning in preview.warnings)
    assert any("Key" in warning for warning in preview.warnings)


def test_preview_omits_asset_ids_that_would_escape_project(tmp_path):
    beats = [
        {
            "id": "beat-1",
            "scene_id": "../../../escape",
            "detected_props": ["../../../escape"],
        }
    ]

    preview = _preview(_Store(beats), tmp_path, group=_group("beat-1"))

    assert preview.candidates == ()
    assert len(preview.warnings) == 2


def test_preview_scans_group_temporary_uploads_after_auto_candidates(tmp_path):
    _prepare_assets(tmp_path)
    upload_path = _png(
        tmp_path
        / "videos"
        / "ep001"
        / "narrative_groups"
        / "references"
        / "ng-01"
        / "upload-a.png"
    )

    preview = _preview(_Store(_beats()), tmp_path)

    candidate = preview.candidates[-1]
    assert candidate.source_kind == "temporary_upload"
    assert candidate.temporary_upload_id == "upload-a"
    assert candidate.reference_id == _reference_id("temporary_upload", "upload-a")
    assert not hasattr(candidate, "path")
    assert upload_path.is_file()


@pytest.mark.parametrize("max_images", [0, 11, -1])
def test_preview_rejects_invalid_max_images(tmp_path, max_images):
    with pytest.raises(ValueError, match="max_images"):
        _preview(_Store(_beats()), tmp_path, max_images=max_images)


def test_update_accepts_only_server_candidates_and_preserves_selection_order(tmp_path):
    _prepare_assets(tmp_path)
    store = _Store(_beats())
    group = _group("beat-1", "beat-2")
    service.save_groups(tmp_path, 1, [group])
    candidates = _preview(store, tmp_path).candidates

    updated = _run(
        service.update_video_reference_settings(
            store=store,
            project_dir=tmp_path,
            episode_number=1,
            group_id="ng-01",
            expected_revision=0,
            selections=(
                VideoReferenceSelection(candidates[2].reference_id, "  hero key  "),
                VideoReferenceSelection(candidates[0].reference_id, "Alice in blue"),
            ),
            max_images=10,
        )
    )

    assert updated.video_reference_settings.revision == 1
    assert [item.reference_id for item in updated.video_reference_settings.references] == [
        candidates[2].reference_id,
        candidates[0].reference_id,
    ]
    assert updated.video_reference_settings.references[0].subject_description == "hero key"
    assert service.load_groups(tmp_path, 1)[0].video_reference_settings == (
        updated.video_reference_settings
    )


@pytest.mark.parametrize(
    ("selections", "match"),
    [
        ((), "between 1"),
        ((VideoReferenceSelection("forged", "description"),), "unknown"),
        ((VideoReferenceSelection("forged", "  "),), "description"),
    ],
)
def test_update_rejects_empty_unknown_and_blank_selections(
    tmp_path, selections, match
):
    service.save_groups(tmp_path, 1, [_group("beat-1")])

    with pytest.raises(ValueError, match=match):
        _run(
            service.update_video_reference_settings(
                store=_Store(_beats()),
                project_dir=tmp_path,
                episode_number=1,
                group_id="ng-01",
                expected_revision=0,
                selections=selections,
                max_images=10,
            )
        )


def test_update_rejects_duplicate_over_limit_and_unordered_selections(tmp_path):
    _prepare_assets(tmp_path)
    store = _Store(_beats())
    service.save_groups(tmp_path, 1, [_group("beat-1", "beat-2")])
    candidate = _preview(store, tmp_path).candidates[0]
    selection = VideoReferenceSelection(candidate.reference_id, "Alice")

    with pytest.raises(ValueError, match="duplicate"):
        _run(
            service.update_video_reference_settings(
                store=store,
                project_dir=tmp_path,
                episode_number=1,
                group_id="ng-01",
                expected_revision=0,
                selections=(selection, selection),
                max_images=10,
            )
        )
    with pytest.raises(ValueError, match="between 1"):
        _run(
            service.update_video_reference_settings(
                store=store,
                project_dir=tmp_path,
                episode_number=1,
                group_id="ng-01",
                expected_revision=0,
                selections=(selection, selection),
                max_images=1,
            )
        )
    with pytest.raises(TypeError, match="ordered"):
        _run(
            service.update_video_reference_settings(
                store=store,
                project_dir=tmp_path,
                episode_number=1,
                group_id="ng-01",
                expected_revision=0,
                selections={selection},
                max_images=10,
            )
        )


def test_update_rejects_stale_revision_and_active_video_stage(tmp_path):
    _prepare_assets(tmp_path)
    store = _Store(_beats())
    group = _group("beat-1")
    service.save_groups(tmp_path, 1, [group])
    candidate = _preview(store, tmp_path, group=group).candidates[0]
    selection = (VideoReferenceSelection(candidate.reference_id, "Alice"),)

    with pytest.raises(RuntimeError, match="stale"):
        _run(
            service.update_video_reference_settings(
                store=store,
                project_dir=tmp_path,
                episode_number=1,
                group_id="ng-01",
                expected_revision=4,
                selections=selection,
                max_images=10,
            )
        )

    active_group = replace(
        group,
        stages={
            **group.stages,
            "video": replace(group.stages["video"], status="running"),
        },
    )
    service.save_groups(tmp_path, 1, [active_group])
    with pytest.raises(RuntimeError, match="running"):
        _run(
            service.update_video_reference_settings(
                store=store,
                project_dir=tmp_path,
                episode_number=1,
                group_id="ng-01",
                expected_revision=0,
                selections=selection,
                max_images=10,
            )
        )


def _saved_group(source_kind, asset_id, description="valid description"):
    kwargs = (
        {"temporary_upload_id": asset_id}
        if source_kind == "temporary_upload"
        else {"asset_id": asset_id}
    )
    item = models.VideoReferenceItem(
        reference_id=_reference_id(source_kind, asset_id),
        source_kind=source_kind,
        label=asset_id,
        subject_description=description,
        **kwargs,
    )
    return replace(
        _group("beat-1"),
        video_reference_settings=models.VideoReferenceSettings(
            revision=1, references=(item,)
        ),
    )


def _resolve_saved(store, tmp_path, group, max_images=10):
    return _run(
        resolve_saved_video_references(
            store=store,
            project_dir=tmp_path,
            episode_number=1,
            group=group,
            max_images=max_images,
        )
    )


def test_resolved_saved_references_validate_and_return_server_only_paths(tmp_path):
    path = _png(canonical_prop_reference_path(tmp_path, "Key"))
    group = _saved_group("prop_reference", "Key")

    resolved = _resolve_saved(_Store(_beats()), tmp_path, group)

    assert isinstance(resolved, tuple)
    assert resolved[0].path == path.resolve()
    assert resolved[0].subject_description == "valid description"


def test_resolved_rejects_missing_corrupt_duplicate_blank_and_count(tmp_path):
    store = _Store(_beats())
    missing = _saved_group("prop_reference", "Key")
    with pytest.raises(ValueError, match="missing"):
        _resolve_saved(store, tmp_path, missing)

    path = canonical_prop_reference_path(tmp_path, "Key")
    path.parent.mkdir(parents=True)
    path.write_text("not an image", encoding="utf-8")
    with pytest.raises(ValueError, match="decod"):
        _resolve_saved(store, tmp_path, missing)

    _png(path)
    item = missing.video_reference_settings.references[0]
    duplicate = replace(
        missing,
        video_reference_settings=models.VideoReferenceSettings(
            revision=1, references=(item, item)
        ),
    )
    with pytest.raises(ValueError, match="duplicate"):
        _resolve_saved(store, tmp_path, duplicate)
    with pytest.raises(ValueError, match="description"):
        _resolve_saved(store, tmp_path, _saved_group("prop_reference", "Key", " "))
    with pytest.raises(ValueError, match="between 1"):
        _resolve_saved(store, tmp_path, _group("beat-1"))
    with pytest.raises(ValueError, match="between 1"):
        _resolve_saved(store, tmp_path, duplicate, max_images=1)


def test_resolved_rejects_escaped_asset_paths(tmp_path):
    outside = _png(tmp_path.parent / "escape" / "master.png")
    group = _saved_group("scene_master", "../../../escape")

    with pytest.raises(ValueError, match="project|asset ID"):
        _resolve_saved(_Store(_beats()), tmp_path, group)

    assert outside.is_file()


def test_temporary_upload_resolution_is_safe_and_decodable(tmp_path):
    upload_path = _png(
        tmp_path
        / "videos"
        / "ep001"
        / "narrative_groups"
        / "references"
        / "ng-01"
        / "upload-a.png"
    )
    resolved = _resolve_saved(
        _Store(_beats()), tmp_path, _saved_group("temporary_upload", "upload-a")
    )

    assert resolved[0].path == upload_path.resolve()

    escaped = _saved_group("temporary_upload", "../upload-a")
    with pytest.raises(ValueError, match="upload"):
        _resolve_saved(_Store(_beats()), tmp_path, escaped)


@pytest.mark.parametrize(
    "group_id",
    ["../sibling", "nested/group", r"nested\group"],
)
def test_temporary_upload_path_rejects_unsafe_group_id(tmp_path, group_id):
    with pytest.raises(ValueError, match="group"):
        temporary_upload_path(tmp_path, 1, group_id, "upload-a")


def test_temporary_upload_path_rejects_absolute_group_id_inside_project(tmp_path):
    with pytest.raises(ValueError, match="group"):
        temporary_upload_path(tmp_path, 1, str(tmp_path / "stolen"), "upload-a")


def test_preview_rejects_malicious_group_id_before_scanning_sibling(tmp_path):
    stolen = _png(
        tmp_path
        / "videos"
        / "ep001"
        / "narrative_groups"
        / "sibling"
        / "stolen.png"
    )
    group = replace(_group("beat-1"), id="../sibling")

    with pytest.raises(ValueError, match="group"):
        _preview(_Store([]), tmp_path, group=group)
    assert stolen.is_file()


def test_temporary_upload_path_rejects_symlink_outside_current_group(tmp_path):
    references_root = (
        tmp_path
        / "videos"
        / "ep001"
        / "narrative_groups"
        / "references"
    )
    sibling_file = _png(references_root / "sibling" / "upload-a.png")
    group_root = references_root / "ng-01"
    group_root.mkdir(parents=True)
    (group_root / "upload-a.png").symlink_to(sibling_file)

    with pytest.raises(ValueError, match="group|no-follow"):
        temporary_upload_path(tmp_path, 1, "ng-01", "upload-a")


def test_preview_rejects_asset_ids_that_escape_their_canonical_roots(tmp_path):
    identity_id = "../Alice_Hero"
    scene_id = "../../escaped-scene"
    prop_id = "../../escaped-prop"
    _png(tmp_path / "assets" / "Alice" / "identities" / "Hero.png")
    _png(tmp_path / "escaped-scene" / "master.png")
    _png(tmp_path / "escaped-prop" / "reference_3view.png")
    beats = [
        {
            "id": "beat-1",
            "detected_identities": [identity_id],
            "scene_id": scene_id,
            "detected_props": [prop_id],
        }
    ]

    preview = _preview(_Store(beats), tmp_path, group=_group("beat-1"))

    assert preview.candidates == ()
    assert len(preview.warnings) == 3


@pytest.mark.parametrize(
    ("source_kind", "asset_id", "escaped_path"),
    [
        (
            "character_identity",
            "../Alice_Hero",
            Path("assets/Alice/identities/Hero.png"),
        ),
        ("scene_master", "../../escaped-scene", Path("escaped-scene/master.png")),
        (
            "prop_reference",
            "../../escaped-prop",
            Path("escaped-prop/reference_3view.png"),
        ),
    ],
)
def test_resolved_rejects_asset_ids_outside_canonical_root(
    tmp_path, source_kind, asset_id, escaped_path
):
    _png(tmp_path / escaped_path)

    with pytest.raises(ValueError, match="asset ID"):
        _resolve_saved(_Store(_beats()), tmp_path, _saved_group(source_kind, asset_id))


def test_resolved_returns_immutable_verified_bytes_and_sha256(tmp_path):
    path = _png(canonical_prop_reference_path(tmp_path, "Key"))
    original = path.read_bytes()

    resolved = _resolve_saved(
        _Store(_beats()), tmp_path, _saved_group("prop_reference", "Key")
    )[0]
    path.write_bytes(b"changed after resolution")

    assert resolved.content == original
    assert resolved.sha256 == hashlib.sha256(original).hexdigest()
    assert isinstance(resolved.content, bytes)


def test_resolved_rejects_final_file_symlink_even_within_asset_root(tmp_path):
    target = _png(canonical_prop_reference_path(tmp_path, "Other"))
    path = canonical_prop_reference_path(tmp_path, "Key")
    path.parent.mkdir(parents=True)
    path.symlink_to(target)

    with pytest.raises(ValueError, match="symlink|no-follow"):
        _resolve_saved(
            _Store(_beats()), tmp_path, _saved_group("prop_reference", "Key")
        )


def test_resolved_no_follow_read_rejects_regular_file_swapped_to_symlink(
    tmp_path, monkeypatch
):
    path = _png(canonical_prop_reference_path(tmp_path, "Key"))
    target = _png(canonical_prop_reference_path(tmp_path, "Other"))
    real_snapshot = video_references._snapshot_reference_image

    def swap_then_snapshot(project_dir, lexical_path, label):
        path.unlink()
        path.symlink_to(target)
        return real_snapshot(project_dir, lexical_path, label)

    monkeypatch.setattr(
        video_references, "_snapshot_reference_image", swap_then_snapshot
    )

    with pytest.raises(ValueError, match="no-follow"):
        _resolve_saved(
            _Store(_beats()), tmp_path, _saved_group("prop_reference", "Key")
        )


def test_resolved_rejects_png_with_valid_header_but_truncated_pixels(tmp_path):
    path = _png(canonical_prop_reference_path(tmp_path, "Key"))
    encoded = path.read_bytes()
    path.write_bytes(encoded[:45])

    with pytest.raises(ValueError, match="decod"):
        _resolve_saved(
            _Store(_beats()), tmp_path, _saved_group("prop_reference", "Key")
        )


def test_resolved_enforces_byte_and_pixel_limits(tmp_path, monkeypatch):
    path = _png(canonical_prop_reference_path(tmp_path, "Key"))
    group = _saved_group("prop_reference", "Key")
    monkeypatch.setattr(
        video_references, "MAX_VIDEO_REFERENCE_BYTES", len(path.read_bytes()) - 1,
        raising=False,
    )
    with pytest.raises(ValueError, match="byte limit"):
        _resolve_saved(_Store(_beats()), tmp_path, group)

    monkeypatch.setattr(
        video_references, "MAX_VIDEO_REFERENCE_BYTES", 1024 * 1024,
        raising=False,
    )
    monkeypatch.setattr(
        video_references, "MAX_VIDEO_REFERENCE_PIXELS", 15,
        raising=False,
    )
    with pytest.raises(ValueError, match="pixel limit"):
        _resolve_saved(_Store(_beats()), tmp_path, group)


def test_update_uses_to_thread_for_both_sidecar_sections(tmp_path, monkeypatch):
    _prepare_assets(tmp_path)
    store = _Store(_beats())
    group = _group("beat-1")
    service.save_groups(tmp_path, 1, [group])
    candidate = _preview(store, tmp_path, group=group).candidates[0]
    calls = []
    real_to_thread = asyncio.to_thread

    async def tracked_to_thread(function, /, *args, **kwargs):
        calls.append(function.__name__)
        return await real_to_thread(function, *args, **kwargs)

    monkeypatch.setattr(service, "asyncio", asyncio, raising=False)
    monkeypatch.setattr(service.asyncio, "to_thread", tracked_to_thread)

    _run(
        service.update_video_reference_settings(
            store=store,
            project_dir=tmp_path,
            episode_number=1,
            group_id="ng-01",
            expected_revision=0,
            selections=(VideoReferenceSelection(candidate.reference_id, "Alice"),),
            max_images=10,
        )
    )

    assert calls == [
        "_load_video_reference_update_group",
        "_commit_video_reference_settings",
    ]


def test_update_detects_group_structure_change_while_resolving_candidates(tmp_path):
    _prepare_assets(tmp_path)
    base = _group("beat-1")
    service.save_groups(tmp_path, 1, [base])

    class _BarrierStore(_Store):
        def __init__(self, beats):
            super().__init__(beats)
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def get_beats_as_dicts(self, episode_number):
            self.entered.set()
            await self.release.wait()
            return await super().get_beats_as_dicts(episode_number)

    store = _BarrierStore(_beats())
    candidate = _preview(_Store(_beats()), tmp_path, group=base).candidates[0]

    async def scenario():
        updating = asyncio.create_task(
            service.update_video_reference_settings(
                store=store,
                project_dir=tmp_path,
                episode_number=1,
                group_id="ng-01",
                expected_revision=0,
                selections=(VideoReferenceSelection(candidate.reference_id, "Alice"),),
                max_images=10,
            )
        )
        await store.entered.wait()
        changed = replace(
            base,
            beat_ids=("beat-2",),
            cell_to_beat=(models.CellMapping(cell=0, beat_id="beat-2"),),
        )
        await asyncio.to_thread(service.save_groups, tmp_path, 1, [changed])
        store.release.set()
        with pytest.raises(RuntimeError, match="structure|changed"):
            await updating

    _run(scenario())


def test_video_reference_models_reject_invalid_runtime_values():
    with pytest.raises(ValueError, match="source_kind"):
        models.VideoReferenceItem(
            reference_id="id",
            source_kind="local_path",
            label="label",
            subject_description="description",
        )
    with pytest.raises(TypeError, match="subject_description"):
        models.VideoReferenceItem(
            reference_id="id",
            source_kind="prop_reference",
            label="label",
            subject_description=123,
        )
    with pytest.raises(ValueError, match="revision"):
        models.VideoReferenceSettings(revision=-1)
    with pytest.raises(TypeError, match="VideoReferenceItem"):
        models.VideoReferenceSettings(references=({"reference_id": "id"},))
