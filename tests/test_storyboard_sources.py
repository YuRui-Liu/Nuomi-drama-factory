import hashlib
import json
from dataclasses import replace

import pytest
from PIL import Image


def _source(tmp_path):
    from novelvideo.narrative_groups.storyboard_sources import StoryboardSource

    Image.new("RGB", (16, 8), "blue").save(tmp_path / "grid.png")
    Image.new("RGB", (8, 8), "blue").save(tmp_path / "cell.png")
    return StoryboardSource(
        project_id="project", episode=1, group_id="group", batch_id="batch",
        asset_id="asset", generation_id="task-a", grid_path="grid.png",
        grid_sha256=hashlib.sha256((tmp_path / "grid.png").read_bytes()).hexdigest(),
        rows=1, columns=2, splitter_version="cleanup-v1",
        cells=[dict(shot_id="shot-1", cell_index=0, path="cell.png",
                    sha256=hashlib.sha256((tmp_path / "cell.png").read_bytes()).hexdigest(),
                    width=8, height=8, crop_box=(0, 0, 8, 8), scale_size=(8, 8))],
    )


def test_identity_includes_generation_not_only_revision():
    from novelvideo.narrative_groups.storyboard_sources import source_identity

    first = source_identity(grid_sha256="a" * 64, generation_id="task-a")
    assert first != source_identity(grid_sha256="a" * 64, generation_id="task-b")
    assert first == source_identity(grid_sha256="a" * 64, generation_id="task-a")


def test_source_validates_and_roundtrips_without_mutation(tmp_path):
    from novelvideo.narrative_groups.storyboard_sources import StoryboardSource

    source = _source(tmp_path)
    before = (tmp_path / "grid.png").read_bytes()
    source.validate_files(tmp_path, project_id="project")
    assert StoryboardSource.model_validate_json(source.model_dump_json()) == source
    assert (tmp_path / "grid.png").read_bytes() == before
    with pytest.raises(ValueError):
        source.cells[0].shot_id = "changed"


@pytest.mark.parametrize("change", [
    {"shot_id": "shot-1", "cell_index": 1},
    {"shot_id": "shot-2", "cell_index": 0},
    {"shot_id": "shot-2", "cell_index": 2},
])
def test_duplicate_or_out_of_bounds_mapping_rejected(tmp_path, change):
    source = _source(tmp_path)
    data = source.model_dump()
    cell = {**data["cells"][0], **change}
    data["cells"] = [*data["cells"], cell]
    with pytest.raises(ValueError):
        type(source).model_validate(data)


@pytest.mark.parametrize("path", ["../other.png", "/tmp/other.png", "a/../../other.png"])
def test_paths_must_be_project_relative(tmp_path, path):
    source = _source(tmp_path)
    data = source.model_dump()
    data["grid_path"] = path
    with pytest.raises(ValueError):
        type(source).model_validate(data)


def test_symlink_escape_rejected(tmp_path):
    from novelvideo.narrative_groups.storyboard_sources import StoryboardSource

    project = tmp_path / "project"
    project.mkdir()
    source = _source(project)
    (project / "link.png").symlink_to(tmp_path / "outside.png")
    data = source.model_dump()
    data["cells"][0]["path"] = "link.png"
    with pytest.raises(ValueError, match="outside allowed storage"):
        StoryboardSource.model_validate(data).validate_files(project, project_id="project")


@pytest.mark.parametrize("target", ["grid.png", "cell.png"])
@pytest.mark.parametrize("damage", ["missing", "digest"])
def test_missing_or_changed_image_rejected(tmp_path, target, damage):
    source = _source(tmp_path)
    if damage == "missing":
        (tmp_path / target).unlink()
    else:
        Image.new("RGB", (8, 8), "red").save(tmp_path / target)
    with pytest.raises(ValueError, match="missing|digest"):
        source.validate_files(tmp_path, project_id="project")


def test_project_and_dimensions_must_match(tmp_path):
    source = _source(tmp_path)
    with pytest.raises(ValueError, match="project"):
        source.validate_files(tmp_path, project_id="another")
    data = source.model_dump()
    data["cells"][0]["width"] = 9
    with pytest.raises(ValueError, match="dimensions"):
        type(source).model_validate(data).validate_files(tmp_path, project_id="project")


def _split(tmp_path, generation_id="task-a", **overrides):
    from novelvideo.narrative_groups.storyboard_sources import split_storyboard_source

    return split_storyboard_source(
        media_root=tmp_path, grid_path="grid.png", project_id="project", episode=1,
        group_id="group", batch_id="batch", asset_id="asset", generation_id=generation_id,
        rows=1, columns=2, shot_ids=("shot-1", "shot-2"),
        **{"target_aspect": "1:1", **overrides},
    )


def test_versioned_split_preserves_original_and_earlier_cells(tmp_path):
    Image.new("RGB", (160, 80), "blue").save(tmp_path / "grid.png")
    original = (tmp_path / "grid.png").read_bytes()
    first = _split(tmp_path)
    reports = json.loads((tmp_path / first.grid_path).with_name("cleanup.json").read_text())
    assert reports[0]["source_crop_box"] == [0, 0, 78, 80]
    assert first.cells[0].crop_box == (0, 0, 78, 80)
    assert (tmp_path / "grid.png").read_bytes() == original
    first_bytes = (tmp_path / first.cells[0].path).read_bytes()
    Image.new("RGB", (160, 80), "red").save(tmp_path / "grid.png")
    second = _split(tmp_path, generation_id="task-b")
    assert first.cells[0].path != second.cells[0].path
    assert (tmp_path / first.cells[0].path).read_bytes() == first_bytes
    first.validate_files(tmp_path, project_id="project")
    second.validate_files(tmp_path, project_id="project")
    assert not (tmp_path / "frames/ep001/beat_01.png").exists()


