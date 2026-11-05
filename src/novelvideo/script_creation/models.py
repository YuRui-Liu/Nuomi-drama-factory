from dataclasses import dataclass
from typing import Any

KINDS = frozenset({'brief', 'idea_candidate', 'outline', 'people', 'scenes', 'props', 'episode_synopsis', 'episode_script'})

@dataclass(frozen=True, slots=True)
class Block:
    id: str
    markdown: str

@dataclass(frozen=True, slots=True)
class SceneIdentity:
    id: str
    ordinal: int
    heading: str
    parent_ids: tuple[str, ...] = ()
    anchor_block_ids: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class OutlineItem:
    id: str
    parent_id: str | None
    ordinal: int
    title: str
    kind: str
    episode_start: int | None = None
    episode_end: int | None = None
    source_document_id: str | None = None
    source_revision_id: str | None = None
    source_locator: str | None = None

@dataclass(frozen=True, slots=True)
class ContinuityFact:
    id: str
    subject_id: str
    predicate: str
    object_id: str
    state: str
    episode_start: int | None
    episode_end: int | None
    source_document_id: str | None
    source_revision_id: str | None
    source_locator: str | None
    adoption_status: str = "adopted"

@dataclass(frozen=True, slots=True)
class Revision:
    id: str
    document_id: str
    parent_revision_id: str | None
    markdown: str
    blocks: tuple[Block, ...]
    client_mutation_id: str
    created_at: str
    restored_from_revision_id: str | None = None
    scene_identities: tuple[SceneIdentity, ...] = ()
    outline_items: tuple[OutlineItem, ...] = ()

@dataclass(frozen=True, slots=True)
class Document:
    id: str
    kind: str
    title: str
    episode_number: int | None
    current_revision_id: str
    adopted_revision_id: str | None
    source_origin: dict[str, Any] | None
    created_at: str
    updated_at: str
    revision: Revision
