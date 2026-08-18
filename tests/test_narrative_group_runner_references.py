from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.narrative_groups.references import (
    GroupImageReference,
    GroupReferencePreview,
    GroupStyleReference,
    UnknownGroupReferenceIds,
)
from novelvideo.task_backend.runners import narrative_group


def _preview(tmp_path: Path) -> GroupReferencePreview:
    character = tmp_path / "assets" / "identities" / "hero.png"
    scene = tmp_path / "assets" / "scenes" / "room.png"
    character.parent.mkdir(parents=True)
    scene.parent.mkdir(parents=True)
    character.write_bytes(b"character")
    scene.write_bytes(b"scene")
    return GroupReferencePreview(
        style=GroupStyleReference(
            id="style",
            name="anime",
            prompt="2D anime illustration\nAvoid photorealism and 3D rendering.",
        ),
        image_references=(
            GroupImageReference(
                id="character-id",
                kind="character",
                source_kind="identity",
                label="Hero · Hero_default",
                path=str(character),
                beat_numbers=(1, 2),
                first_appearance=1,
                character_name="Hero",
                identity_id="Hero_default",
            ),
            GroupImageReference(
                id="scene-id",
                kind="scene",
                source_kind="scene_master",
                label="room",
                path=str(scene),
                beat_numbers=(1,),
                first_appearance=1,
                scene_id="room",
            ),
        ),
        asset_root=str(tmp_path / "assets"),
    )


def _payload(tmp_path: Path, **overrides):
    payload = {
        "project_dir": str(tmp_path),
        "stage": "render",
        "layout": {"rows": 1, "columns": 2},
        "beats": [
            {"beat_number": 1, "visual_description": "Hero enters the room"},
            {"beat_number": 2, "visual_description": "Hero sits"},
        ],
    }
    payload.update(overrides)
    return payload


def test_generation_input_applies_anime_style_and_reference_mapping(tmp_path, monkeypatch):
    preview = _preview(tmp_path)
    monkeypatch.setattr(narrative_group, "resolve_group_reference_preview", lambda *args, **kwargs: preview)

    value = narrative_group._generation_input(_payload(tmp_path))

    assert value.references == tuple(ref.path for ref in preview.image_references)
    assert "2D anime illustration" in value.prompt
    assert "Avoid photorealism and 3D rendering" in value.prompt
    assert "finished cinematic frame" not in value.prompt
    assert "Reference 1" in value.prompt and "Hero_default" in value.prompt
    assert "Reference 2" in value.prompt and "room" in value.prompt


def test_selection_can_disable_style_or_all_images(tmp_path, monkeypatch):
    preview = _preview(tmp_path)
    monkeypatch.setattr(narrative_group, "resolve_group_reference_preview", lambda *args, **kwargs: preview)

    no_style = narrative_group._generation_input(
        _payload(tmp_path, reference_selection={"use_style": False})
    )
    no_images = narrative_group._generation_input(
        _payload(
            tmp_path,
            reference_selection={
                "selected_character_reference_ids": [],
                "selected_scene_reference_ids": [],
            },
        )
    )

    assert "2D anime illustration" not in no_style.prompt
    assert len(no_style.references) == 2
    assert no_images.references == ()


def test_sketch_keeps_black_and_white_and_style_avoidance(tmp_path, monkeypatch):
    preview = _preview(tmp_path)
    monkeypatch.setattr(narrative_group, "resolve_group_reference_preview", lambda *args, **kwargs: preview)

    value = narrative_group._generation_input(_payload(tmp_path, stage="sketch"))

    assert "black-and-white storyboard sketch" in value.prompt
    assert "2D anime illustration" in value.prompt
    assert "Avoid photorealism and 3D rendering" in value.prompt


