"""SQLite evidence writes that participate in formal publication transactions."""

from __future__ import annotations

from typing import Any


async def write_publication_evidence(
    db: Any, *, run_id: str, publication: Any
) -> None:
    await db.execute(
        "DELETE FROM entity_evidence "
        "WHERE run_id = ? AND entity_type IN ('character', 'scene')",
        (run_id,),
    )
    rows = [
        (
            run_id,
            entity_type,
            entity_id,
            f"episode:{reference.episode_number:04d}",
            reference.source_start,
            reference.source_end,
            "source_quote",
            reference.quote,
        )
        for entity_type, entity_id, references in (
            *(
                ("character", item.model.name, item.source_refs)
                for item in publication.characters
            ),
            *(
                ("scene", item.model.name, item.source_refs)
                for item in publication.scenes
            ),
        )
        for reference in references
    ]
    if rows:
        await db.executemany(
            "INSERT INTO entity_evidence "
            "(run_id, entity_type, entity_id, chunk_id, source_start, source_end, "
            "evidence_kind, evidence_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    for entity_type in ("character", "scene"):
        await db.execute(
            "INSERT INTO active_evidence_runs (entity_type, run_id) VALUES (?, ?) "
            "ON CONFLICT(entity_type) DO UPDATE SET run_id = excluded.run_id",
            (entity_type, run_id),
        )


__all__ = ["write_publication_evidence"]
