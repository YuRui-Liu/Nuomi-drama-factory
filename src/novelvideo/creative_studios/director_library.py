"""Personal directing copies, using the team's immutable CAS version store."""
from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

from pydantic import Field, StringConstraints, field_validator

from novelvideo.agent_teams.models import Contract
from novelvideo.agent_teams.store import AgentTeamStore

Short = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=30)]
Text = Annotated[str, Field(strict=True, max_length=100_000)]


class Preferences(Contract):
    pace: Text = ''
    camera_motion: Text = ''
    composition: Text = ''
    performance: Text = ''
    method: Text = ''


class DirectorInput(Contract):
    name: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100)]
    description: str = Field(default='', strict=True, max_length=1000)
    creative_types: list[Short] = Field(default_factory=list, max_length=20)
    tags: list[Short] = Field(default_factory=list, max_length=20)
    preferences: Preferences
    source_id: str | None = Field(default=None, max_length=200)
    source_revision: int | None = Field(default=None, strict=True, ge=1)

    @field_validator('creative_types', 'tags')
    @classmethod
    def unique(cls, values):
        return list(dict.fromkeys(values))


class DirectorEntry(DirectorInput):
    id: str
    revision: int
    owner: str
    created_at: str
    updated_at: str
    archived: bool = False


class DirectorLibrary:
    def __init__(self, store: AgentTeamStore, owner: str):
        self.store, self.owner = store, owner

    def list(self):
        return sorted((entry for entry in self.store._list('director', self.owner, DirectorEntry)
                       if not entry.archived), key=lambda entry: entry.updated_at, reverse=True)

    def get(self, id):
        entry = self.store._get('director', id, None, DirectorEntry)
        if entry is None or entry.owner != self.owner or entry.archived:
            raise KeyError(id)
        return entry

    def save(self, data: DirectorInput, expected_revision: int = 0, id: str | None = None):
        old = self.get(id) if id else None
        now = datetime.now(timezone.utc).isoformat()
        entry = DirectorEntry(**data.model_dump(), id=id or uuid4().hex,
                              revision=(old.revision if old else 0) + 1, owner=self.owner,
                              created_at=old.created_at if old else now, updated_at=now)
        return self.store._publish('director', entry, expected_revision)

    def delete(self, id: str, expected_revision: int):
        old = self.get(id)
        entry = old.model_copy(update={'revision': old.revision + 1, 'archived': True,
                                       'updated_at': datetime.now(timezone.utc).isoformat()})
        self.store._publish('director', entry, expected_revision)
