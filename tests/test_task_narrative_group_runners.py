import pytest

from novelvideo.narrative_groups.service import retry_split, run_group_grid
from novelvideo.task_backend.runners.render import run_group_render_grid
from novelvideo.task_backend.runners.sketch import run_group_sketch_grid
from novelvideo.task_backend.registry import get_project_task_runner


def group_payload(stage="render"):
    return {
        "group_id": "ng-01",
        "stage": stage,
        "revision": 1,
        "cell_to_beat": [
            {"cell": 0, "beat_id": "beat-1"},
            {"cell": 1, "beat_id": "beat-2"},
        ],
    }


@pytest.mark.asyncio
async def test_successful_grid_generation_splits_before_task_completion(tmp_path):
    calls = []

    async def generator(payload):
        calls.append("generate")
        path = tmp_path / "grid.png"
        path.write_bytes(b"grid")
        return {"grid_asset": str(path), "actual_provider": "grsai", "actual_model": "gpt-image-2"}

    async def splitter(grid_asset, payload):
        calls.append("split")
        return {"cell_assets": [{"cell": 0, "path": "beat-1.png"}], "errors": []}

    result = await run_group_grid(group_payload(), generator=generator, splitter=splitter)

    assert calls == ["generate", "split"]
    assert result["status"] == "completed"
    assert result["cell_to_beat"][0] == {"cell": 0, "beat_id": "beat-1"}
    assert result["actual_provider"] == "grsai"


@pytest.mark.asyncio
async def test_partial_split_failure_preserves_successful_cells(tmp_path):
    async def generator(payload):
        return {"grid_asset": str(tmp_path / "grid.png")}

    async def splitter(grid_asset, payload):
        return {
            "cell_assets": [{"cell": 0, "path": "beat-1.png"}],
            "errors": [{"cell": 1, "message": "crop failed"}],
        }

    result = await run_group_grid(group_payload(), generator=generator, splitter=splitter)

    assert result["status"] == "partial_failure"
    assert result["cell_assets"] == [{"cell": 0, "path": "beat-1.png"}]
    assert result["errors"][0]["cell"] == 1


@pytest.mark.asyncio
async def test_split_retry_does_not_generate_again():
    calls = []

    async def splitter(grid_asset, payload):
        calls.append((grid_asset, payload["group_id"]))
        return {"cell_assets": [], "errors": []}

    payload = {**group_payload(), "grid_asset": "existing-grid.png"}
    result = await retry_split(payload, splitter=splitter)

    assert calls == [("existing-grid.png", "ng-01")]
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_split_exception_preserves_paid_grid_for_retry():
    async def generator(payload):
        return {"grid_asset": "paid-grid.png", "actual_model": "gpt-image-2"}

    def splitter(grid, payload):
        raise IndexError("invalid cell mapping")

    result = await run_group_grid(group_payload(), generator=generator, splitter=splitter)
    assert result["status"] == "partial_failure"
    assert result["grid_asset"] == "paid-grid.png"
    assert result["actual_model"] == "gpt-image-2"
    assert result["errors"][0]["message"] == "invalid cell mapping"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("wrapper", "stage"),
    [(run_group_sketch_grid, "sketch"), (run_group_render_grid, "render")],
)
async def test_stage_runner_rejects_mismatched_stage(wrapper, stage):
    async def unused(*args):
        raise AssertionError("must not call dependency")

    other = "render" if stage == "sketch" else "sketch"
    with pytest.raises(ValueError, match=stage):
        await wrapper(group_payload(other), generator=unused, splitter=unused)


def test_builtin_narrative_group_runners_use_standard_registry_contract():
    from novelvideo.task_backend.runners import narrative_group  # noqa: F401

    assert get_project_task_runner("narrative_group_grid") is narrative_group.run_narrative_group_grid
    assert get_project_task_runner("narrative_group_split") is narrative_group.run_narrative_group_split


def test_generation_batch_payload_controls_layout_style_and_panel_tags():
    from novelvideo.task_backend.runners.narrative_group import (
        _grid_prompt,
        _normalize_generation_batch_payload,
    )

    payload = _normalize_generation_batch_payload(
        {
            "batch_id": "batch-1",
            "shot_ids": ["shot-1", "shot-2"],
            "layout": "diptych",
            "target_cell_aspect": "9:16",
            "style_snapshot_id": "snapshot-1",
            "style_hash": "style-hash-1",
            "image_projection": "FULL_IMAGE_STYLE",
            "panel_tag": "PANEL_STYLE",
            "stage": "render",
            "beats": [{"action": "one"}, {"action": "two"}],
        }
    )
    prompt = _grid_prompt(payload)

    assert payload["layout"] == {"rows": 1, "columns": 2, "capacity": 2}
    assert payload["cell_to_beat"] == [
        {"cell": 0, "beat_id": "shot-1"},
        {"cell": 1, "beat_id": "shot-2"},
    ]
    assert prompt.count("FULL_IMAGE_STYLE") == 1
    assert prompt.count("PANEL_STYLE") == 2


