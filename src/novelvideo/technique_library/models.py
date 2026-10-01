"""Strict portable catalog schema; no prompt or binary content is accepted."""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

Provenance = Literal['official', 'author', 'reconstructed', 'unpublished', 'unknown']
UseCase = Literal['cinema', 'performance', 'action', 'product', 'music', 'stylized']


def safe_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('URL must be an absolute http(s) link without credentials')
    if any(ord(char) < 32 for char in value):
        raise ValueError('URL contains control characters')
    return value


class Source(BaseModel):
    model_config = ConfigDict(extra='forbid')
    repository: str
    revision: str = Field(pattern=r'^[0-9a-f]{40}$')
    path: str
    upstream_id: str
    url: str
    author: str = ''
    provenance: Provenance = 'unknown'
    prompt_provenance: str | None = None
    source_verification: bool | str | None = None
    mode: str | None = None
    duration: str | None = None
    _url = field_validator('url')(safe_url)


class Case(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^h3-[0-9a-f]{16}$')
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    use_cases: list[UseCase]
    provenance: Provenance
    sources: list[Source] = Field(min_length=1)
    media_url: str | None = None
    mode: str | None = None
    duration: str | None = None
    local_verification: Literal['unverified'] = 'unverified'
    authorization_status: Literal['unknown'] = 'unknown'
    imported_at: str | None = None
    conflicts: list[str] = Field(default_factory=list)

    @field_validator('media_url')
    @classmethod
    def media_link(cls, value):
        return safe_url(value) if value else None


class Bundle(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['1.0'] = '1.0'
    sources: list[dict]
    report: dict
    cases: list[Case] = Field(min_length=1)
