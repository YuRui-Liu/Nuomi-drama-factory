"""Idempotent lazy migration for projects that only have ``novel.txt``."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from novelvideo.episode_source_store import EpisodeSourceMigrationResult
from novelvideo.episode_sources import EpisodeCandidate, content_sha256


async def ensure_legacy_migration(store, *, confirmed_fallback: bool = False):
    if await store.list_sources():
        return EpisodeSourceMigrationResult("already_migrated")

    list_episodes = getattr(store.sqlite_store, "list_episodes", None)
    if callable(list_episodes):
        episodes = await list_episodes()
        existing_candidates = tuple(
            EpisodeCandidate(
                source_filename=f"E{episode.number:03d}.md",
                content=episode.raw_content,
                episode_number=episode.number,
                title=episode.title or "",
                content_hash=content_sha256(episode.raw_content),
            )
            for episode in sorted(episodes, key=lambda item: item.number)
            if isinstance(episode.number, int)
            and not isinstance(episode.number, bool)
            and episode.number > 0
            and (episode.raw_content or "").strip()
        )
        if existing_candidates:
            await store.upsert_sources(
                existing_candidates,
                expected_revision=0,
                migrated_at=datetime.now(timezone.utc).isoformat(),
            )
            return EpisodeSourceMigrationResult(
                "migrated",
                tuple(item.episode_number or 0 for item in existing_candidates),
            )

    novel_path = Path(store.sqlite_store.project_dir) / "novel.txt"
    if not novel_path.is_file():
        return EpisodeSourceMigrationResult("not_needed")
    text = novel_path.read_text(encoding="utf-8")
    from novelvideo.cognee.chapter_detector import ChapterDetector

    chapters = ChapterDetector().detect(text)
    if not chapters:
        return EpisodeSourceMigrationResult("not_needed")
    if chapters[0].is_fallback and not confirmed_fallback:
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
    await store.upsert_sources(
        candidates,
        expected_revision=0,
        migrated_at=datetime.now(timezone.utc).isoformat(),
    )
    return EpisodeSourceMigrationResult(
        "migrated", tuple(item.episode_number or 0 for item in candidates)
    )
