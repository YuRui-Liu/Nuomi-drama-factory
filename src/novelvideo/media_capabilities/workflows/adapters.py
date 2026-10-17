"""Explicitly registered, versioned adapters; packages never execute code."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol
from .models import AdapterReference, WorkflowError


@dataclass
class PreparedInputs:
    values: dict
    files: dict = field(default_factory=dict)


class WorkflowAdapter(Protocol):
    def prepare(self, values: dict) -> PreparedInputs: ...
    def compile(self, prepared: PreparedInputs, uploaded: dict) -> dict: ...


class IdentityAdapter:
    def prepare(self, values: dict) -> PreparedInputs:
        return PreparedInputs(dict(values))

    def compile(self, prepared: PreparedInputs, uploaded: dict) -> dict:
        return prepared.values | uploaded


_ADAPTERS: dict[tuple[str, int], WorkflowAdapter] = {("identity", 1): IdentityAdapter()}
_INTEGRATIONS: dict[tuple[str, int], object] = {}


def register_adapter(identifier: str, version: int, adapter: WorkflowAdapter) -> None:
    key = (identifier, version)
    if key in _ADAPTERS and _ADAPTERS[key] is not adapter:
        raise WorkflowError("adapter_conflict", identifier)
    _ADAPTERS[key] = adapter


def get_adapter(reference: AdapterReference | None) -> WorkflowAdapter:
    key = (reference.id, reference.version) if reference else ("identity", 1)
    if key not in _ADAPTERS:
        raise WorkflowError("adapter_unavailable", key[0])
    return _ADAPTERS[key]


def register_integration(identifier: str, version: int, validator) -> None:
    _INTEGRATIONS[(identifier, version)] = validator


def validate_integration(integration, manifest) -> None:
    validator = _INTEGRATIONS.get((integration.adapter.id, integration.adapter.version))
    if validator is None:
        raise WorkflowError("integration_unavailable", integration.adapter.id)
    validator(integration, manifest)
