from __future__ import annotations

import json
import multiprocessing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import novelvideo.director_plan.store as store_module
from novelvideo.director_plan.models import DirectorPlanRevision, ValidationReport
from novelvideo.director_plan.store import DirectorPlanStore


def _save_revision_in_process(
    project_dir: str,
    source_script_hash: str,
    ready: Any,
    start: Any,
    results: Any,
) -> None:
    ready.put(True)
    start.wait(timeout=10)
    revision = make_revision("race").model_copy(
        update={"source_script_hash": source_script_hash}
    )
    try:
        DirectorPlanStore(project_dir).save(revision)
    except ValueError:
        results.put(("value_error", source_script_hash))
    else:
        results.put(("saved", source_script_hash))


def make_revision(
    revision_id: str,
    *,
    episode: int = 1,
    status: str = "review_required",
    passed: bool = True,
    created_at: datetime | None = None,
) -> DirectorPlanRevision:
    return DirectorPlanRevision(
        revision_id=revision_id,
        episode=episode,
        status=status,
        source_script_hash="sha256:abc",
        director_model="director-v1",
        prompt_version="v2",
        project_style_snapshot_id="style-1",
        groups=(),
        validation_report=ValidationReport(passed=passed),
        created_at=created_at or datetime(2026, 8, 29, 12, tzinfo=timezone.utc),
    )


def test_save_load_and_list_multiple_revisions_in_stable_order(tmp_path: Path) -> None:
    store = DirectorPlanStore(tmp_path)
    later = make_revision(
        "rev-b", created_at=datetime(2026, 8, 29, 13, tzinfo=timezone.utc)
    )
    tie_b = make_revision("rev-c")
    tie_a = make_revision("rev-a")

    for revision in (later, tie_b, tie_a):
        store.save(revision)

    assert store.load(1, "rev-b") == later
    assert [item.revision_id for item in store.list(1)] == ["rev-a", "rev-c", "rev-b"]
    assert (
        tmp_path / "director_plans" / "episode_001" / "revisions" / "rev-a.json"
    ).is_file()


def test_save_is_idempotent_but_rejects_different_content(tmp_path: Path) -> None:
    store = DirectorPlanStore(tmp_path)
    revision = make_revision("rev-a")
    store.save(revision)

    store.save(revision)

    with pytest.raises(ValueError, match="already exists"):
        store.save(revision.model_copy(update={"source_script_hash": "sha256:different"}))


@pytest.mark.parametrize(
    "revision_id",
    ["", ".", "..", "../escape", r"..\escape", r"C:\escape"],
)
def test_save_rejects_unsafe_revision_ids(tmp_path: Path, revision_id: str) -> None:
    outside_marker = tmp_path.parent / "escape.json"

    with pytest.raises(ValueError, match="revision_id"):
        DirectorPlanStore(tmp_path).save(make_revision(revision_id))

    assert not outside_marker.exists()


def test_save_rejects_absolute_revision_id_without_writing(tmp_path: Path) -> None:
    outside_marker = (tmp_path.parent / "absolute-escape").resolve()

    with pytest.raises(ValueError, match="revision_id"):
        DirectorPlanStore(tmp_path).save(make_revision(str(outside_marker)))

    assert not outside_marker.with_suffix(".json").exists()


def test_concurrent_processes_cannot_overwrite_same_revision(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_save_revision_in_process,
            args=(str(tmp_path), source_hash, ready, start, results),
        )
        for source_hash in ("sha256:one", "sha256:two")
    ]
    for process in processes:
        process.start()
    for _ in processes:
        ready.get(timeout=10)
    start.set()
    for process in processes:
        process.join(timeout=15)

    assert [process.exitcode for process in processes] == [0, 0]
    outcomes = sorted(results.get(timeout=5)[0] for _ in processes)
    assert outcomes == ["saved", "value_error"]
    assert DirectorPlanStore(tmp_path).load(1, "race").source_script_hash in {
        "sha256:one",
        "sha256:two",
    }


