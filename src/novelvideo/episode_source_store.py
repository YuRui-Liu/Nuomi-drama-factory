"""Durable, revisioned storage for per-episode source documents."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from novelvideo.episode_sources import (
    EpisodeCandidate,
    canonical_novel_text,
    content_sha256,
    resolve_episode_candidates,
)


class EpisodeSourceRevisionConflict(RuntimeError):
    """The project changed after a caller captured its base revision."""


class EpisodeImportPreviewNotFound(LookupError):
    """A preview does not exist or is no longer usable."""


@dataclass(frozen=True, slots=True)
class EpisodeSource:
    episode_number: int
    title: str
    content: str
    content_hash: str
    source_filename: str
    source_revision: int
    downstream_stale: bool
    imported_at: str
    updated_at: str

    @property
    def raw_content(self) -> str:
        return self.content


@dataclass(frozen=True, slots=True)
class EpisodeImportPreview:
    id: str
    base_revision: int
    items: tuple[EpisodeCandidate, ...]
    expires_at: str

    @property
    def preview_id(self) -> str:
        return self.id


@dataclass(frozen=True, slots=True)
class EpisodeSourceWriteResult:
    target_revision: int
    added: tuple[int, ...]
    overwritten: tuple[int, ...]
    skipped: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class EpisodeSourceMigrationResult:
    status: str
    episode_numbers: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class _PreparedToken:
    preview_id: str | None
    expected_revision: int
    imports: tuple[EpisodeCandidate, ...]
    skipped: tuple[int, ...]
    canonical_novel: str
    validate_preview_expiry: bool = True
    audit_id: str | None = None
    audit_episodes: tuple[dict[str, Any], ...] = ()


class EpisodeSourceStore:
    def __init__(self, sqlite_store: Any) -> None:
        self.sqlite_store = sqlite_store
        self._recovery_complete = False
        self._recovery_lock = asyncio.Lock()

    async def _db(self):
        db = await self.sqlite_store._ensure_db()
        if not self._recovery_complete:
            async with self._recovery_lock:
                if not self._recovery_complete:
                    await self._recover_pending_commit(db)
                    self._recovery_complete = True
        return db

    @property
    def _journal_path(self) -> Path:
        return Path(self.sqlite_store.state_dir) / "episode_source_pending_commit.json"

    async def _recover_pending_commit(self, db: Any) -> None:
        journal_path = self._journal_path
        if not journal_path.is_file():
            return
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        row = await (
            await db.execute(
                "SELECT project_revision FROM episode_source_state WHERE id=1"
            )
        ).fetchone()
        revision = int(row[0]) if row else 0
        novel_path = Path(self.sqlite_store.project_dir) / "novel.txt"
        old_revision = int(journal["old_revision"])
        new_revision = int(journal["new_revision"])
        backups = self._validated_journal_backups(journal)
        if revision == old_revision:
            if journal["old_present"]:
                self._replace_from_backup(backups["old_backup"], novel_path)
            else:
                novel_path.unlink(missing_ok=True)
        elif revision == new_revision:
            self._replace_from_backup(backups["new_backup"], novel_path)
        else:
            raise RuntimeError(
                f"pending episode commit does not match database revision {revision}"
            )
        self._clear_journal(journal)

    async def current_revision(self) -> int:
        db = await self._db()
        async with db.execute(
            "SELECT project_revision FROM episode_source_state WHERE id=1"
        ) as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def list_sources(self) -> list[EpisodeSource]:
        db = await self._db()
        async with db.execute(
            "SELECT * FROM episode_sources ORDER BY episode_number"
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._source_from_row(row) for row in rows]

    async def save_preview(
        self,
        *,
        base_revision: int,
        items: Sequence[EpisodeCandidate],
        ttl_seconds: int = 3600,
    ) -> EpisodeImportPreview:
        preview = EpisodeImportPreview(
            id=uuid4().hex,
            base_revision=base_revision,
            items=tuple(items),
            expires_at=(
                datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
            ).isoformat(),
        )
        db = await self._db()
        await db.execute(
            "INSERT INTO episode_import_previews VALUES (?, ?, ?, ?)",
            (
                preview.id,
                preview.base_revision,
                json.dumps(
                    [asdict(item) for item in preview.items], ensure_ascii=False
                ),
                preview.expires_at,
            ),
        )
        await db.commit()
        return preview

    async def get_preview(self, preview_id: str) -> EpisodeImportPreview | None:
        db = await self._db()
        async with db.execute(
            "SELECT * FROM episode_import_previews WHERE preview_id=?", (preview_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        try:
            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            expires_at = datetime.min.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            await db.execute(
                "DELETE FROM episode_import_previews WHERE preview_id=?", (preview_id,)
            )
            await db.commit()
            return None
        items = tuple(
            EpisodeCandidate(**{**item, "warnings": tuple(item.get("warnings", ()))})
            for item in json.loads(row["payload_json"])
        )
        return EpisodeImportPreview(
            id=row["preview_id"],
            base_revision=int(row["base_revision"]),
            items=items,
            expires_at=row["expires_at"],
        )

    async def commit_preview(
        self,
        preview_id: str,
        *,
        expected_revision: int,
        resolutions: Mapping[int, str],
    ) -> EpisodeSourceWriteResult:
        preview = await self.get_preview(preview_id)
        if preview is None:
            raise EpisodeImportPreviewNotFound(preview_id)
        if preview.base_revision != expected_revision:
            raise EpisodeSourceRevisionConflict("preview base revision mismatch")
        existing = {source.episode_number for source in await self.list_sources()}
        resolution = resolve_episode_candidates(preview.items, existing, resolutions)
        skipped = tuple(
            item.episode_number for item in resolution.skipped if item.episode_number
        )
        return await self.upsert_sources(
            resolution.imports,
            expected_revision=expected_revision,
            skipped=skipped,
            preview_id=preview_id,
        )

    async def upsert_sources(
        self,
        items: Sequence[EpisodeCandidate],
        *,
        expected_revision: int,
        skipped: tuple[int, ...] = (),
        migrated_at: str | None = None,
        preview_id: str | None = None,
        canonical_novel: str | None = None,
        validate_preview_expiry: bool = True,
        audit_id: str | None = None,
        audit_episodes: tuple[dict[str, Any], ...] = (),
    ) -> EpisodeSourceWriteResult:
        db = await self._db()
        novel_path = Path(self.sqlite_store.project_dir) / "novel.txt"
        novel_temporary: Path | None = None
        old_novel: bytes | None = None
        novel_replaced = False
        if canonical_novel is not None:
            novel_path.parent.mkdir(parents=True, exist_ok=True)
            novel_temporary = novel_path.with_name(
                f"{novel_path.name}.{uuid4().hex}.tmp"
            )
            with novel_temporary.open("wb") as stream:
                stream.write(canonical_novel.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
        await db.execute("BEGIN IMMEDIATE")
        try:
            row = await (
                await db.execute(
                    "SELECT project_revision FROM episode_source_state WHERE id=1"
                )
            ).fetchone()
            actual_revision = int(row[0]) if row else 0
            old_novel = novel_path.read_bytes() if novel_path.is_file() else None
            if preview_id is not None:
                preview_row = await (
                    await db.execute(
                        "SELECT expires_at FROM episode_import_previews WHERE preview_id=?",
                        (preview_id,),
                    )
                ).fetchone()
                if preview_row is None:
                    raise EpisodeImportPreviewNotFound(preview_id)
                try:
                    expires_at = datetime.fromisoformat(preview_row[0])
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    expires_at = datetime.min.replace(tzinfo=timezone.utc)
                if validate_preview_expiry and expires_at <= datetime.now(timezone.utc):
                    await db.execute(
                        "DELETE FROM episode_import_previews WHERE preview_id=?",
                        (preview_id,),
                    )
                    await db.commit()
                    raise EpisodeImportPreviewNotFound(preview_id)
            if actual_revision != expected_revision:
                raise EpisodeSourceRevisionConflict(
                    f"expected revision {expected_revision}, found {actual_revision}"
                )

            target_revision = actual_revision + (1 if items else 0)
            added: list[int] = []
            overwritten: list[int] = []
            previously_consumed: dict[int, int] = {}
            now = datetime.now(timezone.utc).isoformat()
            for item in items:
                if item.episode_number is None:
                    raise ValueError("episode number is required")
                previous = await (
                    await db.execute(
                        "SELECT source_revision, imported_at FROM episode_sources "
                        "WHERE episode_number=?",
                        (item.episode_number,),
                    )
                ).fetchone()
                source_revision = int(previous[0]) + 1 if previous else 1
                if previous:
                    previously_consumed[item.episode_number] = int(previous[0])
                imported_at = previous[1] if previous else now
                stale = 1 if previous else 0
                await db.execute(
                    """
                    INSERT INTO episode_sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(episode_number) DO UPDATE SET
                      title=excluded.title, raw_content=excluded.raw_content,
                      content_hash=excluded.content_hash,
                      source_filename=excluded.source_filename,
                      source_revision=excluded.source_revision,
                      downstream_stale=excluded.downstream_stale,
                      updated_at=excluded.updated_at
                    """,
                    (
                        item.episode_number,
                        item.title,
                        item.content,
                        item.content_hash or content_sha256(item.content),
                        item.source_filename,
                        source_revision,
                        stale,
                        imported_at,
                        now,
                    ),
                )
                # Deliberately update only the compatibility mirror. Planning fields survive.
                await db.execute(
                    """
                    INSERT INTO episodes(number, raw_content) VALUES (?, ?)
                    ON CONFLICT(number) DO UPDATE SET raw_content=excluded.raw_content,
                      updated_at=datetime('now')
                    """,
                    (item.episode_number, item.content),
                )
                (overwritten if previous else added).append(item.episode_number)

            if items:
                await db.execute(
                    """
                    INSERT INTO episode_source_state(id, project_revision, migrated_at)
                    VALUES (1, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET project_revision=excluded.project_revision,
                      migrated_at=COALESCE(excluded.migrated_at, episode_source_state.migrated_at)
                    """,
                    (target_revision, migrated_at),
                )
            if preview_id is not None:
                await db.execute(
                    "DELETE FROM episode_import_previews WHERE preview_id=?",
                    (preview_id,),
                )
            if audit_id is not None:
                await db.execute(
                    """INSERT INTO episode_import_records VALUES (?, ?, ?, ?)
                    ON CONFLICT(import_id) DO UPDATE SET
                      target_revision=excluded.target_revision,
                      episodes_json=excluded.episodes_json""",
                    (
                        audit_id,
                        target_revision,
                        json.dumps(audit_episodes, ensure_ascii=False),
                        now,
                    ),
                )
                for episode in audit_episodes:
                    if episode.get("status") != "overwritten":
                        continue
                    number = int(episode["episode_number"])
                    consumed_revision = previously_consumed[number]
                    await db.executemany(
                        """INSERT INTO episode_stage_revisions VALUES (?, ?, ?, 1, ?)
                        ON CONFLICT(episode_number, stage) DO UPDATE SET
                          stale=1,
                          updated_at=excluded.updated_at""",
                        [
                            (number, stage, consumed_revision, now)
                            for stage in ("characters", "scenes", "beats", "media")
                        ],
                    )
            if novel_temporary is not None:
                self._write_journal(
                    old_revision=actual_revision,
                    new_revision=target_revision,
                    old_novel=old_novel,
                    new_novel=canonical_novel.encode("utf-8"),
                    phase="prepared",
                )
                os.replace(novel_temporary, novel_path)
                novel_replaced = True
                self._update_journal_phase("novel_replaced")
            await db.commit()
            if canonical_novel is not None:
                self._clear_journal(
                    json.loads(self._journal_path.read_text(encoding="utf-8"))
                )
        except Exception:
            await db.rollback()
            if novel_replaced:
                self._restore_novel(novel_path, old_novel)
            if self._journal_path.is_file():
                self._clear_journal(
                    json.loads(self._journal_path.read_text(encoding="utf-8"))
                )
            raise
        finally:
            if novel_temporary is not None:
                novel_temporary.unlink(missing_ok=True)
        return EpisodeSourceWriteResult(
            target_revision=target_revision,
            added=tuple(sorted(added)),
            overwritten=tuple(sorted(overwritten)),
            skipped=tuple(sorted(skipped)),
        )

    async def prepare_import(self, batch: object):
        snapshot_items = self._batch_value(batch, "snapshot_items")
        if snapshot_items is not None:
            items = tuple(
                item
                if isinstance(item, EpisodeCandidate)
                else EpisodeCandidate(
                    **{**item, "warnings": tuple(item.get("warnings", ()))},
                )
                for item in snapshot_items
            )
            return await self._prepare_items(
                items,
                expected=int(self._batch_value(batch, "expected_revision")),
                resolutions=self._batch_value(batch, "resolutions", {}),
                preview_id=str(self._batch_value(batch, "preview_id")),
                validate_preview_expiry=False,
                audit_id=self._batch_value(batch, "audit_id"),
                audit_episodes=tuple(self._batch_value(batch, "audit_episodes", ())),
            )
        preview_id = self._batch_value(batch, "preview_id")
        expected = int(self._batch_value(batch, "expected_revision"))
        resolutions = self._batch_value(batch, "resolutions", {})
        preview = await self.get_preview(str(preview_id))
        if preview is None:
            raise EpisodeImportPreviewNotFound(str(preview_id))
        if (
            preview.base_revision != expected
            or await self.current_revision() != expected
        ):
            raise EpisodeSourceRevisionConflict("project revision changed")
        return await self._prepare_items(
            preview.items,
            expected=expected,
            resolutions=resolutions,
            preview_id=str(preview_id),
            validate_preview_expiry=True,
            audit_id=self._batch_value(batch, "audit_id"),
            audit_episodes=tuple(self._batch_value(batch, "audit_episodes", ())),
        )

    async def _prepare_items(
        self, items, *, expected, resolutions, preview_id, validate_preview_expiry,
        audit_id=None, audit_episodes=(),
    ):
        from novelvideo.episode_import_service import PreparedEpisodeImport

        if await self.current_revision() != expected:
            raise EpisodeSourceRevisionConflict("project revision changed")
        existing_sources = await self.list_sources()
        resolution = resolve_episode_candidates(
            items,
            {source.episode_number for source in existing_sources},
            resolutions,
        )
        imports_by_number = {
            source.episode_number: EpisodeCandidate(
                source_filename=source.source_filename,
                content=source.content,
                episode_number=source.episode_number,
                title=source.title,
                content_hash=source.content_hash,
            )
            for source in existing_sources
        }
        imports_by_number.update(
            {item.episode_number: item for item in resolution.imports}
        )
        token = _PreparedToken(
            preview_id=preview_id,
            expected_revision=expected,
            imports=resolution.imports,
            skipped=tuple(
                item.episode_number
                for item in resolution.skipped
                if item.episode_number
            ),
            canonical_novel=canonical_novel_text(tuple(imports_by_number.values())),
            validate_preview_expiry=validate_preview_expiry,
            audit_id=audit_id,
            audit_episodes=tuple(audit_episodes),
        )
        return PreparedEpisodeImport(
            target_revision=expected + (1 if resolution.imports else 0),
            canonical_novel=token.canonical_novel,
            contains_overwrite=bool(resolution.overwritten_episode_numbers),
            repository_token=token,
            has_changes=bool(resolution.imports),
            changed_contents=tuple(
                item.content
                for item in resolution.imports
                if item.episode_number not in resolution.overwritten_episode_numbers
            ),
        )

    async def commit_prepared(self, prepared: Any) -> None:
        token = prepared.repository_token
        if not isinstance(token, _PreparedToken):
            raise TypeError("invalid episode source repository token")
        await self.upsert_sources(
            token.imports,
            expected_revision=token.expected_revision,
            skipped=token.skipped,
            preview_id=token.preview_id,
            canonical_novel=token.canonical_novel,
            validate_preview_expiry=token.validate_preview_expiry,
            audit_id=token.audit_id,
            audit_episodes=token.audit_episodes,
        )

    async def discard_prepared(self, prepared: Any) -> None:
        return None

    @staticmethod
    def _restore_novel(novel_path: Path, previous: bytes | None) -> None:
        if previous is None:
            novel_path.unlink(missing_ok=True)
            return
        temporary = novel_path.with_name(f"{novel_path.name}.{uuid4().hex}.rollback")
        try:
            with temporary.open("wb") as stream:
                stream.write(previous)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, novel_path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _write_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

    def _write_journal(
        self,
        *,
        old_revision: int,
        new_revision: int,
        old_novel: bytes | None,
        new_novel: bytes,
        phase: str,
    ) -> None:
        journal = self._journal_path
        token = uuid4().hex
        old_backup = journal.with_name(
            f"episode_source_pending_commit.{token}.old.novel"
        )
        new_backup = journal.with_name(
            f"episode_source_pending_commit.{token}.new.novel"
        )
        if old_novel is not None:
            self._write_bytes(old_backup, old_novel)
        self._write_bytes(new_backup, new_novel)
        payload = {
            "old_revision": old_revision,
            "new_revision": new_revision,
            "old_present": old_novel is not None,
            "old_backup": old_backup.name,
            "new_backup": new_backup.name,
            "old_hash": hashlib.sha256(old_novel).hexdigest()
            if old_novel is not None
            else None,
            "new_hash": hashlib.sha256(new_novel).hexdigest(),
            "phase": phase,
        }
        self._replace_json(journal, payload)

    def _update_journal_phase(self, phase: str) -> None:
        payload = json.loads(self._journal_path.read_text(encoding="utf-8"))
        payload["phase"] = phase
        self._replace_json(self._journal_path, payload)

    @staticmethod
    def _replace_json(path: Path, payload: Mapping[str, Any]) -> None:
        temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        try:
            EpisodeSourceStore._write_bytes(
                temporary,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                ),
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _replace_from_backup(backup: Path, target: Path) -> None:
        content = backup.read_bytes()
        temporary = target.with_name(f"{target.name}.{uuid4().hex}.recovery")
        try:
            EpisodeSourceStore._write_bytes(temporary, content)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def _clear_journal(self, journal: Mapping[str, Any]) -> None:
        backups = self._validated_journal_backups(journal)
        self._journal_path.unlink(missing_ok=True)
        for path in backups.values():
            path.unlink(missing_ok=True)

    def _validated_journal_backups(
        self, journal: Mapping[str, Any]
    ) -> dict[str, Path]:
        state_dir = self._journal_path.parent.resolve()
        validated: dict[str, Path] = {}
        for key, hash_key, required in (
            ("old_backup", "old_hash", bool(journal.get("old_present"))),
            ("new_backup", "new_hash", True),
        ):
            value = journal.get(key)
            if not required:
                continue
            if not isinstance(value, str):
                raise RuntimeError("unsafe episode journal backup")
            candidate = Path(value)
            if (
                candidate.is_absolute()
                or candidate.name != value
                or not candidate.name.startswith("episode_source_pending_commit.")
                or not candidate.name.endswith((".old.novel", ".new.novel"))
            ):
                raise RuntimeError("unsafe episode journal backup")
            resolved = (state_dir / candidate).resolve()
            if resolved.parent != state_dir or not resolved.is_file():
                raise RuntimeError("unsafe episode journal backup")
            expected_hash = journal.get(hash_key)
            actual_hash = hashlib.sha256(resolved.read_bytes()).hexdigest()
            if not isinstance(expected_hash, str) or actual_hash != expected_hash:
                raise RuntimeError("episode journal backup hash mismatch")
            validated[key] = resolved
        return validated

    async def migrate_legacy_novel(self) -> EpisodeSourceMigrationResult:
        if await self.list_sources():
            return EpisodeSourceMigrationResult("already_migrated")
        novel_path = Path(self.sqlite_store.project_dir) / "novel.txt"
        if not novel_path.is_file():
            return EpisodeSourceMigrationResult("not_needed")
        text = novel_path.read_text(encoding="utf-8")
        from novelvideo.cognee.chapter_detector import ChapterDetector

        chapters = ChapterDetector().detect(text)
        if not chapters:
            return EpisodeSourceMigrationResult("not_needed")
        if chapters[0].is_fallback:
            return EpisodeSourceMigrationResult("confirmation_required")
        candidates = tuple(
            EpisodeCandidate(
                source_filename=novel_path.name,
                content=chapter.content,
                episode_number=chapter.number if len(chapters) > 1 else 1,
                title=chapter.title or "",
                content_hash=content_sha256(chapter.content),
            )
            for chapter in chapters
        )
        await self.upsert_sources(
            candidates,
            expected_revision=0,
            migrated_at=datetime.now(timezone.utc).isoformat(),
        )
        return EpisodeSourceMigrationResult(
            "migrated", tuple(item.episode_number or 0 for item in candidates)
        )

    @staticmethod
    def _batch_value(batch: object, name: str, default: Any = None) -> Any:
        if isinstance(batch, Mapping):
            return batch.get(name, default)
        return getattr(batch, name, default)

    @staticmethod
    def _source_from_row(row: Any) -> EpisodeSource:
        return EpisodeSource(
            episode_number=int(row["episode_number"]),
            title=row["title"],
            content=row["raw_content"],
            content_hash=row["content_hash"],
            source_filename=row["source_filename"],
            source_revision=int(row["source_revision"]),
            downstream_stale=bool(row["downstream_stale"]),
            imported_at=row["imported_at"],
            updated_at=row["updated_at"],
        )
