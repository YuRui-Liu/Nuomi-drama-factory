from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from novelvideo.task_backend.runners import narrative_group


def _png(path: Path, color: str = "blue") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2, 2), color).save(path, "PNG")
    return str(path.resolve())


def test_split_partial_grid_does_not_promote_padding_cell(tmp_path):
    grid = tmp_path / "grid.png"
    Image.new("RGB", (360, 640), "blue").save(grid)
    result = narrative_group._split_existing_grid(
        str(grid),
        {"episode": 1, "stage": "render", "revision": 2,
         "group_id": "group-1", "layout": {"rows": 2, "columns": 2},
         "aspect_ratio": "9:16",
         "cell_to_beat": [{"cell": i, "beat_id": f"shot-{i + 1}"} for i in range(3)]},
        SimpleNamespace(output_dir=tmp_path),
    )
    assert [cell["shot_id"] for cell in result["cell_assets"]] == ["shot-1", "shot-2", "shot-3"]
    assert all(Path(cell["path"]).is_file() for cell in result["cell_assets"])


def test_versioned_render_split_avoids_legacy_frame_overwrite(tmp_path):
    grid = tmp_path / "grid.png"
    Image.new("RGB", (160, 80), "blue").save(grid)
    original = grid.read_bytes()
    payload = {
        "episode": 1, "stage": "render", "revision": 1,
        "group_id": "group-1", "batch_id": "batch-1", "project_id": "project",
        "generation_id": "generation-a", "storyboard_contract_version": 1,
        "layout": {"rows": 1, "columns": 2}, "aspect_ratio": "9:16",
        "cell_to_beat": [{"cell": i, "beat_id": f"shot-{i + 1}"} for i in range(2)],
    }
    first = narrative_group._split_existing_grid(str(grid), payload, SimpleNamespace(output_dir=tmp_path))
    first_path = Path(first["cell_assets"][0]["path"])
    first_bytes = first_path.read_bytes()
    assert grid.read_bytes() == original
    assert "storyboard_source" in first
    Image.new("RGB", (160, 80), "red").save(grid)
    second = narrative_group._split_existing_grid(
        str(grid), {**payload, "generation_id": "generation-b"}, SimpleNamespace(output_dir=tmp_path)
    )
    assert second["cell_assets"][0]["path"] != str(first_path)
    assert first_path.read_bytes() == first_bytes
    assert not (tmp_path / "frames/ep001/beat_01.png").exists()


@pytest.mark.asyncio
async def test_generation_preserves_split_quality_flags(tmp_path, monkeypatch):
    payload = {"project_dir": str(tmp_path), "group_id": "group-1",
               "stage": "render", "revision": 1,
               "layout": {"rows": 1, "columns": 1},
               "beat_ids": ["shot-1"], "cell_to_beat": [{"cell": 0, "beat_id": "shot-1"}]}
    monkeypatch.setattr(narrative_group, "stage_payload", lambda *args: payload)
    recorded = []
    monkeypatch.setattr(narrative_group, "record_stage_result",
                        lambda *args, **kwargs: recorded.append(kwargs))

    async def generate(*args):
        return {"grid_asset": str(tmp_path / "grid.png"), "degraded": False}

    monkeypatch.setattr(narrative_group, "_generate_grid", generate)
    monkeypatch.setattr(narrative_group, "_split_existing_grid", lambda *args: {
        "cell_assets": [{"cell": 0, "path": "frame.png"}], "errors": [],
        "upscaled": True, "degraded": True, "cleaned_cell_size": "720x1280",
    })
    result = await narrative_group._execute(
        {"episode": 1, "payload": payload}, SimpleNamespace(output_dir=tmp_path),
        split_only=False,
    )
    assert result.get("upscaled") is True
    assert result.get("degraded") is True
    assert recorded[-1]["provider_parameters"]["upscaled"] is True
    assert recorded[-1]["provider_parameters"]["degraded"] is True


