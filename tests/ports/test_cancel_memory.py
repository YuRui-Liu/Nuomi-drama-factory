import sqlite3
import time

import pytest

from novelvideo.ports.local.tasks import InMemoryCancellationStore, SQLiteCancellationStore
from novelvideo.ports.tasks import cancel_key


def test_cancel_key_matches_existing_shape() -> None:
    assert (
        cancel_key(
            project_id="p1",
            task_type="render",
            episode=2,
            beat_num=3,
            scope="scene",
            task_id="t1",
        )
        == "task:cancel:p1:render:2:3:scene:t1"
    )


@pytest.mark.asyncio
async def test_memory_cancellation_store_can_write_read_and_isolate_keys() -> None:
    store = InMemoryCancellationStore()

    await store.request_cancel(
        project_id="p1",
        task_type="render",
        episode=2,
        task_id="t1",
        beat_num=3,
    )

    assert await store.is_cancel_requested(
        project_id="p1",
        task_type="render",
        episode=2,
        task_id="t1",
        beat_num=3,
    )
    assert not await store.is_cancel_requested(
        project_id="p1",
        task_type="render",
        episode=2,
        task_id="different",
        beat_num=3,
    )


@pytest.mark.asyncio
async def test_sqlite_cancellation_store_is_shared_across_instances_and_persists_ttl(
    tmp_path,
) -> None:
    database_path = tmp_path / "project" / "data.db"
    writer = SQLiteCancellationStore(database_path)
    reader = SQLiteCancellationStore(database_path)

    await writer.request_cancel(
        project_id="p1",
        task_type="render",
        episode=2,
        task_id="t1",
        beat_num=3,
        scope="scene",
        ttl_seconds=60,
    )

    assert await reader.is_cancel_requested(
        project_id="p1",
        task_type="render",
        episode=2,
        task_id="t1",
        beat_num=3,
        scope="scene",
    )
    with sqlite3.connect(database_path) as conn:
        row = conn.execute(
            "SELECT expires_at FROM task_cancellations WHERE cancel_key = ?",
            ("task:cancel:p1:render:2:3:scene:t1",),
        ).fetchone()
    assert row is not None
    assert float(row[0]) > time.time()


@pytest.mark.asyncio
async def test_sqlite_cancellation_store_reclaims_expired_flags(tmp_path) -> None:
    database_path = tmp_path / "project" / "data.db"
    store = SQLiteCancellationStore(database_path)
    await store.request_cancel(
        project_id="p1",
        task_type="render",
        episode=2,
        task_id="expired",
        ttl_seconds=0,
    )

    assert not await store.is_cancel_requested(
        project_id="p1",
        task_type="render",
        episode=2,
        task_id="expired",
    )
