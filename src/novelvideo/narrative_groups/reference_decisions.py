"""Frozen narrative-reference snapshot envelope.

Generation-time matching and decision building were retired by the planned
binding cutover. This envelope remains so already-frozen v1 snapshots stay
readable while new submissions freeze planned v2 references.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ReferenceDecisionSnapshot:
    id: str
    schema_version: str
    images: tuple[Any, ...]
    ignored_requirement_ids: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    style_reference: str = ""

    @property
    def schema(self) -> str:
        return self.schema_version

    @property
    def ignored(self) -> tuple[str, ...]:
        return self.ignored_requirement_ids


__all__ = ["ReferenceDecisionSnapshot"]