@pytest.mark.asyncio
async def test_versioned_execute_persists_source_and_preserves_selection(tmp_path, monkeypatch):
    from dataclasses import replace
    from novelvideo.narrative_groups.service import group_beats, save_groups, load_groups

    group = group_beats([{"id": "shot-1"}, {"id": "shot-2"}])[0]
    save_groups(tmp_path, 1, [replace(group, stages={**group.stages,
        "render": replace(group.stages["render"], revision=1)})])
    payload = {"project_dir": str(tmp_path), "project_id": "project", "episode": 1,
        "group_id": group.id, "stage": "render", "revision": 1,
        "storyboard_contract_version": 1, "aspect_ratio": "9:16"}
    calls = []

    async def generate(data, ctx):
        calls.append(data)
        path = tmp_path / f"grid-{len(calls)}.png"
        Image.new("RGB", (160, 80), "blue" if len(calls) == 1 else "red").save(path)
        return {"grid_asset": str(path)}

    monkeypatch.setattr(narrative_group, "_generate_grid", generate)
    first = await narrative_group._execute({"task_id": "task-a", "episode": 1, "payload": payload},
        SimpleNamespace(output_dir=tmp_path), split_only=False)
    render = load_groups(tmp_path, 1)[0].stages["render"]
    assert first["storyboard_source"]["generation_id"] == "task-a"
    assert len(render.storyboard_sources) == 1
    before = render.cell_assets
    await narrative_group._execute({"task_id": "task-b", "episode": 1, "payload": payload},
        SimpleNamespace(output_dir=tmp_path), split_only=False)
    current = load_groups(tmp_path, 1)[0].stages["render"]
    assert len(current.storyboard_sources) == 2
    assert current.selected_storyboard_id == render.selected_storyboard_id
    assert current.cell_assets == before
    split_input = current.provider_parameters["storyboard_split_input"]
    retried = await narrative_group._execute(
        {"task_id": "retry-task", "episode": 1,
         "payload": {**payload, "storyboard_split_input": split_input}},
        SimpleNamespace(output_dir=tmp_path), split_only=True)
    assert retried["storyboard_source"]["generation_id"] == "task-b"
    assert len(calls) == 2
    assert len(load_groups(tmp_path, 1)[0].stages["render"].storyboard_sources) == 2
    Image.new("RGB", (160, 80), "green").save(tmp_path / split_input["grid_path"])
    with pytest.raises(ValueError, match="digest"):
        await narrative_group._execute(
            {"task_id": "retry-again", "episode": 1,
             "payload": {**payload, "storyboard_split_input": split_input}},
            SimpleNamespace(output_dir=tmp_path), split_only=True)
    assert len(calls) == 2
    failed = load_groups(tmp_path, 1)[0].stages["render"]
    assert failed.status == "failed"
    assert "digest" in failed.error
    assert failed.selected_storyboard_id == render.selected_storyboard_id
    assert failed.cell_assets == before


def _payload(project_dir: Path, snapshot: dict | None = None) -> dict:
    payload = {
        "project_dir": str(project_dir),
        "stage": "sketch",
        "layout": {"rows": 1, "columns": 1},
        "beats": [{"beat_number": 1, "visual_description": "A letter on a desk"}],
    }
    if snapshot is not None:
        payload["reference_resolution"] = snapshot
    return payload


def _snapshot(*images: dict, ignored: tuple[str, ...] = ()) -> dict:
    return {
        "id": "refsnap_test",
        "schema_version": "narrative-reference-decision/v1",
        "images": list(images),
        "ignored_requirement_ids": list(ignored),
        "warnings": ["snapshot warning"],
        "style_reference": "",
    }


def _image(requirement_id: str, image_path: str, *, resolution: str) -> dict:
    return {
        "requirement_id": requirement_id,
        "source": "upload" if resolution == "temporary" else "project_asset",
        "source_id": f"source-{requirement_id}",
        "asset_kind": "prop",
        "image_path": image_path,
        "resolution": resolution,
    }


