import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from novelvideo.director_plan.models import (
    GenerationBatchPlan,
    ProductionPlan,
    VideoSegmentPlan,
    canonical_production_plan_hash,
)
from novelvideo.director_plan.production_state import (
    ProductionArtifactResult,
    VideoSegmentState,
)
from novelvideo.director_plan.production_store import (
    ProductionStateConflict,
    ProductionStore,
)
from scripts.reset_test_director_data import reset_test_director_data


def _plan(*, revision_id: str = "revision-1") -> ProductionPlan:
    payload = dict(
        revision_id=revision_id,
        episode=1,
        generation_batches=(
            GenerationBatchPlan(
                id="batch-1",
                group_id="group-1",
                shot_ids=("shot-1", "shot-2"),
                layout="diptych",
                rows=1,
                columns=2,
                capacity=2,
                style_snapshot_id="style-1",
                style_snapshot_hash="style-hash-1",
            ),
        ),
        video_segments=(
            VideoSegmentPlan(
                id="segment-1",
                group_id="group-1",
                shot_ids=("shot-1",),
                duration_seconds=3,
                continuity_reason="single_shot",
                audio_mode="project_default",
                style_snapshot_id="style-1",
                style_snapshot_hash="style-hash-1",
            ),
            VideoSegmentPlan(
                id="segment-2",
                group_id="group-1",
                shot_ids=("shot-2",),
                duration_seconds=3,
                continuity_reason="single_shot",
                audio_mode="project_default",
                style_snapshot_id="style-1",
                style_snapshot_hash="style-hash-1",
            ),
        ),
        style_snapshot_hash="style-hash-1",
    )
    return ProductionPlan(
        **payload,
        production_plan_hash=canonical_production_plan_hash(payload),
    )


PLAN_HASH = _plan().production_plan_hash
STALE_PLAN_HASH = "0" * 64


def _completed_segment(production_id: str, uri: str) -> VideoSegmentState:
    return VideoSegmentState(
        production_id=production_id,
        provider="minimax",
        request_id=f"request-{production_id}",
        job_id=f"job-{production_id}",
        stage="persisted",
        result=ProductionArtifactResult(uri=uri),
    )


