from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from novelvideo.shot_continuity import (
    BoundaryState,
    CameraLock,
    ContinuityRevisionConflict,
    SceneLock,
    ShotContinuityContract,
    ShotContinuityStore,
)


def _contract(
    shot_id: str,
    *,
    revision: int = 0,
    state: str = "initial",
    predecessor_shot_id: str | None = None,
    predecessor_revision: int | None = None,
) -> ShotContinuityContract:
    return ShotContinuityContract(
        revision=revision,
        shot_id=shot_id,
        scene_id="scene-1",
        predecessor_shot_id=predecessor_shot_id,
        predecessor_revision=predecessor_revision,
        scene=SceneLock(scene_state=state),
        camera=CameraLock(shot_size="medium", angle="eye-level"),
        boundary=BoundaryState(carry_in="start", planned_carry_out="end"),
    )


def test_path_for_returns_episode_json_path(tmp_path: Path) -> None:
    store = ShotContinuityStore(tmp_path)

    assert store.path_for(1) == tmp_path / ".shot_continuity" / "ep001.json"


@pytest.mark.parametrize("episode", [0, -1])
def test_path_for_rejects_nonpositive_episode(tmp_path: Path, episode: int) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        ShotContinuityStore(tmp_path).path_for(episode)


def test_put_assigns_revisions_and_semantically_deduplicates(tmp_path: Path) -> None:
    store = ShotContinuityStore(tmp_path)
    candidate = _contract("shot-1", revision=87)

    first = store.put(1, candidate, expected_revision=0)
    duplicate = store.put(1, _contract("shot-1", revision=999), expected_revision=1)
    changed = store.put(
        1,
        _contract("shot-1", revision=0, state="changed"),
        expected_revision=1,
    )

    assert (first.revision, duplicate.revision, changed.revision) == (1, 1, 2)
    assert store.list_revisions(1, "shot-1") == (first, changed)
    assert candidate.revision == 87


def test_put_rejects_stale_expected_revision(tmp_path: Path) -> None:
    store = ShotContinuityStore(tmp_path)
    store.put(1, _contract("shot-1"), expected_revision=0)

    with pytest.raises(
        ContinuityRevisionConflict,
        match=r"expected 0, found 1",
    ):
        store.put(1, _contract("shot-1", state="changed"), expected_revision=0)


def test_load_active_and_list_revisions_preserve_history(tmp_path: Path) -> None:
    store = ShotContinuityStore(tmp_path)
    assert store.load_active(1, "missing") is None
    assert store.list_revisions(1, "missing") == ()

    saved = [store.put(1, _contract("shot-1"), expected_revision=0)]
    for revision in range(1, 4):
        saved.append(
            store.put(
                1,
                _contract("shot-1", state=f"state-{revision}"),
                expected_revision=revision,
            )
        )

    assert store.load_active(1, "shot-1") == saved[-1]
    assert store.list_revisions(1, "shot-1") == tuple(saved)


def test_stale_dependents_only_returns_direct_active_old_bindings(tmp_path: Path) -> None:
    store = ShotContinuityStore(tmp_path)
    store.put(1, _contract("shot-1"), expected_revision=0)
    stale = store.put(
        1,
        _contract(
            "shot-2",
            predecessor_shot_id="shot-1",
            predecessor_revision=1,
        ),
        expected_revision=0,
    )
    store.put(
        1,
        _contract(
            "shot-3",
            predecessor_shot_id="shot-2",
            predecessor_revision=1,
        ),
        expected_revision=0,
    )
    store.put(1, _contract("shot-1", state="changed"), expected_revision=1)

    assert store.stale_dependents(1, "shot-1") == (stale,)

    refreshed = _contract(
        "shot-2",
        state="refreshed",
        predecessor_shot_id="shot-1",
        predecessor_revision=2,
    )
    store.put(1, refreshed, expected_revision=1)
    assert store.stale_dependents(1, "shot-1") == ()


@pytest.mark.parametrize("episode", [0, -1])
def test_episode_must_be_positive(tmp_path: Path, episode: int) -> None:
    store = ShotContinuityStore(tmp_path)
    with pytest.raises(ValueError):
        store.load_active(episode, "shot-1")
    with pytest.raises(ValueError):
        store.put(episode, _contract("shot-1"), expected_revision=0)


@pytest.mark.parametrize(
    "shot_id",
    ["", ".", "..", "../escape", "nested/shot", r"nested\shot", " leading"],
)
def test_shot_id_must_be_safe(tmp_path: Path, shot_id: str) -> None:
    store = ShotContinuityStore(tmp_path)
    with pytest.raises(ValueError):
        store.load_active(1, shot_id)


