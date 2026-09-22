"""Read authoritative source versions from a project-bound SQLite database."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

import aiosqlite


class SourceVersionConflict(ValueError):
    error_code = "SOURCE_VERSION_CONFLICT"


class SourceVersionUnavailable(SourceVersionConflict):
    error_code = "SOURCE_VERSION_UNAVAILABLE"


async def register_existing_source_database(project_dir: str | Path, state_dir: str | Path) -> None:
    """Upgrade source-backed projects using paths from an authorized ProjectContext.

    Probe read-only first: merely viewing a source-less legacy project must not
    create a database, source table, or locator. Never derive this path from JSON.
    """
    root = Path(project_dir).resolve()
    if (root / ".episode-source-db.json").exists():
        return
    database = Path(state_dir).resolve() / "data.db"
    if not database.is_file():
        return
    try:
        async with aiosqlite.connect(database.as_uri() + "?mode=ro", uri=True) as db:
            async with db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='episode_sources'"
            ) as cursor:
                if await cursor.fetchone() is None:
                    return
            async with db.execute("SELECT 1 FROM episode_sources LIMIT 1") as cursor:
                if await cursor.fetchone() is None:
                    return
        # mode=rw is intentional: only the verified existing database may be
        # migrated, and disappearance between probe and migration must fail.
        async with aiosqlite.connect(database.as_uri() + "?mode=rw", uri=True) as db:
            await bind_source_database(db, root, database.parent)
    except (OSError, sqlite3.Error):
        raise SourceVersionUnavailable("SOURCE_VERSION_UNAVAILABLE: source database cannot be registered") from None


async def bind_source_database(db, project_dir: str | Path, state_dir: str | Path) -> None:
    root = Path(project_dir).resolve()
    state = Path(state_dir).resolve()
    await db.execute(
        "CREATE TABLE IF NOT EXISTS episode_source_identity ("
        "id INTEGER PRIMARY KEY CHECK(id=1), output_dir TEXT NOT NULL, "
        "state_dir TEXT NOT NULL, binding_id TEXT NOT NULL)"
    )
    await db.execute(
        "INSERT OR IGNORE INTO episode_source_identity VALUES (1, ?, ?, ?)",
        (str(root), str(state), uuid4().hex),
    )
    row = await (await db.execute(
        "SELECT output_dir, state_dir, binding_id FROM episode_source_identity WHERE id=1"
    )).fetchone()
    if row is None or row[0] != str(root) or row[1] != str(state):
        raise SourceVersionUnavailable("SOURCE_VERSION_UNAVAILABLE: project identity mismatch")
    await db.commit()
    payload = {"db_path": str(state / "data.db"), "binding_id": row[2]}
    locator = root / ".episode-source-db.json"
    if locator.is_file():
        try:
            if json.loads(locator.read_text()) == payload:
                return
        except (OSError, ValueError):
            pass  # The authoritative project store can repair its own locator.
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / f".episode-source-db.{uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temporary, locator)
    finally:
        temporary.unlink(missing_ok=True)


def current_source_version(project_dir: str | Path, episode: int) -> tuple[int, str] | None:
    root = Path(project_dir).resolve()
    locator = root / ".episode-source-db.json"
    if not locator.exists():
        return None  # Legacy projects have no registered episode source store.
    connection = None
    try:
        payload = json.loads(locator.read_text(encoding="utf-8"))
        database = Path(payload["db_path"]).resolve(strict=True)
        if database.name != "data.db":
            raise ValueError("invalid source database name")
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)
        identity = connection.execute(
            "SELECT output_dir, state_dir, binding_id FROM episode_source_identity WHERE id=1"
        ).fetchone()
        if identity != (str(root), str(database.parent), payload["binding_id"]):
            raise ValueError("source database identity mismatch")
        row = connection.execute(
            "SELECT source_revision, content_hash FROM episode_sources WHERE episode_number=?",
            (episode,),
        ).fetchone()
        return (int(row[0]), str(row[1])) if row else None
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        raise SourceVersionUnavailable("SOURCE_VERSION_UNAVAILABLE: source database cannot be verified") from None
    finally:
        if connection is not None:
            connection.close()


def require_current_source(
    project_dir: str | Path, episode: int, source_hash: str, *, source_revision: int | None = None,
) -> None:
    current = current_source_version(project_dir, episode)
    if current is not None and (
        current[1] != source_hash or (source_revision is not None and current[0] != source_revision)
    ):
        raise SourceVersionConflict("SOURCE_VERSION_CONFLICT: episode source changed; rebuild semantics and director plan")
