"""Immutable, project-local history of adopted character voice references."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


_SCHEMA = """
CREATE TABLE IF NOT EXISTS character_voice_versions (
    version_id TEXT PRIMARY KEY,
    character_id TEXT NOT NULL,
    identity_id TEXT NOT NULL DEFAULT '',
    slot TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    audio_sha256 TEXT NOT NULL,
    path TEXT NOT NULL DEFAULT '',
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(character_id, identity_id, slot, candidate_id)
);
CREATE TABLE IF NOT EXISTS character_voice_current (
    character_id TEXT NOT NULL,
    identity_id TEXT NOT NULL DEFAULT '',
    slot TEXT NOT NULL,
    version_id TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY(character_id, identity_id, slot)
);
"""


class VoiceVersionStore:
    """Append-only voice version history and per-identity/per-slot selection."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        with self.connection() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _decode(row: sqlite3.Row | None, *, current: bool = False) -> dict | None:
        if row is None:
            return None
        item = dict(row)
        item["snapshot"] = json.loads(item.pop("snapshot_json"))
        item["current"] = bool(item.pop("is_current", current))
        return item

    def append_version(
        self,
        character_key: str,
        *,
        identity_id: str = "",
        slot: str = "default",
        candidate_id: str,
        audio_sha256: str,
        path: str = "",
        snapshot: dict,
        db: sqlite3.Connection | None = None,
    ) -> dict:
        """Create a version once and atomically point its scope at that version.

        Pass an existing connection to include version creation in a larger
        transaction such as candidate approval.
        """
        if not character_key or not slot or not candidate_id or not audio_sha256:
            raise ValueError("character, slot, candidate and audio digest are required")
        if db is None:
            with self.connection() as own_db:
                own_db.execute("BEGIN IMMEDIATE")
                return self._insert_and_select(
                    own_db, character_key, identity_id=identity_id, slot=slot,
                    candidate_id=candidate_id, audio_sha256=audio_sha256, path=path,
                    snapshot=snapshot,
                )
        return self._insert_and_select(
            db, character_key, identity_id=identity_id, slot=slot,
            candidate_id=candidate_id, audio_sha256=audio_sha256, path=path,
            snapshot=snapshot,
        )

    def _insert_and_select(
        self,
        db: sqlite3.Connection,
        character_key: str,
        *,
        identity_id: str,
        slot: str,
        candidate_id: str,
        audio_sha256: str,
        path: str,
        snapshot: dict,
    ) -> dict:
        serialized = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        found = db.execute(
            """SELECT * FROM character_voice_versions
               WHERE character_id=? AND identity_id=? AND slot=? AND candidate_id=?""",
            (character_key, identity_id, slot, candidate_id),
        ).fetchone()
        if found:
            if found["audio_sha256"] != audio_sha256 or found["path"] != path:
                raise ValueError("voice version is immutable")
            version_id = found["version_id"]
        else:
            version_id = uuid4().hex
            db.execute(
                """INSERT INTO character_voice_versions
                   (version_id,character_id,identity_id,slot,candidate_id,audio_sha256,path,snapshot_json)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (version_id, character_key, identity_id, slot, candidate_id, audio_sha256, path, serialized),
            )
        db.execute(
            """INSERT INTO character_voice_current(character_id,identity_id,slot,version_id)
               VALUES (?,?,?,?) ON CONFLICT(character_id,identity_id,slot) DO UPDATE SET
               version_id=excluded.version_id, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
            (character_key, identity_id, slot, version_id),
        )
        return self._get_version(db, version_id)

    def _get_version(self, db: sqlite3.Connection, version_id: str) -> dict | None:
        return self._decode(db.execute(
            """SELECT v.*, CASE WHEN c.version_id IS NULL THEN 0 ELSE 1 END AS is_current
               FROM character_voice_versions v LEFT JOIN character_voice_current c
               ON c.version_id=v.version_id WHERE v.version_id=?""", (version_id,),
        ).fetchone())

    def get_version(self, version_id: str) -> dict | None:
        with self.connection() as db:
            return self._get_version(db, version_id)

    def get_current(self, character_key: str, *, identity_id: str = "", slot: str = "default") -> dict | None:
        with self.connection() as db:
            row = db.execute(
                """SELECT v.*, 1 AS is_current FROM character_voice_current c
                   JOIN character_voice_versions v ON v.version_id=c.version_id
                   WHERE c.character_id=? AND c.identity_id=? AND c.slot=?""",
                (character_key, identity_id, slot),
            ).fetchone()
            return self._decode(row)

    def list_versions(self, character_key: str) -> list[dict]:
        with self.connection() as db:
            rows = db.execute(
                """SELECT v.*, CASE WHEN c.version_id IS NULL THEN 0 ELSE 1 END AS is_current
                   FROM character_voice_versions v LEFT JOIN character_voice_current c
                   ON c.version_id=v.version_id WHERE v.character_id=?
                   ORDER BY v.created_at, v.version_id""",
                (character_key,),
            ).fetchall()
            return [self._decode(row) for row in rows]

    def get_legacy_current(
        self,
        character_key: str,
        *,
        identity_id: str = "",
        slot: str = "default",
        project_dir: str | Path,
        reference: dict,
    ) -> dict | None:
        """Project a legacy reference as current without recording adoption."""
        stored_path = str(reference.get("path") or "")
        if not stored_path:
            return None
        root = Path(project_dir).resolve()
        candidate_path = Path(stored_path)
        path = (candidate_path if candidate_path.is_absolute() else root / candidate_path).resolve()
        if not path.is_relative_to(root):
            return None
        relative = path.relative_to(root).as_posix()
        digest = str(reference.get("sha256") or "")
        if path.is_file():
            actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest and digest != actual_digest:
                return None
            digest = actual_digest
        snapshot = {"source": "legacy", "approval_recorded": False}
        version_id = "legacy-" + hashlib.sha256(
            json.dumps([character_key, identity_id, slot, relative, digest], separators=(",", ":")).encode()
        ).hexdigest()
        return {
            "version_id": version_id,
            "character_id": character_key,
            "identity_id": identity_id,
            "slot": slot,
            "candidate_id": "",
            "audio_sha256": digest,
            "path": relative,
            "snapshot": snapshot,
            "current": True,
            "created_at": "",
        }
