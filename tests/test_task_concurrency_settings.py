from __future__ import annotations

import json
import sqlite3

import pytest

from novelvideo import config


@pytest.fixture(autouse=True)
def _reset_process_snapshot_after_test():
    yield
    from novelvideo.task_concurrency_settings import (
        reset_process_task_concurrency_for_tests,
    )

    reset_process_task_concurrency_for_tests()


def _isolate_settings_db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    from novelvideo.task_concurrency_settings import (
        reset_process_task_concurrency_for_tests,
    )

    reset_process_task_concurrency_for_tests()


def test_defaults_and_atomic_round_trip(monkeypatch, tmp_path) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    from novelvideo.task_concurrency_settings import (
        DEFAULT_TASK_CONCURRENCY,
        TASK_CONCURRENCY_SETTINGS_KEY,
        load_task_concurrency_settings,
        save_task_concurrency_settings,
    )

    assert load_task_concurrency_settings().lanes == DEFAULT_TASK_CONCURRENCY

    values = {"default": 8, "video": 6, "world": 2, "ffmpeg": 2}
    assert save_task_concurrency_settings(values).lanes == values
    assert load_task_concurrency_settings().lanes == values

    database_path = tmp_path / "state" / "local" / "settings.db"
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT key, value FROM runtime_settings WHERE key = ?",
            (TASK_CONCURRENCY_SETTINGS_KEY,),
        ).fetchall()
    assert rows == [
        (TASK_CONCURRENCY_SETTINGS_KEY, json.dumps(values, separators=(",", ":")))
    ]


@pytest.mark.parametrize(
    "values",
    [
        {"default": 3, "video": 5, "world": 1},
        {"default": 3, "video": 5, "world": 1, "ffmpeg": 1, "extra": 2},
        {"default": 0, "video": 5, "world": 1, "ffmpeg": 1},
        {"default": 33, "video": 5, "world": 1, "ffmpeg": 1},
        {"default": 3.5, "video": 5, "world": 1, "ffmpeg": 1},
        {"default": True, "video": 5, "world": 1, "ffmpeg": 1},
    ],
)
def test_rejects_incomplete_or_invalid_values(monkeypatch, tmp_path, values) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    from novelvideo.task_concurrency_settings import (
        TaskConcurrencySettingsError,
        save_task_concurrency_settings,
    )

    with pytest.raises(TaskConcurrencySettingsError) as exc_info:
        save_task_concurrency_settings(values)

    assert exc_info.value.code == "TASK_CONCURRENCY_INVALID"


def test_load_falls_back_per_lane_for_damaged_stored_values(
    monkeypatch, tmp_path
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    from novelvideo.task_concurrency_settings import (
        TASK_CONCURRENCY_SETTINGS_KEY,
        load_task_concurrency_settings,
    )

    database_path = tmp_path / "state" / "local" / "settings.db"
    database_path.parent.mkdir(parents=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE runtime_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO runtime_settings(key, value, updated_at) VALUES (?, ?, ?)",
            (
                TASK_CONCURRENCY_SETTINGS_KEY,
                json.dumps({"default": 7, "video": "bad", "world": 2}),
                "2026-09-06T00:00:00+00:00",
            ),
        )

    assert load_task_concurrency_settings().lanes == {
        "default": 7,
        "video": 5,
        "world": 2,
        "ffmpeg": 1,
    }


def test_process_snapshot_remains_frozen_until_explicit_test_reset(
    monkeypatch, tmp_path
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    from novelvideo.task_concurrency_settings import (
        process_task_concurrency_settings,
        reset_process_task_concurrency_for_tests,
        save_task_concurrency_settings,
    )

    first = process_task_concurrency_settings()
    save_task_concurrency_settings({"default": 9, "video": 6, "world": 2, "ffmpeg": 2})

    assert process_task_concurrency_settings() is first
    assert process_task_concurrency_settings().lanes["default"] == 3

    reset_process_task_concurrency_for_tests()
    assert process_task_concurrency_settings().lanes["default"] == 9
