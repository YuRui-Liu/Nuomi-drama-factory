"""Copy canonical episode sources into editable creative documents."""
from .store import DocumentConflict, DocumentNotFound


async def import_episode_source(documents, sources, episode_number: int):
    if episode_number < 1:
        raise ValueError("episode_number must be positive")
    existing = await documents.find_import(episode_number)
    if existing:
        return existing
    source = next((item for item in await sources.list_sources() if item.episode_number == episode_number), None)
    if source is None:
        raise DocumentNotFound("episode source not found")
    origin = {
        "source_episode_number": episode_number,
        "content_hash": source.content_hash,
        "source_revision": source.source_revision,
    }
    try:
        return await documents.create(
            kind="episode_script",
            title=source.title or f"第{episode_number}集",
            episode_number=episode_number,
            markdown=source.content,
            client_mutation_id=f"import:{episode_number}",
            source_origin=origin,
        )
    except DocumentConflict:
        existing = await documents.find_import(episode_number)
        if existing:
            return existing
        raise
