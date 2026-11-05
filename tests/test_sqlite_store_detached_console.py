from unittest.mock import AsyncMock

import pytest

from novelvideo.sqlite_store import SQLiteStore, console


@pytest.mark.asyncio
async def test_initialize_does_not_write_to_detached_terminal(monkeypatch, tmp_path):
    store = SQLiteStore('local/test', output_dir=str(tmp_path), state_dir=str(tmp_path))
    store._ensure_db = AsyncMock()

    def disconnected(*args, **kwargs):
        raise SystemExit(1)  # Rich's handling of a broken stdout pipe.

    monkeypatch.setattr(console, 'print', disconnected)
    await store.initialize()
    store._ensure_db.assert_awaited_once()