def test_split_retry_reuses_files_but_rejects_parameter_change(tmp_path):
    Image.new("RGB", (160, 80), "blue").save(tmp_path / "grid.png")
    first = _split(tmp_path)
    modified = (tmp_path / first.cells[0].path).stat().st_mtime_ns
    assert _split(tmp_path) == first
    assert (tmp_path / first.cells[0].path).stat().st_mtime_ns == modified
    with pytest.raises(ValueError, match="parameters"):
        _split(tmp_path, target_aspect="9:16")


def test_split_retry_does_not_silently_repair_corruption(tmp_path):
    Image.new("RGB", (160, 80), "blue").save(tmp_path / "grid.png")
    first = _split(tmp_path)
    (tmp_path / first.cells[0].path).write_bytes(b"damaged")
    with pytest.raises(ValueError, match="digest"):
        _split(tmp_path)


def test_registration_preserves_selection_and_select_uses_cas(tmp_path):
    from novelvideo.narrative_groups.service import (
        group_beats, save_groups, load_groups, register_storyboard_source,
        select_storyboard_source,
    )

    group = group_beats([{"id": "shot-1"}, {"id": "shot-2"}])[0]
    save_groups(tmp_path, 1, [group])
    Image.new("RGB", (160, 80), "blue").save(tmp_path / "grid.png")
    first = _split(tmp_path).model_copy(update={"group_id": group.id})
    second = _split(tmp_path, generation_id="task-b").model_copy(update={"group_id": group.id})
    register_storyboard_source(tmp_path, 1, group.id, source=first, project_id="project")
    register_storyboard_source(tmp_path, 1, group.id, source=second, project_id="project")
    render = load_groups(tmp_path, 1)[0].stages["render"]
    assert render.selected_storyboard_id == first.source_id
    assert len(render.storyboard_sources) == 2
    with pytest.raises(RuntimeError, match="selection changed"):
        select_storyboard_source(tmp_path, 1, group.id, source_id=second.source_id, expected_selected_id="stale")
    with pytest.raises(KeyError):
        select_storyboard_source(tmp_path, 1, group.id, source_id="missing", expected_selected_id=first.source_id)
    selected = select_storyboard_source(
        tmp_path, 1, group.id, source_id=second.source_id, expected_selected_id=first.source_id,
    )
    assert selected.stages["render"].selected_storyboard_id == second.source_id
    assert selected.stages["video"].needs_regeneration
    assert selected.stages["video"].stale_reason == "storyboard_source_changed"
    assert selected.stages["render"].cell_assets[0]["sha256"] == second.cells[0].sha256


def test_partial_batch_cannot_replace_whole_group_selection(tmp_path):
    from novelvideo.narrative_groups.service import (
        group_beats, save_groups, register_storyboard_source, sidecar_path,
    )

    group = group_beats([{"id": "shot-1"}, {"id": "shot-2"}, {"id": "shot-3"}])[0]
    save_groups(tmp_path, 1, [group])
    original = sidecar_path(tmp_path, 1).read_bytes()
    Image.new("RGB", (160, 80), "blue").save(tmp_path / "grid.png")
    partial = _split(tmp_path).model_copy(update={"group_id": group.id})
    with pytest.raises(ValueError, match="complete group"):
        register_storyboard_source(tmp_path, 1, group.id, source=partial, project_id="project")
    assert sidecar_path(tmp_path, 1).read_bytes() == original


def test_selected_batches_merge_without_losing_other_shots(tmp_path):
    from novelvideo.narrative_groups.service import (
        group_beats, save_groups, register_storyboard_source, select_storyboard_source,
    )

    group = group_beats([{"id": f"shot-{i}"} for i in range(1, 5)])[0]
    group = replace(group, generation_batches=(
        {"id": "batch-a", "shot_ids": ["shot-1", "shot-2"]},
        {"id": "batch-b", "shot_ids": ["shot-3", "shot-4"]},
    ))
    save_groups(tmp_path, 1, [group])
    Image.new("RGB", (160, 80), "blue").save(tmp_path / "grid.png")
    first = _split(tmp_path).model_copy(update={"group_id": group.id, "batch_id": "batch-a"})
    other = _split(tmp_path, generation_id="task-b").model_copy(update={
        "group_id": group.id, "batch_id": "batch-b",
        "cells": tuple(cell.model_copy(update={"shot_id": f"shot-{i+3}"})
                       for i, cell in enumerate(first.cells)),
    })
    newer = _split(tmp_path, generation_id="task-c").model_copy(update={"group_id": group.id, "batch_id": "batch-a"})
    register_storyboard_source(tmp_path, 1, group.id, source=first, project_id="project")
    merged = register_storyboard_source(tmp_path, 1, group.id, source=other, project_id="project")
    selected = merged.stages["render"].selected_storyboard_id
    candidate = register_storyboard_source(tmp_path, 1, group.id, source=newer, project_id="project")
    assert candidate.stages["render"].selected_storyboard_id == selected
    changed = select_storyboard_source(tmp_path, 1, group.id,
        source_id=newer.source_id, expected_selected_id=selected)
    render = changed.stages["render"]
    assert render.selected_storyboard_sources == {"batch-a": newer.source_id, "batch-b": other.source_id}
    assert [cell["shot_id"] for cell in render.cell_assets] == [f"shot-{i}" for i in range(1, 5)]
    assert render.cell_assets[2]["storyboard_source_id"] == other.source_id
    assert render.selected_storyboard_id != selected
