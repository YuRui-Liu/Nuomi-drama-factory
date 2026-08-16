from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from novelvideo.media_capabilities.production.models import (
    ProductionNodeStatus,
    ProductionRunStatus,
)
from novelvideo.media_capabilities.production.store import (
    InvalidProductionTransition,
    ProductionStore,
)


def test_run_nodes_and_edges_round_trip(tmp_path: Path) -> None:
    database = tmp_path / "production.db"
    store = ProductionStore(database)
    run = store.create_run("project-1", {"profile": "balanced", "limits": {"video": 2}})
    image = store.add_node(
        run.id,
        "image.generate",
        "episode-1-shot-1-image",
        {"implementation": "image-primary", "params": {"seed": 7}},
    )
    video = store.add_node(
        run.id,
        "video.generate",
        "episode-1-shot-1-video",
        {"implementation": "video-primary"},
    )
    edge = store.add_edge(run.id, image.id, video.id)

    restarted = ProductionStore(database)

    assert restarted.get_run(run.id) == run
    assert restarted.list_nodes(run.id) == [image, video]
    assert restarted.list_edges(run.id) == [edge]


def test_configuration_snapshots_are_immutable(tmp_path: Path) -> None:
    database = tmp_path / "production.db"
    store = ProductionStore(database)
    run_snapshot = {"profile": "strict", "nested": {"revision": 1}}
    node_snapshot = {"implementation": "image-primary", "params": {"seed": 7}}
    run = store.create_run("project-1", run_snapshot)
    node = store.add_node(
        run.id,
        "image.generate",
        "image-1",
        node_snapshot,
    )

    run_snapshot["nested"]["revision"] = 99
    node_snapshot["params"]["seed"] = 99

    assert store.get_run(run.id).config_snapshot == {
        "profile": "strict",
        "nested": {"revision": 1},
    }
    assert store.get_node(node.id).config_snapshot == {
        "implementation": "image-primary",
        "params": {"seed": 7},
    }

    connection = sqlite3.connect(database)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE production_runs SET config_snapshot_json = '{}' WHERE id = ?",
                (run.id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE production_nodes SET config_snapshot_json = '{}' WHERE id = ?",
                (node.id,),
            )
    finally:
        connection.close()


def test_node_creation_is_idempotent_per_run(tmp_path: Path) -> None:
    store = ProductionStore(tmp_path / "production.db")
    first_run = store.create_run("project-1", {})
    second_run = store.create_run("project-1", {})

    first = store.add_node(
        first_run.id,
        "image.generate",
        "shot-1",
        {"implementation": "image-primary"},
    )
    repeated = store.add_node(
        first_run.id,
        "image.generate",
        "shot-1",
        {"implementation": "image-primary"},
    )
    other_run = store.add_node(
        second_run.id,
        "image.generate",
        "shot-1",
        {"implementation": "image-primary"},
    )

    assert repeated == first
    assert len(store.list_nodes(first_run.id)) == 1
    assert other_run.id != first.id


def test_schema_enables_foreign_keys_for_nodes_and_edges(tmp_path: Path) -> None:
    store = ProductionStore(tmp_path / "production.db")
    run = store.create_run("project-1", {})
    node = store.add_node(run.id, "image.generate", "image-1", {})

    connection = store._connect()
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO production_nodes "
                "(id, run_id, node_type, idempotency_key, status, "
                "config_snapshot_json, created_at, updated_at) "
                "VALUES ('orphan', 'missing', 'image.generate', 'key', "
                "'pending', '{}', 'now', 'now')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO production_edges "
                "(id, run_id, upstream_node_id, downstream_node_id, created_at) "
                "VALUES ('edge', ?, ?, 'missing', 'now')",
                (run.id, node.id),
            )
    finally:
        connection.close()


def test_node_transitions_follow_the_declared_lifecycle(tmp_path: Path) -> None:
    store = ProductionStore(tmp_path / "production.db")
    run = store.create_run("project-1", {})
    node = store.add_node(run.id, "image.generate", "image-1", {})

    for status in (
        ProductionNodeStatus.READY,
        ProductionNodeStatus.QUEUED,
        ProductionNodeStatus.RUNNING,
        ProductionNodeStatus.SUCCEEDED,
    ):
        node = store.transition_node(node.id, status)
        assert node.status == status

    with pytest.raises(InvalidProductionTransition, match="terminal"):
        store.transition_node(node.id, ProductionNodeStatus.FAILED)

    pending = store.add_node(run.id, "audio.generate", "audio-1", {})
    with pytest.raises(InvalidProductionTransition, match="pending -> running"):
        store.transition_node(pending.id, ProductionNodeStatus.RUNNING)


def test_run_transitions_allow_pause_resume_and_cancelling(tmp_path: Path) -> None:
    store = ProductionStore(tmp_path / "production.db")
    run = store.create_run("project-1", {})

    paused = store.transition_run(run.id, ProductionRunStatus.PAUSED)
    resumed = store.transition_run(paused.id, ProductionRunStatus.RUNNING)
    cancelling = store.transition_run(resumed.id, ProductionRunStatus.CANCELLING)
    cancelled = store.transition_run(cancelling.id, ProductionRunStatus.CANCELLED)

    assert cancelled.status == ProductionRunStatus.CANCELLED
    with pytest.raises(InvalidProductionTransition, match="terminal"):
        store.transition_run(cancelled.id, ProductionRunStatus.RUNNING)

    active = store.create_run("project-2", {})
    with pytest.raises(InvalidProductionTransition, match="running -> cancelled"):
        store.transition_run(active.id, ProductionRunStatus.CANCELLED)
