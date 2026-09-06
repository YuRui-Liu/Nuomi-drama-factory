"""Persistent CE task-concurrency settings and the frozen process snapshot."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from novelvideo.sqlite_pragmas import configure_sqlite_connection

TASK_CONCURRENCY_SETTINGS_KEY = "task_concurrency_v1"
TASK_CONCURRENCY_LANES = ("default", "video", "world", "ffmpeg")
DEFAULT_TASK_CONCURRENCY = {
    "default": 3,
    "video": 5,
    "world": 1,
    "ffmpeg": 1,
}


class TaskConcurrencySettingsError(RuntimeError):
    """A settings failure with a stable machine-readable code."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TaskConcurrencySettings:
    lanes: dict[str, int]


def _settings_db_path() -> Path:
    # config imports runtime settings during module initialization, so keep this
    # import lazy just like the other settings modules do.
    from novelvideo import config

    return Path(config.STATE_DIR) / "local" / "settings.db"


def _connect() -> sqlite3.Connection:
    path = _settings_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
    configure_sqlite_connection(connection)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.commit()
    return connection


def _is_valid_lane_value(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 32


def _validated(values: Mapping[str, object]) -> dict[str, int]:
    if not isinstance(values, Mapping) or set(values) != set(TASK_CONCURRENCY_LANES):
        raise TaskConcurrencySettingsError(
            "All four task concurrency lane values are required.",
            code="TASK_CONCURRENCY_INVALID",
        )
    result: dict[str, int] = {}
    for lane in TASK_CONCURRENCY_LANES:
        value = values[lane]
        if not _is_valid_lane_value(value):
            raise TaskConcurrencySettingsError(
                f"{lane} concurrency must be an integer from 1 to 32.",
                code="TASK_CONCURRENCY_INVALID",
            )
        result[lane] = value
    return result


def _loaded_lanes(payload: object) -> dict[str, int]:
    if not isinstance(payload, dict):
        return dict(DEFAULT_TASK_CONCURRENCY)
    lanes: dict[str, int] = {}
    for lane, default in DEFAULT_TASK_CONCURRENCY.items():
        value = payload.get(lane)
        lanes[lane] = value if _is_valid_lane_value(value) else default
    return lanes


def load_task_concurrency_settings() -> TaskConcurrencySettings:
    connection = _connect()
    try:
        row = connection.execute(
            "SELECT value FROM runtime_settings WHERE key = ?",
            (TASK_CONCURRENCY_SETTINGS_KEY,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return TaskConcurrencySettings(dict(DEFAULT_TASK_CONCURRENCY))
    try:
        payload = json.loads(str(row[0]))
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = None
    return TaskConcurrencySettings(_loaded_lanes(payload))


def save_task_concurrency_settings(
    values: Mapping[str, object],
) -> TaskConcurrencySettings:
    lanes = _validated(values)
    payload = json.dumps(lanes, ensure_ascii=False, separators=(",", ":"))
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO runtime_settings(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (
                TASK_CONCURRENCY_SETTINGS_KEY,
                payload,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return TaskConcurrencySettings(lanes)


@lru_cache(maxsize=1)
def process_task_concurrency_settings() -> TaskConcurrencySettings:
    """Return the settings frozen for the lifetime of this process."""
    return load_task_concurrency_settings()


def reset_process_task_concurrency_for_tests() -> None:
    """Clear the frozen process snapshot. Tests only; saves never call this."""
    process_task_concurrency_settings.cache_clear()
