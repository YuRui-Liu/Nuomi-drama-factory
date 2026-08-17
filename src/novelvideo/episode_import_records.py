"""Durable import audit records and downstream revision consumption state."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Iterable


STALE_STAGES = ("characters", "scenes", "beats", "media")


class EpisodeImportRecords:
    def __init__(self, sqlite_store: Any) -> None:
        self.sqlite_store = sqlite_store

    async def _db(self):
        db = await self.sqlite_store._ensure_db()
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS episode_import_records (
                import_id TEXT PRIMARY KEY,
                target_revision INTEGER NOT NULL,
                episodes_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS episode_stage_revisions (
                episode_number INTEGER NOT NULL,
                stage TEXT NOT NULL,
                consumed_revision INTEGER NOT NULL,
                stale INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (episode_number, stage)
            );
            """
        )
        await db.commit()
        return db

    async def record_result(
        self, *, import_id: str, target_revision: int, episodes: Iterable[dict[str, Any]]
    ) -> None:
        db = await self._db()
        await db.execute(
            """INSERT INTO episode_import_records VALUES (?, ?, ?, ?)
            ON CONFLICT(import_id) DO UPDATE SET
              target_revision=excluded.target_revision,
              episodes_json=excluded.episodes_json""",
            (
                import_id,
                target_revision,
                json.dumps(list(episodes), ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()

    async def list_results(self) -> list[dict[str, Any]]:
        db = await self._db()
        async with db.execute(
            "SELECT * FROM episode_import_records ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            {
                "import_id": row["import_id"],
                "target_revision": int(row["target_revision"]),
                "episodes": json.loads(row["episodes_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    async def mark_stale(self, *, episode_number: int, source_revision: int) -> None:
        db = await self._db()
        now = datetime.now(timezone.utc).isoformat()
        await db.executemany(
            """INSERT INTO episode_stage_revisions VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(episode_number, stage) DO UPDATE SET
              stale=1,
              updated_at=excluded.updated_at""",
            [(episode_number, stage, source_revision, now) for stage in STALE_STAGES],
        )
        await db.commit()

    async def list_stale(self, episode_number: int | None = None) -> list[dict[str, Any]]:
        db = await self._db()
        sql = "SELECT * FROM episode_stage_revisions WHERE stale=1"
        params: tuple[Any, ...] = ()
        if episode_number is not None:
            sql += " AND episode_number=?"
            params = (episode_number,)
        sql += " ORDER BY episode_number, CASE stage WHEN 'characters' THEN 1 WHEN 'scenes' THEN 2 WHEN 'beats' THEN 3 ELSE 4 END"
        async with db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [
            {
                "episode_number": int(row["episode_number"]),
                "stage": row["stage"],
                "consumed_revision": int(row["consumed_revision"]),
                "stale": bool(row["stale"]),
            }
            for row in rows
        ]

    async def clear_stale(
        self, *, episode_number: int, stage: str, source_revision: int
    ) -> bool:
        """Clear a stage only after it consumed the current source revision."""
        if stage not in STALE_STAGES:
            raise ValueError(f"unknown downstream stage: {stage}")
        db = await self._db()
        source = await (
            await db.execute(
                "SELECT source_revision FROM episode_sources WHERE episode_number=?",
                (episode_number,),
            )
        ).fetchone()
        if source is None or int(source[0]) != source_revision:
            return False
        cursor = await db.execute(
            "DELETE FROM episode_stage_revisions WHERE episode_number=? AND stage=?",
            (episode_number, stage),
        )
        remaining = await (
            await db.execute(
                "SELECT 1 FROM episode_stage_revisions WHERE episode_number=? AND stale=1 LIMIT 1",
                (episode_number,),
            )
        ).fetchone()
        if remaining is None:
            await db.execute(
                "UPDATE episode_sources SET downstream_stale=0 WHERE episode_number=?",
                (episode_number,),
            )
        await db.commit()
        return cursor.rowcount > 0

    async def mark_stage_consumed(
        self, *, episode_number: int, stage: str, source_revision: int
    ) -> bool:
        """Clear one stage only when it consumed the current source revision."""
        if stage not in STALE_STAGES:
            raise ValueError(f"unknown downstream stage: {stage}")
        return await self.clear_stale(
            episode_number=episode_number,
            stage=stage,
            source_revision=source_revision,
        )
