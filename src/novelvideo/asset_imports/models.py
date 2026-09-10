from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class AssetType(StrEnum):
    CHARACTER = "character"
    SCENE = "scene"
    PROP = "prop"


class DecodedAssetTable(BaseModel):
    filename: str
    text: str
    sha256: str


class FieldEvidence(BaseModel):
    field: str
    value: Any
    source_start: int
    source_end: int
    quote: str


class AssetCandidate(BaseModel):
    name: str
    fields: dict[str, Any] = Field(default_factory=dict)
    evidence: list[FieldEvidence] = Field(default_factory=list)
    source_code: str = ""


class ExtractionResult(BaseModel):
    candidates: list[AssetCandidate] = Field(default_factory=list)
    ignored_sections: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class FieldChange(BaseModel):
    field: str
    current: Any = None
    proposed: Any = None
    disposition: Literal["fill", "preserve", "conflict"]
    evidence: list[FieldEvidence] = Field(default_factory=list)


class AssetDiff(BaseModel):
    name: str
    canonical_name: str | None = None
    source_code: str = ""
    disposition: Literal["create", "supplement", "skip", "conflict"]
    changes: list[FieldChange] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AssetImportPreview(BaseModel):
    import_id: str
    asset_type: AssetType
    filename: str
    content_sha256: str
    diffs: list[AssetDiff]
    ignored_sections: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_count: int = 0
    supplemented_count: int = 0
    skipped_count: int = 0
    warning_count: int = 0


class AssetImportResult(BaseModel):
    import_id: str
    asset_type: AssetType
    diffs: list[AssetDiff]
    created_count: int = 0
    supplemented_count: int = 0
    skipped_count: int = 0
    warning_count: int = 0