@pytest.mark.parametrize("damage", ["missing", "content", "escape"])
def test_reference_snapshot_is_revalidated_before_generation(tmp_path, damage):
    original = Path(_png(tmp_path / "assets" / "props" / "letter.png"))
    image_path = str(original)
    if damage == "missing":
        original.unlink()
    elif damage == "content":
        original.write_bytes(b"not an image")
    else:
        image_path = _png(tmp_path / "outside.png")

    with pytest.raises(narrative_group.ReferenceSnapshotInvalid) as exc_info:
        narrative_group._generation_input(
            _payload(tmp_path, _snapshot(_image("prop:letter", image_path, resolution="project_asset")))
        )

    assert exc_info.value.error_code == "REFERENCE_SNAPSHOT_INVALID"
    assert str(exc_info.value) == "REFERENCE_SNAPSHOT_INVALID"
    assert str(tmp_path) not in str(exc_info.value)


@pytest.mark.asyncio
async def test_invalid_snapshot_fails_before_transport_client_is_created(
    tmp_path, monkeypatch
):
    missing = str((tmp_path / "assets" / "missing.png").resolve())
    runtime = SimpleNamespace(
        model="image-model",
        api_key="secret",
        create_client=lambda: (_ for _ in ()).throw(
            AssertionError("transport client must not be created")
        ),
    )
    monkeypatch.setattr(
        "novelvideo.media_capabilities.runtime.configuration."
        "load_grsai_runtime_configuration",
        lambda *args, **kwargs: runtime,
    )
    payload = _payload(
        tmp_path,
        _snapshot(
            _image("prop:letter", missing, resolution="project_asset")
        ),
    )

    with pytest.raises(narrative_group.ReferenceSnapshotInvalid):
        await narrative_group._generate_grid(
            payload, SimpleNamespace(output_dir=tmp_path)
        )


def test_reference_snapshot_wins_over_legacy_selection(tmp_path):
    formal = _png(tmp_path / "assets" / "props" / "letter.png")
    payload = _payload(
        tmp_path,
        _snapshot(_image("prop:letter", formal, resolution="project_asset"), ignored=("prop:key",)),
    )
    payload["reference_selection"] = {
        "selected_character_reference_ids": ["stale-selection"]
    }

    value = narrative_group._generation_input(payload)

    assert value.references == (formal,)
    assert value.warnings == (
        "snapshot warning",
        "legacy reference snapshot missing semantic mapping; references remain usable without prompt labels",
    )
    assert value.reference_audit == {
        "snapshot_id": "refsnap_test",
        "formal": 1,
        "temporary": 0,
        "fallback": 0,
        "ignored": 1,
        "mapping_missing": 1,
    }


def test_reference_snapshot_audit_counts_each_resolution(tmp_path):
    images = (
        _image("prop:formal", _png(tmp_path / "assets" / "formal.png"), resolution="project_asset"),
        _image(
            "prop:temporary",
            _png(tmp_path / ".runtime" / "reference_uploads" / "temporary.png"),
            resolution="temporary",
        ),
        _image("prop:fallback", _png(tmp_path / "assets" / "fallback.png"), resolution="fallback"),
    )

    value = narrative_group._generation_input(
        _payload(tmp_path, _snapshot(*images, ignored=("prop:ignored",)))
    )

    assert value.reference_audit == {
        "snapshot_id": "refsnap_test",
        "formal": 1,
        "temporary": 1,
        "fallback": 1,
        "ignored": 1,
        "mapping_missing": 3,
    }


def test_generation_without_snapshot_rejects_legacy_runtime_resolution(tmp_path):
    with pytest.raises(narrative_group.ReferenceSnapshotInvalid):
        narrative_group._generation_input(_payload(tmp_path))
