"""Durable narrative-group dialogue snapshots, tasks, takes and adoption refs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping
from uuid import uuid4

from pydantic import BaseModel

from novelvideo.audio.dubbing_contracts import (
    DialogueSnapshot,
    DubbingPreparation,
    DubbingTake,
    DubbingTask,
)


class DubbingRevisionConflict(RuntimeError):
    """A group changed since the caller loaded its expected revision."""


class DubbingRequestConflict(RuntimeError):
    """An idempotency key was reused for different generation inputs."""


def _canonical(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def line_input_hash(line: Mapping[str, Any] | BaseModel) -> str:
    """Hash only audio-affecting inputs for one immutable dialogue take."""
    if isinstance(line, BaseModel):
        values = line.model_dump(mode="json")
    elif isinstance(line, Mapping):
        values = dict(line)
    else:
        raise TypeError("line must be a mapping or model")
    payload = {
        "character_id": values.get("character_id", values.get("character_key")),
        "identity_id": values.get("identity_id") or "",
        "text": values.get("text"),
        "voice_version_id": values.get("voice_version_id"),
        "performance": values.get("performance") or {},
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _model_json(value: BaseModel) -> str:
    return _canonical(value)


class DubbingStore:
    """Small SQLite repository sharing the project's existing ``data.db``."""

    def __init__(self, db_path: str | Path, project_dir: str | Path):
        self.db_path = Path(db_path)
        self.project_dir = Path(project_dir).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS dubbing_group_state (
                    episode INTEGER NOT NULL,
                    group_id TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (episode, group_id)
                );
                CREATE TABLE IF NOT EXISTS dubbing_legacy_dialogues (
                    dialogue_id TEXT PRIMARY KEY,
                    episode INTEGER NOT NULL,
                    group_id TEXT NOT NULL,
                    source_revision TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    UNIQUE (episode, group_id, source_revision, source_key)
                );
                CREATE TABLE IF NOT EXISTS dubbing_preparations (
                    preparation_id TEXT PRIMARY KEY,
                    episode INTEGER NOT NULL,
                    group_id TEXT NOT NULL,
                    script_revision TEXT NOT NULL,
                    source_revision TEXT NOT NULL,
                    source_snapshot_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
                CREATE TABLE IF NOT EXISTS dubbing_tasks (
                    task_id TEXT PRIMARY KEY,
                    episode INTEGER NOT NULL,
                    group_id TEXT NOT NULL,
                    preparation_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    dialogue_ids_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL,
                    backend_task_id TEXT NOT NULL DEFAULT '',
                    failed_dialogue_ids_json TEXT NOT NULL DEFAULT '[]',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    UNIQUE (episode, group_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS dubbing_takes (
                    take_id TEXT PRIMARY KEY,
                    episode INTEGER NOT NULL,
                    group_id TEXT NOT NULL,
                    preparation_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    dialogue_id TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    voice_version_id TEXT NOT NULL,
                    audio_path TEXT NOT NULL,
                    audio_sha256 TEXT NOT NULL,
                    actual_duration_seconds REAL NOT NULL,
                    target_duration_seconds REAL,
                    duration_delta_seconds REAL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    workflow_version TEXT NOT NULL,
                    compiler_version TEXT NOT NULL DEFAULT '',
                    effective_parameters_json TEXT NOT NULL,
                    technical_status TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    UNIQUE (task_id, dialogue_id, input_hash)
                );
                CREATE TABLE IF NOT EXISTS dubbing_adoptions (
                    episode INTEGER NOT NULL,
                    group_id TEXT NOT NULL,
                    dialogue_id TEXT NOT NULL,
                    take_id TEXT NOT NULL,
                    adopted_by TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    adopted_revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    PRIMARY KEY (episode, group_id, dialogue_id)
                );
                CREATE INDEX IF NOT EXISTS idx_dubbing_takes_group
                    ON dubbing_takes(episode, group_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_dubbing_tasks_group
                    ON dubbing_tasks(episode, group_id, created_at);
                """
            )
            task_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(dubbing_tasks)")
            }
            if "dialogue_ids_json" not in task_columns:
                db.execute(
                    "ALTER TABLE dubbing_tasks ADD COLUMN dialogue_ids_json TEXT NOT NULL DEFAULT '[]'"
                )
            take_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(dubbing_takes)")
            }
            if "compiler_version" not in take_columns:
                db.execute(
                    "ALTER TABLE dubbing_takes ADD COLUMN compiler_version TEXT NOT NULL DEFAULT ''"
                )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
            if db.in_transaction:
                db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _decode_dialogue(row: sqlite3.Row) -> DialogueSnapshot:
        return DialogueSnapshot.model_validate_json(row["snapshot_json"])

    def import_legacy_dialogues(
        self,
        *,
        episode: int,
        group_id: str,
        source_revision: str,
        lines: list[Mapping[str, Any]],
    ) -> list[DialogueSnapshot]:
        """Persist UUIDs for the legacy H3 source locator without text matching."""
        if episode <= 0 or not group_id.strip() or not source_revision.strip():
            raise ValueError("episode, group_id and source_revision are required")
        source_keys = [str(line.get("source_key") or "").strip() for line in lines]
        if not source_keys or any(not item for item in source_keys):
            raise ValueError("legacy source lines require source_key")
        if len(source_keys) != len(set(source_keys)):
            raise ValueError("legacy source keys must be unique")

        result: list[DialogueSnapshot] = []
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            historical = db.execute(
                """SELECT * FROM dubbing_legacy_dialogues
                   WHERE episode=? AND group_id=? AND source_revision!=?""",
                (episode, group_id, source_revision),
            ).fetchall()
            for old_row in historical:
                old = self._decode_dialogue(old_row)
                if old.source_status != "deleted":
                    db.execute(
                        "UPDATE dubbing_legacy_dialogues SET snapshot_json=? WHERE dialogue_id=?",
                        (_model_json(old.model_copy(update={"source_status": "deleted"})), old.dialogue_id),
                    )
            for raw in lines:
                source_key = str(raw["source_key"])
                row = db.execute(
                    """SELECT * FROM dubbing_legacy_dialogues
                       WHERE episode=? AND group_id=? AND source_revision=? AND source_key=?""",
                    (episode, group_id, source_revision, source_key),
                ).fetchone()
                if row is not None:
                    existing = self._decode_dialogue(row)
                    expected = {key: raw[key] for key in ("character_id", "identity_id", "speaker", "text", "performance", "target_duration_seconds") if key in raw}
                    actual = {
                        "character_id": existing.character_id,
                        "identity_id": existing.identity_id,
                        "speaker": existing.speaker,
                        "text": existing.text,
                        "performance": existing.performance,
                        "target_duration_seconds": existing.target_duration_seconds,
                    }
                    if any(actual[key] != value for key, value in expected.items()):
                        raise ValueError("legacy source revision is immutable")
                    result.append(existing)
                    continue

                dialogue_id = uuid4().hex
                snapshot = DialogueSnapshot(
                    dialogue_id=dialogue_id,
                    character_id=str(raw.get("character_id") or "unknown"),
                    identity_id=str(raw.get("identity_id") or ""),
                    speaker=str(raw.get("speaker") or ""),
                    script_revision=str(raw.get("script_revision") or source_revision),
                    source_revision=source_revision,
                    group_id=group_id,
                    text=str(raw.get("text") or "").strip(),
                    voice_version_id=str(raw.get("voice_version_id") or ""),
                    target_duration_seconds=raw.get("target_duration_seconds"),
                    performance=dict(raw.get("performance") or {}),
                    source_key=source_key,
                )
                db.execute(
                    """INSERT INTO dubbing_legacy_dialogues
                       (dialogue_id,episode,group_id,source_revision,source_key,snapshot_json)
                       VALUES (?,?,?,?,?,?)""",
                    (dialogue_id, episode, group_id, source_revision, source_key, _model_json(snapshot)),
                )
                result.append(snapshot)
        return result

    def save_preparation(self, preparation: DubbingPreparation) -> DubbingPreparation:
        payload = _model_json(preparation)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT payload_json FROM dubbing_preparations WHERE preparation_id=?",
                (preparation.preparation_id,),
            ).fetchone()
            if existing:
                if existing["payload_json"] != payload:
                    raise DubbingRequestConflict("preparation id reused with different inputs")
                return DubbingPreparation.model_validate_json(existing["payload_json"])
            current = db.execute(
                "SELECT revision FROM dubbing_group_state WHERE episode=? AND group_id=?",
                (preparation.episode, preparation.group_id),
            ).fetchone()
            revision = int(current["revision"]) if current else 0
            if revision != preparation.expected_revision:
                raise DubbingRevisionConflict("dubbing group revision changed")
            db.execute(
                """INSERT INTO dubbing_preparations
                   (preparation_id,episode,group_id,script_revision,source_revision,
                    source_snapshot_hash,payload_json)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    preparation.preparation_id,
                    preparation.episode,
                    preparation.group_id,
                    preparation.script_revision,
                    preparation.source_revision,
                    preparation.source_snapshot_hash,
                    payload,
                )
            )
            db.execute(
                """INSERT INTO dubbing_group_state(episode,group_id,revision) VALUES(?,?,?)
                   ON CONFLICT(episode,group_id) DO NOTHING""",
                (preparation.episode, preparation.group_id, revision),
            )
        return preparation

    def get_preparation(self, preparation_id: str) -> DubbingPreparation | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT payload_json FROM dubbing_preparations WHERE preparation_id=?",
                (preparation_id,),
            ).fetchone()
        return DubbingPreparation.model_validate_json(row["payload_json"]) if row else None

    def create_task(
        self,
        *,
        episode: int,
        group_id: str,
        preparation_id: str,
        request_id: str,
        payload_hash: str,
        dialogue_ids: tuple[str, ...],
    ) -> tuple[DubbingTask, bool]:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT * FROM dubbing_tasks
                   WHERE episode=? AND group_id=? AND request_id=?""",
                (episode, group_id, request_id),
            ).fetchone()
            if row is not None:
                task = self._decode_task(row)
                if (task.preparation_id != preparation_id or task.payload_hash != payload_hash
                        or task.dialogue_ids != tuple(dialogue_ids)):
                    raise DubbingRequestConflict("request id reused with different generation inputs")
                return task, False
            task = DubbingTask(
                task_id=uuid4().hex,
                preparation_id=preparation_id,
                request_id=request_id,
                payload_hash=payload_hash,
                dialogue_ids=tuple(dialogue_ids),
            )
            db.execute(
                """INSERT INTO dubbing_tasks
                   (task_id,episode,group_id,preparation_id,request_id,payload_hash,dialogue_ids_json,status)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (task.task_id, episode, group_id, preparation_id, request_id, payload_hash,
                 json.dumps(task.dialogue_ids), task.status),
            )
        return task, True

    @staticmethod
    def _decode_task(row: sqlite3.Row) -> DubbingTask:
        return DubbingTask(
            task_id=row["task_id"],
            preparation_id=row["preparation_id"],
            request_id=row["request_id"],
            payload_hash=row["payload_hash"],
            dialogue_ids=tuple(json.loads(row["dialogue_ids_json"])),
            status=row["status"],
            backend_task_id=row["backend_task_id"],
            failed_dialogue_ids=tuple(json.loads(row["failed_dialogue_ids_json"])),
            error=row["error"],
        )

    def get_task(self, task_id: str) -> DubbingTask | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM dubbing_tasks WHERE task_id=?", (task_id,)).fetchone()
        return self._decode_task(row) if row else None

    def get_task_by_backend_id(self, backend_task_id: str) -> DubbingTask | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM dubbing_tasks WHERE backend_task_id=?", (backend_task_id,)
            ).fetchone()
        return self._decode_task(row) if row else None

    def list_tasks(self, episode: int, group_id: str) -> list[DubbingTask]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM dubbing_tasks WHERE episode=? AND group_id=? ORDER BY created_at,task_id",
                (episode, group_id),
            ).fetchall()
        return [self._decode_task(row) for row in rows]

    def latest_preparation(self, episode: int, group_id: str) -> DubbingPreparation | None:
        with self.connection() as db:
            row = db.execute(
                """SELECT payload_json FROM dubbing_preparations WHERE episode=? AND group_id=?
                   ORDER BY created_at DESC,preparation_id DESC LIMIT 1""",
                (episode, group_id),
            ).fetchone()
        return DubbingPreparation.model_validate_json(row["payload_json"]) if row else None

    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        backend_task_id: str | None = None,
        failed_dialogue_ids: tuple[str, ...] | None = None,
        error: str | None = None,
    ) -> DubbingTask:
        with self.connection() as db:
            row = db.execute("SELECT * FROM dubbing_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            current = self._decode_task(row)
            replacement = current.model_copy(
                update={
                    key: value
                    for key, value in {
                        "status": status,
                        "backend_task_id": backend_task_id,
                        "failed_dialogue_ids": failed_dialogue_ids,
                        "error": error,
                    }.items()
                    if value is not None
                }
            )
            db.execute(
                """UPDATE dubbing_tasks SET status=?,backend_task_id=?,
                   failed_dialogue_ids_json=?,error=? WHERE task_id=?""",
                (replacement.status, replacement.backend_task_id,
                 json.dumps(replacement.failed_dialogue_ids), replacement.error, task_id),
            )
        return replacement

    def save_take(self, take: DubbingTake) -> DubbingTake:
        values = (
            take.take_id,
            take.preparation_id,
            take.task_id,
            take.dialogue_id,
            take.input_hash,
            take.voice_version_id,
            take.audio_path,
            take.audio_sha256,
            take.actual_duration_seconds,
            take.target_duration_seconds,
            take.duration_delta_seconds,
            take.provider,
            take.model,
            take.workflow_version,
            take.compiler_version,
            _canonical(take.effective_parameters),
            take.technical_status,
            take.status,
            take.error,
        )
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM dubbing_takes WHERE take_id=?", (take.take_id,)).fetchone()
            if existing is not None:
                if self._take_values(existing) != values:
                    raise ValueError("dubbing take is immutable")
                return take
            prep = self.get_preparation(take.preparation_id)
            if prep is None:
                raise ValueError("dubbing preparation not found")
            db.execute(
                """INSERT INTO dubbing_takes(
                   take_id,episode,group_id,preparation_id,task_id,dialogue_id,input_hash,
                   voice_version_id,audio_path,audio_sha256,actual_duration_seconds,
                   target_duration_seconds,duration_delta_seconds,provider,model,workflow_version,
                   compiler_version,effective_parameters_json,technical_status,status,error)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (take.take_id, prep.episode, prep.group_id, *values[1:]),
            )
        return take

    @staticmethod
    def _take_values(row: sqlite3.Row) -> tuple[Any, ...]:
        return (
            row["take_id"], row["preparation_id"], row["task_id"], row["dialogue_id"],
            row["input_hash"], row["voice_version_id"], row["audio_path"], row["audio_sha256"],
            row["actual_duration_seconds"], row["target_duration_seconds"], row["duration_delta_seconds"],
            row["provider"], row["model"], row["workflow_version"],
            row["compiler_version"],
            row["effective_parameters_json"], row["technical_status"], row["status"], row["error"],
        )

    @staticmethod
    def _decode_take(row: sqlite3.Row) -> DubbingTake:
        return DubbingTake(
            take_id=row["take_id"], preparation_id=row["preparation_id"], task_id=row["task_id"],
            dialogue_id=row["dialogue_id"], input_hash=row["input_hash"],
            voice_version_id=row["voice_version_id"], audio_path=row["audio_path"],
            audio_sha256=row["audio_sha256"], actual_duration_seconds=row["actual_duration_seconds"],
            target_duration_seconds=row["target_duration_seconds"], duration_delta_seconds=row["duration_delta_seconds"],
            provider=row["provider"], model=row["model"], workflow_version=row["workflow_version"],
            compiler_version=row["compiler_version"],
            effective_parameters=json.loads(row["effective_parameters_json"]),
            technical_status=row["technical_status"], status=row["status"], error=row["error"],
        )

    def get_take(self, take_id: str) -> DubbingTake | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM dubbing_takes WHERE take_id=?", (take_id,)).fetchone()
        return self._decode_take(row) if row else None

    def list_takes(self, episode: int, group_id: str) -> list[DubbingTake]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM dubbing_takes WHERE episode=? AND group_id=? ORDER BY created_at,take_id",
                (episode, group_id),
            ).fetchall()
        return [self._decode_take(row) for row in rows]

    def find_reusable_take(
        self, episode: int, group_id: str, dialogue_id: str, input_hash: str
    ) -> DubbingTake | None:
        with self.connection() as db:
            row = db.execute(
                """SELECT * FROM dubbing_takes WHERE episode=? AND group_id=? AND dialogue_id=?
                   AND input_hash=? AND status='succeeded' AND technical_status='passed'
                   ORDER BY created_at DESC,take_id DESC LIMIT 1""",
                (episode, group_id, dialogue_id, input_hash),
            ).fetchone()
        return self._decode_take(row) if row else None

    def adopt_takes(
        self,
        *,
        episode: int,
        group_id: str,
        take_ids: list[str],
        expected_revision: int,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        if not actor.strip() or not reason.strip() or not take_ids:
            raise ValueError("actor, reason and take_ids are required")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision FROM dubbing_group_state WHERE episode=? AND group_id=?",
                (episode, group_id),
            ).fetchone()
            revision = int(row["revision"]) if row else 0
            if revision != expected_revision:
                raise DubbingRevisionConflict("dubbing group revision changed")
            takes: list[sqlite3.Row] = []
            for take_id in take_ids:
                take = db.execute(
                    """SELECT * FROM dubbing_takes WHERE take_id=? AND episode=? AND group_id=?""",
                    (take_id, episode, group_id),
                ).fetchone()
                if take is None:
                    raise KeyError(take_id)
                if take["status"] != "succeeded" or take["technical_status"] != "passed":
                    raise ValueError("only technically passed successful takes can be adopted")
                takes.append(take)
            next_revision = revision + 1
            for take in takes:
                db.execute(
                    """INSERT INTO dubbing_adoptions
                       (episode,group_id,dialogue_id,take_id,adopted_by,reason,adopted_revision)
                       VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(episode,group_id,dialogue_id) DO UPDATE SET
                         take_id=excluded.take_id,adopted_by=excluded.adopted_by,
                         reason=excluded.reason,adopted_revision=excluded.adopted_revision,
                         created_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
                    (episode, group_id, take["dialogue_id"], take["take_id"], actor, reason, next_revision),
                )
            db.execute(
                """INSERT INTO dubbing_group_state(episode,group_id,revision) VALUES(?,?,?)
                   ON CONFLICT(episode,group_id) DO UPDATE SET revision=excluded.revision""",
                (episode, group_id, next_revision),
            )
        return {
            "revision": next_revision,
            "adoptions": self.get_group_state(episode, group_id)["adoptions"],
        }

    def get_group_state(self, episode: int, group_id: str) -> dict[str, Any]:
        with self.connection() as db:
            state = db.execute(
                "SELECT revision FROM dubbing_group_state WHERE episode=? AND group_id=?",
                (episode, group_id),
            ).fetchone()
            rows = db.execute(
                """SELECT dialogue_id,take_id,adopted_by,reason,adopted_revision,created_at
                   FROM dubbing_adoptions WHERE episode=? AND group_id=? ORDER BY dialogue_id""",
                (episode, group_id),
            ).fetchall()
        return {
            "revision": int(state["revision"]) if state else 0,
            "adoptions": {row["dialogue_id"]: dict(row) for row in rows},
        }

    def list_adopted_takes(self, episode: int, group_id: str) -> dict[str, DubbingTake]:
        with self.connection() as db:
            rows = db.execute(
                """SELECT t.* FROM dubbing_takes t JOIN dubbing_adoptions a ON a.take_id=t.take_id
                   WHERE a.episode=? AND a.group_id=? ORDER BY a.dialogue_id""",
                (episode, group_id),
            ).fetchall()
        return {row["dialogue_id"]: self._decode_take(row) for row in rows}


__all__ = [
    "DubbingRequestConflict",
    "DubbingRevisionConflict",
    "DubbingStore",
    "line_input_hash",
]
