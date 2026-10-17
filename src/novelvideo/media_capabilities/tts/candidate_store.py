"""Durable voice candidates in the existing project SQLite database."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4


class VoiceCandidateStore:
    def __init__(self, db_path: str | Path, project_dir: str | Path):
        self.db_path = str(db_path)
        self.project_dir = Path(project_dir).resolve()
        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS character_voice_candidates (
                candidate_id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                character_name TEXT NOT NULL, character_id TEXT NOT NULL DEFAULT '',
                voice_version_id TEXT NOT NULL DEFAULT '', slot TEXT NOT NULL, kind TEXT NOT NULL,
                batch_id TEXT NOT NULL, profile_digest TEXT NOT NULL,
                payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'prepared',
                provider_task_id TEXT NOT NULL DEFAULT '', path TEXT NOT NULL DEFAULT '',
                sha256 TEXT NOT NULL DEFAULT '', report_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS character_nonverbal_selections (
                character_name TEXT NOT NULL, purpose TEXT NOT NULL, candidate_id TEXT NOT NULL,
                PRIMARY KEY(character_name,purpose))""")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(character_voice_candidates)")}
            for name, definition in (
                ("character_id", "TEXT NOT NULL DEFAULT ''"),
                ("voice_version_id", "TEXT NOT NULL DEFAULT ''"),
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE character_voice_candidates ADD COLUMN {name} {definition}")
            self._ensure_character_ids(db)

    @staticmethod
    def _ensure_character_ids(db: sqlite3.Connection) -> None:
        table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='characters'").fetchone()
        if not table:
            return
        columns = {row["name"] for row in db.execute("PRAGMA table_info(characters)")}
        if "character_id" not in columns:
            db.execute("ALTER TABLE characters ADD COLUMN character_id TEXT NOT NULL DEFAULT ''")
        rows = db.execute("SELECT name, character_id FROM characters ORDER BY name").fetchall()
        seen: set[str] = set()
        for row in rows:
            character_id = str(row["character_id"] or "")
            try:
                UUID(character_id)
            except (ValueError, AttributeError):
                character_id = ""
            if not character_id or character_id in seen:
                character_id = str(uuid4())
                db.execute("UPDATE characters SET character_id=? WHERE name=?", (character_id, row["name"]))
            seen.add(character_id)
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_characters_character_id ON characters(character_id)")
        db.execute(
            """CREATE TRIGGER IF NOT EXISTS fill_character_id_after_insert
               AFTER INSERT ON characters WHEN NEW.character_id=''
               BEGIN UPDATE characters SET character_id=lower(hex(randomblob(16)))
               WHERE rowid=NEW.rowid; END"""
        )

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def decode(row) -> dict:
        if row is None:
            raise ValueError("voice candidate not found")
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        result["report"] = json.loads(result.pop("report_json"))
        result["character_key"] = result.get("character_id", "")
        return result

    def get(self, candidate_id: str) -> dict:
        with self.connection() as db:
            return self.decode(db.execute("SELECT * FROM character_voice_candidates WHERE candidate_id=?", (candidate_id,)).fetchone())

    def list(self, character_name: str) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT * FROM character_voice_candidates WHERE character_name=? AND status!='deleted' ORDER BY created_at, candidate_id", (character_name,)).fetchall()
            return [self.decode(row) for row in rows]

    def delete(self, candidate_id: str) -> None:
        """Remove a library entry, retaining bytes referenced by existing productions."""
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            candidate = self.decode(db.execute("SELECT * FROM character_voice_candidates WHERE candidate_id=?", (candidate_id,)).fetchone())
            if candidate['status'] == 'deleted':
                return
            if candidate['status'] in {'prepared', 'submitted', 'submission_unknown', 'candidate'}:
                raise ValueError('声音仍在处理中，请等待任务结束后删除')
            row = db.execute(
                "SELECT * FROM characters WHERE character_id=? OR name=? "
                "ORDER BY CASE WHEN character_id=? THEN 0 ELSE 1 END LIMIT 1",
                (candidate['character_id'], candidate['character_name'], candidate['character_id']),
            ).fetchone()
            if row:
                if row['reference_audio_path'] == candidate['path']:
                    db.execute("UPDATE characters SET reference_audio_path='',reference_audio_sha256='',reference_audio_updated_at='' WHERE character_id=? OR name=?", (candidate['character_id'], candidate['character_name']))
                samples = json.loads(row['voice_samples_by_age_group_json'] or '{}')
                samples = {key: value for key, value in samples.items() if value.get('path') != candidate['path']}
                db.execute('UPDATE characters SET voice_samples_by_age_group_json=? WHERE character_id=? OR name=?', (json.dumps(samples, ensure_ascii=False), candidate['character_id'], candidate['character_name']))
            db.execute('DELETE FROM character_nonverbal_selections WHERE candidate_id=?', (candidate_id,))
            db.execute("UPDATE character_voice_candidates SET status='deleted' WHERE candidate_id=?", (candidate_id,))

    def create(self, request_id: str, payload: dict) -> dict:
        if not request_id.strip():
            raise ValueError("request id required")
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        kind = payload.get("kind", "dialogue")
        batch = payload.get("batch_id", "")
        key = (payload["character_name"], payload["slot"], kind, batch, payload["profile_digest"])
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM character_voice_candidates WHERE request_id=?", (request_id,)).fetchone()
            if old:
                previous_payload = json.loads(old["payload_json"])
                if "previous_candidate_id" not in payload:
                    previous_payload.pop("previous_candidate_id", None)
                if previous_payload != payload:
                    raise ValueError("request id reused with different payload")
                return self.decode(old)
            if kind == "nonverbal":
                selected = db.execute("SELECT candidate_id FROM character_nonverbal_selections WHERE character_name=? AND purpose=?", key[:2]).fetchone()
                if selected and "previous_candidate_id" not in payload:
                    payload = {**payload, "previous_candidate_id": selected[0]}
                    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                if not batch:
                    raise ValueError("nonverbal batch id required")
                count = db.execute("""SELECT count(*) FROM character_voice_candidates
                    WHERE character_name=? AND slot=? AND kind=? AND batch_id=? AND profile_digest=?""", key).fetchone()[0]
                if count >= 2:
                    raise ValueError("awaiting_import: nonverbal attempt limit reached")
            character_id = ""
            has_characters = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='characters'"
            ).fetchone()
            if has_characters:
                char = db.execute("SELECT character_id FROM characters WHERE name=?", (payload["character_name"],)).fetchone()
                character_id = str(char["character_id"] or "") if char else ""
            candidate_id = uuid4().hex
            db.execute("""INSERT INTO character_voice_candidates
                (candidate_id,request_id,character_name,character_id,slot,kind,batch_id,profile_digest,payload_json)
                VALUES (?,?,?,?,?,?,?,?,?)""", (candidate_id, request_id, key[0], character_id, *key[1:], serialized))
        return self.get(candidate_id)

    def claim_submission(self, candidate_id: str) -> bool:
        # Record intent BEFORE network I/O; an interrupted submit cannot be
        # retried without a provider task ID or external reconciliation.
        with self.connection() as db:
            result = db.execute("""UPDATE character_voice_candidates SET status='submission_unknown'
                WHERE candidate_id=? AND status='prepared'""", (candidate_id,))
            return result.rowcount == 1

    def set_provider_task(self, candidate_id: str, task_id: str) -> None:
        if not task_id.strip():
            raise ValueError("provider task id required")
        with self.connection() as db:
            result = db.execute("""UPDATE character_voice_candidates SET provider_task_id=?,status='submitted'
                WHERE candidate_id=? AND status='submission_unknown' AND provider_task_id=''""", (task_id, candidate_id))
            if result.rowcount != 1:
                raise ValueError("provider submission state changed")

    def save_audio(self, candidate_id: str, content: bytes, filename: str) -> dict:
        suffix = Path(filename).suffix.lower()
        if suffix not in {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"} or not content:
            raise ValueError("invalid candidate audio")
        digest = hashlib.sha256(content).hexdigest()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM character_voice_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
            old = self.decode(row)
            if old["sha256"] and old["sha256"] != digest:
                raise ValueError("candidate audio is immutable")
            if old["path"]:
                return old
            path = self.project_dir / "assets" / "voice_candidates" / candidate_id / (digest + suffix)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise ValueError("candidate audio integrity mismatch")
            else:
                with path.open("xb") as output:
                    output.write(content)
            relative = path.relative_to(self.project_dir).as_posix()
            db.execute("UPDATE character_voice_candidates SET path=?,sha256=?,status='candidate' WHERE candidate_id=?", (relative, digest, candidate_id))
        return self.get(candidate_id)

    def save_report(self, candidate_id: str, report: dict) -> dict:
        if report.get("status") not in {"passed", "rejected", "uncertain", "qc_unavailable"}:
            raise ValueError("invalid voice quality status")
        with self.connection() as db:
            changed = db.execute("""UPDATE character_voice_candidates SET report_json=?,status=?
                WHERE candidate_id=? AND path!='' AND status!='approved'""",
                (json.dumps(report, ensure_ascii=False), report["status"], candidate_id))
            if changed.rowcount != 1:
                raise ValueError("candidate cannot be reviewed")
        return self.get(candidate_id)

    def read_audio(self, candidate_id: str) -> bytes:
        row = self.get(candidate_id)
        if row['status'] == 'deleted':
            raise ValueError('此候选已删除')
        path = (self.project_dir / row["path"]).resolve()
        if not row["path"] or not path.is_relative_to(self.project_dir):
            raise ValueError("invalid candidate path")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != row["sha256"]:
            raise ValueError("candidate audio integrity mismatch")
        return content

    def approve(self, candidate_id: str, *, actor: str, reason: str,
                expected_reference: dict[str, str] | None = None) -> dict:
        """Explicit human exception approval, guarded against stale facts/references."""
        from novelvideo.models import NovelCharacter
        from novelvideo.media_capabilities.tts.character_voice import character_voice_snapshot, voice_snapshot_digest
        from novelvideo.media_capabilities.tts.voice_versions import VoiceVersionStore

        if not actor.strip() or not reason.strip():
            raise ValueError("review actor and reason required")
        self.read_audio(candidate_id)
        versions = VoiceVersionStore(self.db_path)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            candidate = self.decode(db.execute("SELECT * FROM character_voice_candidates WHERE candidate_id=?", (candidate_id,)).fetchone())
            row = db.execute(
                "SELECT * FROM characters WHERE character_id=? OR name=? "
                "ORDER BY CASE WHEN character_id=? THEN 0 ELSE 1 END LIMIT 1",
                (candidate["character_id"], candidate["character_name"], candidate["character_id"]),
            ).fetchone()
            if candidate['status'] == 'deleted':
                raise ValueError('此候选已删除')
            if row is None:
                raise ValueError("character no longer exists")
            character_id = str(row["character_id"] or "")
            role = NovelCharacter(name=row["name"], character_id=character_id,
                                  gender=row["gender"] or "", age_group=row["age_group"] or "",
                                  role=row["role"] or "", description=row['description'] or '',
                                  voice_facts_json=row["voice_facts_json"] or "{}")
            if voice_snapshot_digest(character_voice_snapshot(role, db_path=self.db_path)) != candidate["profile_digest"]:
                raise ValueError("stale voice profile; review a new candidate")
            if candidate["voice_version_id"]:
                return candidate
            allowed_modes = {"dialogue", "both"} if candidate["kind"] == "dialogue" else {"nonverbal", "both"}
            if role.voice_facts.vocalization_mode not in allowed_modes or role.voice_facts.conflicts:
                raise ValueError("vocalization_not_dialogue")
            report = candidate["report"]
            if not report.get("technical", {}).get("passed") or report.get("status") == "rejected":
                raise ValueError("technical quality check must pass before approval")
            slot = candidate["slot"]
            identity_id = str(candidate["payload"].get("identity_id") or "")
            approval = {"method": "human", "actor": actor, "reason": reason}
            snapshot = {
                "source": candidate["payload"].get("origin", "candidate"),
                "profile_snapshot": candidate["payload"].get("profile_snapshot", {}),
                "payload": candidate["payload"],
                "report": report,
                "approval": approval,
            }
            if candidate["kind"] == "nonverbal":
                selected = db.execute("SELECT candidate_id FROM character_nonverbal_selections WHERE character_name=? AND purpose=?", (role.name, slot)).fetchone()
                selected_id = selected[0] if selected else ""
                if selected_id != candidate["payload"].get("previous_candidate_id", ""):
                    raise ValueError("stale nonverbal selection")
                version = versions.append_version(
                    character_id, identity_id=identity_id, slot=slot,
                    candidate_id=candidate_id, audio_sha256=candidate["sha256"],
                    path=candidate["path"], snapshot={**snapshot, "approval": approval}, db=db,
                )
                db.execute("""INSERT INTO character_nonverbal_selections VALUES(?,?,?)
                    ON CONFLICT(character_name,purpose) DO UPDATE SET candidate_id=excluded.candidate_id""", (role.name, slot, candidate_id))
                report = {**report, "approval": approval}
                db.execute("UPDATE character_voice_candidates SET status='approved',report_json=?,voice_version_id=?,character_id=? WHERE candidate_id=?",
                           (json.dumps(report, ensure_ascii=False), version["version_id"], character_id, candidate_id))
                candidate.update(status="approved", report=report, voice_version_id=version["version_id"], character_id=character_id,
                                 character_key=character_id)
                return candidate
            samples = json.loads(row["voice_samples_by_age_group_json"] or "{}")
            identity_values = json.loads(row["identities_json"] or "[]")
            identity = None
            if identity_id:
                identity = next((value for value in identity_values if value.get("identity_id") == identity_id), None)
                if identity is None:
                    raise ValueError("identity no longer exists")
            identity_samples = json.loads(identity.get("voice_samples_by_age_group_json") or "{}") if identity else {}
            current = (
                {"path": (identity.get("reference_audio_path") or "") if identity else (row["reference_audio_path"] or ""),
                 "sha256": (identity.get("reference_audio_sha256") or "") if identity else (row["reference_audio_sha256"] or "")}
                if slot == "default" else (identity_samples if identity else samples).get(slot, {})
            )
            if expected_reference is not None and {
                key: current.get(key, "") for key in ("path", "sha256")
            } != expected_reference:
                raise ValueError("当前声音已变化，请刷新候选后重新选择")
            if candidate["status"] == "approved" and current.get("path") != candidate["path"]:
                raise ValueError("reference changed after approval; explicit version selection required")
            if expected_reference is None and current != candidate["payload"].get("target_reference", {}):
                raise ValueError("stale target reference; another version was selected")
            metadata = {"path": candidate["path"], "sha256": candidate["sha256"], "updated_at": candidate["created_at"]}
            if identity and slot == "default":
                identity.update(reference_audio_path=metadata["path"], reference_audio_sha256=metadata["sha256"],
                                reference_audio_updated_at=metadata["updated_at"])
                db.execute("UPDATE characters SET identities_json=? WHERE character_id=?",
                           (json.dumps(identity_values, ensure_ascii=False), character_id))
            elif identity:
                identity_samples[slot] = metadata
                identity["voice_samples_by_age_group_json"] = json.dumps(identity_samples, ensure_ascii=False)
                db.execute("UPDATE characters SET identities_json=? WHERE character_id=?",
                           (json.dumps(identity_values, ensure_ascii=False), character_id))
            elif slot == "default":
                db.execute("""UPDATE characters SET reference_audio_path=?,reference_audio_sha256=?,
                    reference_audio_updated_at=? WHERE character_id=?""",
                    (metadata["path"], metadata["sha256"], metadata["updated_at"], character_id))
            elif slot in {"child", "youth", "middle", "elder"}:
                samples[slot] = metadata
                db.execute("UPDATE characters SET voice_samples_by_age_group_json=? WHERE character_id=?",
                           (json.dumps(samples, ensure_ascii=False), character_id))
            else:
                raise ValueError("invalid voice slot")
            report = {**report, "approval": approval}
            version = versions.append_version(
                character_id, identity_id=identity_id, slot=slot,
                candidate_id=candidate_id, audio_sha256=candidate["sha256"],
                path=candidate["path"], snapshot=snapshot, db=db,
            )
            db.execute("UPDATE character_voice_candidates SET status='approved',report_json=?,voice_version_id=?,character_id=? WHERE candidate_id=?",
                       (json.dumps(report, ensure_ascii=False), version["version_id"], character_id, candidate_id))
        return self.get(candidate_id)
