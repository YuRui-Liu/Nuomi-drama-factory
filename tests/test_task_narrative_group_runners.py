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
    monkeypatch.setattr(
        narrative_group,
        "resolve_group_reference_preview",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not resolve")),
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