def test_put_requires_candidate_identity_to_match_key(tmp_path: Path) -> None:
    data_dir = tmp_path / ".shot_continuity"
    data_dir.mkdir()
    contract = _contract("shot-2", revision=1)
    (data_dir / "ep001.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "episode": 1,
                "shots": {
                    "shot-1": {
                        "active_revision": 1,
                        "revisions": [contract.model_dump(mode="json")],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="shot_id"):
        ShotContinuityStore(tmp_path).load_active(1, "shot-1")


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 2, "episode": 1, "shots": {}},
        {"schema_version": 1, "episode": 2, "shots": {}},
    ],
)
def test_load_rejects_schema_or_episode_identity_mismatch(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    data_dir = tmp_path / ".shot_continuity"
    data_dir.mkdir()
    (data_dir / "ep001.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        ShotContinuityStore(tmp_path).load_active(1, "shot-1")


@pytest.mark.parametrize("invalid_revision", [True, 1.0, "1"])
@pytest.mark.parametrize("location", ["active_revision", "revision"])
def test_load_rejects_non_integer_revision_values(
    tmp_path: Path,
    invalid_revision: object,
    location: str,
) -> None:
    data_dir = tmp_path / ".shot_continuity"
    data_dir.mkdir()
    contract = _contract("shot-1", revision=1).model_dump(mode="json")
    entry: dict[str, object] = {
        "active_revision": 1,
        "revisions": [contract],
    }
    if location == "active_revision":
        entry["active_revision"] = invalid_revision
    else:
        contract["revision"] = invalid_revision
    (data_dir / "ep001.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "episode": 1,
                "shots": {"shot-1": entry},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="revision"):
        ShotContinuityStore(tmp_path).load_active(1, "shot-1")


def test_store_rejects_symlinked_storage_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".shot_continuity").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        ShotContinuityStore(tmp_path).put(1, _contract("shot-1"), 0)


def test_write_is_complete_and_leaves_no_temporary_files(tmp_path: Path) -> None:
    store = ShotContinuityStore(tmp_path)
    saved = store.put(7, _contract("shot-1"), expected_revision=0)
    target = tmp_path / ".shot_continuity" / "ep007.json"

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload == {
        "schema_version": 1,
        "episode": 7,
        "shots": {
            "shot-1": {
                "active_revision": 1,
                "revisions": [saved.model_dump(mode="json")],
            }
        },
    }
    assert not list(target.parent.glob("*.tmp"))


def test_same_process_concurrent_cas_loses_no_revision(tmp_path: Path) -> None:
    store = ShotContinuityStore(tmp_path)
    store.put(1, _contract("shot-1"), expected_revision=0)

    def update(index: int) -> int:
        while True:
            active = store.load_active(1, "shot-1")
            assert active is not None
            try:
                return store.put(
                    1,
                    _contract("shot-1", state=f"state-{index}"),
                    expected_revision=active.revision,
                ).revision
            except ContinuityRevisionConflict:
                continue

    with ThreadPoolExecutor(max_workers=6) as executor:
        revisions = tuple(executor.map(update, range(12)))

    assert sorted(revisions) == list(range(2, 14))
    assert tuple(c.revision for c in store.list_revisions(1, "shot-1")) == tuple(
        range(1, 14)
    )


def test_put_many_is_atomic_when_later_candidate_conflicts(tmp_path: Path) -> None:
    store = ShotContinuityStore(tmp_path)
    store.put(1, _contract("shot-2"), 0)

    with pytest.raises(ContinuityRevisionConflict):
        store.put_many(
            1,
            (
                (_contract("shot-1"), 0),
                (_contract("shot-2", state="changed"), 0),
            ),
        )

    assert store.load_active(1, "shot-1") is None
    assert store.load_active(1, "shot-2").scene.scene_state == "initial"


def test_put_many_binds_child_to_final_in_batch_predecessor_revision(
    tmp_path: Path,
) -> None:
    store = ShotContinuityStore(tmp_path)

    saved = store.put_many(
        1,
        (
            (_contract("shot-1"), 0),
            (
                _contract(
                    "shot-2",
                    predecessor_shot_id="shot-1",
                    predecessor_revision=1,
                ),
                0,
            ),
        ),
    )

    assert tuple(item.revision for item in saved) == (1, 1)
    assert saved[1].predecessor_revision == saved[0].revision


def test_put_many_rejects_stale_external_predecessor_without_writing(
    tmp_path: Path,
) -> None:
    store = ShotContinuityStore(tmp_path)
    store.put(1, _contract("shot-1"), 0)
    store.put(1, _contract("shot-1", state="changed"), 1)

    with pytest.raises(
        ContinuityRevisionConflict, match="predecessor_revision_stale"
    ):
        store.put_many(
            1,
            ((
                _contract(
                    "shot-2",
                    predecessor_shot_id="shot-1",
                    predecessor_revision=1,
                ),
                0,
            ),),
        )

    assert store.load_active(1, "shot-2") is None
