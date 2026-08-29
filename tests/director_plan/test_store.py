from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from novelvideo.director_plan.models import DirectorPlanRevision, ValidationReport
from novelvideo.director_plan.store import DirectorPlanStore


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