def test_load_missing_revision_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        DirectorPlanStore(tmp_path).load(1, "missing")


def test_load_active_returns_none_without_pointer(tmp_path: Path) -> None:
    assert DirectorPlanStore(tmp_path).load_active(1) is None


def test_activate_supersedes_previous_revision_and_updates_pointer(tmp_path: Path) -> None:
    store = DirectorPlanStore(tmp_path)
    first = make_revision("rev-a")
    second = make_revision("rev-b", created_at=first.created_at + timedelta(seconds=1))
    store.save(first)
    store.save(second)

    activated_first = store.activate(1, "rev-a")
    activated_second = store.activate(1, "rev-b")

    assert activated_first.status == "active"
    assert activated_first.activated_at is not None
    assert activated_second.status == "active"
    assert activated_second.activated_at is not None
    assert store.load(1, "rev-a").status == "superseded"
    assert store.load_active(1) == activated_second


def test_activate_can_restore_superseded_revision(tmp_path: Path) -> None:
    store = DirectorPlanStore(tmp_path)
    store.save(make_revision("rev-a"))
    store.save(make_revision("rev-b"))
    store.activate(1, "rev-a")
    store.activate(1, "rev-b")

    restored = store.activate(1, "rev-a")

    assert restored.status == "active"
    assert store.load(1, "rev-b").status == "superseded"
    assert store.load_active(1) == restored


@pytest.mark.parametrize("fail_on_write", [1, 2, 3, 4])
def test_activation_recovers_after_crash_at_each_write_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_on_write: int
) -> None:
    store = DirectorPlanStore(tmp_path)
    store.save(make_revision("rev-old"))
    store.save(make_revision("rev-target"))
    store.activate(1, "rev-old")
    original_atomic_write = store_module._atomic_write_json
    write_count = 0

    def fail_at_selected_write(path: Path, payload: dict[str, Any]) -> None:
        nonlocal write_count
        write_count += 1
        if write_count == fail_on_write:
            raise OSError("injected activation crash")
        original_atomic_write(path, payload)

    with monkeypatch.context() as patch:
        patch.setattr(store_module, "_atomic_write_json", fail_at_selected_write)
        with pytest.raises(OSError, match="injected activation crash"):
            store.activate(1, "rev-target")

    episode_dir = tmp_path / "director_plans" / "episode_001"
    journal_path = episode_dir / "activation-journal.json"
    if fail_on_write == 1:
        assert not journal_path.exists()
        expected_active_id = "rev-old"
    else:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        assert journal["old_id"] == "rev-old"
        assert journal["target_id"] == "rev-target"
        assert journal["activated_at"]
        expected_active_id = "rev-target"

    recovered_store = DirectorPlanStore(tmp_path)
    active = recovered_store.load_active(1)

    assert active is not None
    assert active.revision_id == expected_active_id
    assert active.status == "active"
    if fail_on_write > 1:
        assert recovered_store.load(1, "rev-old").status == "superseded"
        assert recovered_store.load(1, "rev-target").activated_at is not None
    assert not journal_path.exists()


@pytest.mark.parametrize(
    ("status", "passed"),
    [("draft", True), ("review_required", False), ("active", True)],
)
def test_activate_rejects_invalid_status_or_failed_validation(
    tmp_path: Path, status: str, passed: bool
) -> None:
    store = DirectorPlanStore(tmp_path)
    store.save(make_revision("rev-a", status=status, passed=passed))

    with pytest.raises(ValueError):
        store.activate(1, "rev-a")

    assert store.load_active(1) is None


def test_activate_missing_revision_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        DirectorPlanStore(tmp_path).activate(1, "missing")


def test_atomic_writes_leave_no_temporary_files(tmp_path: Path) -> None:
    store = DirectorPlanStore(tmp_path)
    store.save(make_revision("rev-a"))
    store.activate(1, "rev-a")

    episode_dir = tmp_path / "director_plans" / "episode_001"
    assert not [path for path in episode_dir.rglob("*") if ".tmp-" in path.name]
