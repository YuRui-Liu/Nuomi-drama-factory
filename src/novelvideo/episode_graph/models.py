from typing import Any, Literal

from pydantic import BaseModel, Field
from typing_extensions import TypedDict


class GraphAttributes(TypedDict, total=False):
    """Finite attribute vocabulary compatible with strict structured output."""

    description: str | None
    tags: list[str] | None
    aliases: list[str] | None
    state: str | None
    role: str | None
    location: str | None
    time_of_day: str | None
    purpose: str | None
    outcome: str | None
    evidence: str | None


class EpisodeGraphSource(BaseModel):
    number: int = Field(gt=0)
    title: str
    content: str = Field(min_length=1)
    source_revision: int = Field(gt=0)


class EpisodeGraphGroup(BaseModel):
    first_episode: int
    last_episode: int
    episodes: tuple[EpisodeGraphSource, ...]
    content_hash: str

    @property
    def key(self) -> str:
        return f"e{self.first_episode}-e{self.last_episode}"


class GraphEntity(BaseModel):
    name: str = Field(min_length=1)
    kind: Literal["character", "identity", "scene", "prop"]
    attributes: GraphAttributes = Field(default_factory=dict)
    source_episodes: set[int] = Field(min_length=1)


class GraphEvent(BaseModel):
    episode: int = Field(gt=0)
    ordinal: int = Field(gt=0)
    description: str = Field(min_length=1)
    attributes: GraphAttributes = Field(default_factory=dict)
    source_episodes: set[int] = Field(min_length=1)


class GraphRelation(BaseModel):
    source_key: str = Field(min_length=1)
    relation_type: str = Field(min_length=1)
    target_key: str = Field(min_length=1)
    episode: int = Field(gt=0)
    attributes: GraphAttributes = Field(default_factory=dict)
    source_episodes: set[int] = Field(min_length=1)


class EpisodeGraphExtraction(BaseModel):
    group_key: str
    entities: list[GraphEntity] = Field(default_factory=list)
    events: list[GraphEvent] = Field(default_factory=list)
    relations: list[GraphRelation] = Field(default_factory=list)


class ConflictValues(BaseModel):
    """Explicitly distinguishes competing values from a native list value."""

    values: list[Any] = Field(min_length=2)


class MergedEpisodeGraph(BaseModel):
    entities: list[GraphEntity] = Field(default_factory=list)
    events: list[GraphEvent] = Field(default_factory=list)
    relations: list[GraphRelation] = Field(default_factory=list)
    conflict_count: int = 0