def test_frozen_reference_snapshot_preserves_prompt_mapping_with_strong_sketch(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from novelvideo.task_backend.runners import narrative_group

    assets = tmp_path / "assets"
    assets.mkdir()
    identity = assets / "identity.png"
    scene = assets / "scene.png"
    identity.write_bytes(b"identity")
    scene.write_bytes(b"scene")
    sketch = tmp_path / "sketch.png"
    sketch.write_bytes(b"sketch")
    group = SimpleNamespace(
        id="ng-01",
        stages={
            "sketch": SimpleNamespace(
                status="completed", revision=2, grid_asset=str(sketch)
            )
        },
    )
    monkeypatch.setattr(narrative_group, "load_materialized_groups", lambda *_: [group])
    monkeypatch.setattr(
        narrative_group,
        "validate_reference_image",
        lambda path, **_: SimpleNamespace(image_path=str(path)),
    )
    payload = {
        "project_dir": str(tmp_path),
        "episode": 1,
        "group_id": "ng-01",
        "stage": "render",
        "constraint_mode": "strong_sketch",
        "source_sketch_revision": 2,
        "source_sketch_asset": str(sketch),
        "beats": [
            {"id": "shot-1", "beat_number": 4, "action": "Alice enters"},
            {"id": "shot-2", "beat_number": 7, "action": "Wide hall"},
        ],
        "reference_resolution": {
            "id": "refsnap-1",
            "schema_version": "narrative-reference-decision/v1",
            "ignored_requirement_ids": [],
            "warnings": [],
            "images": [
                {
                    "requirement_id": "character_identity:Alice_youth",
                    "source": "matched",
                    "source_id": "Alice_youth",
                    "asset_kind": "character_identity",
                    "image_path": str(identity),
                    "resolution": "matched",
                    "entity_id": "Alice_youth",
                    "shot_ids": ["shot-1"],
                },
                {
                    "requirement_id": "scene_base:hall",
                    "source": "matched",
                    "source_id": "hall",
                    "asset_kind": "scene_base",
                    "image_path": str(scene),
                    "resolution": "matched",
                    "entity_id": "hall",
                    "shot_ids": ["shot-2"],
                },
            ],
        },
    }

    generation_input = narrative_group._generation_input(payload)

    assert generation_input.references == (str(sketch), str(identity), str(scene))
    assert "Reference 2: character Alice, identity Alice_youth; use for panels 4." in generation_input.prompt
    assert "Reference 3: scene hall; use for panels 7." in generation_input.prompt


def test_legacy_frozen_snapshot_without_mapping_warns_instead_of_crashing(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from novelvideo.task_backend.runners import narrative_group

    assets = tmp_path / "assets"
    assets.mkdir()
    image = assets / "legacy.png"
    image.write_bytes(b"legacy")
    monkeypatch.setattr(
        narrative_group,
        "validate_reference_image",
        lambda path, **_: SimpleNamespace(image_path=str(path)),
    )
    generation_input = narrative_group._generation_input(
        {
            "project_dir": str(tmp_path),
            "beats": [],
            "reference_resolution": {
                "id": "refsnap-legacy",
                "schema_version": "narrative-reference-decision/v1",
                "ignored_requirement_ids": [],
                "warnings": [],
                "images": [
                    {
                        "requirement_id": "character_identity:Alice_youth",
                        "source": "matched",
                        "source_id": "Alice_youth",
                        "asset_kind": "character_identity",
                        "image_path": str(image),
                        "resolution": "matched",
                    }
                ],
            },
        }
    )

    assert any(
        "missing semantic mapping" in warning
        for warning in generation_input.warnings
    )
    assert generation_input.reference_audit["mapping_missing"] == 1


def test_generation_paths_reject_escape_and_hash_untrusted_ids(tmp_path):
    from types import SimpleNamespace

    from novelvideo.task_backend.runners.narrative_group import (
        _project_dir,
        _safe_path_slug,
    )

    ctx = SimpleNamespace(output_dir=tmp_path)
    with pytest.raises(ValueError, match="project output root"):
        _project_dir({"project_dir": tmp_path / ".." / "outside"}, ctx)

    slug = _safe_path_slug("../../escape", prefix="batch")
    assert slug.startswith("batch-")
    assert "/" not in slug and "\\" not in slug and ".." not in slug


def test_split_runner_recovers_grid_from_sidecar_without_generator(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from novelvideo.narrative_groups.service import (
        advance_revision,
        group_beats,
        record_stage_result,
        save_groups,
    )
    from novelvideo.task_backend.runners import narrative_group

    save_groups(tmp_path, 1, group_beats([{"id": "1", "beat_number": 1}]))
    advance_revision(tmp_path, 1, "ng-01", "sketch")
    grid = tmp_path / "grid.png"
    grid.write_bytes(b"grid")
    record_stage_result(
        tmp_path,
        1,
        "ng-01",
        "sketch",
        expected_revision=1,
        status="partial_failure",
        grid_asset=str(grid),
    )
    calls = []
    monkeypatch.setattr(
        narrative_group,
        "_split_existing_grid",
        lambda grid_asset, payload, ctx: calls.append(grid_asset)
        or {"cell_assets": [{"cell": 0, "path": "cell.png"}], "errors": []},
    )
    monkeypatch.setattr(
        narrative_group,
        "_generate_grid",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not generate")),
    )
    ctx = SimpleNamespace(output_dir=tmp_path)
    envelope = {
        "task_type": "narrative_group_split",
        "episode": 1,
        "payload": {"episode": 1, "group_id": "ng-01", "stage": "sketch", "revision": 1},
    }

    result = narrative_group.run_narrative_group_split(envelope, ctx)

    assert calls == [str(grid)]
    assert result["status"] == "completed"