def test_initialize_persists_revision_scoped_schema_and_plan_hash(
    tmp_path: Path,
) -> None:
    state = ProductionStore(tmp_path).initialize(
        _plan(),
        expected_revision_id="revision-1",
        expected_plan_hash=PLAN_HASH,
    )

    path = (
        tmp_path
        / "director_plans"
        / "ep001"
        / "revision-1"
        / "production.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert state.state_version == 0
    assert payload["schema_version"] == 1
    assert payload["revision_id"] == "revision-1"
    assert payload["production_plan_hash"] == PLAN_HASH


def test_atomic_save_failure_keeps_previous_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import novelvideo.director_plan.production_store as production_store

    store = ProductionStore(tmp_path)
    current = store.initialize(
        _plan(),
        expected_revision_id="revision-1",
        expected_plan_hash=PLAN_HASH,
    )
    path = store.path_for(1, "revision-1")
    original = path.read_bytes()
    updated = current.model_copy(
        update={
            "video_segments": (
                _completed_segment("segment-1", "segment-1.mp4"),
                current.video_segments[1],
            )
        }
    )

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(production_store.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        store.save(
            1,
            updated,
            expected_revision_id="revision-1",
            expected_plan_hash=PLAN_HASH,
            expected_state_version=0,
        )

    assert path.read_bytes() == original
    assert list(path.parent.glob("production.json.tmp-*")) == []


def test_updating_one_segment_does_not_overwrite_its_sibling(tmp_path: Path) -> None:
    store = ProductionStore(tmp_path)
    store.initialize(
        _plan(),
        expected_revision_id="revision-1",
        expected_plan_hash=PLAN_HASH,
    )
    first = store.update_video_segment(
        1,
        "revision-1",
        _completed_segment("segment-1", "segment-1.mp4"),
        expected_revision_id="revision-1",
        expected_plan_hash=PLAN_HASH,
        expected_state_version=0,
    )
    second = store.update_video_segment(
        1,
        "revision-1",
        _completed_segment("segment-2", "segment-2.mp4"),
        expected_revision_id="revision-1",
        expected_plan_hash=PLAN_HASH,
        expected_state_version=first.state_version,
    )

    assert [item.result.uri for item in second.video_segments if item.result] == [
        "segment-1.mp4",
        "segment-2.mp4",
    ]


@pytest.mark.parametrize(
    ("expected_revision_id", "expected_plan_hash"),
    [
        ("old-revision", PLAN_HASH),
        ("revision-1", STALE_PLAN_HASH),
    ],
)
def test_stale_revision_or_plan_cannot_mutate_active_revision(
    tmp_path: Path, expected_revision_id: str, expected_plan_hash: str
) -> None:
    store = ProductionStore(tmp_path)
    store.initialize(
        _plan(),
        expected_revision_id="revision-1",
        expected_plan_hash=PLAN_HASH,
    )

    with pytest.raises(ProductionStateConflict):
        store.update_video_segment(
            1,
            "revision-1",
            _completed_segment("segment-1", "segment-1.mp4"),
            expected_revision_id=expected_revision_id,
            expected_plan_hash=expected_plan_hash,
            expected_state_version=0,
        )


def test_two_readers_with_same_cas_version_conflict(tmp_path: Path) -> None:
    first_store = ProductionStore(tmp_path)
    second_store = ProductionStore(tmp_path)
    first_reader = first_store.initialize(
        _plan(),
        expected_revision_id="revision-1",
        expected_plan_hash=PLAN_HASH,
    )
    second_reader = second_store.load(1, "revision-1")

    first_store.update_video_segment(
        1,
        "revision-1",
        _completed_segment("segment-1", "first.mp4"),
        expected_revision_id="revision-1",
        expected_plan_hash=PLAN_HASH,
        expected_state_version=first_reader.state_version,
    )
    with pytest.raises(ProductionStateConflict, match="state version"):
        second_store.update_video_segment(
            1,
            "revision-1",
            _completed_segment("segment-2", "second.mp4"),
            expected_revision_id="revision-1",
            expected_plan_hash=PLAN_HASH,
            expected_state_version=second_reader.state_version,
        )


def test_reset_requires_exact_resolved_project_confirmation(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    director_data = project / "director_plans"
    director_data.mkdir()

    with pytest.raises(ValueError, match="exactly match"):
        reset_test_director_data(
            project, confirmed_project_dir=tmp_path / "different-project"
        )

    assert director_data.is_dir()


@pytest.mark.parametrize(
    ("expected_revision_id", "expected_plan_hash"),
    [("old-revision", PLAN_HASH), ("revision-1", STALE_PLAN_HASH)],
)
def test_initialize_rejects_stale_expectations_before_writing(
    tmp_path: Path, expected_revision_id: str, expected_plan_hash: str
) -> None:
    with pytest.raises(ProductionStateConflict):
        ProductionStore(tmp_path).initialize(
            _plan(),
            expected_revision_id=expected_revision_id,
            expected_plan_hash=expected_plan_hash,
        )

    assert not (tmp_path / "director_plans").exists()


def test_load_missing_state_has_no_filesystem_side_effects(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ProductionStore(tmp_path).load(1, "revision-1")

    assert not (tmp_path / "director_plans").exists()


def test_reset_deletes_only_director_and_legacy_sidecar_data(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "director_plans").mkdir(parents=True)
    (project / ".narrative_groups").mkdir()
    keep = project / "episode.md"
    keep.write_text("keep", encoding="utf-8")

    removed = reset_test_director_data(project, confirmed_project_dir=project)

    assert removed == (
        project.resolve() / "director_plans",
        project.resolve() / ".narrative_groups",
    )
    assert not (project / "director_plans").exists()
    assert not (project / ".narrative_groups").exists()
    assert keep.read_text(encoding="utf-8") == "keep"


def test_reset_rejects_director_data_symlink_outside_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    link = project / "director_plans"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    with pytest.raises(ValueError, match="reparse|symlink"):
        reset_test_director_data(project, confirmed_project_dir=project)

    assert outside.is_dir()


def test_reset_rejects_internal_and_nested_symlinks(tmp_path: Path) -> None:
    project = tmp_path / "project"
    director_data = project / "director_plans"
    internal = project / "internal"
    director_data.mkdir(parents=True)
    internal.mkdir()
    link = director_data / "linked"
    try:
        link.symlink_to(internal, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    with pytest.raises(ValueError, match="reparse|symlink"):
        reset_test_director_data(project, confirmed_project_dir=project)

    assert director_data.is_dir()
    assert internal.is_dir()


def test_store_save_revalidates_and_preserves_exact_unique_id_sets(
    tmp_path: Path,
) -> None:
    store = ProductionStore(tmp_path)
    current = store.initialize(
        _plan(),
        expected_revision_id="revision-1",
        expected_plan_hash=PLAN_HASH,
    )
    missing = current.model_copy(update={"video_segments": current.video_segments[:1]})
    duplicate = current.model_copy(
        update={"video_segments": (current.video_segments[0],) * 2}
    )

    with pytest.raises(ProductionStateConflict, match="id set"):
        store.save(
            1,
            missing,
            expected_revision_id="revision-1",
            expected_plan_hash=PLAN_HASH,
            expected_state_version=0,
        )
    with pytest.raises(ValidationError):
        store.save(
            1,
            duplicate,
            expected_revision_id="revision-1",
            expected_plan_hash=PLAN_HASH,
            expected_state_version=0,
        )


def test_store_rejects_project_or_internal_reparse_points(tmp_path: Path) -> None:
    real_project = tmp_path / "real-project"
    real_project.mkdir()
    project_link = tmp_path / "project-link"
    try:
        project_link.symlink_to(real_project, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    with pytest.raises(ValueError, match="reparse|symlink"):
        ProductionStore(project_link)

    director_target = tmp_path / "director-target"
    director_target.mkdir()
    try:
        (real_project / "director_plans").symlink_to(
            director_target, target_is_directory=True
        )
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")
    with pytest.raises(ValueError, match="reparse|symlink"):
        ProductionStore(real_project).path_for(1, "revision-1")
