from dataclasses import dataclass
from typing import Any

KINDS = frozenset({'brief', 'outline', 'people', 'scenes', 'props', 'episode_synopsis', 'episode_script'})

@dataclass(frozen=True, slots=True)
class Block:
    id: str
    markdown: str

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