def test_unknown_reference_id_fails_at_runner_boundary(tmp_path, monkeypatch):
    preview = _preview(tmp_path)
    monkeypatch.setattr(narrative_group, "resolve_group_reference_preview", lambda *args, **kwargs: preview)

    with pytest.raises(UnknownGroupReferenceIds):
        narrative_group._generation_input(
            _payload(
                tmp_path,
                reference_selection={"selected_character_reference_ids": ["unknown"]},
            )
        )


def test_asset_deleted_after_preview_is_filtered_with_warning(tmp_path, monkeypatch):
    preview = _preview(tmp_path)
    Path(preview.image_references[0].path).unlink()
    monkeypatch.setattr(narrative_group, "resolve_group_reference_preview", lambda *args, **kwargs: preview)

    value = narrative_group._generation_input(_payload(tmp_path))

    assert value.references == (preview.image_references[1].path,)
    assert any("不存在" in warning for warning in value.warnings)


@pytest.mark.asyncio
async def test_generate_grid_passes_selected_paths_and_reports_reference_metadata(
    tmp_path, monkeypatch
):
    preview = _preview(tmp_path)
    monkeypatch.setattr(narrative_group, "resolve_group_reference_preview", lambda *args, **kwargs: preview)
    submitted = []

    class Client:
        http = SimpleNamespace(aclose=lambda: _done())

        async def submit(self, request, *, api_key):
            submitted.append(request)
            return "provider-task"

        async def query(self, task_id, *, api_key):
            return SimpleNamespace(status="succeeded", results=[{"url": "https://result"}])

        async def download(self, url):
            return b"png"

    async def _close():
        return None

    async def _done():
        return None

    client = Client()
    runtime = SimpleNamespace(
        model="image-model", api_key="secret", create_client=lambda: client
    )
    monkeypatch.setattr(
        "novelvideo.media_capabilities.runtime.configuration.load_grsai_runtime_configuration",
        lambda *args: runtime,
    )
    payload = _payload(
        tmp_path,
        output_dir=str(tmp_path / "output"),
        episode=1,
        group_id="ng-01",
        revision=1,
    )

    result = await narrative_group._generate_grid(
        payload, SimpleNamespace(output_dir=tmp_path / "output")
    )

    assert submitted[0].references == [ref.path for ref in preview.image_references]
    assert result["reference_count"] == 2
    assert result["reference_warnings"] == []
    assert all(str(tmp_path) not in warning for warning in result["reference_warnings"])


@pytest.mark.asyncio
async def test_execute_resolves_current_references_and_preserves_metadata(tmp_path, monkeypatch):
    from novelvideo.narrative_groups.service import advance_revision, group_beats, save_groups

    save_groups(tmp_path, 1, group_beats([{"id": "1", "beat_number": 1}]))
    advance_revision(tmp_path, 1, "ng-01", "render")
    preview = _preview(tmp_path)
    resolves = []
    monkeypatch.setattr(
        narrative_group,
        "resolve_group_reference_preview",
        lambda *args, **kwargs: resolves.append((args, kwargs)) or preview,
    )

    async def generate(payload, ctx):
        generation_input = narrative_group._generation_input(payload)
        grid = tmp_path / "grid.png"
        grid.write_bytes(b"grid")
        return {
            "grid_asset": str(grid),
            "reference_count": len(generation_input.references),
            "reference_warnings": ["asset changed after preview"],
        }

    monkeypatch.setattr(narrative_group, "_generate_grid", generate)
    monkeypatch.setattr(
        narrative_group,
        "_split_existing_grid",
        lambda *args: {"cell_assets": [], "errors": []},
    )
    envelope = {
        "episode": 1,
        "payload": {
            "project_dir": str(tmp_path),
            "episode": 1,
            "group_id": "ng-01",
            "stage": "render",
            "revision": 1,
            "beats": [{"beat_number": 1}],
        },
    }

    result = await narrative_group._execute(
        envelope, SimpleNamespace(output_dir=tmp_path), split_only=False
    )

    assert len(resolves) == 1
    assert result["reference_count"] == 2
    assert result["reference_warnings"] == ["asset changed after preview"]
